"""
Tests for wiring Datasette startup (setup_db table counts + invoke_startup)
into the ASGI lifespan protocol, per plans/first-request/02-lifespan-startup.

These exercise Datasette._startup_sequence() via three different callers:
- AsgiLifespan, by hand-driving lifespan.startup messages (no HTTP request)
- AsgiRunOnFirstRequest, the fallback for hosts that never send lifespan
  events (this is what DatasetteClient / plain httpx.ASGITransport uses)
- Both at once, to prove startup hooks run at most once
"""

import asyncio
import contextlib
import httpx
import pytest

from datasette import hookimpl
from datasette.app import Datasette
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
