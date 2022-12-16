import httpx
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def datasette(ds_client):
    await ds_client.ds.invoke_startup()
    return ds_client.ds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,expected_status",
    [
        ("get", "/", 200),
        ("options", "/", 200),
        ("head", "/", 200),
        ("put", "/", 405),
        ("patch", "/", 405),
        ("delete", "/", 405),
    ],
)
async def test_client_methods(datasette, method, path, expected_status):
    client_method = getattr(datasette.client, method)
    response = await client_method(path)
    assert isinstance(response, httpx.Response)
    assert response.status_code == expected_status
    # Try that again using datasette.client.request
    response2 = await datasette.client.request(method, path)
    assert response2.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [None, "/prefix/"])
async def test_client_post(datasette, prefix):
    original_base_url = datasette._settings["base_url"]
    try:
        if prefix is not None:
            datasette._settings["base_url"] = prefix
        response = await datasette.client.post(
            "/-/messages",
            data={
                "message": "A message",
            },
        )
        assert isinstance(response, httpx.Response)
        assert response.status_code == 302
        assert "ds_messages" in response.cookies
    finally:
        datasette._settings["base_url"] = original_base_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix,expected_path", [(None, "/asgi-scope"), ("/prefix/", "/prefix/asgi-scope")]
)
async def test_client_path(datasette, prefix, expected_path):
    original_base_url = datasette._settings["base_url"]
    try:
        if prefix is not None:
            datasette._settings["base_url"] = prefix
        response = await datasette.client.get("/asgi-scope")
        path = response.json()["path"]
        assert path == expected_path
    finally:
        datasette._settings["base_url"] = original_base_url
