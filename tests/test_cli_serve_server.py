import socket
import subprocess
import sys
import tempfile
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


def _find_free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# Shaped after datasette-litestream's (sync) startup hook, which schedules a
# background task with asyncio.get_running_loop().create_task(...):
# https://github.com/simonw/datasette-litestream/blob/main/datasette_litestream/__init__.py
# That only has a chance to actually run if invoke_startup() executes on the
# same event loop that goes on to serve requests - if it runs on a throwaway
# loop that gets closed straight after (as on unmodified main), the task is
# scheduled but never gets a turn before the loop is torn down.
MARKER_TASK_PLUGIN = '''
import asyncio
from datasette import hookimpl
from datasette.utils.asgi import Response


@hookimpl
def startup(datasette):
    async def _mark():
        # The await is essential to the regression: a task with no
        # internal await point can complete during the brief window
        # between run_until_complete()'s coroutine finishing and the
        # temporary loop actually stopping, masking the bug this test
        # guards against. Real background tasks (like
        # datasette-litestream's credential_refresh_loop) always have an
        # internal await, and never get to resume once their throwaway
        # loop is closed.
        await asyncio.sleep(0.2)
        datasette._marker_task_ran = True

    asyncio.get_running_loop().create_task(_mark())


@hookimpl
def register_routes():
    async def marker_status(datasette):
        return Response.json(
            {"marker_task_ran": getattr(datasette, "_marker_task_ran", False)}
        )

    return [(r"^/-/marker-task-ran$", marker_status)]
'''


STARTUP_ERROR_PLUGIN = '''
from datasette import hookimpl
from datasette.utils import StartupError


@hookimpl
def startup(datasette):
    raise StartupError("boom from plugin")
'''


@pytest.mark.serial
def test_startup_hook_background_task_runs_on_serving_loop(tmp_path):
    """
    Litestream-shaped regression test: a startup hook that does
    asyncio.get_running_loop().create_task(...) must have that task
    actually execute before/while the server is handling requests. This
    only holds if invoke_startup() and uvicorn.Server.serve() share one
    event loop. This test fails against unmodified main, where
    invoke_startup() runs on a throwaway loop that is closed before
    uvicorn opens its own loop to serve.
    """
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "marker_task_plugin.py").write_text(MARKER_TASK_PLUGIN, "utf-8")

    port = _find_free_port()
    ds_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "datasette",
            "--memory",
            "--plugins-dir",
            str(plugins_dir),
            "-p",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tempfile.gettempdir(),
    )
    try:
        url = f"http://localhost:{port}/-/marker-task-ran"
        deadline = time.time() + 15.0
        marker_task_ran = False
        while time.time() < deadline:
            if ds_proc.poll() is not None:
                raise AssertionError(
                    "datasette serve exited early\n"
                    + ds_proc.stdout.read().decode("utf-8")
                )
            try:
                response = httpx.get(url, timeout=1.0)
            except httpx.TransportError:
                time.sleep(0.1)
                continue
            if response.status_code == 200 and response.json().get(
                "marker_task_ran"
            ):
                marker_task_ran = True
                break
            time.sleep(0.1)
        assert marker_task_ran, (
            "The startup hook's asyncio.create_task(...) never ran - "
            "invoke_startup() and the server are not sharing an event loop"
        )
    finally:
        ds_proc.terminate()
        try:
            ds_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ds_proc.kill()
            ds_proc.wait()


@pytest.mark.serial
def test_startup_error_fails_fast_before_port_binds(tmp_path):
    """
    A "startup" plugin hook that raises StartupError must fail fast: print
    the message, exit non-zero, and never accept a connection on the port -
    the failure must happen before uvicorn.Server binds the socket.
    """
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "startup_error_plugin.py").write_text(
        STARTUP_ERROR_PLUGIN, "utf-8"
    )

    port = _find_free_port()
    ds_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "datasette",
            "--memory",
            "--plugins-dir",
            str(plugins_dir),
            "-p",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tempfile.gettempdir(),
    )
    try:
        deadline = time.time() + 15.0
        # While the process is still alive (it should crash almost
        # immediately) repeatedly confirm nothing is listening on the port
        while ds_proc.poll() is None and time.time() < deadline:
            with pytest.raises(OSError):
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    pass
            time.sleep(0.05)

        stdout, _ = ds_proc.communicate(timeout=5)
        output = stdout.decode("utf-8")
        assert ds_proc.returncode not in (0, None), output
        assert "boom from plugin" in output, output

        # And confirm it never accepted a connection even now it has exited
        with pytest.raises(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                pass
    finally:
        if ds_proc.poll() is None:
            ds_proc.kill()
            ds_proc.wait()
