import pathlib
import urllib

import pytest

from datasette.app import Datasette
from datasette.fixtures import EXTRA_DATABASE_SQL
from datasette.plugins import DEFAULT_PLUGINS
from datasette.resources import DatabaseResource, TableResource
from datasette.utils import UNSTABLE_API_MESSAGE, escape_sqlite, tilde_encode
from datasette.utils.sqlite import sqlite_version
from datasette.version import __version__

from .fixtures import EXPECTED_PLUGINS, make_ds


async def test_homepage(ds_client):
    response = await ds_client.get("/.json")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    data = response.json()
    assert sorted(data.get("metadata").keys()) == [
        "about",
        "about_url",
        "description_html",
        "license",
        "license_url",
        "source",
        "source_url",
        "title",
    ]
    databases = data.get("databases")
    assert isinstance(databases, list)
    assert [d["name"] for d in databases] == ["fixtures"]
    d = databases[0]
    assert d["url"] == "/fixtures"
    assert isinstance(d["tables"], int)
    assert isinstance(d["views"], int)
    assert "size" in d
    assert data["totals"] == {
        "databases": 1,
        "tables": d["tables"],
        "views": d["views"],
    }
    assert data["total"] == d["tables"] + d["views"]


async def test_database_page(ds_client):
    response = await ds_client.get("/fixtures.json")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["database"] == "fixtures"

    # Build lookup for easier assertions
    tables = data["tables"]
    tables_by_name = {t["name"]: t for t in tables}

    # Verify tables are sorted by (hidden, name) - visible first, then hidden
    table_names = [t["name"] for t in tables]
    expected_order = sorted(tables, key=lambda t: (t["hidden"], t["name"]))
    assert table_names == [t["name"] for t in expected_order]

    # Expected visible tables (not hidden)
    expected_visible_tables = {
        "123_starts_with_digits",
        "Table With Space In Name",
        "attraction_characteristic",
        "binary_data",
        "complex_foreign_keys",
        "compound_primary_key",
        "compound_primary_key_edge_cases",
        "compound_three_primary_keys",
        "custom_foreign_key_label",
        "facet_cities",
        "facetable",
        "foreign_key_references",
        "infinity",
        "primary_key_edge_case_references",
        "primary_key_edge_cases",
        "primary_key_multiple_columns",
        "primary_key_multiple_columns_explicit_label",
        "roadside_attraction_characteristics",
        "roadside_attractions",
        "searchable",
        "searchable_tags",
        "select",
        "simple_primary_key",
        "sortable",
        "table/with/slashes.csv",
        "tags",
    }

    # The external-content index is visible, but its shadow tables need a
    # second dependency hop and are excluded by the one-hop permission policy.
    expected_hidden_tables = {
        "no_primary_key",
        "searchable_fts",
    }

    # Verify all expected tables exist
    assert expected_visible_tables.issubset(tables_by_name.keys())
    assert expected_hidden_tables.issubset(tables_by_name.keys())

    # Verify hidden status
    visible_tables = {t["name"] for t in tables if not t["hidden"]}
    hidden_tables = {t["name"] for t in tables if t["hidden"]}
    assert visible_tables == expected_visible_tables
    assert hidden_tables == expected_hidden_tables

    # Helper to compare foreign keys (order-insensitive)
    def fk_set(fks):
        return {(fk["other_table"], fk["column"], fk["other_column"]) for fk in fks}

    # Test specific table properties
    # -- facetable: has outgoing FK to facet_cities
    facetable = tables_by_name["facetable"]
    assert facetable["count"] == 15
    assert facetable["primary_keys"] == ["pk"]
    assert facetable["fts_table"] is None
    assert facetable["private"] is False
    assert fk_set(facetable["foreign_keys"]["outgoing"]) == {
        ("facet_cities", "_city_id", "id")
    }
    assert fk_set(facetable["foreign_keys"]["incoming"]) == set()

    # -- facet_cities: has incoming FK from facetable
    facet_cities = tables_by_name["facet_cities"]
    assert facet_cities["count"] == 4
    assert facet_cities["columns"] == ["id", "name"]
    assert fk_set(facet_cities["foreign_keys"]["incoming"]) == {
        ("facetable", "id", "_city_id")
    }

    # -- simple_primary_key: has multiple incoming FKs
    simple_pk = tables_by_name["simple_primary_key"]
    assert simple_pk["count"] == 5
    assert simple_pk["columns"] == ["id", "content"]
    assert simple_pk["primary_keys"] == ["id"]
    # Should have incoming FKs from complex_foreign_keys (f1, f2, f3) and foreign_key_references
    incoming = fk_set(simple_pk["foreign_keys"]["incoming"])
    assert ("complex_foreign_keys", "id", "f1") in incoming
    assert ("complex_foreign_keys", "id", "f2") in incoming
    assert ("complex_foreign_keys", "id", "f3") in incoming
    assert ("foreign_key_references", "id", "foreign_key_with_label") in incoming
    assert ("foreign_key_references", "id", "foreign_key_with_blank_label") in incoming

    # -- complex_foreign_keys: has multiple outgoing FKs to same table
    complex_fk = tables_by_name["complex_foreign_keys"]
    assert complex_fk["count"] == 1
    assert complex_fk["columns"] == ["pk", "f1", "f2", "f3"]
    outgoing = fk_set(complex_fk["foreign_keys"]["outgoing"])
    assert outgoing == {
        ("simple_primary_key", "f1", "id"),
        ("simple_primary_key", "f2", "id"),
        ("simple_primary_key", "f3", "id"),
    }

    # -- searchable: has FTS table association
    searchable = tables_by_name["searchable"]
    assert searchable["count"] == 2
    assert searchable["fts_table"] == "searchable_fts"
    assert searchable["columns"] == ["pk", "text1", "text2", "name with . and spaces"]

    # -- searchable_fts: is the FTS virtual table (hidden)
    searchable_fts = tables_by_name["searchable_fts"]
    assert searchable_fts["hidden"] is True
    assert searchable_fts["fts_table"] == "searchable_fts"
    # The "rank" column became visible in pragma_table_info in SQLite 3.37+
    if sqlite_version() >= (3, 37, 0):
        assert "rank" in searchable_fts["columns"]

    # -- compound primary keys
    compound_pk = tables_by_name["compound_primary_key"]
    assert compound_pk["primary_keys"] == ["pk1", "pk2"]
    assert compound_pk["count"] == 3

    compound_three = tables_by_name["compound_three_primary_keys"]
    assert compound_three["primary_keys"] == ["pk1", "pk2", "pk3"]
    assert compound_three["count"] == 1001

    # -- sortable: generated data
    sortable = tables_by_name["sortable"]
    assert sortable["count"] == 201
    assert sortable["primary_keys"] == ["pk1", "pk2"]

    # -- no_primary_key: hidden table with generated data
    no_pk = tables_by_name["no_primary_key"]
    assert no_pk["hidden"] is True
    assert no_pk["count"] == 202
    assert no_pk["primary_keys"] == []

    # -- roadside attractions relationship chain
    attractions = tables_by_name["roadside_attractions"]
    assert attractions["count"] == 4
    assert fk_set(attractions["foreign_keys"]["incoming"]) == {
        ("roadside_attraction_characteristics", "pk", "attraction_id")
    }

    characteristics = tables_by_name["attraction_characteristic"]
    assert characteristics["count"] == 2
    assert fk_set(characteristics["foreign_keys"]["incoming"]) == {
        ("roadside_attraction_characteristics", "pk", "characteristic_id")
    }

    # -- searchable_tags: multiple outgoing FKs
    searchable_tags = tables_by_name["searchable_tags"]
    assert searchable_tags["primary_keys"] == ["searchable_id", "tag"]
    outgoing = fk_set(searchable_tags["foreign_keys"]["outgoing"])
    assert outgoing == {
        ("searchable", "searchable_id", "pk"),
        ("tags", "tag", "tag"),
    }

    # -- tables with special names
    assert "123_starts_with_digits" in tables_by_name
    assert "Table With Space In Name" in tables_by_name
    assert "table/with/slashes.csv" in tables_by_name
    assert "select" in tables_by_name  # SQL reserved word

    # Verify select table has SQL reserved word columns
    select_table = tables_by_name["select"]
    assert set(select_table["columns"]) == {"group", "having", "and", "json"}

    # Verify all tables have required fields
    for table in tables:
        assert "name" in table
        assert "columns" in table
        assert "primary_keys" in table
        assert "count" in table
        assert "hidden" in table
        assert "fts_table" in table
        assert "foreign_keys" in table
        assert "private" in table
        assert "incoming" in table["foreign_keys"]
        assert "outgoing" in table["foreign_keys"]


async def test_no_files_uses_memory_database():
    ds = Datasette([])
    response = await ds.client.get("/.json")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "unstable": UNSTABLE_API_MESSAGE,
        "databases": [
            {
                "name": "_memory",
                "private": False,
                "tables": 0,
                "views": 0,
                "url": "/_memory",
                "size": None,
            },
        ],
        "items": [],
        "queries": [],
        "total": 0,
        "queries_total": 0,
        "totals": {"databases": 1, "tables": 0, "views": 0},
        "q": "",
        "database_filter": None,
        "previous_url": None,
        "next_url": None,
        "metadata": {},
        "private": False,
    }
    # Try that SQL query
    response = await ds.client.get(
        "/_memory/-/query.json?sql=select+sqlite_version()&_shape=array"
    )
    assert len(response.json()) == 1
    assert list(response.json()[0].keys()) == ["sqlite_version()"]


@pytest.mark.parametrize(
    "path,expected_redirect",
    (
        ("/:memory:", "/_memory"),
        ("/:memory:.json", "/_memory.json"),
        ("/:memory:?sql=select+1", "/_memory?sql=select+1"),
        ("/:memory:.json?sql=select+1", "/_memory.json?sql=select+1"),
        ("/:memory:.csv?sql=select+1", "/_memory.csv?sql=select+1"),
    ),
)
async def test_old_memory_urls_redirect(path, expected_redirect):
    response = await Datasette([]).client.get(path)
    assert response.status_code == 301
    assert response.headers["location"] == expected_redirect


async def test_database_page_for_database_with_dot_in_name(ds_on_disk_with_dot):
    response = await ds_on_disk_with_dot.client.get("/fixtures~2Edot.json")
    assert response.status_code == 200


async def test_custom_sql(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=select+content+from+simple_primary_key",
    )
    data = response.json()
    assert data == {
        "rows": [
            {"content": "hello"},
            {"content": "world"},
            {"content": ""},
            {"content": "RENDER_CELL_DEMO"},
            {"content": "RENDER_CELL_ASYNC"},
        ],
        "ok": True,
        "truncated": False,
    }


@pytest.mark.xfail(reason="Sometimes flaky in CI due to timing issues")
async def test_sql_time_limit(ds_client_shorter_time_limit):
    response = await ds_client_shorter_time_limit.get(
        "/fixtures/-/query.json?sql=select+sleep(0.1)",
    )
    assert response.status_code == 400
    expected_message = (
        "SQL query took too long. The time limit is"
        " controlled by the sql_time_limit_ms setting."
    )
    assert response.json() == {
        "ok": False,
        "error": expected_message,
        "errors": [expected_message],
        "status": 400,
    }


async def test_custom_sql_time_limit(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=select+sleep(0.01)",
    )
    assert response.status_code == 200
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=select+sleep(0.01)&_timelimit=5",
    )
    assert response.status_code == 400
    assert response.json()["error"].startswith("SQL query took too long.")


async def test_custom_sql_time_limit_cannot_exceed_setting():
    ds = await make_ds(settings={"facet_time_limit_ms": 30000})
    # Apply the short limit after startup so schema initialization is unaffected
    ds.sql_time_limit_ms = 20
    response = await ds.client.get(
        "/fixtures/-/query.json?sql=select+sleep(0.1)&_timelimit=30000"
    )
    assert response.status_code == 400
    assert response.json()["error"].startswith("SQL query took too long.")


async def test_invalid_custom_sql(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=.schema",
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert response.json()["error"] == "Statement must be a SELECT"


async def test_row(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key/1.json")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["row"] == {"id": 1, "content": "hello"}
    assert "rows" not in data
    assert "truncated" not in data


@pytest.mark.parametrize(
    "shape,expected",
    (
        ("objects", [{"id": 1, "content": "hello"}]),
        ("arrays", [[1, "hello"]]),
        ("array", [{"id": 1, "content": "hello"}]),
        ("arrayfirst", [1]),
        ("object", {"1": {"id": 1, "content": "hello"}}),
    ),
)
async def test_row_shape(ds_client, shape, expected):
    # Any ?_shape= returns the same shapes as the table JSON
    response = await ds_client.get(
        f"/fixtures/simple_primary_key/1.json?_shape={shape}"
    )
    assert response.status_code == 200
    data = response.json()
    if shape in ("objects", "arrays"):
        assert "row" not in data
        assert data["truncated"] is False
        data = data["rows"]
    assert data == expected


async def test_row_json_cols(ds_client):
    response = await ds_client.get("/fixtures/facetable/1.json?_json=tags")
    assert response.status_code == 200
    assert response.json()["row"]["tags"] == ["tag1", "tag2"]


async def test_row_not_found_is_an_error(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key/missing.json")
    assert response.status_code == 404
    data = response.json()
    assert data["ok"] is False
    assert "row" not in data


@pytest.mark.parametrize("suffix", ("", ".json"))
@pytest.mark.parametrize(
    "row_path",
    (
        "a",  # too few components for a two-column primary key
        "a,b,c",  # too many components for a two-column primary key
    ),
)
async def test_row_pk_arity_mismatch_returns_400(ds_client, row_path, suffix):
    # A row URL with the wrong number of comma-separated primary key
    # components used to raise an uncaught sqlite3.ProgrammingError (HTTP 500)
    # because the SQL had one bind placeholder per PK column but params were
    # only bound for the supplied components. It should be a 400 instead,
    # mirroring the existing guard in datasette/views/table.py.
    response = await ds_client.get(f"/fixtures/compound_primary_key/{row_path}{suffix}")
    assert response.status_code == 400
    if suffix == ".json":
        assert response.json()["ok"] is False
        assert response.json()["status"] == 400


@pytest.mark.parametrize(
    "path,expected_rows",
    (
        # The valid two-component URL still resolves the row.
        pytest.param(
            "/fixtures/compound_primary_key/a,b.json?_shape=objects",
            [{"pk1": "a", "pk2": "b", "content": "c"}],
            id="compound_pk_correct_arity",
        ),
        pytest.param(
            "/fixtures/table~2Fwith~2Fslashes~2Ecsv/3.json?_shape=objects",
            [{"pk": "3", "content": "hey"}],
            id="strange_table_name",
        ),
    ),
)
async def test_row_resolves(ds_client, path, expected_rows):
    response = await ds_client.get(path)
    assert response.status_code == 200
    assert response.json()["rows"] == expected_rows


async def test_row_foreign_key_tables(ds_client):
    response = await ds_client.get(
        "/fixtures/simple_primary_key/1.json?_extra=foreign_key_tables"
    )
    assert response.status_code == 200
    # Foreign keys are sorted by (other_table, column, other_column)
    assert response.json()["foreign_key_tables"] == [
        {
            "other_table": "complex_foreign_keys",
            "column": "id",
            "other_column": "f1",
            "count": 1,
            "link": "/fixtures/complex_foreign_keys?f1=1",
        },
        {
            "other_table": "complex_foreign_keys",
            "column": "id",
            "other_column": "f2",
            "count": 0,
            "link": "/fixtures/complex_foreign_keys?f2=1",
        },
        {
            "other_table": "complex_foreign_keys",
            "column": "id",
            "other_column": "f3",
            "count": 1,
            "link": "/fixtures/complex_foreign_keys?f3=1",
        },
        {
            "other_table": "foreign_key_references",
            "column": "id",
            "other_column": "foreign_key_with_blank_label",
            "count": 0,
            "link": "/fixtures/foreign_key_references?foreign_key_with_blank_label=1",
        },
        {
            "other_table": "foreign_key_references",
            "column": "id",
            "other_column": "foreign_key_with_label",
            "count": 1,
            "link": "/fixtures/foreign_key_references?foreign_key_with_label=1",
        },
    ]


async def test_row_foreign_key_tables_omit_denied_tables(request):
    actor = {"id": "reader"}
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "tables": {
                        "parents": {"permissions": {"view-table": True}},
                        "private_children": {"permissions": {"view-table": False}},
                    }
                }
            }
        },
    )
    request.addfinalizer(ds.close)
    db = ds.add_memory_database("fk_count_leak", name="data")
    await db.execute_write("create table parents (id integer primary key, name text)")
    await db.execute_write("""
        create table private_children (
            id integer primary key,
            parent_id integer references parents(id)
        )
    """)
    await db.execute_write("insert into parents values (1, 'Public parent')")
    await db.execute_write("""
        insert into private_children (id, parent_id) values
            (1, 1),
            (2, 1),
            (3, 1)
    """)
    await ds.invoke_startup()

    parent = TableResource(database="data", table="parents")
    private_children = TableResource(database="data", table="private_children")
    assert await ds.allowed(action="view-table", resource=parent, actor=actor)
    assert not await ds.allowed(
        action="view-table", resource=private_children, actor=actor
    )
    assert not await ds.allowed(
        action="execute-sql",
        resource=DatabaseResource(database="data"),
        actor=actor,
    )

    direct_child = await ds.client.get("/data/private_children.json", actor=actor)
    assert direct_child.status_code == 403
    parent_response = await ds.client.get(
        "/data/parents/1.json?_extra=foreign_key_tables", actor=actor
    )
    assert parent_response.status_code == 200

    foreign_key_tables = parent_response.json().get("foreign_key_tables", [])
    assert foreign_key_tables == [], (
        "denied child table name, foreign-key column, and row count disclosed: "
        f"{foreign_key_tables}"
    )


async def test_row_extras(ds_client):
    response = await ds_client.get(
        "/fixtures/simple_primary_key/1.json?_extra=database,table,primary_keys,query,request,debug,foreign_key_tables,column_details"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["database"] == "fixtures"
    assert data["table"] == "simple_primary_key"
    assert data["primary_keys"] == ["id"]
    assert data["query"]["sql"] == 'select * from simple_primary_key where "id"=:p0'
    assert data["query"]["params"] == {"p0": "1"}
    assert data["request"]["path"] == "/fixtures/simple_primary_key/1.json"
    assert data["debug"]["url_vars"] == {
        "database": "fixtures",
        "table": "simple_primary_key",
        "pks": "1",
        "format": "json",
    }
    assert len(data["foreign_key_tables"]) == 5
    id_detail = data["column_details"]["id"]
    assert id_detail["type"].lower() == "integer"
    assert id_detail == {
        "type": id_detail["type"],
        "sqlite_type": "INTEGER",
        "notnull": False,
        "default": None,
        "is_pk": True,
        "pk_position": 1,
        "hidden": 0,
    }
    content_detail = data["column_details"]["content"]
    assert content_detail["type"].lower() == "text"
    assert content_detail == {
        "type": content_detail["type"],
        "sqlite_type": "TEXT",
        "notnull": False,
        "default": None,
        "is_pk": False,
        "pk_position": 0,
        "hidden": 0,
    }


async def test_column_details_extra_row_for_null_blob(ds_client):
    response = await ds_client.get("/fixtures/binary_data/3.json?_extra=column_details")
    assert response.status_code == 200
    data_detail = response.json()["column_details"]["data"]
    assert data_detail["type"].lower() == "blob"
    assert data_detail == {
        "type": data_detail["type"],
        "sqlite_type": "BLOB",
        "notnull": False,
        "default": None,
        "is_pk": False,
        "pk_position": 0,
        "hidden": 0,
    }


async def test_row_extra_render_cell():
    """Test that _extra=render_cell returns rendered HTML from render_cell plugin hook on row pages"""
    from datasette import hookimpl
    from datasette.app import Datasette

    class TestRenderCellPlugin:
        __name__ = "TestRenderCellPlugin"

        @hookimpl
        def render_cell(self, value, column, table, database):
            # Only modify cells in our test table
            if table == "test_render" and column == "name":
                return f"<strong>{value}</strong>"
            return None

    ds = Datasette(memory=True)
    await ds.invoke_startup()
    db = ds.add_memory_database("test_row_render")
    await db.execute_write(
        "create table test_render (id integer primary key, name text)"
    )
    await db.execute_write("insert into test_render values (1, 'Alice')")

    # Register our test plugin
    ds.pm.register(TestRenderCellPlugin(), name="TestRenderCellPlugin")

    try:
        # Request row with _extra=render_cell
        response = await ds.client.get(
            "/test_row_render/test_render/1.json?_extra=render_cell"
        )
        assert response.status_code == 200
        data = response.json()

        # render_cell is a single object to match "row". Only columns
        # modified by plugins are included, so there is no "id" key
        assert data["render_cell"] == {"name": "<strong>Alice</strong>"}
        # The row itself still contains raw values
        assert data["row"] == {"id": 1, "name": "Alice"}

        # With a ?_shape= render_cell is a list, matching "rows"
        response = await ds.client.get(
            "/test_row_render/test_render/1.json?_extra=render_cell&_shape=objects"
        )
        data = response.json()
        assert data["render_cell"] == [{"name": "<strong>Alice</strong>"}]
        assert data["rows"] == [{"id": 1, "name": "Alice"}]

    finally:
        ds.pm.unregister(name="TestRenderCellPlugin")


async def test_databases_json(ds_on_disk_two_databases_one_immutable):
    response = await ds_on_disk_two_databases_one_immutable.client.get(
        "/-/databases.json"
    )
    databases = response.json()["databases"]
    assert len(databases) == 2
    extra_database, fixtures_database = databases
    assert extra_database["name"] == "extra database"
    assert extra_database["hash"] is None
    assert extra_database["is_mutable"] is True
    assert extra_database["is_memory"] is False

    assert fixtures_database["name"] == "fixtures"
    assert fixtures_database["hash"] is not None
    assert fixtures_database["is_mutable"] is False
    assert fixtures_database["is_memory"] is False


async def test_threads_json(ds_client):
    ds_client.ds.root_enabled = True
    try:
        response = await ds_client.get("/-/threads.json", actor={"id": "root"})
    finally:
        ds_client.ds.root_enabled = False
    expected_keys = {"ok", "threads", "num_threads"}
    expected_keys.update({"tasks", "num_tasks"})
    data = response.json()
    assert set(data.keys()) == expected_keys
    # Should be at least one _execute_writes thread for __INTERNAL__
    thread_names = [thread["name"] for thread in data["threads"]]
    assert "_execute_writes for database __INTERNAL__" in thread_names


async def test_plugins_json(ds_client):
    response = await ds_client.get("/-/plugins.json")
    # Filter out TrackEventPlugin
    actual_plugins = sorted(
        [p for p in response.json() if p["name"] != "TrackEventPlugin"],
        key=lambda p: p["name"],
    )
    assert actual_plugins == EXPECTED_PLUGINS
    # Try with ?all=1
    response = await ds_client.get("/-/plugins.json?all=1")
    names = {p["name"] for p in response.json()}
    assert names.issuperset(p["name"] for p in EXPECTED_PLUGINS)
    assert names.issuperset(DEFAULT_PLUGINS)


async def test_versions_json(ds_client):
    response = await ds_client.get("/-/versions.json")
    data = response.json()
    assert "python" in data
    assert data.get("asgi") == "3.0"
    assert "version" in data["python"]
    assert "full" in data["python"]
    assert "datasette" in data
    assert "version" in data["datasette"]
    assert data["datasette"]["version"] == __version__
    assert "sqlite" in data
    assert "version" in data["sqlite"]
    assert "fts_versions" in data["sqlite"]
    assert "compile_options" in data["sqlite"]
    # By default, the json1 extension is enabled in the SQLite
    # provided by the `ubuntu-latest` github actions runner, and
    # all versions of SQLite from 3.38.0 onwards
    assert data["sqlite"]["extensions"]["json1"]


async def test_actions_json(ds_client):
    original_root_enabled = ds_client.ds.root_enabled
    try:
        ds_client.ds.root_enabled = True
        response = await ds_client.get("/-/actions.json", actor={"id": "root"})
        data = response.json()["actions"]
    finally:
        ds_client.ds.root_enabled = original_root_enabled
    assert isinstance(data, list)
    assert len(data) > 0
    # Check structure of first action
    action = data[0]
    for key in (
        "name",
        "abbr",
        "description",
        "takes_parent",
        "takes_child",
        "resource_class",
        "also_requires",
    ):
        assert key in action
    # Check that some expected actions exist
    action_names = {a["name"] for a in data}
    for expected_action in (
        "view-instance",
        "view-database",
        "view-table",
        "execute-sql",
    ):
        assert expected_action in action_names


async def test_settings_json(ds_client):
    response = await ds_client.get("/-/settings.json")
    assert response.json() == {
        "ok": True,
        "default_page_size": 50,
        "default_facet_size": 30,
        "default_allow_sql": True,
        "facet_suggest_time_limit_ms": 200,
        "facet_time_limit_ms": 200,
        "max_returned_rows": 100,
        "max_insert_rows": 100,
        "max_post_body_bytes": 10 * 1024 * 1024,
        "sql_time_limit_ms": 200,
        "default_allow_download": True,
        "allow_signed_tokens": True,
        "max_signed_tokens_ttl": 0,
        "default_allow_facet": True,
        "suggest_facets": True,
        "default_cache_ttl": 5,
        "num_sql_threads": 1,
        "cache_size_kb": 0,
        "default_allow_stream": True,
        "max_stream_mb": 100,
        "truncate_cells_html": 2048,
        "force_https_urls": False,
        "template_debug": False,
        "trace_debug": False,
        "base_url": "/",
    }


test_json_columns_default_expected = [
    {"intval": 1, "strval": "s", "floatval": 0.5, "jsonval": '{"foo": "bar"}'}
]


@pytest.mark.parametrize("endpoint", ["table", "query"])
@pytest.mark.parametrize("include_columns", [False, True])
@pytest.mark.parametrize(
    "shape", ["objects", "object", "array", "arrayfirst", "arrays"]
)
async def test_json_columns_with_output_shapes(endpoint, include_columns, shape):
    if endpoint == "query" and shape == "object":
        pytest.skip("Object shape requires a table primary key")
    ds = Datasette()
    db = ds.add_memory_database("json_shapes")
    await db.execute_write("CREATE TABLE t (value text, id integer primary key)")
    await db.execute_write("INSERT INTO t VALUES (?, ?)", ['["a", "b"]', 1])
    parameters = {"_json": "value", "_shape": shape}
    if include_columns:
        parameters["_extra"] = "columns"
    if endpoint == "query":
        parameters["sql"] = "SELECT value, id FROM t"
        path = "/json_shapes/-/query.json"
    else:
        path = "/json_shapes/t.json"
    try:
        response = await ds.client.get(path + "?" + urllib.parse.urlencode(parameters))
        assert response.status_code == 200
        result = response.json()
        row = {"value": ["a", "b"], "id": 1}
        if shape == "object":
            assert result == {"1": row}
        elif shape == "array":
            assert result == [row]
        elif shape == "arrayfirst":
            assert result == [["a", "b"]]
        elif shape == "arrays":
            assert result["rows"] == [[["a", "b"], 1]]
        else:
            assert result["rows"] == [row]
        if shape in ("objects", "arrays"):
            assert ("columns" in result) == include_columns
    finally:
        ds.close()


@pytest.mark.parametrize("endpoint", ["table", "row"])
@pytest.mark.parametrize(
    "value,expected", [('["a"]', ["a"]), ("not JSON", "not JSON"), (None, None)]
)
async def test_json_columns_preserve_other_values(endpoint, value, expected):
    ds = Datasette()
    db = ds.add_memory_database("json_values")
    await db.execute_write(
        "CREATE TABLE t (id integer primary key, value text, other text)"
    )
    await db.execute_write(
        "INSERT INTO t VALUES (?, ?, ?)", [1, value, '{"keep": true}']
    )
    path = "/json_values/t.json" if endpoint == "table" else "/json_values/t/1.json"
    try:
        response = await ds.client.get(path + "?_json=value")
        assert response.status_code == 200
        row = {"id": 1, "value": expected, "other": '{"keep": true}'}
        if endpoint == "row":
            assert response.json()["row"] == row
        else:
            assert response.json()["rows"] == [row]
    finally:
        ds.close()


@pytest.mark.parametrize("include_columns", [False, True])
@pytest.mark.parametrize("composite_key", [False, True])
@pytest.mark.parametrize(
    "values",
    [
        [('"same"', "same"), ("same", "same")],
        [("null", None), ("None", "None")],
        [(' {"id":1}', {"id": 1})],
        [('["x"]', ["x"]), ("['x']", "['x']")],
    ],
)
async def test_json_columns_object_preserves_primary_keys(
    include_columns, composite_key, values
):
    ds = Datasette()
    db = ds.add_memory_database("json_keys")
    primary_key = "id, tenant" if composite_key else "id"
    await db.execute_write(
        f"CREATE TABLE t (id text, tenant text, payload text, primary key ({primary_key}))"
    )
    expected = {}
    for index, (raw_id, decoded_id) in enumerate(values):
        payload = f"row-{index}"
        await db.execute_write(
            "INSERT INTO t VALUES (?, ?, ?)", [raw_id, '"team"', payload]
        )
        key = tilde_encode(raw_id)
        if composite_key:
            key += "," + tilde_encode('"team"')
        expected[key] = {
            "id": decoded_id,
            "tenant": "team" if composite_key else '"team"',
            "payload": payload,
        }
    parameters = [("_shape", "object"), ("_json", "id")]
    if composite_key:
        parameters.append(("_json", "tenant"))
    if include_columns:
        parameters.append(("_extra", "columns"))
    try:
        response = await ds.client.get(
            "/json_keys/t.json?" + urllib.parse.urlencode(parameters)
        )
        assert response.status_code == 200
        assert response.json() == expected
        for key in expected:
            detail = await ds.client.get(f"/json_keys/t/{key}.json")
            assert detail.status_code == 200
    finally:
        ds.close()


@pytest.mark.parametrize(
    "shape", ["objects", "object", "array", "arrayfirst", "arrays"]
)
async def test_json_columns_with_empty_results(shape):
    ds = Datasette()
    db = ds.add_memory_database("json_empty")
    await db.execute_write("CREATE TABLE t (id integer primary key, value text)")
    try:
        response = await ds.client.get(f"/json_empty/t.json?_json=value&_shape={shape}")
        assert response.status_code == 200
        result = response.json()
        if shape == "object":
            assert result == {}
        elif shape in ("array", "arrayfirst"):
            assert result == []
        else:
            assert result["rows"] == []
    finally:
        ds.close()


@pytest.mark.parametrize(
    "extra_args,expected",
    [
        ("", test_json_columns_default_expected),
        ("&_json=intval", test_json_columns_default_expected),
        ("&_json=strval", test_json_columns_default_expected),
        ("&_json=floatval", test_json_columns_default_expected),
        (
            "&_json=jsonval",
            [{"intval": 1, "strval": "s", "floatval": 0.5, "jsonval": {"foo": "bar"}}],
        ),
    ],
)
async def test_json_columns(ds_client, extra_args, expected):
    sql = """
        select 1 as intval, "s" as strval, 0.5 as floatval,
        '{"foo": "bar"}' as jsonval
    """
    path = "/fixtures/-/query.json?" + urllib.parse.urlencode(
        {"sql": sql, "_shape": "array"}
    )
    path += extra_args
    response = await ds_client.get(
        path,
    )
    assert response.json() == expected


async def test_config_cache_size():
    ds = await make_ds(settings={"cache_size_kb": 2500})
    response = await ds.client.get("/fixtures/pragma_cache_size.json")
    assert response.json()["rows"] == [{"cache_size": -2500}]


async def test_config_force_https_urls():
    ds = await make_ds(settings={"force_https_urls": True})
    response = await ds.client.get(
        "/fixtures/facetable.json?_size=3&_facet=state&_extra=suggested_facets"
    )
    assert response.json()["next_url"].startswith("https://")
    assert response.json()["facet_results"]["results"]["state"]["results"][0][
        "toggle_url"
    ].startswith("https://")
    assert response.json()["suggested_facets"][0]["toggle_url"].startswith("https://")
    # Also confirm that request.url and request.scheme are set correctly
    response = await ds.client.get("/")
    assert ds._last_request.url.startswith("https://")
    assert ds._last_request.scheme == "https"


@pytest.mark.parametrize(
    "path,status_code",
    [
        ("/fixtures.db", 200),
        ("/fixtures.json", 200),
        ("/fixtures/no_primary_key.json", 200),
        # A 400 invalid SQL query should still have the header:
        ("/fixtures/-/query.json?sql=select+blah", 400),
        # Write APIs
        ("/fixtures/-/create", 405),
        ("/fixtures/facetable/-/insert", 405),
        ("/fixtures/facetable/-/drop", 405),
    ],
)
async def test_cors(
    ds_on_disk_with_cors,
    ds_on_disk_two_databases_one_immutable,
    path,
    status_code,
):
    # Both fixtures are on disk and immutable, so /fixtures.db can be downloaded
    response = await ds_on_disk_with_cors.client.get(path)
    assert response.status_code == status_code
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert (
        response.headers["Access-Control-Allow-Headers"]
        == "Authorization, Content-Type"
    )
    assert (
        response.headers["Access-Control-Expose-Headers"]
        == "Link, Datasette-Truncated, Datasette-Max-Returned-Rows"
    )
    assert (
        response.headers["Access-Control-Allow-Methods"] == "GET, POST, HEAD, OPTIONS"
    )
    assert response.headers["Access-Control-Max-Age"] == "3600"
    # Same request to ds_on_disk_two_databases_one_immutable
    # should not have those headers - I'm using that fixture because
    # regular ds_on_disk doesn't have immutable fixtures.db which means
    # the test for /fixtures.db returns a 403 error
    client = ds_on_disk_two_databases_one_immutable.client
    response = await client.get(path)
    assert response.status_code == status_code
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Headers" not in response.headers
    assert "Access-Control-Expose-Headers" not in response.headers
    assert "Access-Control-Allow-Methods" not in response.headers
    assert "Access-Control-Max-Age" not in response.headers


async def test_cors_query_redirect(ds_client_with_cors):
    # /db?sql= redirects to /db/-/query - the redirect itself needs CORS
    # headers, otherwise browsers refuse to follow it cross-origin
    response = await ds_client_with_cors.get(
        "/fixtures?sql=select+1", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/fixtures/-/query?sql=select+1"
    assert response.headers["Access-Control-Allow-Origin"] == "*"


@pytest.mark.parametrize(
    "path",
    (
        "/",
        ".json",
        "/searchable",
        "/searchable.json",
        "/searchable_view",
        "/searchable_view.json",
    ),
)
async def test_database_with_space_in_name(ds_client_two_attached_databases, path):
    response = await ds_client_two_attached_databases.get(
        "/extra~20database" + path, follow_redirects=True
    )
    assert response.status_code == 200


async def test_common_prefix_database_names():
    # https://github.com/simonw/datasette/issues/597
    ds = await make_ds(
        extra_databases={"foo-bar": EXTRA_DATABASE_SQL, "foo": EXTRA_DATABASE_SQL}
    )
    assert [
        d["name"]
        for d in (await ds.client.get("/-/databases.json")).json()["databases"]
    ] == ["foo-bar", "foo", "fixtures"]
    for db_name, path in (("foo", "/foo.json"), ("foo-bar", "/foo-bar.json")):
        data = (await ds.client.get(path)).json()
        assert db_name == data["database"]


async def test_inspect_file_used_for_count(ds_on_disk_immutable_and_inspect_file):
    response = await ds_on_disk_immutable_and_inspect_file.client.get(
        "/fixtures/sortable.json?_extra=count"
    )
    assert response.json()["count"] == 100


async def test_http_options_request(ds_client):
    response = await ds_client.options("/fixtures")
    assert response.status_code == 200
    assert response.text == "ok"


async def test_db_path(ds_on_disk):
    # Needs ds_on_disk because needs file based database
    db = ds_on_disk.get_database()
    path = pathlib.Path(db.path)

    assert path.exists()

    datasette = Datasette([path])

    # Previously this broke if path was a pathlib.Path:
    await datasette.refresh_schemas()


async def test_hidden_sqlite_stat1_table():
    ds = Datasette()
    db = ds.add_memory_database("db")
    await db.execute_write("create table normal (id integer primary key, name text)")
    await db.execute_write("create index idx on normal (name)")
    await db.execute_write("analyze")
    data = (await ds.client.get("/db.json?_show_hidden=1")).json()
    tables = [(t["name"], t["hidden"]) for t in data["tables"]]
    assert tables == [("normal", False)]


async def test_hide_tables_starting_with_underscore():
    ds = Datasette()
    db = ds.add_memory_database("test_hide_tables_starting_with_underscore")
    await db.execute_write("create table normal (id integer primary key, name text)")
    await db.execute_write("create table _hidden (id integer primary key, name text)")
    data = (
        await ds.client.get(
            "/test_hide_tables_starting_with_underscore.json?_show_hidden=1"
        )
    ).json()
    tables = [(t["name"], t["hidden"]) for t in data["tables"]]
    assert tables == [("normal", False), ("_hidden", True)]


@pytest.mark.parametrize("db_name", ("foo", r"fo%o", "f~/c.d"))
async def test_tilde_encoded_database_names(db_name):
    ds = Datasette()
    ds.add_memory_database(db_name)
    response = await ds.client.get("/.json")
    databases_by_name = {d["name"]: d for d in response.json()["databases"]}
    assert db_name in databases_by_name
    path = databases_by_name[db_name]["url"]
    # And the JSON for that database
    response2 = await ds.client.get(path + ".json")
    assert response2.status_code == 200


@pytest.mark.parametrize("table_name", ("[foo]", "foo]", "[foo]/bar"))
async def test_table_with_reserved_characters_in_name(table_name):
    # Table names containing characters such as "]" that cannot be escaped
    # using SQLite [bracket] quoting used to break schema introspection and
    # the table page - https://github.com/simonw/datasette/issues/2431
    ds = Datasette()
    db = ds.add_memory_database("test_reserved_table_names")
    await db.execute_write(
        f"create table {escape_sqlite(table_name)} (id integer primary key, name text)"
    )
    await db.execute_write(
        f"insert into {escape_sqlite(table_name)} (id, name) values (1, 'one')"
    )
    # Schema introspection (populate_schema_tables) must not crash:
    db_response = await ds.client.get("/test_reserved_table_names.json")
    assert db_response.status_code == 200
    tables = {t["name"]: t for t in db_response.json()["tables"]}
    assert tables[table_name]["count"] == 1
    # And the table page itself must load and return the row:
    table_response = await ds.client.get(
        f"/test_reserved_table_names/{tilde_encode(table_name)}.json?_shape=array"
    )
    assert table_response.status_code == 200
    assert table_response.json() == [{"id": 1, "name": "one"}]


@pytest.mark.parametrize(
    "config,expected",
    (
        ({}, {}),
        ({"plugins": {"datasette-foo": "bar"}}, {"plugins": {"datasette-foo": "bar"}}),
        # Test redaction
        (
            {
                "plugins": {
                    "datasette-auth": {"secret_key": "key"},
                    "datasette-foo": "bar",
                    "datasette-auth2": {"password": "password"},
                    "datasette-sentry": {
                        "dsn": "sentry:///foo",
                    },
                }
            },
            {
                "plugins": {
                    "datasette-auth": {"secret_key": "***"},
                    "datasette-foo": "bar",
                    "datasette-auth2": {"password": "***"},
                    "datasette-sentry": {"dsn": "***"},
                }
            },
        ),
    ),
)
async def test_config_json(config, expected):
    "/-/config.json should return redacted configuration"
    ds = Datasette(config=config)
    ds.root_enabled = True
    response = await ds.client.get("/-/config.json", actor={"id": "root"})
    assert response.json() == {"ok": True, **expected}


@pytest.mark.parametrize("path", ("/-/config", "/-/config.json"))
async def test_config_requires_permissions_debug(path):
    # Configuration can include allow blocks, the names of private tables
    # and the SQL of private stored queries
    config = {
        "databases": {
            "data": {
                "tables": {"private_t": {"allow": {"id": "alice"}}},
                "queries": {
                    "hidden_q": {
                        "sql": "select * from private_t where v = 'x'",
                        "allow": {"id": "root"},
                    }
                },
            }
        }
    }
    ds = Datasette(config=config)
    ds.root_enabled = True
    ds.add_memory_database("config_requires_permissions_debug", name="data")
    for actor in (None, {"id": "alice"}):
        response = await ds.client.get(path, actor=actor)
        assert response.status_code == 403
        assert "private_t" not in response.text
        assert "hidden_q" not in response.text
    response = await ds.client.get(path, actor={"id": "root"})
    assert response.status_code == 200
    assert "private_t" in response.text
    assert "hidden_q" in response.text


@pytest.mark.skip(reason="rm?")
@pytest.mark.parametrize(
    "metadata,expected_config,expected_metadata",
    (
        ({}, {}, {}),
        (
            # Metadata input
            {
                "title": "Datasette Fixtures",
                "databases": {
                    "fixtures": {
                        "tables": {
                            "sortable": {
                                "sortable_columns": [
                                    "sortable",
                                    "sortable_with_nulls",
                                    "sortable_with_nulls_2",
                                    "text",
                                ],
                            },
                            "no_primary_key": {"sortable_columns": [], "hidden": True},
                            "primary_key_multiple_columns_explicit_label": {
                                "label_column": "content2"
                            },
                            "simple_view": {"sortable_columns": ["content"]},
                            "searchable_view_configured_by_metadata": {
                                "fts_table": "searchable_fts",
                                "fts_pk": "pk",
                            },
                            "roadside_attractions": {
                                "columns": {
                                    "name": "The name of the attraction",
                                    "address": "The street address for the attraction",
                                }
                            },
                            "attraction_characteristic": {"sort_desc": "pk"},
                            "facet_cities": {"sort": "name"},
                            "paginated_view": {"size": 25},
                        },
                    }
                },
            },
            # Should produce a config with just the table configuration keys
            {
                "databases": {
                    "fixtures": {
                        "tables": {
                            "sortable": {
                                "sortable_columns": [
                                    "sortable",
                                    "sortable_with_nulls",
                                    "sortable_with_nulls_2",
                                    "text",
                                ]
                            },
                            # These one get redacted:
                            "no_primary_key": "***",
                            "primary_key_multiple_columns_explicit_label": "***",
                            "simple_view": {"sortable_columns": ["content"]},
                            "searchable_view_configured_by_metadata": {
                                "fts_table": "searchable_fts",
                                "fts_pk": "pk",
                            },
                            "attraction_characteristic": {"sort_desc": "pk"},
                            "facet_cities": {"sort": "name"},
                            "paginated_view": {"size": 25},
                        }
                    }
                }
            },
            # And metadata with everything else
            {
                "title": "Datasette Fixtures",
                "databases": {
                    "fixtures": {
                        "tables": {
                            "roadside_attractions": {
                                "columns": {
                                    "name": "The name of the attraction",
                                    "address": "The street address for the attraction",
                                }
                            },
                        }
                    }
                },
            },
        ),
    ),
)
async def test_upgrade_metadata(metadata, expected_config, expected_metadata):
    ds = Datasette(metadata=metadata)
    response = await ds.client.get("/-/config.json")
    assert response.json() == {"ok": True, **expected_config}
    response2 = await ds.client.get("/-/metadata.json")
    assert response2.json() == expected_metadata


class Either:
    def __init__(self, a, b):
        self.a = a
        self.b = b

    def __eq__(self, other):
        return other == self.a or other == self.b
