"""
Tests for the write_thread_idle_timeout_ms setting: a database's write thread
closes its connection and exits after sitting idle, and the next write
starts a new one.
"""

import asyncio
import queue
import random
import sqlite3
import threading
import time

import pytest

from datasette.app import Datasette
from datasette.database import Database, DatasetteClosedError


def _make_db(tmp_path, name="idle", **settings):
    path = tmp_path / f"{name}.db"
    conn = sqlite3.connect(path)
    conn.execute("create table t (id integer primary key, writer integer, seq integer)")
    conn.close()
    ds = Datasette([str(path)], settings=settings)
    return ds, ds.get_database(name)


async def _wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


def _write_threads(name):
    thread_name = f"_execute_writes for database {name}"
    return [t for t in threading.enumerate() if t.name == thread_name]


@pytest.mark.asyncio
async def test_write_thread_exits_when_idle_and_restarts(tmp_path):
    ds, db = _make_db(tmp_path, write_thread_idle_timeout_ms=50)
    try:
        await db.execute_write("insert into t (writer, seq) values (1, 1)")
        thread = db._write_thread
        assert thread is not None and thread.is_alive()
        connections_with_writer = len(db._all_connections)
        assert await _wait_for(lambda: db._write_thread is None)
        thread.join(timeout=5)
        assert not thread.is_alive()
        # Its write connection was closed and is no longer tracked
        assert len(db._all_connections) == connections_with_writer - 1
        assert not _write_threads("idle")
        # The next write starts a new thread
        await db.execute_write("insert into t (writer, seq) values (1, 2)")
        assert db._write_thread is not None and db._write_thread is not thread
        assert db._write_threads_started == 2
        count = (await db.execute("select count(*) from t")).single_value()
        assert count == 2
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_write_thread_idle_timeout_zero_keeps_thread(tmp_path):
    ds, db = _make_db(tmp_path, write_thread_idle_timeout_ms=0)
    try:
        await db.execute_write("insert into t (writer, seq) values (1, 1)")
        thread = db._write_thread
        await asyncio.sleep(0.2)
        assert db._write_thread is thread
        assert thread.is_alive()
    finally:
        ds.close()
    assert not thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize("named", (True, False))
async def test_memory_database_write_thread_never_idles(named):
    # Closing the write connection of a memory database would lose its data
    ds = Datasette(settings={"write_thread_idle_timeout_ms": 10})
    if named:
        db = ds.add_memory_database("idle_mem")
    else:
        db = ds.add_database(Database(ds, is_memory=True), name="idle_unnamed")
    try:
        await db.execute_write("create table t (id integer primary key)")
        await db.execute_write("insert into t (id) values (1)")
        thread = db._write_thread
        await asyncio.sleep(0.1)
        assert db._write_thread is thread and thread.is_alive()
        count = await db.execute_write_fn(
            lambda conn: conn.execute("select count(*) from t").fetchone()[0]
        )
        assert count == 1
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_close_after_idle_exit(tmp_path):
    ds, db = _make_db(tmp_path, write_thread_idle_timeout_ms=10)
    await db.execute_write("insert into t (writer, seq) values (1, 1)")
    thread = db._write_thread
    assert await _wait_for(lambda: db._write_thread is None)
    ds.close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    with pytest.raises(DatasetteClosedError):
        await db.execute_write("insert into t (writer, seq) values (1, 2)")


@pytest.mark.asyncio
async def test_close_drains_queued_writes_with_idle_timeout(tmp_path):
    ds, db = _make_db(tmp_path, write_thread_idle_timeout_ms=10)
    release = threading.Event()

    def slow(conn):
        release.wait(5)
        conn.execute("insert into t (writer, seq) values (0, 0)")

    await db.execute_write_fn(slow, block=False)
    futures = []
    for i in range(20):
        _, future = await db._send_to_write_thread(
            lambda conn, i=i: conn.execute(
                "insert into t (writer, seq) values (1, ?)", [i]
            ),
            block=False,
        )
        futures.append(future)
    release.set()
    # close() is synchronous, so the queued writes complete before it returns
    db.close()
    await asyncio.wait_for(asyncio.gather(*futures), timeout=5)
    conn = sqlite3.connect(db.path)
    try:
        assert conn.execute("select count(*) from t").fetchone()[0] == 21
    finally:
        conn.close()
        ds.close()


@pytest.mark.asyncio
async def test_connect_failure_retried_after_queue_drains(tmp_path):
    # With an idle timeout, a failed connect is reported to the writes that
    # were queued, then the thread exits and the next write tries again
    ds, db = _make_db(tmp_path, write_thread_idle_timeout_ms=30000)

    class ConnectError(Exception):
        pass

    original_connect = db.connect

    def broken_connect(write=False):
        if write:
            raise ConnectError("no")
        return original_connect(write=write)

    db.connect = broken_connect
    try:
        with pytest.raises(ConnectError):
            await db.execute_write("insert into t (writer, seq) values (1, 1)")
    finally:
        db.connect = original_connect
    assert await _wait_for(lambda: db._write_thread is None)
    await db.execute_write("insert into t (writer, seq) values (1, 2)")
    assert db._write_threads_started == 2
    ds.close()


class _RacyQueue(queue.Queue):
    """
    The first time the write thread asks for a task while the queue is
    empty, behave as if the idle timeout fired - but first pause, so the
    test can enqueue a task in the window between the timeout and the
    thread's exit decision.
    """

    def __init__(self):
        super().__init__()
        self.armed = False
        self.window_open = threading.Event()
        self.proceed = threading.Event()

    def get(self, block=True, timeout=None):
        if self.armed and self.empty():
            self.armed = False
            self.window_open.set()
            self.proceed.wait(5)
            raise queue.Empty
        return super().get(block, timeout)


@pytest.mark.asyncio
async def test_task_enqueued_between_timeout_and_exit_is_not_stranded(tmp_path):
    ds, db = _make_db(tmp_path, write_thread_idle_timeout_ms=60000)
    racy = _RacyQueue()
    db._write_queue = racy
    try:
        racy.armed = True
        await db.execute_write("insert into t (writer, seq) values (1, 1)")
        # The thread has finished that write and is now inside the window
        assert await asyncio.to_thread(racy.window_open.wait, 5)
        thread = db._write_thread
        assert thread is not None
        _, future = await db._send_to_write_thread(
            lambda conn: conn.execute("insert into t (writer, seq) values (1, 2)"),
            block=False,
        )
        # The thread is still registered, so no new thread was started
        assert db._write_threads_started == 1
        racy.proceed.set()
        await asyncio.wait_for(future, timeout=5)
        assert db._write_thread is thread
        count = (await db.execute("select count(*) from t")).single_value()
        assert count == 2
    finally:
        racy.proceed.set()
        ds.close()


def _recorder(log, writer, seq):
    # A cheap task (no disk write), so tasks finish in microseconds and the
    # idle timeout keeps firing between bursts
    def record(conn):
        conn.execute("select 1").fetchone()
        log.append((writer, seq, threading.get_ident()))

    return record


@pytest.mark.asyncio
@pytest.mark.parametrize("idle_timeout_ms", (1, 5))
async def test_idle_exit_race_stress(tmp_path, idle_timeout_ms):
    """
    Many concurrent writers working in rounds. Each round starts once the
    previous round's tasks are done, so the write thread's idle clock starts
    at about the same moment, and the writers then wait about 0.8x-1.3x of
    the idle timeout before queueing a burst. Tasks therefore keep landing
    on both sides of, and right on top of, the thread's exit decision.
    Every task must complete, and each writer's tasks must run in the order
    they were queued.
    """
    ds, db = _make_db(tmp_path, write_thread_idle_timeout_ms=idle_timeout_ms)
    idle_timeout = idle_timeout_ms / 1000
    rng = random.Random(42)
    writers = 12
    rounds = 150 if idle_timeout_ms == 1 else 60
    seqs = [0] * writers
    log = []

    async def writer_round(w, delay, burst):
        await asyncio.sleep(delay)
        pending = []
        for _ in range(burst):
            seq = seqs[w]
            seqs[w] += 1
            fn = _recorder(log, w, seq)
            if seq % 3 == 0:
                # Fire-and-forget write
                _, future = await db._send_to_write_thread(fn, block=False)
                pending.append(future)
            else:
                await db.execute_write_fn(fn, transaction=bool(seq % 2))
        await asyncio.gather(*pending)

    async def all_rounds():
        for _ in range(rounds):
            # One shared delay per round close to the timeout, plus a little
            # jitter per writer, so arrivals cluster around the exit decision
            base = idle_timeout * rng.uniform(0.8, 1.2)
            await asyncio.gather(
                *(
                    writer_round(
                        w, base + idle_timeout * rng.uniform(0, 0.1), rng.randint(1, 3)
                    )
                    for w in range(writers)
                )
            )

    try:
        await asyncio.wait_for(all_rounds(), timeout=120)
        assert len(log) == sum(seqs)
        for w in range(writers):
            assert [seq for writer_id, seq, _ in log if writer_id == w] == list(
                range(seqs[w])
            )
        print(
            f"idle_timeout_ms={idle_timeout_ms} tasks={len(log)} "
            f"threads_started={db._write_threads_started} "
            f"distinct_thread_idents={len({ident for _, _, ident in log})} "
            f"exits_averted={db._write_thread_exits_averted}"
        )
        # Only meaningful if the thread really did churn
        assert db._write_threads_started > rounds // 5
    finally:
        ds.close()
    assert not _write_threads("idle")


def test_idle_exit_race_stress_many_event_loops(tmp_path):
    """
    Producers on several OS threads, each with its own event loop, race
    with the write thread's exit decision for real (no single-loop
    ordering between producers). Rounds are synchronised with a barrier,
    then each producer waits a random 0.5x-1.5x of the idle timeout.
    """
    ds, db = _make_db(tmp_path, write_thread_idle_timeout_ms=1)
    loops = 6
    rounds = 200
    barrier = threading.Barrier(loops)
    errors = []
    log = []

    def run_loop(w):
        rng = random.Random(w)

        async def go():
            for seq in range(rounds):
                barrier.wait(timeout=30)
                await asyncio.sleep(0.001 * rng.uniform(0.5, 1.5))
                await asyncio.wait_for(
                    db.execute_write_fn(_recorder(log, w, seq)), timeout=10
                )

        try:
            asyncio.run(go())
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
            barrier.abort()

    threads = [threading.Thread(target=run_loop, args=(w,)) for w in range(loops)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    try:
        assert not errors, errors
        assert len(log) == loops * rounds
        for w in range(loops):
            assert [seq for writer_id, seq, _ in log if writer_id == w] == list(
                range(rounds)
            )
        print(
            f"tasks={len(log)} threads_started={db._write_threads_started} "
            f"exits_averted={db._write_thread_exits_averted}"
        )
        assert db._write_threads_started > rounds // 5
    finally:
        ds.close()
