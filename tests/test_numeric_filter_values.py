import sqlite3
from contextlib import closing

import pytest

from datasette.filters import Filters


@pytest.mark.parametrize(
    "value,expected",
    (
        ("3.5", 3.5),
        ("-2", -2),
        ("-2.5", -2.5),
        (str(2**63 - 1), 2**63 - 1),
        (str(-(2**63)), -(2**63)),
        (str(2**63), float(2**63)),
        (str(-(2**63) - 1), float(-(2**63) - 1)),
        ("1" + "0" * 400, "1" + "0" * 400),
        ("1e3", 1000.0),
        ("not-a-number", "not-a-number"),
        ("nan", "nan"),
        ("inf", "inf"),
        ("-inf", "-inf"),
    ),
)
def test_numeric_filter_parameters(value, expected):
    filters = Filters((("score__gt", value),))
    sql_bits, params = filters.build_where_clauses("items")

    assert sql_bits == ['"score" > :p0']
    assert params == {"p0": expected}


def test_numeric_filter_parameters_against_calculated_view():
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("create table searchable(pk integer)")
        conn.executemany("insert into searchable(pk) values (?)", [(0,), (1,), (2,)])
        conn.execute(
            "create view calculated as "
            "select pk + 1 as pk_plus_one, pk / 2.0 as score from searchable"
        )

        sql_bits, params = Filters((("score__gt", "0.1"),)).build_where_clauses(
            "calculated"
        )
        rows = conn.execute(
            "select score from calculated where {}".format(" and ".join(sql_bits)),
            params,
        ).fetchall()

        assert rows == [(0.5,), (1.0,)]


@pytest.mark.parametrize(
    "lookup,value,expected",
    (
        ("gt", str(2**63), [(1e20,)]),
        ("lt", str(-(2**63) - 1), [(-1e20,)]),
    ),
)
def test_large_numeric_filter_parameters_against_calculated_view(
    lookup, value, expected
):
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("create table numbers(score real)")
        conn.executemany("insert into numbers values (?)", [(-1e20,), (0.0,), (1e20,)])
        conn.execute(
            "create view calculated as select score + 0.0 as score from numbers"
        )
        sql_bits, params = Filters(((f"score__{lookup}", value),)).build_where_clauses(
            "calculated"
        )
        rows = conn.execute(
            "select score from calculated where {}".format(" and ".join(sql_bits)),
            params,
        ).fetchall()

        assert rows == expected
