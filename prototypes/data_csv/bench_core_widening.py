"""
How long does a late widening to text hold SQLite's write lock, and what
does that do to everyone else? Before: the inserter rebuilt the table in
the transaction that widened (12s for 6M rows). After: the declared type
changes in place and the stored numbers are converted ~50ms at a time.

The input is the first ROWS rows of big-1gb.csv with three late widenings
injected: qty turns float at 90% of the rows (an in-place edit, both
before and after), id turns text at 95% and price (about 1% of whose
values have kept spellings, like "553.0") at 97%.

``direct``: drives TypedInserter on a WAL file, one BEGIN/COMMIT per
write() - and per convert() for the backlog left at the end - timing every
transaction including its COMMIT, while a thread writes one row to another
table of the same file every 100ms through a second connection with a 5s
busy timeout (Python's default, which Datasette's write connection also
uses), recording how long each waited and any "database is locked".

``datasette``: import_rows() into a Datasette Database (a WAL file), with,
at the same time: a reader running ``select * from t limit 10`` every 50ms
through db.execute(); a writer inserting a row into another table every
100ms through db.execute_write_fn(), the same queue as the import; the
direct writer above; and an event loop lag probe (10ms sleeps). Latencies
are reported for the whole import and for its last 6% (from 94% of the
rows, where both widenings to text happen, to the end).

Each run imports with the inserter as it was on proto/data-csv (BEFORE, a
copy of that inserter.py) and as it is now (AFTER), interleaved.

    git show proto/data-csv:datasette/imports/inserter.py > /tmp/inserter_before.py
    uv run python prototypes/data_csv/bench_core_widening.py direct \\
        --csv big-1gb.csv --rows 1000000 --before /tmp/inserter_before.py \\
        --work /tmp/work --repeat 2
    uv run python prototypes/data_csv/bench_core_widening.py datasette ...
"""

import argparse
import asyncio
import csv
import importlib.util
import json
import os
import sqlite3
import statistics
import threading
import time

from datasette.imports import inserter as after_module

QTY_FLOAT_AT, ID_TEXT_AT, PRICE_TEXT_AT = 0.90, 0.95, 0.97


def load_before(path):
    spec = importlib.util.spec_from_file_location("inserter_before", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows_from(csv_path, count):
    qty_at, id_at, price_at = (
        int(count * QTY_FLOAT_AT),
        int(count * ID_TEXT_AT),
        int(count * PRICE_TEXT_AT),
    )
    with open(csv_path, newline="") as fp:
        reader = csv.reader(fp)
        header = next(reader)
        for i, record in enumerate(reader):
            if i >= count:
                return
            row = dict(zip(header, record))
            if i == qty_at:
                row["qty"] = "1.5"
            if i == id_at:
                row["id"] = f"id-{i}"
            if i == price_at:
                row["price"] = "n/a"
            yield row


def chunks(rows, sample_size, batch_size):
    chunk, size = [], sample_size
    for row in rows:
        chunk.append(row)
        if len(chunk) >= size:
            yield chunk
            chunk, size = [], batch_size
    if chunk:
        yield chunk


def summary(values, unit=1000):
    "p50 / p99 / max in ms (values in seconds)."
    if not values:
        return {"n": 0}
    values = sorted(values)
    return {
        "n": len(values),
        "p50": round(statistics.median(values) * unit, 1),
        "p99": round(values[min(len(values) - 1, int(len(values) * 0.99))] * unit, 1),
        "max": round(values[-1] * unit, 1),
    }


class DirectWriter(threading.Thread):
    "Writes a row to another table every 100ms on a connection of its own."

    def __init__(self, path, interval=0.1):
        super().__init__(daemon=True)
        self.path = path
        self.interval = interval
        self.stop_event = threading.Event()
        self.waits = []  # (time, seconds)
        self.errors = []

    def run(self):
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.execute("create table if not exists direct_other (t real)")
        while not self.stop_event.wait(self.interval):
            start = time.perf_counter()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("insert into direct_other values (?)", [time.time()])
                conn.execute("COMMIT")
                self.waits.append((start, time.perf_counter() - start))
            except sqlite3.OperationalError as ex:
                self.errors.append((start, time.perf_counter() - start, str(ex)))
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
        conn.close()

    def stop(self):
        self.stop_event.set()
        self.join()


def new_db_path(work, label):
    path = os.path.join(work, f"widen-{label}-{os.getpid()}-{time.time_ns()}.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    return path


def remove(path):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


def run_direct(module, label, args):
    path = new_db_path(args.work, label)
    conn = sqlite3.connect(path, isolation_level=None)
    writer = DirectWriter(path)
    writer.start()
    inserter = module.TypedInserter("t")
    transactions = []
    start = time.perf_counter()

    def transaction(fn):
        began = time.perf_counter()
        conn.execute("BEGIN IMMEDIATE")
        fn()
        conn.execute("COMMIT")
        transactions.append(time.perf_counter() - began)

    for chunk in chunks(rows_from(args.csv, args.rows), 1000, args.batch_size):
        transaction(lambda chunk=chunk: inserter.write(conn, chunk))
    converts = 0
    while getattr(inserter, "backlog", 0):
        transaction(lambda: inserter.convert(conn))
        converts += 1
    transaction(lambda: inserter.finish(conn))
    total = time.perf_counter() - start
    writer.stop()
    check = conn.execute(
        "select typeof(id), typeof(price), count(*) from t group by 1, 2"
    )
    types = sorted(tuple(r) for r in check.fetchall())
    integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
    conn.close()
    remove(path)
    longest = sorted(transactions, reverse=True)[:3]
    return {
        "label": label,
        "rows": args.rows,
        "total_seconds": round(total, 2),
        "transactions": len(transactions),
        "converts_after_last_row": converts,
        "longest_transactions_ms": [round(t * 1000, 1) for t in longest],
        "over_200ms": sum(1 for t in transactions if t > 0.2),
        "widenings": [
            (w.column, w.to_type, w.method, round(w.seconds, 3))
            for w in inserter.widenings
        ],
        "convert_seconds": round(getattr(inserter, "convert_seconds", 0.0), 2),
        "direct_writer_wait_ms": summary([w for _, w in writer.waits]),
        "direct_writer_errors": len(writer.errors),
        "storage_classes": types,
        "quick_check": integrity,
    }


async def run_datasette(module, label, args):
    from datasette.app import Datasette
    from datasette.database import Database

    path = new_db_path(args.work, label)
    ds = Datasette()
    db = ds.add_database(Database(ds, path=path, is_mutable=True), name="bench")
    await db.execute_write("create table queued_other (t real)")
    writer = DirectWriter(path)
    marks = {}
    late_at = int(args.rows * 0.94)
    stop = asyncio.Event()
    reads, queued, lags = [], [], []
    write_fn_seconds = []

    class Timed:
        "Times each write function on the write thread."

        def __getattr__(self, name):
            return getattr(db, name)

        async def execute_write_fn(self, fn, *a, **kw):
            def timed(conn):
                began = time.perf_counter()
                try:
                    return fn(conn)
                finally:
                    write_fn_seconds.append(time.perf_counter() - began)

            return await db.execute_write_fn(timed, *a, **kw)

    async def reader():
        while not stop.is_set():
            began = time.perf_counter()
            try:
                await db.execute("select * from t limit 10")
                reads.append((began, time.perf_counter() - began))
            except Exception:  # noqa: BLE001, S110 - before the table exists
                pass
            await asyncio.sleep(0.05)

    async def queued_writer():
        while not stop.is_set():
            began = time.perf_counter()
            await db.execute_write_fn(
                lambda conn: conn.execute(
                    "insert into queued_other values (?)", [time.time()]
                )
            )
            queued.append((began, time.perf_counter() - began))
            await asyncio.sleep(0.1)

    async def lag_probe():
        while not stop.is_set():
            began = time.perf_counter()
            await asyncio.sleep(0.01)
            lags.append((began, time.perf_counter() - began - 0.01))

    def progress(written):
        if written >= late_at and "late" not in marks:
            marks["late"] = time.perf_counter()

    tasks = [asyncio.ensure_future(f()) for f in (reader, queued_writer, lag_probe)]
    writer.start()
    start = time.perf_counter()
    result = await module.import_rows(
        Timed(),
        "t",
        rows_from(args.csv, args.rows),
        sample_size=1000,
        batch_size=args.batch_size,
        progress=progress,
    )
    end = time.perf_counter()
    stop.set()
    await asyncio.gather(*tasks)
    writer.stop()
    late = marks.get("late", end)

    def split(samples):
        return {
            "all": summary([s for _, s in samples]),
            "late": summary([s for t, s in samples if late <= t <= end]),
        }

    db.close()
    ds.close()
    remove(path)
    return {
        "label": label,
        "rows": args.rows,
        "total_seconds": round(end - start, 2),
        "late_window_seconds": round(end - late, 2),
        "write_fns": len(write_fn_seconds),
        "longest_write_fns_ms": [
            round(t * 1000, 1) for t in sorted(write_fn_seconds, reverse=True)[:3]
        ],
        "write_fns_over_200ms": sum(1 for t in write_fn_seconds if t > 0.2),
        "widenings": [(w.column, w.to_type, w.method) for w in result.widenings],
        "reader_ms": split(reads),
        "queued_writer_ms": split(queued),
        "direct_writer_ms": split(writer.waits),
        "direct_writer_errors": len(writer.errors),
        "loop_lag_ms": split(lags),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("direct", "datasette"))
    parser.add_argument("--csv", required=True)
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--before", required=True, help="the old inserter.py")
    parser.add_argument("--work", required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--json", help="append results to this file")
    parser.add_argument("--only", choices=("before", "after"))
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    before = load_before(args.before)
    variants = [("before", before), ("after", after_module)]
    if args.only:
        variants = [v for v in variants if v[0] == args.only]
    for repeat in range(args.repeat):
        for label, module in variants:
            if args.mode == "direct":
                result = run_direct(module, label, args)
            else:
                result = asyncio.run(run_datasette(module, label, args))
            result["repeat"] = repeat
            print(json.dumps(result), flush=True)
            if args.json:
                with open(args.json, "a") as fp:
                    fp.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
