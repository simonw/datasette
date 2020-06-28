from datasette.plugins import DEFAULT_PLUGINS
from datasette.utils import detect_json1
from .fixtures import (  # noqa
    app_client,
    app_client_no_files,
    app_client_with_hash,
    app_client_shorter_time_limit,
    app_client_larger_cache_size,
    app_client_returned_rows_matches_page_size,
    app_client_two_attached_databases,
    app_client_two_attached_databases_one_immutable,
    app_client_conflicting_database_names,
    app_client_with_cors,
    app_client_with_dot,
    app_client_immutable_and_inspect_file,
    generate_compound_rows,
    generate_sortable_rows,
    make_app_client,
    EXPECTED_PLUGINS,
    METADATA,
)
import json
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
    assert d["hidden_tables_count"] == 5
    # 201 in no_primary_key, plus 5 in other hidden tables:
    assert d["hidden_table_rows_sum"] == 206
    assert d["views_count"] == 4


def test_homepage_sort_by_relationships(app_client):
    response = app_client.get("/.json?_sort=relationships")
    assert response.status == 200
    tables = [
        t["name"] for t in response.json["fixtures"]["tables_and_views_truncated"]
    ]
    assert [
        "simple_primary_key",
        "complex_foreign_keys",
        "roadside_attraction_characteristics",
        "searchable_tags",
        "foreign_key_references",
    ] == tables


def test_database_page(app_client):
    response = app_client.get("/fixtures.json")
    data = response.json
    assert "fixtures" == data["database"]
    assert [
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
            "count": 1,
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
            "count": 1,
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
                        "other_column": "city_id",
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
                "city_id",
                "neighborhood",
                "tags",
                "complex_array",
                "distinct_some_null",
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
                        "column": "city_id",
                        "other_column": "id",
                    }
                ],
            },
            "private": False,
        },
        {
            "name": "foreign_key_references",
            "columns": ["pk", "foreign_key_with_label", "foreign_key_with_no_label"],
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
                        "column": "foreign_key_with_label",
                        "other_column": "id",
                    },
                ],
            },
            "private": False,
        },
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
            "columns": ["pk", "name", "address", "latitude", "longitude"],
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
            "count": 4,
            "hidden": False,
            "fts_table": None,
            "foreign_keys": {
                "incoming": [
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
            "columns": ["text1", "text2", "name with . and spaces", "content"],
            "primary_keys": [],
            "count": 2,
            "hidden": True,
            "fts_table": "searchable_fts",
            "foreign_keys": {"incoming": [], "outgoing": []},
            "private": False,
        },
        {
            "name": "searchable_fts_content",
            "columns": [
                "docid",
                "c0text1",
                "c1text2",
                "c2name with . and spaces",
                "c3content",
            ],
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
    ] == data["tables"]


def test_no_files_uses_memory_database(app_client_no_files):
    response = app_client_no_files.get("/.json")
    assert response.status == 200
    assert {
        ":memory:": {
            "hash": None,
            "color": "f7935d",
            "hidden_table_rows_sum": 0,
            "hidden_tables_count": 0,
            "name": ":memory:",
            "show_table_row_counts": False,
            "path": "/:memory:",
            "table_rows_sum": 0,
            "tables_count": 0,
            "tables_and_views_more": False,
            "tables_and_views_truncated": [],
            "views_count": 0,
            "private": False,
        }
    } == response.json
    # Try that SQL query
    response = app_client_no_files.get(
        "/:memory:.json?sql=select+sqlite_version()&_shape=array"
    )
    assert 1 == len(response.json)
    assert ["sqlite_version()"] == list(response.json[0].keys())


def test_database_page_for_database_with_dot_in_name(app_client_with_dot):
    response = app_client_with_dot.get("/fixtures.dot.json")
    assert 200 == response.status


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
    ] == data["rows"]
    assert ["content"] == data["columns"]
    assert "fixtures" == data["database"]
    assert not data["truncated"]


def test_sql_time_limit(app_client_shorter_time_limit):
    response = app_client_shorter_time_limit.get("/fixtures.json?sql=select+sleep(0.5)")
    assert 400 == response.status
    assert "SQL Interrupted" == response.json["title"]


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


def test_table_json(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_shape=objects")
    assert response.status == 200
    data = response.json
    assert (
        data["query"]["sql"]
        == "select id, content from simple_primary_key order by id limit 51"
    )
    assert data["query"]["params"] == {}
    assert data["rows"] == [
        {"id": "1", "content": "hello"},
        {"id": "2", "content": "world"},
        {"id": "3", "content": ""},
        {"id": "4", "content": "RENDER_CELL_DEMO"},
    ]


def test_table_not_exists_json(app_client):
    assert {
        "ok": False,
        "error": "Table not found: blah",
        "status": 404,
        "title": None,
    } == app_client.get("/fixtures/blah.json").json


def test_jsono_redirects_to_shape_objects(app_client_with_hash):
    response_1 = app_client_with_hash.get(
        "/fixtures/simple_primary_key.jsono", allow_redirects=False
    )
    response = app_client_with_hash.get(
        response_1.headers["Location"], allow_redirects=False
    )
    assert response.status == 302
    assert response.headers["Location"].endswith("?_shape=objects")


def test_table_shape_arrays(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_shape=arrays")
    assert [
        ["1", "hello"],
        ["2", "world"],
        ["3", ""],
        ["4", "RENDER_CELL_DEMO"],
    ] == response.json["rows"]


def test_table_shape_arrayfirst(app_client):
    response = app_client.get(
        "/fixtures.json?"
        + urllib.parse.urlencode(
            {
                "sql": "select content from simple_primary_key order by id",
                "_shape": "arrayfirst",
            }
        )
    )
    assert ["hello", "world", "", "RENDER_CELL_DEMO"] == response.json


def test_table_shape_objects(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_shape=objects")
    assert [
        {"id": "1", "content": "hello"},
        {"id": "2", "content": "world"},
        {"id": "3", "content": ""},
        {"id": "4", "content": "RENDER_CELL_DEMO"},
    ] == response.json["rows"]


def test_table_shape_array(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_shape=array")
    assert [
        {"id": "1", "content": "hello"},
        {"id": "2", "content": "world"},
        {"id": "3", "content": ""},
        {"id": "4", "content": "RENDER_CELL_DEMO"},
    ] == response.json


def test_table_shape_array_nl(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_shape=array&_nl=on")
    lines = response.text.split("\n")
    results = [json.loads(line) for line in lines]
    assert [
        {"id": "1", "content": "hello"},
        {"id": "2", "content": "world"},
        {"id": "3", "content": ""},
        {"id": "4", "content": "RENDER_CELL_DEMO"},
    ] == results


def test_table_shape_invalid(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_shape=invalid")
    assert {
        "ok": False,
        "error": "Invalid _shape: invalid",
        "status": 400,
        "title": None,
    } == response.json


def test_table_shape_object(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_shape=object")
    assert {
        "1": {"id": "1", "content": "hello"},
        "2": {"id": "2", "content": "world"},
        "3": {"id": "3", "content": ""},
        "4": {"id": "4", "content": "RENDER_CELL_DEMO"},
    } == response.json


def test_table_shape_object_compound_primary_Key(app_client):
    response = app_client.get("/fixtures/compound_primary_key.json?_shape=object")
    assert {"a,b": {"pk1": "a", "pk2": "b", "content": "c"}} == response.json


def test_table_with_slashes_in_name(app_client):
    response = app_client.get(
        "/fixtures/table%2Fwith%2Fslashes.csv?_shape=objects&_format=json"
    )
    assert response.status == 200
    data = response.json
    assert data["rows"] == [{"pk": "3", "content": "hey"}]


def test_table_with_reserved_word_name(app_client):
    response = app_client.get("/fixtures/select.json?_shape=objects")
    assert response.status == 200
    data = response.json
    assert data["rows"] == [
        {
            "rowid": 1,
            "group": "group",
            "having": "having",
            "and": "and",
            "json": '{"href": "http://example.com/", "label":"Example"}',
        }
    ]


@pytest.mark.parametrize(
    "path,expected_rows,expected_pages",
    [
        ("/fixtures/no_primary_key.json", 201, 5),
        ("/fixtures/paginated_view.json", 201, 9),
        ("/fixtures/no_primary_key.json?_size=25", 201, 9),
        ("/fixtures/paginated_view.json?_size=50", 201, 5),
        ("/fixtures/paginated_view.json?_size=max", 201, 3),
        ("/fixtures/123_starts_with_digits.json", 0, 1),
        # Ensure faceting doesn't break pagination:
        ("/fixtures/compound_three_primary_keys.json?_facet=pk1", 1001, 21),
        # Paginating while sorted by an expanded foreign key should work
        (
            "/fixtures/roadside_attraction_characteristics.json?_size=2&_sort=attraction_id&_labels=on",
            5,
            3,
        ),
    ],
)
def test_paginate_tables_and_views(app_client, path, expected_rows, expected_pages):
    fetched = []
    count = 0
    while path:
        response = app_client.get(path)
        assert 200 == response.status
        count += 1
        fetched.extend(response.json["rows"])
        path = response.json["next_url"]
        if path:
            assert urllib.parse.urlencode({"_next": response.json["next"]}) in path
            path = path.replace("http://localhost", "")
        assert count < 30, "Possible infinite loop detected"

    assert expected_rows == len(fetched)
    assert expected_pages == count


@pytest.mark.parametrize(
    "path,expected_error",
    [
        ("/fixtures/no_primary_key.json?_size=-4", "_size must be a positive integer"),
        ("/fixtures/no_primary_key.json?_size=dog", "_size must be a positive integer"),
        ("/fixtures/no_primary_key.json?_size=1001", "_size must be <= 100"),
    ],
)
def test_validate_page_size(app_client, path, expected_error):
    response = app_client.get(path)
    assert expected_error == response.json["error"]
    assert 400 == response.status


def test_page_size_zero(app_client):
    "For _size=0 we return the counts, empty rows and no continuation token"
    response = app_client.get("/fixtures/no_primary_key.json?_size=0")
    assert 200 == response.status
    assert [] == response.json["rows"]
    assert 201 == response.json["filtered_table_rows_count"]
    assert None is response.json["next"]
    assert None is response.json["next_url"]


def test_paginate_compound_keys(app_client):
    fetched = []
    path = "/fixtures/compound_three_primary_keys.json?_shape=objects"
    page = 0
    while path:
        page += 1
        response = app_client.get(path)
        fetched.extend(response.json["rows"])
        path = response.json["next_url"]
        if path:
            path = path.replace("http://localhost", "")
        assert page < 100
    assert 1001 == len(fetched)
    assert 21 == page
    # Should be correctly ordered
    contents = [f["content"] for f in fetched]
    expected = [r[3] for r in generate_compound_rows(1001)]
    assert expected == contents


def test_paginate_compound_keys_with_extra_filters(app_client):
    fetched = []
    path = (
        "/fixtures/compound_three_primary_keys.json?content__contains=d&_shape=objects"
    )
    page = 0
    while path:
        page += 1
        assert page < 100
        response = app_client.get(path)
        fetched.extend(response.json["rows"])
        path = response.json["next_url"]
        if path:
            path = path.replace("http://localhost", "")
    assert 2 == page
    expected = [r[3] for r in generate_compound_rows(1001) if "d" in r[3]]
    assert expected == [f["content"] for f in fetched]


@pytest.mark.parametrize(
    "query_string,sort_key,human_description_en",
    [
        ("_sort=sortable", lambda row: row["sortable"], "sorted by sortable"),
        (
            "_sort_desc=sortable",
            lambda row: -row["sortable"],
            "sorted by sortable descending",
        ),
        (
            "_sort=sortable_with_nulls",
            lambda row: (
                1 if row["sortable_with_nulls"] is not None else 0,
                row["sortable_with_nulls"],
            ),
            "sorted by sortable_with_nulls",
        ),
        (
            "_sort_desc=sortable_with_nulls",
            lambda row: (
                1 if row["sortable_with_nulls"] is None else 0,
                -row["sortable_with_nulls"]
                if row["sortable_with_nulls"] is not None
                else 0,
                row["content"],
            ),
            "sorted by sortable_with_nulls descending",
        ),
        # text column contains '$null' - ensure it doesn't confuse pagination:
        ("_sort=text", lambda row: row["text"], "sorted by text"),
    ],
)
def test_sortable(app_client, query_string, sort_key, human_description_en):
    path = "/fixtures/sortable.json?_shape=objects&{}".format(query_string)
    fetched = []
    page = 0
    while path:
        page += 1
        assert page < 100
        response = app_client.get(path)
        assert human_description_en == response.json["human_description_en"]
        fetched.extend(response.json["rows"])
        path = response.json["next_url"]
        if path:
            path = path.replace("http://localhost", "")
    assert 5 == page
    expected = list(generate_sortable_rows(201))
    expected.sort(key=sort_key)
    assert [r["content"] for r in expected] == [r["content"] for r in fetched]


def test_sortable_and_filtered(app_client):
    path = (
        "/fixtures/sortable.json"
        "?content__contains=d&_sort_desc=sortable&_shape=objects"
    )
    response = app_client.get(path)
    fetched = response.json["rows"]
    assert (
        'where content contains "d" sorted by sortable descending'
        == response.json["human_description_en"]
    )
    expected = [row for row in generate_sortable_rows(201) if "d" in row["content"]]
    assert len(expected) == response.json["filtered_table_rows_count"]
    expected.sort(key=lambda row: -row["sortable"])
    assert [r["content"] for r in expected] == [r["content"] for r in fetched]


def test_sortable_argument_errors(app_client):
    response = app_client.get("/fixtures/sortable.json?_sort=badcolumn")
    assert "Cannot sort table by badcolumn" == response.json["error"]
    response = app_client.get("/fixtures/sortable.json?_sort_desc=badcolumn2")
    assert "Cannot sort table by badcolumn2" == response.json["error"]
    response = app_client.get(
        "/fixtures/sortable.json?_sort=sortable_with_nulls&_sort_desc=sortable"
    )
    assert "Cannot use _sort and _sort_desc at the same time" == response.json["error"]


def test_sortable_columns_metadata(app_client):
    response = app_client.get("/fixtures/sortable.json?_sort=content")
    assert "Cannot sort table by content" == response.json["error"]
    # no_primary_key has ALL sort options disabled
    for column in ("content", "a", "b", "c"):
        response = app_client.get("/fixtures/sortable.json?_sort={}".format(column))
        assert "Cannot sort table by {}".format(column) == response.json["error"]


@pytest.mark.parametrize(
    "path,expected_rows",
    [
        (
            "/fixtures/searchable.json?_search=dog",
            [
                [1, "barry cat", "terry dog", "panther"],
                [2, "terry dog", "sara weasel", "puma"],
            ],
        ),
        (
            # Special keyword shouldn't break FTS query
            "/fixtures/searchable.json?_search=AND",
            [],
        ),
        (
            # Without _searchmode=raw this should return no results
            "/fixtures/searchable.json?_search=te*+AND+do*",
            [],
        ),
        (
            # _searchmode=raw
            "/fixtures/searchable.json?_search=te*+AND+do*&_searchmode=raw",
            [
                [1, "barry cat", "terry dog", "panther"],
                [2, "terry dog", "sara weasel", "puma"],
            ],
        ),
        (
            "/fixtures/searchable.json?_search=weasel",
            [[2, "terry dog", "sara weasel", "puma"]],
        ),
        (
            "/fixtures/searchable.json?_search_text2=dog",
            [[1, "barry cat", "terry dog", "panther"]],
        ),
        (
            "/fixtures/searchable.json?_search_name%20with%20.%20and%20spaces=panther",
            [[1, "barry cat", "terry dog", "panther"]],
        ),
    ],
)
def test_searchable(app_client, path, expected_rows):
    response = app_client.get(path)
    assert expected_rows == response.json["rows"]


@pytest.mark.parametrize(
    "path,expected_rows",
    [
        (
            "/fixtures/searchable_view_configured_by_metadata.json?_search=weasel",
            [[2, "terry dog", "sara weasel", "puma"]],
        ),
        # This should return all results because search is not configured:
        (
            "/fixtures/searchable_view.json?_search=weasel",
            [
                [1, "barry cat", "terry dog", "panther"],
                [2, "terry dog", "sara weasel", "puma"],
            ],
        ),
        (
            "/fixtures/searchable_view.json?_search=weasel&_fts_table=searchable_fts&_fts_pk=pk",
            [[2, "terry dog", "sara weasel", "puma"]],
        ),
    ],
)
def test_searchable_views(app_client, path, expected_rows):
    response = app_client.get(path)
    assert expected_rows == response.json["rows"]


def test_searchable_invalid_column(app_client):
    response = app_client.get("/fixtures/searchable.json?_search_invalid=x")
    assert 400 == response.status
    assert {
        "ok": False,
        "error": "Cannot search by that column",
        "status": 400,
        "title": None,
    } == response.json


@pytest.mark.parametrize(
    "path,expected_rows",
    [
        ("/fixtures/simple_primary_key.json?content=hello", [["1", "hello"]]),
        (
            "/fixtures/simple_primary_key.json?content__contains=o",
            [["1", "hello"], ["2", "world"], ["4", "RENDER_CELL_DEMO"]],
        ),
        ("/fixtures/simple_primary_key.json?content__exact=", [["3", ""]]),
        (
            "/fixtures/simple_primary_key.json?content__not=world",
            [["1", "hello"], ["3", ""], ["4", "RENDER_CELL_DEMO"]],
        ),
    ],
)
def test_table_filter_queries(app_client, path, expected_rows):
    response = app_client.get(path)
    assert expected_rows == response.json["rows"]


def test_table_filter_queries_multiple_of_same_type(app_client):
    response = app_client.get(
        "/fixtures/simple_primary_key.json?content__not=world&content__not=hello"
    )
    assert [["3", ""], ["4", "RENDER_CELL_DEMO"]] == response.json["rows"]


@pytest.mark.skipif(not detect_json1(), reason="Requires the SQLite json1 module")
def test_table_filter_json_arraycontains(app_client):
    response = app_client.get("/fixtures/facetable.json?tags__arraycontains=tag1")
    assert [
        [
            1,
            "2019-01-14 08:00:00",
            1,
            1,
            "CA",
            1,
            "Mission",
            '["tag1", "tag2"]',
            '[{"foo": "bar"}]',
            "one",
        ],
        [
            2,
            "2019-01-14 08:00:00",
            1,
            1,
            "CA",
            1,
            "Dogpatch",
            '["tag1", "tag3"]',
            "[]",
            "two",
        ],
    ] == response.json["rows"]


def test_table_filter_extra_where(app_client):
    response = app_client.get("/fixtures/facetable.json?_where=neighborhood='Dogpatch'")
    assert [
        [
            2,
            "2019-01-14 08:00:00",
            1,
            1,
            "CA",
            1,
            "Dogpatch",
            '["tag1", "tag3"]',
            "[]",
            "two",
        ]
    ] == response.json["rows"]


def test_table_filter_extra_where_invalid(app_client):
    response = app_client.get("/fixtures/facetable.json?_where=neighborhood=Dogpatch'")
    assert 400 == response.status
    assert "Invalid SQL" == response.json["title"]


def test_table_filter_extra_where_disabled_if_no_sql_allowed():
    with make_app_client(metadata={"allow_sql": {}}) as client:
        response = client.get("/fixtures/facetable.json?_where=neighborhood='Dogpatch'")
        assert 403 == response.status
        assert "_where= is not allowed" == response.json["error"]


def test_table_through(app_client):
    # Just the museums:
    response = app_client.get(
        '/fixtures/roadside_attractions.json?_through={"table":"roadside_attraction_characteristics","column":"characteristic_id","value":"1"}'
    )
    assert [
        [
            3,
            "Burlingame Museum of PEZ Memorabilia",
            "214 California Drive, Burlingame, CA 94010",
            37.5793,
            -122.3442,
        ],
        [
            4,
            "Bigfoot Discovery Museum",
            "5497 Highway 9, Felton, CA 95018",
            37.0414,
            -122.0725,
        ],
    ] == response.json["rows"]
    assert (
        'where roadside_attraction_characteristics.characteristic_id = "1"'
        == response.json["human_description_en"]
    )


def test_max_returned_rows(app_client):
    response = app_client.get("/fixtures.json?sql=select+content+from+no_primary_key")
    data = response.json
    assert {"sql": "select content from no_primary_key", "params": {}} == data["query"]
    assert data["truncated"]
    assert 100 == len(data["rows"])


def test_view(app_client):
    response = app_client.get("/fixtures/simple_view.json?_shape=objects")
    assert response.status == 200
    data = response.json
    assert data["rows"] == [
        {"upper_content": "HELLO", "content": "hello"},
        {"upper_content": "WORLD", "content": "world"},
        {"upper_content": "", "content": ""},
        {"upper_content": "RENDER_CELL_DEMO", "content": "RENDER_CELL_DEMO"},
    ]


def test_row(app_client):
    response = app_client.get("/fixtures/simple_primary_key/1.json?_shape=objects")
    assert response.status == 200
    assert [{"id": "1", "content": "hello"}] == response.json["rows"]


def test_row_format_in_querystring(app_client):
    # regression test for https://github.com/simonw/datasette/issues/563
    response = app_client.get(
        "/fixtures/simple_primary_key/1?_format=json&_shape=objects"
    )
    assert response.status == 200
    assert [{"id": "1", "content": "hello"}] == response.json["rows"]


def test_row_strange_table_name(app_client):
    response = app_client.get(
        "/fixtures/table%2Fwith%2Fslashes.csv/3.json?_shape=objects"
    )
    assert response.status == 200
    assert [{"pk": "3", "content": "hey"}] == response.json["rows"]


def test_row_foreign_key_tables(app_client):
    response = app_client.get(
        "/fixtures/simple_primary_key/1.json?_extras=foreign_key_tables"
    )
    assert response.status == 200
    assert [
        {
            "column": "id",
            "count": 1,
            "other_column": "foreign_key_with_label",
            "other_table": "foreign_key_references",
        },
        {
            "column": "id",
            "count": 1,
            "other_column": "f3",
            "other_table": "complex_foreign_keys",
        },
        {
            "column": "id",
            "count": 0,
            "other_column": "f2",
            "other_table": "complex_foreign_keys",
        },
        {
            "column": "id",
            "count": 1,
            "other_column": "f1",
            "other_table": "complex_foreign_keys",
        },
    ] == response.json["foreign_key_tables"]


def test_unit_filters(app_client):
    response = app_client.get(
        "/fixtures/units.json?distance__lt=75km&frequency__gt=1kHz"
    )
    assert response.status == 200
    data = response.json

    assert data["units"]["distance"] == "m"
    assert data["units"]["frequency"] == "Hz"

    assert len(data["rows"]) == 1
    assert data["rows"][0][0] == 2


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
    assert "sqlite" in response.json
    assert "version" in response.json["sqlite"]
    assert "fts_versions" in response.json["sqlite"]
    assert "compile_options" in response.json["sqlite"]


def test_config_json(app_client):
    response = app_client.get("/-/config.json")
    assert {
        "default_page_size": 50,
        "default_facet_size": 30,
        "facet_suggest_time_limit_ms": 50,
        "facet_time_limit_ms": 200,
        "max_returned_rows": 100,
        "sql_time_limit_ms": 200,
        "allow_download": True,
        "allow_facet": True,
        "suggest_facets": True,
        "default_cache_ttl": 5,
        "default_cache_ttl_hashed": 365 * 24 * 60 * 60,
        "num_sql_threads": 1,
        "cache_size_kb": 0,
        "allow_csv_stream": True,
        "max_csv_mb": 100,
        "truncate_cells_html": 2048,
        "force_https_urls": False,
        "hash_urls": False,
        "template_debug": False,
        "base_url": "/",
    } == response.json


def test_page_size_matching_max_returned_rows(
    app_client_returned_rows_matches_page_size,
):
    fetched = []
    path = "/fixtures/no_primary_key.json"
    while path:
        response = app_client_returned_rows_matches_page_size.get(path)
        fetched.extend(response.json["rows"])
        assert len(response.json["rows"]) in (1, 50)
        path = response.json["next_url"]
        if path:
            path = path.replace("http://localhost", "")
    assert 201 == len(fetched)


@pytest.mark.parametrize(
    "path,expected_facet_results",
    [
        (
            "/fixtures/facetable.json?_facet=state&_facet=city_id",
            {
                "state": {
                    "name": "state",
                    "hideable": True,
                    "type": "column",
                    "toggle_url": "/fixtures/facetable.json?_facet=city_id",
                    "results": [
                        {
                            "value": "CA",
                            "label": "CA",
                            "count": 10,
                            "toggle_url": "_facet=state&_facet=city_id&state=CA",
                            "selected": False,
                        },
                        {
                            "value": "MI",
                            "label": "MI",
                            "count": 4,
                            "toggle_url": "_facet=state&_facet=city_id&state=MI",
                            "selected": False,
                        },
                        {
                            "value": "MC",
                            "label": "MC",
                            "count": 1,
                            "toggle_url": "_facet=state&_facet=city_id&state=MC",
                            "selected": False,
                        },
                    ],
                    "truncated": False,
                },
                "city_id": {
                    "name": "city_id",
                    "hideable": True,
                    "type": "column",
                    "toggle_url": "/fixtures/facetable.json?_facet=state",
                    "results": [
                        {
                            "value": 1,
                            "label": "San Francisco",
                            "count": 6,
                            "toggle_url": "_facet=state&_facet=city_id&city_id=1",
                            "selected": False,
                        },
                        {
                            "value": 2,
                            "label": "Los Angeles",
                            "count": 4,
                            "toggle_url": "_facet=state&_facet=city_id&city_id=2",
                            "selected": False,
                        },
                        {
                            "value": 3,
                            "label": "Detroit",
                            "count": 4,
                            "toggle_url": "_facet=state&_facet=city_id&city_id=3",
                            "selected": False,
                        },
                        {
                            "value": 4,
                            "label": "Memnonia",
                            "count": 1,
                            "toggle_url": "_facet=state&_facet=city_id&city_id=4",
                            "selected": False,
                        },
                    ],
                    "truncated": False,
                },
            },
        ),
        (
            "/fixtures/facetable.json?_facet=state&_facet=city_id&state=MI",
            {
                "state": {
                    "name": "state",
                    "hideable": True,
                    "type": "column",
                    "toggle_url": "/fixtures/facetable.json?_facet=city_id&state=MI",
                    "results": [
                        {
                            "value": "MI",
                            "label": "MI",
                            "count": 4,
                            "selected": True,
                            "toggle_url": "_facet=state&_facet=city_id",
                        }
                    ],
                    "truncated": False,
                },
                "city_id": {
                    "name": "city_id",
                    "hideable": True,
                    "type": "column",
                    "toggle_url": "/fixtures/facetable.json?_facet=state&state=MI",
                    "results": [
                        {
                            "value": 3,
                            "label": "Detroit",
                            "count": 4,
                            "selected": False,
                            "toggle_url": "_facet=state&_facet=city_id&state=MI&city_id=3",
                        }
                    ],
                    "truncated": False,
                },
            },
        ),
        (
            "/fixtures/facetable.json?_facet=planet_int",
            {
                "planet_int": {
                    "name": "planet_int",
                    "hideable": True,
                    "type": "column",
                    "toggle_url": "/fixtures/facetable.json",
                    "results": [
                        {
                            "value": 1,
                            "label": 1,
                            "count": 14,
                            "selected": False,
                            "toggle_url": "_facet=planet_int&planet_int=1",
                        },
                        {
                            "value": 2,
                            "label": 2,
                            "count": 1,
                            "selected": False,
                            "toggle_url": "_facet=planet_int&planet_int=2",
                        },
                    ],
                    "truncated": False,
                }
            },
        ),
        (
            # planet_int is an integer field:
            "/fixtures/facetable.json?_facet=planet_int&planet_int=1",
            {
                "planet_int": {
                    "name": "planet_int",
                    "hideable": True,
                    "type": "column",
                    "toggle_url": "/fixtures/facetable.json?planet_int=1",
                    "results": [
                        {
                            "value": 1,
                            "label": 1,
                            "count": 14,
                            "selected": True,
                            "toggle_url": "_facet=planet_int",
                        }
                    ],
                    "truncated": False,
                }
            },
        ),
    ],
)
def test_facets(app_client, path, expected_facet_results):
    response = app_client.get(path)
    facet_results = response.json["facet_results"]
    # We only compare the querystring portion of the taggle_url
    for facet_name, facet_info in facet_results.items():
        assert facet_name == facet_info["name"]
        assert False is facet_info["truncated"]
        for facet_value in facet_info["results"]:
            facet_value["toggle_url"] = facet_value["toggle_url"].split("?")[1]
    assert expected_facet_results == facet_results


def test_suggested_facets(app_client):
    suggestions = [
        {
            "name": suggestion["name"],
            "querystring": suggestion["toggle_url"].split("?")[-1],
        }
        for suggestion in app_client.get("/fixtures/facetable.json").json[
            "suggested_facets"
        ]
    ]
    expected = [
        {"name": "created", "querystring": "_facet=created"},
        {"name": "planet_int", "querystring": "_facet=planet_int"},
        {"name": "on_earth", "querystring": "_facet=on_earth"},
        {"name": "state", "querystring": "_facet=state"},
        {"name": "city_id", "querystring": "_facet=city_id"},
        {"name": "neighborhood", "querystring": "_facet=neighborhood"},
        {"name": "tags", "querystring": "_facet=tags"},
        {"name": "complex_array", "querystring": "_facet=complex_array"},
        {"name": "created", "querystring": "_facet_date=created"},
    ]
    if detect_json1():
        expected.append({"name": "tags", "querystring": "_facet_array=tags"})
    assert expected == suggestions


def test_allow_facet_off():
    with make_app_client(config={"allow_facet": False}) as client:
        assert 400 == client.get("/fixtures/facetable.json?_facet=planet_int").status
        # Should not suggest any facets either:
        assert [] == client.get("/fixtures/facetable.json").json["suggested_facets"]


def test_suggest_facets_off():
    with make_app_client(config={"suggest_facets": False}) as client:
        # Now suggested_facets should be []
        assert [] == client.get("/fixtures/facetable.json").json["suggested_facets"]


def test_expand_labels(app_client):
    response = app_client.get(
        "/fixtures/facetable.json?_shape=object&_labels=1&_size=2"
        "&neighborhood__contains=c"
    )
    assert {
        "2": {
            "pk": 2,
            "created": "2019-01-14 08:00:00",
            "planet_int": 1,
            "on_earth": 1,
            "state": "CA",
            "city_id": {"value": 1, "label": "San Francisco"},
            "neighborhood": "Dogpatch",
            "tags": '["tag1", "tag3"]',
            "complex_array": "[]",
            "distinct_some_null": "two",
        },
        "13": {
            "pk": 13,
            "created": "2019-01-17 08:00:00",
            "planet_int": 1,
            "on_earth": 1,
            "state": "MI",
            "city_id": {"value": 3, "label": "Detroit"},
            "neighborhood": "Corktown",
            "tags": "[]",
            "complex_array": "[]",
            "distinct_some_null": None,
        },
    } == response.json


def test_expand_label(app_client):
    response = app_client.get(
        "/fixtures/foreign_key_references.json?_shape=object"
        "&_label=foreign_key_with_label&_size=1"
    )
    assert {
        "1": {
            "pk": "1",
            "foreign_key_with_label": {"value": "1", "label": "hello"},
            "foreign_key_with_no_label": "1",
        }
    } == response.json


@pytest.mark.parametrize(
    "path,expected_cache_control",
    [
        ("/fixtures/facetable.json", "max-age=5"),
        ("/fixtures/facetable.json?_ttl=invalid", "max-age=5"),
        ("/fixtures/facetable.json?_ttl=10", "max-age=10"),
        ("/fixtures/facetable.json?_ttl=0", "no-cache"),
    ],
)
def test_ttl_parameter(app_client, path, expected_cache_control):
    response = app_client.get(path)
    assert expected_cache_control == response.headers["Cache-Control"]


@pytest.mark.parametrize(
    "path,expected_redirect",
    [
        ("/fixtures/facetable.json?_hash=1", "/fixtures-HASH/facetable.json"),
        (
            "/fixtures/facetable.json?city_id=1&_hash=1",
            "/fixtures-HASH/facetable.json?city_id=1",
        ),
    ],
)
def test_hash_parameter(
    app_client_two_attached_databases_one_immutable, path, expected_redirect
):
    # First get the current hash for the fixtures database
    current_hash = app_client_two_attached_databases_one_immutable.ds.databases[
        "fixtures"
    ].hash[:7]
    response = app_client_two_attached_databases_one_immutable.get(
        path, allow_redirects=False
    )
    assert response.status == 302
    location = response.headers["Location"]
    assert expected_redirect.replace("HASH", current_hash) == location


def test_hash_parameter_ignored_for_mutable_databases(app_client):
    path = "/fixtures/facetable.json?_hash=1"
    response = app_client.get(path, allow_redirects=False)
    assert response.status == 200


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
    with make_app_client(config={"force_https_urls": True}) as client:
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


def test_infinity_returned_as_null(app_client):
    response = app_client.get("/fixtures/infinity.json?_shape=array")
    assert [
        {"rowid": 1, "value": None},
        {"rowid": 2, "value": None},
        {"rowid": 3, "value": 1.5},
    ] == response.json


def test_infinity_returned_as_invalid_json_if_requested(app_client):
    response = app_client.get("/fixtures/infinity.json?_shape=array&_json_infinity=1")
    assert [
        {"rowid": 1, "value": float("inf")},
        {"rowid": 2, "value": float("-inf")},
        {"rowid": 3, "value": 1.5},
    ] == response.json


def test_custom_query_with_unicode_characters(app_client):
    response = app_client.get("/fixtures/𝐜𝐢𝐭𝐢𝐞𝐬.json?_shape=array")
    assert [{"id": 1, "name": "San Francisco"}] == response.json


def test_trace(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_trace=1")
    data = response.json
    assert "_trace" in data
    trace_info = data["_trace"]
    assert isinstance(trace_info["request_duration_ms"], float)
    assert isinstance(trace_info["sum_trace_duration_ms"], float)
    assert isinstance(trace_info["num_traces"], int)
    assert isinstance(trace_info["traces"], list)
    assert len(trace_info["traces"]) == trace_info["num_traces"]
    for trace in trace_info["traces"]:
        assert isinstance(trace["type"], str)
        assert isinstance(trace["start"], float)
        assert isinstance(trace["end"], float)
        assert trace["duration_ms"] == (trace["end"] - trace["start"]) * 1000
        assert isinstance(trace["traceback"], list)
        assert isinstance(trace["database"], str)
        assert isinstance(trace["sql"], str)
        assert isinstance(trace["params"], (list, dict, None.__class__))


@pytest.mark.parametrize(
    "path,status_code",
    [
        ("/fixtures.json", 200),
        ("/fixtures/no_primary_key.json", 200),
        # A 400 invalid SQL query should still have the header:
        ("/fixtures.json?sql=select+blah", 400),
    ],
)
def test_cors(app_client_with_cors, path, status_code):
    response = app_client_with_cors.get(path)
    assert response.status == status_code
    assert "*" == response.headers["Access-Control-Allow-Origin"]


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
    response = app_client_two_attached_databases.get("/extra database" + path)
    assert response.status == 200


def test_common_prefix_database_names(app_client_conflicting_database_names):
    # https://github.com/simonw/datasette/issues/597
    assert ["fixtures", "foo", "foo-bar"] == [
        d["name"]
        for d in app_client_conflicting_database_names.get("/-/databases.json").json
    ]
    for db_name, path in (("foo", "/foo.json"), ("foo-bar", "/foo-bar.json")):
        data = app_client_conflicting_database_names.get(path).json
        assert db_name == data["database"]


def test_null_foreign_keys_are_not_expanded(app_client):
    response = app_client.get(
        "/fixtures/foreign_key_references.json?_shape=array&_labels=on"
    )
    assert [
        {
            "pk": "1",
            "foreign_key_with_label": {"value": "1", "label": "hello"},
            "foreign_key_with_no_label": {"value": "1", "label": "1"},
        },
        {"pk": "2", "foreign_key_with_label": None, "foreign_key_with_no_label": None,},
    ] == response.json


def test_inspect_file_used_for_count(app_client_immutable_and_inspect_file):
    response = app_client_immutable_and_inspect_file.get("/fixtures/sortable.json")
    assert response.json["filtered_table_rows_count"] == 100
