# Permission Plugin Examples

These snippets show how to use the new `permission_resources_sql` hook to
contribute rows to the action-based permission resolver. Each hook receives the
current actor dictionary (or ``None``) and must return an instance of
`datasette.utils.permissions.PluginSQL` (or a coroutine that resolves to one).

All examples assume the plugin lives in `my_permission_plugin/__init__.py` and
is registered using the standard `entry_points` mechanism.

The hook may return a single `PluginSQL`, `None`, or a list/tuple of
`PluginSQL` objects if you need to contribute multiple rows at once.

## Allow Alice To View A Specific Table

This plugin grants the actor with `id == "alice"` permission to perform the
`view-table` action against the `sales` table inside the `accounting` database.

```python
from datasette import hookimpl
from datasette.utils.permissions import PluginSQL

@hookimpl
def permission_resources_sql(datasette, actor, action):
    if action != "view-table":
        return None
    if not actor or actor.get("id") != "alice":
        return None

    return PluginSQL(
        source="alice_sales_allow",
        sql="""
            SELECT
                'accounting' AS parent,
                'sales' AS child,
                1 AS allow,
                'alice can view accounting/sales' AS reason
        """,
        params={},
    )
```

## Restrict Execute-SQL To A Database Prefix

Only allow `execute-sql` against databases whose name begins with
`analytics_`. This shows how to use parameters that the permission resolver
will pass through to the SQL snippet.

```python
from datasette import hookimpl
from datasette.utils.permissions import PluginSQL

@hookimpl
def permission_resources_sql(datasette, actor, action):
    if action != "execute-sql":
        return None

    return PluginSQL(
        source="analytics_execute_sql",
        sql="""
            SELECT
                parent,
                NULL AS child,
                1 AS allow,
                'execute-sql allowed for analytics_*' AS reason
            FROM catalog_databases
            WHERE database_name LIKE :prefix
        """,
        params={
            "prefix": "analytics_%",
        },
    )
```

## Read Permissions From A Custom Table

This example stores grants in an internal table called `permission_grants`
with columns `(actor_id, action, parent, child, allow, reason)`.

```python
from datasette import hookimpl
from datasette.utils.permissions import PluginSQL

@hookimpl
def permission_resources_sql(datasette, actor, action):
    if not actor:
        return None

    return PluginSQL(
        source="permission_grants_table",
        sql="""
            SELECT
                parent,
                child,
                allow,
                COALESCE(reason, 'permission_grants table') AS reason
            FROM permission_grants
            WHERE actor_id = :actor_id
              AND action = :action
        """,
        params={
            "actor_id": actor.get("id"),
            "action": action,
        },
    )
```

## Default Deny With An Exception

Combine a root-level deny with a specific table allow for trusted users.
The resolver will automatically apply the most specific rule.

```python
from datasette import hookimpl
from datasette.utils.permissions import PluginSQL

TRUSTED = {"alice", "bob"}

@hookimpl
def permission_resources_sql(datasette, actor, action):
    if action != "view-table":
        return None

    actor_id = (actor or {}).get("id")

    if actor_id not in TRUSTED:
        return PluginSQL(
            source="view_table_root_deny",
            sql="""
                SELECT NULL AS parent, NULL AS child, 0 AS allow,
                       'default deny view-table' AS reason
        """,
        params={},
    )

    return PluginSQL(
        source="trusted_allow",
        sql="""
            SELECT NULL AS parent, NULL AS child, 0 AS allow,
                   'default deny view-table' AS reason
            UNION ALL
            SELECT 'reports' AS parent, 'daily_metrics' AS child, 1 AS allow,
                   'trusted user access' AS reason
        """,
        params={"actor_id": actor_id},
    )
```

The `UNION ALL` ensures the deny rule is always present, while the second row
adds the exception for trusted users.

## Using Datasette.allowed_resources_sql()

Within Datasette itself (or a plugin that has access to a `Datasette` instance)
you can inspect the combined rules for debugging:

```python
sql, params = await datasette.allowed_resources_sql(
    actor={"id": "alice"},
    action="view-table",
)
print(sql)
print(params)
```

The SQL can then be executed directly or embedded in other queries.
