"""
Tests for wiring Datasette startup (setup_db table counts + invoke_startup)
into the ASGI lifespan protocol.

These exercise Datasette._startup_sequence() via three different callers:
- AsgiLifespan, by hand-driving lifespan.startup messages (no HTTP request)
- AsgiRunOnFirstRequest, the fallback for hosts that never send lifespan
  events (this is what DatasetteClient / plain httpx.ASGITransport uses)
- Both at once, to prove startup hooks run at most once

Also covers the asgi_wrapper reorder:
plugin asgi_wrapper middleware runs INSIDE AsgiRunOnFirstRequest (below it)
but OUTSIDE AsgiLifespan (above it), so wrappers only ever see http/
websocket scopes after startup has completed, in both the lifespan and
fallback paths - while lifespan scopes still flow through wrappers
unchanged.
"""

import asyncio
import contextlib
import sqlite3

import httpx
import pytest

from datasette import hookimpl
from datasette.app import Datasette
from datasette.database import Database
from datasette.plugins import pm


async def _drive_lifespan_startup(app):
    """Send a single lifespan.startup message into app's ASGI lifespan loop
    and return the list of messages sent back - without ever sending
    lifespan.shutdown. Mirrors what a real server does: after startup
    completes it parks waiting for the next event. We cancel that wait
    once we've observed the startup response, rather than closing the
    Datasette instance down with a shutdown message.
    """
    messages_sent = []
    startup_responded = asyncio.Event()
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "lifespan.startup"}
        # No further messages: block until the task is cancelled below,
        # same as a real server parked waiting for lifespan.shutdown.
        await asyncio.Event().wait()

    async def send(message):
        messages_sent.append(message)
        startup_responded.set()

    task = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    try:
        await asyncio.wait_for(startup_responded.wait(), timeout=5)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return messages_sent


@pytest.mark.asyncio
async def test_lifespan_startup_runs_before_any_request():
    ds = Datasette(memory=True)
    assert ds._startup_invoked is False
    app = ds.app()

    messages = await _drive_lifespan_startup(app)

    assert {"type": "lifespan.startup.complete"} in messages
    assert ds._startup_invoked is True
    # Internal catalog tables should be populated too, entirely without an
    # HTTP request having been made.
    internal_db = ds.get_internal_database()
    databases = await internal_db.execute("select * from catalog_databases")
    assert len(databases.rows) >= 1


@pytest.mark.asyncio
async def test_lifespan_startup_failure_reports_lifespan_startup_failed():
    class RaisingStartupPlugin:
        __name__ = "RaisingStartupPlugin"

        @hookimpl
        def startup(self, datasette):
            async def inner():
                raise RuntimeError("boom from startup hook")

            return inner

    ds = Datasette(memory=True)
    pm.register(RaisingStartupPlugin(), name="raising_startup_plugin")
    try:
        app = ds.app()
        messages = await _drive_lifespan_startup(app)
    finally:
        pm.unregister(name="raising_startup_plugin")

    assert messages == [
        {"type": "lifespan.startup.failed", "message": "boom from startup hook"}
    ]
    # The exception happened before invoke_startup() got to the end of its
    # body, so startup is not considered to have completed.
    assert ds._startup_invoked is False


@pytest.mark.asyncio
async def test_startup_runs_exactly_once_across_lifespan_and_first_request():
    call_count = {"n": 0}

    class CountingStartupPlugin:
        __name__ = "CountingStartupPlugin"

        @hookimpl
        def startup(self, datasette):
            async def inner():
                call_count["n"] += 1

            return inner

    ds = Datasette(memory=True)
    pm.register(CountingStartupPlugin(), name="counting_startup_plugin")
    try:
        # Build the ASGI app once, the way a real deployment does - and
        # reuse the SAME app instance for both the lifespan drive and the
        # HTTP requests below, since a fresh ds.app() call would reset the
        # AsgiRunOnFirstRequest fallback's state.
        app = ds.app()

        messages = await _drive_lifespan_startup(app)
        assert {"type": "lifespan.startup.complete"} in messages
        assert call_count["n"] == 1

        # A first HTTP request (as if the host never sent lifespan events,
        # or lifespan already ran) should not run the hook again.
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            response1 = await client.get("/-/versions.json")
            assert response1.status_code == 200
            # ... nor should a second, repeat request.
            response2 = await client.get("/-/versions.json")
            assert response2.status_code == 200
    finally:
        pm.unregister(name="counting_startup_plugin")

    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_no_lifespan_first_request_still_triggers_startup():
    # Pin today's behavior: a client that never drives ASGI lifespan events
    # at all (like httpx.ASGITransport, which DatasetteClient uses) still
    # gets startup armed by the AsgiRunOnFirstRequest fallback.
    ds = Datasette(memory=True)
    assert ds._startup_invoked is False
    app = ds.app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost"
    ) as client:
        response = await client.get("/-/versions.json")
        assert response.status_code == 200

    assert ds._startup_invoked is True
    internal_db = ds.get_internal_database()
    databases = await internal_db.execute("select * from catalog_databases")
    assert len(databases.rows) >= 1


@pytest.mark.asyncio
async def test_datasette_client_first_request_triggers_startup():
    # Same as above, but through the real DatasetteClient (ds.client) that
    # plugins and tests actually use, to confirm nothing regressed there.
    ds = Datasette(memory=True)
    assert ds._startup_invoked is False
    response = await ds.client.get("/-/versions.json")
    assert response.status_code == 200
    assert ds._startup_invoked is True


@pytest.mark.asyncio
async def test_concurrent_first_requests_all_wait_for_slow_startup():
    call_count = {"n": 0}

    class SlowStartupPlugin:
        __name__ = "SlowStartupPlugin"

        @hookimpl
        def startup(self, datasette):
            async def inner():
                call_count["n"] += 1
                await asyncio.sleep(0.2)

            return inner

    ds = Datasette(memory=True)
    pm.register(SlowStartupPlugin(), name="slow_startup_plugin")
    try:
        app = ds.app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            responses = await asyncio.gather(
                *[client.get("/-/versions.json") for _ in range(10)]
            )
    finally:
        pm.unregister(name="slow_startup_plugin")

    # Every one of the 10 simultaneous first requests must have blocked
    # until startup actually finished, not raced ahead of it.
    assert all(response.status_code == 200 for response in responses)
    assert call_count["n"] == 1
    assert ds._startup_invoked is True


@pytest.mark.asyncio
async def test_setup_db_still_runs_when_invoke_startup_ran_first(tmp_path, monkeypatch):
    # Regression test: `datasette serve` (cli.py _serve_async) calls
    # ds.invoke_startup() directly, before uvicorn ever sends a
    # lifespan.startup event that drives _startup_sequence(). If
    # _startup_sequence()'s fast path only checked `_startup_invoked`, it
    # would see startup already done and skip the immutable-database
    # table-count precompute (setup_db) entirely - a silent regression
    # versus main, where AsgiRunOnFirstRequest ran setup_db unconditionally
    # on request #1.
    db_path = tmp_path / "immutable.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("create table t (id integer primary key)")
    conn.commit()
    conn.close()

    ds = Datasette([], immutables=[str(db_path)])

    call_count = {"n": 0}
    original_table_counts = Database.table_counts

    async def counting_table_counts(self, *args, **kwargs):
        call_count["n"] += 1
        return await original_table_counts(self, *args, **kwargs)

    monkeypatch.setattr(Database, "table_counts", counting_table_counts)

    # Simulate the CLI path: invoke_startup() runs directly and completes
    # BEFORE _startup_sequence() ever gets a chance to run setup_db.
    await ds.invoke_startup()
    assert ds._startup_invoked is True
    assert call_count["n"] == 0

    # The lifespan/first-request path (or the CLI itself, per the fix)
    # calling the shared entry point afterwards must still precompute
    # table counts for immutable databases.
    await ds._startup_sequence()
    assert call_count["n"] == 1
    assert ds._setup_db_done is True

    # Idempotency: a second call must not recompute.
    await ds._startup_sequence()
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_asgi_wrapper_runs_after_startup_fallback_path():
    # Ticket 05: plugin asgi_wrapper middleware must never see an http scope
    # before startup has completed - even on the fallback path (no ASGI
    # lifespan events at all), which is what plain httpx.ASGITransport /
    # bare app() embedding exercises. Before the app() reorder in this
    # ticket, the wrapper loop ran OUTSIDE (above) AsgiRunOnFirstRequest, so
    # this assertion could see _startup_invoked is False on request #1 -
    # this test fails on the pre-reorder app().
    class AssertStartupPlugin:
        __name__ = "AssertStartupPlugin"

        @hookimpl
        def asgi_wrapper(self, datasette):
            def wrap(app):
                async def check_startup(scope, receive, send):
                    if scope["type"] == "http":
                        assert datasette._startup_invoked is True, (
                            "asgi_wrapper saw an http scope before startup "
                            "completed"
                        )
                    await app(scope, receive, send)

                return check_startup

            return wrap

    ds = Datasette(memory=True)
    pm.register(AssertStartupPlugin(), name="assert_startup_plugin")
    try:
        assert ds._startup_invoked is False
        app = ds.app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            response = await client.get("/-/versions.json")
            assert response.status_code == 200
    finally:
        pm.unregister(name="assert_startup_plugin")

    assert ds._startup_invoked is True


@pytest.mark.asyncio
async def test_short_circuit_wrapper_no_longer_defers_startup():
    # Ticket 05: a wrapper that short-circuits (returns a response without
    # ever calling the inner app - the shape of a 401/403/CORS-preflight
    # responder) used to mean startup never ran for that request, because
    # the wrapper sat OUTSIDE AsgiRunOnFirstRequest. Now that
    # AsgiRunOnFirstRequest is outermost, it arms startup before the
    # wrapper (or anything else) ever sees the scope.
    class ShortCircuitPlugin:
        __name__ = "ShortCircuitPlugin"

        @hookimpl
        def asgi_wrapper(self, datasette):
            def wrap(app):
                async def forbidden(scope, receive, send):
                    if scope["type"] != "http":
                        await app(scope, receive, send)
                        return
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 403,
                            "headers": [[b"content-type", b"text/plain"]],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"Forbidden",
                        }
                    )
                    # Deliberately never call app(...): this is the
                    # short-circuiting shape (auth-passwords, auth-tailscale,
                    # datasette-cors preflight).

                return forbidden

            return wrap

    ds = Datasette(memory=True)
    pm.register(ShortCircuitPlugin(), name="short_circuit_plugin")
    try:
        assert ds._startup_invoked is False
        app = ds.app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            response = await client.get("/-/versions.json")
            assert response.status_code == 403
    finally:
        pm.unregister(name="short_circuit_plugin")

    assert ds._startup_invoked is True


@pytest.mark.asyncio
async def test_asgi_wrapper_still_sees_lifespan_scopes():
    # Ticket 05: the reorder only moves AsgiRunOnFirstRequest outside the
    # wrapper loop - AsgiLifespan stays inside it, so a wrapper that
    # inspects/wraps lifespan scopes still sees identical message flow.
    # Pin that lifespan scopes keep reaching wrappers alongside http scopes.
    seen_types = []

    class RecordingPlugin:
        __name__ = "RecordingPlugin"

        @hookimpl
        def asgi_wrapper(self, datasette):
            def wrap(app):
                async def record(scope, receive, send):
                    seen_types.append(scope["type"])
                    await app(scope, receive, send)

                return record

            return wrap

    ds = Datasette(memory=True)
    pm.register(RecordingPlugin(), name="recording_plugin")
    try:
        app = ds.app()
        messages = await _drive_lifespan_startup(app)
        assert {"type": "lifespan.startup.complete"} in messages

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            response = await client.get("/-/versions.json")
            assert response.status_code == 200
    finally:
        pm.unregister(name="recording_plugin")

    assert "lifespan" in seen_types
    assert "http" in seen_types
