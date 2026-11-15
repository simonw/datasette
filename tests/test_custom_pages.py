import pathlib

import pytest
import pytest_asyncio

from .fixtures import make_ds

TEST_TEMPLATE_DIRS = str(pathlib.Path(__file__).parent / "test_templates")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def custom_pages_client():
    ds = await make_ds(template_dir=TEST_TEMPLATE_DIRS)
    yield ds.client
    ds.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def custom_pages_client_with_base_url():
    ds = await make_ds(
        template_dir=TEST_TEMPLATE_DIRS, settings={"base_url": "/prefix/"}
    )
    yield ds.client
    ds.close()


@pytest.mark.asyncio
async def test_custom_pages_view_name(custom_pages_client):
    response = await custom_pages_client.get("/about")
    assert response.status_code == 200
    assert response.text == "ABOUT! view_name:page"


@pytest.mark.asyncio
async def test_request_is_available(custom_pages_client):
    response = await custom_pages_client.get("/request")
    assert response.status_code == 200
    assert response.text == "path:/request"


@pytest.mark.asyncio
async def test_custom_pages_with_base_url(custom_pages_client_with_base_url):
    # datasette.client adds the base_url prefix to the path
    response = await custom_pages_client_with_base_url.get("/request")
    assert response.status_code == 200
    assert response.text == "path:/prefix/request"


@pytest.mark.asyncio
async def test_custom_pages_nested(custom_pages_client):
    response = await custom_pages_client.get("/nested/nest")
    assert response.status_code == 200
    assert response.text == "Nest!"
    response = await custom_pages_client.get("/nested/nest2")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_custom_status(custom_pages_client):
    response = await custom_pages_client.get("/202")
    assert response.status_code == 202
    assert response.text == "202!"


@pytest.mark.asyncio
async def test_custom_headers(custom_pages_client):
    response = await custom_pages_client.get("/headers")
    assert response.status_code == 200
    assert response.headers["x-this-is-foo"] == "foo"
    assert response.headers["x-this-is-bar"] == "bar"
    assert response.text == "FOOBAR"


@pytest.mark.asyncio
async def test_custom_content_type(custom_pages_client):
    response = await custom_pages_client.get("/atom")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert response.text == "<?xml ...>"


@pytest.mark.asyncio
async def test_redirect(custom_pages_client):
    response = await custom_pages_client.get("/redirect")
    assert response.status_code == 302
    assert response.headers["Location"] == "/example"


@pytest.mark.asyncio
async def test_redirect2(custom_pages_client):
    response = await custom_pages_client.get("/redirect2")
    assert response.status_code == 301
    assert response.headers["Location"] == "/example"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected",
    [
        ("/route_Sally", "<p>Hello from Sally</p>"),
        ("/topic_python", "Topic page for python"),
        ("/topic_python/info", "Slug: info, Topic: python"),
    ],
)
async def test_custom_route_pattern(custom_pages_client, path, expected):
    response = await custom_pages_client.get(path)
    assert response.status_code == 200
    assert response.text.strip() == expected


@pytest.mark.asyncio
async def test_custom_route_pattern_404(custom_pages_client):
    response = await custom_pages_client.get("/route_OhNo")
    assert response.status_code == 404
    assert "<h1>Error 404</h1>" in response.text
    assert ">Oh no</" in response.text


@pytest.mark.asyncio
async def test_custom_route_pattern_with_slash_slash_302(custom_pages_client):
    # https://github.com/simonw/datasette/issues/2429
    response = await custom_pages_client.get("//example.com/")
    assert response.status_code == 302
    assert response.headers["location"] == "/example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/\\example.com/",
        "/\\\\example.com/",
        "/\\/example.com/",
    ),
)
async def test_redirect_does_not_allow_backslash_open_redirect(
    custom_pages_client, path
):
    # https://github.com/simonw/datasette/issues/2680
    # Browsers normalise backslashes to forward slashes, so a Location of
    # /\example.com would be treated as the protocol-relative //example.com
    response = await custom_pages_client.get(path)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location == "/example.com"
    # Must not start with anything a browser reads as protocol-relative
    assert not location.startswith("//")
    assert not location.startswith("/\\")
