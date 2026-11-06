"""
Throughput of the incremental typed inserter.

- insert_rows() on a plain connection, committing per batch, for several
  batch sizes, into (i) a named shared-cache in-memory database opened the
  way Datasette opens ``memory_name`` databases and (ii) an on-disk WAL file
- import_rows() through a real Datasette Database (one execute_write_fn()
  per batch), same targets
- baselines: parsing alone, sqlite-utils insert_all() (all TEXT, one
  transaction), sqlite-utils insert_all() + TypeTracker + transform() (what
  ``sqlite-utils insert --csv`` does), and a raw executemany()
- the cost of type detection itself: dict -> list conversion vs
  conversion + per-column type checks, without any SQLite work

    uv run python prototypes/data_csv/bench_inserter.py \\
        --file /path/to/events-100k.csv --work /tmp/work

    # one big run through import_rows() + Datasette, printing peak RSS:
    uv run python prototypes/data_csv/bench_inserter.py \\
        --file /path/to/big-1gb.csv --work /tmp/work --single disk:1000:async
"""

import argparse
import asyncio
import os
import resource
import sqlite3
import statistics
import sys
import time
import uuid

import sqlite_utils
from sqlite_utils.utils import Format, TypeTracker, rows_from_file

from datasette.app import Datasette
from datasette.database import Database
from datasette.imports.inserter import (
    TypedInserter,
    import_rows,
    insert_rows,
    iter_chunks,
)


def read_rows(path):
    "Rows from rows_from_file() plus the file to close afterwards."
    fp = open(path, "rb")  # noqa: SIM115
    fmt = Format.NL if path.endswith(".ndjson") else None
    rows, _ = rows_from_file(fp, format=fmt)
    return rows, fp


def target(storage, work):
    """(connection, cleanup) for a fresh database."""
    if storage == "memory":
        # As datasette/database.py connects to memory_name databases
        uri = f"file:{uuid.uuid4().hex}?mode=memory&cache=shared"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        keepalive = sqlite3.connect(uri, uri=True, check_same_thread=False)

        def cleanup():
            conn.close()
            keepalive.close()

        return conn, cleanup
    path = os.path.join(work, f"bench-{uuid.uuid4().hex[:8]}.db")
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    def cleanup():
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)

    return conn, cleanup


def best_of(repeat, fn, *args):
    times = []
    result = None
    for _ in range(repeat):
        result, seconds = fn(*args)
        times.append(seconds)
    return result, min(times), statistics.median(times)


def run_parse_only(path):
    rows, fp = read_rows(path)
    start = time.perf_counter()
    count = sum(1 for _ in rows)
    elapsed = time.perf_counter() - start
    fp.close()
    return count, elapsed


def run_insert_rows(path, storage, work, batch_size, widen="auto"):
    conn, cleanup = target(storage, work)
    conn.isolation_level = None
    rows, fp = read_rows(path)
    start = time.perf_counter()
    result = insert_rows(conn, "t", rows, batch_size=batch_size, widen=widen)
    elapsed = time.perf_counter() - start
    fp.close()
    cleanup()
    return result, elapsed


def run_import_rows(path, storage, work, batch_size):
    async def go():
        ds = Datasette()
        if storage == "memory":
            db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
        else:
            db_path = os.path.join(work, f"ds-{uuid.uuid4().hex[:8]}.db")
            sqlite3.connect(db_path).execute("PRAGMA journal_mode=WAL").close()
            db = ds.add_database(Database(ds, path=db_path, is_mutable=True))
        rows, fp = read_rows(path)
        start = time.perf_counter()
        result = await import_rows(db, "t", rows, batch_size=batch_size)
        elapsed = time.perf_counter() - start
        fp.close()
        ds.close()
        if storage != "memory":
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db_path + suffix):
                    os.remove(db_path + suffix)
        return result, elapsed

    return asyncio.run(go())


def run_sqlite_utils(path, storage, work, detect):
    conn, cleanup = target(storage, work)
    db = sqlite_utils.Database(conn)
    rows, fp = read_rows(path)
    start = time.perf_counter()
    if detect:
        tracker = TypeTracker()
        rows = tracker.wrap(rows)
    db.table("t").insert_all(rows)
    if detect:
        db.table("t").transform(types=tracker.types)
    elapsed = time.perf_counter() - start
    fp.close()
    count = conn.execute("select count(*) from t").fetchone()[0]
    cleanup()
    return count, elapsed


def run_executemany(path, storage, work):
    "Lower bound: no type checks, all values bound as they come."
    conn, cleanup = target(storage, work)
    conn.isolation_level = None
    rows, fp = read_rows(path)
    rows = iter(rows)
    first = next(rows)
    keys = list(first)
    conn.execute("create table t ({})".format(", ".join(f'"{k}"' for k in keys)))
    sql = "insert into t values ({})".format(", ".join("?" * len(keys)))
    start = time.perf_counter()
    conn.execute("BEGIN")
    conn.execute(sql, list(first.values()))
    conn.executemany(sql, (list(map(row.get, keys)) for row in rows))
    conn.execute("COMMIT")
    elapsed = time.perf_counter() - start
    fp.close()
    cleanup()
    return None, elapsed


def run_detection_only(path, batch_size):
    """
    Time the Python side of the inserter without SQLite: (a) turning dicts
    into value lists, (b) that plus type checks and conversions.
    """
    rows, fp = read_rows(path)
    chunks = list(iter_chunks(rows, 1000, batch_size))
    fp.close()
    conn = sqlite3.connect(":memory:")
    # (a) dict -> list only
    inserter = TypedInserter("a")
    inserter.write(conn, chunks[0])
    start = time.perf_counter()
    for chunk in chunks[1:]:
        inserter._row_values(chunk)
    to_lists = time.perf_counter() - start
    # (b) dict -> list + scan (type checks and value conversion)
    inserter = TypedInserter("b")
    inserter.write(conn, chunks[0])
    start = time.perf_counter()
    for chunk in chunks[1:]:
        values = inserter._row_values(chunk)
        changes = inserter._scan(values)
        if changes:
            # widen the (tiny) table so later chunks are checked correctly
            inserter._widen(conn, changes)
    with_scan = time.perf_counter() - start
    count = sum(len(c) for c in chunks[1:])
    return count, to_lists, with_scan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument(
        "--batch-sizes", type=int, nargs="+", default=[100, 1000, 10000]
    )
    parser.add_argument("--storage", nargs="+", default=["memory", "disk"])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--single",
        help="storage:batch_size[:async] - run insert_rows (or import_rows"
        " through Datasette with :async) once and print the result",
    )
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    print(f"SQLite {sqlite3.sqlite_version}, Python {sys.version.split()[0]}")
    print(f"File: {args.file} ({os.path.getsize(args.file) / 1e6:.1f} MB)")

    if args.single:
        storage, batch_size, *mode = args.single.split(":")
        fn = run_import_rows if mode == ["async"] else run_insert_rows
        result, elapsed = fn(args.file, storage, args.work, int(batch_size))
        print(
            f"{fn.__name__} {storage} batch_size={batch_size}: {result.rows:,} rows"
            f" in {elapsed:.1f}s = {result.rows / elapsed:,.0f} rows/s"
            f" ({result.batches:,} committed batches)"
        )
        print("types:", result.types)
        for widening in result.widenings:
            print("  ", widening)
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"Peak RSS: {peak:,.0f} MB")
        return

    count, parse = run_parse_only(args.file)
    print(f"\nRows: {count:,}")
    print(f"Parse only (rows_from_file): {parse:.2f}s = {count / parse:,.0f} rows/s")

    print("\n| what | storage | batch size | best | median | rows/s (best) |")
    print("|---|---|---|---|---|---|")

    def report(label, storage, batch, best, median):
        print(
            f"| {label} | {storage} | {batch} | {best:.2f}s | {median:.2f}s |"
            f" {count / best:,.0f} |",
            flush=True,
        )

    for storage in args.storage:
        for batch_size in args.batch_sizes:
            _, best, median = best_of(
                args.repeat,
                run_insert_rows,
                args.file,
                storage,
                args.work,
                batch_size,
            )
            report("insert_rows (commit per batch)", storage, batch_size, best, median)
        for batch_size in args.batch_sizes:
            _, best, median = best_of(
                args.repeat,
                run_import_rows,
                args.file,
                storage,
                args.work,
                batch_size,
            )
            report(
                "import_rows via Datasette execute_write_fn",
                storage,
                batch_size,
                best,
                median,
            )
        _, best, median = best_of(
            args.repeat, run_sqlite_utils, args.file, storage, args.work, False
        )
        report("sqlite-utils insert_all, all TEXT, 1 txn", storage, "-", best, median)
        _, best, median = best_of(
            args.repeat, run_sqlite_utils, args.file, storage, args.work, True
        )
        report(
            "sqlite-utils insert_all + TypeTracker + transform",
            storage,
            "-",
            best,
            median,
        )
        _, best, median = best_of(
            args.repeat, run_executemany, args.file, storage, args.work
        )
        report("raw executemany, no types, 1 txn", storage, "-", best, median)

    print("\nType detection cost (Python only, no SQLite), rows after the sample:")
    for batch_size in args.batch_sizes:
        results = [
            run_detection_only(args.file, batch_size) for _ in range(args.repeat)
        ]
        rows = results[0][0]
        to_lists = min(r[1] for r in results)
        with_scan = min(r[2] for r in results)
        print(
            f"- batch {batch_size}: dict->list {to_lists:.3f}s;"
            f" dict->list + type checks/conversion {with_scan:.3f}s;"
            f" type checks alone {with_scan - to_lists:.3f}s"
            f" = {(with_scan - to_lists) / rows * 1e6:.2f} us/row"
        )


if __name__ == "__main__":
    main()
