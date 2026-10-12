import errno
import sqlite3

import pytest

from datasette.app import Datasette
from datasette.connection_pool import ConnectionLeaseError


@pytest.fixture
def database_file(tmp_path):
    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    conn.executescript("create table t(id); insert into t values (1), (2)")
    conn.close()
    return str(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["read", "write", "isolated"])
async def test_saved_methods_and_cursors_cannot_escape_callback(database_file, kind):
    ds = Datasette([database_file], settings={"schema_watch_interval_ms": 0})
    db = ds.get_database("data")
    saved = {}

    def callback(conn):
        saved["commit"] = conn.commit
        saved["cursor"] = conn.execute("select * from t")
        saved["fetch"] = saved["cursor"].fetchone
        saved["connection"] = saved["cursor"].connection
        assert saved["fetch"]()[0] == 1

    method = {
        "read": db.execute_fn,
        "write": db.execute_write_fn,
        "isolated": db.execute_isolated_fn,
    }[kind]
    try:
        await method(callback)
        for call in (saved["commit"], lambda: saved["connection"].execute("select 1")):
            with pytest.raises(ConnectionLeaseError):
                call()
        for call in (saved["fetch"], lambda: next(saved["cursor"])):
            with pytest.raises((ConnectionLeaseError, sqlite3.ProgrammingError)):
                call()
        assert (await db.execute("select count(*) from t")).single_value() == 2
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_read_result_containing_cursor_is_rejected(database_file):
    ds = Datasette([database_file])
    db = ds.get_database("data")
    try:
        with pytest.raises(ConnectionLeaseError):
            await db.execute_fn(lambda c: {"nested": [c.execute("select * from t")]})
        result = await db.execute_write_fn(
            lambda c: {"nested": [c.execute("insert into t values (3)")]}
        )
        assert result["nested"][0].rowcount == 1
        with pytest.raises((ConnectionLeaseError, sqlite3.ProgrammingError)):
            result["nested"][0].fetchone()
        assert (await db.execute("select count(*) from t")).single_value() == 3
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        sqlite3.OperationalError("unable to open database file"),
        OSError(errno.EMFILE, "Too many open files"),
    ],
)
async def test_schema_recovers_after_resource_error_without_file_change(
    database_file, monkeypatch, failure
):
    ds = Datasette([database_file], settings={"schema_watch_interval_ms": 0})
    try:
        await ds.invoke_startup()
        db = ds.get_database("data")
        conn = sqlite3.connect(database_file)
        conn.execute("create table added_externally(id)")
        conn.close()
        watcher = ds._schema_watcher
        original = watcher._connect

        def fail(database, prepare):
            if database is db and prepare:
                raise failure
            return original(database, prepare)

        with monkeypatch.context() as patch:
            patch.setattr(watcher, "_connect", fail)
            await ds.refresh_schemas()
        state = db._watch_state
        assert state.needs_scan
        # Move past the retry backoff and fingerprint race window without
        # touching the file or relying on timing-sensitive sleeps.
        state.retry_at = 0
        state.fp_racy = False
        await watcher.sweep(background=True)
        tables = await ds.get_internal_database().execute(
            "select table_name from catalog_tables where database_name=?", ["data"]
        )
        assert {row[0] for row in tables.rows} == {"t", "added_externally"}
        assert state.error is None
        assert not state.needs_scan
    finally:
        ds.close()
