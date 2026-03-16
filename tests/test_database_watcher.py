import asyncio
import sqlite3
import tempfile
import time

import pytest
import pytest_asyncio

from datasette.app import Datasette


@pytest_asyncio.fixture
async def ds_with_file_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    conn = sqlite3.connect(tmp_path)
    conn.execute("CREATE TABLE t (id integer primary key)")
    conn.close()
    ds = Datasette(files=[tmp_path])
    client = ds.client
    # Trigger startup (which starts the watchers)
    await ds.invoke_startup()
    await ds._start_database_watchers()
    try:
        yield ds, client, tmp_path
    finally:
        await ds._stop_database_watchers()


@pytest.mark.asyncio
async def test_database_updated_initially_empty(ds_with_file_db):
    ds, client, tmp_path = ds_with_file_db
    db_name = list(ds.databases.keys())[0]
    assert db_name not in ds._database_updated


@pytest.mark.asyncio
async def test_database_updated_after_write(ds_with_file_db):
    ds, client, tmp_path = ds_with_file_db
    db_name = list(ds.databases.keys())[0]
    if db_name == "_memory":
        db_name = list(ds.databases.keys())[1]

    # Write to the database
    conn = sqlite3.connect(tmp_path)
    conn.execute("INSERT INTO t (id) VALUES (1)")
    conn.commit()
    conn.close()

    # Wait for the watcher to detect the change (interval is 1s by default)
    await asyncio.sleep(1.5)

    assert db_name in ds._database_updated
    assert isinstance(ds._database_updated[db_name], float)


@pytest.mark.asyncio
async def test_databases_endpoint_includes_last_updated(ds_with_file_db):
    ds, client, tmp_path = ds_with_file_db
    db_name = list(ds.databases.keys())[0]
    if db_name == "_memory":
        db_name = list(ds.databases.keys())[1]

    # Before any writes, last_updated should be None
    response = await client.get("/-/databases.json")
    databases = response.json()
    file_db = [d for d in databases if d["name"] == db_name][0]
    assert file_db["last_updated"] is None

    # Write to the database
    conn = sqlite3.connect(tmp_path)
    conn.execute("INSERT INTO t (id) VALUES (1)")
    conn.commit()
    conn.close()

    # Wait for the watcher to detect the change
    await asyncio.sleep(1.5)

    response = await client.get("/-/databases.json")
    databases = response.json()
    file_db = [d for d in databases if d["name"] == db_name][0]
    assert file_db["last_updated"] is not None
    assert isinstance(file_db["last_updated"], float)


@pytest.mark.asyncio
async def test_database_updated_timestamp_increases(ds_with_file_db):
    ds, client, tmp_path = ds_with_file_db
    db_name = list(ds.databases.keys())[0]
    if db_name == "_memory":
        db_name = list(ds.databases.keys())[1]

    # First write
    conn = sqlite3.connect(tmp_path)
    conn.execute("INSERT INTO t (id) VALUES (1)")
    conn.commit()
    conn.close()
    await asyncio.sleep(1.5)
    first_ts = ds._database_updated[db_name]

    # Second write
    conn = sqlite3.connect(tmp_path)
    conn.execute("INSERT INTO t (id) VALUES (2)")
    conn.commit()
    conn.close()
    await asyncio.sleep(1.5)
    second_ts = ds._database_updated[db_name]

    assert second_ts > first_ts
