---
orphan: true
---

# Datasette 1.0a20 plugin upgrade guide

<!-- START UPGRADE 1.0a20 -->

Datasette 1.0a20 makes some breaking changes to Datasette's permission system. Plugins need to be updated if they use **any of the following**:

- The `register_permissions()` plugin  hook - this should be replaced with `register_actions`
- The `permission_allowed()` plugin hook - this should be upgraded to use `permission_resources_sql()`.
- The `datasette.permission_allowed()` internal method - this should be replaced with `datasette.allowed()`
- Logic that grants access to the `"root"` actor can be removed.

## Permissions are now actions

The `register_permissions()` hook shoud be replaced with `register_actions()`.

Old code:

```python
@hookimpl
def register_permissions(datasette):
    return [
        Permission(
            name="explain-sql",
            abbr=None,
            description="Can explain SQL queries",
            takes_database=True,
            takes_resource=False,
            default=False,
        ),
        Permission(
            name="annotate-rows",
            abbr=None,
            description="Can annotate rows",
            takes_database=True,
            takes_resource=True,
            default=False,
        ),
        Permission(
            name="view-debug-info",
            abbr=None,
            description="Can view debug information",
            takes_database=False,
            takes_resource=False,
            default=False,
        ),
    ]
```
The new `Action` does not have a `default=` parameter.

Here's the equivalent new code:

```python
from datasette import hookimpl
from datasette.permissions import Action
from datasette.resources import DatabaseResource, TableResource

@hookimpl
def register_actions(datasette):
    return [
        Action(
            name="explain-sql",
            abbr=None,
            description="Explain SQL queries",
            resource_class=DatabaseResource,
        ),
        Action(
            name="annotate-rows",
            abbr=None,
            description="Annotate rows",
            resource_class=TableResource,
        ),
        Action(
            name="view-debug-info",
            abbr=None,
            description="View debug information",
        ),
    ]
```

For actions that apply to specific resources (like databases or tables), specify the `resource_class` instead of `takes_parent` and `takes_child`. Note that `view-debug-info` does not specify a `resource_class` because it applies globally.

## permission_allowed() hook is replaced by permission_resources_sql()

The following old code:
```python
@hookimpl
def permission_allowed(action):
    if action == "permissions-debug":
        return True
```
Can be replaced by:
```python
from datasette.permissions import PermissionSQL

@hookimpl
def permission_resources_sql(action):
    return PermissionSQL.allow(reason="datasette-allow-permissions-debug")
```
A `.deny(reason="")` class method is also available.

For more complex permission checks consult the documentation for that plugin hook:
<https://docs.datasette.io/en/latest/plugin_hooks.html#permission-resources-sql-datasette-actor-action>

## Using datasette.allowed() to check permissions instead of datasette.permission_allowed()

The internal method `datasette.permission_allowed()` has been replaced by `datasette.allowed()`.

The old method looked like this:
```python
can_debug = await datasette.permission_allowed(
    request.actor,
    "view-debug-info",
)
can_explain_sql = await datasette.permission_allowed(
    request.actor,
    "explain-sql",
    resource="database_name",
)
can_annotate_rows = await datasette.permission_allowed(
    request.actor,
    "annotate-rows",
    resource=(database_name, table_name),
)
```
Note the confusing design here where `resource` could be either a string or a tuple depending on the permission being checked.

The new keyword-only design makes this a lot more clear:
```python
from datasette.resources import DatabaseResource, TableResource
can_debug = await datasette.allowed(
    actor=request.actor,
    action="view-debug-info",
)
can_explain_sql = await datasette.allowed(
    actor=request.actor,
    action="explain-sql",
    resource=DatabaseResource(database_name),
)
can_annotate_rows = await datasette.allowed(
    actor=request.actor,
    action="annotate-rows",
    resource=TableResource(database_name, table_name),
)
```

## Root user checks are no longer necessary

Some plugins would introduce their own custom permission and then ensure the `"root"` actor had access to it using a pattern like this:

```python
@hookimpl
def register_permissions(datasette):
    return [
        Permission(
            name="upload-dbs",
            abbr=None,
            description="Upload SQLite database files",
            takes_database=False,
            takes_resource=False,
            default=False,
        )
    ]


@hookimpl
def permission_allowed(actor, action):
    if action == "upload-dbs" and actor and actor.get("id") == "root":
        return True
```
This is no longer necessary in Datasette 1.0a20 - the `"root"` actor automatically has all permissions when Datasette is started with the `datasette --root` option.

The `permission_allowed()` hook in this example can be entirely removed.

## Fixing async with httpx.AsyncClient(app=app)

Some older plugins may use the following pattern in their tests, which is no longer supported:
```python
app = Datasette([], memory=True).app()
async with httpx.AsyncClient(app=app) as client:
    response = await client.get("http://localhost/path")
```
The new pattern is to use `ds.client` like this:
```python
ds = Datasette([], memory=True)
response = await ds.client.get("/path")
```
