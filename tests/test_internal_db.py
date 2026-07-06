import pytest

from datasette.utils import escape_sqlite


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
        table_names = [
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        ]

        def columns_for_table(table_name):
            return {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info({})".format(escape_sqlite(table_name))
                ).fetchall()
            }

        def primary_keys_for_table(table_name):
            return [
                name
                for _, name in sorted(
                    (row[5], row[1])
                    for row in conn.execute(
                        "PRAGMA table_info({})".format(escape_sqlite(table_name))
                    ).fetchall()
                    if row[5]
                )
            ]

        columns_by_table = {
            table_name: columns_for_table(table_name) for table_name in table_names
        }

        for table_name in table_names:
            foreign_key_rows = conn.execute(
                "PRAGMA foreign_key_list({})".format(escape_sqlite(table_name))
            ).fetchall()
            foreign_keys_by_id = {}
            for foreign_key in foreign_key_rows:
                foreign_keys_by_id.setdefault(foreign_key[0], []).append(foreign_key)

            for foreign_key_rows in foreign_keys_by_id.values():
                foreign_key_rows.sort(key=lambda row: row[1])
                other_table = foreign_key_rows[0][2]
                other_columns = [row[4] for row in foreign_key_rows]
                message = 'Column "{}.{}" references other table "{}" which does not exist'.format(
                    table_name, foreign_key_rows[0][3], other_table
                )
                assert other_table in table_names, message + " (bad table)"
                if all(other_column is None for other_column in other_columns):
                    other_columns = primary_keys_for_table(other_table)
                length_message = 'Foreign key from "{}" to "{}" has {} columns but references {} columns'.format(
                    table_name,
                    other_table,
                    len(foreign_key_rows),
                    len(other_columns),
                )
                assert len(other_columns) == len(foreign_key_rows), length_message

                for foreign_key, other_column in zip(foreign_key_rows, other_columns):
                    column = foreign_key[3]
                    message = 'Column "{}.{}" references other column "{}.{}" which does not exist'.format(
                        table_name, column, other_table, other_column
                    )
                    assert other_column in columns_by_table[other_table], (
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


@pytest.mark.asyncio
async def test_stale_catalog_child_entries_removed_for_missing_database(tmp_path):
    from datasette.app import Datasette

    import sqlite3

    internal_db_path = str(tmp_path / "internal.db")
    alpha_db_path = str(tmp_path / "alpha.db")
    bravo_db_path = str(tmp_path / "bravo.db")

    for db_path, table_name in (
        (alpha_db_path, "alpha_table"),
        (bravo_db_path, "bravo_table"),
        (bravo_db_path, "bravo_table_2"),
    ):
        conn = sqlite3.connect(db_path)
        conn.execute(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)")
        conn.close()

    ds1 = Datasette(files=[alpha_db_path, bravo_db_path], internal=internal_db_path)
    await ds1.invoke_startup()

    catalog_tables = await ds1.get_internal_database().execute("""
        SELECT database_name, table_name
        FROM catalog_tables
        ORDER BY database_name, table_name
        """)
    assert [tuple(row) for row in catalog_tables.rows] == [
        ("alpha", "alpha_table"),
        ("bravo", "bravo_table"),
        ("bravo", "bravo_table_2"),
    ]

    ds1.close()

    ds2 = Datasette(files=[alpha_db_path], internal=internal_db_path)
    await ds2.invoke_startup()

    catalog_tables = await ds2.get_internal_database().execute("""
        SELECT database_name, table_name
        FROM catalog_tables
        ORDER BY database_name, table_name
        """)
    assert [tuple(row) for row in catalog_tables.rows] == [("alpha", "alpha_table")]

    ds2.close()


@pytest.mark.asyncio
async def test_orphan_stale_catalog_child_entries_removed(tmp_path):
    from datasette.app import Datasette

    import sqlite3

    internal_db_path = str(tmp_path / "internal.db")
    alpha_db_path = str(tmp_path / "alpha.db")

    conn = sqlite3.connect(alpha_db_path)
    conn.execute("CREATE TABLE alpha_table (id INTEGER PRIMARY KEY)")
    conn.close()

    ds1 = Datasette(files=[alpha_db_path], internal=internal_db_path)
    await ds1.invoke_startup()
    ds1.close()

    # Simulate the state left behind by old cleanup code: the parent database
    # row was deleted, but child catalog rows survived because foreign key
    # enforcement is not enabled for these internal catalog writes.
    conn = sqlite3.connect(internal_db_path)
    conn.execute("DELETE FROM catalog_databases WHERE database_name = 'fixtures'")
    conn.execute("""
        INSERT INTO catalog_tables (database_name, table_name, rootpage, sql)
        VALUES ('fixtures', 'stale_table', 1, 'CREATE TABLE stale_table (id INTEGER)')
    """)
    conn.commit()
    conn.close()

    ds2 = Datasette(files=[alpha_db_path], internal=internal_db_path)
    await ds2.invoke_startup()

    catalog_tables = await ds2.get_internal_database().execute("""
        SELECT database_name, table_name
        FROM catalog_tables
        ORDER BY database_name, table_name
        """)
    assert [tuple(row) for row in catalog_tables.rows] == [("alpha", "alpha_table")]

    response = await ds2.client.get("/-/jump.json")
    assert response.status_code == 200

    ds2.close()
