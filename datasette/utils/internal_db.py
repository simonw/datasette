import textwrap
from datasette.utils import table_column_details


async def init_internal_db(db):
    create_tables_sql = textwrap.dedent(
        """
    CREATE TABLE IF NOT EXISTS catalog_databases (
        database_name TEXT PRIMARY KEY,
        path TEXT,
        is_memory INTEGER,
        schema_version INTEGER
    );
    CREATE TABLE IF NOT EXISTS catalog_tables (
        database_name TEXT,
        table_name TEXT,
        rootpage INTEGER,
        sql TEXT,
        PRIMARY KEY (database_name, table_name),
        FOREIGN KEY (database_name) REFERENCES databases(database_name)
    );
    CREATE TABLE IF NOT EXISTS catalog_columns (
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
    );
    CREATE TABLE IF NOT EXISTS catalog_indexes (
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
    );
    CREATE TABLE IF NOT EXISTS catalog_foreign_keys (
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
    );
    """
    ).strip()
    await db.execute_write_script(create_tables_sql)


async def populate_schema_tables(internal_db, db):
    database_name = db.name

    def delete_everything(conn):
        with conn:
            conn.execute(
                "DELETE FROM catalog_tables WHERE database_name = ?", [database_name]
            )
            conn.execute(
                "DELETE FROM catalog_columns WHERE database_name = ?", [database_name]
            )
            conn.execute(
                "DELETE FROM catalog_foreign_keys WHERE database_name = ?",
                [database_name],
            )
            conn.execute(
                "DELETE FROM catalog_indexes WHERE database_name = ?", [database_name]
            )

    await internal_db.execute_write_fn(delete_everything)

    tables = (await db.execute("select * from sqlite_master WHERE type = 'table'")).rows

    def collect_info(conn):
        tables_to_insert = []
        columns_to_insert = []
        foreign_keys_to_insert = []
        indexes_to_insert = []

        for table in tables:
            table_name = table["name"]
            tables_to_insert.append(
                (database_name, table_name, table["rootpage"], table["sql"])
            )
            columns = table_column_details(conn, table_name)
            columns_to_insert.extend(
                {
                    **{"database_name": database_name, "table_name": table_name},
                    **column._asdict(),
                }
                for column in columns
            )
            foreign_keys = conn.execute(
                f"PRAGMA foreign_key_list([{table_name}])"
            ).fetchall()
            foreign_keys_to_insert.extend(
                {
                    **{"database_name": database_name, "table_name": table_name},
                    **dict(foreign_key),
                }
                for foreign_key in foreign_keys
            )
            indexes = conn.execute(f"PRAGMA index_list([{table_name}])").fetchall()
            indexes_to_insert.extend(
                {
                    **{"database_name": database_name, "table_name": table_name},
                    **dict(index),
                }
                for index in indexes
            )
        return (
            tables_to_insert,
            columns_to_insert,
            foreign_keys_to_insert,
            indexes_to_insert,
        )

    (
        tables_to_insert,
        columns_to_insert,
        foreign_keys_to_insert,
        indexes_to_insert,
    ) = await db.execute_fn(collect_info)

    await internal_db.execute_write_many(
        """
        INSERT INTO catalog_tables (database_name, table_name, rootpage, sql)
        values (?, ?, ?, ?)
    """,
        tables_to_insert,
    )
    await internal_db.execute_write_many(
        """
        INSERT INTO catalog_columns (
            database_name, table_name, cid, name, type, "notnull", default_value, is_pk, hidden
        ) VALUES (
            :database_name, :table_name, :cid, :name, :type, :notnull, :default_value, :is_pk, :hidden
        )
    """,
        columns_to_insert,
    )
    await internal_db.execute_write_many(
        """
        INSERT INTO catalog_foreign_keys (
            database_name, table_name, "id", seq, "table", "from", "to", on_update, on_delete, match
        ) VALUES (
            :database_name, :table_name, :id, :seq, :table, :from, :to, :on_update, :on_delete, :match
        )
    """,
        foreign_keys_to_insert,
    )
    await internal_db.execute_write_many(
        """
        INSERT INTO catalog_indexes (
            database_name, table_name, seq, name, "unique", origin, partial
        ) VALUES (
            :database_name, :table_name, :seq, :name, :unique, :origin, :partial
        )
    """,
        indexes_to_insert,
    )
