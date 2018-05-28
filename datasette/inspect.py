import hashlib
import sqlite3

from .utils import detect_spatialite, detect_fts, escape_sqlite, get_all_foreign_keys


HASH_BLOCK_SIZE = 1024 * 1024


def inspect_hash(path):
    " Calculate the hash of a database, efficiently. "
    m = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            data = fp.read(HASH_BLOCK_SIZE)
            if not data:
                break
            m.update(data)

    return m.hexdigest()


def inspect_views(conn):
    " List views in a database. "
    return [v[0] for v in conn.execute('select name from sqlite_master where type = "view"')]


def detect_label_column(column_names):
    """ Detect the label column - which we display as the label for a joined column.

        If a table has two columns, one of which is ID, then label_column is the other one.
    """
    if (column_names and len(column_names) == 2 and "id" in column_names):
        return [c for c in column_names if c != "id"][0]

    return None


def detect_primary_keys(conn, table):
    " Figure out primary keys for a table. "
    table_info_rows = [
        row
        for row in conn.execute(
            'PRAGMA table_info("{}")'.format(table)
        ).fetchall()
        if row[-1]
    ]
    table_info_rows.sort(key=lambda row: row[-1])
    return [str(r[1]) for r in table_info_rows]


def inspect_tables(conn, database_metadata):
    " List tables and their row counts, excluding uninteresting tables. "
    tables = {}
    table_names = [
        r["name"]
        for r in conn.execute(
            'select * from sqlite_master where type="table"'
        )
    ]

    for table in table_names:
        table_metadata = database_metadata.get("tables", {}).get(
            table, {}
        )

        try:
            count = conn.execute(
                "select count(*) from {}".format(escape_sqlite(table))
            ).fetchone()[0]
        except sqlite3.OperationalError:
            # This can happen when running against a FTS virtual table
            # e.g. "select count(*) from some_fts;"
            count = 0

        column_names = [
            r[1]
            for r in conn.execute(
                "PRAGMA table_info({});".format(escape_sqlite(table))
            ).fetchall()
        ]

        tables[table] = {
            "name": table,
            "columns": column_names,
            "primary_keys": detect_primary_keys(conn, table),
            "count": count,
            "label_column": detect_label_column(column_names),
            "hidden": table_metadata.get("hidden") or False,
            "fts_table": detect_fts(conn, table),
        }

    foreign_keys = get_all_foreign_keys(conn)
    for table, info in foreign_keys.items():
        tables[table]["foreign_keys"] = info

    # Mark tables 'hidden' if they relate to FTS virtual tables
    hidden_tables = [
        r["name"]
        for r in conn.execute(
            """
                select name from sqlite_master
                where rootpage = 0
                and sql like '%VIRTUAL TABLE%USING FTS%'
            """
        )
    ]

    if detect_spatialite(conn):
        # Also hide Spatialite internal tables
        hidden_tables += [
            "ElementaryGeometries",
            "SpatialIndex",
            "geometry_columns",
            "spatial_ref_sys",
            "spatialite_history",
            "sql_statements_log",
            "sqlite_sequence",
            "views_geometry_columns",
            "virts_geometry_columns",
        ] + [
            r["name"]
            for r in conn.execute(
                """
                    select name from sqlite_master
                    where name like "idx_%"
                    and type = "table"
                """
            )
        ]

    for t in tables.keys():
        for hidden_table in hidden_tables:
            if t == hidden_table or t.startswith(hidden_table):
                tables[t]["hidden"] = True
                continue

    return tables
