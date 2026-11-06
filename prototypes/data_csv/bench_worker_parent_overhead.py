"""
Where does the parent's CPU go in the worker path? Times, without any
database, the pieces between the pipe and TypedInserter.write():

- ``import_rows()``'s per-row pull from an async iterable
  (``chunk.append(await iterator.__anext__())``), for an async generator
  over rows that are already decoded - the floor for the async path
- the same pull for a plain list consumed in an executor (the sync path)
- decoding: ProtocolDecoder.feed() on the worker's real output for a file
  (json.loads of 256KB chunks + dict(zip()))

    uv run python prototypes/data_csv/bench_worker_parent_overhead.py \\
        --file /path/to/events-100k.csv
"""

import argparse
import asyncio
import subprocess
import sys
import time

from datasette.imports.inserter import _take
from datasette.imports.subprocess_import import ProtocolDecoder, worker_command


async def pull_async(rows, size=1000):
    async def gen():
        for row in rows:
            yield row

    iterator = gen().__aiter__()
    total = 0
    start = time.perf_counter()
    while True:
        chunk = []
        while len(chunk) < size:
            try:
                chunk.append(await iterator.__anext__())
            except StopAsyncIteration:
                break
        if not chunk:
            break
        total += len(chunk)
    return time.perf_counter() - start, total


async def pull_executor(rows, size=1000):
    iterator = iter(rows)
    loop = asyncio.get_running_loop()
    total = 0
    start = time.perf_counter()
    while True:
        chunk = await loop.run_in_executor(None, _take, iterator, size)
        if not chunk:
            break
        total += len(chunk)
    return time.perf_counter() - start, total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    output = subprocess.run(
        worker_command(args.file), capture_output=True, check=True
    ).stdout
    print(f"worker output: {len(output) / 1e6:.1f} MB")
    for read_size in (64 * 1024, 256 * 1024, 1024 * 1024):
        best = None
        for _ in range(args.repeat):
            decoder = ProtocolDecoder()
            rows = []
            start = time.perf_counter()
            longest = 0
            for i in range(0, len(output), read_size):
                t = time.perf_counter()
                for kind, payload in decoder.feed(output[i : i + read_size]):
                    if kind == "rows":
                        rows.extend(payload)
                longest = max(longest, time.perf_counter() - t)
            elapsed = time.perf_counter() - start
            best = min(best or elapsed, elapsed)
        print(
            f"decode, {read_size // 1024}KB reads: {best / len(rows) * 1e6:.2f} us/row, "
            f"longest feed() {longest * 1000:.1f} ms"
        )
    for name, fn in (("async generator", pull_async), ("executor", pull_executor)):
        best = None
        for _ in range(args.repeat):
            elapsed, total = asyncio.run(fn(rows))
            best = min(best or elapsed, elapsed)
        print(f"pull, {name}: {best / total * 1e6:.2f} us/row")


if __name__ == "__main__":
    sys.exit(main())
