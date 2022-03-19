from datasette.app import Datasette
from datasette.utils import resolve_routes
import pytest


@pytest.fixture(scope="session")
def routes():
    ds = Datasette()
    return ds._routes()


@pytest.mark.parametrize(
    "path,expected_class,expected_matches",
    (
        ("/", "IndexView", {"format": None}),
        ("/foo", "DatabaseView", {"format": None, "database": "foo"}),
        ("/foo.csv", "DatabaseView", {"format": "csv", "database": "foo"}),
        ("/foo.json", "DatabaseView", {"format": "json", "database": "foo"}),
        ("/foo.humbug", "DatabaseView", {"format": "humbug", "database": "foo"}),
        (
            "/foo/humbug",
            "TableView",
            {"database": "foo", "table": "humbug", "format": None},
        ),
        (
            "/foo/humbug.json",
            "TableView",
            {"database": "foo", "table": "humbug", "format": "json"},
        ),
        (
            "/foo/humbug.blah",
            "TableView",
            {"database": "foo", "table": "humbug", "format": "blah"},
        ),
        (
            "/foo/humbug/1",
            "RowView",
            {"format": None, "database": "foo", "pks": "1", "table": "humbug"},
        ),
        (
            "/foo/humbug/1.json",
            "RowView",
            {"format": "json", "database": "foo", "pks": "1", "table": "humbug"},
        ),
        ("/-/metadata.json", "JsonDataView", {"format": "json"}),
        ("/-/metadata", "JsonDataView", {"format": None}),
    ),
)
def test_routes(routes, path, expected_class, expected_matches):
    match, view = resolve_routes(routes, path)
    if expected_class is None:
        assert match is None
    else:
        assert view.view_class.__name__ == expected_class
        assert match.groupdict() == expected_matches
