import sqlite3

import pytest

from datasette.filters import Filters


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("42", 42),
        ("-3", -3),
        ("0.1", 0.1),
        ("-2.5", -2.5),
        ("1e3", 1000.0),
    ),
)
def test_numeric_comparison_filter_converts_numbers(value, expected):
    sql_bits, params = Filters([("score__gt", value)]).build_where_clauses("scores")

    assert sql_bits == ['"score" > :p0']
    assert params == {"p0": expected}


def test_numeric_comparison_filter_calculated_view():
    conn = sqlite3.connect(":memory:")
    conn.execute("create table searchable(pk integer)")
    conn.executemany("insert into searchable(pk) values (?)", [(0,), (1,), (2,)])
    conn.execute(
        "create view calculated as "
        "select pk + 1 as pk_plus_one, pk / 2.0 as score from searchable"
    )

    sql_bits, params = Filters([("score__gt", "0.1")]).build_where_clauses(
        "calculated"
    )
    rows = conn.execute(
        "select score from calculated where {}".format(" and ".join(sql_bits)), params
    ).fetchall()

    assert rows == [(0.5,), (1.0,)]
