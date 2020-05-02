from datasette.filters import Filters
import pytest


@pytest.mark.parametrize(
    "args,expected_where,expected_params",
    [
        ((("name_english__contains", "foo"),), ['"name_english" like :p0'], ["%foo%"]),
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
        ((("foo__notlike", "2%2"),), ['"foo" not like :p0'], ["2%2"],),
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
    ],
)
def test_build_where(args, expected_where, expected_params):
    f = Filters(sorted(args))
    sql_bits, actual_params = f.build_where_clauses("table")
    assert expected_where == sql_bits
    assert {
        "p{}".format(i): param for i, param in enumerate(expected_params)
    } == actual_params
