from datasette.utils.sql_analysis import Operation
from datasette.write_sql import (
    IgnoreWriteSqlOperation,
    RejectWriteSqlOperation,
    RequireWriteSqlPermissions,
    UnsupportedWriteSqlOperation,
    WriteSqlOperationDecision,
    decision_for_write_sql_operation,
)


def test_decision_for_write_sql_operation_ignores_internal_and_select_operations():
    internal_decision = decision_for_write_sql_operation(
        Operation("read", "schema", None, None, "main", internal=True)
    )
    select_decision = decision_for_write_sql_operation(
        Operation("select", "statement", None, None, None)
    )

    assert isinstance(internal_decision, IgnoreWriteSqlOperation)
    assert isinstance(internal_decision, WriteSqlOperationDecision)
    assert isinstance(select_decision, IgnoreWriteSqlOperation)
    assert isinstance(select_decision, WriteSqlOperationDecision)


def test_decision_for_write_sql_operation_requires_table_write_permissions():
    decision = decision_for_write_sql_operation(
        Operation("insert", "table", "data", "dogs", None)
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


def test_decision_for_write_sql_operation_rejects_vacuum():
    decision = decision_for_write_sql_operation(
        Operation("vacuum", "statement", None, None, None)
    )

    assert isinstance(decision, RejectWriteSqlOperation)
    assert decision.message == "VACUUM is not allowed in user-supplied SQL"


def test_decision_for_write_sql_operation_reports_unsupported_functions():
    decision = decision_for_write_sql_operation(
        Operation("function", "function", None, None, None, target="upper")
    )

    assert isinstance(decision, UnsupportedWriteSqlOperation)
    assert decision.message == "Unsupported SQL operation: function function"
