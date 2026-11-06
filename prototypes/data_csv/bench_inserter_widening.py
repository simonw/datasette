"""
How expensive is widening a column, depending on how it is done?

Part 1 - cost of one widening on a table that already holds N rows of
big-1gb.csv. The inserter's methods are timed by calling its own _widen()
on the table, so the SQL is exactly what an import runs:

- "schema": edit the declared type in place (PRAGMA writable_schema);
  integer -> float is a metadata change only, widening to text also runs an
  UPDATE converting the stored numbers back to their text
- "rebuild": CREATE new / INSERT SELECT / DROP / RENAME
- sqlite-utils ``table.transform(types=...)`` (a full rebuild) for comparison

for integer -> float (late_float), integer -> text (late_text) and float ->
text (price; about 1% of its values, like "553.0", have their original text
kept, which the conversion looks up - transform() turns them into "553.0"
by luck and "1.10" into "1.1"). Each method runs on a fresh copy of the same
table, for a named shared-cache in-memory database opened the way Datasette
opens ``memory_name`` databases and for an on-disk WAL database. Times
include the COMMIT.

Part 2 - end to end: insert_rows() over N rows of big-1gb.csv in which qty
turns float at 90% of the rows, id turns text at 95% and price turns text
at 97%, once per widening strategy.

    uv run python prototypes/data_csv/bench_inserter_widening.py \\
        --csv /path/to/big-1gb.csv --rows 100000 1000000 --work /tmp/work
"""

import argparse
import csv
import itertools
import os
import resource
import sqlite3
import sys
import time
import uuid

import sqlite_utils

from datasette.imports.inserter import (
    _F,
    _I,
    _T,
    TypedInserter,
    _canonical,
    insert_rows,
)

COLUMNS = (
    ("id", "INTEGER"),
    ("name", "TEXT"),
    ("city", "TEXT"),
    ("qty", "INTEGER"),
    ("price", "REAL"),
    ("created", "TEXT"),
    ("late_float", "INTEGER"),
    ("late_text", "INTEGER"),
    ("maybe_empty", "INTEGER"),
    ("notes", "TEXT"),
)
RANKS = {"INTEGER": _I, "REAL": _F, "TEXT": _T}


def open_db(storage, work):
    """Return (writer, keepalive, path) for the given storage."""
    if storage == "memory":
        # Exactly how datasette/database.py connects to memory_name databases
        uri = f"file:{uuid.uuid4().hex}?mode=memory&cache=shared"
        writer = sqlite3.connect(
            uri, uri=True, check_same_thread=False, isolation_level="IMMEDIATE"
        )
        reader = sqlite3.connect(uri, uri=True, check_same_thread=False)
        reader.execute("PRAGMA query_only=1")
        return writer, reader, None
    path = os.path.join(work, f"widen-{uuid.uuid4().hex[:8]}.db")
    writer = sqlite3.connect(path, isolation_level="IMMEDIATE")
    writer.execute("PRAGMA journal_mode=WAL")
    return writer, None, path


def build_table(conn, csv_path, rows):
    """
    Load N rows as the inserter would have stored them. Returns the load
    time and the price values that are not canonical - the ones whose text
    the inserter would have kept - as (rowid, text).
    """
    conn.execute(
        "CREATE TABLE t ({})".format(", ".join(f'"{n}" {t}' for n, t in COLUMNS))
    )
    numeric = [i for i, (_, t) in enumerate(COLUMNS) if t != "TEXT"]
    price = [n for n, _ in COLUMNS].index("price")
    kept = []
    with open(csv_path, newline="") as fp:
        reader = csv.reader(fp)
        next(reader)

        def values():
            for rowid, row in enumerate(itertools.islice(reader, rows), 1):
                for i in numeric:
                    if row[i] == "":
                        row[i] = None
                if row[price] is not None and not _canonical(row[price], _F):
                    kept.append((rowid, row[price]))
                yield row

        start = time.perf_counter()
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "INSERT INTO t VALUES ({})".format(", ".join("?" * len(COLUMNS))),
            values(),
        )
        conn.execute("COMMIT")
    return time.perf_counter() - start, kept


def adopting_inserter(conn, widen, kept):
    """
    A TypedInserter that believes it created table t (as build_table() made
    it), with the kept price texts in its spellings table.
    """
    inserter = TypedInserter("t", widen=widen)
    for name, sql_type in COLUMNS:
        column = inserter._new_column(name)
        column.rank = column.declared = RANKS[sql_type]
    inserter.created = True
    inserter._refresh(conn)
    price = inserter._by_key["price"]
    conn.execute("BEGIN IMMEDIATE")
    inserter._ensure_spellings_table(conn)
    conn.executemany(
        f"INSERT INTO {inserter._spellings_table} (col, rid, val) VALUES (?, ?, ?)",
        [(price.index, rowid, text) for rowid, text in kept],
    )
    conn.execute("COMMIT")
    price.spellings = len(kept)
    return inserter


def inserter_method(widen, changes):
    def setup(conn, kept):
        return adopting_inserter(conn, widen, kept)

    def run(conn, inserter):
        inserter._widen(conn, changes)
        return [w.method for w in inserter.widenings]

    return setup, run


def transform_method(types):
    def setup(conn, kept):
        return None

    def run(conn, state):
        sqlite_utils.Database(conn).table("t").transform(types=types)

    return setup, run


METHODS = (
    ("inserter schema edit int->float", inserter_method("schema", {"late_float": _F})),
    ("inserter rebuild int->float", inserter_method("rebuild", {"late_float": _F})),
    ("sqlite-utils transform int->float", transform_method({"late_float": float})),
    (
        "inserter schema edit+UPDATE int->text",
        inserter_method("schema", {"late_text": _T}),
    ),
    ("inserter rebuild int->text", inserter_method("rebuild", {"late_text": _T})),
    ("sqlite-utils transform int->text", transform_method({"late_text": str})),
    (
        "inserter schema edit+UPDATE float->text",
        inserter_method("schema", {"price": _T}),
    ),
    ("inserter rebuild float->text", inserter_method("rebuild", {"price": _T})),
    ("sqlite-utils transform float->text", transform_method({"price": str})),
)


def part1(csv_path, row_counts, storages, work):
    print("## Part 1: cost of one widening on a table of N rows (incl. COMMIT)")
    print(
        "| rows | storage | load N rows | "
        + " | ".join(label for label, _ in METHODS)
        + " |"
    )
    print("|---|---|---|" + "---|" * len(METHODS))
    for rows in row_counts:
        template_path = os.path.join(work, f"template-{uuid.uuid4().hex[:8]}.db")
        template = sqlite3.connect(template_path, isolation_level="IMMEDIATE")
        template.execute("PRAGMA journal_mode=WAL")
        load, kept = build_table(template, csv_path, rows)
        for storage in storages:
            results = []
            for label, (setup, run) in METHODS:
                conn, keepalive, path = open_db(storage, work)
                template.backup(conn)
                if storage == "disk":
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                state = setup(conn, kept)
                start = time.perf_counter()
                conn.execute("BEGIN IMMEDIATE")
                methods = run(conn, state)
                conn.execute("COMMIT")
                results.append(time.perf_counter() - start)
                if methods and "fallback" in methods[0]:
                    print(f"<!-- {label}: {methods} -->")
                check = conn.execute("PRAGMA quick_check").fetchone()[0]
                assert check == "ok", (label, check)
                if "inserter" in label and "float->text" in label and kept:
                    # The kept text came back
                    rowid, text = kept[0]
                    got = conn.execute(
                        "select price from t where rowid = ?", [rowid]
                    ).fetchone()[0]
                    assert got == text, (label, got, text)
                conn.close()
                if keepalive is not None:
                    keepalive.close()
                if path:
                    remove_db(path)
            print(
                "| {:,} | {} | {:.2f}s | {} |".format(
                    rows,
                    storage,
                    load,
                    " | ".join(
                        f"{r * 1000:.2f}ms" if r < 0.1 else f"{r:.2f}s" for r in results
                    ),
                ),
                flush=True,
            )
        print(f"<!-- {rows:,} rows: {len(kept):,} price texts kept -->")
        template.close()
        remove_db(template_path)


def remove_db(path):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def late_rows(csv_path, rows):
    """
    rows of big-1gb.csv; qty turns float at 90%, id turns text at 95% and
    price turns text at 97%.
    """
    float_at = int(rows * 0.9)
    text_at = int(rows * 0.95)
    price_at = int(rows * 0.97)
    with open(csv_path, newline="") as fp:
        for i, row in enumerate(itertools.islice(csv.DictReader(fp), rows)):
            if i == float_at:
                row["qty"] = row["qty"] + ".5"
            if i == text_at:
                row["id"] = "N/A"
            if i == price_at:
                row["price"] = "N/A"
            yield row


def part2(csv_path, row_counts, storages, work, batch_size):
    print()
    print(
        "## Part 2: whole import with late widenings (qty -> float at 90%,"
        f" id -> text at 95%, price -> text at 97%; batch_size={batch_size})"
    )
    print(
        "| rows | storage | strategy | total | time spent widening | widenings"
        " | texts kept |"
    )
    print("|---|---|---|---|---|---|---|")
    for rows in row_counts:
        for storage in storages:
            outputs = {}
            for widen in ("auto", "schema", "rebuild", "deferred"):
                conn, keepalive, path = open_db(storage, work)
                conn.isolation_level = None
                result = insert_rows(
                    conn,
                    "t",
                    late_rows(csv_path, rows),
                    widen=widen,
                    batch_size=batch_size,
                )
                if widen == "deferred":
                    # One rebuild in finish() covers every widening
                    widen_seconds = result.widenings[0].seconds
                else:
                    # Columns widened in the same batch share one timing
                    widen_seconds = sum(
                        {w.at_row: w.seconds for w in result.widenings}.values()
                    )
                print(
                    "| {:,} | {} | {} | {:.2f}s | {:.2f}s | {} | {:,} |".format(
                        rows,
                        storage,
                        widen,
                        result.seconds,
                        widen_seconds,
                        ", ".join(
                            f"{w.column} {w.from_type}->{w.to_type} @{w.at_row:,}"
                            for w in result.widenings
                        ),
                        result.spellings_kept,
                    ),
                    flush=True,
                )
                outputs[widen] = conn.execute(
                    "select id, qty, price, typeof(id), typeof(qty), typeof(price)"
                    " from t where rowid in (1, ?, ?, ?, ?) or price like '%.0'"
                    " order by rowid limit 50",
                    [
                        int(rows * 0.9) + 1,
                        int(rows * 0.95) + 1,
                        int(rows * 0.97) + 1,
                        rows,
                    ],
                ).fetchall()
                conn.close()
                if keepalive is not None:
                    keepalive.close()
                if path:
                    remove_db(path)
            same = len({repr(v) for v in outputs.values()}) == 1
            print(f"<!-- strategies produced identical sample rows: {same} -->")


def deferred_demo():
    print()
    print("## Values that only fit the wider type, per strategy")
    for widen in ("auto", "schema", "rebuild", "deferred"):
        conn = sqlite3.connect(":memory:")
        inserter = TypedInserter("t", widen=widen)
        inserter.write(conn, [{"zip": "10001", "big": "1", "price": "1.5"}])
        inserter.write(
            conn, [{"zip": "02134", "big": "9223372036854775809", "price": "1.50"}]
        )
        inserter.write(conn, [{"zip": "x", "big": "x", "price": "x"}])
        inserter.finish(conn)
        print(
            f"- {widen}: ",
            conn.execute("select zip, big, price from t where rowid = 2").fetchone(),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--rows", type=int, nargs="+", default=[100_000, 1_000_000])
    parser.add_argument("--part2-rows", type=int, nargs="+", default=None)
    parser.add_argument("--storage", nargs="+", default=["memory", "disk"])
    parser.add_argument("--work", required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--skip-part1", action="store_true")
    parser.add_argument("--skip-part2", action="store_true")
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    print(f"SQLite {sqlite3.sqlite_version}, Python {sys.version.split()[0]}")
    if not args.skip_part1:
        part1(args.csv, args.rows, args.storage, args.work)
    if not args.skip_part2:
        part2(
            args.csv,
            args.part2_rows or args.rows,
            args.storage,
            args.work,
            args.batch_size,
        )
    deferred_demo()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"\nPeak RSS: {peak:,.0f} MB")


if __name__ == "__main__":
    main()
