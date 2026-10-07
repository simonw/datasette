import asyncio
import sqlite3
import threading
import time

import pytest
import sqlite_utils

from datasette.app import Datasette
from datasette.connection_pool import ConnectionLeaseError


def _make_dbs(tmp_path, n):
    paths = []
    for i in range(n):
        path = tmp_path / f"db{i}.db"
        conn = sqlite3.connect(path)
        conn.execute("create table t (id integer primary key, v text)")
        conn.execute("insert into t (v) values ('x')")
        conn.commit()
        conn.close()
        paths.append(str(path))
    return paths


def _file_conns(db):
    return [c for c in db._all_connections]


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
async def test_lease_expires_after_callback(tmp_path, num_sql_threads):
    (path,) = _make_dbs(tmp_path, 1)
    ds = Datasette([path], settings={"num_sql_threads": num_sql_threads})
    db = ds.get_database("db0")
    stash = {}

    def callback(conn):
        stash["conn"] = conn
        assert isinstance(conn, sqlite3.Connection)
        return conn.execute("select count(*) from t").fetchone()[0]

    assert await db.execute_fn(callback) == 1
    with pytest.raises(ConnectionLeaseError, match="db0"):
        stash["conn"].execute("select 1")
    with pytest.raises(ConnectionLeaseError):
        stash["conn"].row_factory = None
    # A second lease of the same underlying connection gets a new proxy, so
    # the stale one still fails while another callback is using it
    seen = {}

    def callback2(conn):
        with pytest.raises(ConnectionLeaseError):
            stash["conn"].execute("select 1")
        seen["ok"] = True

    await db.execute_fn(callback2)
    assert seen["ok"]
    ds.close()


@pytest.mark.asyncio
async def test_returning_cursor_raises(tmp_path):
    (path,) = _make_dbs(tmp_path, 1)
    ds = Datasette([path])
    db = ds.get_database("db0")
    with pytest.raises(ConnectionLeaseError, match="returned a Cursor"):
        await db.execute_fn(lambda conn: conn.execute("select * from t"))
    # Pool is still usable afterwards
    assert (await db.execute("select count(*) from t")).single_value() == 1
    ds.close()


@pytest.mark.asyncio
async def test_leased_connection_compatibility(tmp_path):
    (path,) = _make_dbs(tmp_path, 1)
    ds = Datasette([path])
    db = ds.get_database("db0")

    def callback(conn):
        out = {}
        out["tables"] = sqlite_utils.Database(conn).table_names()
        out["columns"] = list(sqlite_utils.Database(conn)["t"].columns_dict)
        target = sqlite3.connect(":memory:")
        conn.backup(target)
        out["backup"] = target.execute("select count(*) from t").fetchone()[0]
        conn.create_function("double", 1, lambda x: x * 2)
        out["fn"] = conn.execute("select double(21)").fetchone()[0]
        cursor = conn.cursor()
        cursor.execute("select v from t")
        out["cursor"] = cursor.fetchall()[0][0]
        return out

    assert await db.execute_fn(callback) == {
        "tables": ["t"],
        "columns": ["id", "v"],
        "backup": 1,
        "fn": 42,
        "cursor": "x",
    }
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 3])
async def test_global_cap_evicts_least_recently_used(tmp_path, num_sql_threads):
    paths = _make_dbs(tmp_path, 6)
    ds = Datasette(paths, settings={"num_sql_threads": num_sql_threads})
    pool = ds._read_pool
    # Below the 4 x num_sql_threads clamp, so set the private attribute
    pool.max_open = 2
    for i in range(6):
        await ds.get_database(f"db{i}").execute("select 1")
        assert pool.snapshot()["open"] <= 2
    # The two most recently used databases are the ones still open
    open_dbs = {
        name
        for name in (f"db{i}" for i in range(6))
        if ds.get_database(name)._read_pool_state.open
    }
    assert open_dbs == {"db4", "db5"}
    assert pool.stats["evicted_lru"] >= 4
    for i in range(6):
        db = ds.get_database(f"db{i}")
        assert len(_file_conns(db)) == db._read_pool_state.open
    ds.close()


@pytest.mark.asyncio
async def test_prepare_connection_runs_for_every_pooled_connection(tmp_path):
    paths = _make_dbs(tmp_path, 3)
    ds = Datasette(paths)
    ds._read_pool.max_open = 1
    calls = []
    original = ds._prepare_connection

    def counting(conn, database):
        calls.append(database)
        return original(conn, database)

    ds._prepare_connection = counting
    # Pool may have been created already with the original bound method
    ds._read_pool._prepare_connection = counting
    for _ in range(2):
        for i in range(3):
            assert (
                await ds.get_database(f"db{i}").execute("select v from t")
            ).single_value() == "x"
    # Cap of 1 means every switch of database reopens and re-prepares
    assert calls == ["db0", "db1", "db2"] * 2
    ds.close()


def test_idle_reaper_closes_connections_without_event_loop(tmp_path):
    paths = _make_dbs(tmp_path, 3)
    ds = Datasette(paths, settings={"num_sql_threads": 0})
    pool = ds._read_pool
    pool.idle_timeout = 0.2

    async def query_all():
        for i in range(3):
            await ds.get_database(f"db{i}").execute("select 1")

    asyncio.run(query_all())
    assert pool.snapshot()["open"] == 3
    # No event loop is running now - the reaper thread does the work
    deadline = time.monotonic() + 5
    while pool.snapshot()["open"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pool.snapshot()["open"] == 0
    assert pool.stats["expired"] == 3
    for i in range(3):
        assert _file_conns(ds.get_database(f"db{i}")) == []
    ds.close()


@pytest.mark.parametrize(
    "num_sql_threads,configured,expected",
    [
        (3, 128, 128),
        (3, 5, 12),
        (8, 20, 32),
        (0, 1, 1),
        (3, 0, 0),
    ],
)
def test_max_open_connections_clamped_to_four_times_threads(
    num_sql_threads, configured, expected
):
    ds = Datasette(
        settings={
            "num_sql_threads": num_sql_threads,
            "max_open_connections": configured,
        }
    )
    assert ds._read_pool.max_open == expected
    # /-/settings.json reports the configured value
    assert ds.setting("max_open_connections") == configured
    ds.close()


def test_connection_idle_timeout_ms_setting():
    ds = Datasette(settings={"connection_idle_timeout_ms": 1500})
    assert ds._read_pool.idle_timeout == 1.5
    ds.close()
    ds = Datasette(settings={"connection_idle_timeout_ms": 0})
    assert ds._read_pool.idle_timeout == 0
    ds.close()


@pytest.mark.asyncio
async def test_soft_cap_when_every_connection_is_leased(tmp_path):
    # Only reachable with a cap below the thread count, which the setting
    # clamp prevents: open one more connection rather than wait, and close
    # it again on release
    paths = _make_dbs(tmp_path, 3)
    ds = Datasette(paths, settings={"num_sql_threads": 3})
    pool = ds._read_pool
    pool.max_open = 1
    barrier = threading.Barrier(3, timeout=5)

    def slow(conn):
        barrier.wait()
        return conn.execute("select 1").fetchone()[0]

    results = await asyncio.gather(
        *[ds.get_database(f"db{i}").execute_fn(slow) for i in range(3)]
    )
    assert results == [1, 1, 1]
    assert pool.stats["exceeded_cap"] >= 2
    assert pool.stats["peak_open"] == 3
    # Back under the cap once everything has been returned
    assert pool.snapshot()["open"] <= 1
    ds.close()


@pytest.mark.asyncio
async def test_memory_databases_not_counted(tmp_path):
    ds = Datasette(memory=True, settings={"max_open_connections": 1})
    db = ds.add_memory_database("pool_mem_test")
    await db.execute_write("create table t (id integer)")
    await db.execute_write("insert into t values (1)")
    await ds.get_database("_memory").execute("select 1")
    assert (await db.execute("select count(*) from t")).single_value() == 1
    assert ds._read_pool.snapshot()["open"] == 0
    ds.close()


@pytest.mark.asyncio
async def test_database_close_with_leased_connection(tmp_path):
    (path,) = _make_dbs(tmp_path, 1)
    ds = Datasette([path], settings={"num_sql_threads": 0})
    db = ds.get_database("db0")
    await db.execute("select 1")

    def closes_database(conn):
        conn.execute("select 1")
        db.close()
        return "done"

    assert await db.execute_fn(closes_database) == "done"
    assert _file_conns(db) == []
    assert ds._read_pool.snapshot()["open"] == 0
    ds.close()


@pytest.mark.asyncio
async def test_callback_that_closes_connection_is_discarded(tmp_path):
    (path,) = _make_dbs(tmp_path, 1)
    ds = Datasette([path])
    db = ds.get_database("db0")
    await db.execute_fn(lambda conn: conn.close())
    assert ds._read_pool.stats["discarded_broken"] == 1
    assert (await db.execute("select count(*) from t")).single_value() == 1
    ds.close()


@pytest.mark.asyncio
async def test_open_transaction_rolled_back_on_release(tmp_path):
    (path,) = _make_dbs(tmp_path, 1)
    ds = Datasette([path], settings={"num_sql_threads": 1})
    db = ds.get_database("db0")

    def begin(conn):
        conn.execute("begin")
        conn.execute("select * from t").fetchall()

    await db.execute_fn(begin)
    assert ds._read_pool.stats["rolled_back"] == 1
    assert not await db.execute_fn(lambda conn: conn.in_transaction)
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
async def test_invalidate_database_discards_stale_connections(
    tmp_path, num_sql_threads
):
    paths = _make_dbs(tmp_path, 2)
    ds = Datasette(paths, settings={"num_sql_threads": num_sql_threads})
    db0, db1 = ds.get_database("db0"), ds.get_database("db1")
    pool = ds._read_pool
    await db0.execute("select 1")
    await db1.execute("select 1")
    old = db0._read_pool_state.idle[-1].conn
    assert db0._read_pool_state.open == 1
    # What the SchemaWatcher does when it sees db0's file replaced
    db0._invalidate_connections()
    # Idle connections for db0 are closed at once; db1 is untouched
    assert db0._read_pool_state.open == 0
    assert db1._read_pool_state.open == 1
    assert old not in db0._all_connections
    with pytest.raises(sqlite3.ProgrammingError):
        old.execute("select 1")
    # The next read opens a new connection
    await db0.execute("select 1")
    assert db0._read_pool_state.idle[-1].conn is not old
    assert pool.stats["discarded_stale"] == 1
    ds.close()


@pytest.mark.asyncio
async def test_leased_connection_discarded_on_release_after_invalidation(tmp_path):
    (path,) = _make_dbs(tmp_path, 1)
    ds = Datasette([path], settings={"num_sql_threads": 2})
    db = ds.get_database("db0")
    pool = ds._read_pool
    seen = {}

    def callback(conn):
        seen["raw"] = conn.execute("select 1").connection
        # The file is replaced while this callback holds the connection:
        # it must not be closed under us, only discarded on release
        db._invalidate_connections()
        return conn.execute("select count(*) from t").fetchone()[0]

    assert await db.execute_fn(callback) == 1
    assert db._read_pool_state.open == 0
    assert pool.stats["discarded_stale"] == 1
    with pytest.raises(ConnectionLeaseError):
        seen["raw"].execute("select 1")
    assert (await db.execute("select count(*) from t")).single_value() == 1
    ds.close()


def test_reaper_unavailable_falls_back_to_checkout_reaping(tmp_path, monkeypatch):
    # Pyodide cannot start threads: idle connections must then be reaped by
    # the next checkout instead, and reads must keep working
    from datasette import connection_pool

    reaper = connection_pool._Reaper()
    monkeypatch.setattr(connection_pool, "_reaper", reaper)

    def no_threads(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", no_threads)
    paths = _make_dbs(tmp_path, 2)
    ds = Datasette(paths, settings={"num_sql_threads": 0})
    pool = ds._read_pool
    pool.idle_timeout = 0.05

    async def read(name):
        return (await ds.get_database(name).execute("select 1")).single_value()

    assert asyncio.run(read("db0")) == 1
    assert reaper.unavailable
    assert pool.snapshot()["open"] == 1
    time.sleep(0.1)
    assert asyncio.run(read("db1")) == 1
    # db0's expired connection was closed during db1's checkout
    assert pool.stats["expired"] == 1
    assert ds.get_database("db0")._read_pool_state.open == 0
    ds.close()
