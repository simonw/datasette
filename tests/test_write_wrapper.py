"""
Tests for the write_wrapper plugin hook.
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
@pytest.mark.parametrize(
    "use_execute_write",
    (False, True),
    ids=["execute_write_fn", "execute_write"],
)
async def test_write_wrapper_before_and_after(datasette, use_execute_write):
    """Test that code before and after yield both execute."""
    log = []

    class Plugin:
        __name__ = "Plugin"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            def wrapper(conn):
                log.append("before")
                yield
                log.append("after")

            return wrapper

    pm.register(Plugin(), name="test_before_after")
    try:
        db = datasette.get_database("test")
        if use_execute_write:
            await db.execute_write(
                "create table if not exists t (id integer primary key)"
            )
        else:
            await db.execute_write_fn(
                lambda conn: conn.execute(
                    "create table if not exists t (id integer primary key)"
                )
            )
        assert log == ["before", "after"]
    finally:
        pm.unregister(name="test_before_after")


@pytest.mark.asyncio
async def test_write_wrapper_receives_result_via_yield(datasette):
    """Test that the result of fn(conn) is sent back through yield."""
    captured = {}

    class Plugin:
        __name__ = "Plugin"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            def wrapper(conn):
                result = yield
                captured["result"] = result

            return wrapper

    pm.register(Plugin(), name="test_result")
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
        pm.unregister(name="test_result")


@pytest.mark.asyncio
async def test_write_wrapper_exception_thrown_into_generator(datasette):
    """Test that exceptions from fn(conn) are thrown into the generator."""
    caught = {}

    class Plugin:
        __name__ = "Plugin"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            def wrapper(conn):
                try:
                    yield
                except Exception as e:
                    caught["error"] = e

            return wrapper

    pm.register(Plugin(), name="test_exception")
    try:
        db = datasette.get_database("test")
        with pytest.raises(Exception, match="deliberate"):
            await db.execute_write_fn(
                lambda conn: (_ for _ in ()).throw(Exception("deliberate"))
            )
        assert "error" in caught
        assert str(caught["error"]) == "deliberate"
    finally:
        pm.unregister(name="test_exception")


@pytest.mark.asyncio
async def test_write_wrapper_conn_is_usable(datasette):
    """Test that the conn passed to the wrapper can execute SQL."""

    class Plugin:
        __name__ = "Plugin"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            def wrapper(conn):
                conn.execute("create table if not exists hook_log (msg text)")
                conn.execute("insert into hook_log values ('before')")
                yield
                conn.execute("insert into hook_log values ('after')")

            return wrapper

    pm.register(Plugin(), name="test_conn")
    try:
        db = datasette.get_database("test")
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t3 (id integer primary key)"
            )
        )
        result = await db.execute("select msg from hook_log order by rowid")
        messages = [row[0] for row in result.rows]
        assert messages == ["before", "after"]
    finally:
        pm.unregister(name="test_conn")


@pytest.mark.asyncio
async def test_write_wrapper_multiple_plugins_nest(datasette):
    """Test that multiple write_wrapper plugins nest correctly."""
    log = []

    class PluginA:
        __name__ = "PluginA"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            def wrapper(conn):
                log.append("A-before")
                yield
                log.append("A-after")

            return wrapper

    class PluginB:
        __name__ = "PluginB"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
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
        assert set(log) == {"A-before", "A-after", "B-before", "B-after"}
        # Verify proper nesting: each plugin's before/after should be
        # symmetric around the write
        a_before = log.index("A-before")
        a_after = log.index("A-after")
        b_before = log.index("B-before")
        b_after = log.index("B-after")
        if a_before < b_before:
            assert a_after > b_after, "A is outer so A-after should come after B-after"
        else:
            assert b_after > a_after, "B is outer so B-after should come after A-after"
    finally:
        pm.unregister(name="PluginA")
        pm.unregister(name="PluginB")


@pytest.mark.asyncio
async def test_write_wrapper_return_none_skips(datasette):
    """Test that returning None from write_wrapper means no wrapping."""
    log = []

    class Plugin:
        __name__ = "Plugin"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            log.append("hook-called")
            return None

    pm.register(Plugin(), name="test_skip")
    try:
        db = datasette.get_database("test")
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t5 (id integer primary key)"
            )
        )
        assert log == ["hook-called"]
    finally:
        pm.unregister(name="test_skip")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_value,transaction_value,expected_request,expected_transaction",
    (
        ("fake-request", True, "fake-request", True),
        (None, True, None, True),
        (None, False, None, False),
    ),
    ids=["with-request", "request-none-by-default", "transaction-false"],
)
async def test_write_wrapper_hook_parameters(
    datasette,
    request_value,
    transaction_value,
    expected_request,
    expected_transaction,
):
    """Test that request and transaction parameters are passed through."""
    captured = {}

    class Plugin:
        __name__ = "Plugin"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            captured["request"] = request
            captured["database"] = database
            captured["transaction"] = transaction

    pm.register(Plugin(), name="test_params")
    try:
        db = datasette.get_database("test")
        kwargs = {"transaction": transaction_value}
        if request_value is not None:
            kwargs["request"] = request_value
        await db.execute_write_fn(
            lambda conn: conn.execute(
                "create table if not exists t6 (id integer primary key)"
            ),
            **kwargs,
        )
        assert captured["request"] == expected_request
        assert captured["database"] == "test"
        assert captured["transaction"] == expected_transaction
    finally:
        pm.unregister(name="test_params")


@pytest.mark.asyncio
async def test_write_wrapper_via_api(tmp_path):
    """Test that write_wrapper fires for API write operations."""
    log = []

    db_path = str(tmp_path / "test.db")
    ds = Datasette([db_path], pdb=False)
    ds.root_enabled = True

    class Plugin:
        __name__ = "Plugin"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            if database != "test":
                return None

            def wrapper(conn):
                log.append("before")
                yield
                log.append("after")

            return wrapper

    pm.register(Plugin(), name="test_api")
    try:
        db = ds.get_database("test")
        await db.execute_write(
            "create table if not exists api_test (id integer primary key, name text)"
        )
        log.clear()

        token = "dstok_{}".format(
            ds.sign(
                {"a": "root", "token": "dstok", "t": int(time.time())},
                namespace="token",
            )
        )
        response = await ds.client.post(
            "/test/api_test/-/insert",
            json={"row": {"name": "test"}, "return": True},
            headers={
                "Authorization": "Bearer {}".format(token),
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 201, response.json()
        assert log == ["before", "after"]
    finally:
        pm.unregister(name="test_api")


@pytest.mark.asyncio
async def test_write_wrapper_change_group_pattern(datasette):
    """Test the motivating use case: activating a change group around a write."""
    db = datasette.get_database("test")

    await db.execute_write(
        "create table if not exists groups (id integer primary key, current integer)"
    )
    await db.execute_write(
        "create table if not exists data (id integer primary key, value text)"
    )
    await db.execute_write("insert into groups (id, current) values (1, null)")

    class Plugin:
        __name__ = "Plugin"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            if request and getattr(request, "group_id", None):
                group_id = request.group_id

                def wrapper(conn):
                    conn.execute(
                        "update groups set current = 1 where id = ?", [group_id]
                    )
                    yield
                    conn.execute("update groups set current = null where current = 1")

                return wrapper

    pm.register(Plugin(), name="test_change_group")
    try:

        class FakeRequest:
            group_id = 1

        await db.execute_write_fn(
            lambda conn: conn.execute("insert into data (value) values ('test')"),
            request=FakeRequest(),
        )

        result = await db.execute("select current from groups where id = 1")
        assert result.rows[0][0] is None
    finally:
        pm.unregister(name="test_change_group")
