"""
What happens when an import worker crashes, errors, runs out of memory or
is cancelled - and what happens to the server? Each scenario imports into
``Database(ds, is_temp_disk=True)`` while an event-loop lag probe runs,
then reports what the parent saw, what is left in the database (row count,
``PRAGMA integrity_check``) and whether the server still answers.

    uv run python prototypes/data_csv/worker_crash_experiments.py \\
        --data /path/to/data --work /tmp/work [scenario ...]

Scenarios: sigkill-50, ndjson-bad-line, plugin-raises,
memory-plugin, memory-json, cancel-20, parent-killed.
"""

# Blocking calls inside async functions are deliberate in this harness
# ruff: noqa: ASYNC220, ASYNC230, ASYNC251

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time

from datasette.app import Datasette
from datasette.database import Database
from datasette.imports.subprocess_import import (
    WorkerImportError,
    import_file_via_worker,
)

PLUGIN = """
    import time
    from datasette import hookimpl
    from datasette.imports.loaders import Loader, LoaderError


    class ExperimentLoader(Loader):
        name = "experiment"

        def sniff(self, first_bytes):
            return 1.0 if first_bytes.startswith(b"#experiment") else 0.0

        def load(self, path):
            with open(path) as fp:
                _, count, action = fp.read().split()
            for i in range(int(count)):
                yield {"id": i, "name": f"row {i}", "value": i * 1.5}
            if action == "raise":
                raise LoaderError(f"Gave up after {count} rows")
            if action == "sleep":
                time.sleep(30)
            if action == "allocate":
                hog = []
                while True:
                    hog.append(bytearray(64 * 1024 * 1024))


    @hookimpl
    def register_import_loaders():
        return [ExperimentLoader]
"""


def vm_size(pid):
    "Virtual memory size (what RLIMIT_AS limits) of a process, in bytes."
    try:
        with open(f"/proc/{pid}/status") as fp:
            for line in fp:
                if line.startswith("VmSize:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None


def rss(pid="self"):
    with open(f"/proc/{pid}/status") as fp:
        for line in fp:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024


def process_state(pid):
    "'gone', 'zombie' or 'running'"
    try:
        with open(f"/proc/{pid}/stat") as fp:
            state = fp.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return "gone"
    return "zombie" if state == "Z" else "running"


def workers_running():
    "PIDs of any import worker processes left on the machine."
    output = subprocess.run(
        ["pgrep", "-f", "datasette.imports.worker"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return [int(pid) for pid in output.split()]


class Lag:
    def __init__(self):
        self.samples = []
        self.stop = False

    async def run(self):
        while not self.stop:
            start = time.perf_counter()
            await asyncio.sleep(0.01)
            self.samples.append(time.perf_counter() - start - 0.01)

    def summary(self):
        if not self.samples:
            return {}
        s = sorted(self.samples)
        return {
            "p50_ms": round(s[len(s) // 2] * 1000, 2),
            "p99_ms": round(s[int(len(s) * 0.99)] * 1000, 2),
            "max_ms": round(s[-1] * 1000, 2),
        }


async def setup():
    ds = Datasette()
    db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
    await ds.invoke_startup()
    await ds.client.get("/scratch.json")
    return ds, db


async def state_of(ds, db, table="t"):
    exists = await db.table_exists(table)
    count = (
        (await db.execute(f"select count(*) from [{table}]")).single_value()
        if exists
        else None
    )
    integrity = (await db.execute("pragma integrity_check")).single_value()
    start = time.perf_counter()
    response = await ds.client.get(f"/scratch/{table}.json?_size=1")
    request_ms = round((time.perf_counter() - start) * 1000, 1)
    # Leftover TEMP tables on the write connection (kept spellings)
    temp_tables = await db.execute_write_fn(
        lambda conn: [
            row[0] for row in conn.execute("select name from temp.sqlite_master")
        ]
    )
    return {
        "table_exists": exists,
        "rows_in_table": count,
        "integrity_check": integrity,
        "table_request": response.status_code,
        "table_request_ms": request_ms,
        "temp_tables_left": temp_tables,
        "parent_rss_mb": round(rss() / 1e6, 1),
    }


def describe(ex):
    if isinstance(ex, WorkerImportError):
        return {
            "raised": "WorkerImportError",
            "kind": ex.kind,
            "error_type": ex.error_type,
            "message": str(ex)[:200],
            "returncode": ex.returncode,
            "rows_read": ex.result.rows_read if ex.result else None,
            "rows_kept": ex.result.rows if ex.result else None,
            "stderr_tail": ex.stderr.strip().splitlines()[-1:] if ex.stderr else [],
        }
    return {"raised": type(ex).__name__, "message": str(ex)[:200]}


async def run_import(ds, db, path, **kwargs):
    lag = Lag()
    lag_task = asyncio.ensure_future(lag.run())
    start = time.perf_counter()
    outcome = {}
    try:
        result = await import_file_via_worker(db, "t", path, **kwargs)
        outcome = {
            "raised": None,
            "rows": result.rows,
            "worker_max_rss_mb": round(result.worker["max_rss"] / 1e6, 1),
        }
    except BaseException as ex:  # noqa: BLE001
        outcome = describe(ex)
    outcome["seconds"] = round(time.perf_counter() - start, 2)
    lag.stop = True
    await lag_task
    outcome["lag_during"] = lag.summary()
    return outcome


async def sigkill_50(args):
    ds, db = await setup()
    path = os.path.join(args.data, "big-1gb.csv")
    info = {}

    def progress(p):
        if "killed_at" not in info and p.fraction and p.fraction >= 0.5:
            info["killed_at"] = time.perf_counter()
            info["rows_written_at_kill"] = p.rows_written
            info["pid"] = p.pid
            os.kill(p.pid, signal.SIGKILL)

    outcome = await run_import(ds, db, path, progress=progress)
    outcome["detected_after_ms"] = round(
        (time.perf_counter() - info["killed_at"]) * 1000, 1
    )
    outcome["rows_written_at_kill"] = info["rows_written_at_kill"]
    outcome["worker_after"] = process_state(info["pid"])
    outcome.update(await state_of(ds, db))
    return outcome


async def ndjson_bad_line(args):
    ds, db = await setup()
    source = os.path.join(args.data, "events-100k.ndjson")
    path = os.path.join(args.work, "bad-line-50001.ndjson")
    with open(source) as fp, open(path, "w") as out:
        out.writelines(
            '{"id": 50001, "name": "broken\n' if number == 50001 else line
            for number, line in enumerate(fp, 1)
        )
    outcome = await run_import(ds, db, path)
    outcome.update(await state_of(ds, db))
    return outcome


def plugins_dir(work):
    directory = os.path.join(work, "experiment-plugins")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "experiment_loader.py"), "w") as fp:
        fp.write(textwrap.dedent(PLUGIN))
    return directory


def experiment_file(work, count, action):
    path = os.path.join(work, f"experiment-{action}.txt")
    with open(path, "w") as fp:
        fp.write(f"#experiment {count} {action}\n")
    return path


async def plugin_raises(args):
    ds, db = await setup()
    path = experiment_file(args.work, 30000, "raise")
    outcome = await run_import(ds, db, path, plugins_dir=plugins_dir(args.work))
    outcome.update(await state_of(ds, db))
    return outcome


async def memory_plugin(args):
    ds, db = await setup()
    path = experiment_file(args.work, 20000, "allocate")
    before = rss()
    outcome = await run_import(
        ds,
        db,
        path,
        plugins_dir=plugins_dir(args.work),
        memory_limit=1024 * 1024 * 1024,
    )
    outcome["parent_rss_growth_mb"] = round((rss() - before) / 1e6, 1)
    outcome.update(await state_of(ds, db))
    return outcome


async def memory_json(args):
    """
    The real JSON loader (json.load() of the whole file) under a memory cap:
    the smallest RLIMIT_AS that lets the import finish, found by bisection,
    with glibc's default malloc arenas and with MALLOC_ARENA_MAX=1. Both
    arms pass ``env`` explicitly: with ``env=None`` import_file_via_worker
    sets MALLOC_ARENA_MAX=1 itself whenever there is a memory_limit, which
    is why the first version of this experiment compared one arm with
    itself.
    """
    path = os.path.join(args.data, "events-100k.json")
    MB = 1024 * 1024
    results = {"file_mb": round(os.path.getsize(path) / 1e6, 1)}

    def environment(arenas):
        env = {k: v for k, v in os.environ.items() if k != "MALLOC_ARENA_MAX"}
        if arenas == "1":
            env["MALLOC_ARENA_MAX"] = "1"
        return env

    # Address space at the header and at the end, without a limit
    for arenas in ("default", "1"):
        probe = subprocess.Popen(
            [sys.executable, "-P", "-m", "datasette.imports.worker", "--", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment(arenas),
        )
        probe.stdout.readline()
        header_vm = vm_size(probe.pid)
        drain = threading.Thread(target=probe.stdout.read)
        drain.start()
        peak_vm = header_vm
        while probe.poll() is None:
            peak_vm = max(peak_vm, vm_size(probe.pid) or 0)
            time.sleep(0.002)
        drain.join()
        results[f"arenas={arenas} vm_at_header_mb"] = round(header_vm / MB, 1)
        results[f"arenas={arenas} vm_peak_sampled_mb"] = round(peak_vm / MB, 1)

    async def works(limit_mb, arenas):
        ds, db = await setup()
        outcome = await run_import(
            ds, db, path, memory_limit=limit_mb * MB, env=environment(arenas)
        )
        db.close()
        return outcome

    for repeat in range(args.repeat):
        for arenas in ("default", "1"):
            low, high = 32, 1024
            failure = None
            while high - low > 4:
                middle = (low + high) // 2
                outcome = await works(middle, arenas)
                if outcome["raised"] is None:
                    high = middle
                else:
                    low = middle
                    failure = {
                        key: outcome.get(key)
                        for key in ("kind", "error_type", "message", "rows_kept")
                    }
            results[f"repeat {repeat} arenas={arenas}"] = {
                "works_at_mb": high,
                "fails_at_mb": low,
                "failure_at_low": failure,
            }
    return results


async def cancel_20(args):
    ds, db = await setup()
    path = os.path.join(args.data, "big-1gb.csv")
    reached = asyncio.Event()
    info = {}

    def progress(p):
        info["pid"] = p.pid
        if p.fraction and p.fraction >= 0.2:
            reached.set()

    task = asyncio.ensure_future(
        import_file_via_worker(db, "t", path, progress=progress)
    )
    await reached.wait()
    start = time.perf_counter()
    task.cancel()
    try:
        await task
        raised = None
    except asyncio.CancelledError:
        raised = "CancelledError"
    outcome = {
        "raised": raised,
        "cancel_took_ms": round((time.perf_counter() - start) * 1000, 1),
        "worker_after": process_state(info["pid"]),
        "workers_left_on_machine": [
            pid for pid in workers_running() if pid == info["pid"]
        ],
    }
    outcome.update(await state_of(ds, db))
    return outcome


PARENT_SCRIPT = """
import asyncio, sys
from datasette.app import Datasette
from datasette.database import Database
from datasette.imports.subprocess_import import import_file_via_worker, worker_command

async def main():
    path, parent_pid, plugins_dir = sys.argv[1], sys.argv[2] == "yes", sys.argv[3]
    plugins_dir = None if plugins_dir == "-" else plugins_dir
    ds = Datasette()
    db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
    argv = worker_command(path, plugins_dir=plugins_dir, progress_interval=0.05)
    if not parent_pid:
        i = argv.index("--parent-pid")
        del argv[i : i + 2]
    def progress(p):
        print(p.pid, flush=True)
    await import_file_via_worker(db, "t", path, progress=progress, _argv=argv)

asyncio.run(main())
"""


async def parent_killed(args):
    "SIGKILL the Datasette process during an import: does the worker go too?"
    results = {}
    script = os.path.join(args.work, "parent.py")
    with open(script, "w") as fp:
        fp.write(PARENT_SCRIPT)
    big = os.path.join(args.data, "big-1gb.csv")
    # 5000 rows, then the loader sleeps without writing anything
    busy = experiment_file(args.work, 5000, "sleep")
    plugins = plugins_dir(args.work)
    variants = {
        "CSV, with --parent-pid (PR_SET_PDEATHSIG)": (big, "yes", "-"),
        "CSV, without --parent-pid": (big, "no", "-"),
        "busy loader, with --parent-pid": (busy, "yes", plugins),
        "busy loader, without --parent-pid": (busy, "no", plugins),
    }
    for name, (path, parent_pid, plugin_dir) in variants.items():
        parent = subprocess.Popen(
            [sys.executable, script, path, parent_pid, plugin_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        pid = int(parent.stdout.readline())
        time.sleep(1.0)
        parent.kill()
        parent.wait()
        killed = time.perf_counter()
        while process_state(pid) == "running" and time.perf_counter() - killed < 5:
            await asyncio.sleep(0.005)
        state = process_state(pid)
        results[name] = {
            # A zombie has exited: it waits for init to reap it
            "worker_state": "exited" if state in ("gone", "zombie") else state,
            "after_ms": round((time.perf_counter() - killed) * 1000, 1),
        }
        if state == "running":
            os.kill(pid, signal.SIGKILL)
    return results


SCENARIOS = {
    "sigkill-50": sigkill_50,
    "ndjson-bad-line": ndjson_bad_line,
    "plugin-raises": plugin_raises,
    "memory-plugin": memory_plugin,
    "memory-json": memory_json,
    "cancel-20": cancel_20,
    "parent-killed": parent_killed,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--repeat", type=int, default=2, help="memory-json")
    parser.add_argument("scenarios", nargs="*", default=list(SCENARIOS))
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    for name in args.scenarios:
        result = asyncio.run(SCENARIOS[name](args))
        print(json.dumps({"scenario": name, **result}, default=str), flush=True)


if __name__ == "__main__":
    main()
