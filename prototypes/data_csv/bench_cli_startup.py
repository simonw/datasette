"""
How long ``datasette FILE`` takes to answer its first request, and how much
memory it uses, for each way of storing the data.

    uv run python prototypes/data_csv/bench_cli_startup.py FILE [--modes wal,memory,save] [--repeat 2] [--work DIR]

Modes:

- ``empty``: ``datasette`` with no files, for the fixed startup cost
- ``wal``: ``datasette FILE`` - the default, a WAL temp file
- ``memory``: the same with ``DATASETTE_SCRATCH_STORAGE=memory`` -
  shared-cache in-memory storage
- ``save``: ``datasette FILE --save WORK/save.db`` (the file is deleted
  before each run)
- ``sqlite-utils``: ``sqlite-utils memory FILE "select count(*) ..."``
  (detects CSV types by default)
- ``sqlite-utils-nodetect``: the same with ``--no-detect-types``

For the datasette modes the clock starts when the process is started and
stops when ``/-/versions.json`` answers 200; then
``/scratch/TABLE.json?_size=1`` is timed too. Peak RSS is ``VmHWM`` from
``/proc/PID/status`` just before the process is stopped (and ``ru_maxrss``
from ``wait4()``, which agrees). Modes are interleaved within each
repetition so shared-machine noise affects all of them.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PORT = 8811


def vm_hwm_kb(pid):
    with open(f"/proc/{pid}/status") as fp:
        for line in fp:
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    return None


def get(path, timeout=600):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as r:
        return r.status, r.read()


def run_datasette(mode, path, table, work):
    args = [sys.executable, "-m", "datasette", path, "-p", str(PORT)]
    env = dict(os.environ, DATASETTE_SECRET="not_a_secret")
    if mode == "empty":
        args = [sys.executable, "-m", "datasette", "-p", str(PORT)]
    elif mode == "memory":
        env["DATASETTE_SCRATCH_STORAGE"] = "memory"
    elif mode == "save":
        save = os.path.join(work, "save.db")
        for suffix in ("", "-journal", "-wal", "-shm"):
            if os.path.exists(save + suffix):
                os.unlink(save + suffix)
        args += ["--save", save]
    stderr_path = os.path.join(work, f"stderr-{mode}.txt")
    with open(stderr_path, "w") as stderr:
        start = time.perf_counter()
        process = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=stderr, env=env
        )
        first = None
        while process.poll() is None:
            try:
                status, _ = get("/-/versions.json", timeout=5)
                if status == 200:
                    first = time.perf_counter() - start
                    break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.05)
        result = {"mode": mode, "first_request_s": first}
        if first is not None and mode != "empty":
            db = "save" if mode == "save" else "scratch"
            t = time.perf_counter()
            status, body = get(f"/{db}/{table}.json?_size=1")
            result["table_request_s"] = time.perf_counter() - t
            result["table_request_status"] = status
            t = time.perf_counter()
            status, body = get(f"/{db}/-/query.json?sql=select+count(*)+from+[{table}]")
            result["count_s"] = time.perf_counter() - t
            result["count"] = json.loads(body)["rows"][0]
        result["vm_hwm_mb"] = vm_hwm_kb(process.pid) / 1024
        process.send_signal(signal.SIGINT)
        _, _, rusage = os.wait4(process.pid, 0)
        result["ru_maxrss_mb"] = rusage.ru_maxrss / 1024
        result["cpu_user_s"] = rusage.ru_utime
        result["cpu_sys_s"] = rusage.ru_stime
    with open(stderr_path) as fp:
        lines = [
            line.strip()
            for line in fp
            if line.startswith(("Loaded", "  column")) and "INFO" not in line
        ]
    result["summary"] = lines
    if mode == "save":
        result["file_mb"] = os.path.getsize(os.path.join(work, "save.db")) / 1e6
    return result


def run_sqlite_utils(mode, path, table):
    exe = os.path.join(os.path.dirname(sys.executable), "sqlite-utils")
    args = [exe, "memory", path, f"select count(*) from [{table}]"]
    if mode == "sqlite-utils-nodetect":
        args.insert(2, "--no-detect-types")
    start = time.perf_counter()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(args, stdout=out, stderr=err)
        _, status, rusage = os.wait4(process.pid, 0)
        elapsed = time.perf_counter() - start
        out.seek(0)
        err.seek(0)
        return {
            "mode": mode,
            "total_s": elapsed,
            "returncode": os.waitstatus_to_exitcode(status),
            "output": out.read().decode().strip()[:200],
            "error": err.read().decode().strip()[-300:],
            "ru_maxrss_mb": rusage.ru_maxrss / 1024,
            "cpu_user_s": rusage.ru_utime,
            "cpu_sys_s": rusage.ru_stime,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--modes", default="empty,wal,memory,save")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--table", default=None)
    parser.add_argument("--work", default=tempfile.gettempdir())
    options = parser.parse_args()
    from datasette.imports.sources import Source, assign_table_names

    source = Source(options.file, "file", options.file)
    assign_table_names([source])
    table = options.table or source.table
    sqlite_utils_table = os.path.splitext(os.path.basename(options.file))[0]
    os.makedirs(options.work, exist_ok=True)
    for i in range(options.repeat):
        for mode in options.modes.split(","):
            if mode.startswith("sqlite-utils"):
                result = run_sqlite_utils(mode, options.file, sqlite_utils_table)
            else:
                result = run_datasette(mode, options.file, table, options.work)
            result["repeat"] = i + 1
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
