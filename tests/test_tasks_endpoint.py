"""
Tests for the /-/tasks introspection endpoint.

/-/tasks exposes datasette._background_tasks (see tests/test_background_tasks.py
for the supervisor machinery itself) the same way /-/threads exposes threading
internals: gated behind the permissions-debug permission, JSON-only.
"""

import asyncio
import contextlib

import pytest

from datasette.app import Datasette


@pytest.mark.asyncio
async def test_tasks_requires_permissions_debug():
    ds = Datasette(memory=True)
    ds.root_enabled = True

    denied = await ds.client.get("/-/tasks.json")
    assert denied.status_code == 403

    allowed = await ds.client.get("/-/tasks.json", actor={"id": "root"})
    assert allowed.status_code == 200
    data = allowed.json()
    assert data["ok"] is True
    assert "tasks" in data
    assert "launched" in data


@pytest.mark.asyncio
async def test_running_and_crashed_task_states():
    ds = Datasette(memory=True)
    ds.root_enabled = True

    async def long_running(datasette):
        await asyncio.Event().wait()

    async def crasher(datasette):
        raise RuntimeError("kaboom")

    long_handle = ds.add_background_task(long_running, name="long-runner")
    crash_handle = ds.add_background_task(crasher, name="crasher")

    await ds.start_background_tasks()

    # Let the crasher run to completion and its done-callback (which sets
    # handle.state = "crashed") actually fire before we read state back out.
    await asyncio.wait_for(
        asyncio.gather(crash_handle.task, return_exceptions=True), timeout=5
    )
    await asyncio.sleep(0)

    try:
        response = await ds.client.get("/-/tasks.json", actor={"id": "root"})
        assert response.status_code == 200
        data = response.json()
        assert data["launched"] is True

        by_name = {t["name"]: t for t in data["tasks"]}
        assert by_name["long-runner"]["state"] == "running"
        assert by_name["long-runner"]["exception"] is None
        assert by_name["long-runner"]["started_at"] is not None

        crashed = by_name["crasher"]
        assert crashed["state"] == "crashed"
        assert crashed["exception"] is not None
        assert isinstance(crashed["exception"], str)
        assert "kaboom" in crashed["exception"]
        assert "RuntimeError" in crashed["exception"]
    finally:
        long_handle.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await long_handle.task


@pytest.mark.asyncio
async def test_cold_instance_launched_false_and_task_registered():
    ds = Datasette(memory=True)
    ds.root_enabled = True

    async def task(datasette):
        pass

    ds.add_background_task(task, name="cold-task")

    # ds.client / httpx.ASGITransport routes through the full ASGI app,
    # including AsgiRunOnFirstRequest - the first-request fallback that
    # itself launches background tasks (_launch_background_tasks) so hosts
    # without lifespan support still get supervised tasks running. That
    # means a plain HTTP request here would launch "cold-task" before we
    # ever get a response, making the "never launched" state impossible to
    # observe over HTTP. _suppress_background_tasks is the same switch the
    # `datasette --get` CLI path sets to stop its one-shot request from
    # launching long-lived work (see _launch_background_tasks's docstring
    # in datasette/app.py) - setting it here keeps this one request from
    # arming the launch, so we can still exercise the real permission-gated
    # HTTP endpoint while asserting on a genuinely pre-launch snapshot.
    ds._suppress_background_tasks = True

    response = await ds.client.get("/-/tasks.json", actor={"id": "root"})
    assert response.status_code == 200
    data = response.json()
    assert data["launched"] is False
    assert len(data["tasks"]) == 1
    task_data = data["tasks"][0]
    assert task_data["name"] == "cold-task"
    assert task_data["state"] == "registered"
    assert task_data["started_at"] is None
    assert task_data["exception"] is None
