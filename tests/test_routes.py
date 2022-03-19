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
        ("/", "IndexView", {"as_format": ""}),
        ("/foo", "DatabaseView", {"as_format": None, "db_name": "foo"}),
        ("/foo.csv", "DatabaseView", {"as_format": ".csv", "db_name": "foo"}),
        ("/foo.json", "DatabaseView", {"as_format": ".json", "db_name": "foo"}),
        ("/foo.humbug", "DatabaseView", {"as_format": None, "db_name": "foo.humbug"}),
        ("/foo/humbug", "TableView", {"db_name": "foo", "table": "humbug"}),
        ("/foo/humbug.json", "TableView", {"db_name": "foo", "table": "humbug"}),
        ("/foo/humbug.blah", "TableView", {"db_name": "foo", "table": "humbug"}),
        (
            "/foo/humbug/1",
            "RowView",
            {"as_format": None, "db_name": "foo", "pk_path": "1", "table": "humbug"},
        ),
        (
            "/foo/humbug/1.json",
            "RowView",
            {"as_format": ".json", "db_name": "foo", "pk_path": "1", "table": "humbug"},
        ),
        ("/-/metadata.json", "JsonDataView", {"as_format": ".json"}),
        ("/-/metadata", "JsonDataView", {"as_format": ""}),
    ),
)
def test_routes(routes, path, expected_class, expected_matches):
    match, view = resolve_routes(routes, path)
    if expected_class is None:
        assert match is None
    else:
        assert view.view_class.__name__ == expected_class
        assert match.groupdict() == expected_matches
