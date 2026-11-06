"""
How much parent CPU do positional rows save? The import worker sends rows
as JSON arrays plus a column list; the parent (subprocess_import.py) turns
each into a dictionary, which TypedInserter then turns back into a list.
TypedInserter.write(conn, rows, columns) takes the lists as they are.

This runs the real worker once per input to capture its protocol stream,
then replays the parent's work on it in this process, single-threaded,
timing CPU with time.process_time():

- ``decode``: split lines and parse each with the same C JSON scanner the
  parent's ProtocolDecoder uses (row arrays -> lists, "columns" messages
  extend the column list) - the part both variants share
- ``dicts``: decode, ``dict(zip(columns, values))`` per row (what
  ProtocolDecoder does today), TypedInserter.write(conn, chunk)
- ``positional``: decode, TypedInserter.write(conn, chunk, columns)

Chunks are a 1,000-row sample then 1,000-row batches (scratch.py's
DEFAULT_BATCH_SIZE), each in its own transaction on a WAL file with
synchronous=OFF, as the scratch database's writer has. Variants are
interleaved and repeated.

    uv run python prototypes/data_csv/bench_core_positional.py \\
        --work /tmp/work --repeat 3 events-100k.csv big-1m.csv
"""

import argparse
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time

from datasette.imports.inserter import TypedInserter
from datasette.imports.subprocess_import import _SCAN

CHUNK = 1000


def capture_stream(path, work):
    out = os.path.join(work, os.path.basename(path) + ".protocol")
    if not os.path.exists(out):
        with open(out, "wb") as fp:
            subprocess.run(
                [sys.executable, "-m", "datasette.imports.worker", "--", path],
                stdout=fp,
                check=True,
            )
    return out


def decoded(stream_path):
    """
    Yield (columns, values) for every row of a captured stream, parsing it
    the way ProtocolDecoder does; ``columns`` is one list, extended in place.
    """
    columns = []
    with open(stream_path, "rb") as fp:
        pending = b""
        while True:
            data = fp.read(256 * 1024)
            if not data:
                break
            data = pending + data
            end = data.rfind(b"\n") + 1
            pending = data[end:]
            text = data[:end].decode("utf-8", "surrogatepass")
            pos = 0
            length = len(text)
            while pos < length:
                value, pos = _SCAN(text, pos)
                pos += 1
                if type(value) is list:
                    yield columns, value
                elif value.get("type") == "columns":
                    columns.extend(value["add"])


def open_db(work, label):
    path = os.path.join(work, f"positional-{label}-{os.getpid()}.db")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.unlink(path + suffix)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    return conn, path


def remove(path):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.unlink(path + suffix)


def run(stream_path, variant, work):
    conn, path = open_db(work, variant) if variant != "decode" else (None, None)
    inserter = TypedInserter("t")
    rows = 0
    chunk = []
    cpu = time.process_time()
    wall = time.perf_counter()

    def write(chunk, columns):
        conn.execute("BEGIN IMMEDIATE")
        if variant == "dicts":
            inserter.write(conn, chunk)
        else:
            inserter.write(conn, chunk, columns)
        conn.execute("COMMIT")

    columns = None
    for columns, values in decoded(stream_path):
        rows += 1
        if variant == "decode":
            continue
        if variant == "dicts":
            chunk.append(dict(zip(columns, values)))
        else:
            chunk.append(values)
        if len(chunk) >= CHUNK:
            write(chunk, columns)
            chunk = []
    if chunk:
        write(chunk, columns)
    if conn is not None:
        conn.execute("BEGIN IMMEDIATE")
        while inserter.backlog:
            inserter.convert(conn)
        inserter.finish(conn)
        conn.execute("COMMIT")
    cpu = time.process_time() - cpu
    wall = time.perf_counter() - wall
    if conn is not None:
        conn.close()
        remove(path)
    return rows, cpu, wall


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--work", required=True)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    for path in args.paths:
        stream = capture_stream(path, args.work)
        results = {"decode": [], "dicts": [], "positional": []}
        rows = None
        for _ in range(args.repeat):
            for variant in ("positional", "dicts", "decode"):
                rows, cpu, wall = run(stream, variant, args.work)
                results[variant].append((cpu, wall))
        cpu = {v: statistics.median(c for c, _ in r) for v, r in results.items()}
        saving = cpu["dicts"] - cpu["positional"]
        print(
            json.dumps(
                {
                    "file": os.path.basename(path),
                    "rows": rows,
                    "stream_bytes": os.path.getsize(stream),
                    "cpu_seconds": {
                        v: [round(c, 2) for c, _ in r] for v, r in results.items()
                    },
                    "median_cpu": {v: round(c, 2) for v, c in cpu.items()},
                    "saving_seconds": round(saving, 2),
                    "saving_us_per_row": round(saving / rows * 1e6, 2),
                    "saving_percent_of_parent": round(100 * saving / cpu["dicts"], 1),
                    "saving_percent_after_decode": round(
                        100 * saving / (cpu["dicts"] - cpu["decode"]), 1
                    ),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
