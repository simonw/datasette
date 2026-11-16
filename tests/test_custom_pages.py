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


@pytest.mark.parametrize(
    "path,expected_status,expected_text",
    (
        pytest.param("/about", 200, "ABOUT! view_name:page", id="view_name"),
        pytest.param("/request", 200, "path:/request", id="request_is_available"),
        pytest.param("/202", 202, "202!", id="custom_status"),
    ),
)
async def test_custom_page(custom_pages_client, path, expected_status, expected_text):
    response = await custom_pages_client.get(path)
    assert response.status_code == expected_status
    assert response.text == expected_text


async def test_custom_pages_with_base_url(custom_pages_client_with_base_url):
    # datasette.client adds the base_url prefix to the path
    response = await custom_pages_client_with_base_url.get("/request")
    assert response.status_code == 200
    assert response.text == "path:/prefix/request"


async def test_custom_pages_nested(custom_pages_client):
    response = await custom_pages_client.get("/nested/nest")
    assert response.status_code == 200
    assert response.text == "Nest!"
    response = await custom_pages_client.get("/nested/nest2")
    assert response.status_code == 404


async def test_custom_headers(custom_pages_client):
    response = await custom_pages_client.get("/headers")
    assert response.status_code == 200
    assert response.headers["x-this-is-foo"] == "foo"
    assert response.headers["x-this-is-bar"] == "bar"
    assert response.text == "FOOBAR"


async def test_custom_content_type(custom_pages_client):
    response = await custom_pages_client.get("/atom")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert response.text == "<?xml ...>"


@pytest.mark.parametrize(
    "path,expected_status,expected_location",
    (
        ("/redirect", 302, "/example"),
        ("/redirect2", 301, "/example"),
        # https://github.com/simonw/datasette/issues/2429
        ("//example.com/", 302, "/example.com"),
    ),
)
async def test_redirect(custom_pages_client, path, expected_status, expected_location):
    response = await custom_pages_client.get(path)
    assert response.status_code == expected_status
    assert response.headers["location"] == expected_location


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


async def test_custom_route_pattern_404(custom_pages_client):
    response = await custom_pages_client.get("/route_OhNo")
    assert response.status_code == 404
    assert "<h1>Error 404</h1>" in response.text
    assert ">Oh no</" in response.text


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
