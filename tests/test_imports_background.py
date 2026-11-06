"""
Tests for background imports: ``datasette data.csv`` serving at once while
its inputs load as background tasks, the import registry, /-/imports.json,
the progress notices and cancelling. See datasette/imports/background.py.
"""

import asyncio
import contextlib
import http.server
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from datasette.app import Datasette
from datasette.cli import serve
from datasette.imports.background import (
    TerminalReporter,
    import_registry,
)
from datasette.imports.scratch import ReloadSession, ScratchDatabase, TableExists
from datasette.imports.sources import Source, assign_table_names, spool_in_thread
from datasette.imports.subprocess_import import worker_slots

CSV = "id,name,score\n1,Cleo,4.5\n2,Pancakes,3\n3,Fido,\n"
ROOT = {"id": "root"}


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "dogs.csv").write_text(CSV)
    (tmp_path / "cities.json").write_text(
        json.dumps([{"id": 1, "city": "Lima"}, {"id": 2, "city": "Oslo"}])
    )
    # Fails at the 1,501st line, after a full chunk has been committed
    (tmp_path / "bad.ndjson").write_text('{"a": 1}\n' * 1500 + "{oops\n")
    return tmp_path


def _source(path, table=None):
    source = Source(str(path), "file", str(path))
    if table:
        source.table = table
    else:
        assign_table_names([source])
    return source


def _scratch_ds():
    ds = Datasette()
    ds.root_enabled = True
    db = ds.add_database(ScratchDatabase(ds), name="scratch")
    return ds, db


def _stalling_worker(tmp_path, rows=4):
    """
    A stand-in for the import worker: sends a header, ``rows`` rows and a
    progress message saying it has read 40% of the file, then waits to be
    killed. Gives an import that stays "importing" for as long as a test
    needs.
    """
    script = tmp_path / "stalling_worker.py"
    script.write_text(
        "import json, sys, time\n"
        "def send(m): sys.stdout.write(json.dumps(m) + '\\n')\n"
        "send({'type': 'header', 'protocol': 1, 'loader': 'csv', 'size': 1000})\n"
        "send({'type': 'columns', 'add': ['n']})\n"
        f"for i in range({rows}): send([i])\n"
        "send({'type': 'progress', 'rows': 4, 'bytes': 400, 'size': 1000})\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    return [sys.executable, str(script)]


async def _wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting")
        await asyncio.sleep(0.02)


async def _settled(job):
    "Wait for the task itself to end and its done callbacks to run."
    with contextlib.suppress(asyncio.CancelledError):
        await job.task.task
    await asyncio.sleep(0)


async def _start_stalled(ds, db, tmp_path, table="slow"):
    "An import that reaches 40% (four rows) and then waits."
    path = tmp_path / "slow.csv"
    path.write_text("n\n1\n")
    job = import_registry(ds).start(
        db,
        _source(path, table),
        sample_size=2,
        batch_size=2,
        use_worker=True,
        worker_options={"argv": _stalling_worker(tmp_path)},
    )
    await ds.start_background_tasks()
    await _wait_for(lambda: job.state == "importing" and job.rows == 4)
    return job


# The registry ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_state_transitions(data_dir):
    ds, db = _scratch_ds()
    registry = import_registry(ds)
    assert import_registry(ds) is registry
    events = []
    registry.add_listener(lambda job, event: events.append((job.state, event)))
    try:
        job = registry.start(db, _source(data_dir / "dogs.csv"))
        # Nothing runs until background tasks start
        assert job.state == "queued" and job.waiting_for == "start"
        assert job.task.name == "import scratch.dogs"
        assert registry.get("scratch", "DOGS") is job
        with pytest.raises(TableExists):
            registry.start(db, _source(data_dir / "dogs.csv"))
        await ds.start_background_tasks()
        assert await job.wait(10)
        assert job.state == "done"
        assert (job.rows, job.table_exists, job.complete) == (3, True, True)
        assert job.loader == "csv"
        assert job.fraction == 1.0 and job.percent == 100
        names = [event for _, event in events]
        assert names[0] == "queued"
        assert names.index("waiting") < names.index("importing") < names.index("done")
        assert "downloading" not in names
        assert names[-1] == "done"
        # /-/tasks shows the import's task, which says what it did
        await _settled(job)
        assert job.task.state == "completed"
        assert job.task.status == "imported dogs: 3 rows"
        rows = (await db.execute("select name from dogs")).rows
        assert [row[0] for row in rows] == ["Cleo", "Pancakes", "Fido"]
        data = job.to_dict()
        assert data["state"] == "done" and data["rows"] == 3
        assert data["status"] == "imported 3 rows"
        assert data["description"] == "imported dogs: 3 rows"
        assert data["task"] == "import scratch.dogs"
        # A finished import does not stop the table from being imported
        # again (the import then finds that it exists)
        again = registry.start(db, _source(data_dir / "dogs.csv"))
        assert await again.wait(10)
        assert again.state == "failed"
        assert "already exists" in again.error
        assert again.rows_kept == 0
        assert (await db.execute("select count(*) from dogs")).first()[0] == 3
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_failure_does_not_affect_other_imports(data_dir):
    ds, db = _scratch_ds()
    registry = import_registry(ds)
    try:
        jobs = [
            registry.start(db, _source(data_dir / name))
            for name in ("dogs.csv", "bad.ndjson", "cities.json")
        ]
        await ds.start_background_tasks()
        assert await registry.wait(20)
        assert [job.state for job in jobs] == ["done", "failed", "done"]
        bad = jobs[1]
        assert "Line 1501" in bad.error
        # on_error="keep": the rows before the bad line stay
        assert bad.table_exists and bad.rows_kept == 1500
        assert (await db.execute("select count(*) from bad")).first()[0] == 1500
        assert "import failed: Line 1501" in bad.status()
        assert "the table has the first 1,500 rows" in bad.status()
        # Pages say so
        home = (await ds.client.get("/")).text
        assert "import failed: Line 1501" in home
        assert 'class="imports-notice message-error"' in home
        table = (await ds.client.get("/scratch/bad")).text
        assert "This table is incomplete." in table
        # A finished, complete table has no notice
        assert "imports-notice" not in (await ds.client.get("/scratch/dogs")).text
        # Nothing is running, so nothing polls
        assert "data-poll" not in home
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_drop_on_error(data_dir):
    ds, db = _scratch_ds()
    try:
        job = import_registry(ds).start(
            db, _source(data_dir / "bad.ndjson"), on_error="drop"
        )
        await ds.start_background_tasks()
        assert await job.wait(10)
        assert job.state == "failed"
        assert not job.table_exists and job.rows_kept == 0
        assert "the table was removed" in job.status()
        assert await db.table_names() == []
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_queued_for_a_worker_slot(data_dir):
    ds, db = _scratch_ds()
    registry = import_registry(ds)
    slots = worker_slots(ds)
    try:
        # Take both worker slots, so imports have to wait
        await slots.acquire()
        await slots.acquire()
        jobs = [
            registry.start(db, _source(data_dir / name))
            for name in ("dogs.csv", "cities.json")
        ]
        await ds.start_background_tasks()
        await _wait_for(lambda: all(job.waiting_for == "worker" for job in jobs))
        response = await ds.client.get("/-/imports.json")
        data = response.json()
        assert data["active"] == 2
        assert [i["state"] for i in data["imports"]] == ["queued", "queued"]
        assert data["imports"][0]["waiting_for"] == "worker"
        assert data["imports"][0]["status"] == "queued, waiting for an import worker"
        home = (await ds.client.get("/")).text
        assert "queued, waiting for an import worker" in home
        assert "data-poll=" in home
        slots.release()
        slots.release()
        assert await registry.wait(20)
        assert [job.state for job in jobs] == ["done", "done"]
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_progress_notices_and_endpoint(tmp_path):
    ds, db = _scratch_ds()
    try:
        job = await _start_stalled(ds, db, tmp_path)
        assert job.percent == 40
        assert job.describe() == "importing slow: 40% (4 rows)"
        assert job.task.status == "importing slow: 40% (4 rows)"
        # The rows so far can be queried
        response = await ds.client.get("/scratch/slow.json?_shape=array")
        assert [row["n"] for row in response.json()] == [0, 1, 2, 3]

        response = await ds.client.get("/-/imports.json")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        (data,) = response.json()["imports"]
        assert data["state"] == "importing"
        assert data["percent"] == 40 and data["fraction"] == 0.4
        assert data["rows"] == 4 and data["bytes"] == 400 and data["size"] == 1000
        assert data["status"] == "importing 40% (4 rows)"
        assert data["table_url"] == "http://localhost/scratch/slow"
        assert data["cancel_url"] == "http://localhost/scratch/slow/-/cancel-import"
        assert data["can_cancel"] is False
        as_root = (await ds.client.get("/-/imports.json", actor=ROOT)).json()
        assert as_root["imports"][0]["can_cancel"] is True
        # Filters
        for query, count in (
            ("?database=scratch", 1),
            ("?database=other", 0),
            ("?table=SLOW", 1),
            ("?table=other", 0),
        ):
            response = await ds.client.get("/-/imports.json" + query)
            assert len(response.json()["imports"]) == count, query

        # Homepage, database page and table page: progress text, a progress
        # bar and polling, but no cancel button for anonymous users
        for path in ("/", "/scratch", "/scratch/slow"):
            html = (await ds.client.get(path)).text
            assert "importing 40% (4 rows)" in html, path
            assert '<progress max="100" value="40">' in html
            assert "data-poll=" in html and "datasetteImportPolling" in html
            assert "cancel-import" not in html
        table_html = (await ds.client.get("/scratch/slow")).text
        assert "This table is still being imported" in table_html
        home_html = (await ds.client.get("/")).text
        assert 'data-poll="/-/imports.json"' in home_html
        assert 'href="/scratch/slow"' in home_html
        # Root gets a cancel button: a POST form back to this page
        root_html = (await ds.client.get("/scratch/slow", actor=ROOT)).text
        assert 'action="/scratch/slow/-/cancel-import"' in root_html
        assert 'name="next" value="/scratch/slow"' in root_html

        # /-/imports as a page
        page = await ds.client.get("/-/imports")
        assert page.status_code == 200
        assert "importing 40% (4 rows)" in page.text
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_imports_json_respects_view_permissions(tmp_path):
    ds = Datasette(
        config={"databases": {"scratch": {"tables": {"secret": {"allow": False}}}}}
    )
    ds.root_enabled = True
    db = ds.add_database(ScratchDatabase(ds), name="scratch")
    try:
        (tmp_path / "secret.csv").write_text(CSV)
        (tmp_path / "public.csv").write_text(CSV)
        registry = import_registry(ds)
        for name in ("secret", "public"):
            registry.start(db, _source(tmp_path / f"{name}.csv"))
        await ds.start_background_tasks()
        assert await registry.wait(10)
        tables = [
            i["table"]
            for i in (await ds.client.get("/-/imports.json")).json()["imports"]
        ]
        assert tables == ["public"]
    finally:
        await ds.invoke_shutdown()


# Cancelling ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_with_json_post(tmp_path):
    ds, db = _scratch_ds()
    try:
        job = await _start_stalled(ds, db, tmp_path)
        pid = job.pid
        assert pid
        url = "/scratch/slow/-/cancel-import"
        # Anonymous users may not cancel
        response = await ds.client.post(url, json={})
        assert response.status_code == 403
        assert job.state == "importing"
        # GET does nothing
        response = await ds.client.get(url, actor=ROOT)
        assert response.status_code == 405
        # Root may
        start = time.monotonic()
        response = await ds.client.post(url, json={}, actor=ROOT)
        assert time.monotonic() - start < 3
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        assert data["import"]["state"] == "cancelled"
        assert data["import"]["rows_kept"] == 4
        await _settled(job)
        assert job.state == "cancelled" and job.task.state == "cancelled"
        # The worker is gone, the rows so far stay, and the name is free
        assert not _alive(pid)
        assert (await db.execute("select count(*) from slow")).first()[0] == 4
        assert "import cancelled (the table has the first 4 rows)" in job.status()
        assert "This table is incomplete" in (await ds.client.get("/scratch/slow")).text
        # Cancelling again says it is not running
        response = await ds.client.post(url, json={}, actor=ROOT)
        assert response.status_code == 409
        # No import into that table at all
        response = await ds.client.post(
            "/scratch/nope/-/cancel-import", json={}, actor=ROOT
        )
        assert response.status_code == 404
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_cancel_with_form_post_and_csrf(tmp_path):
    ds, db = _scratch_ds()
    try:
        job = await _start_stalled(ds, db, tmp_path)
        url = "/scratch/slow/-/cancel-import"
        form = {"next": "/scratch"}
        # A cross-site form post is refused before it gets anywhere
        response = await ds.client.post(
            url,
            data=form,
            actor=ROOT,
            headers={"sec-fetch-site": "cross-site", "origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert job.state == "importing"
        # A same-origin one cancels and redirects back, with a message
        response = await ds.client.post(
            url, data=form, actor=ROOT, headers={"sec-fetch-site": "same-origin"}
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/scratch"
        assert "ds_messages" in response.headers.get("set-cookie", "")
        assert job.state == "cancelled"
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_cancel_redirect_stays_on_this_site(tmp_path):
    ds, db = _scratch_ds()
    try:
        await _start_stalled(ds, db, tmp_path)
        response = await ds.client.post(
            "/scratch/slow/-/cancel-import",
            data={"next": "//evil.example/"},
            actor=ROOT,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/scratch/slow"
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_cancel_action_can_be_granted(tmp_path):
    ds = Datasette(config={"permissions": {"cancel-import": {"id": "alice"}}})
    db = ds.add_database(ScratchDatabase(ds), name="scratch")
    try:
        job = await _start_stalled(ds, db, tmp_path)
        url = "/scratch/slow/-/cancel-import"
        bob = await ds.client.post(url, json={}, actor={"id": "bob"})
        assert bob.status_code == 403
        alice = await ds.client.post(url, json={}, actor={"id": "alice"})
        assert alice.status_code == 200
        assert job.state == "cancelled"
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_cancel_before_background_tasks_start(data_dir):
    ds, db = _scratch_ds()
    try:
        registry = import_registry(ds)
        job = registry.start(db, _source(data_dir / "dogs.csv"))
        assert job.cancel()
        assert job.state == "cancelled" and job.rows_kept == 0
        await ds.start_background_tasks()
        assert job.task not in ds._background_tasks.tasks()
        assert await db.table_names() == []
        # The name was released
        again = registry.start(db, _source(data_dir / "dogs.csv"))
        assert await again.wait(10)
        assert again.state == "done"
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_imports_and_kills_workers(tmp_path):
    ds, db = _scratch_ds()
    job = await _start_stalled(ds, db, tmp_path)
    pid = job.pid
    path = db.path
    start = time.monotonic()
    await ds.invoke_shutdown()
    assert time.monotonic() - start < 3
    assert job.state == "cancelled"
    assert not _alive(pid)
    assert not os.path.exists(path)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0): check its state
    try:
        with open(f"/proc/{pid}/stat") as fp:
            return fp.read().split(") ", 1)[1][0] != "Z"
    except OSError:
        return True


# Tables that are not there yet ---------------------------------------------------------


@pytest.mark.asyncio
async def test_table_not_created_yet_says_so(data_dir):
    ds, db = _scratch_ds()
    slots = worker_slots(ds)
    try:
        await slots.acquire()
        await slots.acquire()
        job = import_registry(ds).start(db, _source(data_dir / "dogs.csv"))
        await ds.start_background_tasks()
        await _wait_for(lambda: job.waiting_for == "worker")
        response = await ds.client.get("/scratch/dogs")
        assert response.status_code == 404
        assert "This table is being imported and will appear here soon" in (
            response.text
        )
        assert "queued, waiting for an import worker" in response.text
        response = await ds.client.get("/scratch/dogs.json")
        assert response.status_code == 404
        data = response.json()
        assert data["ok"] is False
        assert data["import"]["state"] == "queued"
        assert "not ready yet" in data["error"]
        # An unrelated missing table is a plain 404
        response = await ds.client.get("/scratch/other")
        assert response.status_code == 404
        assert "being imported" not in response.text
        slots.release()
        slots.release()
        assert await job.wait(10)
        assert (await ds.client.get("/scratch/dogs")).status_code == 200
    finally:
        await ds.invoke_shutdown()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pipes")
@pytest.mark.asyncio
async def test_worker_pipe_is_bigger_than_the_default():
    import fcntl

    from datasette.imports.subprocess_import import PIPE_SIZE, WorkerProcess

    with open("/proc/sys/fs/pipe-max-size") as fp:  # noqa: ASYNC230
        if int(fp.read()) < PIPE_SIZE:
            pytest.skip("pipe-max-size is smaller")
    worker = WorkerProcess([sys.executable, "-c", "pass"])
    await worker.start()
    try:
        transport = worker.process._transport.get_pipe_transport(1)
        fd = transport.get_extra_info("pipe").fileno()
        assert fcntl.fcntl(fd, fcntl.F_GETPIPE_SZ) == PIPE_SIZE
    finally:
        await worker.stop()


# Downloads and standard input --------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        body = CSV.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def http_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.asyncio
async def test_url_downloads_in_the_task(http_server, tmp_path):
    ds, db = _scratch_ds()
    events = []
    registry = import_registry(ds)
    registry.add_listener(lambda job, event: events.append(event))
    try:
        source = Source(http_server + "/dogs.csv", "url", http_server + "/dogs.csv")
        assign_table_names([source])
        job = registry.start(db, source, workdir=str(tmp_path))
        await ds.start_background_tasks()
        assert await job.wait(10)
        assert job.state == "done" and job.rows == 3
        assert "downloading" in events
        assert job.downloaded == job.download_size == len(CSV)
        # Its own copy is gone
        assert source.path is None
        assert [p.name for p in tmp_path.iterdir()] == []
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_spool_in_thread_cancels_without_waiting_for_the_writer(tmp_path):
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb")
    progress = []
    try:
        os.write(write_fd, b"a,b\n1,2\n")
        task = asyncio.ensure_future(
            spool_in_thread(
                lambda: stream,
                str(tmp_path),
                "stdin-",
                lambda done, total: progress.append(done),
                progress_interval=0,
            )
        )
        await _wait_for(lambda: progress)
        assert progress[-1] == 8
        # The writer never finishes: cancelling must not wait for it
        start = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - start < 1
        assert list(tmp_path.iterdir()) == []
        # And the event loop was never blocked: this test got here
    finally:
        os.close(write_fd)


@pytest.mark.asyncio
async def test_spool_in_thread_copies_everything(tmp_path):
    read_fd, write_fd = os.pipe()

    def writer():
        with os.fdopen(write_fd, "wb") as fp:
            for _ in range(100):
                fp.write(b"x" * 10_000)

    thread = threading.Thread(target=writer)
    thread.start()
    path = await spool_in_thread(lambda: os.fdopen(read_fd, "rb"), str(tmp_path))
    thread.join()
    assert os.path.getsize(path) == 1_000_000


# The command line ------------------------------------------------------------------


def ds_from_cli(args, **kwargs):
    ctx = serve.make_context("serve", list(args))
    return serve.callback(**ctx.params, return_instance=True, **kwargs)


@pytest.mark.asyncio
async def test_cli_registers_background_imports(data_dir, capsys):
    ds = ds_from_cli(
        [str(data_dir / "dogs.csv"), str(data_dir / "bad.ndjson")],
        background_imports=True,
    )
    try:
        registry = import_registry(ds)
        jobs = registry.imports()
        assert [(job.table, job.state) for job in jobs] == [
            ("dogs", "queued"),
            ("bad", "queued"),
        ]
        # The first request starts them
        response = await ds.client.get("/-/imports.json")
        assert response.status_code == 200
        assert await registry.wait(20)
        assert [job.state for job in jobs] == ["done", "failed"]
        db = ds.get_database("scratch")
        assert [loaded.table for loaded in db.loaded] == ["dogs"]
        page = (await ds.client.get("/scratch")).text
        assert "--save data" in page and 'href="/scratch/dogs"' in page
        stderr = capsys.readouterr().err
        assert f"Importing {data_dir / 'dogs.csv'} (csv, 46 bytes) into table dogs" in (
            stderr
        )
        assert f"Loaded 3 rows from {data_dir / 'dogs.csv'} (csv) into table dogs" in (
            stderr
        )
        assert (
            f"Failed to import {data_dir / 'bad.ndjson'} into table bad: Line 1501"
            in stderr
        )
        assert "table bad has the first 1,500 rows" in stderr
        assert "Imported 1 of 2 inputs (3 rows)" in stderr
    finally:
        await ds.invoke_shutdown()


def test_cli_still_checks_local_files_at_once(tmp_path):
    import click

    (tmp_path / "empty.csv").write_text("")
    for args, message in (
        ([str(tmp_path / "missing.csv")], "does not exist"),
        ([str(tmp_path / "empty.csv")], "is empty"),
        (["t:" + str(tmp_path)], "is a directory"),
    ):
        # These fail before the server would start
        with pytest.raises(click.ClickException) as ex:
            ds_from_cli(args, background_imports=True)
        assert message in ex.value.message


@pytest.mark.asyncio
async def test_cli_save_drops_failed_tables_and_uses_wal(data_dir):
    save = data_dir / "data.db"
    ds = ds_from_cli(
        [str(data_dir / "dogs.csv"), str(data_dir / "bad.ndjson"), "--save", str(save)],
        background_imports=True,
    )
    try:
        registry = import_registry(ds)
        await ds.client.get("/")
        assert await registry.wait(20)
        bad = registry.get("data", "bad")
        assert bad.state == "failed" and bad.on_error == "drop"
        assert not bad.table_exists
        assert await ds.get_database("data").table_names() == ["dogs"]
    finally:
        await ds.invoke_shutdown()
    conn = sqlite3.connect(save)
    assert conn.execute("pragma journal_mode").fetchone()[0] == "wal"
    assert [r[0] for r in conn.execute("select name from sqlite_master")] == ["dogs"]
    conn.close()


@pytest.mark.asyncio
async def test_cli_reload_session_keeps_finished_tables(data_dir, monkeypatch):
    import hupper

    class FakeReloader:
        def watch_files(self, files):
            pass

    monkeypatch.setattr(hupper, "start_reloader", lambda *a, **kw: FakeReloader())
    monkeypatch.setattr(hupper, "is_active", lambda: True)
    monkeypatch.delenv(ReloadSession.ENV, raising=False)
    session = ReloadSession.start()
    args = [str(data_dir / "dogs.csv"), str(data_dir / "cities.json"), "--reload"]
    try:
        ds = ds_from_cli(args, background_imports=True)
        try:
            await ds.client.get("/")
            assert await import_registry(ds).wait(20)
            manifest = session.manifest(session.scratch_path)
            assert {k: v["complete"] for k, v in manifest.items()} == {
                "dogs": True,
                "cities": True,
            }
        finally:
            await ds.invoke_shutdown()
        # A reload after a code change: nothing to import again
        (data_dir / "cities.json").write_text(json.dumps([{"id": 9}]))
        ds = ds_from_cli(args, background_imports=True)
        try:
            jobs = import_registry(ds).imports()
            assert [job.table for job in jobs] == ["cities"]
            await ds.client.get("/")
            assert await import_registry(ds).wait(20)
            db = ds.get_database("scratch")
            assert (await db.execute("select count(*) from dogs")).first()[0] == 3
            assert (await db.execute("select id from cities")).first()[0] == 9
        finally:
            await ds.invoke_shutdown()
    finally:
        os.environ.pop(ReloadSession.ENV, None)
        import shutil

        shutil.rmtree(session.directory, ignore_errors=True)


def test_get_loads_before_answering(data_dir):
    from click.testing import CliRunner

    from datasette.cli import cli

    result = CliRunner().invoke(
        cli,
        [str(data_dir / "dogs.csv"), "--get", "/-/imports.json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    # Loaded before the request, without the background machinery
    assert json.loads(result.stdout) == {"ok": True, "active": 0, "imports": []}
    assert "Loaded 3 rows" in result.stderr
    result = CliRunner().invoke(
        cli,
        [str(data_dir / "dogs.csv"), "--get", "/scratch/dogs.json?_shape=array"],
    )
    assert len(json.loads(result.stdout)) == 3


def test_terminal_reporter_prints_periodic_progress():
    import io

    class Job:
        id = "1"
        table = "big"
        title = "big.csv"
        kind = "file"
        state = "importing"
        seconds = 12.0
        download_size = downloaded = None

        def describe(self):
            return "importing big: 40% (2,560,000 rows)"

    stream = io.StringIO()
    reporter = TerminalReporter(stream, interval=0)
    job = Job()
    reporter(job, "progress")
    reporter(job, "progress")
    assert stream.getvalue().splitlines() == [
        "  importing big: 40% (2,560,000 rows) after 12s",
        "  importing big: 40% (2,560,000 rows) after 12s",
    ]


# The real server -----------------------------------------------------------------


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.read()


def test_server_answers_while_stdin_is_still_arriving(tmp_path):
    """
    ``datasette -`` with standard input that has not finished: the server
    answers at once, shows the import reading standard input, and loads the
    table when the input ends. Ctrl+C then stops it and removes every
    temporary file.
    """
    port = _free_port()
    env = dict(os.environ, TMPDIR=str(tmp_path), DATASETTE_SECRET="not_a_secret")
    process = subprocess.Popen(
        [sys.executable, "-m", "datasette", "-", "-p", str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        process.stdin.write(b"id,name\n1,Cleo\n")
        process.stdin.flush()
        start = time.monotonic()
        while True:
            try:
                status, _ = _get(f"http://127.0.0.1:{port}/", timeout=1)
                break
            except OSError:
                if time.monotonic() - start > 20:
                    raise
                time.sleep(0.05)
        assert status == 200
        _, body = _get(f"http://127.0.0.1:{port}/-/imports.json")
        (job,) = json.loads(body)["imports"]
        assert job["state"] == "downloading" and job["kind"] == "stdin"
        assert job["status"].startswith("reading standard input")
        # The rest of the input, then the end of it
        process.stdin.write(b"2,Pancakes\n")
        process.stdin.close()
        deadline = time.monotonic() + 20
        while True:
            _, body = _get(f"http://127.0.0.1:{port}/-/imports.json")
            if not json.loads(body)["active"]:
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        _, body = _get(f"http://127.0.0.1:{port}/scratch/stdin.json?_shape=array")
        assert [row["name"] for row in json.loads(body)] == ["Cleo", "Pancakes"]
    finally:
        process.send_signal(signal.SIGINT)
        try:
            _, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            raise
    assert b"Loaded 2 rows from standard input (csv) into table stdin" in stderr
    # The scratch database and the copy of standard input are gone
    assert list(tmp_path.iterdir()) == []


def test_ctrl_c_while_reading_stdin(tmp_path):
    "Standard input that never ends does not stop Ctrl+C."
    port = _free_port()
    env = dict(os.environ, TMPDIR=str(tmp_path), DATASETTE_SECRET="not_a_secret")
    process = subprocess.Popen(
        [sys.executable, "-m", "datasette", "-", "-p", str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        process.stdin.write(b"id,name\n1,Cleo\n")
        process.stdin.flush()
        start = time.monotonic()
        while True:
            with contextlib.suppress(OSError):
                _get(f"http://127.0.0.1:{port}/-/imports.json", timeout=1)
                break
            assert time.monotonic() - start < 20
            time.sleep(0.05)
        process.send_signal(signal.SIGINT)
        _, stderr = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
    assert (
        b"Stopped importing standard input into table stdin, because Datasette "
        b"is shutting down" in stderr
    )
    assert list(tmp_path.iterdir()) == []
