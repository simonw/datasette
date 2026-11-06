"""
Why loading through Datasette is slower than the same work on one thread.

    uv run python prototypes/data_csv/bench_cli_pipeline.py FILE [--modes import_rows,one_write_fn] [--repeat 2]

Both modes load FILE into a ScratchDatabase (WAL, synchronous=OFF) of a
real Datasette instance, committing every 1,000 rows:

- ``import_rows``: what ``datasette FILE`` does - ``import_rows()`` reads
  chunks from the loader in an executor thread and sends one
  ``db.execute_write_fn()`` per chunk to the write thread.
- ``one_write_fn``: a single ``db.execute_write_fn(transaction=False)``
  that runs ``insert_rows()`` - parsing and writing on the write thread,
  no thread hand-offs. It holds the write thread for the whole load, so it
  only suits loading before the server starts.
- ``import_rows_10k``: ``import_rows()`` with 10,000-row batches.

Each mode runs in its own subprocess; prints wall time, CPU time, peak
RSS and the CPU time of each thread (from ``/proc/self/task/TID/stat``):
the event loop, the executor threads that parse, and Datasette's write
thread. ``--switch-interval`` sets ``sys.setswitchinterval()`` (default
0.005 s), how often a thread waiting for the GIL forces the running one to
let go: if the threaded mode's extra CPU time is GIL hand-off overhead, it
changes with this.
"""

import argparse
import asyncio
import json
import os
import resource
import subprocess
import sys
import threading
import time


def thread_cpu_seconds():
    "CPU seconds used so far by each live thread, by thread name."
    ticks = os.sysconf("SC_CLK_TCK")
    out = {}
    for thread in threading.enumerate():
        try:
            with open(f"/proc/self/task/{thread.native_id}/stat") as fp:
                fields = fp.read().rsplit(")", 1)[1].split()
        except OSError:
            continue
        # utime and stime are fields 14 and 15; [0] here is field 3
        cpu = (int(fields[11]) + int(fields[12])) / ticks
        if cpu >= 0.1:
            out[thread.name] = round(cpu, 1)
    return out


async def run(mode, path):
    from datasette.app import Datasette
    from datasette.imports.inserter import import_rows, insert_rows
    from datasette.imports.loaders import load_path
    from datasette.imports.scratch import ScratchDatabase, import_writes

    ds = Datasette()
    db = ds.add_database(ScratchDatabase(ds), name="scratch")
    start = time.perf_counter()
    cpu = time.process_time()
    result = load_path(path)
    schema = dict(result.schema.columns) if result.schema else None
    with import_writes():
        if mode in ("import_rows", "import_rows_10k"):
            imported = await import_rows(
                db,
                "data",
                result.rows,
                schema=schema,
                batch_size=10000 if mode == "import_rows_10k" else 1000,
            )
        else:
            imported = await db.execute_write_fn(
                lambda conn: insert_rows(
                    conn, "data", result.rows, schema=schema, batch_size=1000
                ),
                transaction=False,
            )
    elapsed = time.perf_counter() - start
    out = {
        "mode": mode,
        "rows": imported.rows,
        "seconds": round(elapsed, 2),
        "cpu_seconds": round(time.process_time() - cpu, 2),
        "rows_per_second": round(imported.rows / elapsed),
        "ru_maxrss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
        ),
        "switch_interval": sys.getswitchinterval(),
        "thread_cpu_seconds": thread_cpu_seconds(),
    }
    ds.close()
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--modes", default="import_rows,one_write_fn")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--switch-interval", type=float, default=None)
    parser.add_argument("--one", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.switch_interval:
        sys.setswitchinterval(options.switch_interval)
    if options.one:
        print(json.dumps(asyncio.run(run(options.one, options.file))))
        return
    for i in range(options.repeat):
        for mode in options.modes.split(","):
            extra = []
            if options.switch_interval:
                extra = ["--switch-interval", str(options.switch_interval)]
            output = subprocess.run(
                [sys.executable, __file__, options.file, "--one", mode, *extra],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            result = json.loads(output)
            result["repeat"] = i + 1
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
