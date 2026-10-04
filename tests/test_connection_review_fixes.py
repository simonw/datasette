"""
Regression tests for problems found by the second review of the connection
management redesign (concurrency, lifecycle and public-contract findings).
Each test names the problem it pins down.
"""

import asyncio
import gc
import logging
import os
import sqlite3
import threading
import time
import weakref

import pytest

from datasette import schema_watcher as schema_watcher_module
from datasette.app import Datasette
from datasette.connection_pool import ConnectionLeaseError
from datasette.database import Database
from datasette.scratch import LOCK_FILENAME, REGISTRY_FILENAME


def make_db(path, sql="create table t (id integer primary key, v text)"):
    conn = sqlite3.connect(path)
    conn.executescript(sql)
    conn.commit()
    conn.close()


async def catalog_tables(ds, name):
    rows = await ds.get_internal_database().execute(
        "select table_name from catalog_tables where database_name = ? "
        "order by table_name",
        [name],
    )
    return [r[0] for r in rows.rows]


def live_tables(path):
    conn = sqlite3.connect(path)
    try:
        return sorted(
            r[0]
            for r in conn.execute(
                "select name from sqlite_master where type = 'table' "
                "and name not like 'sqlite_%'"
            )
        )
    finally:
        conn.close()


def run_in_threads(*coroutine_functions, timeout=20):
    """Run each coroutine function with asyncio.run() in its own thread:
    several event loops driving one Datasette at the same time."""
    results = [None] * len(coroutine_functions)

    def runner(i, fn):
        try:
            results[i] = ("ok", asyncio.run(fn()))
        except BaseException as e:  # noqa: BLE001
            results[i] = ("error", e)

    threads = [
        threading.Thread(target=runner, args=(i, fn), daemon=True)
        for i, fn in enumerate(coroutine_functions)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    hung = [i for i, thread in enumerate(threads) if thread.is_alive()]
    assert not hung, f"event loop threads {hung} did not finish within {timeout}s"
    for status, value in results:
        if status == "error":
            raise value
    return [value for _, value in results]


@pytest.fixture
def owned_db(tmp_path):
    path = str(tmp_path / "data.db")
    make_db(path)
    ds = Datasette(settings={"schema_watch_interval_ms": 0})
    db = ds.add_database(Database(ds, path=path), name="data", schema_watch="owned")
    asyncio.run(ds.invoke_startup())
    yield ds, db, path
    ds.close()


# ----------------------------------------------------------------------
# Concurrency and lifecycle
# ----------------------------------------------------------------------


def test_cross_loop_ddl_writes_do_not_hang(owned_db, monkeypatch):
    # C1: refresh() treated a scan started on another *live* event loop as
    # abandoned, stole it, and each loop then resolved or cleared the
    # other's future: blocking writes on one loop never returned
    ds, db, path = owned_db
    original = schema_watcher_module.SchemaWatcher._scan_chunk_sync

    def slow_scan(self, states):
        time.sleep(0.2)
        return original(self, states)

    monkeypatch.setattr(
        schema_watcher_module.SchemaWatcher, "_scan_chunk_sync", slow_scan
    )

    def writer(prefix, count):
        async def run():
            for i in range(count):
                await asyncio.wait_for(
                    db.execute_write(f"create table {prefix}{i} (id integer)"), 10
                )

        return run

    def inserter():
        async def run():
            async def worker():
                for _ in range(20):
                    await asyncio.wait_for(
                        db.execute_write("insert into t (v) values ('x')"), 10
                    )

            await asyncio.gather(*(worker() for _ in range(3)))

        return run

    run_in_threads(writer("a", 3), writer("b", 3), inserter())

    async def check():
        return await catalog_tables(ds, "data")

    assert asyncio.run(check()) == live_tables(path)
    state = db._watch_state
    assert state.scan_future is None
    assert not state.needs_scan


def test_scan_from_stopped_loop_is_taken_over(owned_db):
    # The rule for taking over a scan: its loop has closed or stopped, so
    # nothing will finish it. A loop stopped by run_until_complete()
    # returning while a scan task is pending must not hang other loops.
    ds, db, _path = owned_db
    loop = asyncio.new_event_loop()
    try:
        gate = threading.Event()
        original = schema_watcher_module.SchemaWatcher._scan_chunk_sync

        def gated_scan(self, states):
            gate.wait(5)
            return original(self, states)

        schema_watcher_module.SchemaWatcher._scan_chunk_sync = gated_scan
        try:

            async def start_scan():
                # block=False: the scan task is spawned on this loop and
                # left pending when run_until_complete() returns
                await db.execute_write(
                    "create table stopped1 (id integer)", block=False
                )
                await asyncio.sleep(0.2)

            loop.run_until_complete(start_scan())
        finally:
            schema_watcher_module.SchemaWatcher._scan_chunk_sync = original
        gate.set()

        async def other_loop():
            await asyncio.wait_for(
                db.execute_write("create table other1 (id integer)"), 10
            )
            return await catalog_tables(ds, "data")

        tables = run_in_threads(other_loop)[0]
        assert {"stopped1", "other1"} <= set(tables)
    finally:
        loop.close()


def test_one_poller_across_live_event_loops(tmp_path):
    # C2: every switch between two live event loops started another
    # poller task, each sweeping every interval
    path = str(tmp_path / "ext.db")
    make_db(path)
    ds = Datasette([path], settings={"schema_watch_interval_ms": 100})
    asyncio.run(ds.invoke_startup())
    watcher = ds._schema_watcher
    turns = [threading.Event(), threading.Event()]
    done = threading.Event()
    counts = {}

    def client(me, other, rounds):
        async def run():
            for _ in range(rounds):
                turns[me].wait(5)
                turns[me].clear()
                assert (await ds.client.get("/ext.json")).status_code == 200
                turns[other].set()
            if me == 0:
                sweeps = watcher.counters["sweeps"]
                await asyncio.sleep(1.0)
                counts["sweeps_per_s"] = watcher.counters["sweeps"] - sweeps
                counts["pollers"] = len([t for t in watcher._pollers if not t.done()])
                done.set()
            else:
                done.wait(10)

        return run

    turns[0].set()
    run_in_threads(client(0, 1, 10), client(1, 0, 10))
    assert counts["pollers"] == 1
    # One poller at a 100ms interval: about 10 sweeps a second, not 10 per
    # loop switch
    assert counts["sweeps_per_s"] <= 15
    ds.close()


@pytest.mark.asyncio
async def test_failed_catalog_write_is_retried(owned_db, monkeypatch):
    # C3: refresh() cleared needs_scan before scanning and never restored
    # it, so an owned database lost a catalog update for good if the
    # catalog write failed (internal database locked)
    ds, db, _path = owned_db
    original = schema_watcher_module.write_catalog_entries
    locked = threading.Event()
    locked.set()

    def flaky(conn, entries):
        if locked.is_set():
            raise sqlite3.OperationalError("database is locked")
        return original(conn, entries)

    monkeypatch.setattr(schema_watcher_module, "write_catalog_entries", flaky)
    await db.execute_write("create table added_while_locked (id integer)")
    # Let the refresh task spawned by the write's notification fail too
    for _ in range(5):
        await asyncio.sleep(0.01)
    state = db._watch_state
    assert state.needs_scan
    assert state in ds._schema_watcher._pending
    assert await catalog_tables(ds, "data") == ["t"]
    # While still locked, a request is not failed by the catalog update
    state.retry_at = 0.0
    assert (await ds.client.get("/data.json")).status_code == 200
    assert state.retry_at > time.monotonic()
    # Once unlocked, the next request retries (after the back-off)
    locked.clear()
    state.retry_at = 0.0
    await ds.client.get("/data.json")
    assert await catalog_tables(ds, "data") == ["added_while_locked", "t"]
    response = await ds.client.get("/data.json")
    assert {t["name"] for t in response.json()["tables"]} == {
        "added_while_locked",
        "t",
    }


def test_block_false_write_from_closed_loop_reaches_catalog(owned_db):
    # C3: the schema-change notification went to the caller's loop, which
    # had closed: notified_version was recorded anyway and the change was
    # never picked up
    ds, db, path = owned_db

    async def write():
        await db.execute_write(
            "create table added_nonblocking (id integer)", block=False
        )

    asyncio.run(write())
    # Let the write thread finish (it delivers to a closed loop)
    deadline = time.monotonic() + 5
    while "added_nonblocking" not in live_tables(path):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    time.sleep(0.05)

    async def later():
        await ds.client.get("/data.json")
        return await catalog_tables(ds, "data")

    assert asyncio.run(later()) == ["added_nonblocking", "t"]


def test_unclosed_datasette_releases_scratch_directory(tmp_path):
    # C4: the temp internal database's atexit.register(bound method) pinned
    # every Datasette, so the scratch directory lock was never released
    scratch = tmp_path / "scratch"
    ds = Datasette(scratch_dir=str(scratch))
    ref = weakref.ref(ds)
    del ds
    gc.collect()
    assert ref() is None
    Datasette(scratch_dir=str(scratch)).close()


def test_failed_construction_releases_scratch_directory(tmp_path):
    # C4: a Datasette() whose __init__ raised after loading the scratch
    # directory kept it locked, with no object to close()
    from datasette.utils import StartupError

    scratch = tmp_path / "scratch"
    with pytest.raises(StartupError):
        Datasette(scratch_dir=str(scratch), settings={"default_schema_watch": "bogus"})
    # No gc.collect(): the lock is taken last, after validation
    Datasette(scratch_dir=str(scratch)).close()


def test_closed_datasette_is_collected(tmp_path):
    # C4 + C5: atexit and the read-pool reaper's loop variable both kept a
    # closed Datasette (and every Database, cache and plugin state) alive
    path = str(tmp_path / "data.db")
    make_db(path)
    ds = Datasette([path], settings={"connection_idle_timeout_ms": 20})

    async def use(ds):
        await ds.invoke_startup()
        await ds.get_database("data").execute("select 1")
        await ds.get_database("data").execute_write("insert into t (v) values (1)")

    asyncio.run(use(ds))
    # Let the reaper scan (and close) the idle connection
    time.sleep(0.3)
    ds.close()
    ref = weakref.ref(ds)
    del ds
    gc.collect()
    assert ref() is None


@pytest.mark.asyncio
async def test_write_callback_cannot_close_write_connection(owned_db):
    # C6: closing the write lease closed the write thread's connection;
    # every later write failed until the thread idled out
    _ds, db, _path = owned_db

    def close_it(conn):
        conn.close()

    with pytest.raises(ConnectionLeaseError):
        await db.execute_write_fn(close_it)
    await db.execute_write("insert into t (v) values ('after')")

    # Reaching the raw connection through a cursor still closes it: the
    # write thread notices and reopens before the next write
    def close_raw(conn):
        conn.execute("select 1").connection.close()

    with pytest.raises(sqlite3.ProgrammingError):
        await db.execute_write_fn(close_raw)
    for _ in range(3):
        await db.execute_write("insert into t (v) values ('again')")
    rows = await db.execute("select count(*) from t")
    assert rows.first()[0] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", (3, 0))
async def test_isolated_lease_close_is_allowed(tmp_path, num_sql_threads):
    path = str(tmp_path / "data.db")
    make_db(path)
    ds = Datasette([path], settings={"num_sql_threads": num_sql_threads})
    db = ds.get_database("data")
    await db.execute_isolated_fn(lambda conn: conn.close())
    await db.execute_write("insert into t (v) values (1)")
    ds.close()


class Abort(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("exception_class", (Abort, SystemExit))
async def test_write_callback_base_exception_is_delivered(owned_db, exception_class):
    # C7: a BaseException from a write callback killed the write thread
    # without answering the caller, who waited forever
    _ds, db, _path = owned_db

    def boom(conn):
        raise exception_class("stop")

    with pytest.raises(RuntimeError) as excinfo:
        await asyncio.wait_for(db.execute_write_fn(boom), 5)
    assert isinstance(excinfo.value.__cause__, exception_class)
    # Same for an isolated function
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(db.execute_isolated_fn(boom), 5)
    await asyncio.wait_for(db.execute_write("insert into t (v) values (1)"), 5)


# ----------------------------------------------------------------------
# Public contract
# ----------------------------------------------------------------------


def _fts_setup(path):
    make_db(path, "create table secret (id integer primary key, body text)")
    conn = sqlite3.connect(path)
    conn.execute("insert into secret values (1, 'TOP SECRET PAYLOAD')")
    conn.commit()
    conn.close()


def _add_fts(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        create virtual table secret_fts using fts5(body, content='secret', content_rowid='id');
        insert into secret_fts(secret_fts) values ('rebuild');
    """)
    conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ("external", "owned"))
async def test_derived_table_rule_uses_current_schema(tmp_path, mode):
    # Contract C1: the "external-content FTS tables require access to their
    # content table" rule was decided from a catalog that had not seen an
    # FTS table created outside Datasette: its rows were served to
    # anonymous users (until the next poll, or for good for owned DBs)
    path = str(tmp_path / "data.db")
    _fts_setup(path)
    ds = Datasette(
        config={
            "databases": {"data": {"tables": {"secret": {"allow": {"id": "root"}}}}}
        },
        settings={"default_allow_sql": False, "schema_watch_interval_ms": 0},
    )
    ds.add_database(Database(ds, path=path), name="data", schema_watch=mode)
    await ds.invoke_startup()
    assert (await ds.client.get("/data/secret.json")).status_code == 403
    _add_fts(path)
    response = await ds.client.get("/data/secret_fts.json")
    assert response.status_code == 403
    assert "TOP SECRET" not in response.text
    # Finding the catalog behind schedules a rescan
    await ds.client.get("/data.json")
    assert "secret_fts" in await catalog_tables(ds, "data")
    ds.close()


@pytest.mark.asyncio
async def test_add_database_file_changes_are_picked_up(tmp_path):
    # Contract C2: add_database() defaulted to "owned" (never polled), so a
    # table created by another process never appeared in listings
    path = str(tmp_path / "added.db")
    make_db(path)
    ds = Datasette(settings={"schema_watch_interval_ms": 50})
    await ds.invoke_startup()
    ds.add_database(Database(ds, path=path), name="added")
    await ds.client.get("/added.json")
    make_db(path, "create table t_ext (id integer)")

    async def listed():
        response = await ds.client.get("/added.json")
        return {t["name"] for t in response.json()["tables"]}

    deadline = time.monotonic() + 5
    while "t_ext" not in await listed():
        assert time.monotonic() < deadline, "t_ext never appeared"
        await asyncio.sleep(0.05)
    ds.close()


@pytest.mark.asyncio
async def test_named_memory_database_out_of_band_changes(tmp_path):
    # Contract C2: named in-memory databases could not be watched at all;
    # datasette-app-support restores one with backup() into
    # db.connect(write=True) and then shows it
    ds = Datasette(settings={"schema_watch_interval_ms": 50})
    await ds.invoke_startup()
    memory = ds.add_memory_database("review_fixes_temporary", name="temporary")
    await memory.execute_write("create table m0 (id integer)")
    source_path = str(tmp_path / "backup.db")
    make_db(source_path, "create table restored_table (id integer)")
    source = sqlite3.connect(source_path)
    target = memory.connect(write=True)
    source.backup(target)
    source.close()
    target.close()

    async def listed():
        response = await ds.client.get("/temporary.json")
        return {t["name"] for t in response.json()["tables"]}

    deadline = time.monotonic() + 5
    while "restored_table" not in await listed():
        assert time.monotonic() < deadline, "restored_table never appeared"
        await asyncio.sleep(0.05)
    ds.close()


def _config_dir(tmp_path):
    config_dir = tmp_path / "cfg"
    (config_dir / "scratch").mkdir(parents=True)
    make_db(str(config_dir / "main.db"))
    return config_dir


def test_config_dir_user_scratch_folder_is_not_taken_over(tmp_path):
    # Contract C3: an existing config_dir/scratch folder was adopted:
    # its files were served, orphan sidecars deleted and the folder locked
    config_dir = _config_dir(tmp_path)
    make_db(str(config_dir / "scratch" / "private_notes.db"))
    (config_dir / "scratch" / "other.db-wal").write_bytes(b"junk")
    ds = Datasette(config_dir=config_dir)
    assert "private_notes" not in ds.databases
    assert ds.scratch_dir is None
    ds.close()
    assert sorted(os.listdir(config_dir / "scratch")) == [
        "other.db-wal",
        "private_notes.db",
    ]


@pytest.mark.asyncio
async def test_config_dir_scratch_shared_by_two_instances(tmp_path):
    # Contract C3: a Datasette-created config_dir/scratch locked out a
    # second process on the same config dir (datasette cfg --get ...)
    config_dir = _config_dir(tmp_path)
    first = Datasette(config_dir=config_dir)
    assert first.scratch_dir == str((config_dir / "scratch").resolve())
    await first.create_scratch_database("notes")
    assert (config_dir / "scratch" / REGISTRY_FILENAME).exists()
    assert (config_dir / "scratch" / LOCK_FILENAME).exists()
    second = Datasette(config_dir=config_dir)
    assert "notes" not in second.databases
    second.close()
    first.close()
    # Once the first has gone, the directory is used again
    third = Datasette(config_dir=config_dir)
    assert "notes" in third.databases
    third.close()


def test_first_use_of_explicit_scratch_dir_keeps_sidecars(tmp_path, caplog):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "gone.db-wal").write_bytes(b"junk")
    with caplog.at_level(logging.WARNING, logger="datasette.scratch"):
        Datasette(scratch_dir=str(scratch)).close()
    assert (scratch / "gone.db-wal").exists()
    assert "gone.db-wal" in caplog.text
    # The next start (the registry exists now) removes it
    Datasette(scratch_dir=str(scratch)).close()
    assert not (scratch / "gone.db-wal").exists()


FTS_SETUP = """
create table docs (id integer primary key, body text);
create virtual table docs_fts using fts5(body, content='docs', content_rowid='id');
create trigger docs_ai after insert on docs begin
  insert into docs_fts (rowid, body) values (new.id, new.body);
end;
create trigger docs_ad after delete on docs begin
  insert into docs_fts (docs_fts, rowid, body) values ('delete', old.id, old.body);
end;
insert into docs (id, body) values (1, 'oldword');
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", (3, 0))
async def test_write_connection_pins_recursive_triggers(tmp_path, num_sql_threads):
    # Contract C4: INSERT OR REPLACE on a sqlite-utils style FTS table only
    # cleaned up the old index entry once some code had wrapped the write
    # connection in sqlite_utils.Database (recursive_triggers drift) - and
    # the idle write thread's fresh connections lost the drift again
    path = str(tmp_path / "fts.db")
    make_db(path, FTS_SETUP)
    ds = Datasette(
        [path],
        settings={
            "num_sql_threads": num_sql_threads,
            "connection_idle_timeout_ms": 50,
        },
    )
    db = ds.get_database("fts")
    for word in ("newword", "newerword"):
        flag = await db.execute_write_fn(
            lambda conn: conn.execute("PRAGMA recursive_triggers").fetchone()[0]
        )
        assert flag == 1
        await db.execute_write(
            "insert or replace into docs (id, body) values (1, ?)", [word]
        )
        # Let the idle write thread exit: the next write opens a new
        # connection
        await asyncio.sleep(0.3)
    stale = await db.execute(
        "select rowid from docs_fts where docs_fts match 'oldword OR newword'"
    )
    assert stale.rows == []
    ds.close()


@pytest.mark.asyncio
async def test_new_memory_database_is_not_scanned_at_registration():
    # Contract C5: add_memory_database() after startup scanned the
    # shared-cache database straight away, racing plugins that fill it from
    # their own connection (datasette-copy-to-memory's VACUUM INTO failed
    # with "database table is locked")
    ds = Datasette(settings={"schema_watch_interval_ms": 0})
    await ds.invoke_startup()
    watcher = ds._schema_watcher
    scans = watcher.counters["scans"]
    memory = ds.add_memory_database("review_fixes_copy", name="copy")
    await memory.execute("select 1 + 1")
    await asyncio.sleep(0.05)
    assert watcher.counters["scans"] == scans
    state = memory._watch_state
    assert state.scan_future is None
    assert state in watcher._pending
    # Fill it from a connection of its own, as the plugin does. (The plugin
    # uses VACUUM INTO from a connection opened with uri=True; a plain
    # connection is simpler and does not depend on whether the SQLite build
    # treats URI file names as URIs without that flag)
    conn = sqlite3.connect("file:review_fixes_copy?mode=memory&cache=shared", uri=True)
    conn.execute("create table copied (id integer)")
    conn.commit()
    conn.close()
    # The next request scans it
    response = await ds.client.get("/copy.json")
    assert {t["name"] for t in response.json()["tables"]} == {"copied"}
    ds.close()


@pytest.mark.asyncio
async def test_locked_memory_scan_is_retried(monkeypatch):
    ds = Datasette(settings={"schema_watch_interval_ms": 0})
    await ds.invoke_startup()
    memory = ds.add_memory_database("review_fixes_locked", name="locked")
    original = schema_watcher_module.collect_schema
    locked = threading.Event()
    locked.set()

    def locked_scan(conn, name):
        if locked.is_set():
            raise sqlite3.OperationalError("database table is locked: sqlite_master")
        return original(conn, name)

    monkeypatch.setattr(schema_watcher_module, "collect_schema", locked_scan)
    await memory.execute_write("create table m (id integer)")
    for _ in range(5):
        await asyncio.sleep(0.01)
    state = memory._watch_state
    # Transient: not recorded as a broken database
    assert state.error is None
    assert state.needs_scan
    locked.clear()
    state.retry_at = 0.0
    await ds.client.get("/locked.json")
    assert await catalog_tables(ds, "locked") == ["m"]
    ds.close()


@pytest.mark.asyncio
async def test_stale_owned_catalog_is_not_persisted_at_close(tmp_path):
    # Contract C6: close() stored a fresh fingerprint for an owned database
    # whose catalog was stale, so a restart restored the stale catalog
    path = str(tmp_path / "data.db")
    internal = str(tmp_path / "internal.db")
    make_db(path)
    config = {"databases": {"data": {"schema_watch": "owned"}}}

    async def tables_after_start(change_before_close=None):
        ds = Datasette([path], internal=internal, config=config)
        await ds.invoke_startup()
        response = await ds.client.get("/data.json")
        if change_before_close:
            # Behind Datasette's back, after the last request: an owned
            # database is not polled, so the catalog is now stale
            change_before_close()
        ds.close()
        return [t["name"] for t in response.json()["tables"]]

    assert await tables_after_start() == ["t"]
    assert await tables_after_start(
        lambda: make_db(path, "create table t2 (id integer)")
    ) == ["t"]
    # The stale catalog was not persisted as current: the restart rescans
    assert await tables_after_start() == ["t", "t2"]


@pytest.mark.asyncio
async def test_crossdb_memory_connections_are_reaped_and_refreshed(tmp_path):
    # Contract C7: --crossdb _memory connections ATTACH up to ten files but
    # were never counted, reaped or refreshed: the fds stayed open and a
    # removed database stayed queryable
    paths = []
    for i in range(3):
        path = str(tmp_path / f"db{i}.db")
        make_db(path)
        paths.append(path)
    ds = Datasette(paths[:2], crossdb=True, settings={"connection_idle_timeout_ms": 50})
    await ds.invoke_startup()
    memory = ds.get_database("_memory")
    assert (await memory.execute("select count(*) from db0.t")).first()[0] == 0
    pool = ds._read_pool
    assert pool.snapshot()["open"] >= 1
    deadline = time.monotonic() + 5
    while pool.snapshot()["open"]:
        assert time.monotonic() < deadline, "crossdb connections were not reaped"
        await asyncio.sleep(0.05)
    # Added and removed databases are reflected immediately
    await memory.execute("select 1")
    ds.add_database(Database(ds, path=paths[2]), name="db2")
    assert (await memory.execute("select count(*) from db2.t")).first()[0] == 0
    ds.remove_database("db0")
    with pytest.raises(sqlite3.OperationalError):
        await memory.execute("select count(*) from db0.t")
    ds.close()


@pytest.mark.asyncio
async def test_execute_fn_may_return_generator_over_fetched_rows():
    # Contract C8: every generator was rejected, including harmless ones
    ds = Datasette(memory=True)
    db = ds.get_database("_memory")

    def fetched(conn):
        rows = conn.execute("select 1 union all select 2").fetchall()
        return (row[0] for row in rows)

    assert list(await db.execute_fn(fetched)) == [1, 2]

    def live_cursor(conn):
        return (row[0] for row in conn.execute("select 1"))

    with pytest.raises(ConnectionLeaseError):
        await db.execute_fn(live_cursor)

    def closure(conn):
        def rows():
            yield from conn.execute("select 1")

        return rows()

    with pytest.raises(ConnectionLeaseError):
        await db.execute_fn(closure)
    ds.close()


# ----------------------------------------------------------------------
# Found by the torture harness (tests/connection_torture.py)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_survives_database_removed_mid_request(tmp_path, monkeypatch):
    # The index page looked databases up by name again after listing them:
    # one removed meanwhile (remove_database(), a scratch delete or rename)
    # failed the whole page with a KeyError
    from datasette.views import index as index_module

    paths = []
    for name in ("keep", "gone"):
        path = str(tmp_path / f"{name}.db")
        make_db(path)
        paths.append(path)
    ds = Datasette(paths)
    await ds.invoke_startup()
    original = index_module.catalog_summaries

    async def remove_during(datasette, names):
        result = await original(datasette, names)
        if "gone" in datasette.databases:
            datasette.remove_database("gone")
        return result

    monkeypatch.setattr(index_module, "catalog_summaries", remove_during)
    response = await ds.client.get("/.json")
    assert response.status_code == 200
    assert [d["name"] for d in response.json()["databases"]] == ["keep"]
    ds.close()


@pytest.mark.asyncio
async def test_index_and_databases_with_closed_or_deleted_database(tmp_path):
    paths = []
    for name in ("keep", "closed", "deleted"):
        path = str(tmp_path / f"{name}.db")
        make_db(path)
        paths.append(path)
    ds = Datasette(paths, settings={"schema_watch_interval_ms": 0})
    await ds.invoke_startup()
    # Closed by a plugin but still attached
    ds.get_database("closed").close()
    # Deleted from under Datasette before any sweep noticed
    os.unlink(paths[2])
    for path in ("/.json", "/", "/-/databases.json"):
        response = await ds.client.get(path)
        assert response.status_code == 200, (path, response.text[:300])
    databases = (await ds.client.get("/-/databases.json")).json()
    if isinstance(databases, dict):
        databases = databases["databases"]
    sizes = {d["name"]: d["size"] for d in databases}
    assert sizes["deleted"] == 0
    ds.close()


@pytest.mark.asyncio
async def test_cursors_are_closed_before_leaving_their_thread(owned_db):
    # A cursor still references its connection's cached statement, and on
    # Python < 3.12 freeing it resets that statement. execute_write_many()
    # handed its cursor to the caller's event loop thread; freed there while
    # the write thread ran the same SQL again, the write failed with
    # "bad parameter or other API misuse"
    _ds, db, _path = owned_db
    cursor = await db.execute_write_many(
        "insert into t (v) values (?)", [("a",), ("b",)]
    )
    assert cursor.rowcount == 2
    with pytest.raises(sqlite3.ProgrammingError):
        cursor.fetchall()
    cursor = await db.execute_write_fn(
        lambda conn: conn.execute("insert into t (v) values ('c')")
    )
    assert cursor.lastrowid == 3
    with pytest.raises(sqlite3.ProgrammingError):
        cursor.fetchone()

    # Cursors held by the frames of an exception raised by a callback are
    # closed before the connection goes back to the pool (or write thread)
    captured = []

    def read_then_fail(conn):
        cursor = conn.execute("select * from t")
        captured.append(cursor)
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await db.execute_fn(read_then_fail)
    with pytest.raises(ValueError):
        await db.execute_write_fn(read_then_fail)
    for cursor in captured:
        with pytest.raises(sqlite3.ProgrammingError):
            cursor.fetchone()


def test_startup_from_several_event_loops_runs_once(monkeypatch):
    # _startup_sequence() serialized startup with an asyncio.Lock: callers
    # on a second event loop got "is bound to a different event loop" (or,
    # free-threaded, waited forever), and invoke_startup() itself let two
    # loops run every startup hook twice
    ds = Datasette(memory=True)
    calls = []
    original = Datasette._apply_column_types_config

    async def slow(self):
        calls.append(threading.current_thread().name)
        await asyncio.sleep(0.3)
        return await original(self)

    monkeypatch.setattr(Datasette, "_apply_column_types_config", slow)

    def starter(use_sequence):
        async def run():
            if use_sequence:
                await ds._startup_sequence()
            else:
                await ds.invoke_startup()
            assert ds._startup_invoked
            return (await ds.client.get("/_memory.json")).status_code

        return run

    statuses = run_in_threads(starter(True), starter(True), starter(False))
    assert statuses == [200, 200, 200]
    assert len(calls) == 1
    ds.close()


@pytest.mark.asyncio
async def test_startup_hook_can_make_requests():
    # A request made from inside startup (same task) must not wait for
    # startup to finish - that would wait forever
    from datasette import hookimpl
    from datasette.plugins import pm

    statuses = []

    class Plugin:
        __name__ = "StartupRequestPlugin"

        @hookimpl
        def startup(self, datasette):
            async def inner():
                statuses.append(
                    (await datasette.client.get("/-/versions.json")).status_code
                )

            return inner

    pm.register(Plugin(), name="startup_request_plugin")
    try:
        ds = Datasette(memory=True)
        await asyncio.wait_for(ds.invoke_startup(), 10)
        assert statuses == [200]
        ds.close()
    finally:
        pm.unregister(name="startup_request_plugin")


def test_execute_write_many_from_several_event_loops(owned_db):
    # T1 end to end: on Python 3.11 before the fix, dozens of these calls
    # failed with "bad parameter or other API misuse" (3 of 3 runs)
    _ds, db, _path = owned_db
    errors = []

    def loop(i):
        async def run():
            async def worker(t):
                for seq in range(60):
                    try:
                        await db.execute_write_many(
                            "insert into t (v) values (?)", [(f"{i}-{t}-{seq}",)]
                        )
                    except Exception as e:  # noqa: BLE001
                        errors.append(repr(e))

            await asyncio.gather(*(worker(t) for t in range(5)))

        return run

    run_in_threads(loop(0), loop(1), loop(2))
    assert errors == []
