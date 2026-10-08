"""
Tests for the /-/tasks introspection endpoint.

/-/tasks exposes datasette._background_tasks (see tests/test_background_tasks.py
for the supervisor machinery itself) the same way /-/threads exposes threading
internals: gated behind the permissions-debug permission, JSON-only.
"""

import asyncio
import contextlib
import functools

import pytest

from datasette.app import Datasette


async def example_task(datasette):
    pass


class ExampleWorker:
    async def run(self, datasette):
        pass

    async def __call__(self, datasette):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "func, qualified_name",
    [
        (example_task, "example_task"),
        (functools.partial(example_task), "example_task"),
        (ExampleWorker().run, "ExampleWorker.run"),
        (ExampleWorker(), "ExampleWorker.__call__"),
    ],
)
async def test_task_function_path(func, qualified_name):
    ds = Datasette(memory=True)
    ds.root_enabled = True
    handle = ds.add_background_task(func, name="custom-name")
    try:
        response = await ds.client.get("/-/tasks.json", actor={"id": "root"})
        assert response.status_code == 200
        task = response.json()["tasks"][0]
        assert task["name"] == "custom-name"
        assert task["function"] == f"{__name__}.{qualified_name}"
        assert handle.function == task["function"]
        assert "plugin" not in task
        await handle.task
        html = await ds.client.get("/-/tasks", actor={"id": "root"})
        assert html.status_code == 200
        assert task["function"] in html.text
    finally:
        await ds.invoke_shutdown()


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

    async def crashing_task(datasette):
        raise RuntimeError("kaboom")

    long_handle = ds.add_background_task(long_running, name="long-runner")
    crash_handle = ds.add_background_task(crashing_task, name="crashing_task")

    await ds.start_background_tasks()

    # Let the crashing_task run to completion and its done-callback (which sets
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

        crashed = by_name["crashing_task"]
        assert crashed["function"] == (
            f"{__name__}.test_running_and_crashed_task_states.<locals>.crashing_task"
        )
        assert crashed["state"] == "crashed"
        assert crashed["exception"] is not None
        assert isinstance(crashed["exception"], str)
        assert "kaboom" in crashed["exception"]
        assert "RuntimeError" in crashed["exception"]
    finally:
        long_handle.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await long_handle.task
