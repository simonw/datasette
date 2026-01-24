from datasette.app import Datasette
from datasette.plugins import DEFAULT_PLUGINS
from datasette.version import __version__
from .fixtures import make_app_client, EXPECTED_PLUGINS
import pathlib
import pytest
import sys
import urllib


@pytest.mark.asyncio
async def test_homepage(ds_client):
    response = await ds_client.get("/.json")
    assert response.status_code == 200
    assert "application/json; charset=utf-8" == response.headers["content-type"]
    data = response.json()
    assert sorted(list(data.get("metadata").keys())) == [
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
    assert databases.keys() == {"fixtures": 0}.keys()
    d = databases["fixtures"]
    assert d["name"] == "fixtures"
    assert isinstance(d["tables_count"], int)
    assert isinstance(len(d["tables_and_views_truncated"]), int)
    assert d["tables_and_views_more"] is True
    assert isinstance(d["hidden_tables_count"], int)
    assert isinstance(d["hidden_table_rows_sum"], int)
    assert isinstance(d["views_count"], int)


@pytest.mark.asyncio
async def test_homepage_sort_by_relationships(ds_client):
    response = await ds_client.get("/.json?_sort=relationships")
    assert response.status_code == 200
    tables = [
        t["name"]
        for t in response.json()["databases"]["fixtures"]["tables_and_views_truncated"]
    ]
    assert tables == [
        "simple_primary_key",
        "foreign_key_references",
        "complex_foreign_keys",
        "roadside_attraction_characteristics",
        "searchable_tags",
    ]


@pytest.mark.asyncio
async def test_database_page(ds_client):
    response = await ds_client.get("/fixtures.json")
    assert response.status_code == 200
    data = response.json()
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
        "compound_three_primary_keys",
        "custom_foreign_key_label",
        "facet_cities",
        "facetable",
        "foreign_key_references",
        "infinity",
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

    # Expected hidden tables
    expected_hidden_tables = {
        "no_primary_key",
        "searchable_fts",
        "searchable_fts_config",
        "searchable_fts_data",
        "searchable_fts_docsize",
        "searchable_fts_idx",
    }

    # Verify all expected tables exist
    assert expected_visible_tables.issubset(tables_by_name.keys())
    assert expected_hidden_tables.issubset(tables_by_name.keys())

    # Verify hidden status
    visible_tables = {t["name"] for t in tables if not t["hidden"]}
    hidden_tables = {t["name"] for t in tables if t["hidden"]}
    assert expected_visible_tables == visible_tables
    assert expected_hidden_tables == hidden_tables

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
    assert "rank" in searchable_fts["columns"]

    # -- compound primary keys
    compound_pk = tables_by_name["compound_primary_key"]
    assert compound_pk["primary_keys"] == ["pk1", "pk2"]
    assert compound_pk["count"] == 2

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
    assert no_pk["count"] == 201
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


def test_no_files_uses_memory_database(app_client_no_files):
    response = app_client_no_files.get("/.json")
    assert response.status == 200
    assert {
        "databases": {
            "_memory": {
                "name": "_memory",
                "hash": None,
                "color": "a6c7b9",
                "path": "/_memory",
                "tables_and_views_truncated": [],
                "tables_and_views_more": False,
                "tables_count": 0,
                "table_rows_sum": 0,
                "show_table_row_counts": False,
                "hidden_table_rows_sum": 0,
                "hidden_tables_count": 0,
                "views_count": 0,
                "private": False,
            },
        },
        "metadata": {},
    } == response.json
    # Try that SQL query
    response = app_client_no_files.get(
        "/_memory/-/query.json?sql=select+sqlite_version()&_shape=array"
    )
    assert 1 == len(response.json)
    assert ["sqlite_version()"] == list(response.json[0].keys())


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
def test_old_memory_urls_redirect(app_client_no_files, path, expected_redirect):
    response = app_client_no_files.get(path)
    assert response.status == 301
    assert response.headers["location"] == expected_redirect


def test_database_page_for_database_with_dot_in_name(app_client_with_dot):
    response = app_client_with_dot.get("/fixtures~2Edot.json")
    assert response.status == 200


@pytest.mark.asyncio
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
def test_sql_time_limit(app_client_shorter_time_limit):
    response = app_client_shorter_time_limit.get(
        "/fixtures/-/query.json?sql=select+sleep(0.5)",
    )
    assert 400 == response.status
    assert response.json == {
        "ok": False,
        "error": (
            "<p>SQL query took too long. The time limit is controlled by the\n"
            '<a href="https://docs.datasette.io/en/stable/settings.html#sql-time-limit-ms">sql_time_limit_ms</a>\n'
            "configuration option.</p>\n"
            '<textarea style="width: 90%">select sleep(0.5)</textarea>\n'
            "<script>\n"
            'let ta = document.querySelector("textarea");\n'
            'ta.style.height = ta.scrollHeight + "px";\n'
            "</script>"
        ),
        "status": 400,
        "title": "SQL Interrupted",
    }


@pytest.mark.asyncio
async def test_custom_sql_time_limit(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=select+sleep(0.01)",
    )
    assert response.status_code == 200
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=select+sleep(0.01)&_timelimit=5",
    )
    assert response.status_code == 400
    assert response.json()["title"] == "SQL Interrupted"


@pytest.mark.asyncio
async def test_invalid_custom_sql(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=.schema",
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "Statement must be a SELECT" == response.json()["error"]


@pytest.mark.asyncio
async def test_row(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key/1.json?_shape=objects")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["rows"] == [{"id": 1, "content": "hello"}]


@pytest.mark.asyncio
async def test_row_strange_table_name(ds_client):
    response = await ds_client.get(
        "/fixtures/table~2Fwith~2Fslashes~2Ecsv/3.json?_shape=objects"
    )
    assert response.status_code == 200
    assert response.json()["rows"] == [{"pk": "3", "content": "hey"}]


@pytest.mark.asyncio
async def test_row_foreign_key_tables(ds_client):
    response = await ds_client.get(
        "/fixtures/simple_primary_key/1.json?_extras=foreign_key_tables"
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


@pytest.mark.asyncio
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

        # Verify the response structure
        assert "render_cell" in data
        assert "rows" in data

        # render_cell should be a list with one row (since this is a row page)
        # Only columns modified by plugins are included (sparse output)
        render_cell = data["render_cell"]
        assert len(render_cell) == 1

        # The row: id=1, name='Alice'
        # The 'name' column should be rendered by our plugin as <strong>Alice</strong>
        assert render_cell[0]["name"] == "<strong>Alice</strong>"
        # The 'id' column is not included since no plugin modified it
        assert "id" not in render_cell[0]

        # The regular rows should still contain raw values
        assert data["rows"] == [{"id": 1, "name": "Alice"}]

    finally:
        ds.pm.unregister(name="TestRenderCellPlugin")


def test_databases_json(app_client_two_attached_databases_one_immutable):
    response = app_client_two_attached_databases_one_immutable.get("/-/databases.json")
    databases = response.json
    assert 2 == len(databases)
    extra_database, fixtures_database = databases
    assert "extra database" == extra_database["name"]
    assert extra_database["hash"] is None
    assert extra_database["is_mutable"] is True
    assert extra_database["is_memory"] is False

    assert "fixtures" == fixtures_database["name"]
    assert fixtures_database["hash"] is not None
    assert fixtures_database["is_mutable"] is False
    assert fixtures_database["is_memory"] is False


@pytest.mark.asyncio
async def test_threads_json(ds_client):
    response = await ds_client.get("/-/threads.json")
    expected_keys = {"threads", "num_threads"}
    if sys.version_info >= (3, 7, 0):
        expected_keys.update({"tasks", "num_tasks"})
    data = response.json()
    assert set(data.keys()) == expected_keys
    # Should be at least one _execute_writes thread for __INTERNAL__
    thread_names = [thread["name"] for thread in data["threads"]]
    assert "_execute_writes for database __INTERNAL__" in thread_names


@pytest.mark.asyncio
async def test_plugins_json(ds_client):
    response = await ds_client.get("/-/plugins.json")
    # Filter out TrackEventPlugin
    actual_plugins = sorted(
        [p for p in response.json() if p["name"] != "TrackEventPlugin"],
        key=lambda p: p["name"],
    )
    assert EXPECTED_PLUGINS == actual_plugins
    # Try with ?all=1
    response = await ds_client.get("/-/plugins.json?all=1")
    names = {p["name"] for p in response.json()}
    assert names.issuperset(p["name"] for p in EXPECTED_PLUGINS)
    assert names.issuperset(DEFAULT_PLUGINS)


@pytest.mark.asyncio
async def test_versions_json(ds_client):
    response = await ds_client.get("/-/versions.json")
    data = response.json()
    assert "python" in data
    assert "3.0" == data.get("asgi")
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


@pytest.mark.asyncio
async def test_actions_json(ds_client):
    original_root_enabled = ds_client.ds.root_enabled
    try:
        ds_client.ds.root_enabled = True
        cookies = {"ds_actor": ds_client.actor_cookie({"id": "root"})}
        response = await ds_client.get("/-/actions.json", cookies=cookies)
        data = response.json()
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


@pytest.mark.asyncio
async def test_settings_json(ds_client):
    response = await ds_client.get("/-/settings.json")
    assert response.json() == {
        "default_page_size": 50,
        "default_facet_size": 30,
        "default_allow_sql": True,
        "facet_suggest_time_limit_ms": 200,
        "facet_time_limit_ms": 200,
        "max_returned_rows": 100,
        "max_insert_rows": 100,
        "sql_time_limit_ms": 200,
        "allow_download": True,
        "allow_signed_tokens": True,
        "max_signed_tokens_ttl": 0,
        "allow_facet": True,
        "suggest_facets": True,
        "default_cache_ttl": 5,
        "num_sql_threads": 1,
        "cache_size_kb": 0,
        "allow_csv_stream": True,
        "max_csv_mb": 100,
        "truncate_cells_html": 2048,
        "force_https_urls": False,
        "template_debug": False,
        "trace_debug": False,
        "base_url": "/",
    }


test_json_columns_default_expected = [
    {"intval": 1, "strval": "s", "floatval": 0.5, "jsonval": '{"foo": "bar"}'}
]


@pytest.mark.asyncio
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


def test_config_cache_size(app_client_larger_cache_size):
    response = app_client_larger_cache_size.get("/fixtures/pragma_cache_size.json")
    assert response.json["rows"] == [{"cache_size": -2500}]


def test_config_force_https_urls():
    with make_app_client(settings={"force_https_urls": True}) as client:
        response = client.get(
            "/fixtures/facetable.json?_size=3&_facet=state&_extra=next_url,suggested_facets"
        )
        assert response.json["next_url"].startswith("https://")
        assert response.json["facet_results"]["results"]["state"]["results"][0][
            "toggle_url"
        ].startswith("https://")
        assert response.json["suggested_facets"][0]["toggle_url"].startswith("https://")
        # Also confirm that request.url and request.scheme are set correctly
        response = client.get("/")
        assert client.ds._last_request.url.startswith("https://")
        assert client.ds._last_request.scheme == "https"


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
def test_cors(
    app_client_with_cors,
    app_client_two_attached_databases_one_immutable,
    path,
    status_code,
):
    response = app_client_with_cors.get(
        path,
    )
    assert response.status == status_code
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert (
        response.headers["Access-Control-Allow-Headers"]
        == "Authorization, Content-Type"
    )
    assert response.headers["Access-Control-Expose-Headers"] == "Link"
    assert (
        response.headers["Access-Control-Allow-Methods"] == "GET, POST, HEAD, OPTIONS"
    )
    assert response.headers["Access-Control-Max-Age"] == "3600"
    # Same request to app_client_two_attached_databases_one_immutable
    # should not have those headers - I'm using that fixture because
    # regular app_client doesn't have immutable fixtures.db which means
    # the test for /fixtures.db returns a 403 error
    response = app_client_two_attached_databases_one_immutable.get(
        path,
    )
    assert response.status == status_code
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Headers" not in response.headers
    assert "Access-Control-Expose-Headers" not in response.headers
    assert "Access-Control-Allow-Methods" not in response.headers
    assert "Access-Control-Max-Age" not in response.headers


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
def test_database_with_space_in_name(app_client_two_attached_databases, path):
    response = app_client_two_attached_databases.get(
        "/extra~20database" + path, follow_redirects=True
    )
    assert response.status == 200


def test_common_prefix_database_names(app_client_conflicting_database_names):
    # https://github.com/simonw/datasette/issues/597
    assert ["foo-bar", "foo", "fixtures"] == [
        d["name"]
        for d in app_client_conflicting_database_names.get("/-/databases.json").json
    ]
    for db_name, path in (("foo", "/foo.json"), ("foo-bar", "/foo-bar.json")):
        data = app_client_conflicting_database_names.get(path).json
        assert db_name == data["database"]


def test_inspect_file_used_for_count(app_client_immutable_and_inspect_file):
    response = app_client_immutable_and_inspect_file.get(
        "/fixtures/sortable.json?_extra=count"
    )
    assert response.json["count"] == 100


@pytest.mark.asyncio
async def test_http_options_request(ds_client):
    response = await ds_client.options("/fixtures")
    assert response.status_code == 200
    assert response.text == "ok"


@pytest.mark.asyncio
async def test_db_path(app_client):
    # Needs app_client because needs file based database
    db = app_client.ds.get_database()
    path = pathlib.Path(db.path)

    assert path.exists()

    datasette = Datasette([path])

    # Previously this broke if path was a pathlib.Path:
    await datasette.refresh_schemas()


@pytest.mark.asyncio
async def test_hidden_sqlite_stat1_table():
    ds = Datasette()
    db = ds.add_memory_database("db")
    await db.execute_write("create table normal (id integer primary key, name text)")
    await db.execute_write("create index idx on normal (name)")
    await db.execute_write("analyze")
    data = (await ds.client.get("/db.json?_show_hidden=1")).json()
    tables = [(t["name"], t["hidden"]) for t in data["tables"]]
    assert tables in (
        [("normal", False), ("sqlite_stat1", True)],
        [("normal", False), ("sqlite_stat1", True), ("sqlite_stat4", True)],
    )


@pytest.mark.asyncio
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


@pytest.mark.asyncio
@pytest.mark.parametrize("db_name", ("foo", r"fo%o", "f~/c.d"))
async def test_tilde_encoded_database_names(db_name):
    ds = Datasette()
    ds.add_memory_database(db_name)
    response = await ds.client.get("/.json")
    assert db_name in response.json()["databases"].keys()
    path = response.json()["databases"][db_name]["path"]
    # And the JSON for that database
    response2 = await ds.client.get(path + ".json")
    assert response2.status_code == 200


@pytest.mark.asyncio
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
    response = await ds.client.get("/-/config.json")
    assert response.json() == expected


@pytest.mark.asyncio
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
    assert response.json() == expected_config
    response2 = await ds.client.get("/-/metadata.json")
    assert response2.json() == expected_metadata


class Either:
    def __init__(self, a, b):
        self.a = a
        self.b = b

    def __eq__(self, other):
        return other == self.a or other == self.b
