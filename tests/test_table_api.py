from datasette.utils import detect_json1
from datasette.utils.sqlite import sqlite_version
from .fixtures import (  # noqa
    app_client,
    app_client_with_trace,
    app_client_returned_rows_matches_page_size,
    generate_compound_rows,
    generate_sortable_rows,
    make_app_client,
)
import json
import pytest
import urllib


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
        {"id": "5", "content": "RENDER_CELL_ASYNC"},
    ]


def test_table_not_exists_json(app_client):
    assert {
        "ok": False,
        "error": "Table not found",
        "status": 404,
        "title": None,
    } == app_client.get("/fixtures/blah.json").json


def test_table_shape_arrays(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_shape=arrays")
    assert [
        ["1", "hello"],
        ["2", "world"],
        ["3", ""],
        ["4", "RENDER_CELL_DEMO"],
        ["5", "RENDER_CELL_ASYNC"],
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
    assert [
        "hello",
        "world",
        "",
        "RENDER_CELL_DEMO",
        "RENDER_CELL_ASYNC",
    ] == response.json


def test_table_shape_objects(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_shape=objects")
    assert [
        {"id": "1", "content": "hello"},
        {"id": "2", "content": "world"},
        {"id": "3", "content": ""},
        {"id": "4", "content": "RENDER_CELL_DEMO"},
        {"id": "5", "content": "RENDER_CELL_ASYNC"},
    ] == response.json["rows"]


def test_table_shape_array(app_client):
    response = app_client.get("/fixtures/simple_primary_key.json?_shape=array")
    assert [
        {"id": "1", "content": "hello"},
        {"id": "2", "content": "world"},
        {"id": "3", "content": ""},
        {"id": "4", "content": "RENDER_CELL_DEMO"},
        {"id": "5", "content": "RENDER_CELL_ASYNC"},
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
        {"id": "5", "content": "RENDER_CELL_ASYNC"},
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
        "5": {"id": "5", "content": "RENDER_CELL_ASYNC"},
    } == response.json


def test_table_shape_object_compound_primary_key(app_client):
    response = app_client.get("/fixtures/compound_primary_key.json?_shape=object")
    assert response.json == {
        "a,b": {"pk1": "a", "pk2": "b", "content": "c"},
        "a~2Fb,~2Ec-d": {"pk1": "a/b", "pk2": ".c-d", "content": "c"},
    }


def test_table_with_slashes_in_name(app_client):
    response = app_client.get(
        "/fixtures/table~2Fwith~2Fslashes~2Ecsv.json?_shape=objects"
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
    """For _size=0 we return the counts, empty rows and no continuation token"""
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
                (
                    -row["sortable_with_nulls"]
                    if row["sortable_with_nulls"] is not None
                    else 0
                ),
                row["content"],
            ),
            "sorted by sortable_with_nulls descending",
        ),
        # text column contains '$null' - ensure it doesn't confuse pagination:
        ("_sort=text", lambda row: row["text"], "sorted by text"),
        # Still works if sort column removed using _col=
        ("_sort=text&_col=content", lambda row: row["text"], "sorted by text"),
    ],
)
def test_sortable(app_client, query_string, sort_key, human_description_en):
    path = f"/fixtures/sortable.json?_shape=objects&{query_string}"
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
    assert page == 5
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
        response = app_client.get(f"/fixtures/sortable.json?_sort={column}")
        assert f"Cannot sort table by {column}" == response.json["error"]


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
            # _searchmode=raw combined with _search_COLUMN
            "/fixtures/searchable.json?_search_text2=te*&_searchmode=raw",
            [
                [1, "barry cat", "terry dog", "panther"],
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


_SEARCHMODE_RAW_RESULTS = [
    [1, "barry cat", "terry dog", "panther"],
    [2, "terry dog", "sara weasel", "puma"],
]


@pytest.mark.parametrize(
    "table_metadata,querystring,expected_rows",
    [
        (
            {},
            "_search=te*+AND+do*",
            [],
        ),
        (
            {"searchmode": "raw"},
            "_search=te*+AND+do*",
            _SEARCHMODE_RAW_RESULTS,
        ),
        (
            {},
            "_search=te*+AND+do*&_searchmode=raw",
            _SEARCHMODE_RAW_RESULTS,
        ),
        # Can be over-ridden with _searchmode=escaped
        (
            {"searchmode": "raw"},
            "_search=te*+AND+do*&_searchmode=escaped",
            [],
        ),
    ],
)
def test_searchmode(table_metadata, querystring, expected_rows):
    with make_app_client(
        metadata={"databases": {"fixtures": {"tables": {"searchable": table_metadata}}}}
    ) as client:
        response = client.get("/fixtures/searchable.json?" + querystring)
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
            [
                ["1", "hello"],
                ["2", "world"],
                ["4", "RENDER_CELL_DEMO"],
            ],
        ),
        ("/fixtures/simple_primary_key.json?content__exact=", [["3", ""]]),
        (
            "/fixtures/simple_primary_key.json?content__not=world",
            [
                ["1", "hello"],
                ["3", ""],
                ["4", "RENDER_CELL_DEMO"],
                ["5", "RENDER_CELL_ASYNC"],
            ],
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
    assert [
        ["3", ""],
        ["4", "RENDER_CELL_DEMO"],
        ["5", "RENDER_CELL_ASYNC"],
    ] == response.json["rows"]


@pytest.mark.skipif(not detect_json1(), reason="Requires the SQLite json1 module")
def test_table_filter_json_arraycontains(app_client):
    response = app_client.get("/fixtures/facetable.json?tags__arraycontains=tag1")
    assert response.json["rows"] == [
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
            "n1",
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
            "n2",
        ],
    ]


@pytest.mark.skipif(not detect_json1(), reason="Requires the SQLite json1 module")
def test_table_filter_json_arraynotcontains(app_client):
    response = app_client.get(
        "/fixtures/facetable.json?tags__arraynotcontains=tag3&tags__not=[]"
    )
    assert response.json["rows"] == [
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
            "n1",
        ]
    ]


def test_table_filter_extra_where(app_client):
    response = app_client.get(
        "/fixtures/facetable.json?_where=_neighborhood='Dogpatch'"
    )
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
            "n2",
        ]
    ] == response.json["rows"]


def test_table_filter_extra_where_invalid(app_client):
    response = app_client.get("/fixtures/facetable.json?_where=_neighborhood=Dogpatch'")
    assert 400 == response.status
    assert "Invalid SQL" == response.json["title"]


def test_table_filter_extra_where_disabled_if_no_sql_allowed():
    with make_app_client(metadata={"allow_sql": {}}) as client:
        response = client.get(
            "/fixtures/facetable.json?_where=_neighborhood='Dogpatch'"
        )
        assert 403 == response.status
        assert "_where= is not allowed" == response.json["error"]


def test_table_through(app_client):
    # Just the museums:
    response = app_client.get(
        '/fixtures/roadside_attractions.json?_through={"table":"roadside_attraction_characteristics","column":"characteristic_id","value":"1"}'
    )
    assert response.json["rows"] == [
        [
            3,
            "Burlingame Museum of PEZ Memorabilia",
            "214 California Drive, Burlingame, CA 94010",
            None,
            37.5793,
            -122.3442,
        ],
        [
            4,
            "Bigfoot Discovery Museum",
            "5497 Highway 9, Felton, CA 95018",
            "https://www.bigfootdiscoveryproject.com/",
            37.0414,
            -122.0725,
        ],
    ]

    assert (
        response.json["human_description_en"]
        == 'where roadside_attraction_characteristics.characteristic_id = "1"'
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
        {"upper_content": "RENDER_CELL_ASYNC", "content": "RENDER_CELL_ASYNC"},
    ]


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
            "/fixtures/facetable.json?_facet=state&_facet=_city_id",
            {
                "state": {
                    "name": "state",
                    "hideable": True,
                    "type": "column",
                    "toggle_url": "/fixtures/facetable.json?_facet=_city_id",
                    "results": [
                        {
                            "value": "CA",
                            "label": "CA",
                            "count": 10,
                            "toggle_url": "_facet=state&_facet=_city_id&state=CA",
                            "selected": False,
                        },
                        {
                            "value": "MI",
                            "label": "MI",
                            "count": 4,
                            "toggle_url": "_facet=state&_facet=_city_id&state=MI",
                            "selected": False,
                        },
                        {
                            "value": "MC",
                            "label": "MC",
                            "count": 1,
                            "toggle_url": "_facet=state&_facet=_city_id&state=MC",
                            "selected": False,
                        },
                    ],
                    "truncated": False,
                },
                "_city_id": {
                    "name": "_city_id",
                    "hideable": True,
                    "type": "column",
                    "toggle_url": "/fixtures/facetable.json?_facet=state",
                    "results": [
                        {
                            "value": 1,
                            "label": "San Francisco",
                            "count": 6,
                            "toggle_url": "_facet=state&_facet=_city_id&_city_id__exact=1",
                            "selected": False,
                        },
                        {
                            "value": 2,
                            "label": "Los Angeles",
                            "count": 4,
                            "toggle_url": "_facet=state&_facet=_city_id&_city_id__exact=2",
                            "selected": False,
                        },
                        {
                            "value": 3,
                            "label": "Detroit",
                            "count": 4,
                            "toggle_url": "_facet=state&_facet=_city_id&_city_id__exact=3",
                            "selected": False,
                        },
                        {
                            "value": 4,
                            "label": "Memnonia",
                            "count": 1,
                            "toggle_url": "_facet=state&_facet=_city_id&_city_id__exact=4",
                            "selected": False,
                        },
                    ],
                    "truncated": False,
                },
            },
        ),
        (
            "/fixtures/facetable.json?_facet=state&_facet=_city_id&state=MI",
            {
                "state": {
                    "name": "state",
                    "hideable": True,
                    "type": "column",
                    "toggle_url": "/fixtures/facetable.json?_facet=_city_id&state=MI",
                    "results": [
                        {
                            "value": "MI",
                            "label": "MI",
                            "count": 4,
                            "selected": True,
                            "toggle_url": "_facet=state&_facet=_city_id",
                        }
                    ],
                    "truncated": False,
                },
                "_city_id": {
                    "name": "_city_id",
                    "hideable": True,
                    "type": "column",
                    "toggle_url": "/fixtures/facetable.json?_facet=state&state=MI",
                    "results": [
                        {
                            "value": 3,
                            "label": "Detroit",
                            "count": 4,
                            "selected": False,
                            "toggle_url": "_facet=state&_facet=_city_id&state=MI&_city_id__exact=3",
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
        {"name": "_city_id", "querystring": "_facet=_city_id"},
        {"name": "_neighborhood", "querystring": "_facet=_neighborhood"},
        {"name": "tags", "querystring": "_facet=tags"},
        {"name": "complex_array", "querystring": "_facet=complex_array"},
        {"name": "created", "querystring": "_facet_date=created"},
    ]
    if detect_json1():
        expected.append({"name": "tags", "querystring": "_facet_array=tags"})
    assert expected == suggestions


def test_allow_facet_off():
    with make_app_client(settings={"allow_facet": False}) as client:
        assert 400 == client.get("/fixtures/facetable.json?_facet=planet_int").status
        # Should not suggest any facets either:
        assert [] == client.get("/fixtures/facetable.json").json["suggested_facets"]


def test_suggest_facets_off():
    with make_app_client(settings={"suggest_facets": False}) as client:
        # Now suggested_facets should be []
        assert [] == client.get("/fixtures/facetable.json").json["suggested_facets"]


@pytest.mark.parametrize("nofacet", (True, False))
def test_nofacet(app_client, nofacet):
    path = "/fixtures/facetable.json?_facet=state"
    if nofacet:
        path += "&_nofacet=1"
    response = app_client.get(path)
    if nofacet:
        assert response.json["suggested_facets"] == []
        assert response.json["facet_results"] == {}
    else:
        assert response.json["suggested_facets"] != []
        assert response.json["facet_results"] != {}


@pytest.mark.parametrize("nosuggest", (True, False))
def test_nosuggest(app_client, nosuggest):
    path = "/fixtures/facetable.json?_facet=state"
    if nosuggest:
        path += "&_nosuggest=1"
    response = app_client.get(path)
    if nosuggest:
        assert response.json["suggested_facets"] == []
        # But facets should still be returned:
        assert response.json["facet_results"] != {}
    else:
        assert response.json["suggested_facets"] != []
        assert response.json["facet_results"] != {}


@pytest.mark.parametrize("nocount,expected_count", ((True, None), (False, 15)))
def test_nocount(app_client, nocount, expected_count):
    path = "/fixtures/facetable.json"
    if nocount:
        path += "?_nocount=1"
    response = app_client.get(path)
    assert response.json["filtered_table_rows_count"] == expected_count


def test_nocount_nofacet_if_shape_is_object(app_client_with_trace):
    response = app_client_with_trace.get(
        "/fixtures/facetable.json?_trace=1&_shape=object"
    )
    assert "count(*)" not in response.text


def test_expand_labels(app_client):
    response = app_client.get(
        "/fixtures/facetable.json?_shape=object&_labels=1&_size=2"
        "&_neighborhood__contains=c"
    )
    assert {
        "2": {
            "pk": 2,
            "created": "2019-01-14 08:00:00",
            "planet_int": 1,
            "on_earth": 1,
            "state": "CA",
            "_city_id": {"value": 1, "label": "San Francisco"},
            "_neighborhood": "Dogpatch",
            "tags": '["tag1", "tag3"]',
            "complex_array": "[]",
            "distinct_some_null": "two",
            "n": "n2",
        },
        "13": {
            "pk": 13,
            "created": "2019-01-17 08:00:00",
            "planet_int": 1,
            "on_earth": 1,
            "state": "MI",
            "_city_id": {"value": 3, "label": "Detroit"},
            "_neighborhood": "Corktown",
            "tags": "[]",
            "complex_array": "[]",
            "distinct_some_null": None,
            "n": None,
        },
    } == response.json


def test_expand_label(app_client):
    response = app_client.get(
        "/fixtures/foreign_key_references.json?_shape=object"
        "&_label=foreign_key_with_label&_size=1"
    )
    assert response.json == {
        "1": {
            "pk": "1",
            "foreign_key_with_label": {"value": "1", "label": "hello"},
            "foreign_key_with_blank_label": "3",
            "foreign_key_with_no_label": "1",
            "foreign_key_compound_pk1": "a",
            "foreign_key_compound_pk2": "b",
        }
    }


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
    # /fixtures/𝐜𝐢𝐭𝐢𝐞𝐬.json
    response = app_client.get(
        "/fixtures/~F0~9D~90~9C~F0~9D~90~A2~F0~9D~90~AD~F0~9D~90~A2~F0~9D~90~9E~F0~9D~90~AC.json?_shape=array"
    )
    assert [{"id": 1, "name": "San Francisco"}] == response.json


def test_null_and_compound_foreign_keys_are_not_expanded(app_client):
    response = app_client.get(
        "/fixtures/foreign_key_references.json?_shape=array&_labels=on"
    )
    assert response.json == [
        {
            "pk": "1",
            "foreign_key_with_label": {"value": "1", "label": "hello"},
            "foreign_key_with_blank_label": {"value": "3", "label": ""},
            "foreign_key_with_no_label": {"value": "1", "label": "1"},
            "foreign_key_compound_pk1": "a",
            "foreign_key_compound_pk2": "b",
        },
        {
            "pk": "2",
            "foreign_key_with_label": None,
            "foreign_key_with_blank_label": None,
            "foreign_key_with_no_label": None,
            "foreign_key_compound_pk1": None,
            "foreign_key_compound_pk2": None,
        },
    ]


@pytest.mark.parametrize(
    "path,expected_json,expected_text",
    [
        (
            "/fixtures/binary_data.json?_shape=array",
            [
                {"rowid": 1, "data": {"$base64": True, "encoded": "FRwCx60F/g=="}},
                {"rowid": 2, "data": {"$base64": True, "encoded": "FRwDx60F/g=="}},
                {"rowid": 3, "data": None},
            ],
            None,
        ),
        (
            "/fixtures/binary_data.json?_shape=array&_nl=on",
            None,
            (
                '{"rowid": 1, "data": {"$base64": true, "encoded": "FRwCx60F/g=="}}\n'
                '{"rowid": 2, "data": {"$base64": true, "encoded": "FRwDx60F/g=="}}\n'
                '{"rowid": 3, "data": null}'
            ),
        ),
    ],
)
def test_binary_data_in_json(app_client, path, expected_json, expected_text):
    response = app_client.get(path)
    if expected_json:
        assert response.json == expected_json
    else:
        assert response.text == expected_text


@pytest.mark.parametrize(
    "qs",
    [
        "",
        "?_shape=arrays",
        "?_shape=arrayfirst",
        "?_shape=object",
        "?_shape=objects",
        "?_shape=array",
        "?_shape=array&_nl=on",
    ],
)
def test_paginate_using_link_header(app_client, qs):
    path = f"/fixtures/compound_three_primary_keys.json{qs}"
    num_pages = 0
    while path:
        response = app_client.get(path)
        assert response.status == 200
        num_pages += 1
        link = response.headers.get("link")
        if link:
            assert link.startswith("<")
            assert link.endswith('>; rel="next"')
            path = link[1:].split(">")[0]
            path = path.replace("http://localhost", "")
        else:
            path = None
    assert num_pages == 21


@pytest.mark.skipif(
    sqlite_version() < (3, 31, 0),
    reason="generated columns were added in SQLite 3.31.0",
)
def test_generated_columns_are_visible_in_datasette():
    with make_app_client(
        extra_databases={
            "generated.db": """
                CREATE TABLE generated_columns (
                    body TEXT,
                    id INT GENERATED ALWAYS AS (json_extract(body, '$.number')) STORED,
                    consideration INT GENERATED ALWAYS AS (json_extract(body, '$.string')) STORED
                );
                INSERT INTO generated_columns (body) VALUES (
                    '{"number": 1, "string": "This is a string"}'
                );"""
        }
    ) as client:
        response = client.get("/generated/generated_columns.json?_shape=array")
        assert response.json == [
            {
                "rowid": 1,
                "body": '{"number": 1, "string": "This is a string"}',
                "id": 1,
                "consideration": "This is a string",
            }
        ]


@pytest.mark.parametrize(
    "path,expected_columns",
    (
        ("/fixtures/facetable.json?_col=created", ["pk", "created"]),
        (
            "/fixtures/facetable.json?_nocol=created",
            [
                "pk",
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
        ),
        (
            "/fixtures/facetable.json?_col=state&_col=created",
            ["pk", "state", "created"],
        ),
        (
            "/fixtures/facetable.json?_col=state&_col=state",
            ["pk", "state"],
        ),
        (
            "/fixtures/facetable.json?_col=state&_col=created&_nocol=created",
            ["pk", "state"],
        ),
        (
            # Ensure faceting doesn't break, https://github.com/simonw/datasette/issues/1345
            "/fixtures/facetable.json?_nocol=state&_facet=state",
            [
                "pk",
                "created",
                "planet_int",
                "on_earth",
                "_city_id",
                "_neighborhood",
                "tags",
                "complex_array",
                "distinct_some_null",
                "n",
            ],
        ),
        (
            "/fixtures/simple_view.json?_nocol=content",
            ["upper_content"],
        ),
        ("/fixtures/simple_view.json?_col=content", ["content"]),
    ),
)
def test_col_nocol(app_client, path, expected_columns):
    response = app_client.get(path)
    assert response.status == 200
    columns = response.json["columns"]
    assert columns == expected_columns


@pytest.mark.parametrize(
    "path,expected_error",
    (
        ("/fixtures/facetable.json?_col=bad", "_col=bad - invalid columns"),
        ("/fixtures/facetable.json?_nocol=bad", "_nocol=bad - invalid columns"),
        ("/fixtures/facetable.json?_nocol=pk", "_nocol=pk - invalid columns"),
        ("/fixtures/simple_view.json?_col=bad", "_col=bad - invalid columns"),
    ),
)
def test_col_nocol_errors(app_client, path, expected_error):
    response = app_client.get(path)
    assert response.status == 400
    assert response.json["error"] == expected_error
