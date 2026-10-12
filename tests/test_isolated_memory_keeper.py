import sqlite3
import uuid

import pytest

from datasette.app import Datasette
from datasette.write_budget import DatabaseResourceError


@pytest.mark.asyncio
@pytest.mark.parametrize("threads", [0, 2])
@pytest.mark.parametrize("raise_after_commit", [False, True])
async def test_first_isolated_memory_write_keeps_committed_data(
    threads, raise_after_commit
):
    ds = Datasette(settings={"num_sql_threads": threads, "schema_watch_interval_ms": 0})
    db = ds.add_memory_database(uuid.uuid4().hex)
    try:

        def create(conn):
            conn.execute("create table t(id)")
            conn.execute("insert into t values (123)")
            conn.commit()
            if raise_after_commit:
                raise ValueError("after commit")
            return conn.execute("select id from t").fetchone()[0]

        if raise_after_commit:
            with pytest.raises(ValueError, match="after commit"):
                await db.execute_isolated_fn(create)
        else:
            assert await db.execute_isolated_fn(create) == 123
        # Only the keeper remains after the isolated callback, on both
        # success and failure. Do not open a reader before checking this.
        assert len(db._all_connections) == 1
        assert (await db.execute("select id from t")).single_value() == 123
        assert (
            await db.execute_isolated_fn(
                lambda conn: conn.execute("select id from t").fetchone()[0]
            )
            == 123
        )
        await db.execute_write("insert into t values (456)")
        assert (await db.execute("select count(*) from t")).single_value() == 2
        connections = list(db._all_connections)
    finally:
        ds.close()
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("select 1")


@pytest.mark.asyncio
@pytest.mark.parametrize("threads", [0, 2])
async def test_isolated_memory_keeper_occupies_writer_slot(threads, tmp_path):
    path = tmp_path / "file.db"
    conn = sqlite3.connect(path)
    conn.execute("create table t(id)")
    conn.close()
    ds = Datasette(
        [str(path)],
        settings={
            "num_sql_threads": threads,
            "max_write_connections": 1,
            "write_queue_timeout_ms": 50,
            "schema_watch_interval_ms": 0,
        },
    )
    memory = ds.add_memory_database(uuid.uuid4().hex)
    file_db = ds.get_database("file")
    try:
        await memory.execute_isolated_fn(
            lambda conn: conn.execute("create table t(id)")
        )
        with pytest.raises(DatabaseResourceError):
            await file_db.execute_write("insert into t values (1)")
        assert "t" in await memory.table_names()
        memory.close()
        await file_db.execute_write("insert into t values (1)")
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("threads", [0, 2])
async def test_keeper_preparation_failure_does_not_run_isolated_callback(
    threads, monkeypatch
):
    ds = Datasette(
        settings={
            "num_sql_threads": threads,
            "max_write_connections": 1,
            "schema_watch_interval_ms": 0,
        }
    )
    db = ds.add_memory_database(uuid.uuid4().hex)
    original = ds._prepare_connection
    ran = []

    def prepare(conn, name):
        if name == db.name:
            raise ValueError("keeper failed")
        return original(conn, name)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(ds, "_prepare_connection", prepare)
            with pytest.raises(ValueError, match="keeper failed"):
                await db.execute_isolated_fn(lambda conn: ran.append(True))
        assert not ran
        assert not db._all_connections
        db.close()
        another = ds.add_memory_database(uuid.uuid4().hex)
        await another.execute_write("create table t(id)")
    finally:
        ds.close()
