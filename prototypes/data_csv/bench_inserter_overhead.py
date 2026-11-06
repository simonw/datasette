"""
Where does import_rows() spend time per batch, beyond the inserter itself?

- an empty ``await db.execute_write_fn(lambda conn: None)``
- pulling a batch with ``loop.run_in_executor`` (no writing)
- import_rows() as written (next batch read in a thread while writing)
- the same, but reading each batch on the event loop instead of a thread

    uv run python prototypes/data_csv/bench_inserter_overhead.py \\
        --file /path/to/events-100k.csv
"""

import argparse
import asyncio
import sys
import time
import uuid

from sqlite_utils.utils import rows_from_file

from datasette.app import Datasette
from datasette.imports.inserter import _take, import_rows


async def empty_writes(count):
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    await db.execute_write_fn(lambda conn: None)
    start = time.perf_counter()
    for _ in range(count):
        await db.execute_write_fn(lambda conn: None)
    elapsed = time.perf_counter() - start
    ds.close()
    return elapsed / count


async def executor_pulls(path, batch_size):
    loop = asyncio.get_running_loop()
    with open(path, "rb") as fp:  # noqa: ASYNC230
        rows, _ = rows_from_file(fp)
        iterator = iter(rows)
        start = time.perf_counter()
        batches = 0
        while await loop.run_in_executor(None, _take, iterator, batch_size):
            batches += 1
        return time.perf_counter() - start, batches


async def inline_pulls(path, batch_size):
    with open(path, "rb") as fp:  # noqa: ASYNC230
        rows, _ = rows_from_file(fp)
        iterator = iter(rows)
        start = time.perf_counter()
        batches = 0
        while _take(iterator, batch_size):
            batches += 1
        return time.perf_counter() - start, batches


async def run_import(path, batch_size, inline):
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    with open(path, "rb") as fp:  # noqa: ASYNC230
        rows, _ = rows_from_file(fp)
        if inline:
            # An async iterable is read on the event loop, no thread
            source = rows

            async def aiter_rows():
                for row in source:
                    yield row

            rows = aiter_rows()
        start = time.perf_counter()
        result = await import_rows(db, "t", rows, batch_size=batch_size)
        elapsed = time.perf_counter() - start
    ds.close()
    return elapsed, result.batches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[100, 1000])
    args = parser.parse_args()
    print(
        f"Python {sys.version.split()[0]}, switch interval {sys.getswitchinterval()}s"
    )
    per_call = asyncio.run(empty_writes(2000))
    print(f"- empty execute_write_fn(): {per_call * 1e6:.0f} us per call")
    for batch_size in args.batch_sizes:
        seconds, batches = asyncio.run(executor_pulls(args.file, batch_size))
        inline, _ = asyncio.run(inline_pulls(args.file, batch_size))
        print(
            f"- batch {batch_size}: read all batches via run_in_executor {seconds:.2f}s"
            f" vs inline {inline:.2f}s"
            f" ({(seconds - inline) / batches * 1e6:.0f} us extra per batch)"
        )
        for inline_reads in (False, True):
            best = min(
                asyncio.run(run_import(args.file, batch_size, inline_reads))[0]
                for _ in range(3)
            )
            print(
                f"- batch {batch_size}: import_rows, reading "
                + ("on the event loop" if inline_reads else "in a thread")
                + f": {best:.2f}s"
            )


if __name__ == "__main__":
    main()
