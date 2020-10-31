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
    "base_url,plugin,file,expected",
    [
        (
            "/",
            "datasette_cluster_map",
            "datasette-cluster-map.js",
            "/-/static-plugins/datasette_cluster_map/datasette-cluster-map.js",
        ),
        (
            "/prefix/",
            "datasette_cluster_map",
            "datasette-cluster-map.js",
            "/prefix/-/static-plugins/datasette_cluster_map/datasette-cluster-map.js",
        ),
    ],
)
def test_static_plugins(ds, base_url, plugin, file, expected):
    ds._config["base_url"] = base_url
    assert ds.urls.static_plugins(plugin, file) == expected


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


@pytest.mark.parametrize(
    "base_url,format,expected",
    [
        ("/", None, "/:memory:"),
        ("/prefix/", None, "/prefix/:memory:"),
        ("/", "json", "/:memory:.json"),
    ],
)
def test_database(ds, base_url, format, expected):
    ds._config["base_url"] = base_url
    assert ds.urls.database(":memory:", format=format) == expected


@pytest.mark.parametrize(
    "base_url,name,format,expected",
    [
        ("/", "name", None, "/:memory:/name"),
        ("/prefix/", "name", None, "/prefix/:memory:/name"),
        ("/", "name", "json", "/:memory:/name.json"),
        ("/", "name.json", "json", "/:memory:/name.json?_format=json"),
    ],
)
def test_table_and_query(ds, base_url, name, format, expected):
    ds._config["base_url"] = base_url
    assert ds.urls.table(":memory:", name, format=format) == expected
    assert ds.urls.query(":memory:", name, format=format) == expected


@pytest.mark.parametrize(
    "base_url,format,expected",
    [
        ("/", None, "/:memory:/facetable/1"),
        ("/prefix/", None, "/prefix/:memory:/facetable/1"),
        ("/", "json", "/:memory:/facetable/1.json"),
    ],
)
def test_row(ds, base_url, format, expected):
    ds._config["base_url"] = base_url
    assert ds.urls.row(":memory:", "facetable", "1", format=format) == expected


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
