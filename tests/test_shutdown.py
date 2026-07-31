"""
Tests for the shutdown(datasette) plugin hook and Datasette.invoke_shutdown(),
per plans/first-request/04-core-plan.md (decision #6) and
todos/first-request/04-shutdown-hook.md.

Order under test: plugin `shutdown` hooks run first (while background tasks
are still alive) -> supervised background tasks are cancelled and drained
(fixed 5s grace) -> databases are closed. Hook exceptions are logged, never
propagated, and never skip a later step. The whole sequence is idempotent,
so a second lifespan.shutdown message (or any other second caller) is a
no-op.
"""

import asyncio
import contextlib
import logging

import pytest

from datasette import hookimpl
from datasette.app import Datasette
from datasette.plugins import pm


async def _drive_lifespan(app, messages):
    """Drive a single ASGI lifespan connection against `app`, feeding
    `messages` to it in order via receive(). Returns once `app` itself
    returns, which - per AsgiLifespan - happens after it has processed a
    lifespan.shutdown message and sent back .complete or .failed. Returns
    the list of messages `app` sent via send().

    `messages` must end with a lifespan.shutdown (or a startup that fails)
    or this will hang forever waiting for a message that never comes,
    since AsgiLifespan only returns after handling shutdown.
    """
    sent = []
    idx = 0

    async def receive():
        nonlocal idx
        if idx < len(messages):
            message = messages[idx]
            idx += 1
            return message
        # Real servers park here waiting for lifespan.shutdown; nothing
        # left to deliver in this test, so just block - the caller is
        # expected to have already gotten what it needs via a preceding
        # lifespan.shutdown in `messages`.
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    await app({"type": "lifespan"}, receive, send)
    return sent


@pytest.mark.asyncio
async def test_shutdown_hook_runs_before_task_cancellation_then_closes():
    # Ordering proof: the shutdown hook observes the background task still
    # "running" (hooks run BEFORE cancellation); after the whole lifespan
    # drive completes, the task is "cancelled" and the Datasette instance
    # is closed.
    events = []
    task_state_seen_in_hook = {}

    async def bg_task(datasette):
        await asyncio.Event().wait()

    class LifecyclePlugin:
        __name__ = "LifecyclePlugin"

        @hookimpl
        def startup(self, datasette):
            async def inner():
                datasette.add_background_task(bg_task, name="bg-task")

            return inner

        @hookimpl
        def shutdown(self, datasette):
            async def inner():
                events.append("shutdown-hook-ran")
                handle = datasette._background_tasks.tasks()[0]
                task_state_seen_in_hook["state"] = handle.state

            return inner

    ds = Datasette(memory=True)
    pm.register(LifecyclePlugin(), name="lifecycle_plugin")
    try:
        app = ds.app()
        messages = await _drive_lifespan(
            app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        )
    finally:
        pm.unregister(name="lifecycle_plugin")

    assert {"type": "lifespan.startup.complete"} in messages
    assert {"type": "lifespan.shutdown.complete"} in messages
    assert events == ["shutdown-hook-ran"]
    assert task_state_seen_in_hook["state"] == "running"

    handle = ds._background_tasks.tasks()[0]
    assert handle.state == "cancelled"
    assert ds._closed is True
    assert ds._shutdown_invoked is True


@pytest.mark.asyncio
async def test_second_lifespan_shutdown_does_not_double_invoke():
    call_count = {"n": 0}

    class CountingShutdownPlugin:
        __name__ = "CountingShutdownPlugin"

        @hookimpl
        def shutdown(self, datasette):
            async def inner():
                call_count["n"] += 1

            return inner

    ds = Datasette(memory=True)
    pm.register(CountingShutdownPlugin(), name="counting_shutdown_plugin")
    try:
        app = ds.app()
        messages = await _drive_lifespan(
            app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        )
        assert {"type": "lifespan.shutdown.complete"} in messages
        assert call_count["n"] == 1

        # A second, separate lifespan connection (a misbehaving host, or a
        # second embedder driving the same Datasette instance) sends
        # lifespan.shutdown again. AsgiLifespan itself has no memory of
        # the earlier connection, so this exercises invoke_shutdown()'s
        # own idempotency guard, not anything ASGI-layer.
        messages2 = await _drive_lifespan(app, [{"type": "lifespan.shutdown"}])
        assert {"type": "lifespan.shutdown.complete"} in messages2
        assert call_count["n"] == 1
    finally:
        pm.unregister(name="counting_shutdown_plugin")

    assert ds._closed is True


@pytest.mark.asyncio
async def test_raising_shutdown_hook_is_logged_and_does_not_block_the_rest(caplog):
    events = []

    async def bg_task(datasette):
        await asyncio.Event().wait()

    class RaisingShutdownPlugin:
        __name__ = "RaisingShutdownPlugin"

        @hookimpl
        def shutdown(self, datasette):
            async def inner():
                raise RuntimeError("boom from shutdown hook")

            return inner

    class WellBehavedPlugin:
        __name__ = "WellBehavedPlugin"

        @hookimpl
        def startup(self, datasette):
            async def inner():
                datasette.add_background_task(bg_task, name="bg-task")

            return inner

        @hookimpl
        def shutdown(self, datasette):
            async def inner():
                events.append("well-behaved-ran")

            return inner

    ds = Datasette(memory=True)
    pm.register(RaisingShutdownPlugin(), name="raising_shutdown_plugin")
    pm.register(WellBehavedPlugin(), name="well_behaved_plugin")
    try:
        app = ds.app()
        with caplog.at_level(logging.ERROR, logger="datasette"):
            messages = await _drive_lifespan(
                app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
            )
    finally:
        pm.unregister(name="raising_shutdown_plugin")
        pm.unregister(name="well_behaved_plugin")

    # The exception must never turn into lifespan.shutdown.failed - it's
    # swallowed inside invoke_shutdown(), logged, and teardown continues.
    assert {"type": "lifespan.shutdown.complete"} in messages
    assert events == ["well-behaved-ran"]
    assert "shutdown hook failed" in caplog.text
    assert "boom from shutdown hook" in caplog.text

    handle = ds._background_tasks.tasks()[0]
    assert handle.state == "cancelled"
    assert ds._closed is True


@pytest.mark.asyncio
async def test_sync_shutdown_hook_variant_works():
    events = []

    class SyncShutdownPlugin:
        __name__ = "SyncShutdownPlugin"

        @hookimpl
        def shutdown(self, datasette):
            # Deliberately not returning a coroutine/callable - a plain
            # sync hookimpl, same as `def startup(datasette): ...` is
            # supported via await_me_maybe.
            events.append("sync-shutdown-ran")

    ds = Datasette(memory=True)
    pm.register(SyncShutdownPlugin(), name="sync_shutdown_plugin")
    try:
        app = ds.app()
        messages = await _drive_lifespan(
            app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        )
    finally:
        pm.unregister(name="sync_shutdown_plugin")

    assert {"type": "lifespan.shutdown.complete"} in messages
    assert events == ["sync-shutdown-ran"]
    assert ds._closed is True


@pytest.mark.asyncio
async def test_shutdown_logs_stragglers_that_outlive_the_grace_period(
    caplog, monkeypatch
):
    async def stubborn(datasette):
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(10)
        # Swallowing CancelledError above and returning normally simulates
        # a task that ignores cancellation for longer than the grace
        # period - same shape as test_background_tasks.py's equivalent
        # test.
        await asyncio.sleep(10)

    class StubbornTaskPlugin:
        __name__ = "StubbornTaskPlugin"

        @hookimpl
        def startup(self, datasette):
            async def inner():
                datasette.add_background_task(stubborn, name="stubborn-task")

            return inner

    ds = Datasette(memory=True)
    pm.register(StubbornTaskPlugin(), name="stubborn_task_plugin")

    # invoke_shutdown() calls self._background_tasks.cancel_all(grace=5.0)
    # with a grace hardcoded in app.py, per the ticket. Rather than
    # sleeping for a real 5s in this test, monkeypatch the
    # BackgroundTaskSupervisor *instance's* cancel_all to a wrapper that
    # ignores the caller-supplied grace and substitutes a small one - this
    # is the cleanest seam because it requires no production-code changes
    # (no grace= setting/attribute to add) and leaves invoke_shutdown's
    # own code under test untouched.
    real_cancel_all = ds._background_tasks.cancel_all

    async def fast_cancel_all(grace=5.0):
        return await real_cancel_all(grace=0.1)

    monkeypatch.setattr(ds._background_tasks, "cancel_all", fast_cancel_all)

    try:
        app = ds.app()

        sent = []
        queue = asyncio.Queue()
        startup_complete = asyncio.Event()

        async def receive():
            return await queue.get()

        async def send(message):
            sent.append(message)
            if message.get("type") == "lifespan.startup.complete":
                startup_complete.set()

        task = asyncio.create_task(app({"type": "lifespan"}, receive, send))
        await queue.put({"type": "lifespan.startup"})
        await asyncio.wait_for(startup_complete.wait(), timeout=5)

        # Let the stubborn background task actually start running and
        # reach its CancelledError-suppressing sleep before shutdown
        # cancels it - a task cancelled before it has ever run its first
        # step never enters that block at all, so it would finish
        # cancelling immediately instead of behaving like a straggler.
        await asyncio.sleep(0.05)

        with caplog.at_level(logging.WARNING, logger="datasette.background_tasks"):
            await queue.put({"type": "lifespan.shutdown"})
            await asyncio.wait_for(task, timeout=5)

        assert {"type": "lifespan.shutdown.complete"} in sent
        assert "stubborn-task" in caplog.text
        assert ds._closed is True
    finally:
        pm.unregister(name="stubborn_task_plugin")
        # Clean up: the stubborn task ignored cancellation and is still
        # sleeping past the shrunk grace period; actually cancel and await
        # it now that the test has made its assertions, so it doesn't leak
        # past the end of the test.
        handles = ds._background_tasks.tasks()
        if handles and handles[0].task and not handles[0].task.done():
            handles[0].task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await handles[0].task
