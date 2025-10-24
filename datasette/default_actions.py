from datasette import hookimpl
from datasette.permissions import Action
from datasette.resources import (
    InstanceResource,
    DatabaseResource,
    TableResource,
    QueryResource,
)


@hookimpl
def register_actions():
    """Register the core Datasette actions."""
    return (
        # View actions
        Action(
            name="view-instance",
            abbr="vi",
            description="View Datasette instance",
            takes_parent=False,
            takes_child=False,
            resource_class=InstanceResource,
        ),
        Action(
            name="view-database",
            abbr="vd",
            description="View database",
            takes_parent=True,
            takes_child=False,
            resource_class=DatabaseResource,
        ),
        Action(
            name="view-database-download",
            abbr="vdd",
            description="Download database file",
            takes_parent=True,
            takes_child=False,
            resource_class=DatabaseResource,
        ),
        Action(
            name="view-table",
            abbr="vt",
            description="View table",
            takes_parent=True,
            takes_child=True,
            resource_class=TableResource,
        ),
        Action(
            name="view-query",
            abbr="vq",
            description="View named query results",
            takes_parent=True,
            takes_child=True,
            resource_class=QueryResource,
        ),
        Action(
            name="execute-sql",
            abbr="es",
            description="Execute read-only SQL queries",
            takes_parent=True,
            takes_child=False,
            resource_class=DatabaseResource,
            also_requires="view-database",
        ),
        # Debug actions
        Action(
            name="permissions-debug",
            abbr="pd",
            description="Access permission debug tool",
            takes_parent=False,
            takes_child=False,
            resource_class=InstanceResource,
        ),
        Action(
            name="debug-menu",
            abbr="dm",
            description="View debug menu items",
            takes_parent=False,
            takes_child=False,
            resource_class=InstanceResource,
        ),
        # Write actions on tables
        Action(
            name="insert-row",
            abbr="ir",
            description="Insert rows",
            takes_parent=True,
            takes_child=True,
            resource_class=TableResource,
        ),
        Action(
            name="delete-row",
            abbr="dr",
            description="Delete rows",
            takes_parent=True,
            takes_child=True,
            resource_class=TableResource,
        ),
        Action(
            name="update-row",
            abbr="ur",
            description="Update rows",
            takes_parent=True,
            takes_child=True,
            resource_class=TableResource,
        ),
        Action(
            name="alter-table",
            abbr="at",
            description="Alter tables",
            takes_parent=True,
            takes_child=True,
            resource_class=TableResource,
        ),
        Action(
            name="drop-table",
            abbr="dt",
            description="Drop tables",
            takes_parent=True,
            takes_child=True,
            resource_class=TableResource,
        ),
        # Schema actions on databases
        Action(
            name="create-table",
            abbr="ct",
            description="Create tables",
            takes_parent=True,
            takes_child=False,
            resource_class=DatabaseResource,
        ),
    )
