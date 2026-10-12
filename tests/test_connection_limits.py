"""Exercise real HTTP sockets and WAL files under a small OS descriptor limit."""

import json
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Requires RLIMIT_NOFILE")
def test_500_wal_databases_with_http_and_writes_under_256_fds():
    script = r"""
import asyncio, concurrent.futures, json, resource, socket, sqlite3, tempfile, threading, time, urllib.request
from pathlib import Path
import psutil
import uvicorn
from datasette.app import Datasette

async def main():
    with tempfile.TemporaryDirectory() as directory:
        paths = []
        for i in range(500):
            path = str(Path(directory) / f"db{i}.db")
            conn = sqlite3.connect(path)
            conn.execute("pragma journal_mode=wal")
            conn.execute("create table t(id integer primary key)")
            conn.close()
            paths.append(path)
        _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
        ds = Datasette(paths)
        await ds.invoke_startup()
        process = psutil.Process()
        baseline_rss = process.memory_info().rss
        peak = {"fds": 0, "rss": 0, "writers": 0}
        stopped = threading.Event()
        def monitor():
            while not stopped.wait(0.002):
                peak["fds"] = max(peak["fds"], process.num_fds())
                peak["rss"] = max(peak["rss"], process.memory_info().rss)
                peak["writers"] = max(peak["writers"], ds._write_budget.snapshot()["writers"])
        watcher = threading.Thread(target=monitor)
        watcher.start()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(ds.app(), log_level="error"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]))
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.01)
            def request(i):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/db{i}/-/query.json?sql=select+count(*)+from+t", timeout=20) as response:
                    assert response.status == 200
                    assert json.load(response)["ok"]
            def reads():
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as clients:
                    list(clients.map(request, range(500)))
            async def writes():
                for start in range(0, 500, 16):
                    await asyncio.gather(*(ds.get_database(f"db{i}").execute_write("insert into t values (1)") for i in range(start, min(start + 16, 500))))
            await asyncio.gather(asyncio.to_thread(reads), writes())
            for i in range(500):
                assert (await ds.get_database(f"db{i}").execute("select count(*) from t")).single_value() == 1
            assert ds._read_pool.stats["peak_open"] <= 32
            assert ds._write_budget.snapshot()["stats"]["peak_writers"] <= 8
            print(json.dumps({"peak": peak, "baseline_rss": baseline_rss, "writes": 500, "http_reads": 500}), flush=True)
        finally:
            server.should_exit = True
            await asyncio.to_thread(thread.join, 15)
            assert not thread.is_alive()
            stopped.set()
            watcher.join()
            sock.close()
            ds.close()
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    measurements = json.loads(result.stdout.strip().splitlines()[-1])
    assert measurements["peak"]["fds"] < 256
    assert measurements["peak"]["writers"] <= 8
    print(measurements)
