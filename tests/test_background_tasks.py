"""
Tests for datasette.add_background_task() / start_background_tasks() and the
BackgroundTask / BackgroundTaskSupervisor machinery in
datasette/background_tasks.py.
"""

import asyncio
import contextlib
import logging

import httpx
import pytest

from datasette import hookimpl
from datasette.app import Datasette
from datasette.plugins import pm


async def _drive_lifespan_startup(app):
    """Send a single lifespan.startup message into app's ASGI lifespan loop
    and return the list of messages sent back, without ever sending
    lifespan.shutdown. Copied from tests/test_lifespan.py's helper of the
    same name - mirrors what a real server does: after startup completes
    it parks waiting for the next event, and we cancel that wait once
    we've observed the startup response.
    """
    messages_sent = []
    startup_responded = asyncio.Event()
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "lifespan.startup"}
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
async def test_tasks_registered_in_startup_hook_run_after_lifespan_startup():
    # Two tasks registered by one plugin's startup hook - order preserved,
    # both running after lifespan startup completes, and no HTTP request
    # of any kind is issued anywhere in this test.
    events = []

    async def task_one(datasette):
        events.append("task_one")
        await asyncio.Event().wait()

    async def task_two(datasette):
        events.append("task_two")
        await asyncio.Event().wait()

    class TwoTaskPlugin:
        __name__ = "TwoTaskPlugin"

        @hookimpl
        def startup(self, datasette):
            async def inner():
                datasette.add_background_task(task_one, name="task-one")
                datasette.add_background_task(task_two, name="task-two")

            return inner

    ds = Datasette(memory=True)
    pm.register(TwoTaskPlugin(), name="two_task_plugin")
    try:
        app = ds.app()
        messages = await _drive_lifespan_startup(app)
        assert {"type": "lifespan.startup.complete"} in messages

        handles = ds._background_tasks.tasks()
        assert [h.name for h in handles] == ["task-one", "task-two"]

        # Let both tasks run their first line of code.
        await asyncio.sleep(0)
        assert handles[0].state == "running"
        assert handles[1].state == "running"
        assert events == ["task_one", "task_two"]
    finally:
        pm.unregister(name="two_task_plugin")
        await ds._background_tasks.cancel_all(grace=1.0)


@pytest.mark.asyncio
async def test_launch_waits_for_every_startup_hook_before_running_any_task():
    # PluginA registers a task from its startup hook; PluginB does the
    # same from ITS startup hook, which runs after PluginA's (forced with
    # tryfirst=True on A). Even though A's registration happens first,
    # A's task body must not actually execute until every startup hook -
    # including B's - has finished, since launch only happens after
    # invoke_startup() completes. This is the ordering guarantee that
    # dissolves datasette-cron's tryfirst=True launch hack.
    hook_call_order = []
    seen_names_when_a_ran = {}

    async def task_a(datasette):
        seen_names_when_a_ran["names"] = [
            h.name for h in datasette._background_tasks.tasks()
        ]

    async def task_b(datasette):
        pass

    class PluginA:
        __name__ = "PluginA"

        @hookimpl(tryfirst=True)
        def startup(self, datasette):
            async def inner():
                hook_call_order.append("A")
                datasette.add_background_task(task_a, name="task-a")

            return inner

    class PluginB:
        __name__ = "PluginB"

        @hookimpl
        def startup(self, datasette):
            async def inner():
                hook_call_order.append("B")
                datasette.add_background_task(task_b, name="task-b")

            return inner

    ds = Datasette(memory=True)
    pm.register(PluginA(), name="plugin_a")
    pm.register(PluginB(), name="plugin_b")
    try:
        await ds.start_background_tasks()
        # Confirm A's startup hook really did run (and register task-a)
        # strictly before B's startup hook ran.
        assert hook_call_order == ["A", "B"]

        handles = ds._background_tasks.tasks()
        await asyncio.wait_for(asyncio.gather(*[h.task for h in handles]), timeout=5)
        # Yet by the time task-a's own body executed (after launch, which
        # only happens once every startup hook - including B's - has
        # finished), task-b was already registered.
        assert "task-b" in seen_names_when_a_ran["names"]
    finally:
        pm.unregister(name="plugin_a")
        pm.unregister(name="plugin_b")


@pytest.mark.asyncio
async def test_concurrent_first_requests_launch_background_tasks_exactly_once():
    launch_count = {"n": 0}

    async def counting_task(datasette):
        launch_count["n"] += 1

    class CountingTaskPlugin:
        __name__ = "CountingTaskPlugin"

        @hookimpl
        def startup(self, datasette):
            async def inner():
                datasette.add_background_task(counting_task, name="counting-task")

            return inner

    ds = Datasette(memory=True)
    pm.register(CountingTaskPlugin(), name="counting_task_plugin")
    try:
        app = ds.app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            responses = await asyncio.gather(
                *[client.get("/-/versions.json") for _ in range(10)]
            )
        assert all(response.status_code == 200 for response in responses)

        handles = ds._background_tasks.tasks()
        assert len(handles) == 1
        await asyncio.wait_for(handles[0].task, timeout=5)
        assert launch_count["n"] == 1
    finally:
        pm.unregister(name="counting_task_plugin")


@pytest.mark.asyncio
async def test_post_launch_registration_starts_immediately_and_cancel_works():
    ds = Datasette(memory=True)
    await ds.start_background_tasks()  # nothing registered yet, but launched

    started = asyncio.Event()

    async def long_running(datasette):
        started.set()
        await asyncio.Event().wait()

    handle = ds.add_background_task(long_running, name="dynamic-task")
    # Registered after launch: starts immediately rather than sitting in
    # "registered" limbo.
    assert handle.state == "running"
    assert handle.task is not None

    await asyncio.wait_for(started.wait(), timeout=5)
    assert handle.state == "running"

    handle.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handle.task
    await asyncio.sleep(0)
    assert handle.state == "cancelled"


@pytest.mark.asyncio
async def test_pre_launch_registration_starts_as_registered():
    ds = Datasette(memory=True)

    async def task(datasette):
        pass

    handle = ds.add_background_task(task, name="buffered-task")
    assert handle.state == "registered"
    assert handle.task is None

    handle.cancel()  # not yet launched: deregisters instead of cancelling
    assert handle not in ds._background_tasks.tasks()


@pytest.mark.asyncio
async def test_crashing_task_logs_traceback_and_state_is_crashed(caplog):
    ds = Datasette(memory=True)
    await ds.start_background_tasks()

    survivor_ran = asyncio.Event()

    async def crasher(datasette):
        raise RuntimeError("kaboom")

    async def survivor(datasette):
        survivor_ran.set()

    with caplog.at_level(logging.ERROR, logger="datasette.background_tasks"):
        crash_handle = ds.add_background_task(crasher, name="crasher")
        survivor_handle = ds.add_background_task(survivor, name="survivor")
        await asyncio.wait_for(
            asyncio.gather(
                crash_handle.task, survivor_handle.task, return_exceptions=True
            ),
            timeout=5,
        )

    assert crash_handle.state == "crashed"
    assert isinstance(crash_handle.exception, RuntimeError)
    assert str(crash_handle.exception) == "kaboom"

    # The crash must not affect any other task.
    assert survivor_ran.is_set()
    assert survivor_handle.state == "completed"

    assert "crasher" in caplog.text
    assert "kaboom" in caplog.text
    assert "Traceback" in caplog.text
    assert "RuntimeError" in caplog.text


def test_name_collisions_get_suffixed_and_explicit_names_are_respected():
    ds = Datasette(memory=True)

    async def noop(datasette):
        pass

    async def another_noop(datasette):
        pass

    h1 = ds.add_background_task(noop, name="dup")
    h2 = ds.add_background_task(another_noop, name="dup")
    h3 = ds.add_background_task(noop, name="dup")
    assert [h1.name, h2.name, h3.name] == ["dup", "dup-2", "dup-3"]

    h_explicit = ds.add_background_task(noop, name="explicit-name")
    assert h_explicit.name == "explicit-name"

    h_default = ds.add_background_task(noop)
    assert h_default.name == noop.__qualname__


@pytest.mark.asyncio
async def test_start_background_tasks_on_bare_datasette():
    # The headless-CLI path (datasette-rss's `fetch --due` shape): no
    # server, no lifespan, no first HTTP request - just an explicit call.
    ran = asyncio.Event()

    async def task(datasette):
        ran.set()

    ds = Datasette([])
    assert ds._startup_invoked is False

    handle = ds.add_background_task(task, name="headless-task")
    assert handle.state == "registered"

    await ds.start_background_tasks()

    assert ds._startup_invoked is True
    await asyncio.wait_for(ran.wait(), timeout=5)
    await asyncio.wait_for(handle.task, timeout=5)
    # handle.task being done only guarantees the coroutine has returned,
    # not that our done-callback (which updates handle.state) has run yet -
    # asyncio schedules done-callbacks via call_soon, and awaiting an
    # already-done future/task returns immediately without giving the loop
    # a chance to drain its ready queue. Yield once to let it run.
    await asyncio.sleep(0)
    assert handle.state == "completed"


@pytest.mark.asyncio
async def test_cancel_all_cancels_running_tasks_and_leaves_completed_alone():
    ds = Datasette(memory=True)
    await ds.start_background_tasks()

    async def forever(datasette):
        await asyncio.Event().wait()

    async def quick(datasette):
        return "done"

    forever_handle = ds.add_background_task(forever, name="forever")
    quick_handle = ds.add_background_task(quick, name="quick")
    await asyncio.wait_for(quick_handle.task, timeout=5)
    assert quick_handle.state == "completed"

    await ds._background_tasks.cancel_all(grace=1.0)

    assert forever_handle.state == "cancelled"
    assert quick_handle.state == "completed"


@pytest.mark.asyncio
async def test_cancel_all_logs_stragglers_that_outlive_the_grace_period(caplog):
    ds = Datasette(memory=True)
    await ds.start_background_tasks()

    async def stubborn(datasette):
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(10)
        # Swallowing CancelledError above and returning normally simulates
        # a task that ignores cancellation for longer than the grace period.
        await asyncio.sleep(10)

    handle = ds.add_background_task(stubborn, name="stubborn-task")
    # Let the task actually start running and reach its first sleep (inside
    # the CancelledError-suppressing block) before cancelling it - a task
    # cancelled before it has ever run its first step never enters that
    # block at all (the throw happens before the coroutine body starts),
    # so it would finish cancelling immediately instead of behaving like a
    # straggler.
    await asyncio.sleep(0)

    with caplog.at_level(logging.WARNING, logger="datasette.background_tasks"):
        await ds._background_tasks.cancel_all(grace=0.1)

    assert "stubborn-task" in caplog.text

    # Clean up: actually cancel it now that the test has made its
    # assertion, so it doesn't leak past the end of the test.
    handle.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await handle.task
