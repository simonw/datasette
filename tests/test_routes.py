from datasette.app import Datasette
from datasette.utils import resolve_routes
import pytest


@pytest.fixture(scope="session")
def routes():
    ds = Datasette()
    return ds._routes()


@pytest.mark.parametrize(
    "path,expected",
    (
        ("/", "IndexView"),
        ("/foo", "DatabaseView"),
        ("/foo.csv", "DatabaseView"),
        ("/foo.json", "DatabaseView"),
        ("/foo.humbug", "DatabaseView"),
        ("/foo/humbug", "TableView"),
        ("/foo/humbug.json", "TableView"),
        ("/foo/humbug.blah", "TableView"),
        ("/foo/humbug/1", "RowView"),
        ("/foo/humbug/1.json", "RowView"),
        ("/-/metadata.json", "JsonDataView"),
        ("/-/metadata", "JsonDataView"),
    ),
)
def test_routes(routes, path, expected):
    match, view = resolve_routes(routes, path)
    if expected is None:
        assert match is None
    else:
        assert view.view_class.__name__ == expected
