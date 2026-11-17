import pytest

from datasette.app import Datasette
from datasette.utils import PrefixedUrlString
from datasette.utils.asgi import Request


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


@pytest.mark.parametrize(
    "base_url,target,kwargs,expected",
    [
        ("/", "/db/t?a=1", {"add": {"b": 2}}, "/db/t?a=1&b=2"),
        ("/", "/db/t?a=1", {"remove": "a"}, "/db/t"),
        ("/", "/db/t?a=1#top", {"replace": {"a": 2}}, "/db/t?a=2#top"),
        ("/", "/db/t?a=1", {"format": "json"}, "/db/t.json?a=1"),
        ("/prefix/", "/db/t?a=", {"add": {"b": 2}}, "/prefix/db/t?a=&b=2"),
        ("/prefix/", "/db/t", {"format": "csv"}, "/prefix/db/t.csv"),
        (
            "/",
            "/db/t?sql=select+1",
            {"format": "csv", "add": {"_dl": 1}},
            "/db/t.csv?sql=select+1&_dl=1",
        ),
    ],
)
def test_modify_path(ds, base_url, target, kwargs, expected):
    ds._settings["base_url"] = base_url
    actual = ds.urls.modify(target, **kwargs)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)
    # The result can be modified again without doubling the prefix
    assert ds.urls.modify(actual) == expected


@pytest.mark.parametrize(
    "base_url,scope_extras,expected",
    [
        ("/", {}, "/db/t?b=&a=2"),
        # base_url already stripped by a proxy
        ("/prefix/", {}, "/prefix/db/t?b=&a=2"),
        # base_url stripped by Datasette's router, which sets route_path
        (
            "/prefix/",
            {"path": "/prefix/db/t", "route_path": "/db/t"},
            "/prefix/db/t?b=&a=2",
        ),
    ],
)
def test_modify_request(ds, base_url, scope_extras, expected):
    ds._settings["base_url"] = base_url
    request = Request.fake("/db/t?a=1&b=&_next=5")
    request.scope.update(scope_extras)
    actual = ds.urls.modify(request, replace={"a": 2}, remove="_next")
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


def test_modify_request_replaces_format(ds):
    ds._settings["base_url"] = "/"
    request = Request.fake("/db/t.json?a=1", url_vars={"format": "json"})
    assert ds.urls.modify(request, format="csv") == "/db/t.csv?a=1"
    assert ds.urls.modify(request, add={"b": 2}) == "/db/t.json?a=1&b=2"
    # https://github.com/simonw/datasette/issues/1179
    request = Request.fake("/db/t.Notebook", url_vars={"format": "Notebook"})
    assert ds.urls.modify(request, format="json") == "/db/t.json"


def test_path_with_format_and_query_string(ds):
    ds._settings["base_url"] = "/"
    assert ds.urls.path("/db/-/query?sql=select+1", format="json") == (
        "/db/-/query.json?sql=select+1"
    )


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
    "ds_base_url,table,row_path,format,expected",
    [
        ("/", "facetable", "1", None, "/_memory/facetable/1"),
        ("/prefix/", "facetable", "1", None, "/prefix/_memory/facetable/1"),
        ("/", "facetable", "1", "json", "/_memory/facetable/1.json"),
        ("/prefix/", "facetable", "1", "json", "/prefix/_memory/facetable/1.json"),
        # Compound primary keys are comma-separated
        ("/", "compound", "a,b", None, "/_memory/compound/a,b"),
        ("/", "compound", "a,b", "json", "/_memory/compound/a,b.json"),
        # A string row_path is used as-is, without any encoding
        ("/", "compound", "a~2Fb,c", None, "/_memory/compound/a~2Fb,c"),
        # A list or tuple of primary key values is tilde-encoded
        ("/", "facetable", [1], None, "/_memory/facetable/1"),
        ("/", "facetable", (1,), "json", "/_memory/facetable/1.json"),
        ("/prefix/", "compound", ["a", "b"], None, "/prefix/_memory/compound/a,b"),
        ("/", "compound", ["a/b", "c"], None, "/_memory/compound/a~2Fb,c"),
        ("/", "compound", ["a,b", "c.d"], None, "/_memory/compound/a~2Cb,c~2Ed"),
        ("/", "compound", ["a,b", 2], "json", "/_memory/compound/a~2Cb,2.json"),
        # The table name is tilde-encoded
        ("/", "table/with/slashes", "3", None, "/_memory/table~2Fwith~2Fslashes/3"),
    ],
)
def test_row(ds, ds_base_url, table, row_path, format, expected):
    ds._settings["base_url"] = ds_base_url
    actual = ds.urls.row("_memory", table, row_path, format=format)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)


@pytest.mark.parametrize(
    "ds_base_url,table,row_path,column,expected",
    [
        (
            "/",
            "binary_data",
            "1",
            "data",
            "/_memory/binary_data/1.blob?_blob_column=data",
        ),
        (
            "/prefix/",
            "binary_data",
            "1",
            "data",
            "/prefix/_memory/binary_data/1.blob?_blob_column=data",
        ),
        (
            "/",
            "compound",
            "a~2Fb,c",
            "data",
            "/_memory/compound/a~2Fb,c.blob?_blob_column=data",
        ),
        # A list or tuple of primary key values is tilde-encoded
        (
            "/",
            "compound",
            ["a/b", "c"],
            "data",
            "/_memory/compound/a~2Fb,c.blob?_blob_column=data",
        ),
        (
            "/",
            "binary_data",
            (1,),
            "data",
            "/_memory/binary_data/1.blob?_blob_column=data",
        ),
        # Table name is tilde-encoded, column name is URL-encoded
        (
            "/",
            "table/with/slashes",
            "1",
            "my column&more",
            "/_memory/table~2Fwith~2Fslashes/1.blob?_blob_column=my+column%26more",
        ),
    ],
)
def test_row_blob(ds, ds_base_url, table, row_path, column, expected):
    ds._settings["base_url"] = ds_base_url
    actual = ds.urls.row_blob("_memory", table, row_path, column)
    assert actual == expected
    assert isinstance(actual, PrefixedUrlString)
