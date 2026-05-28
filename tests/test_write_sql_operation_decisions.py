import pytest

from datasette.utils.sql_analysis import Operation
from datasette.write_sql import (
    IgnoreWriteSqlOperation,
    RejectWriteSqlOperation,
    RequireWriteSqlPermissions,
    UnsupportedWriteSqlOperation,
    decision_for_write_sql_operation,
)


@pytest.mark.parametrize(
    ("operation", "reason"),
    (
        pytest.param(
            Operation("read", "schema", None, None, "main", internal=True),
            "internal SQLite operation",
            id="internal",
        ),
        pytest.param(
            Operation("select", "statement", None, None, None),
            "select statement",
            id="select-statement",
        ),
        pytest.param(
            Operation("function", "function", None, None, None, target="upper"),
            "SQL function",
            id="function",
        ),
    ),
)
def test_decision_for_write_sql_operation_ignores_operations(operation, reason):
    decision = decision_for_write_sql_operation(operation)

    assert isinstance(decision, IgnoreWriteSqlOperation)
    assert decision.reason == reason


@pytest.mark.parametrize("operation", ("insert", "update"))
def test_decision_for_write_sql_operation_requires_table_write_permissions(operation):
    decision = decision_for_write_sql_operation(
        Operation(operation, "table", "data", "dogs", None)
    )

    assert isinstance(decision, RequireWriteSqlPermissions)
    assert [permission.action for permission in decision.permissions] == [
        "insert-row",
        "update-row",
        "delete-row",
    ]
    assert [str(permission.resource) for permission in decision.permissions] == [
        "data/dogs",
        "data/dogs",
        "data/dogs",
    ]


@pytest.mark.parametrize(
    ("operation", "message"),
    (
        pytest.param(
            Operation("vacuum", "statement", None, None, None),
            "VACUUM is not allowed in user-supplied SQL",
            id="vacuum",
        ),
        pytest.param(
            Operation("insert", "table", "data", "docs", None, table_kind="virtual"),
            "Writes to virtual tables are not allowed in user-supplied SQL",
            id="virtual-table",
        ),
        pytest.param(
            Operation(
                "insert", "table", "data", "docs_data", None, table_kind="shadow"
            ),
            "Writes to shadow tables are not allowed in user-supplied SQL",
            id="shadow-table",
        ),
    ),
)
def test_decision_for_write_sql_operation_rejects_operations(operation, message):
    decision = decision_for_write_sql_operation(operation)

    assert isinstance(decision, RejectWriteSqlOperation)
    assert decision.message == message


def test_decision_for_write_sql_operation_reports_unsupported_operations():
    decision = decision_for_write_sql_operation(
        Operation("unknown", "unknown", None, None, None)
    )

    assert isinstance(decision, UnsupportedWriteSqlOperation)
    assert decision.message == "Unsupported SQL operation: unknown unknown"
