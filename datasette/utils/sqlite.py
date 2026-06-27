import re
from typing import Literal

using_pysqlite3 = False
try:
    import pysqlite3 as sqlite3

    using_pysqlite3 = True
except ImportError:
    import sqlite3

if hasattr(sqlite3, "enable_callback_tracebacks"):
    sqlite3.enable_callback_tracebacks(True)

_cached_sqlite_version = None
_cached_supports_returning = None
SQLiteTableType = Literal["table", "view", "virtual", "shadow"]
_VIRTUAL_TABLE_MODULE_RE = re.compile(
    r"\bCREATE\s+VIRTUAL\s+TABLE\b.*?\bUSING\s+([^\s(]+)",
    re.IGNORECASE | re.DOTALL,
)
_VIRTUAL_TABLE_SHADOW_SUFFIXES = {
    "fts3": ("_content", "_segdir", "_segments", "_stat", "_docsize"),
    "fts4": ("_content", "_segdir", "_segments", "_stat", "_docsize"),
    "fts5": ("_data", "_idx", "_docsize", "_content", "_config"),
    "rtree": ("_node", "_parent", "_rowid"),
    "rtree_i32": ("_node", "_parent", "_rowid"),
}


def sqlite_version():
    global _cached_sqlite_version
    if _cached_sqlite_version is None:
        _cached_sqlite_version = _sqlite_version()
    return _cached_sqlite_version


def _sqlite_version():
    conn = sqlite3.connect(":memory:")
    try:
        return tuple(
            map(
                int,
                conn.execute("select sqlite_version()").fetchone()[0].split("."),
            )
        )
    finally:
        conn.close()


def supports_table_xinfo():
    return sqlite_version() >= (3, 26, 0)


def supports_table_list():
    return sqlite_version() >= (3, 37, 0)


def supports_generated_columns():
    return sqlite_version() >= (3, 31, 0)


def supports_returning():
    global _cached_supports_returning
    if _cached_supports_returning is None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("create table t (id integer primary key)")
            conn.execute("insert into t default values returning id").fetchone()
            _cached_supports_returning = True
        except sqlite3.DatabaseError:
            _cached_supports_returning = False
        finally:
            conn.close()
    return _cached_supports_returning


def sqlite_table_type(
    conn,
    table: str,
    *,
    schema: str | None = "main",
) -> SQLiteTableType | None:
    if supports_table_list():
        try:
            query = "select type from pragma_table_list where name = ?"
            params: tuple[str, ...] = (table,)
            if schema is not None:
                query += " and schema = ?"
                params = (table, schema)
            row = conn.execute(query, params).fetchone()
            if row is not None and row[0] in {"table", "view", "virtual", "shadow"}:
                return row[0]
        except sqlite3.DatabaseError:
            pass
    return _sqlite_table_type_from_schema(conn, table, schema=schema)


def sqlite_hidden_table_names(conn, *, schema: str | None = "main") -> list[str]:
    schema_table = _sqlite_schema_table(schema)
    try:
        rows = conn.execute(
            "select name, sql from {} where type = 'table'".format(schema_table)
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    hidden_tables = []
    content_fts_tables = []
    for name, sql in rows:
        if (
            name in {"sqlite_stat1", "sqlite_stat2", "sqlite_stat3", "sqlite_stat4"}
            or name.startswith("_")
            or sqlite_table_type(conn, name, schema=schema) == "shadow"
        ):
            hidden_tables.append(name)
        elif _is_fts_content_virtual_table(sql):
            content_fts_tables.append(name)
    return sorted(hidden_tables) + content_fts_tables


def _sqlite_table_type_from_schema(
    conn,
    table: str,
    *,
    schema: str | None = "main",
) -> SQLiteTableType | None:
    schema_table = _sqlite_schema_table(schema)
    try:
        row = conn.execute(
            "select type, sql from {} where name = ?".format(schema_table),
            (table,),
        ).fetchone()
    except sqlite3.DatabaseError:
        return None
    if row is None:
        return None
    object_type, sql = row
    if object_type == "view":
        return "view"
    if object_type != "table":
        return None
    if _virtual_table_module(sql) is not None:
        return "virtual"
    if _is_known_shadow_table(conn, table, schema=schema):
        return "shadow"
    return "table"


def _is_known_shadow_table(
    conn,
    table: str,
    *,
    schema: str | None = "main",
) -> bool:
    schema_table = _sqlite_schema_table(schema)
    try:
        rows = conn.execute(
            "select name, sql from {} where type = 'table'".format(schema_table)
        ).fetchall()
    except sqlite3.DatabaseError:
        return False
    for virtual_table, sql in rows:
        module = _virtual_table_module(sql)
        if module is None:
            continue
        for suffix in _VIRTUAL_TABLE_SHADOW_SUFFIXES.get(module, ()):
            if table == virtual_table + suffix:
                return True
    return False


def _sqlite_schema_table(schema: str | None) -> str:
    if schema is None or schema == "main":
        return "sqlite_master"
    if schema == "temp":
        return "sqlite_temp_master"
    return "{}.sqlite_master".format(_quote_identifier(schema))


def _quote_identifier(value: str) -> str:
    return '"{}"'.format(value.replace('"', '""'))


def _virtual_table_module(sql: str | None) -> str | None:
    if not sql:
        return None
    match = _VIRTUAL_TABLE_MODULE_RE.search(sql)
    if match is None:
        return None
    return match.group(1).strip("\"'[]`").lower()


def _is_fts_content_virtual_table(sql: str | None) -> bool:
    return (
        _virtual_table_module(sql) in {"fts3", "fts4", "fts5"}
        and "content=" in sql.lower()
    )
