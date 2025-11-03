---
orphan: true
---

# Datasette 1.0a20 plugin upgrade guide

(upgrade_guide_v1_a20)=

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
            description="Explain SQL queries",
            resource_class=DatabaseResource,
        ),
        Action(
            name="annotate-rows",
            description="Annotate rows",
            resource_class=TableResource,
        ),
        Action(
            name="view-debug-info",
            description="View debug information",
        ),
    ]
```
The `abbr=` is now optional and defaults to `None`.

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

### Root-enabled instances during testing

When writing tests that exercise root-only functionality, make sure to set `datasette.root_enabled = True` on the `Datasette` instance. Root permissions are only granted automatically when Datasette is started with `datasette --root` or when the flag is enabled directly in tests.

## Target the new APIs exclusively

Datasette 1.0a20’s permission system is substantially different from previous releases. Attempting to keep plugin code compatible with both the old `permission_allowed()` and the new `allowed()` interfaces leads to brittle workarounds. Prefer to adopt the 1.0a20 APIs (`register_actions`, `permission_resources_sql()`, and `datasette.allowed()`) outright and drop legacy fallbacks.

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

## Migrating from metadata= to config=

Datasette 1.0 separates metadata (titles, descriptions, licenses) from configuration (settings, plugins, queries, permissions). Plugin tests and code need to be updated accordingly.

### Update test constructors

Old code:
```python
ds = Datasette(
    memory=True,
    metadata={
        "databases": {
            "_memory": {"queries": {"my_query": {"sql": "select 1", "title": "My Query"}}}
        },
        "plugins": {
            "my-plugin": {"setting": "value"}
        }
    }
)
```

New code:
```python
ds = Datasette(
    memory=True,
    config={
        "databases": {
            "_memory": {"queries": {"my_query": {"sql": "select 1", "title": "My Query"}}}
        },
        "plugins": {
            "my-plugin": {"setting": "value"}
        }
    }
)
```

### Update datasette.metadata() calls

The `datasette.metadata()` method has been removed. Use these methods instead:

Old code:
```python
try:
    title = datasette.metadata(database=database)["queries"][query_name]["title"]
except (KeyError, TypeError):
    pass
```

New code:
```python
try:
    query_info = await datasette.get_canned_query(database, query_name, request.actor)
    if query_info and "title" in query_info:
        title = query_info["title"]
except (KeyError, TypeError):
    pass
```

### Update render functions to async

If your plugin's render function needs to call `datasette.get_canned_query()` or other async Datasette methods, it must be declared as async:

Old code:
```python
def render_atom(datasette, request, sql, columns, rows, database, table, query_name, view_name, data):
    # ...
    if query_name:
        title = datasette.metadata(database=database)["queries"][query_name]["title"]
```

New code:
```python
async def render_atom(datasette, request, sql, columns, rows, database, table, query_name, view_name, data):
    # ...
    if query_name:
        query_info = await datasette.get_canned_query(database, query_name, request.actor)
        if query_info and "title" in query_info:
            title = query_info["title"]
```

### Update query URLs in tests

Datasette now redirects `?sql=` parameters from database pages to the query view:

Old code:
```python
response = await ds.client.get("/_memory.atom?sql=select+1")
```

New code:
```python
response = await ds.client.get("/_memory/-/query.atom?sql=select+1")
```
