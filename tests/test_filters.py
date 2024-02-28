from datasette.filters import Filters, through_filters, where_filters, search_filters
from datasette.utils.asgi import Request
import pytest


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
            [":p0 in (select value from json_each([table].[Availability+Info]))"],
            ["yes"],
        ),
        (
            (("Availability+Info__arraynotcontains", "yes"),),
            [":p0 not in (select value from json_each([table].[Availability+Info]))"],
            ["yes"],
        ),
    ],
)
def test_build_where(args, expected_where, expected_params):
    f = Filters(sorted(args))
    sql_bits, actual_params = f.build_where_clauses("table")
    assert expected_where == sql_bits
    assert {f"p{i}": param for i, param in enumerate(expected_params)} == actual_params


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
