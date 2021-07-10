from datasette.app import Datasette
from datasette.utils import PrefixedUrlString
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
    ds._settings["base_url"] = base_url
    actual = ds.urls.path(path)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


def test_path_applied_twice_does_not_double_prefix(ds):
    ds._settings["base_url"] = "/prefix/"
    path = ds.urls.path("/")
    assert path == "/prefix/"
    path = ds.urls.path(path)
    assert path == "/prefix/"


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("/", "/"),
        ("/prefix/", "/prefix/"),
    ],
)
def test_instance(ds, base_url, expected):
    ds._settings["base_url"] = base_url
    actual = ds.urls.instance()
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize(
    "base_url,file,expected",
    [
        ("/", "foo.js", "/-/static/foo.js"),
        ("/prefix/", "foo.js", "/prefix/-/static/foo.js"),
    ],
)
def test_static(ds, base_url, file, expected):
    ds._settings["base_url"] = base_url
    actual = ds.urls.static(file)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


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
    ds._settings["base_url"] = base_url
    actual = ds.urls.static_plugins(plugin, file)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("/", "/-/logout"),
        ("/prefix/", "/prefix/-/logout"),
    ],
)
def test_logout(ds, base_url, expected):
    ds._settings["base_url"] = base_url
    actual = ds.urls.logout()
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize(
    "base_url,format,expected",
    [
        ("/", None, "/_memory"),
        ("/prefix/", None, "/prefix/_memory"),
        ("/", "json", "/_memory.json"),
    ],
)
def test_database(ds, base_url, format, expected):
    ds._settings["base_url"] = base_url
    actual = ds.urls.database("_memory", format=format)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize(
    "base_url,name,format,expected",
    [
        ("/", "name", None, "/_memory/name"),
        ("/prefix/", "name", None, "/prefix/_memory/name"),
        ("/", "name", "json", "/_memory/name.json"),
        ("/", "name.json", "json", "/_memory/name.json?_format=json"),
    ],
)
def test_table_and_query(ds, base_url, name, format, expected):
    ds._settings["base_url"] = base_url
    actual1 = ds.urls.table("_memory", name, format=format)
    assert actual1 == expected
    assert isinstance(actual1, PrefixedUrlString)
    actual2 = ds.urls.query("_memory", name, format=format)
    assert actual2 == expected
    assert isinstance(actual2, PrefixedUrlString)


@pytest.mark.parametrize(
    "base_url,format,expected",
    [
        ("/", None, "/_memory/facetable/1"),
        ("/prefix/", None, "/prefix/_memory/facetable/1"),
        ("/", "json", "/_memory/facetable/1.json"),
    ],
)
def test_row(ds, base_url, format, expected):
    ds._settings["base_url"] = base_url
    actual = ds.urls.row("_memory", "facetable", "1", format=format)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize("base_url", ["/", "/prefix/"])
def test_database_hashed(app_client_with_hash, base_url):
    ds = app_client_with_hash.ds
    original_base_url = ds._settings["base_url"]
    try:
        ds._settings["base_url"] = base_url
        db_hash = ds.get_database("fixtures").hash
        assert len(db_hash) == 64
        expected = f"{base_url}fixtures-{db_hash[:7]}"
        assert ds.urls.database("fixtures") == expected
        assert ds.urls.table("fixtures", "name") == expected + "/name"
        assert ds.urls.query("fixtures", "name") == expected + "/name"
    finally:
        # Reset this since fixture is shared with other tests
        ds._settings["base_url"] = original_base_url
