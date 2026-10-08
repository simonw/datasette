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
_SQLITE_IDENTIFIER_RE = (
    r"""(?:"(?:[^"]|"")*"|'(?:[^']|'')*'|`(?:[^`]|``)*`|\[[^\]]*\]|[^\s.()'"`\[\]]+)"""
)
_VIRTUAL_TABLE_MODULE_RE = re.compile(
    r"^\s*CREATE\s+VIRTUAL\s+TABLE\b\s*(?:IF\s+NOT\s+EXISTS\s+)?"
    + _SQLITE_IDENTIFIER_RE
    + r"(?:\s*\.\s*"
    + _SQLITE_IDENTIFIER_RE
    + r")?\s*\bUSING\b\s*("
    + _SQLITE_IDENTIFIER_RE
    + r")",
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
            # Use the "PRAGMA table_list" statement form rather than the
            # pragma_table_list(...) table-valued function. The
            # table-valued function is resolved like an ordinary relation
            # name, so an attacker-created table or view literally named
            # "pragma_table_list" can shadow it and spoof the reported
            # type (e.g. claiming a virtual table is an ordinary table).
            # The PRAGMA statement form is a distinct piece of SQL syntax
            # that always invokes SQLite's built-in pragma, so it cannot
            # be shadowed by a user-created relation.
            if schema is not None:
                query = f"PRAGMA {_quote_identifier(schema)}.table_list"
            else:
                query = "PRAGMA table_list"
            cursor = conn.execute(query)
            columns = [description[0] for description in cursor.description]
            for row in cursor.fetchall():
                record = dict(zip(columns, row))
                if record.get("name") != table:
                    continue
                if schema is not None and record.get("schema") != schema:
                    continue
                row_type = record.get("type")
                if row_type in {"table", "view", "virtual", "shadow"}:
                    return row_type
        except sqlite3.DatabaseError:
            pass
    return _sqlite_table_type_from_schema(conn, table, schema=schema)


def check_structured_write_table(conn, table: str, *, allow_missing=False):
    """Validate a row-write target on the connection that will perform the write."""
    # SQLite resolves identifiers case-insensitively. The create API must not
    # treat a differently cased existing name as a missing table.
    row = conn.execute(
        "select name from main.sqlite_master where name = ? collate nocase "
        "and type in ('table', 'view')",
        (table,),
    ).fetchone()
    if row is None and allow_missing:
        return
    if row is not None and sqlite_table_type(conn, row[0]) == "table":
        return
    # Virtual table modules can interpret row writes as administrative operations.
    # Their shadow tables are internal storage, not independently writable data.
    raise ValueError("Structured writes require an ordinary table")


def sqlite_hidden_table_names(conn, *, schema: str | None = "main") -> list[str]:
    schema_table = _sqlite_schema_table(schema)
    try:
        rows = conn.execute(
            f"select name, sql from {schema_table} where type = 'table'"
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


def sqlite_derived_table_dependencies(
    conn, *, schema: str | None = "main"
) -> dict[str, str]:
    """Return implementation table -> logical/content table dependencies.

    ``PRAGMA table_list`` safely identifies virtual and shadow tables, but
    does not report which virtual table owns a shadow table or which table is
    named by an FTS ``content=`` option. Derive those relationships from
    ``sqlite_master`` DDL and the documented shadow-table suffixes.

    Database errors propagate: failed discovery must not be mistaken for an
    empty dependency map and cached as permission to skip inheritance.
    """
    schema_table = _sqlite_schema_table(schema)
    rows = conn.execute(
        f"select name, sql from {schema_table} where type = 'table'"
    ).fetchall()

    table_names = {row[0] for row in rows}
    # SQLite identifiers fold ASCII letters only.
    identifier_case = str.maketrans(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
    )
    canonical_names = {name.translate(identifier_case): name for name in table_names}
    dependencies = {}
    for virtual_table, sql in rows:
        module = _virtual_table_module(sql)
        if module is None:
            continue

        # SQLite's documented shadow tables are implementation details of
        # their logical virtual table.
        for suffix in _VIRTUAL_TABLE_SHADOW_SUFFIXES.get(module, ()):
            shadow_table = virtual_table + suffix
            if shadow_table in table_names:
                dependencies[shadow_table] = virtual_table

        # An external-content FTS table can expose values fetched from its
        # content table, so it must also depend on that table's permission.
        if module in {"fts3", "fts4", "fts5"}:
            content_table = _fts_external_content_table(sql)
            if content_table:
                dependencies[virtual_table] = content_table

        if module in {"fts5vocab", "fts4aux"}:
            source = _fts_vocabulary_source(sql, module, schema or "main")
            source = (
                canonical_names.get(source.translate(identifier_case))
                if source
                else None
            )
            # An unresolved source is itself derived, so the one-hop policy denies it.
            dependencies[virtual_table] = source or virtual_table

    return dependencies


def _sqlite_table_type_from_schema(
    conn,
    table: str,
    *,
    schema: str | None = "main",
) -> SQLiteTableType | None:
    schema_table = _sqlite_schema_table(schema)
    try:
        row = conn.execute(
            f"select type, sql from {schema_table} where name = ?",
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
            f"select name, sql from {schema_table} where type = 'table'"
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
    return f"{_quote_identifier(schema)}.sqlite_master"


def _quote_identifier(value: str) -> str:
    return '"{}"'.format(value.replace('"', '""'))


def _virtual_table_module(sql: str | None) -> str | None:
    if not sql:
        return None
    match = _VIRTUAL_TABLE_MODULE_RE.search(_strip_sql_comments(sql))
    if match is None:
        return None
    return _unquote_sql_value(match.group(1)).lower()


def _fts_external_content_table(sql: str | None) -> str | None:
    """Extract the external ``content=`` table from an FTS declaration."""
    if not sql:
        return None
    sql = _strip_sql_comments(sql)
    match = _VIRTUAL_TABLE_MODULE_RE.search(sql)
    if match is None:
        return None
    open_paren = sql.find("(", match.end())
    if open_paren == -1:
        return None
    close_paren = sql.rfind(")")
    if close_paren <= open_paren:
        return None

    for argument in _split_sql_arguments(sql[open_paren + 1 : close_paren]):
        key, separator, value = argument.partition("=")
        if not separator or key.strip().lower() != "content":
            continue
        return _unquote_sql_value(value.strip())
    return None


def _fts_vocabulary_source(sql: str, module: str, schema: str) -> str | None:
    """Resolve a vocabulary source within the current SQLite schema.

    Cross-schema sources cannot be represented by the dependency map and
    are conservatively left unresolved.
    """
    sql = _strip_sql_comments(sql)
    match = _VIRTUAL_TABLE_MODULE_RE.search(sql)
    if match is None:
        return None
    start = sql.find("(", match.end())
    end = sql.rfind(")")
    if start < 0 or end <= start:
        return None
    arguments = [
        _unquote_sql_value(arg.strip())
        for arg in _split_sql_arguments(sql[start + 1 : end])
    ]
    expected = 2 if module == "fts5vocab" else 1
    if len(arguments) == expected:
        return arguments[0]
    if len(arguments) == expected + 1 and arguments[0].lower() == schema.lower():
        return arguments[1]
    return None


def _split_sql_arguments(arguments: str) -> list[str]:
    """Split comma-separated SQLite arguments without splitting quoted text."""
    parts = []
    start = 0
    quote = None
    closing_quote = None
    index = 0
    while index < len(arguments):
        char = arguments[index]
        if quote is None:
            if char in {"'", '"', "`", "["}:
                quote = char
                closing_quote = "]" if char == "[" else char
            elif char == ",":
                parts.append(arguments[start:index])
                start = index + 1
        elif char == closing_quote:
            # Single/double/backtick quoting escapes the delimiter by
            # doubling it. Square-bracket identifiers do not.
            if (
                quote != "["
                and index + 1 < len(arguments)
                and arguments[index + 1] == closing_quote
            ):
                index += 1
            else:
                quote = None
                closing_quote = None
        index += 1
    parts.append(arguments[start:])
    return parts


def _strip_sql_comments(sql: str) -> str:
    """Remove SQLite comments while preserving quoted strings/identifiers."""
    output = []
    quote = None
    closing_quote = None
    index = 0
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""
        if quote is None:
            if char in {"'", '"', "`", "["}:
                quote = char
                closing_quote = "]" if char == "[" else char
                output.append(char)
            elif char == "-" and next_char == "-":
                index += 2
                while index < len(sql) and sql[index] not in "\r\n":
                    index += 1
                output.append(" ")
                continue
            elif char == "/" and next_char == "*":
                index += 2
                while index + 1 < len(sql) and sql[index : index + 2] != "*/":
                    index += 1
                index = min(index + 2, len(sql))
                output.append(" ")
                continue
            else:
                output.append(char)
        else:
            output.append(char)
            if char == closing_quote:
                if (
                    quote != "["
                    and index + 1 < len(sql)
                    and sql[index + 1] == closing_quote
                ):
                    output.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
                    closing_quote = None
        index += 1
    return "".join(output)


def _unquote_sql_value(value: str) -> str:
    if len(value) < 2:
        return value
    pairs = {"'": "'", '"': '"', "`": "`", "[": "]"}
    closing = pairs.get(value[0])
    if closing is None or value[-1] != closing:
        return value
    unquoted = value[1:-1]
    if value[0] != "[":
        unquoted = unquoted.replace(closing * 2, closing)
    return unquoted


def _is_fts_content_virtual_table(sql: str | None) -> bool:
    return (
        _virtual_table_module(sql) in {"fts3", "fts4", "fts5"}
        and "content=" in sql.lower()
    )
