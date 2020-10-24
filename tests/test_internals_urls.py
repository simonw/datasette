from datasette.app import Datasette
from .fixtures import app_client_with_hash
import pytest


@pytest.fixture(scope="module")
def ds():
    return Datasette([], memory=True)


@pytest.mark.parametrize(
    "base_url,path,expected",
    [
        ("/", "/", "/"),
        ("/", "/foo", "/foo"),
        ("/prefix/", "/", "/prefix/"),
        ("/prefix/", "/foo", "/prefix/foo"),
        ("/prefix/", "foo", "/prefix/foo"),
    ],
)
def test_path(ds, base_url, path, expected):
    ds._config["base_url"] = base_url
    assert ds.urls.path(path) == expected


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("/", "/"),
        ("/prefix/", "/prefix/"),
    ],
)
def test_instance(ds, base_url, expected):
    ds._config["base_url"] = base_url
    assert ds.urls.instance() == expected


@pytest.mark.parametrize(
    "base_url,file,expected",
    [
        ("/", "foo.js", "/-/static/foo.js"),
        ("/prefix/", "foo.js", "/prefix/-/static/foo.js"),
    ],
)
def test_static(ds, base_url, file, expected):
    ds._config["base_url"] = base_url
    assert ds.urls.static(file) == expected


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("/", "/-/logout"),
        ("/prefix/", "/prefix/-/logout"),
    ],
)
def test_logout(ds, base_url, expected):
    ds._config["base_url"] = base_url
    assert ds.urls.logout() == expected


@pytest.mark.parametrize("base_url,expected", [
    ("/", "/:memory:"),
    ("/prefix/", "/prefix/:memory:"),
])
def test_database(ds, base_url, expected):
    ds._config["base_url"] = base_url
    assert ds.urls.database(":memory:") == expected
    # Do table and query while we are here
    assert ds.urls.table(":memory:", "name") == expected + "/name"
    assert ds.urls.query(":memory:", "name") == expected + "/name"


@pytest.mark.parametrize("base_url", ["/", "/prefix/"])
def test_database_hashed(app_client_with_hash, base_url):
    ds = app_client_with_hash.ds
    original_base_url = ds._config["base_url"]
    try:
        ds._config["base_url"] = base_url
        db_hash = ds.get_database("fixtures").hash
        assert len(db_hash) == 64
        expected = "{}fixtures-{}".format(base_url, db_hash[:7])
        assert ds.urls.database("fixtures") == expected
        assert ds.urls.table("fixtures", "name") == expected + "/name"
        assert ds.urls.query("fixtures", "name") == expected + "/name"
    finally:
        # Reset this since fixture is shared with other tests
        ds._config["base_url"] = original_base_url
