"""
Where the time goes when ``datasette FILE`` loads a CSV: parsing, type
detection, or SQLite writes.

    uv run python prototypes/data_csv/bench_cli_breakdown.py FILE [--modes parse,full,text] [--batch-size 1000]

Runs the same pipeline the CLI uses - ``load_path()`` rows into a
``TypedInserter`` - but sequentially on one thread with ``insert_rows()``
on a plain connection (a WAL file with ``synchronous=OFF``, like scratch),
so each part can be timed on its own:

- ``parse``: only iterate the loader's rows
- ``full``: the real import, with timers around reading a chunk from the
  loader (parse), ``TypedInserter._scan()`` + ``_convert()`` (type
  detection and value conversion), ``executemany()`` (SQLite inserts) and
  ``_widen()`` (changing column types)
- ``text``: the import with every column declared ``text`` - no detection,
  no conversion, no widening - to see what detection costs end to end

Peak RSS (``ru_maxrss``) is reported for each mode, each run in its own
subprocess.
"""

import argparse
import json
import os
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time


class TimedConnection:
    "Delegates to a sqlite3 connection, timing executemany()."

    def __init__(self, conn, timers):
        self._conn = conn
        self._timers = timers

    def executemany(self, *args):
        start = time.perf_counter()
        try:
            return self._conn.executemany(*args)
        finally:
            self._timers["sqlite_executemany"] += time.perf_counter() - start

    def execute(self, *args):
        start = time.perf_counter()
        try:
            return self._conn.execute(*args)
        finally:
            self._timers["sqlite_execute"] += time.perf_counter() - start

    def __getattr__(self, name):
        return getattr(self._conn, name)


def timed(timers, key, fn):
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            timers[key] += time.perf_counter() - start

    return wrapper


def run_mode(mode, path, batch_size, workdir, journal="wal-off"):
    from datasette.imports import inserter as inserter_module
    from datasette.imports.loaders import load_path

    timers = dict.fromkeys(
        (
            "parse",
            "detect_scan",
            "detect_convert",
            "widen",
            "sqlite_executemany",
            "sqlite_execute",
        ),
        0.0,
    )
    start = time.perf_counter()
    result = load_path(path)
    out = {"mode": mode, "loader": result.loader.name, "batch_size": batch_size}
    if mode != "parse":
        out["journal"] = journal
    if mode == "parse":
        count = sum(1 for _ in result.rows)
        out["rows"] = count
    else:
        fd, db_path = tempfile.mkstemp(dir=workdir, suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(db_path)
        # "wal-off" is like the scratch database, "delete-full" keeps
        # SQLite's defaults, like a new --save file
        mode_, synchronous = journal.split("-")
        conn.execute(f"pragma journal_mode={mode_}")
        conn.execute(f"pragma synchronous={synchronous}")
        inserter_module._take = timed(timers, "parse", inserter_module._take)
        cls = inserter_module.TypedInserter
        cls._scan = timed(timers, "detect_scan", cls._scan)
        cls._convert = timed(timers, "detect_convert", cls._convert)
        cls._widen = timed(timers, "widen", cls._widen)
        schema = None
        if mode == "text":
            first = next(iter(load_path(path).rows))
            schema = dict.fromkeys(first, "text")
        imported = inserter_module.insert_rows(
            TimedConnection(conn, timers),
            "data",
            result.rows,
            schema=schema,
            batch_size=batch_size,
        )
        out["rows"] = imported.rows
        out["types"] = imported.types
        out["widenings"] = [
            f"{w.column} {w.from_type}->{w.to_type} at {w.at_row} ({w.method}, {w.seconds:.2f}s)"
            for w in imported.widenings
        ]
        conn.close()
        out["db_mb"] = os.path.getsize(db_path) / 1e6
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                os.unlink(db_path + suffix)
    out["total_s"] = time.perf_counter() - start
    out.update({k: round(v, 2) for k, v in timers.items()})
    if mode != "parse":
        accounted = sum(timers.values())
        out["other_s"] = round(out["total_s"] - accounted, 2)
    out["ru_maxrss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--modes", default="parse,full,text")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--journal",
        choices=("wal-off", "wal-normal", "wal-full", "delete-full", "delete-off"),
        default="wal-off",
    )
    parser.add_argument("--work", default=tempfile.gettempdir())
    parser.add_argument("--one", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.one:
        print(
            json.dumps(
                run_mode(
                    options.one,
                    options.file,
                    options.batch_size,
                    options.work,
                    options.journal,
                )
            )
        )
        return
    for i in range(options.repeat):
        for mode in options.modes.split(","):
            output = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    options.file,
                    "--one",
                    mode,
                    "--batch-size",
                    str(options.batch_size),
                    "--journal",
                    options.journal,
                    "--work",
                    options.work,
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            result = json.loads(output)
            result["repeat"] = i + 1
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
