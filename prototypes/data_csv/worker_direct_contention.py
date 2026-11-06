"""
Does a worker writing straight into the database file (``--direct-db``)
hold up the server's own writes? A widening that rebuilds the table runs
inside one chunk's transaction, holding SQLite's write lock for as long as
the copy takes; any other connection writing to the same file waits up to
its busy timeout (5 seconds, Python's default, which Datasette's write
connection uses) and then fails with "database is locked".

    uv run python prototypes/data_csv/worker_direct_contention.py make \\
        --source big-1gb.csv --rows 6000000 --out late-6m.csv
    uv run python prototypes/data_csv/worker_direct_contention.py run \\
        --file late-6m.csv --mode direct      # or --mode parent

``make`` copies the first ROWS data rows of a CSV and appends one row whose
``qty`` is text, so the import widens qty integer -> text at the very end,
rebuilding a ROWS-row table.

``run --mode direct``: the worker inserts with insert_rows() on its own
connection while a thread in this process commits a one-row write to
another table of the same file every 100ms through a connection with a 5s
timeout, recording how long each waited and any errors.

``run --mode parent``: the same file through import_file_via_worker(),
with the other writes going through db.execute_write() - the same queue as
the import's chunks.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import threading
import time


def make(args):
    with open(args.source, "rb") as source, open(args.out, "wb") as out:
        header = source.readline()
        out.write(header)
        columns = header.decode().strip().split(",")
        out.writelines(source.readline() for _ in range(args.rows))
        row = ["1"] * len(columns)
        row[columns.index("qty")] = "not a number"
        out.write((",".join(row) + "\n").encode())
    print(
        f"wrote {args.out}: {args.rows + 1} rows, {os.path.getsize(args.out):,} bytes"
    )


def other_writer(path, stop, stats):
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    conn.execute("create table if not exists other (t real)")
    while not stop.is_set():
        start = time.perf_counter()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("insert into other values (?)", [time.time()])
            conn.execute("COMMIT")
            stats["waits"].append(time.perf_counter() - start)
        except sqlite3.OperationalError as ex:
            stats["errors"].append(f"{ex} after {time.perf_counter() - start:.2f}s")
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
        time.sleep(0.1)
    conn.close()


def summary(waits, errors):
    waits = sorted(waits)
    return {
        "writes": len(waits),
        "errors": len(errors),
        "first_errors": errors[:3],
        "wait_p50_ms": round(waits[len(waits) // 2] * 1000, 1) if waits else None,
        "wait_max_s": round(waits[-1], 3) if waits else None,
        "waits_over_1s": sum(1 for w in waits if w > 1),
    }


async def run_direct(db, path):
    from datasette.imports.subprocess_import import WorkerProcess, worker_command

    await db.execute_write("create table other (t real)")
    stop = threading.Event()
    stats = {"waits": [], "errors": []}
    thread = threading.Thread(target=other_writer, args=(db.path, stop, stats))
    thread.start()
    worker = WorkerProcess(
        worker_command(
            path,
            extra_args=["--direct-db", db.path, "--table", "t", "--batch-size", "1000"],
        )
    )
    start = time.perf_counter()
    await worker.start()
    final = None
    while True:
        messages = await worker.read_messages()
        if messages is None:
            break
        for kind, payload in messages:
            if kind in ("end", "error"):
                final = payload
    await worker.stop()
    seconds = time.perf_counter() - start
    stop.set()
    thread.join()
    count = (await db.execute("select count(*) from t")).single_value()
    return {
        "mode": "direct",
        "seconds": round(seconds, 1),
        "rows": count,
        "worker": final,
        "returncode": worker.returncode,
        "stderr_tail": worker.stderr.strip().splitlines()[-2:],
        "other_writes": summary(stats["waits"], stats["errors"]),
    }


async def run_parent(db, path):
    from datasette.imports.subprocess_import import import_file_via_worker

    await db.execute_write("create table other (t real)")
    stop = False
    waits = []
    errors = []

    async def writer():
        while not stop:
            start = time.perf_counter()
            try:
                await db.execute_write("insert into other values (?)", [time.time()])
                waits.append(time.perf_counter() - start)
            except Exception as ex:  # noqa: BLE001
                errors.append(str(ex))
            await asyncio.sleep(0.1)

    task = asyncio.ensure_future(writer())
    start = time.perf_counter()
    result = await import_file_via_worker(db, "t", path)
    seconds = time.perf_counter() - start
    stop = True
    await task
    return {
        "mode": "parent",
        "seconds": round(seconds, 1),
        "rows": result.rows,
        "widenings": [
            {k: getattr(w, k) for k in ("column", "to_type", "at_row", "method")}
            | {"seconds": round(w.seconds, 3)}
            for w in result.import_result.widenings
        ],
        "other_writes": summary(waits, errors),
    }


async def run(args):
    from datasette.app import Datasette
    from datasette.database import Database

    ds = Datasette()
    db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
    try:
        runner = run_direct if args.mode == "direct" else run_parent
        print(json.dumps(await runner(db, args.file)))
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("make", "run"))
    parser.add_argument("--source")
    parser.add_argument("--rows", type=int)
    parser.add_argument("--out")
    parser.add_argument("--file")
    parser.add_argument("--mode", choices=("direct", "parent"), default="direct")
    args = parser.parse_args()
    if args.command == "make":
        make(args)
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
