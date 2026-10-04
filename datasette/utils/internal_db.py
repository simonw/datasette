import textwrap

from sqlite_utils import Database as SQLiteUtilsDatabase
from sqlite_utils import Migrations

from datasette.utils import escape_sqlite, sqlite3, table_column_details
from datasette.utils.sqlite import supports_table_xinfo

INTERNAL_DB_SCHEMA_TABLES = {
    "catalog_databases",
    "catalog_tables",
    "catalog_views",
    "catalog_columns",
    "catalog_indexes",
    "catalog_foreign_keys",
    "metadata_instance",
    "metadata_databases",
    "metadata_resources",
    "metadata_columns",
    "column_types",
    "queries",
}

INTERNAL_DB_SCHEMA_INDEXES = {
    "queries_owner_idx",
}

INTERNAL_DB_SCHEMA_SQL = textwrap.dedent("""
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
        FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name)
    );
    CREATE TABLE IF NOT EXISTS catalog_views (
        database_name TEXT,
        view_name TEXT,
        rootpage INTEGER,
        sql TEXT,
        PRIMARY KEY (database_name, view_name),
        FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name)
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
        FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name),
        FOREIGN KEY (database_name, table_name) REFERENCES catalog_tables(database_name, table_name)
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
        FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name),
        FOREIGN KEY (database_name, table_name) REFERENCES catalog_tables(database_name, table_name)
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
        FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name),
        FOREIGN KEY (database_name, table_name) REFERENCES catalog_tables(database_name, table_name)
    );

    CREATE TABLE IF NOT EXISTS metadata_instance (
        key text,
        value text,
        unique(key)
    );

    CREATE TABLE IF NOT EXISTS metadata_databases (
        database_name text,
        key text,
        value text,
        unique(database_name, key)
    );

    CREATE TABLE IF NOT EXISTS metadata_resources (
        database_name text,
        resource_name text,
        key text,
        value text,
        unique(database_name, resource_name, key)
    );

    CREATE TABLE IF NOT EXISTS metadata_columns (
        database_name text,
        resource_name text,
        column_name text,
        key text,
        value text,
        unique(database_name, resource_name, column_name, key)
    );

    CREATE TABLE IF NOT EXISTS column_types (
        database_name TEXT NOT NULL,
        resource_name TEXT NOT NULL,
        column_name TEXT NOT NULL,
        column_type TEXT NOT NULL,
        config TEXT,
        PRIMARY KEY (database_name, resource_name, column_name)
    );

    CREATE TABLE IF NOT EXISTS queries (
        database_name TEXT NOT NULL,
        name TEXT NOT NULL,
        sql TEXT NOT NULL,
        title TEXT,
        description TEXT,
        description_html TEXT,
        options TEXT NOT NULL DEFAULT '{}',
        parameters TEXT NOT NULL DEFAULT '[]',
        is_write INTEGER NOT NULL DEFAULT 0 CHECK (is_write IN (0, 1)),
        is_private INTEGER NOT NULL DEFAULT 0 CHECK (is_private IN (0, 1)),
        is_trusted INTEGER NOT NULL DEFAULT 0 CHECK (is_trusted IN (0, 1)),
        source TEXT NOT NULL DEFAULT 'user',
        owner_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (database_name, name)
    );

    CREATE INDEX IF NOT EXISTS queries_owner_idx
        ON queries(owner_id);
    """).strip()


internal_migrations = Migrations("datasette_internal")


def _internal_schema_exists(db):
    table_names = set(db.table_names())
    if not INTERNAL_DB_SCHEMA_TABLES.issubset(table_names):
        return False
    index_names = {
        row[0]
        for row in db.execute("select name from sqlite_master where type = 'index'")
    }
    return INTERNAL_DB_SCHEMA_INDEXES.issubset(index_names)


@internal_migrations(name="0001_initial")
def initial_internal_schema(db):
    if _internal_schema_exists(db):
        return
    db.executescript(INTERNAL_DB_SCHEMA_SQL)


async def init_internal_db(db):
    def apply_migrations(conn):
        internal_migrations.apply(SQLiteUtilsDatabase(conn, execute_plugins=False))

    await db.execute_write_fn(apply_migrations, transaction=False)


# Children first, catalog_databases last, so deletes work with FK enforcement
CATALOG_TABLES = (
    "catalog_columns",
    "catalog_foreign_keys",
    "catalog_indexes",
    "catalog_views",
    "catalog_tables",
    "catalog_databases",
)


@internal_migrations(name="0002_catalog_fingerprint")
def catalog_fingerprint_column(db):
    # stat() fingerprint of the database file when the catalog rows were
    # written, so a restart with a persistent internal database can skip
    # unchanged files without opening them
    if "fingerprint" not in db["catalog_databases"].columns_dict:
        db.execute("ALTER TABLE catalog_databases ADD COLUMN fingerprint TEXT")


_COLUMNS_SQL = """
SELECT m.name, p.cid, p.name, p.type, p."notnull", p.dflt_value, p.pk, p.hidden
FROM sqlite_master m, pragma_table_xinfo(m.name) p WHERE m.type = 'table'
"""
_FOREIGN_KEYS_SQL = """
SELECT m.name, p.id, p.seq, p."table", p."from", p."to", p.on_update, p.on_delete, p."match"
FROM sqlite_master m, pragma_foreign_key_list(m.name) p WHERE m.type = 'table'
"""
_INDEXES_SQL = """
SELECT m.name, p.seq, p.name, p."unique", p.origin, p.partial
FROM sqlite_master m, pragma_index_list(m.name) p WHERE m.type = 'table'
"""


def collect_schema(conn, database_name):
    """Read everything the catalog needs from one connection.

    Run it inside a read transaction so it sees a single snapshot. Uses
    three joined pragma table-valued-function queries per database instead
    of three PRAGMA calls per table, falling back to the per-table loop if
    that fails (e.g. a virtual table whose module is not loaded).
    """
    if supports_table_xinfo():
        try:
            return _collect_schema_joined(conn, database_name)
        except sqlite3.DatabaseError:
            pass
    return _collect_schema_per_table(conn, database_name)


def _collect_schema_joined(conn, database_name):
    tables = conn.execute("select * from sqlite_master WHERE type = 'table'").fetchall()
    views = conn.execute("select * from sqlite_master WHERE type = 'view'").fetchall()
    columns = [
        {
            "database_name": database_name,
            "table_name": r[0],
            "cid": r[1],
            "name": r[2],
            "type": r[3],
            "notnull": r[4],
            "default_value": r[5],
            "is_pk": r[6],
            "hidden": r[7],
        }
        for r in conn.execute(_COLUMNS_SQL).fetchall()
    ]
    foreign_keys = [
        {
            "database_name": database_name,
            "table_name": r[0],
            "id": r[1],
            "seq": r[2],
            "table": r[3],
            "from": r[4],
            "to": r[5],
            "on_update": r[6],
            "on_delete": r[7],
            "match": r[8],
        }
        for r in conn.execute(_FOREIGN_KEYS_SQL).fetchall()
    ]
    indexes = [
        {
            "database_name": database_name,
            "table_name": r[0],
            "seq": r[1],
            "name": r[2],
            "unique": r[3],
            "origin": r[4],
            "partial": r[5],
        }
        for r in conn.execute(_INDEXES_SQL).fetchall()
    ]
    return {
        "tables": [(database_name, t["name"], t["rootpage"], t["sql"]) for t in tables],
        "views": [(database_name, v["name"], v["rootpage"], v["sql"]) for v in views],
        "columns": columns,
        "foreign_keys": foreign_keys,
        "indexes": indexes,
    }


def _collect_schema_per_table(conn, database_name):
    tables = conn.execute("select * from sqlite_master WHERE type = 'table'").fetchall()
    views = conn.execute("select * from sqlite_master WHERE type = 'view'").fetchall()
    tables_to_insert = []
    views_to_insert = []
    columns_to_insert = []
    foreign_keys_to_insert = []
    indexes_to_insert = []

    for view in views:
        views_to_insert.append(
            (database_name, view["name"], view["rootpage"], view["sql"])
        )

    for table in tables:
        table_name = table["name"]
        tables_to_insert.append(
            (database_name, table_name, table["rootpage"], table["sql"])
        )
        columns = table_column_details(conn, table_name)
        columns_to_insert.extend(
            {
                "database_name": database_name,
                "table_name": table_name,
                **column._asdict(),
            }
            for column in columns
        )
        foreign_keys = conn.execute(
            f"PRAGMA foreign_key_list({escape_sqlite(table_name)})"
        ).fetchall()
        foreign_keys_to_insert.extend(
            {
                "database_name": database_name,
                "table_name": table_name,
                **dict(foreign_key),
            }
            for foreign_key in foreign_keys
        )
        indexes = conn.execute(
            f"PRAGMA index_list({escape_sqlite(table_name)})"
        ).fetchall()
        indexes_to_insert.extend(
            {
                "database_name": database_name,
                "table_name": table_name,
                **dict(index),
            }
            for index in indexes
        )
    return {
        "tables": tables_to_insert,
        "views": views_to_insert,
        "columns": columns_to_insert,
        "foreign_keys": foreign_keys_to_insert,
        "indexes": indexes_to_insert,
    }


def write_catalog_entries(conn, entries):
    """Replace the catalog rows for each database in ``entries``.

    Each entry is (database_name, path, is_memory, schema_version,
    fingerprint_json, schema) where schema comes from collect_schema().
    Runs on the internal database write connection, in one transaction.
    """
    for database_name, path, is_memory, schema_version, fingerprint, schema in entries:
        # Delete child rows before their catalog_tables parents so this also
        # works if a prepare_connection plugin enables foreign key enforcement.
        for table in CATALOG_TABLES[:-1]:
            conn.execute(
                f"DELETE FROM {table} WHERE database_name = ?",
                [database_name],
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO catalog_databases (
                database_name, path, is_memory, schema_version, fingerprint
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [database_name, path, is_memory, schema_version, fingerprint],
        )
        conn.executemany(
            """
            INSERT INTO catalog_tables (database_name, table_name, rootpage, sql)
            values (?, ?, ?, ?)
            """,
            schema["tables"],
        )
        conn.executemany(
            """
            INSERT INTO catalog_views (database_name, view_name, rootpage, sql)
            values (?, ?, ?, ?)
            """,
            schema["views"],
        )
        conn.executemany(
            """
            INSERT INTO catalog_columns (
                database_name, table_name, cid, name, type, "notnull", default_value, is_pk, hidden
            ) VALUES (
                :database_name, :table_name, :cid, :name, :type, :notnull, :default_value, :is_pk, :hidden
            )
            """,
            schema["columns"],
        )
        conn.executemany(
            """
            INSERT INTO catalog_foreign_keys (
                database_name, table_name, "id", seq, "table", "from", "to", on_update, on_delete, match
            ) VALUES (
                :database_name, :table_name, :id, :seq, :table, :from, :to, :on_update, :on_delete, :match
            )
            """,
            schema["foreign_keys"],
        )
        conn.executemany(
            """
            INSERT INTO catalog_indexes (
                database_name, table_name, seq, name, "unique", origin, partial
            ) VALUES (
                :database_name, :table_name, :seq, :name, :unique, :origin, :partial
            )
            """,
            schema["indexes"],
        )


async def populate_schema_tables(internal_db, db, schema_version):
    """Rebuild the catalog rows for one database (kept for compatibility -
    the SchemaWatcher reads the schema on its own short-lived connection)."""
    database_name = db.name

    def _collect(conn):
        return collect_schema(conn, database_name)

    schema = await db.execute_fn(_collect)
    entry = (
        database_name,
        str(db.path) if db.path is not None else None,
        db.is_memory,
        schema_version,
        None,
        schema,
    )

    def replace_catalog(conn):
        write_catalog_entries(conn, [entry])

    await internal_db.execute_write_fn(replace_catalog)
