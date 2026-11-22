"""
Tests for row URLs and pagination cursors when primary key values are not
ordinary text or integers in a typed column: the empty string, binary values,
and numbers in a column with no declared type.
"""

import pytest
from bs4 import BeautifulSoup as Soup

from datasette.app import Datasette
from datasette.database import Database

SCHEMA = """
create table untyped (id primary key, name text);
insert into untyped values
    (1, 'integer'), ('1', 'text'), (1.5, 'float'), (x'0102', 'blob'),
    ('', 'empty'), ('i:1', 'text that looks typed');
create table typed (id text primary key, name text);
insert into typed values ('', 'empty'), ('$empty', 'literal $empty'), ('a', 'a');
create table integers (id integer primary key, name text);
insert into integers values (1, 'one'), (2, 'two');
create table fts_idx (segid, term, pgno, primary key (segid, term)) without rowid;
insert into fts_idx values (3, x'30746865', 1), (3, x'ff', 2);
create table compound (a text, b text, name text, primary key (a, b));
insert into compound values ('x', '', 'x-empty'), ('', '', 'both'), ('', 'y', 'empty-y');
"""

# table -> {name column value: expected row path}
EXPECTED_PATHS = {
    "untyped": {
        "integer": "i:1",
        "text": "1",
        "float": "f:0x1~2E8000000000000p0",
        "blob": "b:0102",
        "empty": "$empty",
        "text that looks typed": "i~3A1",
    },
    "typed": {"empty": "$empty", "literal $empty": "~24empty", "a": "a"},
    # Ordinary keys are unchanged
    "integers": {"one": "1", "two": "2"},
    "fts_idx": {"1": "i:3,b:30746865", "2": "i:3,b:ff"},
    "compound": {
        "x-empty": "x,$empty",
        "both": "$empty,$empty",
        "empty-y": "$empty,y",
    },
}


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
        "".join(f"drop table if exists {table};" for table in EXPECTED_PATHS) + SCHEMA
    )
    try:
        yield ds
    finally:
        ds.close()


def _name_column(table):
    return "pgno" if table == "fts_idx" else "name"


@pytest.mark.asyncio
@pytest.mark.parametrize("table", EXPECTED_PATHS)
async def test_every_row_link_leads_to_its_own_row(ds, table):
    response = await ds.client.get(f"/data/{table}", actor={"id": "root"})
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    name_column = _name_column(table)
    paths = {}
    for tr in soup.select("table.rows-and-columns tbody tr"):
        name = tr.select_one(f"td.col-{name_column}").text
        link = tr.select_one("td.type-pk a")
        assert link["href"] == f"/data/{table}/{tr['data-row']}"
        # Every row has something to click on and its edit and delete buttons
        assert link.text.strip()
        assert len(tr.select("button[data-row-action]")) == 2
        paths[name] = tr["data-row"]
    assert paths == EXPECTED_PATHS[table]

    for name, path in paths.items():
        html = await ds.client.get(f"/data/{table}/{path}")
        assert html.status_code == 200, path
        data = (await ds.client.get(f"/data/{table}/{path}.json")).json()
        assert str(data["row"][name_column]) == name, path


@pytest.mark.asyncio
@pytest.mark.parametrize("table", EXPECTED_PATHS)
@pytest.mark.parametrize("sort", [None, "_sort", "_sort_desc"])
async def test_pagination_visits_every_row_once(ds, table, sort):
    name_column = _name_column(table)
    path = f"/data/{table}.json?_size=1"
    if sort:
        path += f"&{sort}={name_column}"
    seen = []
    for _ in range(20):
        data = (await ds.client.get(path)).json()
        seen.extend(str(row[name_column]) for row in data["rows"])
        # "next" and "next_url" are both set, or both null on the last page
        assert (data["next"] is None) == (data["next_url"] is None)
        if data["next_url"] is None:
            break
        path = data["next_url"].replace("http://localhost", "")
    else:
        pytest.fail("Pagination did not finish")
    assert sorted(seen) == sorted(EXPECTED_PATHS[table])


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
@pytest.mark.parametrize(
    "path,expected_status",
    [
        # There is no row page for a null primary key
        ("/data/typed/$null", 404),
        ("/data/untyped/$null", 404),
        # The type has to match in a column with no declared type
        ("/data/untyped/i:2", 404),
        ("/data/untyped/i:zz", 400),
        ("/data/untyped/b:zz", 400),
        # Compound key links from before $empty existed
        ("/data/compound/x,", 200),
        ("/data/compound/,y", 200),
    ],
)
async def test_row_page_status(ds, path, expected_status):
    response = await ds.client.get(path)
    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_location",
    [
        # Clients that percent-encode ":" and "$" still reach the typed value
        ("/data/untyped/i%3A1", "/data/untyped/i:1"),
        ("/data/untyped/i%3a1", "/data/untyped/i:1"),
        ("/data/typed/%24empty", "/data/typed/$empty"),
        # Other percent-encoding still becomes tilde-encoding
        ("/data/typed/a%2Fb", "/data/typed/a~2Fb"),
    ],
)
async def test_percent_encoded_markers_redirect(ds, path, expected_location):
    response = await ds.client.get(path, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == expected_location


@pytest.mark.asyncio
async def test_row_fragment_for_empty_primary_key(ds):
    response = await ds.client.get(
        "/data/typed/-/fragment?_row=$empty", actor={"id": "root"}
    )
    assert response.status_code == 200
    rows = Soup(response.text, "html.parser").select("tr[data-row]")
    assert [tr["data-row"] for tr in rows] == ["$empty"]


def test_urls_row_encodes_a_list_of_values(ds):
    assert ds.urls.row("data", "typed", [""]) == "/data/typed/$empty"
    assert ds.urls.row("data", "typed", ["a/b", 1]) == "/data/typed/a~2Fb,1"
    assert ds.urls.row("data", "typed", [b"\x01"]) == "/data/typed/b:01"
    # A string is already encoded
    assert ds.urls.row("data", "typed", "a~2Fb") == "/data/typed/a~2Fb"


NULL_KEYS_SCHEMA = """
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
async def null_keys_ds():
    ds = Datasette()
    db = ds.add_database(Database(ds, memory_name="test_null_key_pages"), name="data")
    await db.execute_write_script(
        "drop table if exists single; drop table if exists pair;" + NULL_KEYS_SCHEMA
    )
    try:
        yield ds
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("table,total", [("single", 7), ("pair", 8)])
@pytest.mark.parametrize("size", [1, 2, 3])
@pytest.mark.parametrize(
    "extra", ["", "&_sort=score", "&_sort_desc=score", "&_sort=score&_col=name"]
)
async def test_pagination_past_null_primary_keys(
    null_keys_ds, table, total, size, extra
):
    # https://github.com/simonw/datasette/issues/2145
    path = f"/data/{table}.json?_size={size}{extra}"
    one_page = (
        await null_keys_ds.client.get(f"/data/{table}.json?_size=max{extra}")
    ).json()
    expected = [row["name"] for row in one_page["rows"]]
    assert len(expected) == total
    seen = []
    for _ in range(20):
        data = (await null_keys_ds.client.get(path)).json()
        assert len(data["rows"]) <= size
        seen.extend(row["name"] for row in data["rows"])
        if data["next_url"] is None:
            break
        # The count of rows before the next page agrees with what was shown
        count = await null_keys_ds.client.post(
            f"/data/{table}/-/count?_count=before&_next={data['next']}"
            + extra.replace("&_col=name", "")
        )
        assert count.json()["count"] == len(seen), data["next"]
        path = data["next_url"].replace("http://localhost", "")
    else:
        pytest.fail("Pagination did not finish")
    # Every row once, in the same order as a single page
    assert seen == expected


@pytest.mark.asyncio
async def test_null_primary_key_cursor_ends_with_rowid(null_keys_ds):
    data = (await null_keys_ds.client.get("/data/single.json?_size=1")).json()
    assert data["rows"][0]["name"] == "n1"
    assert data["next"] == "$null,1"
    data = (await null_keys_ds.client.get("/data/pair.json?_size=5")).json()
    assert [row["name"] for row in data["rows"]][-1] == "empty"
    # No null in this key, so no rowid
    assert data["next"] == "$empty,$empty"


@pytest.mark.asyncio
async def test_row_fragment_for_binary_and_typed_primary_keys(ds):
    for path, name in (("b:0102", "blob"), ("i:1", "integer"), ("1", "text")):
        response = await ds.client.get(
            f"/data/untyped/-/fragment?_row={path}", actor={"id": "root"}
        )
        assert response.status_code == 200, path
        rows = Soup(response.text, "html.parser").select("tr[data-row]")
        assert [tr["data-row"] for tr in rows] == [path]
        assert rows[0].select_one("td.col-name").text == name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    [
        # Text, and numbers in a column with a declared type, are strings
        ("/data/typed/a.json", ["a"]),
        ("/data/typed/$empty.json", [""]),
        ("/data/integers/1.json", ["1"]),
        # Typed values in the URL are the value itself
        ("/data/untyped/i:1.json", [1]),
        ("/data/untyped/f:0x1~2E8000000000000p0.json", [1.5]),
        ("/data/untyped/b:0102.json", [{"$base64": True, "encoded": "AQI="}]),
        (
            "/data/fts_idx/i:3,b:ff.json",
            [3, {"$base64": True, "encoded": "/w=="}],
        ),
    ],
)
async def test_primary_key_values_in_row_json(ds, path, expected):
    assert (await ds.client.get(path)).json()["primary_key_values"] == expected


@pytest.mark.asyncio
async def test_binary_primary_key_is_displayed_as_hex(ds):
    response = await ds.client.get("/data/fts_idx", actor={"id": "root"})
    soup = Soup(response.text, "html.parser")
    tr = soup.select_one('tr[data-row="i:3,b:ff"]')
    assert tr["data-row-display"] == "3,binary ff"
    assert tr.select_one("td.type-pk a").text == "3,binary ff"
    assert (
        tr.select_one('button[data-row-action="edit"]')["aria-label"]
        == "Edit row 3,binary ff"
    )

    response = await ds.client.get("/data/fts_idx/i:3,b:ff", actor={"id": "root"})
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    assert soup.select_one("h1.row-heading").text.strip() == "fts_idx: 3, binary ff"
    assert soup.select_one("dl.row-fields")["data-row-display"] == "3,binary ff"
    assert "b'" not in soup.select_one("h1.row-heading").text


@pytest.mark.asyncio
async def test_shape_object_keys(ds):
    data = (await ds.client.get("/data/typed.json?_shape=object")).json()
    assert {key: row["name"] for key, row in data.items()} == {
        "$empty": "empty",
        "~24empty": "literal $empty",
        "a": "a",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["single", "pair"])
async def test_shape_object_is_an_error_for_null_primary_keys(null_keys_ds, table):
    response = await null_keys_ds.client.get(f"/data/{table}.json?_shape=object")
    assert response.status_code == 400
    assert response.json()["error"] == (
        "_shape=object not available for rows with a null primary key"
    )
    # Other shapes still work
    response = await null_keys_ds.client.get(f"/data/{table}.json?_shape=array")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("table,expected_checks", [("single", 1), ("integers", 0)])
async def test_null_key_check_runs_once_for_a_streamed_export(
    ds, null_keys_ds, monkeypatch, table, expected_checks
):
    datasette = null_keys_ds if table == "single" else ds
    checks = []
    original = Database.execute

    async def execute(self, sql, *args, **kwargs):
        if " is null limit 1" in sql:
            checks.append(sql)
        return await original(self, sql, *args, **kwargs)

    monkeypatch.setattr(Database, "execute", execute)
    response = await datasette.client.get(f"/data/{table}.csv?_stream=1&_size=1")
    assert response.status_code == 200
    rows = response.text.strip().splitlines()[1:]
    # Several pages were fetched, and every row is there
    count = (
        await datasette.get_database("data").execute(f"select count(*) from {table}")
    ).single_value()
    assert len(rows) == count > 1
    assert len(checks) == expected_checks


FOREIGN_KEYS_SCHEMA = """
create table targets (id primary key, name text);
insert into targets values
    (1, 'integer one'), ('1', 'text one'), (x'0102', 'blob'), ('', 'empty');
create table plain_targets (id primary key);
insert into plain_targets values (1), ('1'), (x'0102'), ('');
create table sources (
    id integer primary key,
    target references targets(id),
    plain references plain_targets(id),
    data blob
);
insert into sources values
    (1, 1, 1, x'aa'), (2, '1', '1', null), (3, x'0102', x'0102', null),
    (4, '', '', null);
create table blobs (id primary key, data blob);
insert into blobs values (1, x'aa'), ('1', x'bb');
"""


@pytest.fixture
async def fk_ds():
    ds = Datasette()
    db = ds.add_database(Database(ds, memory_name="test_row_keys_fks"), name="data")
    await db.execute_write_script(
        "".join(
            f"drop table if exists {table};"
            for table in ("sources", "targets", "plain_targets", "blobs")
        )
        + FOREIGN_KEYS_SCHEMA
    )
    try:
        yield ds
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("on_row_page", [False, True])
async def test_foreign_key_links_lead_to_the_referenced_row(fk_ds, on_row_page):
    expected = {
        "1": ("i:1", "integer one"),
        "2": ("1", "text one"),
        "3": ("b:0102", "blob"),
    }
    for source_id, (path, name) in expected.items():
        url = f"/data/sources/{source_id}" if on_row_page else "/data/sources"
        soup = Soup((await fk_ds.client.get(url)).text, "html.parser")
        if on_row_page:
            links = {
                column: soup.select_one(f".row-field-{column} a, .col-{column} a")
                for column in ("target", "plain")
            }
        else:
            tr = soup.select_one(f'tr[data-row="{source_id}"]')
            links = {
                column: tr.select_one(f"td.col-{column} a")
                for column in ("target", "plain")
            }
        assert links["target"]["href"] == f"/data/targets/{path}"
        assert links["plain"]["href"] == f"/data/plain_targets/{path}"
        row = (await fk_ds.client.get(f"/data/targets/{path}.json")).json()["row"]
        assert row["name"] == name
    # A binary value is displayed as hex
    soup = Soup((await fk_ds.client.get("/data/sources")).text, "html.parser")
    assert soup.select_one('tr[data-row="3"] td.col-plain a').text == "binary 0102"


@pytest.mark.asyncio
async def test_autocomplete_path(fk_ds):
    response = await fk_ds.client.get("/data/targets/-/autocomplete?q=1")
    rows = response.json()["rows"]
    assert sorted((repr(row["pks"]["id"]), row["path"]) for row in rows) == [
        ("'1'", "1"),
        ("1", "i:1"),
    ]
    for row in rows:
        data = (await fk_ds.client.get(f"/data/targets/{row['path']}.json")).json()
        assert data["row"]["id"] == row["pks"]["id"]


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
@pytest.mark.parametrize("format_", ["csv", "tsv"])
async def test_csv_blob_links_for_untyped_primary_keys(fk_ds, format_):
    # CSV and TSV link to binary values by their content, not by row
    response = await fk_ds.client.get(f"/data/blobs.{format_}")
    assert response.status_code == 200
    urls = [
        line.replace("\t", ",").split(",", 1)[1]
        for line in response.text.strip().splitlines()[1:]
    ]
    assert all("/data/blobs.blob?_blob_column=data&_blob_hash=" in url for url in urls)
    contents = [
        (await fk_ds.client.get(url.replace("http://localhost", ""))).content
        for url in urls
    ]
    assert contents == [b"\xaa", b"\xbb"]


@pytest.mark.asyncio
async def test_shape_object_keeps_numbers_and_text_apart(ds):
    data = (await ds.client.get("/data/untyped.json?_shape=object")).json()
    assert {key: row["name"] for key, row in data.items()} == {
        "i:1": "integer",
        "1": "text",
        "f:0x1~2E8000000000000p0": "float",
        "b:0102": "blob",
        "$empty": "empty",
        "i~3A1": "text that looks typed",
    }
    # Keys of a table with a typed primary key are unchanged
    data = (await ds.client.get("/data/integers.json?_shape=object")).json()
    assert list(data) == ["1", "2"]
