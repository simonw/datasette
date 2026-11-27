import pytest

from datasette.filters import (
    Filters,
    column_type_has_no_affinity,
    looks_numeric,
    search_filters,
    through_filters,
    where_filters,
)
from datasette.utils.asgi import BadRequest, Request


@pytest.mark.parametrize(
    "args,expected_where,expected_params",
    [
        ((("name_english__contains", "foo"),), ['"name_english" like :p0'], ["%foo%"]),
        (
            (("name_english__notcontains", "foo"),),
            ['"name_english" not like :p0'],
            ["%foo%"],
        ),
        (
            (("foo", "bar"), ("bar__contains", "baz")),
            ['"bar" like :p0', '"foo" = :p1'],
            ["%baz%", "bar"],
        ),
        (
            (("foo__startswith", "bar"), ("bar__endswith", "baz")),
            ['"bar" like :p0', '"foo" like :p1'],
            ["%baz", "bar%"],
        ),
        (
            (("foo__lt", "1"), ("bar__gt", "2"), ("baz__gte", "3"), ("bax__lte", "4")),
            ['"bar" > :p0', '"bax" <= :p1', '"baz" >= :p2', '"foo" < :p3'],
            [2, 4, 3, 1],
        ),
        (
            (("foo__like", "2%2"), ("zax__glob", "3*")),
            ['"foo" like :p0', '"zax" glob :p1'],
            ["2%2", "3*"],
        ),
        # Multiple like arguments:
        (
            (("foo__like", "2%2"), ("foo__like", "3%3")),
            ['"foo" like :p0', '"foo" like :p1'],
            ["2%2", "3%3"],
        ),
        # notlike:
        (
            (("foo__notlike", "2%2"),),
            ['"foo" not like :p0'],
            ["2%2"],
        ),
        (
            (("foo__isnull", "1"), ("baz__isnull", "1"), ("bar__gt", "10")),
            ['"bar" > :p0', '"baz" is null', '"foo" is null'],
            [10],
        ),
        ((("foo__in", "1,2,3"),), ["foo in (:p0, :p1, :p2)"], ["1", "2", "3"]),
        # date
        ((("foo__date", "1988-01-01"),), ['date("foo") = :p0'], ["1988-01-01"]),
        # month and year
        (
            (("foo__month", "1988-01"),),
            ["strftime('%Y-%m', \"foo\") = :p0"],
            ["1988-01"],
        ),
        ((("foo__year", "1988"),), ["strftime('%Y', \"foo\") = :p0"], ["1988"]),
        # JSON array variants of __in (useful for unexpected characters)
        ((("foo__in", "[1,2,3]"),), ["foo in (:p0, :p1, :p2)"], [1, 2, 3]),
        (
            (("foo__in", '["dog,cat", "cat[dog]"]'),),
            ["foo in (:p0, :p1)"],
            ["dog,cat", "cat[dog]"],
        ),
        # Not in, and JSON array not in
        ((("foo__notin", "1,2,3"),), ["foo not in (:p0, :p1, :p2)"], ["1", "2", "3"]),
        ((("foo__notin", "[1,2,3]"),), ["foo not in (:p0, :p1, :p2)"], [1, 2, 3]),
        # JSON arraycontains, arraynotcontains
        (
            (("Availability+Info__arraycontains", "yes"),),
            [':p0 in (select value from json_each("table"."Availability+Info"))'],
            ["yes"],
        ),
        (
            (("Availability+Info__arraynotcontains", "yes"),),
            [':p0 not in (select value from json_each("table"."Availability+Info"))'],
            ["yes"],
        ),
    ],
)
def test_build_where(args, expected_where, expected_params):
    f = Filters(sorted(args))
    sql_bits, actual_params = f.build_where_clauses("table")
    assert sql_bits == expected_where
    assert {f"p{i}": param for i, param in enumerate(expected_params)} == actual_params


@pytest.mark.parametrize(
    "key,expected_where",
    (
        (
            'has"quote__exact',
            '"has""quote" = :p0',
        ),
        (
            'has"quote__isnull',
            '"has""quote" is null',
        ),
        (
            "has]bracket__arraycontains",
            ':p0 in (select value from json_each("table"."has]bracket"))',
        ),
    ),
)
def test_build_where_escapes_column_names(key, expected_where):
    filters = Filters(((key, "value"),))
    sql_bits, _ = filters.build_where_clauses("table")
    assert sql_bits == [expected_where]


def test_build_where_escapes_table_name():
    filters = Filters((("tags__arraycontains", "value"),))
    sql_bits, _ = filters.build_where_clauses("items]bracket")
    assert sql_bits == [':p0 in (select value from json_each("items]bracket"."tags"))']


async def test_through_filters_from_request(ds_client):
    request = Request.fake(
        '/?_through={"table":"roadside_attraction_characteristics","column":"characteristic_id","value":"1"}'
    )
    filter_args = await through_filters(
        request=request,
        datasette=ds_client.ds,
        table="roadside_attractions",
        database="fixtures",
    )()
    assert filter_args.where_clauses == [
        "pk in (select attraction_id from roadside_attraction_characteristics where characteristic_id = :p0)"
    ]
    assert filter_args.params == {"p0": "1"}
    assert filter_args.human_descriptions == [
        'roadside_attraction_characteristics.characteristic_id = "1"'
    ]
    assert filter_args.extra_context == {}


async def test_where_filters_from_request(ds_client):
    await ds_client.ds.invoke_startup()
    request = Request.fake("/?_where=pk+>+3")
    filter_args = await where_filters(
        request=request,
        datasette=ds_client.ds,
        database="fixtures",
    )()
    assert filter_args.where_clauses == ["pk > 3"]
    assert filter_args.params == {}
    assert filter_args.human_descriptions == []
    assert filter_args.extra_context == {
        "extra_wheres_for_ui": [{"text": "pk > 3", "remove_url": "/"}]
    }


async def test_search_filters_from_request(ds_client):
    request = Request.fake("/?_search=bobcat")
    filter_args = await search_filters(
        request=request,
        datasette=ds_client.ds,
        database="fixtures",
        table="searchable",
    )()
    assert filter_args.where_clauses == [
        "rowid in (select rowid from searchable_fts where searchable_fts match escape_fts(:search))"
    ]
    assert filter_args.params == {"search": "bobcat"}
    assert filter_args.human_descriptions == ['search matches "bobcat"']
    assert filter_args.extra_context == {"supports_search": True, "search": "bobcat"}


@pytest.mark.parametrize("lookup", ("in", "notin"))
@pytest.mark.parametrize(
    "value",
    (
        "[",
        '["a"',
        "[[1]]",
        '[{"a": 1}]',
        "[99999999999999999999]",
        '["\\ud800"]',
    ),
)
def test_in_filter_invalid_json_array_is_bad_request(lookup, value):
    filters = Filters(((f"foo__{lookup}", value),))
    with pytest.raises(BadRequest):
        filters.build_where_clauses("table")


@pytest.mark.parametrize(
    "args,expected_where,expected_params",
    [
        # Numeric-looking values are compared as text and as numbers
        (
            (("n", "1"),),
            ['"n" in (:p0, cast(:p0 as numeric))'],
            ["1"],
        ),
        (
            (("n__not", "1.5"),),
            ['"n" not in (:p0, cast(:p0 as numeric))'],
            ["1.5"],
        ),
        (
            (("n__in", "1,dog,-2.5"),),
            ["n in (:p0, cast(:p0 as numeric), :p1, :p2, cast(:p2 as numeric))"],
            ["1", "dog", "-2.5"],
        ),
        (
            (("n__notin", "1"),),
            ["n not in (:p0, cast(:p0 as numeric))"],
            ["1"],
        ),
        # Other values and other columns are left alone
        ((("n", "dog"),), ['"n" = :p0'], ["dog"]),
        ((("n", "0x10"),), ['"n" = :p0'], ["0x10"]),
        ((("n", "1_000"),), ['"n" = :p0'], ["1_000"]),
        ((("n", "nan"),), ['"n" = :p0'], ["nan"]),
        ((("other", "1"),), ['"other" = :p0'], ["1"]),
        ((("n__contains", "1"),), ['"n" like :p0'], ["%1%"]),
        ((("n__gt", "1"),), ['"n" > :p0'], [1]),
    ],
)
def test_build_where_no_affinity_columns(args, expected_where, expected_params):
    f = Filters(sorted(args))
    sql_bits, actual_params = f.build_where_clauses("table", no_affinity_columns={"n"})
    assert expected_where == sql_bits
    assert {f"p{i}": param for i, param in enumerate(expected_params)} == actual_params


@pytest.mark.parametrize(
    "column_type,expected",
    [
        ("", True),
        (None, True),
        ("BLOB", True),
        ("blob", True),
        ("INTEGER", False),
        ("TEXT", False),
        ("VARCHAR(10)", False),
        ("REAL", False),
        ("NUMERIC", False),
        ("DATETIME", False),
    ],
)
def test_column_type_has_no_affinity(column_type, expected):
    assert column_type_has_no_affinity(column_type) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("5", True),
        ("-5", True),
        ("+5", True),
        ("2.5", True),
        (".5", True),
        ("5.", True),
        ("1e3", True),
        ("1.5E-3", True),
        ("", False),
        ("abc", False),
        ("5 ", False),
        ("5\n", False),
        ("0x10", False),
        ("1_000", False),
        ("nan", False),
        ("inf", False),
        (5, False),
        (None, False),
    ],
)
def test_looks_numeric(value, expected):
    assert looks_numeric(value) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qs,expected_status",
    (
        ("state__in=[", 400),
        ("state__notin=[[1]]", 400),
        ("pk__in=[9223372036854775808]", 400),
        ("pk__in=[9223372036854775807, -9223372036854775808]", 200),
    ),
)
async def test_in_filter_json_array_status(ds_client, qs, expected_status):
    response = await ds_client.get("/fixtures/facetable.json?" + qs)
    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qs,expected_states",
    [
        # The facetable_by_state view in the fixtures database has three
        # columns calculated by aggregate functions, which have no type
        # affinity: row_count, planet_total and avg_on_earth.
        # Refs #1671, #2073, #2713
        ("row_count=4", ["MI"]),
        ("row_count__exact=10", ["CA"]),
        ("row_count__not=4", ["CA", "MC"]),
        ("row_count__in=1,4", ["MC", "MI"]),
        ('row_count__in=["1","4"]', ["MC", "MI"]),
        ("row_count__in=[1,4]", ["MC", "MI"]),
        ("row_count__notin=10", ["MC", "MI"]),
        ("row_count__notin=1,4", ["CA"]),
        ("planet_total=2", ["MC"]),
        ("planet_total=4&row_count=4", ["MI"]),
        # Integers and floating point numbers match each other
        ("avg_on_earth=1", ["CA", "MI"]),
        ("avg_on_earth=1.0", ["CA", "MI"]),
        ("avg_on_earth=0", ["MC"]),
        ("avg_on_earth__not=1", ["MC"]),
        ("row_count=4.0", ["MI"]),
        # A value that is not a number matches nothing
        ("row_count=four", []),
        # These already worked, because the value is converted in Python
        ("row_count__gt=3", ["CA", "MI"]),
        ("row_count__lte=4", ["MC", "MI"]),
        # state is passed straight through the view and keeps its affinity
        ("state=MI", ["MI"]),
    ],
)
async def test_filters_against_view_columns_with_no_affinity(
    ds_client, qs, expected_states
):
    response = await ds_client.get(
        "/fixtures/facetable_by_state.json?_sort=state&" + qs
    )
    assert response.status_code == 200
    assert [row["state"] for row in response.json()["rows"]] == expected_states


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "column,expected_facet_values",
    [
        ("row_count", [(1, 1), (4, 1), (10, 1)]),
        ("planet_total", [(2, 1), (4, 1), (10, 1)]),
        ("avg_on_earth", [(0.0, 1), (1.0, 2)]),
    ],
)
async def test_facet_links_work_for_view_expression_columns(
    ds_client, column, expected_facet_values
):
    # https://github.com/simonw/datasette/issues/2073
    response = await ds_client.get(f"/fixtures/facetable_by_state.json?_facet={column}")
    facet_values = response.json()["facet_results"]["results"][column]["results"]
    assert (
        sorted((v["value"], v["count"]) for v in facet_values) == expected_facet_values
    )
    for facet_value in facet_values:
        path = facet_value["toggle_url"].replace("http://localhost", "")
        filtered = (await ds_client.get(path)).json()
        assert len(filtered["rows"]) == facet_value["count"]
        selected = filtered["facet_results"]["results"][column]["results"]
        assert [
            v["selected"] for v in selected if v["value"] == facet_value["value"]
        ] == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_ids",
    [
        # Cases the fixtures database has no data for. Expression columns
        # in a view can hold numbers that are not whole
        ("/data/v.json?score_calc=1.5", [1, 2]),
        # ... or text
        ("/data/v.json?n_text=1", [2]),
        ("/data/v.json?n_text=0", [1, 3]),
        ("/data/v.json?label=dog", [3]),
        # Text that looks like a different spelling of the same number
        # is not matched: '1.0' and '01' are not '1'
        ("/data/v.json?code_calc=1", [1]),
        ("/data/v.json?code_calc=1.0", [2]),
        ("/data/v.json?code_calc=01", [3]),
        ("/data/v.json?code_calc__not=1", [2, 3]),
        # Table columns declared without a type
        ("/data/t.json?untyped=1", [2]),
        ("/data/t.json?untyped=dog", [3]),
        ("/data/t.json?untyped__not=1", [1, 3]),
        ("/data/t.json?untyped_text=1", [1]),
        # Table columns with a declared type are not affected
        ("/data/t.json?n=1", [2]),
        ("/data/t.json?code=1", [1]),
    ],
)
async def test_filters_against_untyped_table_columns_and_text(path, expected_ids):
    from datasette.app import Datasette

    ds = Datasette()
    db = ds.add_memory_database("data")
    await db.execute_write_script("""
        create table t (
            id integer primary key, n integer, score real, code text, untyped,
            untyped_text
        );
        insert into t values (1, 0, 1.5, '1', 0, '1');
        insert into t values (2, 1, 1.5, '2', 1, '1.0');
        insert into t values (3, 0, 2.0, '3', 'dog', '01');
        create view v as select
            id,
            score * 1 as score_calc,
            '' || n as n_text,
            '' || untyped_text as code_calc,
            case when id = 3 then 'dog' else n end as label
        from t;
    """)
    await ds.invoke_startup()
    response = await ds.client.get(path)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["rows"]] == expected_ids
