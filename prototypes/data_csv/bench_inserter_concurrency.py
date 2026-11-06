"""
What happens to people querying a table while import_rows() fills it?

Runs import_rows() into a Datasette database while reader coroutines run
queries against the table in a loop through db.execute() (Datasette's
read thread pool). Counts successful reads and errors by message, and
whether the import itself failed, for each batch size and widening
strategy, for a named shared-cache in-memory database (how ``scratch``
would be opened) and an on-disk WAL database.

    uv run python prototypes/data_csv/bench_inserter_concurrency.py \\
        --file /path/to/events-100k.csv --work /tmp/work
"""

import argparse
import asyncio
import collections
import os
import sqlite3
import sys
import time
import uuid

from sqlite_utils.utils import rows_from_file

from datasette.app import Datasette
from datasette.database import Database
from datasette.imports.inserter import import_rows

QUERIES = (
    "select count(*) from t",
    "select * from t order by rowid desc limit 20",
    "select city, count(*) from t group by city",
)


async def one_run(path, storage, work, batch_size, widen, readers):
    ds = Datasette()
    db_path = None
    if storage == "memory":
        db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    else:
        db_path = os.path.join(work, f"conc-{uuid.uuid4().hex[:8]}.db")
        sqlite3.connect(db_path).execute("PRAGMA journal_mode=WAL").close()
        db = ds.add_database(Database(ds, path=db_path, is_mutable=True))
    outcomes = collections.Counter()
    done = asyncio.Event()

    async def reader(index):
        i = index
        while not done.is_set():
            sql = QUERIES[i % len(QUERIES)]
            i += 1
            try:
                await db.execute(sql)
                outcomes["ok"] += 1
            except Exception as ex:  # noqa: BLE001
                message = str(ex)
                if "no such table" in message:
                    outcomes["no such table (before create)"] += 1
                else:
                    outcomes[f"{type(ex).__name__}: {message}"] += 1
            await asyncio.sleep(0)

    tasks = [asyncio.ensure_future(reader(i)) for i in range(readers)]
    fp = open(path, "rb")  # noqa: SIM115, ASYNC230
    rows, _ = rows_from_file(fp)
    start = time.perf_counter()
    import_error = None
    try:
        result = await import_rows(db, "t", rows, batch_size=batch_size, widen=widen)
        rows_done = result.rows
    except Exception as ex:  # noqa: BLE001
        import_error = f"{type(ex).__name__}: {ex}"
        rows_done = None
    elapsed = time.perf_counter() - start
    done.set()
    await asyncio.gather(*tasks)
    fp.close()
    ds.close()
    if db_path:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                os.remove(db_path + suffix)
    return elapsed, rows_done, import_error, outcomes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument(
        "--batch-sizes", type=int, nargs="+", default=[100, 1000, 10000]
    )
    parser.add_argument("--storage", nargs="+", default=["memory", "disk"])
    parser.add_argument("--widen", nargs="+", default=["schema", "rebuild"])
    parser.add_argument("--readers", type=int, default=3)
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    print(f"SQLite {sqlite3.sqlite_version}, Python {sys.version.split()[0]}")
    print(f"{args.readers} reader coroutines looping over: {QUERIES}")
    print()
    print("| storage | batch | widen | import | rows | reads ok | read errors |")
    print("|---|---|---|---|---|---|---|")
    for storage in args.storage:
        for batch_size in args.batch_sizes:
            for widen in args.widen:
                elapsed, rows, import_error, outcomes = asyncio.run(
                    one_run(
                        args.file,
                        storage,
                        args.work,
                        batch_size,
                        widen,
                        args.readers,
                    )
                )
                ok = outcomes.pop("ok", 0)
                outcomes.pop("no such table (before create)", None)
                errors = "; ".join(f"{n} x {m}" for m, n in outcomes.most_common())
                print(
                    "| {} | {} | {} | {} | {} | {:,} | {} |".format(
                        storage,
                        batch_size,
                        widen,
                        f"FAILED {import_error}" if import_error else f"{elapsed:.1f}s",
                        f"{rows:,}" if rows is not None else "-",
                        ok,
                        errors or "none",
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    main()
