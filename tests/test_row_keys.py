"""
Tests for row URLs and pagination cursors when primary key values are not
ordinary text or integers in a typed column: nulls, the empty string, binary
values, and numbers in a column with no declared type.

Most of these use the primary_key_edge_cases tables in the fixtures database.
Tests that write to the database, or that need rows with tied sort values,
create their own.
"""

import re

import pytest
from bs4 import BeautifulSoup as Soup

from datasette.app import Datasette
from datasette.database import Database

# table -> {content: row path, or None if a null primary key means there is none}
EXPECTED_PATHS = {
    "primary_key_edge_cases": {
        "integer 1": "i:1",
        "text 1": "1",
        "float 1.5": "f:0x1~2E8000000000000p0",
        "binary 0102": "b:0102",
        "empty string": "$empty",
        "text i:1": "i~3A1",
        "text $null": "~24null",
        "null, first": None,
        "null, second": None,
        "null, third": None,
    },
    "compound_primary_key_edge_cases": {
        "a and b": "a,b",
        "a and empty string": "a,$empty",
        "two empty strings": "$empty,$empty",
        "a and null, first": None,
        "a and null, second": None,
        "two nulls": None,
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("table", EXPECTED_PATHS)
async def test_every_row_link_leads_to_its_own_row(ds_client, table):
    response = await ds_client.get(f"/fixtures/{table}")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    paths = {}
    for tr in soup.select("table.rows-and-columns tbody tr"):
        content = tr.select_one("td.col-content").text.strip()
        link = tr.select_one("td.type-pk a")
        if "data-row" not in tr.attrs:
            # A null primary key: nothing to identify the row, so no link
            assert link is None
            assert tr.select_one("td.type-pk").text.strip() == ""
            paths[content] = None
            continue
        assert link["href"] == f"/fixtures/{table}/{tr['data-row']}"
        # Every other row has something to click on
        assert link.text.strip()
        paths[content] = tr["data-row"]
    assert paths == EXPECTED_PATHS[table]

    for content, path in paths.items():
        if path is None:
            continue
        html = await ds_client.get(f"/fixtures/{table}/{path}")
        assert html.status_code == 200, path
        data = (await ds_client.get(f"/fixtures/{table}/{path}.json")).json()
        assert data["row"]["content"] == content, path


@pytest.mark.asyncio
@pytest.mark.parametrize("table", EXPECTED_PATHS)
@pytest.mark.parametrize("size", [1, 2, 3])
@pytest.mark.parametrize("sort", ["", "&_sort=content", "&_sort_desc=content"])
async def test_pagination_visits_every_row_once(ds_client, table, size, sort):
    one_page = (await ds_client.get(f"/fixtures/{table}.json?_size=max{sort}")).json()
    expected = [row["content"] for row in one_page["rows"]]
    assert sorted(expected) == sorted(EXPECTED_PATHS[table])

    path = f"/fixtures/{table}.json?_size={size}{sort}"
    seen = []
    for _ in range(20):
        data = (await ds_client.get(path)).json()
        assert len(data["rows"]) <= size
        seen.extend(row["content"] for row in data["rows"])
        # "next" and "next_url" are both set, or both null on the last page
        assert (data["next"] is None) == (data["next_url"] is None)
        if data["next_url"] is None:
            break
        # The count of rows before the next page agrees with what was shown
        count = await ds_client.post(
            f"/fixtures/{table}/-/count?_count=before&_next={data['next']}{sort}"
        )
        assert count.json()["count"] == len(seen), data["next"]
        path = data["next_url"].replace("http://localhost", "")
    else:
        pytest.fail("Pagination did not finish")
    # Every row once, in the same order as a single page
    assert seen == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_next",
    [
        # A cursor for a row with a null primary key ends with its rowid
        ("/fixtures/primary_key_edge_cases.json?_size=1", "$null,8"),
        ("/fixtures/primary_key_edge_cases.json?_size=3", "$null,10"),
        ("/fixtures/compound_primary_key_edge_cases.json?_size=1", "$null,$null,6"),
        # No null in these keys, so no rowid
        ("/fixtures/primary_key_edge_cases.json?_size=4", "i:1"),
        ("/fixtures/compound_primary_key_edge_cases.json?_size=2", "$empty,$empty"),
        ("/fixtures/compound_primary_key.json?_size=2", "a~2Fb,~2Ec-d"),
    ],
)
async def test_next_cursor(ds_client, path, expected_next):
    assert (await ds_client.get(path)).json()["next"] == expected_next


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_status",
    [
        # There is no row page for a null primary key. The row whose key is
        # the text "$null" is at ~24null
        ("/fixtures/primary_key_edge_cases/$null", 404),
        ("/fixtures/simple_primary_key/$null", 404),
        ("/fixtures/compound_primary_key_edge_cases/a,$null", 404),
        # The type has to match in a column with no declared type
        ("/fixtures/primary_key_edge_cases/i:2", 404),
        ("/fixtures/primary_key_edge_cases/i:zz", 400),
        ("/fixtures/primary_key_edge_cases/b:zz", 400),
        # Compound key links from before $empty existed
        ("/fixtures/compound_primary_key_edge_cases/a,", 200),
        ("/fixtures/compound_primary_key_edge_cases/,", 200),
    ],
)
async def test_row_page_status(ds_client, path, expected_status):
    response = await ds_client.get(path)
    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_location",
    [
        # Clients that percent-encode ":" and "$" still reach the typed value
        (
            "/fixtures/primary_key_edge_cases/i%3A2",
            "/fixtures/primary_key_edge_cases/i:2",
        ),
        (
            "/fixtures/primary_key_edge_cases/i%3a2",
            "/fixtures/primary_key_edge_cases/i:2",
        ),
        (
            "/fixtures/simple_primary_key/%24empty",
            "/fixtures/simple_primary_key/$empty",
        ),
        # Other percent-encoding still becomes tilde-encoding
        ("/fixtures/simple_primary_key/a%2Fb", "/fixtures/simple_primary_key/a~2Fb"),
    ],
)
async def test_percent_encoded_markers_redirect(ds_client, path, expected_location):
    response = await ds_client.get(path, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == expected_location


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,content",
    [
        ("$empty", "empty string"),
        ("b:0102", "binary 0102"),
        ("i:1", "integer 1"),
        ("1", "text 1"),
    ],
)
async def test_row_fragment(ds_client, path, content):
    response = await ds_client.get(
        f"/fixtures/primary_key_edge_cases/-/fragment?_row={path}"
    )
    assert response.status_code == 200
    rows = Soup(response.text, "html.parser").select("tr[data-row]")
    assert [tr["data-row"] for tr in rows] == [path]
    assert rows[0].select_one("td.col-content").text == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    [
        # Text, and numbers in a column with a declared type, are strings
        ("/fixtures/primary_key_edge_cases/1.json", ["1"]),
        ("/fixtures/primary_key_edge_cases/$empty.json", [""]),
        ("/fixtures/simple_primary_key/1.json", ["1"]),
        ("/fixtures/compound_primary_key_edge_cases/a,$empty.json", ["a", ""]),
        # Typed values in the URL are the value itself
        ("/fixtures/primary_key_edge_cases/i:1.json", [1]),
        ("/fixtures/primary_key_edge_cases/f:0x1~2E8000000000000p0.json", [1.5]),
        (
            "/fixtures/primary_key_edge_cases/b:0102.json",
            [{"$base64": True, "encoded": "AQI="}],
        ),
    ],
)
async def test_primary_key_values_in_row_json(ds_client, path, expected):
    assert (await ds_client.get(path)).json()["primary_key_values"] == expected


@pytest.mark.asyncio
async def test_binary_primary_key_is_displayed_as_hex(ds_client):
    response = await ds_client.get("/fixtures/primary_key_edge_cases")
    tr = Soup(response.text, "html.parser").select_one('tr[data-row="b:0102"]')
    assert tr["data-row-display"] == "binary 0102"
    assert tr.select_one("td.type-pk a").text == "binary 0102"

    response = await ds_client.get("/fixtures/primary_key_edge_cases/b:0102")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    heading = soup.select_one("h1.row-heading").text.strip()
    assert heading == "primary_key_edge_cases: binary 0102"
    assert soup.select_one("dl.row-fields")["data-row-display"] == "binary 0102"


@pytest.mark.asyncio
async def test_shape_object_keys(ds_client):
    # ?id__notnull=1 leaves out the rows that have no key
    data = (
        await ds_client.get(
            "/fixtures/primary_key_edge_cases.json?_shape=object&id__notnull=1"
        )
    ).json()
    # The integer 1 and the text "1" are separate keys
    assert {key: row["content"] for key, row in data.items()} == {
        path: content
        for content, path in EXPECTED_PATHS["primary_key_edge_cases"].items()
        if path is not None
    }
    # Keys of a table with a typed primary key are unchanged
    data = (
        await ds_client.get("/fixtures/simple_primary_key.json?_shape=object")
    ).json()
    assert list(data) == ["1", "2", "3", "4", "5"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table", ["primary_key_edge_cases", "compound_primary_key_edge_cases"]
)
async def test_shape_object_is_an_error_for_null_primary_keys(ds_client, table):
    response = await ds_client.get(f"/fixtures/{table}.json?_shape=object")
    assert response.status_code == 400
    assert response.json()["error"] == (
        "_shape=object not available for rows with a null primary key"
    )
    # Other shapes still work
    response = await ds_client.get(f"/fixtures/{table}.json?_shape=array")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("on_row_page", [False, True])
async def test_foreign_key_links_lead_to_the_referenced_row(ds_client, on_row_page):
    # pk of primary_key_edge_case_references -> the row its edge_case links to
    expected = {
        "1": ("i:1", "integer 1"),
        "2": ("i~3A1", "text i:1"),
        "3": ("$empty", "empty string"),
    }
    for pk, (path, content) in expected.items():
        url = "/fixtures/primary_key_edge_case_references"
        if on_row_page:
            soup = Soup((await ds_client.get(f"{url}/{pk}")).text, "html.parser")
        else:
            soup = Soup((await ds_client.get(url)).text, "html.parser").select_one(
                f'tr[data-row="{pk}"]'
            )
        link = soup.find("a", href=re.compile("^/fixtures/primary_key_edge_cases/"))
        if on_row_page and pk == "3":
            # The row page shows an empty string as a blank value, not a link
            assert link is None
            continue
        # There is always something to click on
        assert link.text.strip()
        assert link["href"] == f"/fixtures/primary_key_edge_cases/{path}"
        row = (await ds_client.get(link["href"] + ".json")).json()["row"]
        assert row["content"] == content


@pytest.mark.asyncio
async def test_autocomplete_path(ds_client):
    response = await ds_client.get(
        "/fixtures/primary_key_edge_cases/-/autocomplete?q=1"
    )
    paths = {repr(row["pks"]["id"]): row["path"] for row in response.json()["rows"]}
    assert paths["1"] == "i:1"
    assert paths["'1'"] == "1"
    for row in response.json()["rows"]:
        data = (
            await ds_client.get(f"/fixtures/primary_key_edge_cases/{row['path']}.json")
        ).json()
        assert data["row"]["id"] == row["pks"]["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("qs", ("_initial=1", "q=0102"))
async def test_autocomplete_binary_primary_key(ds_client, qs):
    # A binary primary key made this endpoint return a 500 error, because
    # bytes cannot be serialized as JSON
    response = await ds_client.get(
        "/fixtures/primary_key_edge_cases/-/autocomplete?" + qs
    )
    assert response.status_code == 200, response.text
    binary_rows = [
        row for row in response.json()["rows"] if isinstance(row["pks"]["id"], dict)
    ]
    if qs == "_initial=1":
        # Encoded the same way as binary values elsewhere in the JSON API
        encoded = {"$base64": True, "encoded": "AQI="}
        assert binary_rows == [
            # The primary key is also the label for this table
            {"pks": {"id": encoded}, "path": "b:0102", "label": encoded}
        ]
        data = (
            await ds_client.get("/fixtures/primary_key_edge_cases/b:0102.json")
        ).json()
        assert data["row"]["id"] == binary_rows[0]["pks"]["id"]


def test_autocomplete_path_is_null_for_null_primary_key():
    from datasette.views.table import _autocomplete_response_rows

    rows = [
        {"id": None, "name": "n"},
        {"id": "None", "name": "s"},
        {"id": "", "name": "e"},
    ]
    assert _autocomplete_response_rows(rows, ["id"], "name") == [
        {"pks": {"id": None}, "path": None, "label": "n"},
        {"pks": {"id": "None"}, "path": "None", "label": "s"},
        {"pks": {"id": ""}, "path": "$empty", "label": "e"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,expected_checks", [("primary_key_edge_cases", 1), ("sortable", 1)]
)
async def test_null_key_check_runs_once_for_a_streamed_export(
    ds_client, monkeypatch, table, expected_checks
):
    checks = []
    original = Database.execute

    async def execute(self, sql, *args, **kwargs):
        if " is null limit 1" in sql:
            checks.append(sql)
        return await original(self, sql, *args, **kwargs)

    monkeypatch.setattr(Database, "execute", execute)
    response = await ds_client.get(f"/fixtures/{table}.csv?_stream=1&_size=2")
    assert response.status_code == 200
    rows = response.text.strip().splitlines()[1:]
    # Several pages were fetched, and every row is there
    assert len(rows) == {"primary_key_edge_cases": 10, "sortable": 201}[table]
    assert len(checks) == expected_checks


def test_urls_row_encodes_a_list_of_values(ds_client):
    urls = ds_client.ds.urls
    table = "/fixtures/primary_key_edge_cases"
    assert urls.row("fixtures", "primary_key_edge_cases", [""]) == f"{table}/$empty"
    assert urls.row("fixtures", "primary_key_edge_cases", ["a/b", 1]) == (
        f"{table}/a~2Fb,1"
    )
    assert urls.row("fixtures", "primary_key_edge_cases", [b"\x01"]) == f"{table}/b:01"
    # A string is already encoded
    assert urls.row("fixtures", "primary_key_edge_cases", "a~2Fb") == f"{table}/a~2Fb"


# The tests below write to the database, or need rows with tied sort values

WRITABLE_SCHEMA = """
create table untyped (id primary key, name text, data blob);
insert into untyped values
    (1, 'integer', x'aa'), ('1', 'text', x'bb'), (x'0102', 'blob', null);
create table typed (id text primary key, name text);
insert into typed values ('', 'empty'), ('a', 'a');
create table single (id text primary key, name text, score integer);
insert into single values
    (null, 'n1', 2), ('b', 'b', 1), (null, 'n2', 1), ('', 'empty', null),
    (null, 'n3', null), ('a', 'a', 2), ('None', 'none string', 1);
create table pair (a text, b text, name text, score integer, primary key (a, b));
insert into pair values
    (null, null, 'nn1', 1), ('x', null, 'xn1', 2), (null, 'y', 'ny', 1),
    ('x', null, 'xn2', 1), (null, null, 'nn2', null), ('x', 'y', 'xy', 2),
    (null, 'y', 'ny2', 2), ('', '', 'empty', 1);
"""


@pytest.fixture
async def ds():
    ds = Datasette(
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "update-row": {"id": "root"},
                        "delete-row": {"id": "root"},
                    }
                }
            }
        }
    )
    db = ds.add_database(Database(ds, memory_name="test_row_keys"), name="data")
    await db.execute_write_script(
        "".join(
            f"drop table if exists {table};"
            for table in ("untyped", "typed", "single", "pair")
        )
        + WRITABLE_SCHEMA
    )
    try:
        yield ds
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,expected",
    [
        ("untyped", {"i:1": "Edit row 1", "1": "Edit row 1", "b:0102": None}),
        ("typed", {"$empty": "Edit row ", "a": "Edit row a"}),
        # No buttons for rows with a null primary key
        ("single", {"$empty": None, "None": None, "a": None, "b": None}),
    ],
)
async def test_edit_and_delete_buttons(ds, table, expected):
    response = await ds.client.get(f"/data/{table}", actor={"id": "root"})
    soup = Soup(response.text, "html.parser")
    rows = soup.select("table.rows-and-columns tbody tr")
    assert {tr["data-row"] for tr in rows if "data-row" in tr.attrs} == set(expected)
    for tr in rows:
        buttons = tr.select("button[data-row-action]")
        if "data-row" not in tr.attrs:
            assert buttons == []
            continue
        assert [button["data-row-action"] for button in buttons] == ["edit", "delete"]
        label = expected[tr["data-row"]]
        if label is not None:
            assert buttons[0]["aria-label"].startswith(label)
    if table == "untyped":
        blob_row = soup.select_one('tr[data-row="b:0102"]')
        edit = blob_row.select_one('button[data-row-action="edit"]')
        assert edit["aria-label"].startswith("Edit row binary 0102")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,path", [("typed", "$empty"), ("untyped", "i:1"), ("untyped", "b:0102")]
)
async def test_update_and_delete_row(ds, table, path):
    db = ds.get_database("data")
    count = (await db.execute(f"select count(*) from {table}")).single_value()
    response = await ds.client.post(
        f"/data/{table}/{path}/-/update",
        json={"update": {"name": "Changed"}},
        actor={"id": "root"},
    )
    assert response.status_code == 200, response.text
    changed = await db.execute(f"select count(*) from {table} where name = 'Changed'")
    assert changed.single_value() == 1

    response = await ds.client.post(
        f"/data/{table}/{path}/-/delete", actor={"id": "root"}
    )
    assert response.status_code == 200, response.text
    assert (
        await db.execute(f"select count(*) from {table}")
    ).single_value() == count - 1
    assert (
        await db.execute(f"select count(*) from {table} where name = 'Changed'")
    ).single_value() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("table,total", [("single", 7), ("pair", 8)])
@pytest.mark.parametrize("size", [1, 2, 3])
@pytest.mark.parametrize(
    "extra", ["&_sort=score", "&_sort_desc=score", "&_sort=score&_col=name"]
)
async def test_sorted_pagination_past_null_primary_keys(ds, table, total, size, extra):
    # The score column has ties and nulls, so the cursor has to resume inside
    # a group of rows that share both a sort value and a null primary key
    path = f"/data/{table}.json?_size={size}{extra}"
    one_page = (await ds.client.get(f"/data/{table}.json?_size=max{extra}")).json()
    expected = [row["name"] for row in one_page["rows"]]
    assert len(expected) == total
    seen = []
    for _ in range(20):
        data = (await ds.client.get(path)).json()
        assert len(data["rows"]) <= size
        seen.extend(row["name"] for row in data["rows"])
        if data["next_url"] is None:
            break
        count = await ds.client.post(
            f"/data/{table}/-/count?_count=before&_next={data['next']}"
            + extra.replace("&_col=name", "")
        )
        assert count.json()["count"] == len(seen), data["next"]
        path = data["next_url"].replace("http://localhost", "")
    else:
        pytest.fail("Pagination did not finish")
    assert seen == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("format_", ["csv", "tsv"])
async def test_csv_blob_links_for_untyped_primary_keys(ds, format_):
    # CSV and TSV link to binary values by their content, not by row
    response = await ds.client.get(f"/data/untyped.{format_}?_col=name&_col=data")
    assert response.status_code == 200
    urls = [
        line.replace("\t", ",").split(",")[-1]
        for line in response.text.strip().splitlines()[1:3]
    ]
    assert all("_blob_column=data&_blob_hash=" in url for url in urls)
    contents = [
        (await ds.client.get(url.replace("http://localhost", ""))).content
        for url in urls
    ]
    assert contents == [b"\xaa", b"\xbb"]
