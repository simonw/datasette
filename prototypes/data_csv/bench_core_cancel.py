"""
How long does cancelling a worker import take when it is cancelled while
the stored numbers of a late widening to text are still being converted?

The input is the first ROWS rows of big-1gb.csv with id turned to text at
95% of the rows. import_file() (the worker import, subprocess_import.py as
it is on this branch) is cancelled from its progress callback once
CANCEL_AT rows are written - inside the backlog. Each variant is an
inserter.py whose import_rows() subprocess_import is made to call:
proto/data-csv's (the rebuild: no backlog), the reviewed proto/core one
(the clean up converted the whole backlog before raising) and the current
one (raises at once, converts in the background).

Reports: cancel -> CancelledError; cancel -> worker process gone; for
on_error="keep", cancel -> the table finished (declared TEXT, no TEMP
table) and its rows; for "drop", whether the table is gone at once.

    uv run python prototypes/data_csv/bench_core_cancel.py \\
        --csv big-1gb.csv --rows 2000000 --cancel-at 1901000 \\
        --variant before=/tmp/inserter_before.py \\
        --variant reviewed=/tmp/inserter_reviewed.py \\
        --variant now=datasette/imports/inserter.py --work /tmp/work
"""

import argparse
import asyncio
import csv
import importlib.util
import json
import os
import sqlite3
import time

from datasette.app import Datasette
from datasette.database import Database
from datasette.imports import subprocess_import


def load(name, path):
    spec = importlib.util.spec_from_file_location(f"inserter_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_csv(source, path, rows):
    if os.path.exists(path):
        return path
    text_at = int(rows * 0.95)
    with open(source, newline="") as fp, open(path + ".tmp", "w", newline="") as out:
        reader = csv.reader(fp)
        writer = csv.writer(out)
        header = next(reader)
        writer.writerow(header)
        for i, record in enumerate(reader):
            if i >= rows:
                break
            if i == text_at:
                record[0] = f"id-{i}"
            writer.writerow(record)
    os.rename(path + ".tmp", path)
    return path


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def remove(path):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


async def run(name, module, args, on_error):
    path = os.path.join(args.work, f"cancel-{name}-{os.getpid()}.db")
    remove(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    ds = Datasette()
    db = ds.add_database(Database(ds, path=path, is_mutable=True), name="d")
    subprocess_import.import_rows = module.import_rows
    state = {}
    task = None

    async def watch(pid):
        # From the moment of cancelling, not from when the call raised
        while alive(pid):
            await asyncio.sleep(0.001)
        state["worker_gone"] = time.perf_counter()

    def progress(p):
        state["pid"] = p.pid
        if p.rows_written >= args.cancel_at and "cancelled" not in state:
            state["cancelled"] = time.perf_counter()
            state["rows_at_cancel"] = p.rows_written
            state["watcher"] = asyncio.ensure_future(watch(p.pid))
            task.cancel()

    task = asyncio.ensure_future(
        subprocess_import.import_file(
            db, "t", args.csv_path, progress=progress, on_error=on_error
        )
    )
    try:
        await task
        raise AssertionError("not cancelled")
    except asyncio.CancelledError:
        raised = time.perf_counter() - state["cancelled"]
    await asyncio.wait_for(state["watcher"], 30)
    worker_gone = state["worker_gone"] - state["cancelled"]

    def table_state(conn):
        declared = dict(
            conn.execute("select name, type from pragma_table_info('t')").fetchall()
        )
        temp = conn.execute("select count(*) from sqlite_temp_master").fetchone()[0]
        return declared, temp

    exists_at_raise = await db.table_exists("t")
    finished = None
    declared = {}
    while exists_at_raise:
        declared, temp = await db.execute_write_fn(table_state)
        if "BLOB" not in declared.values() and not temp:
            finished = time.perf_counter() - state["cancelled"]
            break
        if time.perf_counter() - state["cancelled"] > 120:
            break
        await asyncio.sleep(0.01)
    result = {
        "variant": name,
        "on_error": on_error,
        "rows_at_cancel": state["rows_at_cancel"],
        "cancel_to_raise_ms": round(raised * 1000, 1),
        "cancel_to_worker_gone_ms": round(worker_gone * 1000, 1),
        "table_exists_at_raise": exists_at_raise,
        "cancel_to_table_final_ms": round(finished * 1000, 1) if finished else None,
        "declared": declared,
    }
    if exists_at_raise:
        rows = await db.execute("select typeof(id), count(*) from t group by 1")
        result["id_types"] = [tuple(r) for r in rows]
    db.close()
    ds.close()
    remove(path)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--rows", type=int, default=2_000_000)
    parser.add_argument("--cancel-at", type=int, default=1_901_000)
    parser.add_argument("--variant", action="append", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--on-error", action="append", default=None)
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    args.csv_path = make_csv(
        args.csv, os.path.join(args.work, f"cancel-{args.rows}.csv"), args.rows
    )
    variants = []
    for spec in args.variant:
        name, path = spec.split("=", 1)
        variants.append((name, load(name, path)))
    original = subprocess_import.import_rows
    try:
        for repeat in range(args.repeat):
            for on_error in args.on_error or ["keep", "drop"]:
                for name, module in variants:
                    result = asyncio.run(run(name, module, args, on_error))
                    result["repeat"] = repeat
                    print(json.dumps(result), flush=True)
    finally:
        subprocess_import.import_rows = original


if __name__ == "__main__":
    main()
