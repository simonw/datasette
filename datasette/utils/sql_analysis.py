from dataclasses import dataclass
from typing import Literal

from datasette.utils.sqlite import sqlite3

SQLTableOperation = Literal["read", "insert", "update", "delete"]


@dataclass(frozen=True)
class SQLTableAccess:
    operation: SQLTableOperation
    database: str | None
    table: str
    sqlite_schema: str | None
    columns: tuple[str, ...] = ()
    source: str | None = None


@dataclass(frozen=True)
class SQLAnalysis:
    table_accesses: tuple[SQLTableAccess, ...]


_ACTION_TO_OPERATION: dict[int, SQLTableOperation] = {
    sqlite3.SQLITE_READ: "read",
    sqlite3.SQLITE_INSERT: "insert",
    sqlite3.SQLITE_UPDATE: "update",
    sqlite3.SQLITE_DELETE: "delete",
}


def analyze_sql_tables(
    conn,
    sql: str,
    params=None,
    *,
    database_name: str | None = None,
    schema_to_database: dict[str, str] | None = None,
) -> SQLAnalysis:
    """
    Return tables accessed by a SQL statement according to SQLite's authorizer.

    This function is synchronous and connection-based. It temporarily installs a
    SQLite authorizer, prepares ``EXPLAIN <sql>``, and returns the table access
    callbacks observed while SQLite compiles the statement.
    """
    accesses: dict[
        tuple[SQLTableOperation, str | None, str, str | None, str | None], set[str]
    ] = {}

    def database_for_schema(sqlite_schema):
        if schema_to_database and sqlite_schema in schema_to_database:
            return schema_to_database[sqlite_schema]
        if sqlite_schema == "main" and database_name is not None:
            return database_name
        return sqlite_schema

    def authorizer(action, arg1, arg2, sqlite_schema, source):
        operation = _ACTION_TO_OPERATION.get(action)
        if operation is None or arg1 is None:
            return sqlite3.SQLITE_OK

        key = (
            operation,
            database_for_schema(sqlite_schema),
            arg1,
            sqlite_schema,
            source,
        )
        columns = accesses.setdefault(key, set())
        if operation in ("read", "update") and arg2 is not None:
            columns.add(arg2)
        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorizer)
    try:
        conn.execute("EXPLAIN " + sql, params if params is not None else {}).fetchall()
    finally:
        conn.set_authorizer(None)

    return SQLAnalysis(
        table_accesses=tuple(
            SQLTableAccess(
                operation=operation,
                database=database,
                table=table,
                sqlite_schema=sqlite_schema,
                columns=tuple(sorted(columns)),
                source=source,
            )
            for (
                operation,
                database,
                table,
                sqlite_schema,
                source,
            ), columns in accesses.items()
        )
    )
