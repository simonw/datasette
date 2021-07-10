import os
import pathlib
import pytest
import re
import subprocess
import tempfile
import time
import trustme


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
}


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
def check_permission_actions_are_documented():
    from datasette.plugins import pm

    content = (
        pathlib.Path(__file__).parent.parent / "docs" / "authentication.rst"
    ).read_text()
    permissions_re = re.compile(r"\.\. _permissions_([^\s:]+):")
    documented_permission_actions = set(permissions_re.findall(content)).union(
        UNDOCUMENTED_PERMISSIONS
    )

    def before(hook_name, hook_impls, kwargs):
        if hook_name == "permission_allowed":
            action = kwargs.get("action").replace("-", "_")
            assert (
                action in documented_permission_actions
            ), "Undocumented permission action: {}, resource: {}".format(
                action, kwargs["resource"]
            )

    pm.add_hookcall_monitoring(
        before=before, after=lambda outcome, hook_name, hook_impls, kwargs: None
    )


@pytest.fixture(scope="session")
def ds_localhost_http_server():
    ds_proc = subprocess.Popen(
        ["datasette", "--memory", "-p", "8041"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # Avoid FileNotFoundError: [Errno 2] No such file or directory:
        cwd=tempfile.gettempdir(),
    )
    # Give the server time to start
    time.sleep(1.5)
    # Check it started successfully
    assert not ds_proc.poll(), ds_proc.stdout.read().decode("utf-8")
    yield ds_proc
    # Shut it down at the end of the pytest session
    ds_proc.terminate()


@pytest.fixture(scope="session")
def ds_localhost_https_server(tmp_path_factory):
    cert_directory = tmp_path_factory.mktemp("certs")
    ca = trustme.CA()
    server_cert = ca.issue_cert("localhost")
    keyfile = str(cert_directory / "server.key")
    certfile = str(cert_directory / "server.pem")
    client_cert = str(cert_directory / "client.pem")
    server_cert.private_key_pem.write_to_path(path=keyfile)
    for blob in server_cert.cert_chain_pems:
        blob.write_to_path(path=certfile, append=True)
    ca.cert_pem.write_to_path(path=client_cert)
    ds_proc = subprocess.Popen(
        [
            "datasette",
            "--memory",
            "-p",
            "8042",
            "--ssl-keyfile",
            keyfile,
            "--ssl-certfile",
            certfile,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tempfile.gettempdir(),
    )
    # Give the server time to start
    time.sleep(1.5)
    # Check it started successfully
    assert not ds_proc.poll(), ds_proc.stdout.read().decode("utf-8")
    yield ds_proc, client_cert
    # Shut it down at the end of the pytest session
    ds_proc.terminate()


@pytest.fixture(scope="session")
def ds_unix_domain_socket_server(tmp_path_factory):
    socket_folder = tmp_path_factory.mktemp("uds")
    uds = str(socket_folder / "datasette.sock")
    ds_proc = subprocess.Popen(
        ["datasette", "--memory", "--uds", uds],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tempfile.gettempdir(),
    )
    # Give the server time to start
    time.sleep(1.5)
    # Check it started successfully
    assert not ds_proc.poll(), ds_proc.stdout.read().decode("utf-8")
    yield ds_proc, uds
    # Shut it down at the end of the pytest session
    ds_proc.terminate()
