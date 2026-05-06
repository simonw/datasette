import httpx
import pytest
import re
import socket
import subprocess
import sys
import tempfile
import time


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


@pytest.mark.serial
def test_serve_root_url_uses_actual_port_when_port_is_zero():
    # Regression test for https://github.com/simonw/datasette/issues/873
    # `datasette -p 0 --root` printed http://127.0.0.1:0/... instead of
    # the OS-assigned port.
    proc = subprocess.Popen(
        [sys.executable, "-m", "datasette", "--memory", "-p", "0", "--root"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tempfile.gettempdir(),
        text=True,
    )
    try:
        # Read lines until we see the auth-token URL or time out
        url_line = None
        deadline = time.time() + 10.0
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            if "/-/auth-token?token=" in line:
                url_line = line.strip()
                break
        assert url_line, "Did not see auth-token URL in datasette output"
        match = re.match(r"http://127\.0\.0\.1:(\d+)/-/auth-token\?token=", url_line)
        assert match, f"Unexpected auth-token URL format: {url_line!r}"
        printed_port = int(match.group(1))
        assert printed_port != 0, (
            "datasette -p 0 --root should print the OS-assigned port, "
            "not the placeholder 0"
        )
        # Confirm a server is actually listening on that printed port
        deadline2 = time.time() + 5.0
        last_err = None
        while time.time() < deadline2:
            try:
                response = httpx.get(f"http://127.0.0.1:{printed_port}/_memory.json")
                assert response.status_code == 200
                break
            except httpx.ConnectError as exc:
                last_err = exc
                time.sleep(0.1)
        else:
            raise AssertionError(
                f"Could not connect to printed port {printed_port}: {last_err}"
            )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
