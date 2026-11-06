"""
Why does a background import slow down so much while Datasette is busy
answering requests? A worker import (import_file) runs while another
coroutine keeps the event loop busy, holding it for ``--busy`` milliseconds
at a time and then yielding, in one of two ways:

- ``sleep``: ``time.sleep()`` - the loop is blocked but the GIL is free, so
  only the event loop is the bottleneck (fewer loop iterations a second)
- ``spin``: a pure Python loop - the loop is blocked *and* the GIL is held,
  as rendering a page does. Threads that need the GIL (the write thread
  running the inserter) get it only when the spinning thread is forced to
  give it up, every ``sys.getswitchinterval()`` (5ms by default)

and compares the worker's stdout pipe at the Linux default (64KB) with
PIPE_SIZE (1MB), interleaved.

    uv run python prototypes/data_csv/bench_background_pipe.py DATA/events-100k.csv \\
        --busy 0 --busy 10 --hog sleep --hog spin --switch 0.005 --switch 0.0005
"""

import argparse
import asyncio
import sys
import time

from datasette.app import Datasette
from datasette.imports import subprocess_import
from datasette.imports.scratch import ScratchDatabase, import_writes


async def one(path, busy_ms, hog_kind, pipe_size, timeout):
    subprocess_import.PIPE_SIZE = pipe_size
    ds = Datasette()
    db = ds.add_database(ScratchDatabase(ds), name="scratch")
    stop = False

    async def hog():
        # Hold the loop for busy_ms, then let everything else run once
        while not stop:
            if hog_kind == "sleep":
                time.sleep(busy_ms / 1000)  # noqa: ASYNC251 - blocking on purpose
            else:
                end = time.perf_counter() + busy_ms / 1000
                while time.perf_counter() < end:
                    pass
            await asyncio.sleep(0)

    hogger = asyncio.create_task(hog()) if busy_ms else None
    start = time.perf_counter()
    rows = None
    try:
        with import_writes():
            result = await asyncio.wait_for(
                subprocess_import.import_file(db, "t", path), timeout
            )
        rows = result.rows
    except TimeoutError:
        rows = (await db.execute("select count(*) from t")).first()[0]
    finally:
        stop = True
        if hogger:
            await hogger
        db.close()
    return time.perf_counter() - start, rows


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path")
    parser.add_argument("--busy", type=float, action="append")
    parser.add_argument("--hog", action="append", choices=("sleep", "spin"))
    parser.add_argument("--switch", type=float, action="append")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    for _ in range(args.repeat):
        for busy in args.busy or [0, 10]:
            for hog in args.hog or ["sleep", "spin"]:
                if not busy and hog != (args.hog or ["sleep"])[0]:
                    continue
                for switch in args.switch or [0.005]:
                    sys.setswitchinterval(switch)
                    for pipe in (None, 1024 * 1024):
                        seconds, rows = await one(
                            args.path, busy, hog, pipe, args.timeout
                        )
                        label = "64KB" if pipe is None else "1MB"
                        print(
                            f"busy {busy:>4.0f}ms {hog:<5} switch {switch * 1000:.1f}ms "
                            f"pipe {label:>4}: {rows:>9,} rows in {seconds:6.2f}s "
                            f"({rows / seconds:>9,.0f} rows/s)",
                            flush=True,
                        )


if __name__ == "__main__":
    asyncio.run(main())
