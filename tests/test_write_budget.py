import asyncio
import sqlite3
import threading
import time

import pytest

from datasette.app import Datasette
from datasette.database import DatasetteClosedError
from datasette.write_budget import (
    DatabaseAdmissionTimeout,
    DatabaseQueueFull,
    DatabaseReentrancyError,
    DatabaseResourceError,
)


def make_databases(tmp_path, count=3, **settings):
    paths = []
    for i in range(count):
        path = tmp_path / f"budget{i}.db"
        with sqlite3.connect(path) as conn:
            conn.execute("create table t(id)")
        conn.close()
        paths.append(str(path))
    ds = Datasette(paths, settings={"schema_watch_interval_ms": 0, **settings})
    return ds, [ds.get_database(f"budget{i}") for i in range(count)]


async def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_many_writers_bounded_fifo_and_fairness(tmp_path):
    ds, dbs = make_databases(
        tmp_path, 30, max_write_connections=2, max_pending_writes=200
    )
    entered, release = threading.Event(), threading.Event()
    order = []

    def slow(conn):
        entered.set()
        assert release.wait(5)

    first = asyncio.create_task(dbs[0].execute_write_fn(slow))
    tasks = []
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for seq in range(4):
            for db in dbs:

                def write(conn, name=db.name, seq=seq):
                    order.append((name, seq))
                    conn.execute("insert into t values (?)", (seq,))

                tasks.append(asyncio.create_task(db.execute_write_fn(write)))
        await asyncio.sleep(0.03)
        release.set()
        await asyncio.gather(first, *tasks)
        assert ds._write_budget.snapshot()["stats"]["peak_writers"] == 2
        for db in dbs:
            assert [seq for name, seq in order if name == db.name] == list(range(4))
            assert [
                r[0] for r in (await db.execute("select id from t order by rowid")).rows
            ] == list(range(4))
        assert order.index((dbs[-1].name, 0)) < order.index((dbs[0].name, 3))
    finally:
        release.set()
        await asyncio.gather(first, *tasks, return_exceptions=True)
        ds.close()
    await wait_for(lambda: ds._write_budget.snapshot()["writers"] == 0)


@pytest.mark.asyncio
async def test_queue_full_expiration_and_internal_progress(tmp_path):
    ds, dbs = make_databases(
        tmp_path,
        max_write_connections=1,
        max_pending_writes=1,
        write_queue_timeout_ms=100,
    )
    entered, release = threading.Event(), threading.Event()
    ran = []

    def slow(conn):
        entered.set()
        assert release.wait(5)
        conn.execute("insert into t values (1)")

    first = asyncio.create_task(dbs[0].execute_write_fn(slow))
    queued = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        queued = asyncio.create_task(
            dbs[1].execute_write_fn(lambda conn: ran.append(True))
        )
        await wait_for(lambda: ds._write_budget.snapshot()["pending"] == 1)
        with pytest.raises(DatabaseQueueFull) as exc:
            await dbs[2].execute_write_fn(lambda conn: ran.append(True))
        assert exc.value.execution_started is False
        # Internal catalog work must not compete for the single user slot.
        assert (
            await asyncio.wait_for(
                ds._internal_database.execute_write_fn(lambda conn: 42), 2
            )
            == 42
        )
        with pytest.raises(DatabaseAdmissionTimeout):
            await queued
        assert not ran
        assert dbs[1]._write_queue.qsize() == 0
        release.set()
        await first
        await dbs[2].execute_write("insert into t values (2)")
        assert ds._write_budget.snapshot()["stats"]["peak_pending"] == 1
    finally:
        release.set()
        await asyncio.gather(
            first, *([queued] if queued else []), return_exceptions=True
        )
        ds.close()


@pytest.mark.asyncio
async def test_close_waiting_database_releases_pending(tmp_path):
    ds, dbs = make_databases(tmp_path, max_write_connections=1)
    entered, release = threading.Event(), threading.Event()

    def slow(conn):
        entered.set()
        assert release.wait(5)

    first = asyncio.create_task(dbs[0].execute_write_fn(slow))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        pending = asyncio.create_task(
            dbs[1].execute_write_fn(lambda conn: pytest.fail("closed callback ran"))
        )
        await wait_for(lambda: ds._write_budget.snapshot()["pending"] == 1)
        dbs[1].close()
        with pytest.raises(DatasetteClosedError):
            await pending
        assert ds._write_budget.snapshot()["pending"] == 0
    finally:
        release.set()
        await first
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("threads", [0, 3])
async def test_memory_pins_capacity_and_recovers_after_close(tmp_path, threads):
    ds, dbs = make_databases(
        tmp_path,
        max_write_connections=1,
        write_queue_timeout_ms=50,
        num_sql_threads=threads,
    )
    memory = ds.add_memory_database("budget_memory")
    try:
        await memory.execute_write("create table t(id)")
        with pytest.raises(DatabaseResourceError):
            await dbs[0].execute_write("insert into t values (1)")
        assert (
            await memory.execute_write_fn(
                lambda conn: conn.execute("select count(*) from t").fetchone()[0]
            )
            == 0
        )
        memory.close()
        await dbs[0].execute_write("insert into t values (1)")
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("threads", [0, 3])
async def test_isolated_writer_does_not_hold_extra_file_connection(tmp_path, threads):
    ds, dbs = make_databases(tmp_path, max_write_connections=1, num_sql_threads=threads)
    try:
        for db in dbs:
            await db.execute_write("insert into t values (1)")
            assert (
                await db.execute_isolated_fn(
                    lambda conn, db=db: len(db._all_connections)
                )
                == 1
            )
        if threads == 0:
            assert sum(db._write_connection is not None for db in dbs) <= 1
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["read", "write"])
async def test_reentrant_database_call_fails_without_deadlock(tmp_path, kind):
    ds, dbs = make_databases(tmp_path, max_write_connections=1, max_open_connections=1)
    try:

        def callback(conn):
            return asyncio.run(dbs[1].execute_write_fn(lambda conn: 1))

        method = dbs[0].execute_fn if kind == "read" else dbs[0].execute_write_fn
        with pytest.raises(DatabaseReentrancyError):
            await asyncio.wait_for(method(callback), 2)
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_admission_errors_are_http_503(tmp_path, monkeypatch):
    ds, dbs = make_databases(tmp_path)
    await ds.invoke_startup()

    async def fail(*args, **kwargs):
        raise DatabaseQueueFull("Too many queued database writes")

    monkeypatch.setattr(dbs[0], "execute", fail)
    try:
        response = await ds.client.get("/budget0/-/query.json?sql=select+1")
        assert response.status_code == 503, response.text
        assert response.headers["retry-after"] == "1"
        assert response.json()["code"] == "database_queue_full"
        assert response.json()["execution_started"] is False
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("threads", [0, 3])
async def test_unfinished_explicit_transaction_is_rolled_back(tmp_path, threads):
    from datasette.connection_pool import ConnectionLeaseError

    ds, dbs = make_databases(tmp_path, num_sql_threads=threads)
    try:
        with pytest.raises(ConnectionLeaseError, match="rolled back"):
            await dbs[0].execute_write_fn(
                lambda conn: conn.execute("insert into t values (1)"), transaction=False
            )
        await dbs[0].execute_write("insert into t values (2)")
        assert [row[0] for row in (await dbs[0].execute("select id from t")).rows] == [
            2
        ]
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_nonblocking_expiration_notifies_plugin(tmp_path):
    from datasette import hookimpl
    from datasette.plugins import pm

    notifications = []

    class Plugin:
        @hookimpl
        async def write_task_completed(self, database, task_id, exception):
            notifications.append((database, task_id, exception))

    plugin = Plugin()
    pm.register(plugin)
    ds, dbs = make_databases(
        tmp_path, max_write_connections=1, write_queue_timeout_ms=50
    )
    entered, release = threading.Event(), threading.Event()

    def slow(conn):
        entered.set()
        assert release.wait(5)

    first = asyncio.create_task(dbs[0].execute_write_fn(slow))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task_id = await dbs[1].execute_write_fn(
            lambda conn: pytest.fail("expired callback ran"), block=False
        )
        await wait_for(lambda: notifications)
        assert notifications[0][:2] == (dbs[1].name, task_id)
        assert isinstance(notifications[0][2], DatabaseAdmissionTimeout)
        release.set()
        await first
        successful_id = await dbs[1].execute_write_fn(lambda conn: 42, block=False)
        await wait_for(lambda: len(notifications) == 2)
        assert notifications[1] == (dbs[1].name, successful_id, None)
    finally:
        release.set()
        await first
        ds.close()
        pm.unregister(plugin)


@pytest.mark.asyncio
async def test_connection_open_pressure_retries_before_callback(tmp_path, monkeypatch):
    import errno

    from datasette.utils import sqlite3 as sqlite_module

    ds, dbs = make_databases(tmp_path)
    await ds.invoke_startup()
    await dbs[1].execute("select 1")
    original = sqlite_module.connect
    calls = []

    def connect(*args, **kwargs):
        if "budget0.db" in str(args[0]):
            calls.append(1)
            if len(calls) == 1:
                raise OSError(errno.EMFILE, "too many files")
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite_module, "connect", connect)
    ran = []
    try:
        await dbs[0].execute_write_fn(lambda conn: ran.append(1))
        assert calls == [1, 1]
        assert ran == [1]
        assert not dbs[1]._read_pool_state.idle
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("threads", [0, 3])
async def test_resource_error_after_execution_is_not_an_admission_error(
    tmp_path, threads, monkeypatch
):
    ds, dbs = make_databases(tmp_path, num_sql_threads=threads)
    db = dbs[0]
    await ds.invoke_startup()
    try:

        def committed_then_failed(conn):
            conn.execute("insert into t values (1)")
            conn.commit()
            raise DatabaseQueueFull("A different operation could not be admitted")

        with pytest.raises(RuntimeError, match="callback started") as exc:
            await db.execute_write_fn(committed_then_failed, transaction=False)
        assert not isinstance(exc.value, DatabaseResourceError)
        assert isinstance(exc.value.__cause__, DatabaseQueueFull)
        assert (await db.execute("select count(*) from t")).single_value() == 1

        async def refresh_failed(*args):
            raise DatabaseResourceError("Catalog connection unavailable")

        monkeypatch.setattr(ds._schema_watcher, "after_write", refresh_failed)
        db._watch_state.needs_scan = True
        with pytest.raises(RuntimeError, match="Write completed") as exc:
            await db.execute_write("insert into t values (2)")
        assert not isinstance(exc.value, DatabaseResourceError)
        assert (await db.execute("select count(*) from t")).single_value() == 2
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_retiring_writer_keeps_its_slot_until_connection_closes(
    tmp_path, monkeypatch
):
    ds, dbs = make_databases(tmp_path, max_write_connections=1)
    closing, release = threading.Event(), threading.Event()
    original = dbs[0]._forget_connection

    def slow_close(conn):
        closing.set()
        assert release.wait(5)
        original(conn)

    await dbs[0].execute_write("insert into t values (1)")
    monkeypatch.setattr(dbs[0], "_forget_connection", slow_close)
    pending = asyncio.create_task(dbs[1].execute_write("insert into t values (2)"))
    try:
        assert await asyncio.to_thread(closing.wait, 5)
        assert not pending.done()
        assert dbs[1]._write_thread is None
        assert ds._write_budget.snapshot()["writers"] == 1
        release.set()
        await pending
        assert ds._write_budget.snapshot()["stats"]["peak_writers"] == 1
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        ds.close()
