import pytest

from datasette.filters import Filters


@pytest.mark.parametrize(
    "value,expected",
    (
        ("3.5", 3.5),
        ("-2", -2),
        ("-2.5", -2.5),
        ("1e3", 1000.0),
        ("not-a-number", "not-a-number"),
    ),
)
def test_numeric_filter_parameters(value, expected):
    filters = Filters((("score__gt", value),))
    sql_bits, params = filters.build_where_clauses("items")

    assert sql_bits == ['"score" > :p0']
    assert params == {"p0": expected}
