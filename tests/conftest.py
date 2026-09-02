import importlib.metadata
import os
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio

from datasette import Event, hookimpl

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

UNDOCUMENTED_PERMISSIONS = {
    "this_is_allowed",
    "this_is_denied",
    "this_is_allowed_async",
    "this_is_denied_async",
    "no_match",
    # Test actions from test_hook_register_actions_with_custom_resources
    "manage_documents",
    "view_document_collection",
    "view_document",
}


def wait_until_responds(url, timeout=5.0, client=httpx, process=None, **kwargs):
    start = time.time()
    while time.time() - start < timeout:
        # If the server died there is no point waiting out the timeout - fail
        # now, with its output, instead of after `timeout` seconds of silence
        if process is not None and process.poll() is not None:
            raise AssertionError(
                "Server exited early with returncode {}\n{}".format(
                    process.returncode, process.stdout.read().decode("utf-8")
                )
            )
        try:
            client.get(url, **kwargs)
            return
        except httpx.TransportError:
            time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {url} to respond")


def find_free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# The otel fixtures moved to datasette.telemetry_testing, which is public
# plugin API - core's suite consumes it exactly the way a plugin's would.
from datasette.telemetry_testing import (  # noqa: F401, E402
    MetricsCollector,
    otel_metrics,
    otel_meter_provider,
    otel_provider,
    otel_spans,
)


@pytest.fixture
def bare_ds():
    """
    Minimal Datasette with no plugins, data, metadata, or config - for tests
    that want to exercise core behavior (e.g. middleware) in isolation.
    """
    from datasette.app import Datasette

    return Datasette(memory=True)


@pytest_asyncio.fixture(scope="session")
async def ds_client():
    import secrets

    from datasette.app import Datasette
    from datasette.database import Database

    from .fixtures import CONFIG, METADATA, PLUGINS_DIR

    ds = Datasette(
        metadata=METADATA,
        config=CONFIG,
        plugins_dir=PLUGINS_DIR,
        settings={
            "default_page_size": 50,
            "max_returned_rows": 100,
            "sql_time_limit_ms": 200,
            "facet_suggest_time_limit_ms": 200,  # Up from 50 default
            # Default is 3 but this results in "too many open files"
            # errors when running the full test suite:
            "num_sql_threads": 1,
        },
    )
    from datasette.fixtures import populate_fixture_database

    # Use a unique memory_name to avoid collisions between different
    # Datasette instances in the same process, but use "fixtures" for routing
    unique_memory_name = f"fixtures_{secrets.token_hex(8)}"
    db = ds.add_database(Database(ds, memory_name=unique_memory_name), name="fixtures")
    ds.remove_database("_memory")

    def prepare(conn):
        if not conn.execute("select count(*) from sqlite_master").fetchone()[0]:
            populate_fixture_database(conn)

    await db.execute_write_fn(prepare)
    await ds.invoke_startup()
    return ds.client


def pytest_report_header(config):
    conn = sqlite3.connect(":memory:")
    version = conn.execute("select sqlite_version()").fetchone()[0]
    conn.close()
    sqlite_utils_version = importlib.metadata.version("sqlite-utils")
    headers = [
        f"SQLite: {version}",
        f"sqlite-utils: {sqlite_utils_version}",
    ]
    if config.getoption("--playwright"):
        try:
            browsers = config.getoption("--browser")
        except ValueError:
            browsers = None
        if isinstance(browsers, str):
            browsers = [browsers]
        if browsers:
            headers.append("Playwright browsers: {}".format(", ".join(browsers)))
    return headers


def pytest_addoption(parser):
    parser.addoption(
        "--playwright",
        action="store_true",
        default=False,
        help="run Playwright browser automation tests",
    )


def pytest_configure(config):
    import sys

    sys._called_from_test = True


def pytest_unconfigure(config):
    import sys

    del sys._called_from_test


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--playwright"):
        skip_playwright = pytest.mark.skip(reason="need --playwright option to run")
        for item in items:
            if "playwright" in item.keywords:
                item.add_marker(skip_playwright)

    # Ensure test_cli.py and test_black.py and test_inspect.py run first before any asyncio code kicks in
    move_to_front(items, "test_cli")
    move_to_front(items, "test_black")
    move_to_front(items, "test_inspect_cli")
    move_to_front(items, "test_serve_with_get")
    move_to_front(items, "test_serve_with_get_exit_code_for_error")
    move_to_front(items, "test_inspect_cli_writes_to_file")
    move_to_front(items, "test_spatialite_error_if_attempt_to_open_spatialite")
    move_to_front(items, "test_package")
    move_to_front(items, "test_package_with_port")
    # Same reason: this one shells out to a fresh interpreter. Late in a serial
    # run the pytest process holds enough threads that the fork half of
    # subprocess' fork+exec crashes the interpreter on macOS/CPython 3.13
    # (SIGSEGV/SIGBUS inside _execute_child). Reproduces with any subprocess
    # call placed there, on an unmodified tree - running it first avoids it.
    move_to_front(items, "test_datasette_package_never_imports_the_sdk")
    move_to_front(items, "test_no_provider_takes_the_fast_path")


def move_to_front(items, test_name):
    test = [fn for fn in items if fn.name == test_name]
    if test:
        items.insert(0, items.pop(items.index(test[0])))


@pytest.fixture
def restore_working_directory(tmpdir, request):
    try:
        previous_cwd = os.getcwd()
    except OSError:
        # https://github.com/simonw/datasette/issues/1361
        previous_cwd = None
    tmpdir.chdir()

    def return_to_previous():
        os.chdir(previous_cwd)

    if previous_cwd is not None:
        request.addfinalizer(return_to_previous)


@pytest.fixture(scope="session", autouse=True)
def check_actions_are_documented():
    from datasette.default_actions import register_actions as default_register_actions
    from datasette.plugins import pm

    content = (
        pathlib.Path(__file__).parent.parent / "docs" / "authentication.rst"
    ).read_text()
    permissions_re = re.compile(r"\.\. _actions_([^\s:]+):")
    documented_actions = set(permissions_re.findall(content)).union(
        UNDOCUMENTED_PERMISSIONS
    )
    # Only Datasette core actions need to be documented - actions registered
    # by (test) plugins are checked for registration but not documentation
    core_actions = {action.name for action in default_register_actions()}

    def before(hook_name, hook_impls, kwargs):
        if hook_name == "permission_resources_sql":
            datasette = kwargs["datasette"]
            assert kwargs["action"] in datasette.actions, (
                "'{}' has not been registered with register_actions()".format(
                    kwargs["action"]
                )
                + " (or maybe a test forgot to do await ds.invoke_startup())"
            )
            action = kwargs.get("action").replace("-", "_")
            if kwargs["action"] in core_actions:
                assert (
                    action in documented_actions
                ), f"Undocumented permission action: {action}"

    pm.add_hookcall_monitoring(
        before=before, after=lambda outcome, hook_name, hook_impls, kwargs: None
    )


class TrackEventPlugin:
    __name__ = "TrackEventPlugin"

    @dataclass
    class OneEvent(Event):
        name = "one"

        extra: str

    @hookimpl
    def register_events(self, datasette):
        async def inner():
            return [self.OneEvent]

        return inner

    @hookimpl
    def track_event(self, datasette, event):
        datasette._tracked_events = getattr(datasette, "_tracked_events", [])
        datasette._tracked_events.append(event)


@pytest.fixture(scope="session", autouse=True)
def install_event_tracking_plugin():
    from datasette.plugins import pm

    pm.register(TrackEventPlugin(), name="TrackEventPlugin")


@pytest.fixture(scope="session")
def ds_localhost_http_server():
    ds_proc = subprocess.Popen(
        [sys.executable, "-m", "datasette", "--memory", "-p", "8041"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # Avoid FileNotFoundError: [Errno 2] No such file or directory:
        cwd=tempfile.gettempdir(),
    )
    wait_until_responds("http://localhost:8041/")
    # Check it started successfully
    assert not ds_proc.poll(), ds_proc.stdout.read().decode("utf-8")
    yield ds_proc
    # Shut it down at the end of the pytest session
    ds_proc.terminate()


@pytest.fixture(scope="session")
def ds_unix_domain_socket_server(tmp_path_factory):
    # This used to use tmp_path_factory.mktemp("uds") but that turned out to
    # produce paths that were too long to use as UDS on macOS, see
    # https://github.com/simonw/datasette/issues/1407 - so I switched to
    # using tempfile.gettempdir() with a per-process filename.
    uds = str(pathlib.Path(tempfile.gettempdir()) / f"datasette-{os.getpid()}.sock")
    try:
        os.unlink(uds)
    except FileNotFoundError:
        pass
    ds_proc = subprocess.Popen(
        [sys.executable, "-m", "datasette", "--memory", "--uds", uds],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tempfile.gettempdir(),
    )
    # Poll until available
    transport = httpx.HTTPTransport(uds=uds)
    client = httpx.Client(transport=transport)
    try:
        wait_until_responds(
            "http://localhost/_memory.json", timeout=30.0, client=client
        )
        # Check it started successfully
        assert not ds_proc.poll(), ds_proc.stdout.read().decode("utf-8")
        yield ds_proc, uds
    finally:
        client.close()
        # Shut it down at the end of the pytest session
        ds_proc.terminate()
        try:
            ds_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ds_proc.kill()
            ds_proc.wait()
        try:
            os.unlink(uds)
        except FileNotFoundError:
            pass


@pytest.fixture
def serve_with_plugins(tmp_path):
    """Factory fixture for starting ``datasette serve`` in a subprocess with
    plugins written to a temporary ``--plugins-dir``.

    For tests that need the real serve path: event-loop wiring, exit codes,
    signals. The usual in-process ``pm.register`` plugin pattern can't reach
    a subprocess, so plugin source is written out as importable files instead.

    Unlike ``ds_localhost_http_server`` this is function-scoped and takes a
    fresh port each time, because each test needs its own plugins. Call it as::

        proc, port = serve_with_plugins({"my_plugin": PLUGIN_SOURCE})

    ``plugins`` maps module name to Python source. Pass
    ``wait_for_startup=False`` when the server is expected to fail during
    startup rather than begin serving. Extra CLI arguments are passed through.
    Every process started is terminated when the test ends.
    """
    processes = []

    def start(plugins, *extra_args, wait_for_startup=True):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir(exist_ok=True)
        for module_name, source in plugins.items():
            (plugins_dir / f"{module_name}.py").write_text(source, "utf-8")
        port = find_free_port()
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "datasette",
                "--memory",
                "--plugins-dir",
                str(plugins_dir),
                "-h",
                "127.0.0.1",
                "-p",
                str(port),
                *extra_args,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # Avoid FileNotFoundError: [Errno 2] No such file or directory:
            cwd=tempfile.gettempdir(),
        )
        processes.append(proc)
        if wait_for_startup:
            wait_until_responds(
                f"http://127.0.0.1:{port}/-/versions.json", process=proc
            )
        return proc, port

    yield start

    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


# Import fixtures from fixtures.py to make them available
from .fixtures import (  # noqa: F401
    TEMP_PLUGIN_SECRET_FILE,
    app_client,
    app_client_base_url_prefix,
    app_client_conflicting_database_names,
    app_client_csv_max_mb_one,
    app_client_immutable_and_inspect_file,
    app_client_larger_cache_size,
    app_client_no_files,
    app_client_returned_rows_matches_page_size,
    app_client_shorter_time_limit,
    app_client_two_attached_databases,
    app_client_two_attached_databases_crossdb_enabled,
    app_client_two_attached_databases_one_immutable,
    app_client_with_cors,
    app_client_with_dot,
    app_client_with_trace,
    make_app_client,
)
