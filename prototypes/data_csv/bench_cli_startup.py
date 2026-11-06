"""
How long ``datasette FILE`` takes to answer its first request, and how much
memory it uses, for each way of storing the data.

    uv run python prototypes/data_csv/bench_cli_startup.py FILE [--modes wal,memory,save] [--repeat 2] [--work DIR]

Modes:

- ``empty``: ``datasette`` with no files, for the fixed startup cost
- ``wal``: ``datasette FILE`` - the default, a WAL temp file
- ``wal-tmpfs``: the same with ``TMPDIR`` on tmpfs (``/dev/shm``), as on
  distributions that mount ``/tmp`` as tmpfs. The scratch file is then
  RAM that does not show up in the process's RSS: ``tmpfs_mb`` is the size
  of the files in that directory and ``shmem_delta_mb`` the growth of
  ``Shmem`` in ``/proc/meminfo``, both while the server is answering
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

``--cold`` (or a mode name ending in ``-cold``, such as ``wal-cold``) evicts FILE from the page cache before every run with
``posix_fadvise(POSIX_FADV_DONTNEED)`` (only that file's clean pages, so
no root needed and other processes' caches are left alone) and reports
the fraction of FILE that was resident before and after eviction, measured
with ``mincore()``. Without it the file is normally fully cached after the
first run.
"""

import argparse
import ctypes
import json
import os
import shutil
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


def resident_fraction(path):
    "Fraction of the file's pages in the page cache, from mincore()."
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.mmap.restype = ctypes.c_void_p
    libc.mmap.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_long,
    ]
    libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    size = os.path.getsize(path)
    page = os.sysconf("SC_PAGE_SIZE")
    pages = (size + page - 1) // page
    fd = os.open(path, os.O_RDONLY)
    try:
        addr = libc.mmap(None, size, 1, 1, fd, 0)  # PROT_READ, MAP_SHARED
        if addr in (None, ctypes.c_void_p(-1).value):
            return None
        try:
            vec = (ctypes.c_ubyte * pages)()
            if libc.mincore(addr, size, vec) != 0:
                return None
            return sum(1 for v in vec if v & 1) / pages
        finally:
            libc.munmap(addr, size)
    finally:
        os.close(fd)


def evict(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def shmem_mb():
    with open("/proc/meminfo") as fp:
        for line in fp:
            if line.startswith("Shmem:"):
                return int(line.split()[1]) / 1024
    return None


def dir_mb(path):
    total = 0
    for name in os.listdir(path):
        try:
            total += os.path.getsize(os.path.join(path, name))
        except OSError:
            pass
    return total / 1e6


def get(path, timeout=600):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as r:
        return r.status, r.read()


def run_datasette(label, path, table, work, cold=False):
    # "wal-cold" is "wal" with --cold for this run only
    mode = label.removesuffix("-cold")
    cold = cold or label != mode
    args = [sys.executable, "-m", "datasette", path, "-p", str(PORT)]
    env = dict(os.environ, DATASETTE_SECRET="not_a_secret")
    tmpfs_dir = None
    shmem_before = None
    if mode == "wal-tmpfs":
        tmpfs_dir = os.path.join("/dev/shm", f"datasette-cli-bench-{os.getpid()}")
        os.makedirs(tmpfs_dir, exist_ok=True)
        env["TMPDIR"] = tmpfs_dir
        shmem_before = shmem_mb()
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
    cache = {}
    if cold and mode != "empty":
        cache["resident_before_evict"] = resident_fraction(path)
        evict(path)
        cache["resident_at_start"] = resident_fraction(path)
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
        result = {"mode": label, "first_request_s": first, **cache}
        if tmpfs_dir:
            result["tmpfs_mb"] = dir_mb(tmpfs_dir)
            result["shmem_delta_mb"] = shmem_mb() - shmem_before
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
    if tmpfs_dir:
        result["tmpfs_left_after_exit_mb"] = dir_mb(tmpfs_dir)
        shutil.rmtree(tmpfs_dir, ignore_errors=True)
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
    parser.add_argument("--cold", action="store_true")
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
                result = run_datasette(
                    mode, options.file, table, options.work, options.cold
                )
            result["repeat"] = i + 1
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
