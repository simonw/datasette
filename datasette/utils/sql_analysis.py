from dataclasses import dataclass
from typing import Literal

from datasette.utils.sqlite import SQLiteTableType, sqlite3, sqlite_table_type

SQLOperation = Literal[
    "read",
    "insert",
    "update",
    "delete",
    "select",
    "function",
    "create",
    "alter",
    "drop",
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "attach",
    "detach",
    "pragma",
    "analyze",
    "reindex",
    "vacuum",
    "unknown",
]
SQLTargetType = Literal[
    "table",
    "index",
    "view",
    "trigger",
    "virtual-table",
    "schema",
    "statement",
    "transaction",
    "database",
    "pragma",
    "function",
    "unknown",
]
SQLTableOperation = Literal["read", "insert", "update", "delete"]
SQLSchemaOperation = Literal["create", "drop"]
SQLSchemaTargetType = Literal["index", "table", "trigger", "view", "virtual-table"]


@dataclass(frozen=True)
class Operation:
    operation: SQLOperation
    target_type: SQLTargetType
    database: str | None
    table: str | None
    sqlite_schema: str | None
    table_kind: SQLiteTableType | None = None
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
_CREATE_ACTIONS: dict[int, tuple[SQLSchemaOperation, SQLSchemaTargetType]] = {
    sqlite3.SQLITE_CREATE_INDEX: ("create", "index"),
    sqlite3.SQLITE_CREATE_TABLE: ("create", "table"),
    sqlite3.SQLITE_CREATE_TRIGGER: ("create", "trigger"),
    sqlite3.SQLITE_CREATE_VIEW: ("create", "view"),
}
_DROP_ACTIONS: dict[int, tuple[SQLSchemaOperation, SQLSchemaTargetType]] = {
    sqlite3.SQLITE_DROP_INDEX: ("drop", "index"),
    sqlite3.SQLITE_DROP_TABLE: ("drop", "table"),
    sqlite3.SQLITE_DROP_TRIGGER: ("drop", "trigger"),
    sqlite3.SQLITE_DROP_VIEW: ("drop", "view"),
}


def _add_schema_action(
    action_name: str,
    operation: SQLSchemaOperation,
    target_type: SQLSchemaTargetType,
) -> None:
    action_value = getattr(sqlite3, action_name, None)
    if action_value is not None:
        actions = _CREATE_ACTIONS if operation == "create" else _DROP_ACTIONS
        actions[action_value] = (operation, target_type)


_TEMP_SCHEMA_ACTIONS: tuple[
    tuple[str, SQLSchemaOperation, SQLSchemaTargetType], ...
] = (
    ("SQLITE_CREATE_TEMP_INDEX", "create", "index"),
    ("SQLITE_CREATE_TEMP_TABLE", "create", "table"),
    ("SQLITE_CREATE_TEMP_TRIGGER", "create", "trigger"),
    ("SQLITE_CREATE_TEMP_VIEW", "create", "view"),
    ("SQLITE_DROP_TEMP_INDEX", "drop", "index"),
    ("SQLITE_DROP_TEMP_TABLE", "drop", "table"),
    ("SQLITE_DROP_TEMP_TRIGGER", "drop", "trigger"),
    ("SQLITE_DROP_TEMP_VIEW", "drop", "view"),
)
for schema_action in _TEMP_SCHEMA_ACTIONS:
    _add_schema_action(*schema_action)

_VTABLE_SCHEMA_ACTIONS: tuple[
    tuple[str, SQLSchemaOperation, SQLSchemaTargetType], ...
] = (
    ("SQLITE_CREATE_VTABLE", "create", "virtual-table"),
    ("SQLITE_DROP_VTABLE", "drop", "virtual-table"),
)
for schema_action in _VTABLE_SCHEMA_ACTIONS:
    _add_schema_action(*schema_action)

_SQLITE_SCHEMA_TABLES = {
    "sqlite_master",
    "sqlite_schema",
    "sqlite_temp_master",
    "sqlite_temp_schema",
}
_SQLITE_INTERNAL_SCHEMA_FUNCTIONS = {
    "length",
    "like",
    "printf",
    "sqlite_drop_column",
    "sqlite_rename_column",
    "sqlite_rename_quotefix",
    "sqlite_rename_table",
    "sqlite_rename_test",
    "substr",
}

_AUTHORIZER_ACTION_NAMES = {
    getattr(sqlite3, name): name
    for name in (
        "SQLITE_CREATE_INDEX",
        "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE",
        "SQLITE_CREATE_TEMP_TRIGGER",
        "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER",
        "SQLITE_CREATE_VIEW",
        "SQLITE_DELETE",
        "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE",
        "SQLITE_DROP_TEMP_INDEX",
        "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER",
        "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW",
        "SQLITE_INSERT",
        "SQLITE_PRAGMA",
        "SQLITE_READ",
        "SQLITE_SELECT",
        "SQLITE_TRANSACTION",
        "SQLITE_UPDATE",
        "SQLITE_ATTACH",
        "SQLITE_DETACH",
        "SQLITE_ALTER_TABLE",
        "SQLITE_REINDEX",
        "SQLITE_ANALYZE",
        "SQLITE_CREATE_VTABLE",
        "SQLITE_DROP_VTABLE",
        "SQLITE_FUNCTION",
        "SQLITE_SAVEPOINT",
        "SQLITE_RECURSIVE",
    )
    if hasattr(sqlite3, name)
}


def _allow_authorizer_action(*args):
    return sqlite3.SQLITE_OK


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

        if action == sqlite3.SQLITE_SELECT:
            record(
                "select",
                "statement",
                database=None,
                table=None,
                sqlite_schema=sqlite_schema,
                target=None,
                source=source,
            )
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_FUNCTION and arg2 is not None:
            record(
                "function",
                "function",
                database=None,
                table=None,
                sqlite_schema=sqlite_schema,
                target=arg2,
                source=source,
            )
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_SAVEPOINT and arg1 is not None:
            record(
                "savepoint",
                "transaction",
                database=None,
                table=None,
                sqlite_schema=sqlite_schema,
                target="{} {}".format(arg1, arg2) if arg2 is not None else arg1,
                source=source,
            )
            return sqlite3.SQLITE_OK

        action_name = _AUTHORIZER_ACTION_NAMES.get(action, "SQLITE_{}".format(action))
        record(
            "unknown",
            "unknown",
            database=database_for_schema(sqlite_schema),
            table=None,
            sqlite_schema=sqlite_schema,
            target=action_name,
            source=source,
        )
        return sqlite3.SQLITE_OK

    table_kind_cache: dict[tuple[str | None, str], SQLiteTableType | None] = {}

    conn.set_authorizer(authorizer)
    try:
        explain_rows = conn.execute(
            "EXPLAIN " + sql, params if params is not None else {}
        ).fetchall()
        # Passing None before these lookups leaves a failing callback installed
        # on Python 3.10, so use a permissive callback until they are complete.
        conn.set_authorizer(_allow_authorizer_action)

        if not operations:
            vacuum_row = next((row for row in explain_rows if row[1] == "Vacuum"), None)
            if vacuum_row is not None:
                schema_by_index = {
                    row[0]: row[1] for row in conn.execute("PRAGMA database_list")
                }
                sqlite_schema = schema_by_index.get(vacuum_row[2])
                database = database_for_schema(sqlite_schema)
                record(
                    "vacuum",
                    "database",
                    database=database,
                    table=None,
                    sqlite_schema=sqlite_schema,
                    target=database,
                    source=None,
                )
            else:
                record(
                    "unknown",
                    "statement",
                    database=database_name,
                    table=None,
                    sqlite_schema=None,
                    target=None,
                    source=None,
                )

        for key in operations:
            if (
                key.target_type == "table"
                and key.operation in {"read", "insert", "update", "delete"}
                and key.table is not None
            ):
                cache_key = (key.sqlite_schema, key.table)
                if cache_key not in table_kind_cache:
                    table_kind_cache[cache_key] = sqlite_table_type(
                        conn, key.table, schema=key.sqlite_schema
                    )
    finally:
        conn.set_authorizer(None)

    has_schema_operation = any(
        key.target_type in {"table", "index", "view", "trigger", "virtual-table"}
        and key.operation in {"create", "alter", "drop"}
        for key in operations
    )
    dropped_tables = {
        (key.database, key.table)
        for key in operations
        if key.operation == "drop" and key.target_type == "table"
    }

    def key_is_drop_table_delete(key: OperationKey) -> bool:
        return (
            key.operation == "delete"
            and key.target_type == "table"
            and (key.database, key.table) in dropped_tables
        )

    has_user_table_access_in_schema_operation = any(
        key.operation in {"read", "insert", "update", "delete"}
        and key.target_type == "table"
        and not key.internal
        and not key_is_drop_table_delete(key)
        for key in operations
    )

    def operation_is_internal(key: OperationKey) -> bool:
        if key.internal or (has_schema_operation and key.target_type == "schema"):
            return True
        if has_schema_operation and key.operation == "reindex":
            return True
        if (
            has_schema_operation
            and not has_user_table_access_in_schema_operation
            and key.operation == "function"
            and key.target in _SQLITE_INTERNAL_SCHEMA_FUNCTIONS
        ):
            return True
        if key_is_drop_table_delete(key):
            return True
        return False

    def table_kind_for(key: OperationKey) -> SQLiteTableType | None:
        if (
            key.target_type != "table"
            or key.operation not in {"read", "insert", "update", "delete"}
            or key.table is None
        ):
            return None
        return table_kind_cache[(key.sqlite_schema, key.table)]

    return SQLAnalysis(
        operations=tuple(
            Operation(
                operation=key.operation,
                target_type=key.target_type,
                database=key.database,
                table=key.table,
                sqlite_schema=key.sqlite_schema,
                table_kind=table_kind_for(key),
                target=key.target,
                columns=tuple(sorted(columns)),
                source=key.source,
                internal=operation_is_internal(key),
            )
            for key, columns in operations.items()
        )
    )
