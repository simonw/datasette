import pytest
from unittest.mock import patch
from datasette.app import Datasette
from datasette.database import Database


@pytest.mark.asyncio
async def test_internal_only_available_to_root(ds_client):
    cookie = ds_client.actor_cookie({"id": "root"})
    assert (await ds_client.get("/_internal")).status_code == 403
    assert (
        await ds_client.get("/_internal", cookies={"ds_actor": cookie})
    ).status_code == 200


@pytest.mark.asyncio
async def test_internal_databases(ds_client):
    cookie = ds_client.actor_cookie({"id": "root"})
    databases = (
        await ds_client.get(
            "/_internal/databases.json?_shape=array", cookies={"ds_actor": cookie}
        )
    ).json()
    assert len(databases) == 2
    internal, fixtures = databases
    assert internal["database_name"] == "_internal"
    assert internal["is_memory"] == 1
    assert internal["path"] is None
    assert isinstance(internal["schema_version"], int)
    assert fixtures["database_name"] == "fixtures"


@pytest.mark.asyncio
async def test_internal_tables(ds_client):
    cookie = ds_client.actor_cookie({"id": "root"})
    tables = (
        await ds_client.get(
            "/_internal/tables.json?_shape=array", cookies={"ds_actor": cookie}
        )
    ).json()
    assert len(tables) > 5
    table = tables[0]
    assert set(table.keys()) == {"rootpage", "table_name", "database_name", "sql"}


@pytest.mark.asyncio
async def test_internal_indexes(ds_client):
    cookie = ds_client.actor_cookie({"id": "root"})
    indexes = (
        await ds_client.get(
            "/_internal/indexes.json?_shape=array", cookies={"ds_actor": cookie}
        )
    ).json()
    assert len(indexes) > 5
    index = indexes[0]
    assert set(index.keys()) == {
        "partial",
        "name",
        "table_name",
        "unique",
        "seq",
        "database_name",
        "origin",
    }


@pytest.mark.asyncio
async def test_internal_foreign_keys(ds_client):
    cookie = ds_client.actor_cookie({"id": "root"})
    foreign_keys = (
        await ds_client.get(
            "/_internal/foreign_keys.json?_shape=array", cookies={"ds_actor": cookie}
        )
    ).json()
    assert len(foreign_keys) > 5
    foreign_key = foreign_keys[0]
    assert set(foreign_key.keys()) == {
        "table",
        "seq",
        "on_update",
        "on_delete",
        "to",
        "id",
        "match",
        "database_name",
        "table_name",
        "from",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_version_returns_none", (True, False))
async def test_detects_schema_changes(schema_version_returns_none):
    ds = Datasette()
    db_name = "test_detects_schema_changes_{}".format(schema_version_returns_none)
    db = ds.add_memory_database(db_name)
    # Test if Datasette correctly detects schema changes, whether or not
    # the schema_version method is working.
    # https://github.com/simonw/datasette/issues/2058

    _internal = ds.get_database("_internal")

    async def get_tables():
        return [
            dict(r)
            for r in await _internal.execute(
                "select table_name from tables where database_name = ?", [db_name]
            )
        ]

    async def test_it():
        await ds.refresh_schemas()
        initial_hash = await db.schema_hash()
        # _internal should list zero tables
        tables = await get_tables()
        assert tables == []
        # Create a new table
        await db.execute_write("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        await ds.refresh_schemas()
        assert await db.schema_hash() != initial_hash
        # _internal should list one table
        tables = await get_tables()
        assert tables == [
            {"table_name": "test"},
        ]

    async def schema_version_none(self):
        return None

    if schema_version_returns_none:
        with patch(
            "datasette.database.Database.schema_version", new=schema_version_none
        ):
            await test_it()
    else:
        await test_it()
