import textwrap


async def init_internal_db(db):
    await db.execute_write(
        textwrap.dedent(
            """
    CREATE TABLE databases (
        database_name TEXT PRIMARY KEY,
        path TEXT,
        is_memory INTEGER,
        schema_version INTEGER
    )
    """
        ),
        block=True,
    )
    await db.execute_write(
        textwrap.dedent(
            """
    CREATE TABLE tables (
        database_name TEXT,
        table_name TEXT,
        rootpage INTEGER,
        sql TEXT,
        PRIMARY KEY (database_name, table_name),
        FOREIGN KEY (database_name) REFERENCES databases(database_name)
    )
    """
        ),
        block=True,
    )
    await db.execute_write(
        textwrap.dedent(
            """
    CREATE TABLE columns (
        database_name TEXT,
        table_name TEXT,
        cid INTEGER,
        name TEXT,
        type TEXT,
        "notnull" INTEGER,
        default_value TEXT, -- renamed from dflt_value
        is_pk INTEGER, -- renamed from pk
        hidden INTEGER,
        PRIMARY KEY (database_name, table_name, name),
        FOREIGN KEY (database_name) REFERENCES databases(database_name),
        FOREIGN KEY (database_name, table_name) REFERENCES tables(database_name, table_name)
    )
    """
        ),
        block=True,
    )
    await db.execute_write(
        textwrap.dedent(
            """
    CREATE TABLE indexes (
        database_name TEXT,
        table_name TEXT,
        seq INTEGER,
        name TEXT,
        "unique" INTEGER,
        origin TEXT,
        partial INTEGER,
        PRIMARY KEY (database_name, table_name, name),
        FOREIGN KEY (database_name) REFERENCES databases(database_name),
        FOREIGN KEY (database_name, table_name) REFERENCES tables(database_name, table_name)
    )
    """
        ),
        block=True,
    )
    await db.execute_write(
        textwrap.dedent(
            """
    CREATE TABLE foreign_keys (
        database_name TEXT,
        table_name TEXT,
        id INTEGER,
        seq INTEGER,
        "table" TEXT,
        "from" TEXT,
        "to" TEXT,
        on_update TEXT,
        on_delete TEXT,
        match TEXT,
        PRIMARY KEY (database_name, table_name, id, seq),
        FOREIGN KEY (database_name) REFERENCES databases(database_name),
        FOREIGN KEY (database_name, table_name) REFERENCES tables(database_name, table_name)
    )
    """
        ),
        block=True,
    )


async def populate_schema_tables(internal_db, db):
    database_name = db.name
    await internal_db.execute_write(
        "DELETE FROM tables WHERE database_name = ?", [database_name], block=True
    )
    tables = (await db.execute("select * from sqlite_master WHERE type = 'table'")).rows
    for table in tables:
        table_name = table["name"]
        await internal_db.execute_write(
            """
            INSERT INTO tables (database_name, table_name, rootpage, sql)
            values (?, ?, ?, ?)
        """,
            [database_name, table_name, table["rootpage"], table["sql"]],
            block=True,
        )
        # And the columns
        await internal_db.execute_write(
            "DELETE FROM columns WHERE database_name = ? and table_name = ?",
            [database_name, table_name],
            block=True,
        )
        columns = await db.table_column_details(table_name)
        for column in columns:
            params = {
                **{"database_name": database_name, "table_name": table_name},
                **column._asdict(),
            }
            await internal_db.execute_write(
                """
                INSERT INTO columns (
                    database_name, table_name, cid, name, type, "notnull", default_value, is_pk, hidden
                ) VALUES (
                    :database_name, :table_name, :cid, :name, :type, :notnull, :default_value, :is_pk, :hidden
                )
            """,
                params,
                block=True,
            )
        # And the foreign_keys
        await internal_db.execute_write(
            "DELETE FROM foreign_keys WHERE database_name = ? and table_name = ?",
            [database_name, table_name],
            block=True,
        )
        foreign_keys = (
            await db.execute(f"PRAGMA foreign_key_list([{table_name}])")
        ).rows
        for foreign_key in foreign_keys:
            params = {
                **{"database_name": database_name, "table_name": table_name},
                **dict(foreign_key),
            }
            await internal_db.execute_write(
                """
                INSERT INTO foreign_keys (
                    database_name, table_name, "id", seq, "table", "from", "to", on_update, on_delete, match
                ) VALUES (
                    :database_name, :table_name, :id, :seq, :table, :from, :to, :on_update, :on_delete, :match
                )
            """,
                params,
                block=True,
            )
        # And the indexes
        await internal_db.execute_write(
            "DELETE FROM indexes WHERE database_name = ? and table_name = ?",
            [database_name, table_name],
            block=True,
        )
        indexes = (await db.execute(f"PRAGMA index_list([{table_name}])")).rows
        for index in indexes:
            params = {
                **{"database_name": database_name, "table_name": table_name},
                **dict(index),
            }
            await internal_db.execute_write(
                """
                INSERT INTO indexes (
                    database_name, table_name, seq, name, "unique", origin, partial
                ) VALUES (
                    :database_name, :table_name, :seq, :name, :unique, :origin, :partial
                )
            """,
                params,
                block=True,
            )
