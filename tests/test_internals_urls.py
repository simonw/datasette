import pytest

from datasette.app import Datasette
from datasette.utils import PrefixedUrlString, path_from_row_pks


@pytest.fixture(scope="module")
def ds():
    return Datasette([], memory=True)


@pytest.mark.parametrize(
    "ds_base_url,path,expected",
    [
        ("/", "/", "/"),
        ("/", "/foo", "/foo"),
        ("/prefix/", "/", "/prefix/"),
        ("/prefix/", "/foo", "/prefix/foo"),
        ("/prefix/", "foo", "/prefix/foo"),
        ("/data/", "/data", "/data/data"),
        ("/data/", "/data/foo", "/data/data/foo"),
    ],
)
def test_path(ds, ds_base_url, path, expected):
    ds._settings["base_url"] = ds_base_url
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
    "ds_base_url,expected",
    [
        ("/", "/"),
        ("/prefix/", "/prefix/"),
    ],
)
def test_instance(ds, ds_base_url, expected):
    ds._settings["base_url"] = ds_base_url
    actual = ds.urls.instance()
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize(
    "ds_base_url,file,expected",
    [
        ("/", "foo.js", "/-/static/foo.js"),
        ("/prefix/", "foo.js", "/prefix/-/static/foo.js"),
    ],
)
def test_static(ds, ds_base_url, file, expected):
    ds._settings["base_url"] = ds_base_url
    actual = ds.urls.static(file)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize(
    "ds_base_url,plugin,file,expected",
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
def test_static_plugins(ds, ds_base_url, plugin, file, expected):
    ds._settings["base_url"] = ds_base_url
    actual = ds.urls.static_plugins(plugin, file)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize(
    "ds_base_url,expected",
    [
        ("/", "/-/logout"),
        ("/prefix/", "/prefix/-/logout"),
    ],
)
def test_logout(ds, ds_base_url, expected):
    ds._settings["base_url"] = ds_base_url
    actual = ds.urls.logout()
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize(
    "ds_base_url,format,expected",
    [
        ("/", None, "/_memory"),
        ("/prefix/", None, "/prefix/_memory"),
        ("/", "json", "/_memory.json"),
    ],
)
def test_database(ds, ds_base_url, format, expected):
    ds._settings["base_url"] = ds_base_url
    actual = ds.urls.database("_memory", format=format)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize(
    "ds_base_url,name,format,expected",
    [
        ("/", "name", None, "/_memory/name"),
        ("/prefix/", "name", None, "/prefix/_memory/name"),
        ("/", "name", "json", "/_memory/name.json"),
        ("/", "name.json", "json", "/_memory/name~2Ejson.json"),
    ],
)
def test_table_and_query(ds, ds_base_url, name, format, expected):
    ds._settings["base_url"] = ds_base_url
    actual1 = ds.urls.table("_memory", name, format=format)
    assert actual1 == expected
    assert isinstance(actual1, PrefixedUrlString)
    actual2 = ds.urls.query("_memory", name, format=format)
    assert actual2 == expected
    assert isinstance(actual2, PrefixedUrlString)


@pytest.mark.parametrize(
    "ds_base_url,format,expected",
    [
        ("/", None, "/_memory/facetable/1"),
        ("/prefix/", None, "/prefix/_memory/facetable/1"),
        ("/", "json", "/_memory/facetable/1.json"),
    ],
)
def test_row(ds, ds_base_url, format, expected):
    ds._settings["base_url"] = ds_base_url
    actual = ds.urls.row("_memory", "facetable", "1", format=format)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize("ds_base_url", ["/", "/prefix/"])
@pytest.mark.parametrize("format", [None, "json"])
@pytest.mark.parametrize(
    "row,pks,use_rowid,expected_row_path",
    [
        ({"rowid": 42}, [], True, "42"),
        ({"id": "a/b,c.d~"}, ["id"], False, "a~2Fb~2Cc~2Ed~7E"),
        (
            {"a": "one,two", "b": {"value": "three/four"}},
            ["a", "b"],
            False,
            "one~2Ctwo,three~2Ffour",
        ),
    ],
)
def test_row_encoded_path(
    ds, ds_base_url, format, row, pks, use_rowid, expected_row_path
):
    ds._settings["base_url"] = ds_base_url
    row_path = path_from_row_pks(row, pks, use_rowid)
    assert row_path == expected_row_path
    actual = ds.urls.row("_memory", "table/name.csv", row_path, format=format)
    expected = ds_base_url + "_memory/table~2Fname~2Ecsv/" + expected_row_path
    if format:
        expected += "." + format
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)
    assert ds.urls.path(actual) == expected


@pytest.mark.parametrize("ds_base_url", ["/", "/prefix/"])
@pytest.mark.parametrize(
    "row_path", ["1", "a~2Fb~2Cc~2Ed~7E", "one~2Ctwo,three~2Ffour"]
)
@pytest.mark.parametrize(
    "column,expected_column",
    [
        ("content", "content"),
        ("image data", "image+data"),
        ("a+b&c/=d?#%", "a%2Bb%26c%2F%3Dd%3F%23%25"),
        ("café", "caf%C3%A9"),
    ],
)
def test_row_blob(ds, ds_base_url, row_path, column, expected_column):
    ds._settings["base_url"] = ds_base_url
    actual = ds.urls.row_blob("_memory", "table/name.csv", row_path, column)
    assert actual == (
        ds_base_url
        + "_memory/table~2Fname~2Ecsv/"
        + row_path
        + ".blob?_blob_column="
        + expected_column
    )
