from dataclasses import dataclass
from typing import Literal

from datasette.utils.sqlite import sqlite3

SQLOperation = Literal[
    "read",
    "insert",
    "update",
    "delete",
    "create",
    "alter",
    "drop",
    "begin",
    "commit",
    "rollback",
    "attach",
    "detach",
    "pragma",
    "analyze",
    "reindex",
]
SQLTargetType = Literal[
    "table",
    "index",
    "view",
    "trigger",
    "schema",
    "transaction",
    "database",
    "pragma",
    "unknown",
]
SQLTableOperation = Literal["read", "insert", "update", "delete"]


@dataclass(frozen=True)
class Operation:
    operation: SQLOperation
    target_type: SQLTargetType
    database: str | None
    table: str | None
    sqlite_schema: str | None
    target: str | None = None
    columns: tuple[str, ...] = ()
    source: str | None = None
    internal: bool = False


@dataclass(frozen=True)
class SQLAnalysis:
    operations: tuple[Operation, ...]


# Hashable dict key for grouping repeated authorizer callbacks while collecting columns.
@dataclass(frozen=True)
class OperationKey:
    operation: SQLOperation
    target_type: SQLTargetType
    database: str | None
    table: str | None
    sqlite_schema: str | None
    target: str | None
    source: str | None
    internal: bool


_ACTION_TO_OPERATION: dict[int, SQLTableOperation] = {
    sqlite3.SQLITE_READ: "read",
    sqlite3.SQLITE_INSERT: "insert",
    sqlite3.SQLITE_UPDATE: "update",
    sqlite3.SQLITE_DELETE: "delete",
}

# Values are (operation, target_type) pairs used to construct Operation objects.
_CREATE_ACTIONS = {
    sqlite3.SQLITE_CREATE_INDEX: ("create", "index"),
    sqlite3.SQLITE_CREATE_TABLE: ("create", "table"),
    sqlite3.SQLITE_CREATE_TRIGGER: ("create", "trigger"),
    sqlite3.SQLITE_CREATE_VIEW: ("create", "view"),
}
_DROP_ACTIONS = {
    sqlite3.SQLITE_DROP_INDEX: ("drop", "index"),
    sqlite3.SQLITE_DROP_TABLE: ("drop", "table"),
    sqlite3.SQLITE_DROP_TRIGGER: ("drop", "trigger"),
    sqlite3.SQLITE_DROP_VIEW: ("drop", "view"),
}
for action_name, operation, target_type in (
    ("SQLITE_CREATE_TEMP_INDEX", "create", "index"),
    ("SQLITE_CREATE_TEMP_TABLE", "create", "table"),
    ("SQLITE_CREATE_TEMP_TRIGGER", "create", "trigger"),
    ("SQLITE_CREATE_TEMP_VIEW", "create", "view"),
    ("SQLITE_DROP_TEMP_INDEX", "drop", "index"),
    ("SQLITE_DROP_TEMP_TABLE", "drop", "table"),
    ("SQLITE_DROP_TEMP_TRIGGER", "drop", "trigger"),
    ("SQLITE_DROP_TEMP_VIEW", "drop", "view"),
):
    action_value = getattr(sqlite3, action_name, None)
    if action_value is not None:
        actions = _CREATE_ACTIONS if operation == "create" else _DROP_ACTIONS
        actions[action_value] = (operation, target_type)

_SQLITE_SCHEMA_TABLES = {"sqlite_master", "sqlite_schema"}


def analyze_sql_tables(
    conn,
    sql: str,
    params=None,
    *,
    database_name: str | None = None,
    schema_to_database: dict[str, str] | None = None,
) -> SQLAnalysis:
    """
    Return operations performed by a SQL statement according to SQLite's authorizer.

    This function is synchronous and connection-based. It temporarily installs a
    SQLite authorizer, prepares ``EXPLAIN <sql>``, and returns the operation
    callbacks observed while SQLite compiles the statement.
    """
    operations: dict[OperationKey, set[str]] = {}

    def database_for_schema(sqlite_schema):
        if schema_to_database and sqlite_schema in schema_to_database:
            return schema_to_database[sqlite_schema]
        if sqlite_schema == "main" and database_name is not None:
            return database_name
        return sqlite_schema

    def record(
        operation: SQLOperation,
        target_type: SQLTargetType,
        *,
        database: str | None,
        table: str | None,
        sqlite_schema: str | None,
        target: str | None,
        source: str | None,
        column: str | None = None,
        internal: bool = False,
    ):
        key = OperationKey(
            operation=operation,
            target_type=target_type,
            database=database,
            table=table,
            sqlite_schema=sqlite_schema,
            target=target,
            source=source,
            internal=internal,
        )
        columns = operations.setdefault(key, set())
        if column is not None:
            columns.add(column)

    def authorizer(action, arg1, arg2, sqlite_schema, source):
        operation = _ACTION_TO_OPERATION.get(action)
        if operation is not None and arg1 is not None:
            target_type = "schema" if arg1 in _SQLITE_SCHEMA_TABLES else "table"
            column = (
                arg2 if operation in ("read", "update") and arg2 is not None else None
            )
            record(
                operation,
                target_type,
                database=database_for_schema(sqlite_schema),
                table=arg1 if target_type == "table" else None,
                sqlite_schema=sqlite_schema,
                target=arg1,
                source=source,
                column=column,
                internal=target_type == "schema",
            )
            return sqlite3.SQLITE_OK

        create_operation = _CREATE_ACTIONS.get(action)
        if create_operation is not None and arg1 is not None:
            operation, target_type = create_operation
            related_table = arg2 if target_type in {"index", "trigger"} else arg1
            record(
                operation,
                target_type,
                database=database_for_schema(sqlite_schema),
                table=related_table,
                sqlite_schema=sqlite_schema,
                target=arg1,
                source=source,
            )
            return sqlite3.SQLITE_OK

        drop_operation = _DROP_ACTIONS.get(action)
        if drop_operation is not None and arg1 is not None:
            operation, target_type = drop_operation
            related_table = arg2 if target_type in {"index", "trigger"} else arg1
            record(
                operation,
                target_type,
                database=database_for_schema(sqlite_schema),
                table=related_table,
                sqlite_schema=sqlite_schema,
                target=arg1,
                source=source,
            )
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_ALTER_TABLE and arg2 is not None:
            record(
                "alter",
                "table",
                database=database_for_schema(arg1),
                table=arg2,
                sqlite_schema=arg1,
                target=arg2,
                source=source,
            )
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_TRANSACTION and arg1 is not None:
            record(
                arg1.lower(),
                "transaction",
                database=None,
                table=None,
                sqlite_schema=None,
                target=arg1,
                source=source,
            )
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_ATTACH and arg1 is not None:
            record(
                "attach",
                "database",
                database=None,
                table=None,
                sqlite_schema=None,
                target=arg1,
                source=source,
            )
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_DETACH and arg1 is not None:
            record(
                "detach",
                "database",
                database=None,
                table=None,
                sqlite_schema=None,
                target=arg1,
                source=source,
            )
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_PRAGMA and arg1 is not None:
            record(
                "pragma",
                "pragma",
                database=None,
                table=None,
                sqlite_schema=sqlite_schema,
                target=arg1,
                source=source,
            )
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_ANALYZE:
            record(
                "analyze",
                "database" if arg1 is None else "table",
                database=database_for_schema(sqlite_schema),
                table=arg1,
                sqlite_schema=sqlite_schema,
                target=arg1,
                source=source,
            )
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_REINDEX and arg1 is not None:
            record(
                "reindex",
                "index",
                database=database_for_schema(sqlite_schema),
                table=None,
                sqlite_schema=sqlite_schema,
                target=arg1,
                source=source,
            )
            return sqlite3.SQLITE_OK

        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorizer)
    try:
        conn.execute("EXPLAIN " + sql, params if params is not None else {}).fetchall()
    finally:
        conn.set_authorizer(None)

    has_schema_operation = any(
        key.target_type in {"table", "index", "view", "trigger"}
        and key.operation in {"create", "alter", "drop"}
        for key in operations
    )

    return SQLAnalysis(
        operations=tuple(
            Operation(
                operation=key.operation,
                target_type=key.target_type,
                database=key.database,
                table=key.table,
                sqlite_schema=key.sqlite_schema,
                target=key.target,
                columns=tuple(sorted(columns)),
                source=key.source,
                internal=key.internal
                or (has_schema_operation and key.target_type == "schema"),
            )
            for key, columns in operations.items()
        )
    )
