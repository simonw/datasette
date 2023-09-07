from datasette.app import Datasette
from datasette.plugins import DEFAULT_PLUGINS
from datasette.utils.sqlite import supports_table_xinfo
from datasette.version import __version__
from .fixtures import (  # noqa
    app_client,
    app_client_no_files,
    app_client_with_dot,
    app_client_shorter_time_limit,
    app_client_two_attached_databases_one_immutable,
    app_client_larger_cache_size,
    app_client_with_cors,
    app_client_two_attached_databases,
    app_client_conflicting_database_names,
    app_client_immutable_and_inspect_file,
    make_app_client,
    EXPECTED_PLUGINS,
    METADATA,
)
import pathlib
import pytest
import sys
import urllib


def test_homepage(app_client):
    response = app_client.get("/.json")
    assert response.status == 200
    assert "application/json; charset=utf-8" == response.headers["content-type"]
    assert response.json.keys() == {"fixtures": 0}.keys()
    d = response.json["fixtures"]
    assert d["name"] == "fixtures"
    assert d["tables_count"] == 24
    assert len(d["tables_and_views_truncated"]) == 5
    assert d["tables_and_views_more"] is True
    # 4 hidden FTS tables + no_primary_key (hidden in metadata)
    assert d["hidden_tables_count"] == 6
    # 201 in no_primary_key, plus 6 in other hidden tables:
    assert d["hidden_table_rows_sum"] == 207, response.json
    assert d["views_count"] == 4


def test_homepage_sort_by_relationships(app_client):
    response = app_client.get("/.json?_sort=relationships")
    assert response.status == 200
    tables = [
        t["name"] for t in response.json["fixtures"]["tables_and_views_truncated"]
    ]
    assert tables == [
        "simple_primary_key",
        "foreign_key_references",
        "complex_foreign_keys",
        "roadside_attraction_characteristics",
        "searchable_tags",
    ]


def test_database_page(app_client):
    response = app_client.get("/fixtures.json")
    assert response.status == 200
    data = response.json
    assert data["database"] == "fixtures"
    assert data["tables"] == [
        {
            "name": "123_starts_with_digits",
            "columns": ["content"],
            "primary_keys": [],
            "count": 0,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "Table With Space In Name",
            "columns": ["pk", "content"],
            "primary_keys": ["pk"],
            "count": 0,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "attraction_characteristic",
            "columns": ["pk", "name"],
            "primary_keys": ["pk"],
            "count": 2,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [
                    {
                        "other_table": "roadside_attraction_characteristics",
                        "column": "pk",
                        "other_column": "characteristic_id",
                    }
                ],
                "outgoing": [],
            },
            "private": False,
        },
        {
            "name": "binary_data",
            "columns": ["data"],
            "primary_keys": [],
            "count": 3,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "complex_foreign_keys",
            "columns": ["pk", "f1", "f2", "f3"],
            "primary_keys": ["pk"],
            "count": 1,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [],
                "outgoing": [
                    {
                        "other_table": "simple_primary_key",
                        "column": "f3",
                        "other_column": "id",
                    },
                    {
                        "other_table": "simple_primary_key",
                        "column": "f2",
                        "other_column": "id",
                    },
                    {
                        "other_table": "simple_primary_key",
                        "column": "f1",
                        "other_column": "id",
                    },
                ],
            },
            "private": False,
        },
        {
            "name": "compound_primary_key",
            "columns": ["pk1", "pk2", "content"],
            "primary_keys": ["pk1", "pk2"],
            "count": 2,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "compound_three_primary_keys",
            "columns": ["pk1", "pk2", "pk3", "content"],
            "primary_keys": ["pk1", "pk2", "pk3"],
            "count": 1001,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "custom_foreign_key_label",
            "columns": ["pk", "foreign_key_with_custom_label"],
            "primary_keys": ["pk"],
            "count": 1,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [],
                "outgoing": [
                    {
                        "other_table": "primary_key_multiple_columns_explicit_label",
                        "column": "foreign_key_with_custom_label",
                        "other_column": "id",
                    }
                ],
            },
            "private": False,
        },
        {
            "name": "facet_cities",
            "columns": ["id", "name"],
            "primary_keys": ["id"],
            "count": 4,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [
                    {
                        "other_table": "facetable",
                        "column": "id",
                        "other_column": "_city_id",
                    }
                ],
                "outgoing": [],
            },
            "private": False,
        },
        {
            "name": "facetable",
            "columns": [
                "pk",
                "created",
                "planet_int",
                "on_earth",
                "state",
                "_city_id",
                "_neighborhood",
                "tags",
                "complex_array",
                "distinct_some_null",
                "n",
            ],
            "primary_keys": ["pk"],
            "count": 15,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [],
                "outgoing": [
                    {
                        "other_table": "facet_cities",
                        "column": "_city_id",
                        "other_column": "id",
                    }
                ],
            },
            "private": False,
        },
        {
            "name": "foreign_key_references",
            "columns": [
                "pk",
                "foreign_key_with_label",
                "foreign_key_with_blank_label",
                "foreign_key_with_no_label",
                "foreign_key_compound_pk1",
                "foreign_key_compound_pk2",
            ],
            "primary_keys": ["pk"],
            "count": 2,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [],
                "outgoing": [
                    {
                        "other_table": "primary_key_multiple_columns",
                        "column": "foreign_key_with_no_label",
                        "other_column": "id",
                    },
                    {
                        "other_table": "simple_primary_key",
                        "column": "foreign_key_with_blank_label",
                        "other_column": "id",
                    },
                    {
                        "other_table": "simple_primary_key",
                        "column": "foreign_key_with_label",
                        "other_column": "id",
                    },
                ],
            },
            "private": False,
        },
    ] + [
        {
            "name": "infinity",
            "columns": ["value"],
            "primary_keys": [],
            "count": 3,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "primary_key_multiple_columns",
            "columns": ["id", "content", "content2"],
            "primary_keys": ["id"],
            "count": 1,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [
                    {
                        "other_table": "foreign_key_references",
                        "column": "id",
                        "other_column": "foreign_key_with_no_label",
                    }
                ],
                "outgoing": [],
            },
            "private": False,
        },
        {
            "name": "primary_key_multiple_columns_explicit_label",
            "columns": ["id", "content", "content2"],
            "primary_keys": ["id"],
            "count": 1,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [
                    {
                        "other_table": "custom_foreign_key_label",
                        "column": "id",
                        "other_column": "foreign_key_with_custom_label",
                    }
                ],
                "outgoing": [],
            },
            "private": False,
        },
        {
            "name": "roadside_attraction_characteristics",
            "columns": ["attraction_id", "characteristic_id"],
            "primary_keys": [],
            "count": 5,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [],
                "outgoing": [
                    {
                        "other_table": "attraction_characteristic",
                        "column": "characteristic_id",
                        "other_column": "pk",
                    },
                    {
                        "other_table": "roadside_attractions",
                        "column": "attraction_id",
                        "other_column": "pk",
                    },
                ],
            },
            "private": False,
        },
        {
            "name": "roadside_attractions",
            "columns": ["pk", "name", "address", "url", "latitude", "longitude"],
            "primary_keys": ["pk"],
            "count": 4,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [
                    {
                        "other_table": "roadside_attraction_characteristics",
                        "column": "pk",
                        "other_column": "attraction_id",
                    }
                ],
                "outgoing": [],
            },
            "private": False,
        },
        {
            "name": "searchable",
            "columns": ["pk", "text1", "text2", "name with . and spaces"],
            "primary_keys": ["pk"],
            "count": 2,
            "hidden": False,
            "fts_table": "searchable_fts",
            "foreign_keys": {
                "incoming": [
                    {
                        "other_table": "searchable_tags",
                        "column": "pk",
                        "other_column": "searchable_id",
                    }
                ],
                "outgoing": [],
            },
            "private": False,
        },
        {
            "name": "searchable_tags",
            "columns": ["searchable_id", "tag"],
            "primary_keys": ["searchable_id", "tag"],
            "count": 2,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [],
                "outgoing": [
                    {"other_table": "tags", "column": "tag", "other_column": "tag"},
                    {
                        "other_table": "searchable",
                        "column": "searchable_id",
                        "other_column": "pk",
                    },
                ],
            },
            "private": False,
        },
        {
            "name": "select",
            "columns": ["group", "having", "and", "json"],
            "primary_keys": [],
            "count": 1,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "simple_primary_key",
            "columns": ["id", "content"],
            "primary_keys": ["id"],
            "count": 5,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [
                    {
                        "other_table": "foreign_key_references",
                        "column": "id",
                        "other_column": "foreign_key_with_blank_label",
                    },
                    {
                        "other_table": "foreign_key_references",
                        "column": "id",
                        "other_column": "foreign_key_with_label",
                    },
                    {
                        "other_table": "complex_foreign_keys",
                        "column": "id",
                        "other_column": "f3",
                    },
                    {
                        "other_table": "complex_foreign_keys",
                        "column": "id",
                        "other_column": "f2",
                    },
                    {
                        "other_table": "complex_foreign_keys",
                        "column": "id",
                        "other_column": "f1",
                    },
                ],
                "outgoing": [],
            },
            "private": False,
        },
        {
            "name": "sortable",
            "columns": [
                "pk1",
                "pk2",
                "content",
                "sortable",
                "sortable_with_nulls",
                "sortable_with_nulls_2",
                "text",
            ],
            "primary_keys": ["pk1", "pk2"],
            "count": 201,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "table/with/slashes.csv",
            "columns": ["pk", "content"],
            "primary_keys": ["pk"],
            "count": 1,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "tags",
            "columns": ["tag"],
            "primary_keys": ["tag"],
            "count": 2,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [
                    {
                        "other_table": "searchable_tags",
                        "column": "tag",
                        "other_column": "tag",
                    }
                ],
                "outgoing": [],
            },
            "private": False,
        },
        {
            "name": "units",
            "columns": ["pk", "distance", "frequency"],
            "primary_keys": ["pk"],
            "count": 3,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "no_primary_key",
            "columns": ["content", "a", "b", "c"],
            "primary_keys": [],
            "count": 201,
            "hidden": True,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "searchable_fts",
            "columns": [
                "text1",
                "text2",
                "name with . and spaces",
            ]
            + (
                [
                    "searchable_fts",
                    "docid",
                    "__langid",
                ]
                if supports_table_xinfo()
                else []
            ),
            "primary_keys": [],
            "count": 2,
            "hidden": True,
            "fts_table": "searchable_fts",
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "searchable_fts_docsize",
            "columns": ["docid", "size"],
            "primary_keys": ["docid"],
            "count": 2,
            "hidden": True,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "searchable_fts_segdir",
            "columns": [
                "level",
                "idx",
                "start_block",
                "leaves_end_block",
                "end_block",
                "root",
            ],
            "primary_keys": ["level", "idx"],
            "count": 1,
            "hidden": True,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "searchable_fts_segments",
            "columns": ["blockid", "block"],
            "primary_keys": ["blockid"],
            "count": 0,
            "hidden": True,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "searchable_fts_stat",
            "columns": ["id", "value"],
            "primary_keys": ["id"],
            "count": 1,
            "hidden": True,
            "fts_table": None,
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
    ]


def test_no_files_uses_memory_database(app_client_no_files):
    response = app_client_no_files.get("/.json")
    assert response.status == 200
    assert {
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
        }
    } == response.json
    # Try that SQL query
    response = app_client_no_files.get(
        "/_memory.json?sql=select+sqlite_version()&_shape=array"
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


def test_custom_sql(app_client):
    response = app_client.get(
        "/fixtures.json?sql=select+content+from+simple_primary_key&_shape=objects"
    )
    data = response.json
    assert {"sql": "select content from simple_primary_key", "params": {}} == data[
        "query"
    ]
    assert [
        {"content": "hello"},
        {"content": "world"},
        {"content": ""},
        {"content": "RENDER_CELL_DEMO"},
        {"content": "RENDER_CELL_ASYNC"},
    ] == data["rows"]
    assert ["content"] == data["columns"]
    assert "fixtures" == data["database"]
    assert not data["truncated"]


def test_sql_time_limit(app_client_shorter_time_limit):
    response = app_client_shorter_time_limit.get("/fixtures.json?sql=select+sleep(0.5)")
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


def test_custom_sql_time_limit(app_client):
    response = app_client.get("/fixtures.json?sql=select+sleep(0.01)")
    assert 200 == response.status
    response = app_client.get("/fixtures.json?sql=select+sleep(0.01)&_timelimit=5")
    assert 400 == response.status
    assert "SQL Interrupted" == response.json["title"]


def test_invalid_custom_sql(app_client):
    response = app_client.get("/fixtures.json?sql=.schema")
    assert response.status == 400
    assert response.json["ok"] is False
    assert "Statement must be a SELECT" == response.json["error"]


def test_row(app_client):
    response = app_client.get("/fixtures/simple_primary_key/1.json?_shape=objects")
    assert response.status == 200
    assert [{"id": "1", "content": "hello"}] == response.json["rows"]


def test_row_strange_table_name(app_client):
    response = app_client.get(
        "/fixtures/table~2Fwith~2Fslashes~2Ecsv/3.json?_shape=objects"
    )
    assert response.status == 200
    assert [{"pk": "3", "content": "hey"}] == response.json["rows"]


def test_row_foreign_key_tables(app_client):
    response = app_client.get(
        "/fixtures/simple_primary_key/1.json?_extras=foreign_key_tables"
    )
    assert response.status == 200
    assert response.json["foreign_key_tables"] == [
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
        {
            "other_table": "complex_foreign_keys",
            "column": "id",
            "other_column": "f3",
            "count": 1,
            "link": "/fixtures/complex_foreign_keys?f3=1",
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
            "other_column": "f1",
            "count": 1,
            "link": "/fixtures/complex_foreign_keys?f1=1",
        },
    ]


def test_databases_json(app_client_two_attached_databases_one_immutable):
    response = app_client_two_attached_databases_one_immutable.get("/-/databases.json")
    databases = response.json
    assert 2 == len(databases)
    extra_database, fixtures_database = databases
    assert "extra database" == extra_database["name"]
    assert None == extra_database["hash"]
    assert True == extra_database["is_mutable"]
    assert False == extra_database["is_memory"]

    assert "fixtures" == fixtures_database["name"]
    assert fixtures_database["hash"] is not None
    assert False == fixtures_database["is_mutable"]
    assert False == fixtures_database["is_memory"]


def test_metadata_json(app_client):
    response = app_client.get("/-/metadata.json")
    assert METADATA == response.json


def test_threads_json(app_client):
    response = app_client.get("/-/threads.json")
    expected_keys = {"threads", "num_threads"}
    if sys.version_info >= (3, 7, 0):
        expected_keys.update({"tasks", "num_tasks"})
    assert expected_keys == set(response.json.keys())


def test_plugins_json(app_client):
    response = app_client.get("/-/plugins.json")
    assert EXPECTED_PLUGINS == sorted(response.json, key=lambda p: p["name"])
    # Try with ?all=1
    response = app_client.get("/-/plugins.json?all=1")
    names = {p["name"] for p in response.json}
    assert names.issuperset(p["name"] for p in EXPECTED_PLUGINS)
    assert names.issuperset(DEFAULT_PLUGINS)


def test_versions_json(app_client):
    response = app_client.get("/-/versions.json")
    assert "python" in response.json
    assert "3.0" == response.json.get("asgi")
    assert "version" in response.json["python"]
    assert "full" in response.json["python"]
    assert "datasette" in response.json
    assert "version" in response.json["datasette"]
    assert response.json["datasette"]["version"] == __version__
    assert "sqlite" in response.json
    assert "version" in response.json["sqlite"]
    assert "fts_versions" in response.json["sqlite"]
    assert "compile_options" in response.json["sqlite"]


def test_settings_json(app_client):
    response = app_client.get("/-/settings.json")
    assert {
        "default_page_size": 50,
        "default_facet_size": 30,
        "default_allow_sql": True,
        "facet_suggest_time_limit_ms": 50,
        "facet_time_limit_ms": 200,
        "max_returned_rows": 100,
        "sql_time_limit_ms": 200,
        "allow_download": True,
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
    } == response.json


@pytest.mark.parametrize(
    "path,expected_redirect",
    (
        ("/-/config.json", "/-/settings.json"),
        ("/-/config", "/-/settings"),
    ),
)
def test_config_redirects_to_settings(app_client, path, expected_redirect):
    response = app_client.get(path)
    assert response.status == 301
    assert response.headers["Location"] == expected_redirect


test_json_columns_default_expected = [
    {"intval": 1, "strval": "s", "floatval": 0.5, "jsonval": '{"foo": "bar"}'}
]


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
def test_json_columns(app_client, extra_args, expected):
    sql = """
        select 1 as intval, "s" as strval, 0.5 as floatval,
        '{"foo": "bar"}' as jsonval
    """
    path = "/fixtures.json?" + urllib.parse.urlencode({"sql": sql, "_shape": "array"})
    path += extra_args
    response = app_client.get(path)
    assert expected == response.json


def test_config_cache_size(app_client_larger_cache_size):
    response = app_client_larger_cache_size.get("/fixtures/pragma_cache_size.json")
    assert [[-2500]] == response.json["rows"]


def test_config_force_https_urls():
    with make_app_client(settings={"force_https_urls": True}) as client:
        response = client.get("/fixtures/facetable.json?_size=3&_facet=state")
        assert response.json["next_url"].startswith("https://")
        assert response.json["facet_results"]["state"]["results"][0][
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
        ("/fixtures.json?sql=select+blah", 400),
    ],
)
def test_cors(app_client_with_cors, path, status_code):
    response = app_client_with_cors.get(path)
    assert response.status == status_code
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["Access-Control-Allow-Headers"] == "Authorization"
    assert response.headers["Access-Control-Expose-Headers"] == "Link"


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
    response = app_client_immutable_and_inspect_file.get("/fixtures/sortable.json")
    assert response.json["filtered_table_rows_count"] == 100


def test_http_options_request(app_client):
    response = app_client.request("/fixtures", method="OPTIONS")
    assert response.status == 200
    assert response.text == "ok"


@pytest.mark.asyncio
async def test_db_path(app_client):
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
@pytest.mark.parametrize("db_name", ("foo", r"fo%o", "f~/c.d"))
async def test_tilde_encoded_database_names(db_name):
    ds = Datasette()
    ds.add_memory_database(db_name)
    response = await ds.client.get("/.json")
    assert db_name in response.json().keys()
    path = response.json()[db_name]["path"]
    # And the JSON for that database
    response2 = await ds.client.get(path + ".json")
    assert response2.status_code == 200
