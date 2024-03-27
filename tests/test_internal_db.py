import pytest


# ensure refresh_schemas() gets called before interacting with internal_db
async def ensure_internal(ds_client):
    await ds_client.get("/fixtures.json?sql=select+1")
    return ds_client.ds.get_internal_database()


@pytest.mark.asyncio
async def test_internal_databases(ds_client):
    internal_db = await ensure_internal(ds_client)
    databases = await internal_db.execute("select * from catalog_databases")
    assert len(databases) == 1
    assert databases.rows[0]["database_name"] == "fixtures"


@pytest.mark.asyncio
async def test_internal_tables(ds_client):
    internal_db = await ensure_internal(ds_client)
    tables = await internal_db.execute("select * from catalog_tables")
    assert len(tables) > 5
    table = tables.rows[0]
    assert set(table.keys()) == {"rootpage", "table_name", "database_name", "sql"}


@pytest.mark.asyncio
async def test_internal_indexes(ds_client):
    internal_db = await ensure_internal(ds_client)
    indexes = await internal_db.execute("select * from catalog_indexes")
    assert len(indexes) > 5
    index = indexes.rows[0]
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
    internal_db = await ensure_internal(ds_client)
    foreign_keys = await internal_db.execute("select * from catalog_foreign_keys")
    assert len(foreign_keys) > 5
    foreign_key = foreign_keys.rows[0]
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
