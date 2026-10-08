"""SQLite resources must not retain locks or run after their callback ends."""

import operator
import sqlite3

import pytest

from datasette.app import Datasette
from datasette.connection_pool import ConnectionLeaseError


@pytest.fixture
def resource_db(tmp_path):
    path = tmp_path / "resources.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "create table t(id integer primary key, data blob);"
        "insert into t(data) values (x'616263646566'), (zeroblob(6)), (zeroblob(6));"
        "create table another(id);"
    )
    conn.close()
    return str(path)


def callback_method(db, kind):
    return {
        "read": db.execute_fn,
        "write": db.execute_write_fn,
        "isolated": db.execute_isolated_fn,
    }[kind]


def check_no_read_lock(path):
    # A retained cursor/blob would block this commit in rollback journal mode.
    conn = sqlite3.connect(path, timeout=0)
    try:
        conn.execute("insert into another values (1)")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
@pytest.mark.parametrize("kind", ["read", "write", "isolated", "immutable_isolated"])
@pytest.mark.parametrize("started", [False, True])
async def test_stashed_dump_iterator_expires_and_releases_lock(
    resource_db, num_sql_threads, kind, started
):
    ds = Datasette(
        **{("immutables" if kind == "immutable_isolated" else "files"): [resource_db]},
        settings={"num_sql_threads": num_sql_threads},
    )
    db = ds.get_database("resources")
    saved = {}

    def callback(conn):
        saved["dump"] = dump = conn.iterdump()
        saved["next"] = dump.__next__
        saved["send"] = dump.send
        saved["close"] = dump.close
        assert iter(dump) is dump
        if started:
            for statement in dump:
                if statement.startswith('INSERT INTO "t"'):
                    break
            else:
                pytest.fail("dump did not reach a data row")
        return "done"

    try:
        assert (
            await callback_method(db, kind.replace("immutable_", ""))(callback)
            == "done"
        )
        for call in (
            saved["next"],
            lambda: iter(saved["dump"]),
            lambda: saved["send"](None),
            lambda: saved["dump"].throw(ValueError),
            saved["close"],
        ):
            with pytest.raises(ConnectionLeaseError):
                call()
        if kind != "immutable_isolated":
            check_no_read_lock(resource_db)
        assert (await db.execute("select count(*) from t")).single_value() == 3
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
@pytest.mark.parametrize("kind", ["read", "write", "isolated"])
async def test_dump_can_be_consumed_inside_callback(resource_db, num_sql_threads, kind):
    ds = Datasette([resource_db], settings={"num_sql_threads": num_sql_threads})
    try:
        statements = await callback_method(ds.get_database("resources"), kind)(
            lambda conn: list(conn.iterdump())
        )
        restored = sqlite3.connect(":memory:")
        try:
            restored.executescript("\n".join(statements))
            assert (
                restored.execute("select data from t where id=1").fetchone()[0]
                == b"abcdef"
            )
        finally:
            restored.close()
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_returning_dump_from_read_callback_is_rejected(resource_db, nested):
    ds = Datasette([resource_db])

    def callback(conn):
        dump = conn.iterdump()
        return {"dump": [dump]} if nested else dump

    try:
        with pytest.raises(ConnectionLeaseError, match="database resources"):
            await ds.get_database("resources").execute_fn(callback)
        check_no_read_lock(resource_db)
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_generator_over_dump_from_read_callback_is_rejected(resource_db):
    ds = Datasette([resource_db])
    try:
        with pytest.raises(ConnectionLeaseError, match="database resources"):
            await ds.get_database("resources").execute_fn(
                lambda conn: (line for line in conn.iterdump())
            )
        check_no_read_lock(resource_db)
    finally:
        ds.close()


requires_blob = pytest.mark.skipif(
    not hasattr(sqlite3.Connection, "blobopen"),
    reason="Incremental BLOB access requires Python 3.11",
)


@requires_blob
@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
@pytest.mark.parametrize("kind", ["read", "write", "isolated", "immutable_isolated"])
async def test_stashed_blob_expires_and_releases_lock(
    resource_db, num_sql_threads, kind
):
    ds = Datasette(
        **{("immutables" if kind == "immutable_isolated" else "files"): [resource_db]},
        settings={"num_sql_threads": num_sql_threads},
    )
    saved = {}

    def callback(conn):
        saved["blob"] = blob = conn.blobopen("t", "data", 1, readonly=True)
        saved["read"] = blob.read
        saved["close"] = blob.close
        assert isinstance(blob, sqlite3.Blob)
        assert len(blob) == 6
        assert blob[0] == ord("a")
        assert blob[1:3] == b"bc"
        assert blob.read(2) == b"ab"
        assert blob.tell() == 2
        blob.seek(-1, 2)
        assert saved["read"]() == b"f"

    try:
        await callback_method(
            ds.get_database("resources"), kind.replace("immutable_", "")
        )(callback)
        blob = saved["blob"]
        for call in (
            saved["read"],
            saved["close"],
            lambda: blob.write(b"x"),
            lambda: blob.tell(),
            lambda: blob.seek(0),
            lambda: len(blob),
            lambda: blob[0],
            lambda: blob[:],
            lambda: operator.setitem(blob, 0, 120),
            lambda: operator.setitem(blob, slice(0, 1), b"x"),
            blob.__enter__,
            lambda: blob.__exit__(None, None, None),
        ):
            with pytest.raises(ConnectionLeaseError):
                call()
        if kind != "immutable_isolated":
            check_no_read_lock(resource_db)
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["write", "isolated"])
@pytest.mark.parametrize(
    "resource", ["dump", pytest.param("blob", marks=requires_blob)]
)
async def test_returned_write_resource_is_expired(resource_db, kind, resource):
    ds = Datasette([resource_db])

    def callback(conn):
        if resource == "dump":
            return conn.iterdump()
        return conn.blobopen("t", "data", 1, readonly=True)

    try:
        result = await callback_method(ds.get_database("resources"), kind)(callback)
        with pytest.raises(ConnectionLeaseError):
            if resource == "dump":
                next(result)
            else:
                result.read()
        check_no_read_lock(resource_db)
    finally:
        ds.close()


@requires_blob
@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 2])
async def test_writable_blob_closes_before_transaction_commits(
    resource_db, num_sql_threads
):
    ds = Datasette([resource_db], settings={"num_sql_threads": num_sql_threads})
    db = ds.get_database("resources")
    saved = {}

    def write(conn):
        saved["blob"] = blob = conn.blobopen("t", "data", 1)
        blob.write(b"xy")
        blob[2] = ord("z")
        blob[3:] = b"123"
        # Leaving this BLOB open on SQLite makes the enclosing COMMIT fail
        # with "SQL statements in progress"; expiry must close it first.
        return "written"

    try:
        assert await db.execute_write_fn(write) == "written"
        assert (
            await db.execute("select data from t where id=1")
        ).single_value() == b"xyz123"
        with pytest.raises(ConnectionLeaseError):
            saved["blob"].read()
    finally:
        ds.close()


@requires_blob
@pytest.mark.asyncio
async def test_blob_context_manager_returns_lease(resource_db):
    ds = Datasette([resource_db])
    saved = {}

    def write(conn):
        with conn.blobopen("t", "data", 1) as blob:
            saved["blob"] = blob
            blob.write(b"xyz")
        with pytest.raises(sqlite3.ProgrammingError):
            blob.read()

    try:
        await ds.get_database("resources").execute_write_fn(write)
        with pytest.raises(ConnectionLeaseError):
            saved["blob"].read()
    finally:
        ds.close()


@requires_blob
@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_returning_blob_from_read_callback_is_rejected(resource_db, nested):
    ds = Datasette([resource_db])

    def callback(conn):
        blob = conn.blobopen("t", "data", 1, readonly=True)
        return {"blob": [blob]} if nested else blob

    try:
        with pytest.raises(ConnectionLeaseError, match="database resources"):
            await ds.get_database("resources").execute_fn(callback)
        check_no_read_lock(resource_db)
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["read", "write", "isolated"])
@pytest.mark.parametrize(
    "resource", ["dump", pytest.param("blob", marks=requires_blob)]
)
async def test_resources_close_when_callback_raises(resource_db, kind, resource):
    ds = Datasette([resource_db])
    saved = {}

    def fail(conn):
        if resource == "dump":
            saved["resource"] = value = conn.iterdump()
            for statement in value:
                if statement.startswith('INSERT INTO "t"'):
                    break
        else:
            saved["resource"] = conn.blobopen("t", "data", 1, readonly=True)
        raise ValueError("callback failed")

    try:
        with pytest.raises(ValueError, match="callback failed"):
            await callback_method(ds.get_database("resources"), kind)(fail)
        with pytest.raises(ConnectionLeaseError):
            if resource == "dump":
                next(saved["resource"])
            else:
                saved["resource"].read()
        check_no_read_lock(resource_db)
    finally:
        ds.close()
