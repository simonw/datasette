import pytest

from datasette.app import Datasette


@pytest.mark.asyncio
async def test_index_html_cors_headers():
    ds = Datasette([], memory=True, cors=True)
    response = await ds.client.get("/")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["access-control-allow-headers"] == "Authorization, Content-Type"
    assert response.headers["access-control-expose-headers"] == "Link"
