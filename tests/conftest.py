import httpx
import os
import pathlib
import pytest
import pytest_asyncio
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
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


def wait_until_responds(url, timeout=5.0, client=httpx, **kwargs):
    start = time.time()
    while time.time() - start < timeout:
        try:
            client.get(url, **kwargs)
            return
        except httpx.ConnectError:
            time.sleep(0.1)
    raise AssertionError("Timed out waiting for {} to respond".format(url))


@pytest_asyncio.fixture
async def ds_client():
    from datasette.app import Datasette
    from datasette.database import Database
    from .fixtures import CONFIG, METADATA, PLUGINS_DIR
    import secrets

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
    from .fixtures import TABLES, TABLE_PARAMETERIZED_SQL

    # Use a unique memory_name to avoid collisions between different
    # Datasette instances in the same process, but use "fixtures" for routing
    unique_memory_name = f"fixtures_{secrets.token_hex(8)}"
    db = ds.add_database(Database(ds, memory_name=unique_memory_name), name="fixtures")
    ds.remove_database("_memory")

    def prepare(conn):
        if not conn.execute("select count(*) from sqlite_master").fetchone()[0]:
            conn.executescript(TABLES)
            for sql, params in TABLE_PARAMETERIZED_SQL:
                with conn:
                    conn.execute(sql, params)

    await db.execute_write_fn(prepare)
    await ds.invoke_startup()
    return ds.client


def pytest_report_header(config):
    return "SQLite: {}".format(
        sqlite3.connect(":memory:").execute("select sqlite_version()").fetchone()[0]
    )


def pytest_configure(config):
    import sys

    sys._called_from_test = True


def pytest_unconfigure(config):
    import sys

    del sys._called_from_test


def pytest_collection_modifyitems(items):
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
    from datasette.plugins import pm

    content = (
        pathlib.Path(__file__).parent.parent / "docs" / "authentication.rst"
    ).read_text()
    permissions_re = re.compile(r"\.\. _actions_([^\s:]+):")
    documented_actions = set(permissions_re.findall(content)).union(
        UNDOCUMENTED_PERMISSIONS
    )

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
            assert (
                action in documented_actions
            ), "Undocumented permission action: {}".format(action)

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
    # using tempfile.gettempdir()
    uds = str(pathlib.Path(tempfile.gettempdir()) / "datasette.sock")
    ds_proc = subprocess.Popen(
        [sys.executable, "-m", "datasette", "--memory", "--uds", uds],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tempfile.gettempdir(),
    )
    # Poll until available
    transport = httpx.HTTPTransport(uds=uds)
    client = httpx.Client(transport=transport)
    wait_until_responds("http://localhost/_memory.json", client=client)
    # Check it started successfully
    assert not ds_proc.poll(), ds_proc.stdout.read().decode("utf-8")
    yield ds_proc, uds
    # Shut it down at the end of the pytest session
    ds_proc.terminate()


# Import fixtures from fixtures.py to make them available
from .fixtures import (  # noqa: E402, F401
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
    generate_compound_rows,
    generate_sortable_rows,
    make_app_client,
    TEMP_PLUGIN_SECRET_FILE,
)
