from datasette.app import Datasette, Database
from datasette.utils import resolve_routes
import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def routes():
    ds = Datasette()
    return ds._routes()


@pytest.mark.parametrize(
    "path,expected_name,expected_matches",
    (
        ("/", "IndexView", {"format": None}),
        ("/foo", "DatabaseView", {"format": None, "database": "foo"}),
        ("/foo.csv", "DatabaseView", {"format": "csv", "database": "foo"}),
        ("/foo.json", "DatabaseView", {"format": "json", "database": "foo"}),
        ("/foo.humbug", "DatabaseView", {"format": "humbug", "database": "foo"}),
        (
            "/foo/humbug",
            "table_view",
            {"database": "foo", "table": "humbug", "format": None},
        ),
        (
            "/foo/humbug.json",
            "table_view",
            {"database": "foo", "table": "humbug", "format": "json"},
        ),
        (
            "/foo/humbug.blah",
            "table_view",
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
def test_routes(routes, path, expected_name, expected_matches):
    match, view = resolve_routes(routes, path)
    if expected_name is None:
        assert match is None
    else:
        assert (
            view.__name__ == expected_name or view.view_class.__name__ == expected_name
        )
        assert match.groupdict() == expected_matches


@pytest_asyncio.fixture
async def ds_with_route():
    ds = Datasette()
    await ds.invoke_startup()
    ds.remove_database("_memory")
    db = Database(ds, is_memory=True, memory_name="route-name-db")
    ds.add_database(db, name="original-name", route="custom-route-name")
    await db.execute_write_script(
        """
        create table if not exists t (id integer primary key);
        insert or replace into t (id) values (1);
    """
    )
    return ds


@pytest.mark.asyncio
async def test_db_with_route_databases(ds_with_route):
    response = await ds_with_route.client.get("/-/databases.json")
    assert response.json()[0] == {
        "name": "original-name",
        "route": "custom-route-name",
        "path": None,
        "size": 0,
        "is_mutable": True,
        "is_memory": True,
        "hash": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_status",
    (
        ("/", 200),
        ("/original-name", 404),
        ("/original-name/t", 404),
        ("/original-name/t/1", 404),
        ("/custom-route-name", 200),
        ("/custom-route-name?sql=select+id+from+t", 200),
        ("/custom-route-name/t", 200),
        ("/custom-route-name/t/1", 200),
    ),
)
async def test_db_with_route_that_does_not_match_name(
    ds_with_route, path, expected_status
):
    response = await ds_with_route.client.get(path)
    assert response.status_code == expected_status
    # There should be links to custom-route-name but none to original-name
    if response.status_code == 200:
        assert "/custom-route-name" in response.text
        assert "/original-name" not in response.text
