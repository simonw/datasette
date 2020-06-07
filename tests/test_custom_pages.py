import pytest
from .fixtures import make_app_client


@pytest.fixture(scope="session")
def custom_pages_client(tmp_path_factory):
    template_dir = tmp_path_factory.mktemp("page-templates")
    pages_dir = template_dir / "pages"
    pages_dir.mkdir()
    (pages_dir / "about.html").write_text("ABOUT! view_name:{{ view_name }}", "utf-8")
    (pages_dir / "request.html").write_text("path:{{ request.path }}", "utf-8")
    (pages_dir / "202.html").write_text("{{ custom_status(202) }}202!", "utf-8")
    (pages_dir / "headers.html").write_text(
        '{{ custom_header("x-this-is-foo", "foo") }}FOO'
        '{{ custom_header("x-this-is-bar", "bar") }}BAR',
        "utf-8",
    )
    (pages_dir / "atom.html").write_text(
        '{{ custom_header("content-type", "application/xml") }}<?xml ...>', "utf-8",
    )
    (pages_dir / "redirect.html").write_text(
        '{{ custom_redirect("/example") }}', "utf-8"
    )
    (pages_dir / "redirect2.html").write_text(
        '{{ custom_redirect("/example", 301) }}', "utf-8"
    )
    nested_dir = pages_dir / "nested"
    nested_dir.mkdir()
    (nested_dir / "nest.html").write_text("Nest!", "utf-8")
    with make_app_client(template_dir=str(template_dir)) as client:
        yield client


def test_custom_pages_view_name(custom_pages_client):
    response = custom_pages_client.get("/about")
    assert 200 == response.status
    assert "ABOUT! view_name:page" == response.text


def test_request_is_available(custom_pages_client):
    response = custom_pages_client.get("/request")
    assert 200 == response.status
    assert "path:/request" == response.text


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
