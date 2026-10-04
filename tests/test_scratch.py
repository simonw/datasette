"""
Scratch databases: file-based databases that plugins create, fill, change and
delete through the Datasette API, and that survive a restart.
"""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from datasette import schema_watcher as schema_watcher_module
from datasette import scratch as scratch_module
from datasette.app import Datasette
from datasette.database import Database, DatasetteClosedError
from datasette.scratch import (
    REGISTRY_FILENAME,
    ScratchDatabase,
    ScratchDatabaseDeleted,
    ScratchDatabaseExists,
    ScratchDatabaseNotFound,
)
from datasette.utils import StartupError


def _registry_rows(scratch_dir):
    conn = sqlite3.connect(os.path.join(scratch_dir, REGISTRY_FILENAME))
    try:
        conn.row_factory = sqlite3.Row
        return {
            row["name"]: dict(row)
            for row in conn.execute("select * from scratch_databases")
        }
    finally:
        conn.close()


async def _catalog_tables(ds, name):
    rows = await ds.get_internal_database().execute(
        "select table_name from catalog_tables where database_name = ? order by table_name",
        [name],
    )
    return [r[0] for r in rows.rows]


async def _catalog_has_database(ds, name):
    rows = await ds.get_internal_database().execute(
        "select 1 from catalog_databases where database_name = ?", [name]
    )
    return bool(rows.rows)


def _files(scratch_dir, name):
    return sorted(f for f in os.listdir(scratch_dir) if f.startswith(name + ".db"))


def _write_threads(name):
    return [
        t
        for t in threading.enumerate()
        if t.name == f"_execute_writes for database {name}"
    ]


def _make_source(path, rows=10000):
    conn = sqlite3.connect(path)
    conn.execute(
        "create table items (id integer primary key, name text not null, score real)"
    )
    conn.executemany(
        "insert into items (name, score) values (?, ?)",
        [(f"item {i}", i * 0.5) for i in range(rows)],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def scratch_dir(tmp_path):
    return str(tmp_path / "scratch")


# ---------------------------------------------------------------------------
# create / list / names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_list_and_catalog(scratch_dir):
    ds = Datasette(scratch_dir=scratch_dir)
    await ds.invoke_startup()
    db = await ds.create_scratch_database(
        "work", actor={"id": "alice"}, metadata={"source": "test"}
    )
    assert isinstance(db, ScratchDatabase)
    assert db.is_scratch
    assert ds.get_database("work") is db
    assert db.owner == "alice"
    assert db.scratch_metadata == {"source": "test"}
    assert ds._schema_watcher.states["work"].mode == "owned"
    assert os.path.exists(os.path.join(scratch_dir, "work.db"))
    # The catalog knows about it before create returns
    assert await _catalog_has_database(ds, "work")
    # ... and about its tables before each write returns
    await db.execute_write("create table t (id integer primary key, v text)")
    assert await _catalog_tables(ds, "work") == ["t"]
    await db.execute_write("insert into t (v) values ('a')")
    # Created in WAL mode
    assert (await db.execute("pragma journal_mode")).single_value() == "wal"
    infos = await ds.list_scratch_databases()
    assert len(infos) == 1
    info = infos[0]
    assert info.name == "work"
    assert info.path == os.path.join(ds.scratch_dir, "work.db")
    assert info.owner == "alice"
    assert info.metadata == {"source": "test"}
    assert info.attached
    assert info.size > 0
    assert info.created <= info.last_used <= time.time()
    assert _registry_rows(scratch_dir)["work"]["state"] == "ready"
    # Browsable like any other database
    response = await ds.client.get("/work/t.json?_shape=array")
    assert response.status_code == 200
    assert response.json() == [{"id": 1, "v": "a"}]
    ds.close()


@pytest.mark.asyncio
async def test_create_generated_name_and_string_actor(scratch_dir):
    ds = Datasette(scratch_dir=scratch_dir)
    await ds.invoke_startup()
    db1 = await ds.create_scratch_database(actor="bob")
    db2 = await ds.create_scratch_database()
    assert db1.name != db2.name
    assert db1.name.startswith("scratch_")
    assert db1.owner == "bob"
    assert db2.owner is None
    assert [i.name for i in await ds.list_scratch_databases()] == sorted(
        [db1.name, db2.name]
    )
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "",
        "../escape",
        "a/b",
        "a\\b",
        ".hidden",
        "_internal",
        "-x",
        "a.b",
        "a b",
        "café",
        "x" * 65,
        "work\n",
        123,
    ],
)
async def test_invalid_names(scratch_dir, name):
    ds = Datasette(scratch_dir=scratch_dir)
    with pytest.raises(ValueError):
        await ds.create_scratch_database(name)
    assert [f for f in os.listdir(scratch_dir) if not f.startswith(".")] == []
    ds.close()


@pytest.mark.asyncio
async def test_duplicate_names(scratch_dir, tmp_path):
    other = str(tmp_path / "existing.db")
    sqlite3.connect(other).execute("vacuum")
    ds = Datasette([other], scratch_dir=scratch_dir)
    await ds.invoke_startup()
    await ds.create_scratch_database("Work")
    with pytest.raises(ScratchDatabaseExists):
        await ds.create_scratch_database("Work")
    # One file on case-insensitive file systems
    with pytest.raises(ScratchDatabaseExists):
        await ds.create_scratch_database("work")
    # Clashes with a database that is not a scratch database
    with pytest.raises(ScratchDatabaseExists):
        await ds.create_scratch_database("existing")
    with pytest.raises(TypeError):
        await ds.create_scratch_database("bad_metadata", metadata={"x": object()})
    assert "bad_metadata" not in ds.databases
    assert sorted(i.name for i in await ds.list_scratch_databases()) == ["Work"]
    ds.close()


# ---------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_attaches_without_opening(scratch_dir, monkeypatch):
    ds = Datasette(scratch_dir=scratch_dir)
    await ds.invoke_startup()
    for i in range(5):
        db = await ds.create_scratch_database(
            f"s{i}", actor={"id": "alice"}, metadata={"i": i}
        )
        await db.execute_write("create table t (id integer primary key)")
        await db.execute_write("insert into t default values")
    ds.close()

    connects = []
    original_connect = Database.connect

    def counting_connect(self, *args, **kwargs):
        connects.append(self.name)
        return original_connect(self, *args, **kwargs)

    monkeypatch.setattr(Database, "connect", counting_connect)
    ds = Datasette(scratch_dir=scratch_dir)
    # Registered, nothing opened, no threads
    assert sorted(n for n in ds.databases if n.startswith("s")) == [
        f"s{i}" for i in range(5)
    ]
    assert connects == []
    assert ds._read_pool_or_none is None
    assert not any(_write_threads(f"s{i}") for i in range(5))
    await ds.invoke_startup()
    # A temporary internal database has no catalog yet: each scratch
    # database was scanned once, on a short-lived connection
    assert await _catalog_tables(ds, "s3") == ["t"]
    for i in range(5):
        db = ds.get_database(f"s{i}")
        assert db._all_connections == []
        assert db._read_pool_state is None
        assert db._write_thread is None
    infos = {i.name: i for i in await ds.list_scratch_databases()}
    assert infos["s2"].owner == "alice"
    assert infos["s2"].metadata == {"i": 2}
    assert (
        await ds.get_database("s4").execute("select count(*) from t")
    ).single_value() == 1
    ds.close()


@pytest.mark.asyncio
async def test_restart_with_persistent_internal_scans_nothing(
    scratch_dir, tmp_path, monkeypatch
):
    # Fingerprints stored within the racy window would be rescanned
    monkeypatch.setattr(schema_watcher_module, "RACY_WINDOW_NS", 0)
    internal = str(tmp_path / "internal.db")
    ds = Datasette(scratch_dir=scratch_dir, internal=internal)
    await ds.invoke_startup()
    for i in range(3):
        db = await ds.create_scratch_database(f"s{i}")
        await db.execute_write("create table t (id integer primary key)")
        await db.execute_write("insert into t default values")
    ds.close()
    ds = Datasette(scratch_dir=scratch_dir, internal=internal)
    await ds.invoke_startup()
    watcher = ds._schema_watcher
    assert watcher.counters["restored_from_persisted"] == 3
    # Data written, and the -wal checkpointed by close(), after the last
    # catalog scan: still reused, because close() recorded the final
    # fingerprint of each owned database
    assert [watcher.states[f"s{i}"].stats["scans"] for i in range(3)] == [0, 0, 0]
    assert await _catalog_tables(ds, "s1") == ["t"]
    ds.close()


@pytest.mark.asyncio
async def test_directory_lock(scratch_dir):
    ds = Datasette(scratch_dir=scratch_dir)
    with pytest.raises(StartupError, match="in use by another Datasette instance"):
        Datasette(scratch_dir=scratch_dir)
    ds.close()
    Datasette(scratch_dir=scratch_dir).close()


@pytest.mark.asyncio
async def test_temporary_scratch_dir_when_not_configured():
    ds = Datasette()
    await ds.invoke_startup()
    assert ds.scratch_dir is None
    assert not ds._scratch.persistent
    db = await ds.create_scratch_database("tmp")
    await db.execute_write("create table t (id)")
    directory = ds.scratch_dir
    assert os.path.isdir(directory)
    ds.close()
    assert not os.path.exists(directory)


@pytest.mark.asyncio
async def test_config_dir_scratch_subdirectory(tmp_path):
    config_dir = tmp_path / "config"
    (config_dir / "scratch").mkdir(parents=True)
    ds = Datasette(config_dir=config_dir)
    assert ds.scratch_dir == str((config_dir / "scratch").resolve())
    await ds.create_scratch_database("in_config")
    ds.close()
    assert (config_dir / "scratch" / "in_config.db").exists()
    # Not picked up as a config_dir database (only *.db at the top level)
    ds = Datasette(config_dir=config_dir)
    assert ds.get_database("in_config").is_scratch
    ds.close()


def test_cli_scratch_dir(tmp_path):
    from click.testing import CliRunner

    from datasette.cli import cli

    scratch_dir = str(tmp_path / "scratch")

    async def setup():
        ds = Datasette(scratch_dir=scratch_dir)
        db = await ds.create_scratch_database("from_cli")
        await db.execute_write("create table t (id integer primary key)")
        await db.execute_write("insert into t default values")
        ds.close()

    asyncio.run(setup())
    result = CliRunner().invoke(
        cli,
        [
            "serve",
            "--memory",
            "--scratch-dir",
            scratch_dir,
            "--get",
            "/from_cli/t.json?_shape=array",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [{"id": 1}]


@pytest.mark.asyncio
async def test_name_clash_at_startup(scratch_dir, tmp_path):
    ds = Datasette(scratch_dir=scratch_dir)
    await ds.create_scratch_database("clash")
    ds.close()
    other = str(tmp_path / "clash.db")
    sqlite3.connect(other).execute("vacuum")
    ds = Datasette([other], scratch_dir=scratch_dir)
    assert not ds.get_database("clash").is_scratch
    [info] = await ds.list_scratch_databases()
    assert info.name == "clash"
    assert not info.attached
    ds.close()


# ---------------------------------------------------------------------------
# last_used
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_used_in_memory_then_flushed_on_close(scratch_dir):
    ds = Datasette(scratch_dir=scratch_dir)
    db = await ds.create_scratch_database("used")
    await db.execute_write("create table t (id)")
    transactions = ds._scratch.counters["registry_transactions"]
    before = db.last_used
    for _ in range(50):
        await db.execute("select count(*) from t")
    await db.execute_fn(lambda conn: conn.execute("select 1").fetchall())
    await db.execute_isolated_fn(lambda conn: None)
    assert db.last_used > before
    # Never a registry write per read
    assert ds._scratch.counters["registry_transactions"] == transactions
    in_memory = db.last_used
    assert _registry_rows(scratch_dir)["used"]["last_used"] < in_memory
    ds.close()
    assert _registry_rows(scratch_dir)["used"]["last_used"] == in_memory
    ds = Datasette(scratch_dir=scratch_dir)
    [info] = await ds.list_scratch_databases()
    assert info.last_used == in_memory
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
async def test_last_used_periodic_flush(scratch_dir, monkeypatch, num_sql_threads):
    monkeypatch.setattr(scratch_module, "LAST_USED_FLUSH_INTERVAL_S", 0.2)
    ds = Datasette(
        scratch_dir=scratch_dir, settings={"num_sql_threads": num_sql_threads}
    )
    db = await ds.create_scratch_database("used")
    await db.execute_write("create table t (id)")
    flushes = ds._scratch.counters["last_used_flushes"]
    deadline = time.monotonic() + 1.1
    reads = 0
    while time.monotonic() < deadline:
        await db.execute("select count(*) from t")
        reads += 1
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.1)
    new_flushes = ds._scratch.counters["last_used_flushes"] - flushes
    # Several flushes, far fewer than reads
    assert 2 <= new_flushes <= 7, (new_flushes, reads)
    assert reads > 50
    ds.close()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 3])
async def test_delete_removes_files_catalog_and_registry(scratch_dir, num_sql_threads):
    ds = Datasette(
        scratch_dir=scratch_dir, settings={"num_sql_threads": num_sql_threads}
    )
    await ds.invoke_startup()
    db = await ds.create_scratch_database("doomed")
    await db.execute_write("create table t (id integer primary key, v)")
    await db.execute_write_many(
        "insert into t (v) values (?)", [(i,) for i in range(100)]
    )
    assert (await db.execute("select count(*) from t")).single_value() == 100
    if num_sql_threads:
        # WAL mode: the sidecars exist while connections are open
        assert _files(scratch_dir, "doomed") == [
            "doomed.db",
            "doomed.db-shm",
            "doomed.db-wal",
        ]
    assert await _catalog_tables(ds, "doomed") == ["t"]
    await ds.delete_scratch_database("doomed")
    assert _files(scratch_dir, "doomed") == []
    assert "doomed" not in ds.databases
    assert "doomed" not in _registry_rows(scratch_dir)
    assert not await _catalog_has_database(ds, "doomed")
    assert await _catalog_tables(ds, "doomed") == []
    assert await ds.list_scratch_databases() == []
    with pytest.raises(ScratchDatabaseDeleted, match="'doomed' was deleted"):
        await db.execute("select 1")
    with pytest.raises(ScratchDatabaseDeleted):
        await db.execute_write("insert into t (v) values (1)")
    # Still a DatasetteClosedError for code that handles that
    with pytest.raises(DatasetteClosedError):
        await db.execute_fn(lambda conn: 1)
    with pytest.raises(ScratchDatabaseNotFound):
        await ds.delete_scratch_database("doomed")
    # The name can be used again, for a new empty database
    again = await ds.create_scratch_database("doomed")
    assert await again.table_names() == []
    ds.close()


@pytest.mark.asyncio
async def test_delete_with_reads_and_writes_in_flight(scratch_dir):
    # One SQL thread: a slow read occupies it and more reads queue behind it
    ds = Datasette(scratch_dir=scratch_dir, settings={"num_sql_threads": 1})
    await ds.invoke_startup()
    db = await ds.create_scratch_database("busy")
    await db.execute_write("create table t (id integer primary key, v)")
    await db.execute_write_many(
        "insert into t (v) values (?)", [(i,) for i in range(1000)]
    )
    read_started = threading.Event()
    write_started = threading.Event()

    def slow_read(conn):
        read_started.set()
        time.sleep(0.4)
        return conn.execute("select count(*) from t").fetchone()[0]

    def slow_write(conn):
        write_started.set()
        time.sleep(0.4)
        conn.execute("insert into t (v) values ('slow')")
        return "slow write done"

    running_read = asyncio.ensure_future(db.execute_fn(slow_read))
    running_write = asyncio.ensure_future(db.execute_write_fn(slow_write))
    while not (read_started.is_set() and write_started.is_set()):
        await asyncio.sleep(0.01)
    queued_reads = [
        asyncio.ensure_future(db.execute("select count(*) from t")) for _ in range(3)
    ]
    queued_writes = [
        asyncio.ensure_future(db.execute_write("insert into t (v) values (1)"))
        for _ in range(3)
    ]
    non_blocking = await db.execute_write("insert into t (v) values (2)", block=False)
    assert non_blocking is not None
    await asyncio.sleep(0.05)
    await ds.delete_scratch_database("busy")
    # Work that was already running finished normally
    assert await running_read == 1000
    assert await running_write == "slow write done"
    # Work that was queued got a clear error
    for future in queued_reads + queued_writes:
        with pytest.raises(ScratchDatabaseDeleted, match="'busy' was deleted"):
            await future
    assert _files(scratch_dir, "busy") == []
    ds.close()


@pytest.mark.asyncio
async def test_delete_waits_for_untracked_connections(scratch_dir):
    ds = Datasette(scratch_dir=scratch_dir)
    await ds.invoke_startup()
    db = await ds.create_scratch_database("watched")
    await db.execute_write("create table t (id)")
    # Simulate a SchemaWatcher scan holding a short-lived connection
    watcher = ds._schema_watcher
    conn = watcher._connect(db, prepare=False)
    conn.execute("select count(*) from t").fetchall()
    released = threading.Event()

    def release_later():
        time.sleep(0.3)
        # Still there while the scan connection is open
        assert os.path.exists(os.path.join(scratch_dir, "watched.db"))
        released.set()
        watcher._close(db, conn)

    thread = threading.Thread(target=release_later)
    thread.start()
    start = time.monotonic()
    await ds.delete_scratch_database("watched")
    assert time.monotonic() - start >= 0.25
    assert released.is_set()
    thread.join()
    assert _files(scratch_dir, "watched") == []
    # No new untracked connection once the database is closed
    with pytest.raises(DatasetteClosedError):
        watcher._connect(db, prepare=False)
    ds.close()


@pytest.mark.asyncio
async def test_delete_stress_no_crash(scratch_dir):
    """Delete while reads (with the time-limit progress handler installed)
    and writes are running and queued, many times over."""
    ds = Datasette(
        scratch_dir=scratch_dir,
        settings={"num_sql_threads": 3, "sql_time_limit_ms": 2000},
    )
    await ds.invoke_startup()
    for iteration in range(15):
        db = await ds.create_scratch_database(f"stress{iteration}")
        await db.execute_write("create table t (id integer primary key, v)")
        await db.execute_write_many(
            "insert into t (v) values (?)", [(i,) for i in range(2000)]
        )
        calls = []
        for i in range(12):
            calls.append(
                db.execute(
                    "with recursive c(x) as (select 1 union all select x + 1 from c "
                    "where x < 20000) select count(*) from c, (select 1 from t limit 3)"
                )
            )
            calls.append(db.execute_write("insert into t (v) values (?)", [i]))
        futures = [asyncio.ensure_future(c) for c in calls]
        await asyncio.sleep(0.002 * (iteration % 5))
        await ds.delete_scratch_database(f"stress{iteration}")
        results = await asyncio.gather(*futures, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                assert isinstance(result, ScratchDatabaseDeleted), repr(result)
        assert _files(scratch_dir, f"stress{iteration}") == []
    ds.close()


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 3])
async def test_rename(scratch_dir, num_sql_threads):
    ds = Datasette(
        scratch_dir=scratch_dir, settings={"num_sql_threads": num_sql_threads}
    )
    await ds.invoke_startup()
    db = await ds.create_scratch_database(
        "before", actor={"id": "alice"}, metadata={"a": 1}
    )
    await db.execute_write("create table t (id integer primary key, v)")
    await db.execute_write("insert into t (v) values ('kept')")
    other = await ds.create_scratch_database("other")
    with pytest.raises(ScratchDatabaseExists):
        await ds.rename_scratch_database("before", "other")
    with pytest.raises(ScratchDatabaseNotFound):
        await ds.rename_scratch_database("missing", "x")
    with pytest.raises(ValueError):
        await ds.rename_scratch_database("before", "../x")
    renamed = await ds.rename_scratch_database("before", "after")
    assert renamed.name == "after"
    assert renamed.owner == "alice"
    assert renamed.scratch_metadata == {"a": 1}
    assert (await renamed.execute("select v from t")).single_value() == "kept"
    assert _files(scratch_dir, "before") == []
    assert os.path.exists(os.path.join(scratch_dir, "after.db"))
    assert "before" not in ds.databases
    assert await _catalog_tables(ds, "before") == []
    assert await _catalog_tables(ds, "after") == ["t"]
    with pytest.raises(ScratchDatabaseDeleted, match="renamed to 'after'"):
        await db.execute("select 1")
    rows = _registry_rows(scratch_dir)
    assert set(rows) == {"after", "other"}
    assert rows["after"]["owner"] == "alice"
    # Case-only rename
    renamed = await ds.rename_scratch_database("after", "After")
    assert (await renamed.execute("select v from t")).single_value() == "kept"
    assert sorted(i.name for i in await ds.list_scratch_databases()) == [
        "After",
        "other",
    ]
    assert other.name == "other"
    ds.close()


# ---------------------------------------------------------------------------
# copying data in
# ---------------------------------------------------------------------------


def copy_table_into(scratch_conn, source_db, table):
    # The recipe from docs/internals.rst
    from datasette.utils import escape_sqlite

    mode = "mode=ro" if source_db.is_mutable else "immutable=1"
    source_uri = Path(source_db.path).resolve().as_uri() + "?" + mode
    scratch_conn.execute("ATTACH DATABASE ? AS source", [source_uri])
    try:
        with scratch_conn:
            scratch_conn.execute("BEGIN IMMEDIATE")
            create_sql = scratch_conn.execute(
                "select sql from source.sqlite_master where type = 'table' and name = ?",
                [table],
            ).fetchone()[0]
            scratch_conn.execute(create_sql)
            scratch_conn.execute(
                "insert into main.{t} select * from source.{t}".format(
                    t=escape_sqlite(table)
                )
            )
    finally:
        scratch_conn.execute("DETACH DATABASE source")


@pytest.mark.asyncio
@pytest.mark.parametrize("immutable", [False, True])
@pytest.mark.parametrize("num_sql_threads", [0, 3])
async def test_copy_data_in_recipe(tmp_path, scratch_dir, immutable, num_sql_threads):
    source_path = _make_source(str(tmp_path / "source.db"))
    kwargs = {"immutables": [source_path]} if immutable else {"files": [source_path]}
    ds = Datasette(
        scratch_dir=scratch_dir,
        settings={"num_sql_threads": num_sql_threads},
        **kwargs,
    )
    await ds.invoke_startup()
    source = ds.get_database("source")
    assert source.is_mutable is not immutable
    scratch = await ds.create_scratch_database("copy")
    await scratch.execute_write_fn(
        lambda conn: copy_table_into(conn, source, "items"), transaction=False
    )
    assert (await scratch.execute("select count(*) from items")).single_value() == 10000
    # Same schema, primary key included
    assert await scratch.primary_keys("items") == ["id"]
    assert await _catalog_tables(ds, "copy") == ["items"]
    # Modify the copy; the source is unchanged
    await scratch.execute_write("delete from items where id % 2 = 0")
    await scratch.execute_write("update items set score = score * 2")
    assert (await scratch.execute("select count(*) from items")).single_value() == 5000
    assert (await source.execute("select count(*) from items")).single_value() == 10000
    # Nothing left attached to the write connection
    attached = await scratch.execute_write_fn(
        lambda conn: [r[1] for r in conn.execute("pragma database_list").fetchall()]
    )
    assert attached == ["main"]

    # The alias is free again for the next copy on the same write connection
    def attach_again(conn):
        conn.execute(
            "ATTACH DATABASE ? AS source",
            [Path(source.path).resolve().as_uri() + "?mode=ro"],
        )
        try:
            return conn.execute("select count(*) from source.items").fetchone()[0]
        finally:
            conn.execute("DETACH DATABASE source")

    assert await scratch.execute_write_fn(attach_again, transaction=False) == 10000
    await ds.delete_scratch_database("copy")
    ds.close()


@pytest.mark.asyncio
async def test_detach_inside_managed_transaction_fails(tmp_path, scratch_dir):
    # Why the recipe uses transaction=False: SQLite refuses to DETACH a
    # database that was read inside the still-open transaction, so the
    # attachment would stay on the write connection
    source_path = _make_source(str(tmp_path / "source.db"), rows=10)
    ds = Datasette([source_path], scratch_dir=scratch_dir)
    source = ds.get_database("source")
    scratch = await ds.create_scratch_database("tx")
    uri = Path(source.path).resolve().as_uri() + "?mode=ro"

    def attach_read_detach(conn):
        conn.execute("ATTACH DATABASE ? AS source", [uri])
        conn.execute("select count(*) from source.items").fetchall()
        conn.execute("DETACH DATABASE source")

    with pytest.raises(sqlite3.OperationalError, match="database source is locked"):
        await scratch.execute_write_fn(attach_read_detach)
    # ... and the attachment has leaked into the write connection: the next
    # callback on it cannot use the alias
    with pytest.raises(sqlite3.OperationalError, match="already in use"):
        await scratch.execute_write_fn(
            lambda conn: copy_table_into(conn, source, "items"), transaction=False
        )
    ds.close()


# ---------------------------------------------------------------------------
# crash consistency
# ---------------------------------------------------------------------------

CRASH_SCRIPT = r"""
import asyncio, os, sys
from datasette import scratch
from datasette.app import Datasette

scratch_dir, op, crash_after = sys.argv[1], sys.argv[2], int(sys.argv[3])
steps = [0]
original_write = scratch.ScratchDatabases._registry_write
original_fsync = scratch._fsync_dir

def step():
    steps[0] += 1
    if steps[0] == crash_after:
        os._exit(17)

def registry_write(self, *args, **kwargs):
    original_write(self, *args, **kwargs)
    step()

def fsync_dir(d):
    original_fsync(d)
    step()

async def main():
    ds = Datasette(scratch_dir=scratch_dir)
    await ds.invoke_startup()
    if op != "create":
        db = await ds.create_scratch_database("victim", actor={"id": "alice"})
        await db.execute_write("create table t (id integer primary key)")
        await db.execute_write("insert into t default values")
    scratch.ScratchDatabases._registry_write = registry_write
    scratch._fsync_dir = fsync_dir
    if op == "create":
        await ds.create_scratch_database("victim", actor={"id": "alice"})
    elif op == "delete":
        await ds.delete_scratch_database("victim")
    elif op == "rename":
        await ds.rename_scratch_database("victim", "renamed")
    print("completed", steps[0])
    os._exit(0)

asyncio.run(main())
"""


@pytest.mark.parametrize("op", ["create", "delete", "rename"])
@pytest.mark.parametrize("crash_after", [1, 2, 3, 4, 99])
def test_crash_consistency(tmp_path, op, crash_after):
    """Kill the process after the Nth registry transaction or directory fsync
    of an operation; the next startup must see the old or the new state,
    with metadata, and no stray files."""
    scratch_dir = str(tmp_path / "scratch")
    result = subprocess.run(
        [sys.executable, "-c", CRASH_SCRIPT, scratch_dir, op, str(crash_after)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode in (0, 17), result.stderr
    ds = Datasette(scratch_dir=scratch_dir)
    infos = {i.name: i for i in asyncio.run(ds.list_scratch_databases())}
    files = sorted(f for f in os.listdir(scratch_dir) if not f.startswith("."))
    if op == "create" or op == "delete":
        assert set(infos) in (set(), {"victim"})
    else:
        assert set(infos) in ({"victim"}, {"renamed"})
    for name, info in infos.items():
        assert info.owner == "alice"
        assert info.attached
    # Only the listed databases and their own -wal/-shm (a process killed
    # with connections open leaves those): no orphan file or sidecar
    expected = {f"{name}.db" for name in infos}
    assert {f for f in files if f.endswith(".db")} == expected
    for f in files:
        assert f.endswith(".db") or f.rsplit("-", 1)[0] in expected, files
    rows = _registry_rows(scratch_dir)
    assert set(rows) == set(infos)
    assert all(row["state"] == "ready" for row in rows.values())
    for name in infos:
        db = ds.get_database(name)
        tables = asyncio.run(db.table_names())
        if op != "create":
            assert tables == ["t"]
    ds.close()


@pytest.mark.asyncio
async def test_hand_copied_and_hand_deleted_files(scratch_dir, tmp_path):
    ds = Datasette(scratch_dir=scratch_dir)
    await ds.create_scratch_database("kept", actor={"id": "alice"})
    await ds.create_scratch_database("removed_by_hand")
    ds.close()
    os.unlink(os.path.join(scratch_dir, "removed_by_hand.db"))
    # Copied in: a SQLite file, a non-SQLite file and an invalid name
    _make_source(os.path.join(scratch_dir, "dropped_in.db"), rows=3)
    Path(scratch_dir, "notes.db").write_text("not a database")
    _make_source(os.path.join(scratch_dir, "bad name.db"), rows=1)
    # A -wal whose database is gone would be replayed into the next file
    # of that name
    Path(scratch_dir, "ghost.db-wal").write_bytes(b"x" * 100)
    ds = Datasette(scratch_dir=scratch_dir)
    infos = {i.name: i for i in await ds.list_scratch_databases()}
    assert set(infos) == {"kept", "dropped_in"}
    assert infos["kept"].owner == "alice"
    assert infos["dropped_in"].owner is None
    assert (
        await ds.get_database("dropped_in").execute("select count(*) from items")
    ).single_value() == 3
    assert not os.path.exists(os.path.join(scratch_dir, "ghost.db-wal"))
    # Left alone
    assert os.path.exists(os.path.join(scratch_dir, "notes.db"))
    assert os.path.exists(os.path.join(scratch_dir, "bad name.db"))
    counters = ds._scratch.counters
    assert counters["adopted"] == 1
    assert counters["dropped_missing"] == 1
    assert counters["stray_files_removed"] == 1
    ds.close()


# ---------------------------------------------------------------------------
# modes and isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_attached_to_crossdb_memory(scratch_dir, tmp_path):
    other = str(tmp_path / "regular.db")
    sqlite3.connect(other).execute("vacuum")
    ds = Datasette([other], scratch_dir=scratch_dir, crossdb=True)
    await ds.create_scratch_database("private_scratch")
    names = [
        r[1]
        for r in (await ds.get_database("_memory").execute("pragma database_list")).rows
    ]
    assert "regular" in names
    assert "private_scratch" not in names
    ds.close()


@pytest.mark.asyncio
async def test_nolock_creates_rollback_journal_databases(scratch_dir):
    ds = Datasette(scratch_dir=scratch_dir, nolock=True)
    db = await ds.create_scratch_database("nolock")
    await db.execute_write("create table t (id)")
    assert (await db.execute("pragma journal_mode")).single_value() == "delete"
    assert (await db.execute("select count(*) from t")).single_value() == 0
    ds.close()


NO_THREADS_SCRIPT = r"""
import asyncio, os, sys, threading

def no_threads(self):
    raise RuntimeError("can't start new thread")

threading.Thread.start = no_threads

from datasette.app import Datasette

scratch_dir = sys.argv[1]

async def main():
    ds = Datasette(scratch_dir=scratch_dir, settings={"num_sql_threads": 0})
    await ds.invoke_startup()
    db = await ds.create_scratch_database("nothreads", actor={"id": "a"})
    await db.execute_write("create table t (id integer primary key)")
    await db.execute_write("insert into t default values")
    assert (await db.execute("select count(*) from t")).single_value() == 1
    renamed = await ds.rename_scratch_database("nothreads", "renamed")
    assert [i.name for i in await ds.list_scratch_databases()] == ["renamed"]
    await ds.delete_scratch_database("renamed")
    assert [f for f in os.listdir(scratch_dir) if not f.startswith(".")] == []
    ds.close()
    print("ok")

asyncio.run(main())
"""


def test_no_threads(tmp_path):
    # Pyodide: nothing in the scratch database lifecycle may need a thread
    result = subprocess.run(
        [sys.executable, "-c", NO_THREADS_SCRIPT, str(tmp_path / "scratch")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"


@pytest.mark.asyncio
async def test_idle_scratch_database_releases_everything(scratch_dir):
    ds = Datasette(
        scratch_dir=scratch_dir, settings={"connection_idle_timeout_ms": 200}
    )
    await ds.invoke_startup()
    db = await ds.create_scratch_database("idle")
    await db.execute_write("create table t (id)")
    await db.execute("select count(*) from t")
    assert db._write_thread is not None
    for _ in range(100):
        await asyncio.sleep(0.05)
        if (
            db._write_thread is None
            and not db._retiring_write_threads
            and db._all_connections == []
        ):
            break
    assert db._write_thread is None
    assert db._all_connections == []
    assert not _write_threads("idle")
    ds.close()
