import pathlib
import pytest
from .fixtures import make_app_client

TEST_TEMPLATE_DIRS = str(pathlib.Path(__file__).parent / "test_templates")


@pytest.fixture(scope="session")
def custom_pages_client():
    with make_app_client(template_dir=TEST_TEMPLATE_DIRS) as client:
        yield client


@pytest.fixture(scope="session")
def custom_pages_client_with_base_url():
    with make_app_client(
        template_dir=TEST_TEMPLATE_DIRS, config={"base_url": "/prefix/"}
    ) as client:
        yield client


def test_custom_pages_view_name(custom_pages_client):
    response = custom_pages_client.get("/about")
    assert 200 == response.status
    assert "ABOUT! view_name:page" == response.text


def test_request_is_available(custom_pages_client):
    response = custom_pages_client.get("/request")
    assert 200 == response.status
    assert "path:/request" == response.text


def test_custom_pages_with_base_url(custom_pages_client_with_base_url):
    response = custom_pages_client_with_base_url.get("/prefix/request")
    assert 200 == response.status
    assert "path:/prefix/request" == response.text


def test_custom_pages_nested(custom_pages_client):
    response = custom_pages_client.get("/nested/nest")
    assert 200 == response.status
    assert "Nest!" == response.text
    response = custom_pages_client.get("/nested/nest2")
    assert 404 == response.status


def test_custom_status(custom_pages_client):
    response = custom_pages_client.get("/202")
    assert 202 == response.status
    assert "202!" == response.text


def test_custom_headers(custom_pages_client):
    response = custom_pages_client.get("/headers")
    assert 200 == response.status
    assert "foo" == response.headers["x-this-is-foo"]
    assert "bar" == response.headers["x-this-is-bar"]
    assert "FOOBAR" == response.text


def test_custom_content_type(custom_pages_client):
    response = custom_pages_client.get("/atom")
    assert 200 == response.status
    assert response.headers["content-type"] == "application/xml"
    assert "<?xml ...>" == response.text


def test_redirect(custom_pages_client):
    response = custom_pages_client.get("/redirect", allow_redirects=False)
    assert 302 == response.status
    assert "/example" == response.headers["Location"]


def test_redirect2(custom_pages_client):
    response = custom_pages_client.get("/redirect2", allow_redirects=False)
    assert 301 == response.status
    assert "/example" == response.headers["Location"]


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/route_Sally", "<p>Hello from Sally</p>"),
        ("/topic_python", "Topic page for python"),
        ("/topic_python/info", "Slug: info, Topic: python"),
    ],
)
def test_custom_route_pattern(custom_pages_client, path, expected):
    response = custom_pages_client.get(path)
    assert response.status == 200
    assert response.text.strip() == expected


def test_custom_route_pattern_404(custom_pages_client):
    response = custom_pages_client.get("/route_OhNo")
    assert response.status == 404
    assert "<h1>Error 404</h1>" in response.text
    assert ">Oh no</" in response.text
