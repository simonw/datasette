import pytest
import sqlite_utils


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
async def test_internal_views(ds_client):
    internal_db = await ensure_internal(ds_client)
    views = await internal_db.execute("select * from catalog_views")
    assert len(views) >= 4
    view = views.rows[0]
    assert set(view.keys()) == {"rootpage", "view_name", "database_name", "sql"}


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


@pytest.mark.asyncio
async def test_internal_foreign_key_references(ds_client):
    internal_db = await ensure_internal(ds_client)

    def inner(conn):
        db = sqlite_utils.Database(conn)
        table_names = db.table_names()
        for table in db.tables:
            for fk in table.foreign_keys:
                other_table = fk.other_table
                other_column = fk.other_column
                message = 'Column "{}.{}" references other column "{}.{}" which does not exist'.format(
                    table.name, fk.column, other_table, other_column
                )
                assert other_table in table_names, message + " (bad table)"
                assert other_column in db[other_table].columns_dict, (
                    message + " (bad column)"
                )

    await internal_db.execute_fn(inner)


@pytest.mark.asyncio
async def test_stale_catalog_entry_database_fix(tmp_path):
    """
    Test for https://github.com/simonw/datasette/issues/2605

    When the internal database persists across restarts and has entries in
    catalog_databases for databases that no longer exist, accessing the
    index page should not cause a 500 error (KeyError).
    """
    from datasette.app import Datasette

    internal_db_path = str(tmp_path / "internal.db")
    data_db_path = str(tmp_path / "data.db")

    # Create a data database file
    import sqlite3

    conn = sqlite3.connect(data_db_path)
    conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY)")
    conn.close()

    # First Datasette instance: with the data database and persistent internal db
    ds1 = Datasette(files=[data_db_path], internal=internal_db_path)
    await ds1.invoke_startup()

    # Access the index page to populate the internal catalog
    response = await ds1.client.get("/")
    assert "data" in ds1.databases
    assert response.status_code == 200

    # Second Datasette instance: reusing internal.db but WITHOUT the data database
    # This simulates restarting Datasette after removing a database
    ds2 = Datasette(internal=internal_db_path)
    await ds2.invoke_startup()

    # The database is not in ds2.databases
    assert "data" not in ds2.databases

    # Accessing the index page should NOT cause a 500 error
    # This is the bug: it currently raises KeyError when trying to
    # access ds.databases["data"] for the stale catalog entry
    response = await ds2.client.get("/")
    assert response.status_code == 200, (
        f"Index page should return 200, not {response.status_code}. "
        "This fails due to stale catalog entries causing KeyError."
    )
