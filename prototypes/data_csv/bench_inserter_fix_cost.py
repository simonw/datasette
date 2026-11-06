"""
What does keeping original text (so widening to text is lossless) cost?

Runs insert_rows() from two versions of datasette/imports/inserter.py on the
same pre-parsed rows, alternating between them, so CSV parsing and machine
noise affect both alike. Get the version before the fix with:

    git show 3965dda:datasette/imports/inserter.py > /tmp/work/inserter_old.py
    uv run python prototypes/data_csv/bench_inserter_fix_cost.py \\
        --old-module /tmp/work/inserter_old.py \\
        --file /path/to/events-100k.csv --work /tmp/work

Targets as in bench_inserter.py: a named shared-cache in-memory database
opened the way Datasette opens ``memory_name`` databases, and an on-disk
WAL file. Each run commits per batch.
"""

import argparse
import importlib.util
import os
import sqlite3
import time
import uuid

from sqlite_utils.utils import Format, rows_from_file

from datasette.imports import inserter as new_module


def load_module(path):
    spec = importlib.util.spec_from_file_location("inserter_old", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_all(path):
    with open(path, "rb") as fp:
        fmt = Format.NL if path.endswith(".ndjson") else None
        rows, _ = rows_from_file(fp, format=fmt)
        return list(rows)


def connect(storage, work):
    if storage == "memory":
        uri = f"file:{uuid.uuid4().hex}?mode=memory&cache=shared"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        keepalive = sqlite3.connect(uri, uri=True)
        return conn, lambda: (conn.close(), keepalive.close())
    path = os.path.join(work, f"fixcost-{uuid.uuid4().hex[:8]}.db")
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")

    def cleanup():
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)

    return conn, cleanup


def run(module, rows, storage, work, batch_size):
    # insert_rows() changes nothing in the row dicts, so they can be reused
    conn, cleanup = connect(storage, work)
    start = time.perf_counter()
    result = module.insert_rows(conn, "t", rows, batch_size=batch_size)
    elapsed = time.perf_counter() - start
    content = conn.execute("select count(*), total(rowid) from t").fetchone()
    cleanup()
    return elapsed, result, content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-module", required=True)
    parser.add_argument("--file", required=True, nargs="+")
    parser.add_argument("--work", required=True)
    parser.add_argument(
        "--batch-sizes", type=int, nargs="+", default=[100, 1000, 10000]
    )
    parser.add_argument("--storage", nargs="+", default=["memory", "disk"])
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    old_module = load_module(args.old_module)
    print(f"SQLite {sqlite3.sqlite_version}")
    print("| file | storage | batch | before fix (best) | after fix (best) | change |")
    print("|---|---|---|---|---|---|")
    for path in args.file:
        rows = read_all(path)
        for storage in args.storage:
            for batch_size in args.batch_sizes:
                best = {"old": float("inf"), "new": float("inf")}
                kept = None
                for _ in range(args.repeat):
                    for label, module in (("old", old_module), ("new", new_module)):
                        elapsed, result, _ = run(
                            module, rows, storage, args.work, batch_size
                        )
                        best[label] = min(best[label], elapsed)
                        if label == "new":
                            kept = result.spellings_kept
                count = len(rows)
                print(
                    "| {} | {} | {} | {:.3f}s = {:,.0f} rows/s | {:.3f}s = {:,.0f}"
                    " rows/s | {:+.1%} time | ".format(
                        os.path.basename(path),
                        storage,
                        batch_size,
                        best["old"],
                        count / best["old"],
                        best["new"],
                        count / best["new"],
                        best["new"] / best["old"] - 1,
                    )
                    + f"{kept:,} spellings kept",
                    flush=True,
                )


if __name__ == "__main__":
    main()
