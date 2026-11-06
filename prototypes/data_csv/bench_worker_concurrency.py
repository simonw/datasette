"""
How many import workers should run at once? Imports COUNT copies of one
file into separate tables of one Database(is_temp_disk=True), all started
together, with at most SLOTS workers running at a time (``slots=``), and
reports the total time, rows/s and event-loop lag. Each configuration runs
in a fresh process, interleaved, ``--repeat`` times.

    uv run python prototypes/data_csv/bench_worker_concurrency.py \\
        --file events-100k.csv --count 8 --slots 1 2 4 8 --repeat 2
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time


async def one(path, count, slots):
    from datasette.app import Datasette
    from datasette.database import Database
    from datasette.imports.subprocess_import import import_file_via_worker

    ds = Datasette()
    db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
    lag = []
    stop = False

    async def probe():
        while not stop:
            start = time.perf_counter()
            await asyncio.sleep(0.01)
            lag.append(time.perf_counter() - start - 0.01)

    task = asyncio.ensure_future(probe())
    semaphore = asyncio.Semaphore(slots)
    start = time.perf_counter()
    results = await asyncio.gather(
        *(
            import_file_via_worker(db, f"t{i}", path, slots=semaphore)
            for i in range(count)
        )
    )
    seconds = time.perf_counter() - start
    stop = True
    await task
    db.close()
    rows = sum(r.rows for r in results)
    lag.sort()
    return {
        "slots": slots,
        "count": count,
        "seconds": round(seconds, 2),
        "rows_per_second": round(rows / seconds),
        "lag_p99_ms": round(lag[int(len(lag) * 0.99)] * 1000, 1),
        "lag_max_ms": round(lag[-1] * 1000, 1),
        "worker_cpu": round(sum(r.worker["cpu"] for r in results), 1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--slots", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--one", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.one:
        print(json.dumps(asyncio.run(one(args.file, args.count, args.one))))
        return
    for repeat in range(args.repeat):
        for slots in args.slots:
            output = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--file",
                    args.file,
                    "--count",
                    str(args.count),
                    "--one",
                    str(slots),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            result = json.loads(output.stdout.strip().splitlines()[-1])
            result["repeat"] = repeat
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
