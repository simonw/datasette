import pathlib
import sqlite3

import pytest
import pytest_asyncio

from datasette.app import Database, Datasette
from datasette.utils import resolve_routes


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
    await db.execute_write_script("""
        create table if not exists t (id integer primary key);
        insert or replace into t (id) values (1);
    """)
    return ds


async def test_db_with_route_databases(ds_with_route):
    response = await ds_with_route.client.get("/-/databases.json")
    assert response.json()["databases"][0] == {
        "name": "original-name",
        "route": "custom-route-name",
        "path": None,
        "size": 0,
        "is_mutable": True,
        "is_memory": True,
        "hash": None,
    }


@pytest.mark.parametrize(
    "path,expected_status",
    (
        ("/", 200),
        ("/original-name", 404),
        ("/original-name/t", 404),
        ("/original-name/t/1", 404),
        ("/custom-route-name", 200),
        ("/custom-route-name/-/query?sql=select+id+from+t", 200),
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


async def test_db_with_route_download_checks_permissions_against_name(tmp_path):
    # Permission rules refer to a database by its name, so the download at
    # /<route>.db needs to check them against the name, not the route
    path = str(tmp_path / "private.db")
    conn = sqlite3.connect(path)
    conn.execute("create table t (id integer primary key)")
    conn.close()
    ds = Datasette(
        config={"databases": {"original-name": {"allow": {"id": "root"}}}},
    )
    await ds.invoke_startup()
    ds.add_database(
        Database(ds, path=path, is_mutable=False),
        name="original-name",
        route="custom-route-name",
    )
    # Actors the allow block does not match cannot view the database page
    # or download the file
    for actor in (None, {"id": "not-root"}):
        for url in ("/custom-route-name", "/custom-route-name.db"):
            response = await ds.client.get(url, actor=actor)
            assert response.status_code == 403
    # The actor matched by the allow block can do both
    root = {"id": "root"}
    assert (await ds.client.get("/custom-route-name", actor=root)).status_code == 200
    response = await ds.client.get("/custom-route-name.db", actor=root)
    assert response.status_code == 200
    assert response.content == pathlib.Path(path).read_bytes()
    # The name is not a route, and neither is an unknown database
    for missing in ("/original-name.db", "/does-not-exist.db"):
        assert (await ds.client.get(missing, actor=root)).status_code == 404


async def test_db_download_checks_permission_before_404():
    # An actor denied for the whole instance gets a 403 for a database that
    # does not exist, not a 404
    ds = Datasette(config={"allow": {"id": "root"}})
    await ds.invoke_startup()
    assert (await ds.client.get("/does-not-exist.db")).status_code == 403
    response = await ds.client.get("/does-not-exist.db", actor={"id": "root"})
    assert response.status_code == 404
