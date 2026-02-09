"""
Tests for the wrap_write plugin hook.
"""

from datasette.app import Datasette
from datasette.hookspecs import hookimpl
from datasette.plugins import pm
import pytest
import time


@pytest.fixture
def datasette(tmp_path):
    db_path = str(tmp_path / "test.db")
    ds = Datasette([db_path])
    return ds


@pytest.mark.asyncio
async def test_wrap_write_before_and_after(datasette):
    """Test that code before and after yield both execute."""
    log = []

    class WrapWritePlugin:
        __name__ = "WrapWritePlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            def wrapper(conn):
                log.append("before")
                yield
                log.append("after")

            return wrapper

    pm.register(WrapWritePlugin(), name="WrapWritePlugin")
    try:
        db = datasette.get_database("test")
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t (id integer primary key)"
            )
        )
        assert log == ["before", "after"]
    finally:
        pm.unregister(name="WrapWritePlugin")


@pytest.mark.asyncio
async def test_wrap_write_receives_result_via_yield(datasette):
    """Test that the result of fn(conn) is sent back through yield."""
    captured = {}

    class WrapWritePlugin:
        __name__ = "WrapWritePlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            def wrapper(conn):
                result = yield
                captured["result"] = result

            return wrapper

    pm.register(WrapWritePlugin(), name="WrapWritePlugin2")
    try:
        db = datasette.get_database("test")
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t2 (id integer primary key)"
            )
        )
        assert "result" in captured
        # Should be a sqlite3 Cursor
        assert captured["result"] is not None
    finally:
        pm.unregister(name="WrapWritePlugin2")


@pytest.mark.asyncio
async def test_wrap_write_exception_thrown_into_generator(datasette):
    """Test that exceptions from fn(conn) are thrown into the generator."""
    caught = {}

    class WrapWritePlugin:
        __name__ = "WrapWritePlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            def wrapper(conn):
                try:
                    yield
                except Exception as e:
                    caught["error"] = e

            return wrapper

    pm.register(WrapWritePlugin(), name="WrapWritePlugin3")
    try:
        db = datasette.get_database("test")
        with pytest.raises(Exception, match="deliberate"):
            await db.execute_write_fn(lambda conn: (_ for _ in ()).throw(Exception("deliberate")))
        assert "error" in caught
        assert str(caught["error"]) == "deliberate"
    finally:
        pm.unregister(name="WrapWritePlugin3")


@pytest.mark.asyncio
async def test_wrap_write_conn_is_usable(datasette):
    """Test that the conn passed to the wrapper can execute SQL."""
    log = []

    class WrapWritePlugin:
        __name__ = "WrapWritePlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            def wrapper(conn):
                conn.execute("create table if not exists hook_log (msg text)")
                conn.execute("insert into hook_log values ('before')")
                yield
                conn.execute("insert into hook_log values ('after')")

            return wrapper

    pm.register(WrapWritePlugin(), name="WrapWritePlugin4")
    try:
        db = datasette.get_database("test")
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t3 (id integer primary key)"
            )
        )
        # Check that both before and after SQL ran
        result = await db.execute("select msg from hook_log order by rowid")
        messages = [row[0] for row in result.rows]
        assert messages == ["before", "after"]
    finally:
        pm.unregister(name="WrapWritePlugin4")


@pytest.mark.asyncio
async def test_wrap_write_multiple_plugins_nest(datasette):
    """Test that multiple wrap_write plugins nest correctly (outermost first)."""
    log = []

    class PluginA:
        __name__ = "PluginA"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            def wrapper(conn):
                log.append("A-before")
                yield
                log.append("A-after")

            return wrapper

    class PluginB:
        __name__ = "PluginB"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            def wrapper(conn):
                log.append("B-before")
                yield
                log.append("B-after")

            return wrapper

    pm.register(PluginA(), name="PluginA")
    pm.register(PluginB(), name="PluginB")
    try:
        db = datasette.get_database("test")
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t4 (id integer primary key)"
            )
        )
        # Both plugins should wrap the write. The exact nesting order
        # follows pluggy's default LIFO convention.
        assert set(log) == {"A-before", "A-after", "B-before", "B-after"}
        # Verify proper nesting: each plugin's before/after should be
        # symmetric around the write
        a_before = log.index("A-before")
        a_after = log.index("A-after")
        b_before = log.index("B-before")
        b_after = log.index("B-after")
        # Whichever plugin is outer should have before first and after last
        if a_before < b_before:
            assert a_after > b_after, "A is outer so A-after should come after B-after"
        else:
            assert b_after > a_after, "B is outer so B-after should come after A-after"
    finally:
        pm.unregister(name="PluginA")
        pm.unregister(name="PluginB")


@pytest.mark.asyncio
async def test_wrap_write_return_none_skips(datasette):
    """Test that returning None from wrap_write means no wrapping."""
    log = []

    class SkipPlugin:
        __name__ = "SkipPlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            log.append("hook-called")
            return None

    pm.register(SkipPlugin(), name="SkipPlugin")
    try:
        db = datasette.get_database("test")
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t5 (id integer primary key)"
            )
        )
        assert log == ["hook-called"]
    finally:
        pm.unregister(name="SkipPlugin")


@pytest.mark.asyncio
async def test_wrap_write_receives_request(datasette):
    """Test that the request parameter is passed through."""
    captured = {}

    class RequestPlugin:
        __name__ = "RequestPlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            captured["request"] = request
            captured["database"] = database

    pm.register(RequestPlugin(), name="RequestPlugin")
    try:
        db = datasette.get_database("test")
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t6 (id integer primary key)"
            ),
            request="fake-request",
        )
        assert captured["request"] == "fake-request"
        assert captured["database"] == "test"
    finally:
        pm.unregister(name="RequestPlugin")


@pytest.mark.asyncio
async def test_wrap_write_request_none_by_default(datasette):
    """Test that request is None when not provided."""
    captured = {}

    class RequestPlugin:
        __name__ = "RequestPlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            captured["request"] = request

    pm.register(RequestPlugin(), name="RequestPlugin2")
    try:
        db = datasette.get_database("test")
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t7 (id integer primary key)"
            )
        )
        assert captured["request"] is None
    finally:
        pm.unregister(name="RequestPlugin2")


@pytest.mark.asyncio
async def test_wrap_write_receives_transaction(datasette):
    """Test that the transaction parameter is passed through."""
    captured = {}

    class TransactionPlugin:
        __name__ = "TransactionPlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            captured["transaction"] = transaction

    pm.register(TransactionPlugin(), name="TransactionPlugin")
    try:
        db = datasette.get_database("test")
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t8 (id integer primary key)"
            ),
            transaction=False,
        )
        assert captured["transaction"] is False
    finally:
        pm.unregister(name="TransactionPlugin")


@pytest.mark.asyncio
async def test_wrap_write_via_execute_write(datasette):
    """Test that wrap_write fires for execute_write() too."""
    log = []

    class WrapWritePlugin:
        __name__ = "WrapWritePlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            def wrapper(conn):
                log.append("before")
                yield
                log.append("after")

            return wrapper

    pm.register(WrapWritePlugin(), name="WrapWritePluginEW")
    try:
        db = datasette.get_database("test")
        await db.execute_write(
            "create table if not exists t9 (id integer primary key)"
        )
        assert log == ["before", "after"]
    finally:
        pm.unregister(name="WrapWritePluginEW")


@pytest.mark.asyncio
async def test_wrap_write_via_api(tmp_path):
    """Test that wrap_write fires for API write operations."""
    log = []

    db_path = str(tmp_path / "test.db")
    ds = Datasette([db_path], pdb=False)
    ds.root_enabled = True

    class WrapWritePlugin:
        __name__ = "WrapWritePlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            if database != "test":
                return None

            def wrapper(conn):
                log.append("before")
                yield
                log.append("after")

            return wrapper

    pm.register(WrapWritePlugin(), name="WrapWritePluginAPI")
    try:
        db = ds.get_database("test")
        await db.execute_write(
            "create table if not exists api_test (id integer primary key, name text)"
        )
        log.clear()

        # Use the API to insert a row (as root to bypass permissions)
        token = "dstok_{}".format(
            ds.sign({"a": "root", "token": "dstok", "t": int(time.time())}, namespace="token")
        )
        response = await ds.client.post(
            "/test/api_test/-/insert",
            json={"row": {"name": "test"}, "return": True},
            headers={"Authorization": "Bearer {}".format(token), "Content-Type": "application/json"},
        )
        assert response.status_code == 201, response.json()
        assert log == ["before", "after"]
    finally:
        pm.unregister(name="WrapWritePluginAPI")


@pytest.mark.asyncio
async def test_wrap_write_change_group_pattern(datasette):
    """Test the motivating use case: activating a change group around a write."""
    db = datasette.get_database("test")

    # Set up tables
    await db.execute_write(
        "create table if not exists groups (id integer primary key, current integer)"
    )
    await db.execute_write(
        "create table if not exists data (id integer primary key, value text)"
    )

    # Insert a group row
    await db.execute_write("insert into groups (id, current) values (1, null)")

    class ChangeGroupPlugin:
        __name__ = "ChangeGroupPlugin"

        @staticmethod
        @hookimpl
        def wrap_write(datasette, database, request, transaction):
            if request and getattr(request, "group_id", None):
                group_id = request.group_id

                def wrapper(conn):
                    conn.execute(
                        "update groups set current = 1 where id = ?", [group_id]
                    )
                    yield
                    conn.execute("update groups set current = null where current = 1")

                return wrapper

    pm.register(ChangeGroupPlugin(), name="ChangeGroupPlugin")
    try:
        # Create a fake request with a group_id
        class FakeRequest:
            group_id = 1

        await db.execute_write_fn(
            lambda conn: conn.execute(
                "insert into data (value) values ('test')"
            ),
            request=FakeRequest(),
        )

        # After the write, current should be null again
        result = await db.execute("select current from groups where id = 1")
        assert result.rows[0][0] is None
    finally:
        pm.unregister(name="ChangeGroupPlugin")
