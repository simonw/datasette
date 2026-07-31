import signal
import socket
import subprocess
import time

import httpx
import pytest


@pytest.mark.serial
def test_serve_localhost_http(ds_localhost_http_server):
    response = httpx.get("http://localhost:8041/_memory.json")
    assert {
        "database": "_memory",
        "path": "/_memory",
        "tables": [],
    }.items() <= response.json().items()


@pytest.mark.serial
@pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"), reason="Requires socket.AF_UNIX support"
)
def test_serve_unix_domain_socket(ds_unix_domain_socket_server):
    _, uds = ds_unix_domain_socket_server
    transport = httpx.HTTPTransport(uds=uds)
    client = httpx.Client(transport=transport)
    response = client.get("http://localhost/_memory.json")
    assert {
        "database": "_memory",
        "path": "/_memory",
        "tables": [],
    }.items() <= response.json().items()


# Shaped after datasette-litestream's startup hook, which schedules a
# background task with asyncio.get_running_loop().create_task(...):
# https://github.com/datasette/datasette-litestream
MARKER_TASK_PLUGIN = """
import asyncio
from datasette import hookimpl
from datasette.utils.asgi import Response


@hookimpl
def startup(datasette):
    datasette._startup_calls = getattr(datasette, "_startup_calls", 0) + 1

    async def _mark():
        # Must await before setting the flag: a task with no internal
        # await point could finish on the throwaway loop before it
        # closed, masking the regression this test guards against.
        await asyncio.sleep(0.2)
        datasette._marker_task_ran = True

    asyncio.get_running_loop().create_task(_mark())


@hookimpl
def register_routes():
    async def marker_status(datasette):
        return Response.json(
            {
                "marker_task_ran": getattr(datasette, "_marker_task_ran", False),
                "startup_calls": getattr(datasette, "_startup_calls", 0),
            }
        )

    return [(r"^/-/marker-task-ran$", marker_status)]
"""


STARTUP_ERROR_PLUGIN = """
from datasette import hookimpl
from datasette.utils import StartupError


@hookimpl
def startup(datasette):
    raise StartupError("boom from plugin")
"""


@pytest.mark.serial
def test_startup_hook_background_task_runs_on_serving_loop(serve_with_plugins):
    """
    Litestream-shaped regression test: a startup hook that does
    asyncio.get_running_loop().create_task(...) must have that task
    actually execute before/while the server is handling requests. This
    only holds if invoke_startup() and uvicorn.Server.serve() share one
    event loop. This test fails against unmodified main, where
    invoke_startup() runs on a throwaway loop that is closed before
    uvicorn opens its own loop to serve.
    """
    _, port = serve_with_plugins({"marker_task_plugin": MARKER_TASK_PLUGIN})
    # The fixture has already waited for the server to answer requests. The
    # marker task deliberately awaits before setting its flag, so poll for a
    # moment rather than assuming it landed before the first request arrived.
    deadline = time.time() + 3.0
    payload = {}
    while time.time() < deadline:
        payload = httpx.get(
            f"http://127.0.0.1:{port}/-/marker-task-ran", timeout=1.0
        ).json()
        if payload["marker_task_ran"]:
            break
        time.sleep(0.05)
    assert payload.get("marker_task_ran"), (
        "The startup hook's asyncio.create_task(...) never ran - "
        "invoke_startup() and the server are not sharing an event loop"
    )
    # Polling above means this test would also pass if the startup hook were
    # re-run on the serving loop by the first-request fallback - which would
    # hide exactly the bug being tested. invoke_startup() is idempotent today
    # so that cannot happen; assert it explicitly so that if the idempotency
    # guard is ever removed this test fails loudly instead of silently
    # becoming a no-op.
    assert payload["startup_calls"] == 1, (
        "startup hook ran {} times - the marker may have been set by a "
        "re-run on the serving loop rather than by the original task".format(
            payload["startup_calls"]
        )
    )


@pytest.mark.serial
def test_startup_error_fails_fast_before_port_binds(serve_with_plugins):
    """
    A "startup" plugin hook that raises StartupError must fail fast: print
    the message, exit non-zero, and never accept a connection on the port -
    the failure must happen before uvicorn.Server binds the socket.
    """
    proc, port = serve_with_plugins(
        {"startup_error_plugin": STARTUP_ERROR_PLUGIN}, wait_for_startup=False
    )
    stdout, _ = proc.communicate(timeout=15)
    output = stdout.decode("utf-8")
    assert proc.returncode not in (0, None), output
    assert "boom from plugin" in output, output

    # Nothing is listening on the port now the process has exited. This
    # confirms the socket was not left bound; on its own it cannot prove the
    # failure preceded the bind, since a port nothing ever touched also
    # refuses connections.
    with (
        pytest.raises(OSError),
        socket.create_connection(("127.0.0.1", port), timeout=0.2),
    ):
        pass


# Proves the ticket-01 serve path (asyncio.run(_serve_async()) wrapping
# uvicorn.Server.serve()) actually delivers a graceful signal through to
# uvicorn's lifespan.shutdown, which now runs Datasette.invoke_shutdown()
# and therefore every plugin's `shutdown` hook. The plugin below writes a
# sentinel file from inside its shutdown hook so this can be checked from
# outside the subprocess after it exits.
SHUTDOWN_SENTINEL_PLUGIN_TEMPLATE = '''
import pathlib
from datasette import hookimpl

SENTINEL_PATH = {sentinel_path!r}


@hookimpl
def shutdown(datasette):
    pathlib.Path(SENTINEL_PATH).write_text("shutdown ran", "utf-8")
'''


def _start_serve_with_shutdown_sentinel(serve_with_plugins, tmp_path):
    sentinel_path = tmp_path / "shutdown-sentinel.txt"
    proc, _ = serve_with_plugins(
        {
            "shutdown_sentinel_plugin": SHUTDOWN_SENTINEL_PLUGIN_TEMPLATE.format(
                sentinel_path=str(sentinel_path)
            )
        }
    )
    return proc, sentinel_path


@pytest.mark.serial
def test_sigterm_runs_shutdown_hooks(serve_with_plugins, tmp_path):
    ds_proc, sentinel_path = _start_serve_with_shutdown_sentinel(
        serve_with_plugins, tmp_path
    )
    assert not sentinel_path.exists()
    ds_proc.send_signal(signal.SIGTERM)
    try:
        ds_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        ds_proc.kill()
        ds_proc.wait()
        raise AssertionError(
            "datasette serve did not exit within 10s of SIGTERM\n"
            + ds_proc.stdout.read().decode("utf-8")
        )
    assert sentinel_path.exists(), (
        "shutdown hook never wrote its sentinel file after SIGTERM\n"
        + ds_proc.stdout.read().decode("utf-8")
    )
    assert sentinel_path.read_text("utf-8") == "shutdown ran"


@pytest.mark.serial
@pytest.mark.skipif(
    not hasattr(signal, "SIGINT"), reason="Requires signal.SIGINT support"
)
def test_sigint_runs_shutdown_hooks(serve_with_plugins, tmp_path):
    ds_proc, sentinel_path = _start_serve_with_shutdown_sentinel(
        serve_with_plugins, tmp_path
    )
    assert not sentinel_path.exists()
    ds_proc.send_signal(signal.SIGINT)
    try:
        ds_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        ds_proc.kill()
        ds_proc.wait()
        raise AssertionError(
            "datasette serve did not exit within 10s of SIGINT\n"
            + ds_proc.stdout.read().decode("utf-8")
        )
    assert sentinel_path.exists(), (
        "shutdown hook never wrote its sentinel file after SIGINT\n"
        + ds_proc.stdout.read().decode("utf-8")
    )
    assert sentinel_path.read_text("utf-8") == "shutdown ran"
