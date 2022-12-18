import httpx
import pytest
import socket


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
