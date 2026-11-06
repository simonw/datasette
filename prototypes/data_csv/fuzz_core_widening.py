"""
Fuzz incremental widening and positional rows harder than
fuzz_inserter_boundaries.py does.

For random rows full of awkward values (tests/test_imports_inserter.py's
_tricky_rows), each seed imports the rows with TypedInserter, committing
every write() and convert() call, with random sample, batch and
conversion-slice sizes (1-5 rows, so the backlog of stored numbers
still to become text lasts across many writes and later widenings), as
dicts or as positional rows whose column list grows the way the worker
protocol's does, and with SQLITE_DBCONFIG_DEFENSIVE on or off (on, every
widening falls back to a rebuild). Then:

- after every committed transaction the table must be readable: row count,
  PRAGMA integrity_check, declared types matching the inserter's, and every
  value in a TEXT column already final (what the whole-input reference
  has) unless that row is still in the column's backlog - in which case it
  is a number or NULL that, with its kept spelling if there is one, turns
  into the final text;
- the finished table must be exactly the reference: the whole input as the
  sample, as dicts.

    uv run python prototypes/data_csv/fuzz_core_widening.py --seeds 2000
"""

import argparse
import collections
import os
import random
import sqlite3
import sys
import time

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "tests")
)
from test_imports_inserter import _snapshot, _tricky_rows, declared, load

from datasette.imports import inserter as inserter_module
from datasette.imports.inserter import (
    _RENDER_FUNCTION,
    TypedInserter,
    _quote,
    _render_sql,
)

DEFENSIVE = getattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE", None)
# What the runs exercised
STATS = collections.Counter()


def positional(rows, rng):
    """
    Yield (chunk_rows, columns) the way a worker parent would see them: the
    column list only grows, sometimes ahead of the rows in this chunk, and
    rows drop their trailing Nones.
    """
    order = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                order.append(key)
    known = 0
    lookahead = {}
    for i, row in enumerate(rows):
        # How many columns had appeared by this row
        for key in row:
            known = max(known, order.index(key) + 1)
        lookahead[i] = known
    return order, lookahead


def chunks(count, sample_size, batch_size):
    start, size = 0, sample_size
    while start < count:
        yield start, min(start + size, count)
        start += size
        size = batch_size


def check(conn, inserter, reference_rows, reference_types):
    table = _quote(inserter.table)
    if not inserter.created:
        return
    count = conn.execute(f"select count(*) from {table}").fetchone()[0]
    assert count == inserter.rows_written, (count, inserter.rows_written)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    types = declared(conn, inserter.table)
    sql_types = {"integer": "INTEGER", "float": "REAL", "text": "TEXT", "blob": "BLOB"}
    expected = {
        # A column whose stored numbers are still becoming text is BLOB
        column.name: (
            "BLOB" if column.index in inserter._backlog else sql_types[column.type]
        )
        for column in inserter._columns
    }
    assert types == expected, (types, expected)
    conn.create_function(_RENDER_FUNCTION, 1, _render_sql, deterministic=True)
    spellings = {}
    if inserter._spellings_created:
        for col, rid, val in conn.execute(
            f"select col, rid, val from {inserter._spellings_table}"
        ):
            spellings[(col, rid)] = val
    for column in inserter._columns:
        if column.type not in ("text", "blob"):
            continue
        name = _quote(column.name)
        backlog = inserter._backlog.get(column.index)
        position = list(reference_types).index(column.name)
        for rowid, value, kind in conn.execute(
            f"select rowid, {name}, typeof({name}) from {table} order by rowid"
        ):
            final = reference_rows[rowid - 1][position]
            if backlog and backlog[0] <= rowid <= backlog[1]:
                STATS["values checked in a backlog"] += 1
                assert kind in ("integer", "real", "null"), (column.name, rowid, kind)
                if (column.index, rowid) in spellings:
                    text = spellings[(column.index, rowid)]
                elif value is None:
                    text = "" if column.nulls == "blank" else None
                else:
                    text = _render_sql(value) if kind == "real" else str(value)
                assert text == final, (column.name, rowid, value, text, final)
            else:
                assert value == final, (column.name, rowid, value, final)


def run(seed):
    rng = random.Random(seed)
    rows = _tricky_rows(rng, rng.randint(1, 60))
    reference_conn, _ = load(rows, sample_size=len(rows))
    reference = _snapshot(reference_conn)
    reference_types = reference[0]
    reference_rows = [
        tuple(row[i] for i in range(0, len(row), 2)) for row in reference[1]
    ]
    defensive = DEFENSIVE is not None and rng.random() < 0.25
    as_positional = rng.random() < 0.5
    convert_rows = rng.randint(1, 5)
    sample_size = rng.randint(1, 8)
    batch_size = rng.randint(1, 6)
    conn = sqlite3.connect(":memory:", isolation_level=None)
    if defensive:
        conn.setconfig(DEFENSIVE, True)
    inserter = TypedInserter("t")
    inserter._convert_rows = convert_rows
    order, known = positional(rows, rng)

    def committed(fn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            fn()
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        check(conn, inserter, reference_rows, reference_types)

    for start, end in chunks(len(rows), sample_size, batch_size):
        chunk = rows[start:end]
        if as_positional:
            # Columns known by the end of this chunk, sometimes a few more
            ahead = min(len(rows) - 1, end - 1 + rng.randint(0, 3))
            columns = order[: known[ahead]]
            values = []
            for row in chunk:
                row_values = [row.get(key) for key in columns]
                while row_values and row_values[-1] is None and rng.random() < 0.8:
                    row_values.pop()
                values.append(row_values if rng.random() < 0.7 else tuple(row_values))
            committed(lambda v=values, c=columns: inserter.write(conn, v, c))
        else:
            committed(lambda chunk=chunk: inserter.write(conn, chunk))
    while inserter.backlog:
        committed(lambda: inserter.convert(conn))
    committed(lambda: inserter.finish(conn))
    result = _snapshot(conn)
    details = (defensive, as_positional, convert_rows, sample_size, batch_size)
    if result != reference:
        return details
    STATS["runs"] += 1
    STATS["positional runs"] += as_positional
    STATS["defensive runs"] += defensive
    methods = [w.method for w in inserter.widenings]
    STATS["incremental widenings"] += methods.count("incremental")
    STATS["fallback rebuilds"] += methods.count("rebuild")
    if defensive and inserter.widenings:
        assert set(methods) == {"rebuild"}, methods
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--seeds", type=int, default=2000)
    parser.add_argument("--floats", choices=("exact", "python"), default="exact")
    args = parser.parse_args()
    inserter_module._FLOATS_EXACT = args.floats == "exact"
    failures = 0
    start = time.perf_counter()
    for seed in range(args.start, args.start + args.seeds):
        try:
            mismatch = run(seed)
        except Exception as ex:  # noqa: BLE001 - report and carry on
            failures += 1
            print(f"seed {seed}: {ex!r}"[:2000])
            continue
        if mismatch is not None:
            failures += 1
            print(f"MISMATCH seed {seed}: {mismatch}")
    print(
        f"{args.seeds} seeds (floats: {args.floats}): {failures} failures,"
        f" {time.perf_counter() - start:.1f}s"
    )
    print(dict(STATS))


if __name__ == "__main__":
    main()
