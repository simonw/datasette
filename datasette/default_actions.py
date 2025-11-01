from datasette import hookimpl
from datasette.permissions import Action
from datasette.resources import (
    DatabaseResource,
    TableResource,
    QueryResource,
)


@hookimpl
def register_actions():
    """Register the core Datasette actions."""
    return (
        # Global actions (no resource_class)
        Action(
            name="view-instance",
            abbr="vi",
            description="View Datasette instance",
        ),
        Action(
            name="permissions-debug",
            abbr="pd",
            description="Access permission debug tool",
        ),
        Action(
            name="debug-menu",
            abbr="dm",
            description="View debug menu items",
        ),
        # Database-level actions (parent-level)
        Action(
            name="view-database",
            abbr="vd",
            description="View database",
            resource_class=DatabaseResource,
        ),
        Action(
            name="view-database-download",
            abbr="vdd",
            description="Download database file",
            resource_class=DatabaseResource,
            also_requires="view-database",
        ),
        Action(
            name="execute-sql",
            abbr="es",
            description="Execute read-only SQL queries",
            resource_class=DatabaseResource,
            also_requires="view-database",
        ),
        Action(
            name="create-table",
            abbr="ct",
            description="Create tables",
            resource_class=DatabaseResource,
        ),
        # Table-level actions (child-level)
        Action(
            name="view-table",
            abbr="vt",
            description="View table",
            resource_class=TableResource,
        ),
        Action(
            name="insert-row",
            abbr="ir",
            description="Insert rows",
            resource_class=TableResource,
        ),
        Action(
            name="delete-row",
            abbr="dr",
            description="Delete rows",
            resource_class=TableResource,
        ),
        Action(
            name="update-row",
            abbr="ur",
            description="Update rows",
            resource_class=TableResource,
        ),
        Action(
            name="alter-table",
            abbr="at",
            description="Alter tables",
            resource_class=TableResource,
        ),
        Action(
            name="drop-table",
            abbr="dt",
            description="Drop tables",
            resource_class=TableResource,
        ),
        # Query-level actions (child-level)
        Action(
            name="view-query",
            abbr="vq",
            description="View named query results",
            resource_class=QueryResource,
        ),
    )
