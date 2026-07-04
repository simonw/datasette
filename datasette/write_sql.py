from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .permissions import Resource
from .resources import DatabaseResource, TableResource
from .utils import named_parameters, sqlite3
from .utils.asgi import Forbidden
from .utils.sql_analysis import Operation, SQLAnalysis

if TYPE_CHECKING:
    from .app import Datasette


class QueryWriteRejected(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class PermissionRequirement:
    action: str
    resource: Resource


PermissionRequirements = tuple[PermissionRequirement, ...]


class WriteSqlOperationDecision:
    """What Datasette should do with one operation in user-supplied write SQL."""


@dataclass(frozen=True)
class IgnoreWriteSqlOperation(WriteSqlOperationDecision):
    reason: str


@dataclass(frozen=True)
class RequireWriteSqlPermissions(WriteSqlOperationDecision):
    permissions: PermissionRequirements


@dataclass(frozen=True)
class RejectWriteSqlOperation(WriteSqlOperationDecision):
    message: str


@dataclass(frozen=True)
class UnsupportedWriteSqlOperation(WriteSqlOperationDecision):
    message: str


def row_mutation_requirements(database: str, table: str) -> PermissionRequirements:
    resource = TableResource(database=database, table=table)
    return tuple(
        PermissionRequirement(action=action, resource=resource)
        for action in ("insert-row", "update-row", "delete-row")
    )


def decision_for_write_sql_operation(
    operation: Operation,
) -> WriteSqlOperationDecision:
    unsupported_message = (
        f"Unsupported SQL operation: {operation.operation} {operation.target_type}"
    )
    if operation.internal:
        return IgnoreWriteSqlOperation("internal SQLite operation")
    if operation.operation == "select":
        return IgnoreWriteSqlOperation("select statement")
    if operation.operation == "vacuum":
        return RejectWriteSqlOperation("VACUUM is not allowed in user-supplied SQL")
    if operation.operation in {"insert", "update", "delete"}:
        if operation.table_kind == "virtual":
            return RejectWriteSqlOperation(
                "Writes to virtual tables are not allowed in user-supplied SQL"
            )
        if operation.table_kind == "shadow":
            return RejectWriteSqlOperation(
                "Writes to shadow tables are not allowed in user-supplied SQL"
            )
    if operation.operation == "function":
        return IgnoreWriteSqlOperation("SQL function")
    if (
        operation.operation == "read"
        and operation.target_type == "table"
        and operation.database is not None
        and operation.table is not None
    ):
        return RequireWriteSqlPermissions(
            (
                PermissionRequirement(
                    action="view-table",
                    resource=TableResource(
                        database=operation.database, table=operation.table
                    ),
                ),
            )
        )
    if (
        operation.operation in {"insert", "update"}
        and operation.target_type == "table"
        and operation.database is not None
        and operation.table is not None
    ):
        return RequireWriteSqlPermissions(
            row_mutation_requirements(
                database=operation.database,
                table=operation.table,
            )
        )
    if (
        operation.operation == "delete"
        and operation.target_type == "table"
        and operation.database is not None
        and operation.table is not None
    ):
        return RequireWriteSqlPermissions(
            (
                PermissionRequirement(
                    action="delete-row",
                    resource=TableResource(
                        database=operation.database, table=operation.table
                    ),
                ),
            )
        )
    if operation.operation == "create" and operation.target_type == "table":
        if operation.database is None:
            return UnsupportedWriteSqlOperation(unsupported_message)
        return RequireWriteSqlPermissions(
            (
                PermissionRequirement(
                    action="create-table",
                    resource=DatabaseResource(database=operation.database),
                ),
            )
        )
    if operation.operation == "create" and operation.target_type == "view":
        if operation.database is None:
            return UnsupportedWriteSqlOperation(unsupported_message)
        return RequireWriteSqlPermissions(
            (
                PermissionRequirement(
                    action="create-view",
                    resource=DatabaseResource(database=operation.database),
                ),
            )
        )
    if (
        operation.operation == "drop"
        and operation.target_type == "view"
        and operation.database is not None
        and operation.table is not None
    ):
        return RequireWriteSqlPermissions(
            (
                PermissionRequirement(
                    action="drop-view",
                    resource=TableResource(
                        database=operation.database, table=operation.table
                    ),
                ),
            )
        )
    if (
        operation.operation == "alter"
        and operation.target_type == "table"
        and operation.database is not None
        and operation.table is not None
    ):
        return RequireWriteSqlPermissions(
            (
                PermissionRequirement(
                    action="alter-table",
                    resource=TableResource(
                        database=operation.database, table=operation.table
                    ),
                ),
            )
        )
    if (
        operation.operation == "drop"
        and operation.target_type == "table"
        and operation.database is not None
        and operation.table is not None
    ):
        return RequireWriteSqlPermissions(
            (
                PermissionRequirement(
                    action="drop-table",
                    resource=TableResource(
                        database=operation.database, table=operation.table
                    ),
                ),
            )
        )
    if (
        operation.operation in {"create", "drop"}
        and operation.target_type == "index"
        and operation.database is not None
        and operation.table is not None
    ):
        return RequireWriteSqlPermissions(
            (
                PermissionRequirement(
                    action="alter-table",
                    resource=TableResource(
                        database=operation.database, table=operation.table
                    ),
                ),
            )
        )
    return UnsupportedWriteSqlOperation(unsupported_message)


def operation_is_write(operation: Operation) -> bool:
    return operation.operation in {
        "insert",
        "update",
        "delete",
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
    }


async def ensure_query_write_permissions(
    datasette: Datasette,
    database: str,
    sql: str,
    *,
    actor: dict[str, object] | None = None,
    params: dict[str, object] | None = None,
    analysis: SQLAnalysis | None = None,
) -> SQLAnalysis:
    db = datasette.get_database(database)
    if analysis is None:
        if params is None:
            params = {name: "" for name in named_parameters(sql)}
        try:
            analysis = await db.analyze_sql(sql, params)
        except sqlite3.DatabaseError as ex:
            raise Forbidden(f"Could not analyze query: {ex}") from ex

    for operation in analysis.operations:
        decision = decision_for_write_sql_operation(operation)
        if isinstance(decision, IgnoreWriteSqlOperation):
            continue
        if isinstance(decision, RejectWriteSqlOperation):
            raise QueryWriteRejected(decision.message)
        if isinstance(decision, UnsupportedWriteSqlOperation):
            raise Forbidden(decision.message)
        permissions = decision.permissions
        if operation.database != database:
            raise Forbidden("Writable queries may not access attached databases")
        for permission in permissions:
            if not await datasette.allowed(
                action=permission.action,
                resource=permission.resource,
                actor=actor,
            ):
                raise Forbidden(
                    f"Permission denied: need {permission.action} "
                    f"on {permission.resource}"
                )
    return analysis
