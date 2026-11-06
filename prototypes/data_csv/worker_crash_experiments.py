"""
What happens when an import worker crashes, errors, runs out of memory or
is cancelled - and what happens to the server? Each scenario imports into
``Database(ds, is_temp_disk=True)`` while an event-loop lag probe runs,
then reports what the parent saw, what is left in the database (row count,
``PRAGMA integrity_check``) and whether the server still answers.

    uv run python prototypes/data_csv/worker_crash_experiments.py \\
        --data /path/to/data --work /tmp/work [scenario ...]

Scenarios: sigkill-50, direct-sigkill-50, ndjson-bad-line, plugin-raises,
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
import time

from datasette.app import Datasette
from datasette.database import Database
from datasette.imports.subprocess_import import (
    WorkerImportError,
    WorkerProcess,
    import_file_via_worker,
    worker_command,
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


async def direct_sigkill_50(args):
    ds, db = await setup()
    path = os.path.join(args.data, "big-1gb.csv")
    worker = WorkerProcess(
        worker_command(path, extra_args=["--direct-db", db.path, "--table", "t"])
    )
    lag = Lag()
    lag_task = asyncio.ensure_future(lag.run())
    await worker.start()
    last_progress = None
    killed_at = None
    while True:
        messages = await worker.read_messages()
        if messages is None:
            break
        for kind, payload in messages:
            if kind == "progress":
                last_progress = payload
                if (
                    killed_at is None
                    and payload["bytes"]
                    and payload["bytes"] >= payload["size"] / 2
                ):
                    killed_at = time.perf_counter()
                    os.kill(worker.pid, signal.SIGKILL)
    await worker.stop()
    lag.stop = True
    await lag_task
    wal = db.path + "-wal"
    outcome = {
        "returncode": worker.returncode,
        "parent_knows_rows": last_progress["rows"] if last_progress else None,
        "detected_after_ms": round((time.perf_counter() - killed_at) * 1000, 1),
        "wal_mb": round(os.path.getsize(wal) / 1e6, 1) if os.path.exists(wal) else 0,
        "lag_during": lag.summary(),
    }
    outcome.update(await state_of(ds, db))
    # Can the parent write to the database after the worker died mid-write?
    start = time.perf_counter()
    await db.execute_write("create table after_crash (id integer)")
    outcome["parent_write_after_ms"] = round((time.perf_counter() - start) * 1000, 1)
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
    "The real JSON loader (json.load() of the whole file) under a memory cap."
    path = os.path.join(args.data, "events-100k.json")
    results = {}
    # The worker's address space just before it starts loading
    baseline = {}

    def progress(p):
        if p.pid and "vm" not in baseline:
            baseline["vm"] = vm_size(p.pid)

    ds, db = await setup()
    ok = await run_import(ds, db, path, progress=progress)
    results["no_limit"] = {"outcome": ok, "worker_vm_mb": baseline.get("vm", 0) / 1e6}
    probe = subprocess.Popen(
        [sys.executable, "-P", "-m", "datasette.imports.worker", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    probe.stdout.readline()
    header_vm = vm_size(probe.pid)
    probe.kill()
    probe.wait()
    results["worker_vm_at_header_mb"] = round(header_vm / 1e6, 1)
    # glibc reserves 64MB of address space for each thread's malloc arena,
    # which RLIMIT_AS counts: compare with MALLOC_ARENA_MAX=1
    for arenas in ("default", "1"):
        env = None if arenas == "default" else {**os.environ, "MALLOC_ARENA_MAX": "1"}
        for extra_mb in (64, 128, 192, 256, 384):
            ds, db = await setup()
            limit = header_vm + extra_mb * 1024 * 1024
            outcome = await run_import(ds, db, path, memory_limit=limit, env=env)
            results[f"arenas={arenas} limit=header+{extra_mb}MB"] = {
                key: outcome.get(key)
                for key in ("raised", "error_type", "message", "rows", "rows_kept")
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
    await import_file_via_worker(db, "t", path, progress=progress, argv=argv)

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
    "direct-sigkill-50": direct_sigkill_50,
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
    parser.add_argument("scenarios", nargs="*", default=list(SCENARIOS))
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    for name in args.scenarios:
        result = asyncio.run(SCENARIOS[name](args))
        print(json.dumps({"scenario": name, **result}, default=str), flush=True)


if __name__ == "__main__":
    main()
