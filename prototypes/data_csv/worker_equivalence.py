"""
Import each file in-process (load_path + import_rows) and through the
worker, and compare the two tables exactly: the CREATE TABLE statement and
typeof()/quote() of every value. Also writes a few crafted files (nested
values, sparse keys, keys in changing orders, None-only columns, lone
surrogates) to --work and compares those.

    uv run python prototypes/data_csv/worker_equivalence.py \\
        --work /tmp/equiv /path/to/data/*
"""

import argparse
import asyncio
import json
import os
import random
import sqlite3

from datasette.app import Datasette
from datasette.database import Database
from datasette.imports.inserter import import_rows
from datasette.imports.loaders import load_path
from datasette.imports.subprocess_import import (
    WorkerImportError,
    import_file_via_worker,
)


def crafted(work):
    os.makedirs(work, exist_ok=True)
    rng = random.Random(0)
    files = {}

    def ndjson(name, rows):
        path = os.path.join(work, name)
        with open(path, "w") as fp:
            fp.writelines(json.dumps(row) + "\n" for row in rows)
        files[name] = path

    ndjson(
        "nested.ndjson",
        [
            {"id": 1, "geometry": {"type": "Point", "coordinates": [1.5, -2]}},
            {"id": 2, "geometry": [1, [2, [3, None]]], "tags": ["a", "é", "\ud800"]},
            {"id": 3, "geometry": {"nan": float("nan"), "inf": float("inf")}},
            {"id": 4, "geometry": "plain text", "tags": []},
            {"id": 5, "geometry": 7, "tags": {}},
            {"id": 6, "geometry": {"$base64": True, "encoded": "AAA="}},
            {"id": "7", "geometry": None, "tags": [{"deep": [[[]]]}]},
        ],
    )
    names = [f"k{i}" for i in range(300)]
    rows = []
    for i in range(3000):
        row = {"id": i}
        for key in rng.sample(names, 5):
            row[key] = rng.choice([None, i, "x", 2.5, [i], {"k": i}])
        if rng.random() < 0.3:
            row = dict(reversed(list(row.items())))
        rows.append(row)
    rows.append({"only_none": None, "id": -1})
    rows.append({})
    ndjson("sparse-mixed.ndjson", rows)
    ndjson(
        "key-orders.ndjson",
        [{"a": 1, "b": 2}, {"b": None, "a": 3, "c": None}, {"c": 1}, {"d": None}, {}],
    )
    return list(files.values())


async def compare(db, path, n):
    a, b = f"inproc_{n}", f"worker_{n}"
    error_a = error_b = None
    try:
        loaded = await asyncio.get_running_loop().run_in_executor(None, load_path, path)
        await import_rows(
            db,
            a,
            loaded.rows,
            schema=loaded.schema.columns if loaded.schema else None,
            batch_size=1000,
        )
    except Exception as ex:  # noqa: BLE001
        error_a = f"{type(ex).__name__}: {ex}"
    try:
        await import_file_via_worker(db, b, path)
    except WorkerImportError as ex:
        error_b = f"{ex.error_type}: {ex}"
    name = os.path.basename(path)
    if error_a or error_b:
        same = (error_a or "").split(":")[0] == (error_b or "").split(":")[0]
        print(f"{name}: errors {'match' if same else 'DIFFER'}: {error_a} | {error_b}")
        return same
    conn = sqlite3.connect(db.path)
    try:
        sql_a, sql_b = (
            conn.execute("select sql from sqlite_master where name = ?", [t])
            .fetchone()[0]
            .replace(t, "T")
            for t in (a, b)
        )
        columns = [row[1] for row in conn.execute(f'pragma table_info("{a}")')]
        expr = ", ".join(f'typeof("{c}"), quote("{c}")' for c in columns) or "1"
        data_a, data_b = (
            conn.execute(f'select {expr} from "{t}" order by rowid').fetchall()
            for t in (a, b)
        )
    finally:
        conn.close()
    same = sql_a == sql_b and data_a == data_b
    print(
        f"{name}: {len(data_a)} rows, schema {'same' if sql_a == sql_b else 'DIFFERS'}"
        f", data {'same' if data_a == data_b else 'DIFFERS'}"
    )
    return same


async def main(args):
    ds = Datasette()
    db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
    db.connect()
    paths = [p for p in args.paths if os.path.getsize(p) < 100_000_000]
    paths += crafted(args.work)
    results = [await compare(db, path, n) for n, path in enumerate(paths)]
    db.close()
    print(f"{sum(results)} of {len(results)} files identical (or the same error)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True)
    parser.add_argument("paths", nargs="*")
    asyncio.run(main(parser.parse_args()))
