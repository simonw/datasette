"""
SchemaWatcher tests. Changes to "external" databases are made from a
separate OS process (a child Python interpreter using plain sqlite3, or the
sqlite-utils CLI), the way other tools change files Datasette is serving.
"""

import asyncio
import os
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

from datasette import schema_watcher as schema_watcher_module
from datasette.app import Datasette
from datasette.database import Database

INTERVAL_MS = 50


def run_external(path, sql, journal_mode=None, keep_wal=False):
    """Run sql against path in a separate Python process; returns the
    child's time.monotonic() right after COMMIT."""
    script = textwrap.dedent("""
        import sqlite3, sys, time
        path, sql, journal_mode, keep_wal = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
        conn = sqlite3.connect(path, isolation_level=None, timeout=10)
        if journal_mode:
            conn.execute("PRAGMA journal_mode=" + journal_mode)
        if keep_wal == "1":
            # Never checkpoint: changes stay in the -wal file
            conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.executescript(sql)
        print(time.monotonic(), flush=True)
        if keep_wal == "1":
            # Keep the connection open so close() does not checkpoint
            sys.stdin.read()
        conn.close()
        """)
    if keep_wal:
        proc = subprocess.Popen(
            [sys.executable, "-c", script, path, sql, journal_mode or "", "1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        line = proc.stdout.readline()
        return float(line), proc
    out = subprocess.run(
        [sys.executable, "-c", script, path, sql, journal_mode or "", "0"],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


def run_process(*args):
    subprocess.run([sys.executable, *args], check=True)


def make_db(path, sql="create table t (id integer primary key, a text)", wal=False):
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=wal")
    conn.executescript(sql)
    conn.execute("insert into t (a) values ('one')")
    conn.commit()
    conn.close()


async def catalog_columns(ds, db_name, table):
    rows = await ds.get_internal_database().execute(
        "select name from catalog_columns where database_name = ? and table_name = ? order by cid",
        [db_name, table],
    )
    return [r[0] for r in rows.rows]


async def catalog_tables(ds, db_name):
    rows = await ds.get_internal_database().execute(
        "select table_name from catalog_tables where database_name = ? order by table_name",
        [db_name],
    )
    return [r[0] for r in rows.rows]


async def wait_for(check, timeout=5.0):
    t0 = time.monotonic()
    while True:
        result = await check()
        if result:
            return time.monotonic() - t0
        if time.monotonic() - t0 > timeout:
            raise AssertionError(f"timed out after {timeout}s")
        await asyncio.sleep(0.01)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "ext.db")
    make_db(path)
    return path


async def start_ds(paths, interval_ms=INTERVAL_MS, settings=None, **kwargs):
    ds = Datasette(
        files=paths,
        settings={"schema_watch_interval_ms": interval_ms, **(settings or {})},
        **kwargs,
    )
    await ds.invoke_startup()
    await ds.start_background_tasks()
    return ds


@pytest.mark.asyncio
async def test_modes_and_defaults(tmp_path, db_path):
    immutable_path = str(tmp_path / "imm.db")
    make_db(immutable_path)
    ds = Datasette(files=[db_path], immutables=[immutable_path])
    await ds.invoke_startup()
    watcher = ds._schema_watcher
    assert watcher.states["ext"].mode == "external"
    assert watcher.states["imm"].mode == "immutable"
    added = ds.add_database(Database(ds, memory_name="sw_owned_mem"), name="added")
    assert watcher.states["added"].mode == "owned"
    assert added._watch_state is watcher.states["added"]
    ds.close()


@pytest.mark.asyncio
async def test_mode_from_config(tmp_path, db_path):
    ds = Datasette(
        files=[db_path], config={"databases": {"ext": {"schema_watch": "owned"}}}
    )
    assert ds._schema_watcher.states["ext"].mode == "owned"
    ds.close()


@pytest.mark.asyncio
async def test_no_request_path_sweep(db_path):
    ds = await start_ds([db_path], interval_ms=0)
    watcher = ds._schema_watcher
    sweeps = watcher.counters["sweeps"]
    for _ in range(5):
        response = await ds.client.get("/ext/t.json")
        assert response.status_code == 200
    assert watcher.counters["sweeps"] == sweeps
    ds.close()


@pytest.mark.asyncio
async def test_owned_write_path_detects_schema_change(tmp_path):
    ds = Datasette(settings={"schema_watch_interval_ms": 0})
    await ds.invoke_startup()
    db = ds.add_database(Database(ds, memory_name="sw_owned"), name="owned")
    await ds.refresh_schemas(force=True)
    watcher = ds._schema_watcher
    scans = watcher.counters["scans"]
    # Data-only writes: no catalog rebuild
    await db.execute_write("create table if not exists t (id integer primary key)")
    assert await catalog_tables(ds, "owned") == ["t"]
    scans = watcher.counters["scans"]
    for i in range(5):
        await db.execute_write("insert into t default values")
    assert watcher.counters["scans"] == scans
    # Each write API: catalog is current as soon as the call returns
    await db.execute_write("create table t2 (id integer primary key)")
    assert "t2" in await catalog_tables(ds, "owned")
    await db.execute_write_script("create table t3 (id integer); create table t4 (id);")
    assert {"t3", "t4"} <= set(await catalog_tables(ds, "owned"))
    scans = watcher.counters["scans"]
    await db.execute_write_many("insert into t2 (id) values (?)", [[1], [2]])
    assert watcher.counters["scans"] == scans

    def plugin_write(conn):
        conn.execute("alter table t add column extra text")

    await db.execute_write_fn(plugin_write)
    assert await catalog_columns(ds, "owned", "t") == ["id", "extra"]

    def isolated(conn):
        conn.execute("create table t6 (id)")

    await db.execute_isolated_fn(isolated)
    assert "t6" in await catalog_tables(ds, "owned")
    # block=False: refreshed in the background
    await db.execute_write("create table t7 (id)", block=False)
    await wait_for(lambda: _contains(ds, "owned", "t7"))
    ds.close()


async def _contains(ds, db_name, table):
    return table in await catalog_tables(ds, db_name)


@pytest.mark.asyncio
async def test_owned_write_path_non_threaded(tmp_path):
    path = str(tmp_path / "nt.db")
    make_db(path)
    ds = Datasette(settings={"num_sql_threads": 0, "schema_watch_interval_ms": 0})
    await ds.invoke_startup()
    db = ds.add_database(Database(ds, path=path), name="nt")
    await ds._schema_watcher.flush_pending()
    await db.execute_write("create table t2 (id)")
    assert await catalog_tables(ds, "nt") == ["t", "t2"]
    ds.close()


@pytest.mark.asyncio
async def test_external_data_only_insert_does_not_rescan(db_path):
    ds = await start_ds([db_path])
    watcher = ds._schema_watcher
    state = watcher.states["ext"]
    scans = state.stats["scans"]
    checks = state.stats["checks"]
    for i in range(3):
        run_external(db_path, f"insert into t (a) values ('x{i}')")
        await asyncio.sleep(INTERVAL_MS * 3 / 1000)
    # The fingerprint changed, so schema_version was checked...
    assert state.stats["checks"] > checks
    # ... but the catalog was not rebuilt
    assert state.stats["scans"] == scans
    ds.close()


@pytest.mark.asyncio
async def test_external_add_column(db_path):
    ds = await start_ds([db_path])
    run_external(db_path, "alter table t add column b text")

    async def has_b():
        return await catalog_columns(ds, "ext", "t") == ["id", "a", "b"]

    await wait_for(has_b)
    ds.close()


@pytest.mark.asyncio
async def test_external_sqlite_utils_transform(db_path):
    ds = await start_ds([db_path])
    run_process(
        "-m", "sqlite_utils", "transform", db_path, "t", "--rename", "a", "renamed"
    )

    async def renamed():
        return await catalog_columns(ds, "ext", "t") == ["id", "renamed"]

    await wait_for(renamed)
    # And the table page uses the new column too
    response = await ds.client.get("/ext/t.json?_shape=array")
    assert response.json() == [{"id": 1, "renamed": "one"}]
    ds.close()


@pytest.mark.asyncio
async def test_external_create_table_in_wal_before_checkpoint(tmp_path):
    path = str(tmp_path / "walled.db")
    make_db(path, wal=True)
    ds = await start_ds([path])
    _, proc = run_external(
        path, "create table in_wal (id integer)", journal_mode="wal", keep_wal=True
    )
    try:
        assert os.path.getsize(path + "-wal") > 0

        async def seen():
            return "in_wal" in await catalog_tables(ds, "walled")

        await wait_for(seen)
        # Main file still does not contain it: it was only in the WAL
        assert os.path.exists(path + "-wal")
    finally:
        proc.stdin.close()
        proc.wait()
    ds.close()


@pytest.mark.asyncio
async def test_external_atomic_replace_by_rename(tmp_path, db_path):
    ds = await start_ds([db_path])
    # Warm a cached read connection on the old inode
    response = await ds.client.get("/ext/t.json?_shape=array")
    assert response.json() == [{"id": 1, "a": "one"}]
    replacement = str(tmp_path / "replacement.db")
    conn = sqlite3.connect(replacement)
    # Same schema_version as the original file (1 CREATE TABLE) but a
    # different table: only the inode change gives it away
    conn.execute("create table t (id integer primary key, different text)")
    conn.execute("insert into t (different) values ('new file')")
    conn.commit()
    assert conn.execute("pragma schema_version").fetchone()[0] == 1
    conn.close()
    old_inode = os.stat(db_path).st_ino
    run_process(
        "-c",
        "import os, sys; os.replace(sys.argv[1], sys.argv[2])",
        replacement,
        db_path,
    )
    assert os.stat(db_path).st_ino != old_inode

    async def replaced():
        return await catalog_columns(ds, "ext", "t") == ["id", "different"]

    await wait_for(replaced)
    # Cached connections were invalidated, so queries see the new file
    response = await ds.client.get("/ext/t.json?_shape=array")
    assert response.json() == [{"id": 1, "different": "new file"}]
    ds.close()


@pytest.mark.asyncio
async def test_old_inode_connections_without_watcher(tmp_path, db_path):
    # Documents the hazard the watcher fixes: with polling off, a cached
    # read connection keeps reading the replaced (unlinked) file
    # One SQL thread, so every request reuses the same cached connection
    ds = await start_ds([db_path], interval_ms=0, settings={"num_sql_threads": 1})
    response = await ds.client.get("/ext/t.json?_shape=array")
    assert response.json() == [{"id": 1, "a": "one"}]
    replacement = str(tmp_path / "replacement.db")
    conn = sqlite3.connect(replacement)
    conn.execute("create table t (id integer primary key, a text)")
    conn.execute("insert into t (a) values ('new file')")
    conn.commit()
    conn.close()
    os.replace(replacement, db_path)
    response = await ds.client.get("/ext/t.json?_shape=array")
    assert response.json() == [{"id": 1, "a": "one"}]  # stale!
    # An explicit refresh notices the inode change and fixes it
    await ds.refresh_schemas(force=True)
    response = await ds.client.get("/ext/t.json?_shape=array")
    assert response.json() == [{"id": 1, "a": "new file"}]
    ds.close()


@pytest.mark.asyncio
async def test_external_vacuum(db_path):
    run_external(
        db_path,
        "create table big (x); insert into big select zeroblob(1000) from "
        "(with recursive c(i) as (select 1 union all select i+1 from c where i < 200) select i from c);"
        "drop table big;",
    )
    ds = await start_ds([db_path])
    before = await ds.get_internal_database().execute(
        "select schema_version from catalog_databases where database_name = 'ext'"
    )
    run_external(db_path, "vacuum")

    async def bumped():
        after = await ds.get_internal_database().execute(
            "select schema_version from catalog_databases where database_name = 'ext'"
        )
        return after.first()[0] == before.first()[0] + 1

    await wait_for(bumped)
    ds.close()


@pytest.mark.asyncio
async def test_external_rapid_changes(db_path):
    ds = await start_ds([db_path])
    for i in range(10):
        run_external(db_path, f"create table rapid_{i} (id)")

    async def all_seen():
        tables = await catalog_tables(ds, "ext")
        return all(f"rapid_{i}" in tables for i in range(10))

    await wait_for(all_seen)
    ds.close()


def _coarse_fingerprint(real):
    # Simulate a filesystem with 1 second timestamp granularity
    def fingerprint(path, present=None):
        parts = real(path, present)
        out = []
        for i, part in enumerate(parts):
            if part is None:
                out.append(None)
                continue
            part = list(part)
            # main: (dev, ino, size, mtime, ctime); side files: (ino, size, mtime)
            for idx in (3, 4) if i == 0 else (2,):
                part[idx] = part[idx] // 10**9 * 10**9
            out.append(tuple(part))
        return tuple(out)

    return fingerprint


@pytest.mark.asyncio
@pytest.mark.parametrize("racy_rule", (True, False))
async def test_racily_clean_same_tick_change(db_path, monkeypatch, racy_rule):
    """Two schema changes inside one timestamp tick, same file size and
    inode: only the racy-clean rule catches the second one."""
    monkeypatch.setattr(
        schema_watcher_module,
        "fingerprint",
        _coarse_fingerprint(schema_watcher_module.fingerprint),
    )
    if not racy_rule:
        monkeypatch.setattr(schema_watcher_module, "RACY_WINDOW_NS", -(10**18))
    ds = Datasette(files=[db_path], settings={"schema_watch_interval_ms": 0})
    await ds.invoke_startup()
    # Line the first change up with the start of a fresh second
    await asyncio.sleep(1 - (time.time() % 1) + 0.05)
    size = os.path.getsize(db_path)
    run_external(db_path, "alter table t add column b text")
    await ds._refresh_schemas()  # sweep: sees change 1
    assert await catalog_columns(ds, "ext", "t") == ["id", "a", "b"]
    run_external(db_path, "alter table t add column c text")
    assert os.path.getsize(db_path) == size
    assert time.time() % 1 < 0.9, "test machine too slow to stay in one tick"
    await ds._refresh_schemas()  # sweep: fingerprint identical to last one
    columns = await catalog_columns(ds, "ext", "t")
    if racy_rule:
        assert columns == ["id", "a", "b", "c"]
    else:
        # Without the rule the change is missed
        assert columns == ["id", "a", "b"]
    ds.close()


@pytest.mark.asyncio
async def test_external_file_deleted_and_recreated(tmp_path, db_path):
    ds = await start_ds([db_path])
    assert await catalog_tables(ds, "ext") == ["t"]
    run_process("-c", "import os, sys; os.unlink(sys.argv[1])", db_path)

    async def gone():
        return await catalog_tables(ds, "ext") == []

    await wait_for(gone)
    response = await ds.client.get("/ext/t.json")
    assert response.status_code in (404, 500)
    # Writes fail instead of silently creating a new empty file
    with pytest.raises(sqlite3.OperationalError):
        await ds.get_database("ext").execute_write("insert into t (a) values ('x')")
    assert not os.path.exists(db_path)
    make_db(db_path, sql="create table t (id integer primary key, a text, back text)")

    async def back():
        return await catalog_columns(ds, "ext", "t") == ["id", "a", "back"]

    await wait_for(back)
    response = await ds.client.get("/ext/t.json?_shape=array")
    assert response.json() == [{"id": 1, "a": "one", "back": None}]
    ds.close()


@pytest.mark.asyncio
async def test_remove_database_deletes_catalog_rows(tmp_path, db_path):
    other = str(tmp_path / "other.db")
    make_db(other)
    ds = Datasette(files=[db_path, other])
    await ds.invoke_startup()
    assert await catalog_tables(ds, "other") == ["t"]
    ds.remove_database("other")
    await ds._schema_watcher.on_request()
    internal = ds.get_internal_database()
    for table in (
        "catalog_databases",
        "catalog_tables",
        "catalog_columns",
        "catalog_indexes",
    ):
        rows = await internal.execute(
            f"select count(*) from {table} where database_name = 'other'"
        )
        assert rows.first()[0] == 0
    assert await catalog_tables(ds, "ext") == ["t"]
    ds.close()


@pytest.mark.asyncio
async def test_add_database_visible_on_next_request(tmp_path):
    ds = Datasette(settings={"schema_watch_interval_ms": 0})
    await ds.invoke_startup()
    await ds.client.get("/-/versions.json")
    path = str(tmp_path / "late.db")
    make_db(path)
    ds.add_database(Database(ds, path=path), name="late")
    response = await ds.client.get("/late.json")
    assert response.status_code == 200
    assert await catalog_tables(ds, "late") == ["t"]
    ds.close()


@pytest.mark.asyncio
async def test_persisted_fingerprints_skip_unchanged_files(tmp_path, monkeypatch):
    # Shrink the racy window so freshly written files count as stable
    monkeypatch.setattr(schema_watcher_module, "RACY_WINDOW_NS", 1_000_000)
    internal = str(tmp_path / "internal.db")
    paths = []
    for i in range(3):
        path = str(tmp_path / f"p{i}.db")
        make_db(path)
        paths.append(path)
    await asyncio.sleep(0.05)
    ds = Datasette(files=paths, internal=internal)
    await ds.invoke_startup()
    assert ds._schema_watcher.counters["scans"] == 3
    ds.close()
    # Change one file while Datasette is "down"
    run_external(paths[1], "create table added_offline (id)")
    ds2 = Datasette(files=paths, internal=internal)
    await ds2.invoke_startup()
    counters = ds2._schema_watcher.counters
    assert counters["restored_from_persisted"] == 2
    assert counters["scans"] == 1
    assert await catalog_tables(ds2, "p1") == ["added_offline", "t"]
    assert await catalog_tables(ds2, "p0") == ["t"]
    ds2.close()
