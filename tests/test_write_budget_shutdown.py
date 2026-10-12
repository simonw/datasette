import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from datasette.app import Datasette
from datasette.database import Database, DatasetteClosedError
from datasette.write_budget import RETIRE_WRITER, DatabaseAdmissionTimeout


def make_databases(tmp_path, count=2, **settings):
    paths = []
    for i in range(count):
        path = tmp_path / f"shutdown{i}.db"
        with sqlite3.connect(path) as conn:
            conn.execute("create table t(id)")
        conn.close()
        paths.append(str(path))
    ds = Datasette(paths, settings={"schema_watch_interval_ms": 0, **settings})
    return ds, [ds.get_database(f"shutdown{i}") for i in range(count)]


async def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.005)


def rows(db):
    with sqlite3.connect(db.path) as conn:
        result = conn.execute("select id from t order by rowid").fetchall()
    conn.close()
    return [row[0] for row in result]


@pytest.mark.asyncio
@pytest.mark.parametrize("retirement_requested", [False, True])
async def test_close_drains_writes_before_worker_admission(
    tmp_path, monkeypatch, retirement_requested
):
    ds, (db, _) = make_databases(tmp_path, max_write_connections=1)
    admitted = threading.Event()
    release = threading.Event()
    run = ds._write_budget._run

    def paused_scheduler():
        admitted.set()
        assert release.wait(5)
        run()

    monkeypatch.setattr(ds._write_budget, "_run", paused_scheduler)
    replies = []
    closing = None
    try:
        for i in range(20):
            _, reply = await db._send_to_write_thread(
                lambda conn, i=i: conn.execute("insert into t values (?)", [i]),
                block=False,
            )
            replies.append(reply)
        assert admitted.wait(1)
        assert db._write_thread is None
        if retirement_requested:
            # A previous retirement request can precede tasks accepted later.
            with db._write_queue.mutex:
                db._write_queue.queue.appendleft(RETIRE_WRITER)
        closing = asyncio.create_task(asyncio.to_thread(db.close))
        await wait_for(lambda: db._closed)
        release.set()
        await asyncio.wait_for(closing, 5)
        await asyncio.gather(*replies)
        assert rows(db) == list(range(20))
        assert ds._write_budget.snapshot()["writers"] == 0
        assert ds._write_budget.snapshot()["stats"]["peak_writers"] == 1
        with pytest.raises(DatasetteClosedError):
            await db.execute_write("insert into t values (21)")
    finally:
        release.set()
        if closing is not None:
            await closing
        await asyncio.gather(*replies, return_exceptions=True)
        ds.close()


@pytest.mark.asyncio
async def test_close_waits_for_budget_capacity_then_drains(tmp_path):
    ds, (first, waiting) = make_databases(tmp_path, max_write_connections=1)
    entered, release = threading.Event(), threading.Event()

    def slow(conn):
        entered.set()
        assert release.wait(5)

    _, first_reply = await first._send_to_write_thread(slow, block=False)
    closing = None
    waiting_reply = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        _, waiting_reply = await waiting._send_to_write_thread(
            lambda conn: conn.execute("insert into t values (1)"), block=False
        )
        closing = asyncio.create_task(asyncio.to_thread(waiting.close))
        await wait_for(lambda: waiting._closed)
        assert ds._write_budget.snapshot()["pending"] == 1
        release.set()
        await asyncio.wait_for(closing, 5)
        await asyncio.gather(first_reply, waiting_reply)
        assert rows(waiting) == [1]
        assert ds._write_budget.snapshot()["stats"]["peak_writers"] == 1
    finally:
        release.set()
        if closing is not None:
            await closing
        await asyncio.gather(
            first_reply,
            *([waiting_reply] if waiting_reply else []),
            return_exceptions=True,
        )
        ds.close()


@pytest.mark.asyncio
async def test_close_preserves_admission_deadline_while_capacity_pinned(tmp_path):
    ds, (waiting, _) = make_databases(
        tmp_path, max_write_connections=1, write_queue_timeout_ms=50
    )
    memory = ds.add_memory_database("pinned")
    ran = []
    try:
        await memory.execute_write("create table t(id)")
        _, reply = await waiting._send_to_write_thread(
            lambda conn: ran.append(True), block=False
        )
        await asyncio.wait_for(asyncio.to_thread(waiting.close), 2)
        with pytest.raises(DatabaseAdmissionTimeout):
            await reply
        assert not ran
        assert ds._write_budget.snapshot()["pending"] == 0
        assert ds._write_budget.snapshot()["stats"]["peak_writers"] == 1
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_datasette_shutdown_releases_later_memory_writer_before_drain(tmp_path):
    ds, (waiting, _) = make_databases(
        tmp_path, max_write_connections=1, write_queue_timeout_ms=1000
    )
    memory = ds.add_memory_database("pinned")
    try:
        await memory.execute_write("create table t(id)")
        _, reply = await waiting._send_to_write_thread(
            lambda conn: conn.execute("insert into t values (1)"), block=False
        )
        await asyncio.wait_for(asyncio.to_thread(ds.close), 5)
        await reply
        assert rows(waiting) == [1]
        assert ds._write_budget.snapshot()["writers"] == 0
        assert ds._write_budget.snapshot()["stats"]["peak_writers"] == 1
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("database_kind", ["file", "temporary", "internal"])
async def test_close_timeout_preserves_running_connection(
    tmp_path, monkeypatch, database_kind
):
    import datasette.database

    monkeypatch.setattr(datasette.database, "WRITE_SHUTDOWN_TIMEOUT", 0.05)
    ds, (db, _) = make_databases(tmp_path, max_write_connections=1)
    if database_kind == "temporary":
        db = Database(ds, is_temp_disk=True)
        ds.add_database(db, name="temporary")
        await db.execute_write("create table t(id)")
    elif database_kind == "internal":
        db = ds._internal_database
        await db.execute_write("create table t(id)")
    entered, release = threading.Event(), threading.Event()

    def slow(conn):
        entered.set()
        assert release.wait(5)
        conn.execute("insert into t values (1)")
        return conn.execute("select id from t").fetchall()[0][0]

    _, running = await db._send_to_write_thread(slow, block=False)
    queued = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if database_kind != "internal":
            _, queued = await db._send_to_write_thread(
                lambda conn: pytest.fail("cancelled callback ran"), block=False
            )
        await asyncio.wait_for(asyncio.to_thread(db.close), 2)
        if queued is not None:
            with pytest.raises(DatasetteClosedError):
                await queued
        assert Path(db.path).exists()
        assert not running.done()
        release.set()
        assert await running == 1
        if database_kind == "file":
            assert rows(db) == [1]
        else:
            await wait_for(lambda: not Path(db.path).exists())
        await wait_for(lambda: db._write_thread is None)
    finally:
        release.set()
        await asyncio.gather(
            running, *([queued] if queued else []), return_exceptions=True
        )
        ds.close()


@pytest.mark.asyncio
async def test_close_timeout_during_write_connection_open(tmp_path, monkeypatch):
    import datasette.database

    monkeypatch.setattr(datasette.database, "WRITE_SHUTDOWN_TIMEOUT", 0.05)
    ds, (db, _) = make_databases(tmp_path)
    opened, release = threading.Event(), threading.Event()
    connect = db.connect

    def paused_connect(**kwargs):
        conn = connect(**kwargs)
        opened.set()
        assert release.wait(5)
        return conn

    monkeypatch.setattr(db, "connect", paused_connect)
    _, reply = await db._send_to_write_thread(
        lambda conn: conn.execute("insert into t values (1)"), block=False
    )
    try:
        assert await asyncio.to_thread(opened.wait, 5)
        await asyncio.wait_for(asyncio.to_thread(db.close), 2)
        release.set()
        await reply
        assert rows(db) == [1]
        await wait_for(lambda: db._write_thread is None)
    finally:
        release.set()
        await asyncio.gather(reply, return_exceptions=True)
        ds.close()
