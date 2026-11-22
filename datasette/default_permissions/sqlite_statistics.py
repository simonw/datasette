"""Default table-access policy for SQLite optimizer statistics."""

import json

from datasette import hookimpl
from datasette.permissions import PermissionSQL


@hookimpl
def permission_resources_sql(action):
    if action != "view-table":
        return None
    return PermissionSQL(
        sql="""
            SELECT database_name AS parent, value AS child, 0 AS allow,
                'SQLite statistics tables are denied by default' AS reason
            FROM catalog_databases
            CROSS JOIN json_each(:sqlite_statistics_names)
        """,
        params={
            "sqlite_statistics_names": json.dumps(
                ["sqlite_stat1", "sqlite_stat2", "sqlite_stat3", "sqlite_stat4"]
            )
        },
    )
