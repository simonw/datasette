"""
Tests for background imports: ``datasette data.csv`` serving at once while
its inputs load as background tasks, the import registry, /-/imports.json,
the progress notices and cancelling. See datasette/imports/background.py.
"""

import asyncio
import contextlib
import http.server
import io
import json
import logging
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request

import markupsafe
import pytest

from datasette.app import Datasette
from datasette.cli import serve
from datasette.database import Database
from datasette.imports.background import (
    TerminalReporter,
    import_registry,
)
from datasette.imports.inserter import TypedInserter
from datasette.imports.scratch import (
    ReloadSession,
    ScratchDatabase,
    TableExists,
    import_writes,
)
from datasette.imports.sources import Source, assign_table_names, spool_in_thread
from datasette.imports.subprocess_import import worker_slots
from datasette.tokens import TokenRestrictions
from datasette.utils import escape_sqlite

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


def _stalling_worker(tmp_path, rows=4, note=None):
    """
    A stand-in for the import worker: sends a header, ``rows`` rows (and a
    loader's ``note``) and a progress message saying it has read 40% of the
    file, then waits to be killed. Gives an import that stays "importing"
    for as long as a test needs.
    """
    script = tmp_path / "stalling_worker.py"
    note_line = ""
    if note:
        message = {"type": "log", "level": "WARNING", "message": note, "note": True}
        note_line = f"send({message!r})\n"
    script.write_text(
        "import json, sys, time\n"
        "def send(m): sys.stdout.write(json.dumps(m) + '\\n')\n"
        "send({'type': 'header', 'protocol': 1, 'loader': 'csv', 'size': 1000})\n"
        "send({'type': 'columns', 'add': ['n']})\n"
        f"for i in range({rows}): send([i])\n"
        f"{note_line}"
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


async def _start_stalled(ds, db, tmp_path, table="slow", on_error="keep", note=None):
    "An import that reaches 40% (four rows) and then waits."
    path = tmp_path / "slow.csv"
    path.write_text("n\n1\n")
    job = import_registry(ds).start(
        db,
        _source(path, table),
        sample_size=2,
        batch_size=2,
        use_worker=True,
        worker_options={"_argv": _stalling_worker(tmp_path, note=note)},
        on_error=on_error,
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
        tasks = (await ds.client.get("/-/tasks.json", actor={"id": "root"})).json()
        [task] = [t for t in tasks["tasks"] if t["name"] == "import scratch.dogs"]
        assert (task["state"], task["status"]) == ("completed", "imported dogs: 3 rows")
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
@pytest.mark.parametrize("on_error", ["keep", "drop"])
async def test_cancelling_a_queued_reimport_leaves_the_existing_table_alone(
    data_dir, on_error
):
    # A second import into a table that is there already, cancelled while
    # it waits for a worker - before it has checked for the table. The
    # complete table must not be dropped (drop) or called incomplete (keep)
    ds, db = _scratch_ds()
    registry = import_registry(ds)
    slots = worker_slots(ds)
    try:
        first = registry.start(db, _source(data_dir / "dogs.csv"))
        await ds.start_background_tasks()
        assert await first.wait(10) and first.state == "done"
        await slots.acquire()
        await slots.acquire()
        again = registry.start(db, _source(data_dir / "dogs.csv"), on_error=on_error)
        await _wait_for(lambda: again.waiting_for == "worker")
        assert again.cancel()
        assert await again.wait(5)
        assert again.state == "cancelled"
        assert again.existing_table is True
        assert (again.table_exists, again.created_table, again.rows_kept) == (
            False,
            False,
            0,
        )
        assert (await db.execute("select count(*) from dogs")).first()[0] == 3
        # The table's state is still the first import's
        assert registry.get("scratch", "dogs") is first
        assert registry.tables() == [first]
        assert [job.id for job in registry.imports()] == [first.id, again.id]
        for path in ("/scratch/dogs", "/", "/scratch"):
            html = (await ds.client.get(path)).text
            assert "imports-notice" not in html, path
            assert "incomplete" not in html, path
        slots.release()
        slots.release()
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_a_failed_reimport_does_not_replace_the_tables_import(data_dir):
    # Uploading the same file twice: the second import fails because the
    # table exists, and must not make the complete table look incomplete
    ds, db = _scratch_ds()
    registry = import_registry(ds)
    try:
        first = registry.start(db, _source(data_dir / "dogs.csv"))
        await ds.start_background_tasks()
        assert await first.wait(10)
        again = registry.start(db, _source(data_dir / "cities.json", "dogs"))
        assert await again.wait(10)
        assert again.state == "failed" and "already exists" in again.error
        assert again.existing_table is True and again.rows_kept == 0
        assert registry.get("scratch", "dogs") is first
        assert (await db.execute("select count(*) from dogs")).first()[0] == 3
        assert "the table that was there already was left alone" in again.status()
        # Nor does one that failed before it got to the table: a download
        url = "http://127.0.0.1:9/dogs.csv"
        source = Source(url, "url", url)
        source.table = "dogs"
        unreachable = registry.start(db, source)
        assert await unreachable.wait(10)
        assert unreachable.state == "failed" and not unreachable.existing_table
        assert registry.get("scratch", "dogs") is first
        for path in ("/scratch/dogs", "/", "/scratch"):
            html = (await ds.client.get(path)).text
            assert "imports-notice" not in html, path
        # /-/imports.json is the history: every import, saying what happened
        data = (await ds.client.get("/-/imports.json")).json()
        assert [(i["state"], i["existing_table"]) for i in data["imports"]] == [
            ("done", False),
            ("failed", True),
            ("failed", False),
        ]
        # The record that says what state the table is in survives pruning
        # that removes the failed attempts first
        from datasette.imports import background

        original = background.MAX_FINISHED
        background.MAX_FINISHED = 1
        try:
            registry._prune()
        finally:
            background.MAX_FINISHED = original
        assert registry.imports() == [first]

        # A table that is there without any import having created it: the
        # failed import is all the registry knows, and still no notice says
        # the table is incomplete
        with import_writes():
            await db.execute_write("create table other (id integer)")
        other = registry.start(db, _source(data_dir / "dogs.csv", "other"))
        assert await other.wait(10)
        assert other.state == "failed" and other.existing_table
        assert registry.get("scratch", "other") is other
        for path in ("/scratch/other", "/"):
            html = (await ds.client.get(path)).text
            assert "imports-notice" not in html, path
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
@pytest.mark.parametrize("use_worker", (True, False))
async def test_loader_notes_reach_people(tmp_path, use_worker):
    # A JSON loader's note about which part of the document held the rows:
    # on the import, in /-/imports.json, on the terminal and on the page of
    # the table, once it is complete
    note = "Importing the 2 objects in 'results', ignoring 'next', 'total'"
    path = tmp_path / "api.json"
    path.write_text(json.dumps({"next": None, "total": 2, "results": [{"a": 1}] * 2}))
    ds, db = _scratch_ds()
    registry = import_registry(ds)
    stream = io.StringIO()
    registry.add_listener(TerminalReporter(stream))
    try:
        job = registry.start(db, _source(path), use_worker=use_worker)
        await ds.start_background_tasks()
        assert await job.wait(20)
        assert (job.state, job.rows, job.notes) == ("done", 2, [note])
        data = (await ds.client.get("/-/imports.json")).json()["imports"][0]
        assert data["notes"] == [note]
        lines = stream.getvalue().splitlines()
        loaded = next(i for i, line in enumerate(lines) if line.startswith("Loaded"))
        assert lines[loaded + 1] == f"  note: {note}"
        page = (await ds.client.get("/scratch/api")).text
        assert "Notes from importing this table:" in page
        assert markupsafe.escape(note) in page
        # A complete table without notes has no notice at all
        assert "imports-notice" not in (await ds.client.get("/scratch")).text
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_loader_notes_in_progress_and_incomplete_notices(tmp_path):
    note = "Reading quotes as ordinary characters & <friends>"
    ds, db = _scratch_ds()
    stream = io.StringIO()
    import_registry(ds).add_listener(TerminalReporter(stream))
    try:
        job = await _start_stalled(ds, db, tmp_path, note=note)
        await _wait_for(lambda: job.notes == [note])
        assert (await ds.client.get("/-/imports.json")).json()["imports"][0][
            "notes"
        ] == [note]
        expected = f'<ul class="import-notes"><li>{markupsafe.escape(note)}</li></ul>'
        for path in ("/", "/scratch", "/scratch/slow"):
            page = (await ds.client.get(path)).text
            assert expected in page, path
            # The polling script keeps them up to date
            assert "import-notes" in page.split("<script>", 1)[1], path
        job.cancel()
        assert await job.wait(10)
        # Still there once the import is incomplete
        assert expected in (await ds.client.get("/scratch/slow")).text
        lines = stream.getvalue().splitlines()
        cancelled = next(
            i for i, line in enumerate(lines) if line.startswith("Cancelled")
        )
        assert lines[cancelled + 1] == f"  note: {note}"
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
        # Nothing is left incomplete, so the shared pages say nothing about
        # it; /-/imports does
        for path in ("/", "/scratch"):
            assert "imports-notice" not in (await ds.client.get(path)).text
        assert "import failed" in (await ds.client.get("/-/imports")).text
    finally:
        await ds.invoke_shutdown()


@pytest.mark.parametrize("background", (True, False))
def test_scratch_page_hides_tables_the_actor_cannot_view(tmp_path, background):
    # The "Loaded:" list names only tables the actor may view, and the
    # --save command - which names every input - is only shown to an actor
    # who may view all of them
    (tmp_path / "dogs.csv").write_text(CSV)
    (tmp_path / "secret_plans.csv").write_text(CSV)
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "databases": {
                    "scratch": {"tables": {"secret_plans": {"allow": {"id": "root"}}}}
                }
            }
        )
    )
    args = [str(tmp_path / "dogs.csv"), str(tmp_path / "secret_plans.csv")]
    ds = ds_from_cli([*args, "--config", str(config)], _background_imports=background)

    async def check():
        try:
            if background:
                await ds.client.get("/-/imports.json")
                assert await import_registry(ds).wait(20)
            anonymous = (await ds.client.get("/scratch")).text
            banner = anonymous.split('<div class="scratch-save"', 1)[1]
            banner = banner.split("</div>", 1)[0]
            assert 'href="/scratch/dogs"' in banner
            assert "secret_plans" not in banner
            assert "scratch-save-command" not in banner
            # (The SQL editor's autocomplete lists every table, as it does
            # for any database: anyone who can run SQL can list them)
            assert "secret_plans.csv" not in anonymous
            assert 'href="/scratch/secret_plans"' not in anonymous
            root = (await ds.client.get("/scratch", actor=ROOT)).text
            assert 'href="/scratch/secret_plans"' in root
            assert "secret_plans.csv --save data.db" in root
        finally:
            await ds.invoke_shutdown()

    asyncio.run(check())


@pytest.mark.asyncio
async def test_shared_pages_list_the_newest_ten_finished_imports(tmp_path):
    ds, db = _scratch_ds()
    registry = import_registry(ds)
    try:
        jobs = []
        for i in range(12):
            path = tmp_path / f"bad_{i}.ndjson"
            path.write_text('{"a": 1}\n{oops\n')
            jobs.append(registry.start(db, _source(path), use_worker=False))
        await ds.start_background_tasks()
        assert await registry.wait(20)
        assert {job.state for job in jobs} == {"failed"}
        for path in ("/", "/scratch"):
            page = (await ds.client.get(path)).text
            listed = [job.table for job in jobs if f">{job.table}</a></span>:" in page]
            assert listed == [f"bad_{i}" for i in range(2, 12)], path
            assert "2 older finished imports not listed here" in page
            assert '<a href="/-/imports">all imports</a>' in page
        page = (await ds.client.get("/-/imports")).text
        assert all(f">{job.table}</a></span>:" in page for job in jobs)
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
        await asyncio.sleep(0.6)
        slots.release()
        slots.release()
        assert await registry.wait(20)
        assert [job.state for job in jobs] == ["done", "done"]
        # Time queued for a worker is not import time
        for job in jobs:
            assert job.waited_seconds >= 0.5
            assert job.seconds < job.waited_seconds
            assert job.to_dict()["waited_seconds"] >= 0.5
            line = TerminalReporter().finished_line(job)
            assert line.endswith(
                f"in {job.seconds:.2f}s, after waiting "
                f"{job.waited_seconds:.1f}s for an import worker"
            )
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_imports_start_in_the_order_given(tmp_path):
    ds, db = _scratch_ds()
    # One slot, so the order they get it in is the order they run in
    worker_slots(ds, max_workers=1)
    registry = import_registry(ds)
    started = []
    registry.add_listener(
        lambda job, event: started.append(job.table) if event == "importing" else None
    )
    try:
        names = ["t1", "t2", "t3", "t4"]
        for name in names:
            (tmp_path / f"{name}.csv").write_text(CSV)
            registry.start(db, _source(tmp_path / f"{name}.csv"))
        await ds.start_background_tasks()
        assert await registry.wait(20)
        assert started == names
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_worker", (True, False))
async def test_finishing_progress(tmp_path, monkeypatch, use_worker):
    # A column changes to text in the last chunk: once every row is in,
    # the import says how many rows it still has to convert - in its
    # status, /-/imports.json, its task's status and on the terminal
    monkeypatch.setattr(TypedInserter, "_convert_rows", 100)
    path = tmp_path / "late.csv"
    path.write_text("n\n" + "".join(f"{i}\n" for i in range(2999)) + "x\n")
    ds, db = _scratch_ds()
    registry = import_registry(ds)
    seen = []

    def listener(job, event):
        if job.converting:
            seen.append((job.status(), job.describe(), job.task.status))

    registry.add_listener(listener)
    stream = io.StringIO()
    registry.add_listener(TerminalReporter(stream, interval=3600))
    try:
        job = registry.start(db, _source(path), use_worker=use_worker)
        await ds.start_background_tasks()
        assert await job.wait(30)
        assert (job.state, job.rows) == ("done", 3000)
        # The chunk of rows 2,001-3,000 widened n, converting 100 of the
        # 2,000 rows before it; then each write converts 100 more
        assert seen[0] == (
            "finishing: 1,800 rows to convert",
            "finishing late: 1,800 rows to convert",
            "finishing late: 1,800 rows to convert",
        )
        assert [status for status, _, _ in seen][-1] == (
            "finishing: 100 rows to convert"
        )
        assert len(seen) == 18
        lines = stream.getvalue().splitlines()
        assert (
            "  finishing late: 1,800 rows to convert (a column changed to text "
            "after the first rows)"
        ) in lines
        # Once done, nothing is left to convert
        data = job.to_dict()
        assert (data["finishing"], data["backlog"]) == (False, 0)
        assert data["status"] == "imported 3,000 rows"
        values = await db.execute("select typeof(n), count(*) from late group by 1")
        assert [tuple(row) for row in values] == [("text", 3000)]
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_finishing_shows_in_imports_json_and_notices(tmp_path):
    ds, db = _scratch_ds()
    try:
        job = await _start_stalled(ds, db, tmp_path)
        # As the progress from an import that is converting its backlog
        job.finishing, job.backlog = True, 1_234_567
        assert job.converting
        data = (await ds.client.get("/-/imports.json")).json()["imports"][0]
        assert (data["finishing"], data["backlog"]) == (True, 1_234_567)
        assert data["status"] == "finishing: 1,234,567 rows to convert"
        assert data["description"] == "finishing slow: 1,234,567 rows to convert"
        for path in ("/", "/scratch", "/scratch/slow"):
            html = (await ds.client.get(path)).text
            assert "finishing: 1,234,567 rows to convert" in html, path
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
        # So does /-/tasks, for the import's background task
        tasks = (await ds.client.get("/-/tasks.json", actor={"id": "root"})).json()
        [task] = [t for t in tasks["tasks"] if t["name"] == "import scratch.slow"]
        assert task["state"] == "running"
        assert task["status"] == "importing slow: 40% (4 rows)"
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


@pytest.mark.parametrize(
    "next_url,expected",
    [
        ("/scratch?_sort=id", "/scratch?_sort=id"),
        ("/", "/"),
        ("//evil.example/", "/scratch/slow"),
        ("/\\evil.example/", "/scratch/slow"),
        # Browsers drop tabs and newlines before parsing a URL
        ("/\t/evil.example/x", "/scratch/slow"),
        ("/\n/evil.example/x", "/scratch/slow"),
        ("/\r\n/evil.example", "/scratch/slow"),
        ("/ /evil.example", "/scratch/slow"),
        ("/\x00/evil.example", "/scratch/slow"),
        ("https://evil.example/", "/scratch/slow"),
        ("evil.example", "/scratch/slow"),
        ("", "/scratch/slow"),
        (None, "/scratch/slow"),
    ],
)
def test_cancel_redirect_rejects_other_sites(next_url, expected):
    from datasette.imports.default_hooks import _safe_next

    class Job:
        database = "scratch"
        table = "slow"

    ds = Datasette()
    ds.add_memory_database("scratch")
    assert _safe_next(ds, next_url, Job()) == expected


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
async def test_a_token_limited_to_cancel_import_can_cancel(tmp_path):
    # What `datasette create-token root --all cancel-import` makes: it may
    # cancel, as an insert-row token may insert, but it is not shown what
    # the import is - that takes view-table too
    ds, db = _scratch_ds()

    async def _signed_token(restrictions):
        # Datasette's own tokens, which carry the restrictions - not those of
        # a token handler plugin another test may have left registered
        return await ds.create_token(
            "root", restrictions=restrictions, handler="signed"
        )

    try:
        job = await _start_stalled(ds, db, tmp_path)
        token = await _signed_token(TokenRestrictions().allow_all("cancel-import"))
        headers = {"Authorization": f"Bearer {token}"}
        # It cannot list imports: that takes view-instance and view-table
        response = await ds.client.get("/-/imports.json", headers=headers)
        assert response.status_code == 403
        response = await ds.client.post(
            "/scratch/slow/-/cancel-import", json={}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True}
        assert job.state == "cancelled"

        # With view-table (and view-instance, to list imports) it sees them
        job2 = await _start_stalled(ds, db, tmp_path, table="slow2")
        restrictions = TokenRestrictions()
        for action in ("cancel-import", "view-table", "view-instance"):
            restrictions = restrictions.allow_all(action)
        token = await _signed_token(restrictions)
        headers = {"Authorization": f"Bearer {token}"}
        listed = (await ds.client.get("/-/imports.json", headers=headers)).json()
        assert [i["table"] for i in listed["imports"]] == ["slow", "slow2"]
        response = await ds.client.post(
            "/scratch/slow2/-/cancel-import", json={}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["import"]["state"] == "cancelled"
        assert job2.state == "cancelled"
        # Without cancel-import: 403 if it can see the import, 404 if not
        token = await _signed_token(TokenRestrictions().allow_all("view-table"))
        job3 = await _start_stalled(ds, db, tmp_path, table="slow3")
        response = await ds.client.post(
            "/scratch/slow3/-/cancel-import",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        assert job3.active
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_cancel_with_drop_removes_the_partial_table(tmp_path):
    # As for a --save file: a cancelled import leaves nothing behind
    from datasette.database import Database

    sqlite3.connect(tmp_path / "save.db").execute("vacuum")
    ds = Datasette()
    db = ds.add_database(
        Database(ds, path=str(tmp_path / "save.db"), is_mutable=True), name="save"
    )
    try:
        job = await _start_stalled(ds, db, tmp_path, on_error="drop")
        assert await db.table_names() == ["slow"]
        assert job.cancel()
        assert await job.wait(5)
        assert job.state == "cancelled"
        assert not job.table_exists and job.rows_kept == 0
        assert job.status() == "import cancelled (the table was removed)"
        assert await db.table_names() == []
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ("a]b", 'a"b', "[x]"))
async def test_cancel_counts_the_rows_kept_whatever_the_table_name(tmp_path, table):
    # [name] quoting has no escape for "]": the count after a cancellation
    # failed, silently, and kept the rows last reported
    ds, db = _scratch_ds()
    try:
        job = await _start_stalled(ds, db, tmp_path, table=table)
        with import_writes():
            await db.execute_write(
                f"insert into {escape_sqlite(table)} (n) values (100), (101)"
            )
        assert job.cancel()
        assert await job.wait(5)
        assert job.state == "cancelled"
        assert job.table_exists and job.rows_kept == 6
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
        # The script reloads this page once the table exists, and rewrites
        # the sentence before the list once the import has finished
        notice = response.text.split('<div class="imports-notice', 1)[1]
        notice = notice.split(">", 1)[0]
        assert "data-pending-table" in notice
        assert "data-link-tables" not in notice
        assert '<p class="import-intro">This table is being imported' in (
            response.text
        )
        assert "window.location.reload()" in response.text
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "on_error,outcome",
    (
        ("keep", "The rows imported so far will be kept, and the table marked"),
        ("drop", "The rows imported so far will be removed."),
    ),
)
async def test_cancel_button_asks_first(tmp_path, on_error, outcome):
    ds, db = _scratch_ds()
    try:
        await _start_stalled(ds, db, tmp_path, table='a"<b>', on_error=on_error)
        page = (await ds.client.get("/", actor=ROOT)).text
        form = page.split('<form class="import-cancel"', 1)[1].split(">", 1)[0]
        question = form.split('data-confirm="', 1)[1].split('"', 1)[0]
        assert (
            markupsafe.Markup(question)
            .unescape()
            .startswith('Stop importing a"<b>? ' + outcome)
        )
        # Asked by the script, not by inline JavaScript
        assert "onsubmit" not in form
        assert 'matches("form.import-cancel[data-confirm]")' in page
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_imports_into_two_databases_are_told_apart(tmp_path):
    ds, db = _scratch_ds()
    other = ds.add_database(
        Database(ds, path=str(tmp_path / "data.db"), is_mutable=True), name="data"
    )
    sqlite3.connect(tmp_path / "data.db").execute("vacuum").connection.close()
    try:
        (tmp_path / "events.csv").write_text(CSV)
        registry = import_registry(ds)
        registry.start(db, _source(tmp_path / "events.csv"))
        registry.start(
            other, _source(tmp_path / "events.csv"), title="uploaded events.csv"
        )
        await ds.start_background_tasks()
        assert await registry.wait(20)
        page = (await ds.client.get("/-/imports")).text
        assert '<a href="/scratch/events">scratch / events</a>' in page
        assert '<a href="/data/events">data / events</a>' in page
        # And where each came from
        assert f"(from {tmp_path / 'events.csv'})" in page
        assert "(from uploaded events.csv)" in page
        # One database: just the table name, as before
        page = (await ds.client.get("/-/imports?database=data")).text
        assert '<a href="/data/events">events</a>' in page
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_a_request_that_lost_the_race_with_the_first_rows(tmp_path):
    """
    resolve_table() looks for the table in sqlite_master. A request that
    looked just before the first chunk was committed gets TableNotFound;
    by the time the 404 is made the table is there. A page is redirected to
    itself, so the browser shows the table; JSON says to try again.
    """
    from datasette.imports.default_hooks import handle_exception
    from datasette.utils.asgi import Request, TableNotFound

    ds, db = _scratch_ds()
    try:
        await _start_stalled(ds, db, tmp_path)

        async def not_found(path):
            inner = handle_exception(
                datasette=ds,
                request=Request.fake(path),
                exception=TableNotFound("scratch", "slow"),
            )
            return await inner()

        response = await not_found("/scratch/slow?_sort=n")
        assert response.status == 307
        assert response.headers["Location"] == "/scratch/slow?_sort=n"
        response = await not_found("/scratch/slow.json")
        assert response.status == 404
        data = json.loads(response.body)
        assert data["error"] == (
            "Table slow was created while this request was being answered: " "try again"
        )
        assert data["import"]["state"] == "importing"
        # The same table under another case is an ordinary 404 (Datasette
        # compares table names exactly): no redirect, which would loop
        inner = handle_exception(
            datasette=ds,
            request=Request.fake("/scratch/SLOW"),
            exception=TableNotFound("scratch", "SLOW"),
        )
        assert await inner() is None
        response = await ds.client.get("/scratch/SLOW")
        assert response.status_code == 404
    finally:
        await ds.invoke_shutdown()


@pytest.mark.asyncio
async def test_the_catalog_lists_a_new_table_without_a_request(tmp_path):
    """
    The homepage and database page list tables from Datasette's catalog.
    The import refreshes it when its first rows land - and tries again if
    another refresh was running then, because that one may have read the
    schema before the table existed.
    """
    ds, db = _scratch_ds()
    internal = ds.get_internal_database()

    async def listed():
        sql = (
            "select 1 from catalog_tables where database_name = 'scratch' "
            "and table_name = 'slow'"
        )
        return (await internal.execute(sql)).first() is not None

    try:
        await ds.invoke_startup()
        await ds.refresh_schemas(force=True)
        # Another refresh is running
        await ds._refresh_schemas_lock.acquire()
        try:
            await _start_stalled(ds, db, tmp_path)
            await asyncio.sleep(0.2)
            assert not await listed()
        finally:
            ds._refresh_schemas_lock.release()
        deadline = time.monotonic() + 2
        while not await listed():
            assert time.monotonic() < deadline, "the catalog never listed it"
            await asyncio.sleep(0.02)
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


class _FailingHandler(http.server.BaseHTTPRequestHandler):
    "404 for /missing.csv, bytes no loader recognizes for anything else."

    protocol_version = "HTTP/1.0"

    def do_GET(self):
        if self.path.startswith("/missing.csv"):
            self.send_error(404, "File not found")
            return
        body = bytes(range(256)) * 8
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.mark.asyncio
async def test_failed_downloads_never_show_url_credentials(capsys):
    # The error of an import that failed - which every page that lists the
    # import shows, to anyone who can see the table - must not repeat the
    # password or token in its URL: not for an HTTP error, a connection
    # that failed or content no loader recognizes
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FailingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = f"127.0.0.1:{server.server_address[1]}"
    urls = [
        f"http://alice:hunter2@{host}/missing.csv?api_key=SEKRIT1&x=1",
        f"http://u:pw5@{host}/binary.dat",
        f"http://bob:pw3@127.0.0.1:{_free_port()}/refused.csv?access_token=SEKRIT3",
    ]
    secrets = ["hunter2", "SEKRIT1", "pw5", "pw3", "SEKRIT3"]
    ds = ds_from_cli(urls, _background_imports=True)
    try:
        registry = import_registry(ds)
        await ds.client.get("/-/imports.json")
        assert await registry.wait(20)
        jobs = registry.imports()
        assert [job.state for job in jobs] == ["failed"] * 3
        assert jobs[0].error == (
            f"http://alice:REDACTED@{host}/missing.csv?api_key=REDACTED&x=1 "
            "returned HTTP 404 File not found"
        )
        assert jobs[1].error.startswith(
            f"Could not load http://u:REDACTED@{host}/binary.dat: "
        )
        assert jobs[2].error.startswith("Could not download http://bob:REDACTED@")
        for path in (
            "/",
            "/scratch",
            "/scratch/missing",
            "/scratch/binary",
            "/scratch/refused",
            "/scratch/missing.json",
            "/-/imports",
            "/-/imports.json",
        ):
            text = (await ds.client.get(path)).text
            assert "REDACTED" in text, path
            for secret in secrets:
                assert secret not in text, (path, secret)
        stderr = capsys.readouterr().err
        assert "Failed to import http://alice:REDACTED@" in stderr
        for secret in secrets:
            assert secret not in stderr
    finally:
        await ds.invoke_shutdown()
        server.shutdown()


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
        _background_imports=True,
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


@pytest.mark.asyncio
async def test_cli_reporter_leaves_out_uploads(data_dir, capsys):
    # The command line reports on its own inputs: an upload into another
    # database used to be printed as going "in save.db", and counted in the
    # command line's summary
    other = data_dir / "other.db"
    sqlite3.connect(other).execute("vacuum").connection.close()
    (data_dir / "one.csv").write_text("id\n1\n")
    ds = ds_from_cli(
        [
            str(other),
            str(data_dir / "dogs.csv"),
            str(data_dir / "one.csv"),
            "--save",
            str(data_dir / "s.db"),
        ],
        _background_imports=True,
    )
    ds.root_enabled = True
    try:
        registry = import_registry(ds)
        await ds.client.get("/-/imports.json")
        assert await registry.wait(20)
        response = await ds.client.post(
            "/other/-/upload",
            files={"file": ("events.csv", CSV.encode(), "text/csv")},
            actor=ROOT,
        )
        assert response.status_code == 202, response.text
        assert await registry.wait(20)
        assert [job.state for job in registry.imports()] == ["done"] * 3
        stderr = capsys.readouterr().err
        assert "Imported 2 of 2 inputs (4 rows)" in stderr
        assert "events" not in stderr
        assert "3 of 3" not in stderr
        # One row, not "1 rows"
        assert f"Loaded 1 row from {data_dir / 'one.csv'}" in stderr
    finally:
        await ds.invoke_shutdown()


def test_terminal_reporter_summary_says_row_for_one_row():
    class Registry:
        datasette = None

        def __init__(self, jobs):
            self.jobs = jobs

        def imports(self):
            return self.jobs

    class Job:
        table_exists = True
        title = "a.csv"
        loader = "csv"
        seconds = 0.1
        waited_seconds = 0
        widenings = notes = ()
        kind = "file"
        state = "done"
        active = False

        def __init__(self, id, rows, table):
            self.id, self.rows, self.table = id, rows, table

    jobs = [Job("1", 1, "a"), Job("2", 0, "b")]
    registry = Registry(jobs)
    stream = io.StringIO()
    reporter = TerminalReporter(stream)
    for job in jobs:
        job.registry = registry
        reporter(job, "queued")
    for job in jobs:
        reporter(job, "done")
    assert "Imported 2 of 2 inputs (1 row) in" in stream.getvalue()


@contextlib.contextmanager
def no_logging_configured():
    """
    No handler on the root logger, as in ``datasette serve``, so warnings
    go to Python's last-resort handler. Use it inside the test: pytest's
    logging plugin adds its handlers after the fixtures are set up.
    """
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()
    try:
        yield
    finally:
        root.handlers[:] = saved


@pytest.mark.asyncio
async def test_cli_prints_each_note_once(tmp_path, capsys):
    path = tmp_path / "api.json"
    path.write_text(json.dumps({"total": 2, "results": [{"a": 1}, {"a": 2}]}))
    ds = ds_from_cli([str(path)], _background_imports=True)
    try:
        with no_logging_configured():
            await ds.client.get("/-/imports.json")
            assert await import_registry(ds).wait(20)
        stderr = capsys.readouterr().err
        lines = stderr.splitlines()
        loaded = next(i for i, line in enumerate(lines) if line.startswith("Loaded"))
        note = "Importing the 2 objects in 'results', ignoring 'total'"
        assert lines[loaded + 1] == f"  note: {note}"
        # Not also as "Import worker: ..." from the worker's log message
        assert stderr.count(note) == 1
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
            ds_from_cli(args, _background_imports=True)
        assert message in ex.value.message


@pytest.mark.asyncio
async def test_cli_save_drops_failed_tables_and_uses_wal(data_dir):
    save = data_dir / "data.db"
    ds = ds_from_cli(
        [str(data_dir / "dogs.csv"), str(data_dir / "bad.ndjson"), "--save", str(save)],
        _background_imports=True,
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
async def test_cli_save_file_with_nothing_imported_is_removed_at_shutdown(
    data_dir, capsys
):
    # Every input failed: the file this run created is not left behind
    # empty, as it was not when loading before serving failed
    save = data_dir / "fail.db"
    ds = ds_from_cli(
        [str(data_dir / "bad.ndjson"), "--save", str(save)], _background_imports=True
    )
    try:
        await ds.client.get("/")
        assert await import_registry(ds).wait(20)
        assert import_registry(ds).get("fail", "bad").state == "failed"
        # Still there while Datasette serves it
        assert save.exists()
    finally:
        await ds.invoke_shutdown()
    assert sorted(p.name for p in data_dir.iterdir() if p.name.startswith("fail")) == []
    assert f"Removed {save}: nothing was imported into it" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_cli_save_file_is_checkpointed_at_shutdown(data_dir):
    # A --save file this run creates is in WAL mode, and Datasette's read
    # connections are read-only: when the last of them closed, SQLite could
    # not checkpoint it, so every row stayed in the -wal file and a copy of
    # just the .db file had none
    save = data_dir / "s.db"
    ds = ds_from_cli(
        [str(data_dir / "dogs.csv"), "--save", str(save)], _background_imports=True
    )
    try:
        await ds.client.get("/")
        assert await import_registry(ds).wait(20)
        rows = (await ds.client.get("/s/dogs.json?_shape=array")).json()
        assert len(rows) == 3
        assert (data_dir / "s.db-wal").exists()
    finally:
        await ds.invoke_shutdown()
    assert sorted(p.name for p in data_dir.iterdir() if p.name.startswith("s.")) == [
        "s.db"
    ]
    copy = data_dir / "copy.db"
    copy.write_bytes(save.read_bytes())
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("select count(*) from dogs").fetchone() == (3,)
    finally:
        conn.close()


def test_reload_gives_a_worker_with_inputs_time_to_stop(data_dir, monkeypatch):
    import hupper

    calls = []

    class FakeReloader:
        def watch_files(self, files):
            pass

    def start_reloader(*args, **kwargs):
        calls.append(kwargs)
        return FakeReloader()

    monkeypatch.setattr(hupper, "start_reloader", start_reloader)
    monkeypatch.setattr(hupper, "is_active", lambda: True)
    monkeypatch.delenv(ReloadSession.ENV, raising=False)
    ds_from_cli([str(data_dir / "dogs.csv"), "--reload"]).close()
    ds_from_cli(["--reload"]).close()
    # hupper's default - a second - for a worker with nothing to import
    assert calls == [{"shutdown_interval": 10}, {}]


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
        ds = ds_from_cli(args, _background_imports=True)
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
        ds = ds_from_cli(args, _background_imports=True)
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
    class Job:
        id = "1"
        table = "big"
        title = "big.csv"
        kind = "file"
        state = "importing"
        seconds = 12.0
        download_size = downloaded = None
        converting = False

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


def _control_characters(text):
    return [
        ch
        for ch in text
        if ch != "\n" and unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp")
    ]


@pytest.mark.asyncio
async def test_terminal_lines_have_no_control_characters(tmp_path):
    # Table and column names, notes and errors can come from a file (a URL
    # on the command line) or an upload: printed raw, ESC ] 0 ; ... BEL
    # sets the terminal's title, CSI 2 J clears it, a newline fakes a log
    # line
    key = "\x1b]0;OWNED\x07col\nINFO: fake"
    path = tmp_path / "evil.json"
    path.write_text(
        json.dumps(
            {
                "\x1b[2Jresults": [{key: 1}, {key: 2}, {key: "a\x1b[31m"}],
                "total": 3,
            }
        )
    )
    ds, db = _scratch_ds()
    stream = io.StringIO()
    registry = import_registry(ds)
    registry.add_listener(TerminalReporter(stream, interval=0))
    try:
        job = registry.start(
            db, _source(path, table="t\x1b]0;PWNED\x07\x9b2J\u202eabc"), sample_size=1
        )
        await ds.start_background_tasks()
        assert await job.wait(20)
        assert job.state == "done", job.error
        assert job.notes and job.widenings
        output = stream.getvalue()
        assert _control_characters(output) == []
        assert "INFO: fake" in output
        assert not any(line.startswith("INFO") for line in output.splitlines())
        assert "column ]0;OWNEDcol INFO: fake changed from integer to text" in output
        assert "into table t]0;PWNED2Jabc" in output
        # A failure's error too
        job.state, job.error = "failed", "Line 2:\x1b[2J\nboom"
        assert _control_characters(TerminalReporter().finished_line(job)) == []
    finally:
        await ds.invoke_shutdown()


def test_get_lines_have_no_control_characters():
    from datasette.cli import _describe_loaded
    from datasette.imports.scratch import LoadedTable
    from datasette.imports.sources import StatusLine

    source = _source("evil\x1b[2J.json", table="t\x1b]0;PWNED\x07")
    loaded = LoadedTable(
        source=source,
        table=source.table,
        loader="json",
        rows=2,
        seconds=0.1,
        notes=["Importing the 2 objects in '\x1b[2Jresults'\nINFO: fake"],
    )
    stream = io.StringIO()
    status = StatusLine(stream)
    status.start(f"Loading {source.label}...")
    status.done(_describe_loaded(loaded))
    output = stream.getvalue()
    assert _control_characters(output) == []
    assert len(output.splitlines()) == 3


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
        if process.stdin.closed:
            # communicate() flushes stdin, which before Python 3.12 fails
            # with "flush of closed file" if it was closed already
            process.stdin = None
        try:
            _, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            raise
    assert b"Loaded 2 rows from standard input (csv) into table stdin" in stderr
    # The scratch database and the copy of standard input are gone
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_ctrl_c_while_reading_stdin(tmp_path, sig):
    """
    Standard input that never ends does not stop Ctrl+C - or SIGTERM,
    after which the process ends without running atexit functions, so the
    shutdown itself must leave nothing in TMPDIR.
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
            with contextlib.suppress(OSError):
                _, body = _get(f"http://127.0.0.1:{port}/-/imports.json", timeout=1)
                if json.loads(body)["imports"][0]["downloaded"]:
                    break
            assert time.monotonic() - start < 20
            time.sleep(0.05)
        # The scratch database, and the directory with the import's copy of
        # standard input
        names = [p.name for p in tmp_path.iterdir()]
        assert any(name.startswith("datasette-scratch-") for name in names)
        assert any(name.startswith("datasette-import-") for name in names)
        process.send_signal(sig)
        _, stderr = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
    assert (
        b"Stopped importing standard input into table stdin, because Datasette "
        b"is shutting down" in stderr
    )
    assert list(tmp_path.iterdir()) == []
