from .fixtures import app_client
import httpx
import pytest


@pytest.fixture
def datasette(app_client):
    return app_client.ds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,expected_status",
    [
        ("get", "/", 200),
        ("options", "/", 405),
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
async def test_client_post(datasette):
    response = await datasette.client.post(
        "/-/messages",
        data={
            "message": "A message",
        },
        allow_redirects=False,
    )
    assert isinstance(response, httpx.Response)
    assert response.status_code == 302
    assert "ds_messages" in response.cookies
