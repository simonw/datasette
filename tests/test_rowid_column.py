"""
Tests for tables that define ordinary columns named rowid, _rowid_, or oid
(https://github.com/simonw/datasette/issues/3110).
"""

import json

import pytest
from bs4 import BeautifulSoup as Soup

from datasette.app import Datasette
from datasette.database import Column, Database, columns_for_results
from datasette.utils import path_from_row_pks
from datasette.utils.sqlite import supports_generated_columns, supports_returning

SCHEMA = """
create table t_rowid (rowid text, name text);
insert into t_rowid values
    ('z', 'first'), ('a', 'second'), ('a', 'third'), ('b', 'fourth'), (null, 'fifth');

create table t_oid (ROWID text, _RoWiD_ text, name text);
insert into t_oid values
    ('r1', 'u1', 'first'), ('r2', 'u2', 'second'), ('r2', 'u2', 'third');

create table t_all3 (rowid text, _rowid_ text, oid text, name text);
insert into t_all3 values
    ('r1', 'u1', 'o1', 'first'),
    ('r2', 'u2', 'o2', 'second'),
    ('r2', 'u2', 'o2', 'third');

create table t_blob_rowid (rowid text, data blob);
insert into t_blob_rowid values ('dup', x'deadbeef'), ('dup', x'cafebabe');

create table t_blob_all3 (rowid text, _rowid_ text, oid text, data blob);
insert into t_blob_all3 values ('r1', 'u1', 'o1', x'010203'), ('r2', 'u2', 'o2', x'040506');

create table normal_rowid (name text);
insert into normal_rowid values ('one'), ('two'), ('three');

create table normal_pk (id integer primary key, name text);
insert into normal_pk values (1, 'one'), (2, 'two');

create table pk_with_rowid (id text primary key, rowid text, name text, score integer);
insert into pk_with_rowid values
    (null, 'dup', 'n1', 2),
    ('b', 'dup', 'b', 1),
    (null, 'dup', 'n2', 1),
    ('', null, 'empty', null),
    (null, null, 'n3', null),
    ('a', 'z', 'a', 2);

create table pk_is_rowid (rowid text primary key, name text, score integer);
insert into pk_is_rowid values
    (null, 'n1', 2),
    ('b', 'b', 1),
    (null, 'n2', 1),
    (null, 'n3', null),
    ('a', 'a', 2);

create table pk_with_all3 (
    id text primary key,
    rowid text,
    _rowid_ text,
    oid text,
    name text,
    score integer
);
insert into pk_with_all3 values
    (null, 'r1', 'u1', 'o1', 'n1', 2),
    ('b', 'r2', 'u2', 'o2', 'b', 1),
    (null, 'r3', 'u3', 'o3', 'n2', 1),
    ('a', 'r4', 'u4', 'o4', 'a', 2);
"""

ALL_TABLES = (
    "t_rowid",
    "t_oid",
    "t_all3",
    "t_blob_rowid",
    "t_blob_all3",
    "normal_rowid",
    "normal_pk",
    "pk_with_rowid",
    "pk_is_rowid",
    "pk_with_all3",
)


@pytest.fixture
async def ds():
    ds = Datasette(
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "insert-row": {"id": "root"},
                        "update-row": {"id": "root"},
                        "delete-row": {"id": "root"},
                        "execute-sql": {"id": "root"},
                    }
                }
            }
        }
    )
    ds.root_enabled = True
    db = ds.add_database(Database(ds, memory_name="test_rowid_column"), name="data")
    await db.execute_write_script(
        "".join(f"drop table if exists {table};" for table in ALL_TABLES) + SCHEMA
    )
    await ds.invoke_startup()
    try:
        yield ds
    finally:
        ds.close()


# --- Unit tests ---


@pytest.mark.parametrize(
    "columns,expected",
    [
        (["id", "name"], "rowid"),
        (["rowid", "name"], "_rowid_"),
        (["ROWID", "name"], "_rowid_"),
        (["rowid", "_rowid_", "name"], "oid"),
        (["ROWID", "_RoWiD_", "name"], "oid"),
        (["rowid", "_rowid_", "oid", "name"], None),
        (["RowId", "_RowId_", "OID"], None),
        (["_rowid_", "name"], "rowid"),
        (["oid", "name"], "rowid"),
    ],
)
def test_rowid_column_name(columns, expected):
    from datasette.utils import rowid_column_name

    assert rowid_column_name(columns) == expected


@pytest.mark.parametrize(
    "rowid_col,expected",
    [
        ("rowid", "rowid"),
        ("_rowid_", "_rowid_ as _rowid_"),
        ("oid", "oid as oid"),
    ],
)
def test_rowid_select_expr(rowid_col, expected):
    from datasette.utils import rowid_select_expr

    assert rowid_select_expr(rowid_col) == expected


def test_path_from_row_pks_custom_rowid_column():
    row = {"_rowid_": 7, "rowid": "abc", "name": "test"}
    assert path_from_row_pks(row, [], True, rowid_column="_rowid_") == "7"
    assert path_from_row_pks(row, [], True, quote=False, rowid_column="_rowid_") == "7"
    # Default remains "rowid"
    assert path_from_row_pks({"rowid": 3, "name": "x"}, [], True) == "3"


@pytest.mark.asyncio
async def test_columns_for_results_shadowed_rowid(ds):
    db = ds.get_database("data")
    table_columns = await db.table_column_details("t_rowid")
    columns = columns_for_results(table_columns, ["_rowid_", "rowid", "name"])
    assert columns["_rowid_"] == Column("_rowid_", type="INTEGER")
    assert columns["rowid"] is table_columns["rowid"]
    assert columns["rowid"].type == "TEXT"

    oid_table_columns = await db.table_column_details("t_oid")
    oid_columns = columns_for_results(
        oid_table_columns, ["oid", "ROWID", "_RoWiD_", "name"]
    )
    assert oid_columns["oid"] == Column("oid", type="INTEGER")
    assert oid_columns["ROWID"] is oid_table_columns["ROWID"]


# --- Table with `rowid` column and no primary key ---


@pytest.mark.asyncio
async def test_rowid_table_json_and_html(ds):
    data = (await ds.client.get("/data/t_rowid.json?_extra=columns")).json()
    assert data["columns"] == ["_rowid_", "rowid", "name"]
    assert data["rows"] == [
        {"_rowid_": 1, "rowid": "z", "name": "first"},
        {"_rowid_": 2, "rowid": "a", "name": "second"},
        {"_rowid_": 3, "rowid": "a", "name": "third"},
        {"_rowid_": 4, "rowid": "b", "name": "fourth"},
        {"_rowid_": 5, "rowid": None, "name": "fifth"},
    ]

    html = await ds.client.get("/data/t_rowid", actor={"id": "root"})
    assert html.status_code == 200
    soup = Soup(html.text, "html.parser")
    rows = soup.select("table.rows-and-columns tbody tr")
    assert len(rows) == 5
    links = {}
    for tr in rows:
        name = tr.select_one("td.col-name").text
        link = tr.select_one("td.type-pk a")
        assert link is not None
        assert link["href"] == f"/data/t_rowid/{tr['data-row']}"
        assert len(tr.select("button[data-row-action]")) == 2
        links[name] = tr["data-row"]
    assert links == {
        "first": "1",
        "second": "2",
        "third": "3",
        "fourth": "4",
        "fifth": "5",
    }
    # User's text `rowid` column values are rendered intact (not overwritten by integer rowid)
    rowid_cells = [tr.select_one("td.col-rowid").text.strip() for tr in rows]
    assert rowid_cells == ["z", "a", "a", "b", ""]

    # insertRow data records rowidColumn so edit-tools.js does not read text rowid column
    page_data = None
    for script in soup.find_all("script"):
        text = script.string or ""
        marker = "window._datasetteTableData = "
        if marker in text:
            page_data = json.loads(text.split(marker, 1)[1].rstrip().rstrip(";"))
            break
    assert page_data is not None
    assert page_data["insertRow"]["rowidColumn"] == "_rowid_"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        "",
        "&_sort=name",
        "&_sort_desc=name",
        "&_sort=rowid",
        "&_sort_desc=rowid",
        "&_sort=_rowid_",
        "&_sort_desc=_rowid_",
        "&_sort=name&_col=rowid",
    ],
)
@pytest.mark.parametrize("size", [1, 2])
async def test_rowid_table_pagination(ds, extra, size):
    one_page = (await ds.client.get(f"/data/t_rowid.json?_size=max{extra}")).json()
    expected = [row["_rowid_"] for row in one_page["rows"]]
    assert len(expected) == 5

    path = f"/data/t_rowid.json?_size={size}{extra}"
    seen = []
    for _ in range(15):
        data = (await ds.client.get(path)).json()
        assert len(data["rows"]) <= size
        seen.extend(row["_rowid_"] for row in data["rows"])
        assert (data["next"] is None) == (data["next_url"] is None)
        if data["next_url"] is None:
            break
        count_resp = await ds.client.post(
            f"/data/t_rowid/-/count?_count=before&_next={data['next']}"
            + extra.replace("&_col=rowid", "")
        )
        assert count_resp.status_code == 200, count_resp.text
        assert count_resp.json()["count"] == len(seen), (extra, data["next"])
        path = data["next_url"].replace("http://localhost", "")
    else:
        pytest.fail("Pagination did not finish")
    assert seen == expected


@pytest.mark.asyncio
async def test_rowid_table_row_page(ds):
    # Real rowid works and returns only that single row
    r1 = await ds.client.get("/data/t_rowid/1.json")
    assert r1.status_code == 200
    assert r1.json()["primary_keys"] == ["_rowid_"]
    assert r1.json()["row"] == {"_rowid_": 1, "rowid": "z", "name": "first"}

    r2_arr = await ds.client.get("/data/t_rowid/2.json?_shape=array")
    assert r2_arr.status_code == 200
    assert r2_arr.json() == [{"_rowid_": 2, "rowid": "a", "name": "second"}]

    r5_html = await ds.client.get("/data/t_rowid/5")
    assert r5_html.status_code == 200
    assert "fifth" in r5_html.text

    # Text column values do NOT resolve as row URLs
    assert (await ds.client.get("/data/t_rowid/a.json")).status_code == 404
    assert (await ds.client.get("/data/t_rowid/z.json")).status_code == 404
    assert (await ds.client.get("/data/t_rowid/None.json")).status_code == 404


@pytest.mark.asyncio
async def test_rowid_table_delete_only_deletes_one_row(ds):
    db = ds.get_database("data")
    # Attempting to delete by the text `rowid` column value is a 404 and deletes nothing
    bad = await ds.client.post("/data/t_rowid/a/-/delete", actor={"id": "root"})
    assert bad.status_code == 404
    assert (await db.execute("select count(*) from t_rowid")).single_value() == 5

    # Deleting row 2 deletes ONLY 'second', leaving 'third' (which also has rowid='a')
    resp = await ds.client.post("/data/t_rowid/2/-/delete", actor={"id": "root"})
    assert resp.status_code == 200, resp.text
    remaining = [
        row["name"]
        for row in (await db.execute("select name from t_rowid order by _rowid_")).rows
    ]
    assert remaining == ["first", "third", "fourth", "fifth"]


@pytest.mark.asyncio
async def test_rowid_table_update_only_updates_one_row(ds):
    db = ds.get_database("data")
    bad = await ds.client.post(
        "/data/t_rowid/a/-/update",
        json={"update": {"name": "nope"}},
        actor={"id": "root"},
    )
    assert bad.status_code == 404

    resp = await ds.client.post(
        "/data/t_rowid/3/-/update",
        json={"update": {"rowid": "updated", "name": "third-updated"}, "return": True},
        actor={"id": "root"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"] == [
        {"_rowid_": 3, "rowid": "updated", "name": "third-updated"}
    ]
    # Row 2 (which also had rowid='a') is untouched
    rows = (
        await db.execute(
            "select _rowid_ as _rowid_, rowid, name from t_rowid where _rowid_ in (2, 3) order by _rowid_"
        )
    ).dicts()
    assert rows == [
        {"_rowid_": 2, "rowid": "a", "name": "second"},
        {"_rowid_": 3, "rowid": "updated", "name": "third-updated"},
    ]


@pytest.mark.asyncio
async def test_rowid_table_fragment_insert_upsert_autocomplete_and_view_row(ds):
    # /-/fragment?_row= selects by real rowid, not text rowid
    frag = await ds.client.get("/data/t_rowid/-/fragment?_row=2", actor={"id": "root"})
    assert frag.status_code == 200
    frag_rows = Soup(frag.text, "html.parser").select("tr[data-row]")
    assert [tr["data-row"] for tr in frag_rows] == ["2"]
    assert frag_rows[0].select_one("td.col-name").text == "second"

    frag_text = await ds.client.get(
        "/data/t_rowid/-/fragment?_row=a", actor={"id": "root"}
    )
    assert frag_text.status_code == 200
    assert Soup(frag_text.text, "html.parser").select("tr[data-row]") == []

    # /-/insert returns the inserted row with its assigned implicit rowid
    ins = await ds.client.post(
        "/data/t_rowid/-/insert",
        json={"row": {"rowid": "a", "name": "sixth"}},
        actor={"id": "root"},
    )
    assert ins.status_code == 201, ins.text
    assert ins.json()["rows"] == [{"_rowid_": 6, "rowid": "a", "name": "sixth"}]

    # /-/upsert uses _rowid_ and updates only that row
    ups = await ds.client.post(
        "/data/t_rowid/-/upsert",
        json={"row": {"_rowid_": 6, "rowid": "upserted", "name": "sixth-up"}},
        actor={"id": "root"},
    )
    assert ups.status_code == 200, ups.text
    assert ups.json()["rows"] == [
        {"_rowid_": 6, "rowid": "upserted", "name": "sixth-up"}
    ]

    # /-/autocomplete resolves by _rowid_
    auto = await ds.client.get("/data/t_rowid/-/autocomplete?q=second")
    assert auto.status_code == 200
    assert auto.json()["rows"] == [
        {"pks": {"_rowid_": 2}, "path": "2", "label": "second"}
    ]

    # /-/execute-write "View row" link uses real rowid (7), not text rowid ('a')
    exec_resp = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        data={"sql": "insert into t_rowid (rowid, name) values ('a', 'seventh')"},
    )
    assert exec_resp.status_code == 200
    assert '<a href="/data/t_rowid/7">View row</a>' in exec_resp.text


@pytest.mark.asyncio
async def test_rowid_table_blob_links_html_and_csv(ds):
    html = await ds.client.get("/data/t_blob_rowid")
    assert html.status_code == 200
    soup = Soup(html.text, "html.parser")
    hrefs = [a["href"] for a in soup.select("a.blob-download")]
    assert hrefs == [
        "/data/t_blob_rowid/1.blob?_blob_column=data",
        "/data/t_blob_rowid/2.blob?_blob_column=data",
    ]
    assert (await ds.client.get(hrefs[0])).content == b"\xde\xad\xbe\xef"
    assert (await ds.client.get(hrefs[1])).content == b"\xca\xfe\xba\xbe"

    csv_resp = await ds.client.get("/data/t_blob_rowid.csv")
    assert csv_resp.status_code == 200
    lines = csv_resp.text.strip().splitlines()
    assert lines[0] == "_rowid_,rowid,data"
    urls = [line.split(",")[2].replace("http://localhost", "") for line in lines[1:]]
    assert urls == hrefs


# --- Table with both `ROWID` and `_RoWiD_` columns -> falls back to `oid` ---


@pytest.mark.asyncio
async def test_oid_fallback_table(ds):
    data = (await ds.client.get("/data/t_oid.json?_extra=columns")).json()
    assert data["columns"] == ["oid", "ROWID", "_RoWiD_", "name"]
    assert [row["oid"] for row in data["rows"]] == [1, 2, 3]
    assert [row["ROWID"] for row in data["rows"]] == ["r1", "r2", "r2"]

    # Pagination
    seen = []
    path = "/data/t_oid.json?_size=1"
    for _ in range(10):
        page = (await ds.client.get(path)).json()
        seen.extend(row["name"] for row in page["rows"])
        if page["next_url"] is None:
            break
        count_resp = await ds.client.post(
            f"/data/t_oid/-/count?_count=before&_next={page['next']}"
        )
        assert count_resp.json()["count"] == len(seen)
        path = page["next_url"].replace("http://localhost", "")
    assert seen == ["first", "second", "third"]

    # Row page, fragment, update, delete, execute-write
    r2 = await ds.client.get("/data/t_oid/2.json")
    assert r2.status_code == 200
    assert r2.json()["primary_keys"] == ["oid"]
    assert r2.json()["row"]["name"] == "second"
    assert (await ds.client.get("/data/t_oid/r2.json")).status_code == 404

    frag = await ds.client.get("/data/t_oid/-/fragment?_row=2", actor={"id": "root"})
    assert frag.status_code == 200
    assert [
        tr["data-row"] for tr in Soup(frag.text, "html.parser").select("tr[data-row]")
    ] == ["2"]

    upd = await ds.client.post(
        "/data/t_oid/2/-/update",
        json={"update": {"name": "second-updated"}, "return": True},
        actor={"id": "root"},
    )
    assert upd.status_code == 200
    assert upd.json()["rows"][0]["name"] == "second-updated"

    dele = await ds.client.post("/data/t_oid/2/-/delete", actor={"id": "root"})
    assert dele.status_code == 200
    db = ds.get_database("data")
    remaining = [
        row["name"]
        for row in (await db.execute("select name from t_oid order by oid")).rows
    ]
    assert remaining == ["first", "third"]

    exec_resp = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        data={
            "sql": "insert into t_oid (ROWID, _RoWiD_, name) values ('r9', 'u9', 'fourth')"
        },
    )
    assert exec_resp.status_code == 200
    assert '<a href="/data/t_oid/4">View row</a>' in exec_resp.text


# --- Table with all three (`rowid`, `_rowid_`, `oid`) and no PK -> treated like a view ---


@pytest.mark.asyncio
async def test_all_three_shadowed_treated_like_view(ds):
    data = (await ds.client.get("/data/t_all3.json?_extra=columns")).json()
    assert data["columns"] == ["rowid", "_rowid_", "oid", "name"]
    assert len(data["rows"]) == 3

    # HTML has no Link column, no row links, no edit/delete row actions
    html = await ds.client.get("/data/t_all3", actor={"id": "root"})
    assert html.status_code == 200
    soup = Soup(html.text, "html.parser")
    assert soup.select("td.type-pk a") == []
    assert soup.select("button[data-row-action]") == []

    # Offset pagination works both unsorted and sorted, and count_before agrees
    for extra in ("", "&_sort=name", "&_sort_desc=name"):
        seen = []
        path = f"/data/t_all3.json?_size=1{extra}"
        for _ in range(10):
            page = (await ds.client.get(path)).json()
            seen.extend(row["name"] for row in page["rows"])
            if page["next_url"] is None:
                break
            count_resp = await ds.client.post(
                f"/data/t_all3/-/count?_count=before&_next={page['next']}{extra}"
            )
            assert count_resp.status_code == 200
            assert count_resp.json()["count"] == len(seen)
            path = page["next_url"].replace("http://localhost", "")
        assert sorted(seen) == ["first", "second", "third"]

    # No row page, no delete, no update, no fragment, no autocomplete
    assert (await ds.client.get("/data/t_all3/1")).status_code == 404
    assert (await ds.client.get("/data/t_all3/r1")).status_code == 404
    assert (
        await ds.client.post("/data/t_all3/1/-/delete", actor={"id": "root"})
    ).status_code == 404
    assert (
        await ds.client.post(
            "/data/t_all3/1/-/update",
            json={"update": {"name": "x"}},
            actor={"id": "root"},
        )
    ).status_code == 404
    assert (
        await ds.client.get("/data/t_all3/-/fragment?_row=1", actor={"id": "root"})
    ).status_code == 400
    assert (
        await ds.client.get("/data/t_all3/-/autocomplete?q=first")
    ).status_code == 400

    # Insert still works (returns inserted row when SQLite supports RETURNING);
    # upsert is rejected cleanly
    ins = await ds.client.post(
        "/data/t_all3/-/insert",
        json={"row": {"rowid": "r4", "_rowid_": "u4", "oid": "o4", "name": "fourth"}},
        actor={"id": "root"},
    )
    assert ins.status_code == 201, ins.text
    if supports_returning():
        assert ins.json()["rows"] == [
            {"rowid": "r4", "_rowid_": "u4", "oid": "o4", "name": "fourth"}
        ]
    else:
        assert ins.json()["rows"] == []
    db = ds.get_database("data")
    names = [row["name"] for row in (await db.execute("select name from t_all3")).rows]
    assert "fourth" in names
    ups = await ds.client.post(
        "/data/t_all3/-/upsert",
        json={"row": {"rowid": "r4", "_rowid_": "u4", "oid": "o4", "name": "x"}},
        actor={"id": "root"},
    )
    assert ups.status_code == 400

    # Execute-write does not render a broken "View row" link
    exec_resp = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        data={
            "sql": "insert into t_all3 (rowid, _rowid_, oid, name) values ('r5', 'u5', 'o5', 'fifth')"
        },
    )
    assert exec_resp.status_code == 200
    assert "View row" not in exec_resp.text


@pytest.mark.asyncio
async def test_all_three_shadowed_blob_links_use_content_hash(ds):
    html = await ds.client.get("/data/t_blob_all3")
    assert html.status_code == 200
    soup = Soup(html.text, "html.parser")
    hrefs = [a["href"] for a in soup.select("a.blob-download")]
    assert len(hrefs) == 2
    assert all(
        "/data/t_blob_all3.blob?_blob_column=data&_blob_hash=" in h for h in hrefs
    )
    assert [(await ds.client.get(h)).content for h in hrefs] == [
        b"\x01\x02\x03",
        b"\x04\x05\x06",
    ]

    csv_resp = await ds.client.get("/data/t_blob_all3.csv")
    assert csv_resp.status_code == 200
    csv_urls = [
        line.split(",")[3].replace("http://localhost", "")
        for line in csv_resp.text.strip().splitlines()[1:]
    ]
    assert all(
        "/data/t_blob_all3.blob?_blob_column=data&_blob_hash=" in u for u in csv_urls
    )
    assert [(await ds.client.get(u)).content for u in csv_urls] == [
        b"\x01\x02\x03",
        b"\x04\x05\x06",
    ]


# --- Generated `rowid` column ---


@pytest.mark.asyncio
@pytest.mark.skipif(
    not supports_generated_columns(), reason="Needs SQLite generated columns"
)
async def test_generated_rowid_column_uses_alternate_alias(ds):
    db = ds.get_database("data")
    await db.execute_write_script("""
        drop table if exists t_gen;
        create table t_gen (name text, rowid text generated always as (name) virtual);
        insert into t_gen (name) values ('alpha'), ('alpha'), ('beta');
        """)
    data = (await ds.client.get("/data/t_gen.json?_extra=columns")).json()
    assert data["columns"] == ["_rowid_", "name", "rowid"]
    assert [row["_rowid_"] for row in data["rows"]] == [1, 2, 3]
    r2 = await ds.client.get("/data/t_gen/2.json")
    assert r2.status_code == 200
    assert r2.json()["row"] == {"_rowid_": 2, "name": "alpha", "rowid": "alpha"}


# --- Tables with explicit primary key + `rowid` column + NULL primary keys (#2145) ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,total",
    [("pk_with_rowid", 6), ("pk_is_rowid", 5), ("pk_with_all3", 4)],
)
@pytest.mark.parametrize("size", [1, 2])
@pytest.mark.parametrize(
    "extra", ["", "&_sort=score", "&_sort_desc=score", "&_sort=score&_col=name"]
)
async def test_null_pk_pagination_with_rowid_column(ds, table, total, size, extra):
    one_page = (await ds.client.get(f"/data/{table}.json?_size=max{extra}")).json()
    expected = [row["name"] for row in one_page["rows"]]
    assert len(expected) == total

    path = f"/data/{table}.json?_size={size}{extra}"
    seen = []
    for _ in range(20):
        data = (await ds.client.get(path)).json()
        assert len(data["rows"]) <= size
        seen.extend(row["name"] for row in data["rows"])
        if data["next_url"] is None:
            break
        count_resp = await ds.client.post(
            f"/data/{table}/-/count?_count=before&_next={data['next']}"
            + extra.replace("&_col=name", "")
        )
        assert count_resp.status_code == 200, count_resp.text
        assert count_resp.json()["count"] == len(seen), (table, extra, data["next"])
        path = data["next_url"].replace("http://localhost", "")
    else:
        pytest.fail("Pagination did not finish")
    assert seen == expected


@pytest.mark.asyncio
async def test_pk_is_rowid_non_null_row_page_still_uses_pk(ds):
    resp = await ds.client.get("/data/pk_is_rowid/a.json")
    assert resp.status_code == 200
    assert resp.json()["primary_keys"] == ["rowid"]
    assert resp.json()["row"] == {"rowid": "a", "name": "a", "score": 2}


# --- Controls: normal rowid and explicit PK tables unchanged ---


@pytest.mark.asyncio
async def test_normal_tables_unaffected(ds):
    r_rowid = (await ds.client.get("/data/normal_rowid.json?_extra=columns")).json()
    assert r_rowid["columns"] == ["rowid", "name"]
    assert r_rowid["rows"] == [
        {"rowid": 1, "name": "one"},
        {"rowid": 2, "name": "two"},
        {"rowid": 3, "name": "three"},
    ]
    r_row = (await ds.client.get("/data/normal_rowid/2.json")).json()
    assert r_row["primary_keys"] == ["rowid"]
    assert r_row["row"] == {"rowid": 2, "name": "two"}

    r_pk = (await ds.client.get("/data/normal_pk.json?_extra=columns")).json()
    assert r_pk["columns"] == ["id", "name"]
    assert r_pk["rows"] == [{"id": 1, "name": "one"}, {"id": 2, "name": "two"}]


@pytest.mark.asyncio
async def test_rowid_table_update_alter_and_empty_update(ds):
    # Empty update is a no-op 200
    empty = await ds.client.post(
        "/data/t_rowid/2/-/update",
        json={"update": {}, "return": True},
        actor={"id": "root"},
    )
    assert empty.status_code == 200
    assert empty.json()["rows"] == [{"_rowid_": 2, "rowid": "a", "name": "second"}]

    # Update with alter=True adds the missing column and updates only row 2
    ds_alter = Datasette(
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "update-row": {"id": "root"},
                        "alter-table": {"id": "root"},
                    }
                }
            }
        }
    )
    db = ds_alter.add_database(
        Database(ds_alter, memory_name="test_rowid_update_alter"), name="data"
    )
    await db.execute_write_script(
        "drop table if exists t_rowid;"
        "create table t_rowid (rowid text, name text);"
        "insert into t_rowid values ('a', 'second'), ('a', 'third');"
    )
    try:
        resp = await ds_alter.client.post(
            "/data/t_rowid/1/-/update",
            json={"update": {"note": "added"}, "alter": True, "return": True},
            actor={"id": "root"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"] == [
            {"_rowid_": 1, "rowid": "a", "name": "second", "note": "added"}
        ]
        other = (await ds_alter.client.get("/data/t_rowid/2.json")).json()["row"]
        assert other == {"_rowid_": 2, "rowid": "a", "name": "third", "note": None}
    finally:
        ds_alter.close()


@pytest.mark.asyncio
async def test_all_three_shadowed_insert_variants(ds):
    db = ds.get_database("data")
    # Default-values empty row insert with return
    r_empty = await ds.client.post(
        "/data/t_all3/-/insert",
        json={"row": {}},
        actor={"id": "root"},
    )
    assert r_empty.status_code == 201, r_empty.text
    if supports_returning():
        assert r_empty.json()["rows"] == [
            {"rowid": None, "_rowid_": None, "oid": None, "name": None}
        ]
    else:
        assert r_empty.json()["rows"] == []

    # Batch insert with return=True
    r_batch = await ds.client.post(
        "/data/t_all3/-/insert",
        json={
            "rows": [
                {"rowid": "x1", "_rowid_": "y1", "oid": "z1", "name": "b1"},
                {"rowid": "x2", "_rowid_": "y2", "oid": "z2", "name": "b2"},
            ],
            "return": True,
        },
        actor={"id": "root"},
    )
    assert r_batch.status_code == 201, r_batch.text
    if supports_returning():
        assert r_batch.json()["rows"] == [
            {"rowid": "x1", "_rowid_": "y1", "oid": "z1", "name": "b1"},
            {"rowid": "x2", "_rowid_": "y2", "oid": "z2", "name": "b2"},
        ]
    else:
        assert r_batch.json()["rows"] == []
    inserted_names = [
        row["name"] for row in (await db.execute("select name from t_all3")).rows
    ]
    assert inserted_names[-3:] == [None, "b1", "b2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("can_return", [False, True])
async def test_all_three_shadowed_insert_gates_returning(ds, monkeypatch, can_return):
    if can_return and not supports_returning():
        pytest.skip("SQLite does not support RETURNING")
    monkeypatch.setattr("datasette.utils.sqlite.supports_returning", lambda: can_return)
    db = ds.get_database("data")
    sql_log = []
    await db.execute_write_fn(lambda conn: conn.set_trace_callback(sql_log.append))
    try:
        # Single-row insert into all-3-shadowed table with no PK forces return=True
        single = await ds.client.post(
            "/data/t_all3/-/insert",
            json={
                "row": {
                    "rowid": "nr1",
                    "_rowid_": "nu1",
                    "oid": "no1",
                    "name": "single-no-pk",
                }
            },
            actor={"id": "root"},
        )
        assert single.status_code == 201, single.text
        # Empty default-values row insert
        empty = await ds.client.post(
            "/data/t_all3/-/insert",
            json={"row": {}},
            actor={"id": "root"},
        )
        assert empty.status_code == 201, empty.text
        # Batch insert with return=True
        batch = await ds.client.post(
            "/data/t_all3/-/insert",
            json={
                "rows": [
                    {
                        "rowid": "nr2",
                        "_rowid_": "nu2",
                        "oid": "no2",
                        "name": "batch-no-pk",
                    }
                ],
                "return": True,
            },
            actor={"id": "root"},
        )
        assert batch.status_code == 201, batch.text
        # Explicit PK + all-3-shadowed table with non-null PK
        pk_ins = await ds.client.post(
            "/data/pk_with_all3/-/insert",
            json={
                "row": {
                    "id": "c",
                    "rowid": "r5",
                    "_rowid_": "u5",
                    "oid": "o5",
                    "name": "c",
                    "score": 5,
                }
            },
            actor={"id": "root"},
        )
        assert pk_ins.status_code == 201, pk_ins.text
        assert pk_ins.json()["rows"] == [
            {
                "id": "c",
                "rowid": "r5",
                "_rowid_": "u5",
                "oid": "o5",
                "name": "c",
                "score": 5,
            }
        ]
        # Explicit PK + all-3-shadowed table with null PK
        pk_null_ins = await ds.client.post(
            "/data/pk_with_all3/-/insert",
            json={
                "row": {
                    "id": None,
                    "rowid": "r6",
                    "_rowid_": "u6",
                    "oid": "o6",
                    "name": "n-null",
                    "score": 6,
                }
            },
            actor={"id": "root"},
        )
        assert pk_null_ins.status_code == 201, pk_null_ins.text
    finally:
        await db.execute_write_fn(lambda conn: conn.set_trace_callback(None))

    used_returning = any("returning" in sql.lower() for sql in sql_log)
    if can_return:
        assert used_returning
        assert single.json()["rows"] == [
            {"rowid": "nr1", "_rowid_": "nu1", "oid": "no1", "name": "single-no-pk"}
        ]
        assert empty.json()["rows"] == [
            {"rowid": None, "_rowid_": None, "oid": None, "name": None}
        ]
        assert batch.json()["rows"] == [
            {"rowid": "nr2", "_rowid_": "nu2", "oid": "no2", "name": "batch-no-pk"}
        ]
        assert pk_null_ins.json()["rows"] == [
            {
                "id": None,
                "rowid": "r6",
                "_rowid_": "u6",
                "oid": "o6",
                "name": "n-null",
                "score": 6,
            }
        ]
    else:
        assert not used_returning, f"Unexpected RETURNING SQL: {sql_log}"
        assert single.json()["rows"] == []
        assert empty.json()["rows"] == []
        assert batch.json()["rows"] == []
        assert pk_null_ins.json()["rows"] == []

    # Regardless of RETURNING support, all rows are persisted in SQLite
    t_all3_names = [
        row["name"] for row in (await db.execute("select name from t_all3")).rows
    ]
    assert t_all3_names[-3:] == ["single-no-pk", None, "batch-no-pk"]
    pk_names = [
        row["name"] for row in (await db.execute("select name from pk_with_all3")).rows
    ]
    assert "c" in pk_names
    assert "n-null" in pk_names


@pytest.mark.asyncio
async def test_without_rowid_table_with_rowid_column_insert(ds):
    db = ds.get_database("data")
    await db.execute_write_script("""
        drop table if exists without_rowid_shadow;
        create table without_rowid_shadow (
            rowid text primary key,
            name text
        ) without rowid;
        """)
    ins = await ds.client.post(
        "/data/without_rowid_shadow/-/insert",
        json={"row": {"rowid": "k1", "name": "first"}},
        actor={"id": "root"},
    )
    assert ins.status_code == 201, ins.text
    assert ins.json()["rows"] == [{"rowid": "k1", "name": "first"}]


@pytest.mark.asyncio
async def test_rowid_table_filters_shapes_and_streamed_csv(ds):
    # Filtering by user's `rowid` column matches rows with text 'a'
    by_text = (await ds.client.get("/data/t_rowid.json?rowid=a")).json()
    assert [r["name"] for r in by_text["rows"]] == ["second", "third"]

    # Filtering by real `_rowid_` matches only row 2
    by_real = (await ds.client.get("/data/t_rowid.json?_rowid___exact=2")).json()
    assert by_real["rows"] == [{"_rowid_": 2, "rowid": "a", "name": "second"}]

    # JSON shapes
    assert (await ds.client.get("/data/t_rowid.json?_shape=arrayfirst")).json() == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert (await ds.client.get("/data/t_rowid.json?_shape=arrays")).json()["rows"][
        0
    ] == [1, "z", "first"]
    assert (await ds.client.get("/data/t_rowid.json?_shape=object")).status_code == 400

    # Streamed CSV across multiple pages for t_rowid, t_all3, and pk_with_rowid
    for table, expected_count in (
        ("t_rowid", 5),
        ("t_all3", 3),
        ("pk_with_rowid", 6),
    ):
        resp = await ds.client.get(f"/data/{table}.csv?_stream=1&_size=1")
        assert resp.status_code == 200
        lines = resp.text.strip().splitlines()
        assert len(lines) - 1 == expected_count


@pytest.mark.asyncio
async def test_execute_sql_ui_and_api_explorer_for_shadowed_rowid(ds):
    html = await ds.client.get("/data/t_rowid", actor={"id": "root"})
    soup = Soup(html.text, "html.parser")
    page_data = None
    for script in soup.find_all("script"):
        text = script.string or ""
        marker = "window._datasetteTableData = "
        if marker in text:
            page_data = json.loads(text.split(marker, 1)[1].rstrip().rstrip(";"))
            break
    assert page_data["executeSql"]["columns"] == [
        {"name": "_rowid_", "type": "integer"},
        {"name": "rowid", "type": "text"},
        {"name": "name", "type": "text"},
    ]

    html_all3 = await ds.client.get("/data/t_all3", actor={"id": "root"})
    soup_all3 = Soup(html_all3.text, "html.parser")
    page_data_all3 = None
    for script in soup_all3.find_all("script"):
        text = script.string or ""
        marker = "window._datasetteTableData = "
        if marker in text:
            page_data_all3 = json.loads(text.split(marker, 1)[1].rstrip().rstrip(";"))
            break
    assert [c["name"] for c in page_data_all3["executeSql"]["columns"]] == [
        "rowid",
        "_rowid_",
        "oid",
        "name",
    ]
    assert page_data_all3["insertRow"]["rowidColumn"] is None
    assert "upsertPath" not in page_data_all3["insertRow"]

    # API explorer reflects _rowid_ for t_rowid and omits upsert for t_all3
    api_resp = await ds.client.get("/-/api", actor={"id": "root"})
    assert api_resp.status_code == 200
    assert "/data/t_rowid/-/upsert" in api_resp.text
    assert "/data/t_all3/-/insert" in api_resp.text
    assert "/data/t_all3/-/upsert" not in api_resp.text


@pytest.mark.asyncio
async def test_render_cell_extra_pks_for_shadowed_rowid(ds):
    from datasette import hookimpl
    from datasette.plugins import pm

    seen = {}

    class Plugin:
        __name__ = "RecordShadowedRowidPks"

        @hookimpl
        def render_cell(self, table, pks):
            seen.setdefault(table, pks)

    pm.register(Plugin(), name="undo_record_shadowed_rowid_pks")
    try:
        await ds.client.get("/data/t_rowid.json?_extra=render_cell")
        await ds.client.get("/data/t_all3.json?_extra=render_cell")
    finally:
        pm.unregister(name="undo_record_shadowed_rowid_pks")
    assert seen["t_rowid"] == ["_rowid_"]
    assert seen["t_all3"] == []


@pytest.mark.asyncio
async def test_fts_search_with_rowid_column():
    ds = Datasette()
    db = ds.add_database(Database(ds, memory_name="test_rowid_fts_search"), name="data")
    await db.execute_write_script("""
        drop table if exists notes_fts;
        drop table if exists notes;
        create table notes (rowid text, body text);
        insert into notes values ('a', 'hello world'), ('a', 'goodbye moon');
        create virtual table notes_fts using fts5(body, content='notes');
        insert into notes_fts (rowid, body) select _rowid_, body from notes;
        """)
    try:
        resp = await ds.client.get("/data/notes.json?_search=hello")
        assert resp.status_code == 200
        assert resp.json()["rows"] == [
            {"_rowid_": 1, "rowid": "a", "body": "hello world"}
        ]
        col_resp = await ds.client.get("/data/notes.json?_search_body=hello")
        assert col_resp.status_code == 200
        assert col_resp.json()["rows"] == [
            {"_rowid_": 1, "rowid": "a", "body": "hello world"}
        ]
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_fts_search_on_view_exposing_rowid():
    ds = Datasette(
        config={
            "databases": {
                "data": {
                    "tables": {
                        "docs_view": {
                            "fts_table": "docs_fts",
                        }
                    }
                }
            }
        }
    )
    db = ds.add_database(
        Database(ds, memory_name="test_view_exposing_rowid_fts"), name="data"
    )
    await db.execute_write_script("""
        drop view if exists docs_view;
        drop table if exists docs_fts;
        drop table if exists docs;
        create table docs (id integer primary key, body text);
        insert into docs values (10, 'hello world'), (20, 'goodbye moon');
        create virtual table docs_fts using fts5(body, content='docs', content_rowid='id');
        insert into docs_fts (rowid, body) select id, body from docs;
        create view docs_view as select id as rowid, body from docs;
        """)
    try:
        resp = await ds.client.get("/data/docs_view.json?_search=hello")
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"] == [{"rowid": 10, "body": "hello world"}]

        col_resp = await ds.client.get("/data/docs_view.json?_search_body=hello")
        assert col_resp.status_code == 200, col_resp.text
        assert col_resp.json()["rows"] == [{"rowid": 10, "body": "hello world"}]

        explicit_qs = await ds.client.get(
            "/data/docs_view.json?_search=hello&_fts_table=docs_fts&_fts_pk=rowid"
        )
        assert explicit_qs.status_code == 200, explicit_qs.text
        assert explicit_qs.json()["rows"] == [{"rowid": 10, "body": "hello world"}]
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_fts_column_search_honors_configured_fts_pk_rowid():
    ds = Datasette(
        config={
            "databases": {
                "data": {
                    "tables": {
                        "articles": {
                            "fts_table": "articles_fts",
                            "fts_pk": "rowid",
                        }
                    }
                }
            }
        }
    )
    db = ds.add_database(
        Database(ds, memory_name="test_fts_pk_rowid_column_search"), name="data"
    )
    await db.execute_write_script("""
        drop table if exists articles_fts;
        drop table if exists articles;
        create table articles (rowid integer, title text, body text);
        insert into articles (rowid, title, body) values
            (100, 'alpha', 'hello world'),
            (200, 'beta', 'goodbye moon');
        create virtual table articles_fts using fts5(title, body);
        insert into articles_fts (rowid, title, body) values
            (100, 'alpha', 'hello world'),
            (200, 'beta', 'goodbye moon');
        """)
    try:
        resp = await ds.client.get("/data/articles.json?_search=hello")
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"] == [
            {"_rowid_": 1, "rowid": 100, "title": "alpha", "body": "hello world"}
        ]

        col_resp = await ds.client.get("/data/articles.json?_search_body=hello")
        assert col_resp.status_code == 200, col_resp.text
        assert col_resp.json()["rows"] == [
            {"_rowid_": 1, "rowid": 100, "title": "alpha", "body": "hello world"}
        ]

        title_resp = await ds.client.get("/data/articles.json?_search_title=beta")
        assert title_resp.status_code == 200, title_resp.text
        assert title_resp.json()["rows"] == [
            {"_rowid_": 2, "rowid": 200, "title": "beta", "body": "goodbye moon"}
        ]
    finally:
        ds.close()
