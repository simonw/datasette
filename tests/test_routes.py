from datasette.app import Datasette, Database
from datasette.utils import resolve_routes
import pytest
import pytest_asyncio


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


@pytest.fixture
def ds_with_routed_download(tmp_path):
    import sqlite3

    def make(metadata):
        path = str(tmp_path / "secret.db")
        conn = sqlite3.connect(path)
        conn.execute("create table if not exists t (id integer primary key)")
        conn.close()
        ds = Datasette(metadata=metadata)
        ds.remove_database("_memory")
        ds.add_database(
            Database(ds, path=path, is_mutable=False),
            name="original-name",
            route="custom-route-name",
        )
        return ds

    return make


@pytest.mark.asyncio
async def test_db_with_route_download_respects_allow_block(ds_with_routed_download):
    # Permissions are configured against the database name, not its route
    ds = ds_with_routed_download(
        {"databases": {"original-name": {"allow": {"id": "root"}}}}
    )
    await ds.invoke_startup()
    assert (await ds.client.get("/custom-route-name")).status_code == 403
    assert (await ds.client.get("/custom-route-name.db")).status_code == 403
    root = await ds.client.get(
        "/custom-route-name.db",
        cookies={"ds_actor": ds.sign({"a": {"id": "root"}}, "actor")},
    )
    assert root.status_code == 200
    assert root.content[:15] == b"SQLite format 3"


@pytest.mark.asyncio
async def test_db_with_route_download_permission_uses_name(ds_with_routed_download):
    # Plugins are asked about view-database-download for the database name
    from datasette import hookimpl
    from datasette.plugins import pm

    seen = []

    class DenyDownloadPlugin:
        __name__ = "DenyDownloadPlugin"

        @hookimpl
        def permission_allowed(self, action, resource):
            if action == "view-database-download":
                seen.append(resource)
                return resource != "original-name"

    pm.register(DenyDownloadPlugin(), name="deny-download")
    try:
        ds = ds_with_routed_download({})
        await ds.invoke_startup()
        assert (await ds.client.get("/custom-route-name.db")).status_code == 403
        assert seen == ["original-name"]
    finally:
        pm.unregister(name="deny-download")


@pytest.mark.asyncio
async def test_db_with_route_download_allowed_by_default(ds_with_routed_download):
    ds = ds_with_routed_download({})
    await ds.invoke_startup()
    assert (await ds.client.get("/custom-route-name.db")).status_code == 200
    # The name is not a valid URL for a database that has a route
    assert (await ds.client.get("/original-name.db")).status_code == 404


@pytest.mark.asyncio
async def test_unknown_database_download_checks_permissions_first(
    ds_with_routed_download,
):
    # An instance that denies everything says 403, not 404, for any .db URL
    ds = ds_with_routed_download({"allow": {"id": "root"}})
    await ds.invoke_startup()
    assert (await ds.client.get("/no-such-database.db")).status_code == 403
