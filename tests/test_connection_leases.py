"""
Connections handed to execute_write_fn(), the SQL write methods and
execute_isolated_fn() callbacks are lease proxies, like read connections:
they stop working once the callback returns.
"""

import asyncio
import sqlite3
import threading
import time

import pytest
import sqlite_utils

from datasette.app import Datasette
from datasette.database import ConnectionLeaseError


def _make_db(tmp_path, name="leases"):
    path = str(tmp_path / f"{name}.db")
    conn = sqlite3.connect(path)
    conn.execute("create table t (id integer primary key, v text)")
    conn.execute("insert into t (v) values ('x')")
    conn.commit()
    conn.close()
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
@pytest.mark.parametrize("transaction", [True, False])
async def test_write_callback_connection_expires(
    tmp_path, num_sql_threads, transaction
):
    ds = Datasette([_make_db(tmp_path)], settings={"num_sql_threads": num_sql_threads})
    db = ds.get_database("leases")
    stash = {}

    def write(conn):
        stash["conn"] = conn
        assert isinstance(conn, sqlite3.Connection)
        conn.execute("insert into t (v) values ('y')")
        if not transaction:
            # transaction=False DML is not committed for you (unchanged)
            conn.commit()
        return conn.execute("select count(*) from t").fetchone()[0]

    assert await db.execute_write_fn(write, transaction=transaction) == 2
    with pytest.raises(ConnectionLeaseError, match="execute_write_fn"):
        stash["conn"].execute("select 1")
    with pytest.raises(ConnectionLeaseError):
        stash["conn"].row_factory = None
    # The write connection itself is fine for the next write
    await db.execute_write("insert into t (v) values ('z')")
    assert (await db.execute("select count(*) from t")).single_value() == 3
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
async def test_write_callback_may_return_cursor(tmp_path, num_sql_threads):
    # A common plugin pattern: lambda conn: conn.execute(...). The returned
    # cursor's attributes stay readable; only the connection is leased.
    ds = Datasette([_make_db(tmp_path)], settings={"num_sql_threads": num_sql_threads})
    db = ds.get_database("leases")
    cursor = await db.execute_write_fn(
        lambda conn: conn.execute("insert into t (v) values ('y')")
    )
    assert cursor.lastrowid == 2
    assert cursor.rowcount == 1
    many = await db.execute_write_many("insert into t (v) values (?)", [["a"], ["b"]])
    assert many.rowcount == 2
    await db.execute_write_script("insert into t (v) values ('c');")
    assert (await db.execute("select count(*) from t")).single_value() == 5
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
async def test_with_conn_inside_write_callback_yields_proxy(tmp_path, num_sql_threads):
    ds = Datasette([_make_db(tmp_path)], settings={"num_sql_threads": num_sql_threads})
    db = ds.get_database("leases")
    stash = {}

    def write(conn):
        with conn as inner:
            stash["inner"] = inner
            inner.execute("insert into t (v) values ('y')")

    await db.execute_write_fn(write, transaction=False)
    with pytest.raises(ConnectionLeaseError):
        stash["inner"].execute("select 1")
    assert (await db.execute("select count(*) from t")).single_value() == 2
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
async def test_sqlite_utils_on_write_lease(tmp_path, num_sql_threads):
    ds = Datasette([_make_db(tmp_path)], settings={"num_sql_threads": num_sql_threads})
    db = ds.get_database("leases")

    def write(conn):
        sdb = sqlite_utils.Database(conn)
        sdb["dogs"].insert_all([{"id": 1, "name": "Cleo"}], pk="id")
        sdb["dogs"].transform(rename={"name": "dog_name"})
        return sdb["dogs"].columns_dict

    assert await db.execute_write_fn(write) == {"id": int, "dog_name": str}
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
@pytest.mark.parametrize("immutable", [False, True])
async def test_isolated_callback_connection_expires(
    tmp_path, num_sql_threads, immutable
):
    path = _make_db(tmp_path)
    if immutable:
        ds = Datasette(immutables=[path], settings={"num_sql_threads": num_sql_threads})
    else:
        ds = Datasette([path], settings={"num_sql_threads": num_sql_threads})
    db = ds.get_database("leases")
    stash = {}

    def isolated(conn):
        stash["conn"] = conn
        return conn.execute("select count(*) from t").fetchone()[0]

    assert await db.execute_isolated_fn(isolated) == 1
    with pytest.raises(ConnectionLeaseError, match="execute_isolated_fn"):
        stash["conn"].execute("select 1")
    ds.close()


@pytest.mark.asyncio
async def test_write_wrapper_receives_lease(tmp_path):
    from datasette import hookimpl
    from datasette.plugins import pm

    stash = {}

    class WrapperPlugin:
        __name__ = "WrapperPlugin"

        @hookimpl
        def write_wrapper(self, datasette, database, request, transaction):
            if database != "leases":
                return None

            def wrapper(conn):
                stash["conn"] = conn
                yield

            return wrapper

    pm.register(WrapperPlugin(), name="test_write_wrapper_receives_lease")
    try:
        ds = Datasette([_make_db(tmp_path)])
        db = ds.get_database("leases")
        await db.execute_write("insert into t (v) values ('y')")
        with pytest.raises(ConnectionLeaseError):
            stash["conn"].execute("select 1")
        ds.close()
    finally:
        pm.unregister(name="test_write_wrapper_receives_lease")


@pytest.mark.asyncio
async def test_close_waits_for_immutable_isolated_fn(tmp_path):
    # On main this ran on the executor untracked, so close() could close
    # the connection while the callback was still stepping it (a segfault
    # with a progress handler installed)
    ds = Datasette(immutables=[_make_db(tmp_path)], settings={"num_sql_threads": 2})
    db = ds.get_database("leases")
    started = threading.Event()

    def slow(conn):
        started.set()
        time.sleep(0.3)
        return conn.execute("select count(*) from t").fetchone()[0]

    task = asyncio.ensure_future(db.execute_isolated_fn(slow))
    assert await asyncio.to_thread(started.wait, 5)
    assert len(db._pending_execute_futures) == 1
    db.close()
    assert await task == 1
    ds.close()


NO_THREADS_SCRIPT = r"""
import asyncio, os, sqlite3, sys, threading

def no_threads(self):
    raise RuntimeError("can't start new thread")

threading.Thread.start = no_threads

from datasette.app import Datasette
from datasette.database import Database

path, scratch = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(path)
conn.execute("create table t (id integer primary key, v text)")
conn.execute("insert into t (v) values ('x')")
conn.commit()
conn.close()


async def main():
    ds = Datasette(
        [path],
        settings={
            "num_sql_threads": 0,
            "connection_idle_timeout_ms": 50,
            "schema_watch_interval_ms": 50,
        },
    )
    await ds.invoke_startup()
    db = ds.get_database("nothreads")
    assert (await db.execute("select count(*) from t")).single_value() == 1
    await db.execute_write("insert into t (v) values ('y')")
    await db.execute_write("create table t2 (id)")
    tables = await ds.get_internal_database().execute(
        "select table_name from catalog_tables where database_name = 'nothreads' "
        "order by table_name"
    )
    assert [r[0] for r in tables.rows] == ["t", "t2"], tables.rows
    assert await db.execute_isolated_fn(
        lambda conn: conn.execute("select count(*) from t").fetchone()[0]
    ) == 2
    await db.execute_write("insert into t (v) values ('z')", block=False)
    response = await ds.client.get("/nothreads/t.json?_shape=array")
    assert response.status_code == 200, response.text
    assert len(response.json()) == 3
    # External change detected by the polling task, inline on the loop
    other = sqlite3.connect(path)
    other.execute("create table external_t (id)")
    other.commit()
    other.close()
    for _ in range(100):
        await asyncio.sleep(0.02)
        tables = await ds.get_internal_database().execute(
            "select 1 from catalog_tables where table_name = 'external_t'"
        )
        if tables.rows:
            break
    else:
        raise AssertionError("external change not seen")
    # Scratch database added later, created by its first write
    sdb = ds.add_database(Database(ds, path=scratch), name="scratch")
    await sdb.execute_write("create table s (id)")
    assert os.path.exists(scratch)
    ds.close()
    print("ok", threading.active_count())


asyncio.run(main())
"""


def test_num_sql_threads_zero_starts_no_threads(tmp_path):
    # Simulates Pyodide, where no thread can be started: nothing in the
    # read pool, write path or schema watcher may need one
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            NO_THREADS_SCRIPT,
            str(tmp_path / "nothreads.db"),
            str(tmp_path / "scratch.db"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok 1"
