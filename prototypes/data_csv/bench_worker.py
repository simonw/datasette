"""
Where should an import's writes happen, and what does each choice cost the
server? Compares, for one input file:

- ``inprocess``: ``load_path()`` + ``import_rows()`` in the Datasette
  process (the loader runs in a thread of the server)
- ``worker``: ``import_file_via_worker()``: the worker parses the file and
  streams NDJSON, the parent decodes it and inserts with ``import_rows()``
  through the write queue (the design's lean)
- ``direct``: the worker inserts into the database file itself with
  ``insert_rows()`` on its own connection (``--direct-db``); the parent only
  supervises

into ``Database(ds, is_temp_disk=True)`` (a WAL file in the temp directory,
the planned scratch storage). While each import runs the Datasette process
also runs:

- an event loop lag probe: ``await asyncio.sleep(0.01)`` in a loop,
  recording how late it wakes up
- query probes through ``datasette.client``: ``/scratch/t.json?_size=10``
  and a ``select count(*) from t`` through ``/scratch/-/query.json``, one
  every ``--probe-interval`` seconds each

and, after the import, the same probes for ``--idle`` seconds as a
baseline. Every run happens in a fresh process (``run`` sub-command) so CPU
time and peak RSS are per run. ``all`` runs every mode ``--repeat`` times,
interleaved, and prints a table.

    uv run python prototypes/data_csv/bench_worker.py all \\
        --file /path/to/events-100k.csv --repeat 3 --probe-interval 0.05
"""

import argparse
import asyncio
import functools
import json
import os
import resource
import statistics
import subprocess
import sys
import time

MODES = ("inprocess", "worker", "direct", "oldworker")


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, round(fraction * (len(values) - 1)))
    return values[index]


def summarize(values):
    "milliseconds"
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "p50": round(percentile(values, 0.5) * 1000, 2),
        "p95": round(percentile(values, 0.95) * 1000, 2),
        "p99": round(percentile(values, 0.99) * 1000, 2),
        "max": round(max(values) * 1000, 2),
        "mean": round(statistics.mean(values) * 1000, 2),
    }


class Probes:
    def __init__(self, ds, interval):
        self.ds = ds
        self.interval = interval
        self.lag = []
        self.latency = {"table": [], "count": []}
        self.errors = {"table": 0, "count": 0}
        self.stop = asyncio.Event()
        self.tasks = []

    async def lag_probe(self):
        while not self.stop.is_set():
            start = time.perf_counter()
            await asyncio.sleep(0.01)
            self.lag.append(time.perf_counter() - start - 0.01)

    async def query_probe(self, name, url):
        while not self.stop.is_set():
            start = time.perf_counter()
            try:
                response = await self.ds.client.get(url)
                ok = response.status_code == 200
            except Exception:  # noqa: BLE001
                ok = False
            elapsed = time.perf_counter() - start
            if ok:
                self.latency[name].append(elapsed)
            else:
                # Before the table exists
                self.errors[name] += 1
            await asyncio.sleep(self.interval)

    def start(self, queries=True):
        self.tasks.append(asyncio.ensure_future(self.lag_probe()))
        if queries:
            self.tasks.append(
                asyncio.ensure_future(
                    self.query_probe("table", "/scratch/t.json?_size=10")
                )
            )
            self.tasks.append(
                asyncio.ensure_future(
                    self.query_probe(
                        "count",
                        "/scratch/-/query.json?sql=select+count(*)+from+t",
                    )
                )
            )

    async def finish(self):
        self.stop.set()
        await asyncio.gather(*self.tasks)
        return {
            "lag": summarize(self.lag),
            "lag_over_50ms": sum(1 for v in self.lag if v > 0.05),
            "lag_over_100ms": sum(1 for v in self.lag if v > 0.1),
            "table": summarize(self.latency["table"]),
            "count": summarize(self.latency["count"]),
            "errors": dict(self.errors),
        }


class TimedRows:
    "Wraps the in-process loader's rows to time how long producing them takes."

    def __init__(self, rows):
        self.rows = iter(rows)
        self.seconds = 0.0

    def __iter__(self):
        return self

    def __next__(self):
        start = time.perf_counter()
        try:
            return next(self.rows)
        finally:
            self.seconds += time.perf_counter() - start


async def run_inprocess(db, path, batch_size):
    from datasette.imports.inserter import import_rows
    from datasette.imports.loaders import load_path

    loaded = await asyncio.get_running_loop().run_in_executor(None, load_path, path)
    rows = TimedRows(loaded.rows)
    result = await import_rows(
        db,
        "t",
        rows,
        schema=loaded.schema.columns if loaded.schema else None,
        batch_size=batch_size,
    )
    return {"rows": result.rows, "load_seconds": round(rows.seconds, 3)}


OLD_DIR = os.environ.get("BENCH_WORKER_OLD_DIR")


async def run_worker(db, path, batch_size, old=False):
    from datasette.imports.subprocess_import import import_file_via_worker

    argv = None
    if old:
        # An older parent and worker, saved as OLD_DIR/old_subprocess_import.py
        # and OLD_DIR/old_worker.py (git show <commit>:datasette/imports/...)
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "old_subprocess_import", os.path.join(OLD_DIR, "old_subprocess_import.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import_file_via_worker = module.import_file_via_worker
        argv = [
            sys.executable,
            "-P",
            os.path.join(OLD_DIR, "old_worker.py"),
            path,
            "--parent-pid",
            str(os.getpid()),
        ]
    result = await import_file_via_worker(
        db, "t", path, batch_size=batch_size, argv=argv
    )
    return {
        "rows": result.rows,
        "worker_cpu": result.worker["cpu"],
        "worker_seconds": result.worker["seconds"],
        "worker_max_rss_mb": round(result.worker["max_rss"] / 1e6, 1),
        # Time the worker spent in write() to the pipe: blocked on the parent
        "worker_pipe_wait": result.worker.get("write_seconds"),
        "decode_seconds": round(result.stats["decode_seconds"], 3),
        "write_seconds": round(result.stats["write_seconds"], 3),
        "pipe_mb": round(result.stats["bytes_read"] / 1e6, 1),
    }


async def run_direct(db, path, batch_size):
    from datasette.imports.subprocess_import import WorkerProcess, worker_command

    worker = WorkerProcess(
        worker_command(
            path,
            extra_args=[
                "--direct-db",
                db.path,
                "--table",
                "t",
                "--batch-size",
                str(batch_size),
            ],
        )
    )
    await worker.start()
    trailer = error = None
    while True:
        messages = await worker.read_messages()
        if messages is None:
            break
        for kind, payload in messages:
            if kind == "end":
                trailer = payload
            elif kind == "error":
                error = payload
    await worker.stop()
    if error or worker.returncode:
        raise RuntimeError(f"direct worker failed: {error} {worker.stderr[-2000:]}")
    return {
        "rows": trailer["rows"],
        "worker_cpu": trailer["cpu"],
        "worker_seconds": trailer["seconds"],
        "worker_max_rss_mb": round(trailer["max_rss"] / 1e6, 1),
    }


async def one_run(mode, path, batch_size, probe_interval, idle, queries):
    from datasette.app import Datasette
    from datasette.database import Database

    ds = Datasette()
    db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
    await ds.invoke_startup()
    # Warm up: the first request is slow (startup, templates)
    await ds.client.get("/scratch.json")
    probes = Probes(ds, probe_interval)
    cpu_before = time.process_time()
    children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    probes.start(queries)
    start = time.perf_counter()
    runner = {
        "inprocess": run_inprocess,
        "worker": run_worker,
        "direct": run_direct,
        "oldworker": functools.partial(run_worker, old=True),
    }
    details = await runner[mode](db, path, batch_size)
    seconds = time.perf_counter() - start
    during = await probes.finish()
    cpu = time.process_time() - cpu_before
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    child_cpu = (children.ru_utime + children.ru_stime) - (
        children_before.ru_utime + children_before.ru_stime
    )
    count = (await db.execute("select count(*) from t")).single_value()
    assert count == details["rows"], (count, details)
    idle_stats = None
    if idle:
        probes = Probes(ds, probe_interval)
        probes.start(queries)
        await asyncio.sleep(idle)
        idle_stats = await probes.finish()
    db.close()
    return {
        "mode": mode,
        "file": os.path.basename(path),
        "rows": count,
        "seconds": round(seconds, 3),
        "rows_per_second": round(count / seconds),
        "parent_cpu": round(cpu, 3),
        "child_cpu": round(child_cpu, 3),
        "parent_max_rss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
        ),
        **details,
        "during": during,
        "idle": idle_stats,
    }


def run_all(args):
    results = []
    batch_sizes = args.batch_sizes or [args.batch_size]
    runs = [(mode, size) for size in batch_sizes for mode in args.modes]
    for repeat in range(args.repeat):
        for mode, batch_size in runs:
            command = [
                sys.executable,
                __file__,
                "run",
                "--mode",
                mode,
                "--file",
                args.file,
                "--batch-size",
                str(batch_size),
                "--probe-interval",
                str(args.probe_interval),
                "--idle",
                str(args.idle if repeat == 0 else 0),
            ]
            if args.no_queries:
                command.append("--no-queries")
            output = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            if output.returncode:
                print(output.stderr, file=sys.stderr)
                raise SystemExit(f"{mode} failed")
            result = json.loads(output.stdout.strip().splitlines()[-1])
            result["repeat"] = repeat
            result["batch_size"] = batch_size
            results.append(result)
            print(json.dumps(result), flush=True)
    print()
    header = (
        f"{'mode':10s} {'batch':>6s} {'rep':>3s} {'seconds':>8s} {'rows/s':>8s} "
        f"{'parentCPU':>9s} {'childCPU':>8s} {'lag p50/p99/max ms':>20s} "
        f"{'table p50/p95/max':>20s} {'count p50/p95/max':>20s}"
    )
    print(header)
    for r in results:
        d = r["during"]

        def three(s):
            if not s.get("n"):
                return "-"
            return f"{s['p50']}/{s['p95']}/{s['max']}"

        print(
            f"{r['mode']:10s} {r['batch_size']:6d} {r['repeat']:3d} {r['seconds']:8.2f} "
            f"{r['rows_per_second']:8d} {r['parent_cpu']:9.2f} "
            f"{r['child_cpu']:8.2f} {three(d['lag']):>20s} "
            f"{three(d['table']):>20s} {three(d['count']):>20s}"
        )
    for r in results:
        if r.get("idle"):
            d = r["idle"]
            print(
                f"idle after {r['mode']}: lag {d['lag']} table {d['table']} "
                f"count {d['count']}"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "all"))
    parser.add_argument("--file", required=True)
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--modes", nargs="+", default=list(MODES[:3]))
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--batch-sizes", type=int, nargs="+", help="all: run each mode with each"
    )
    parser.add_argument("--probe-interval", type=float, default=0.1)
    parser.add_argument("--idle", type=float, default=2.0)
    parser.add_argument("--no-queries", action="store_true")
    args = parser.parse_args()
    if args.command == "run":
        result = asyncio.run(
            one_run(
                args.mode,
                args.file,
                args.batch_size,
                args.probe_interval,
                args.idle,
                not args.no_queries,
            )
        )
        print(json.dumps(result))
    else:
        run_all(args)


if __name__ == "__main__":
    main()
