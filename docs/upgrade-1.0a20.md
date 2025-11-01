---
orphan: true
---

# Datasette 1.0a20 plugin upgrade guide

<!-- START UPGRADE 1.0a20 -->

Datasette 1.0a20 makes some breaking changes to Datasette's permission system. Plugins need to be updated if they use any of the following:

- The `register_permissions()` plugin  hook - this should be replaced with `register_actions`
- The `permission_allowed()` plugin hook - this should be upgraded to `permission_resources_sql()`.
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
            name="datasette-pins-write",
            abbr=None,
            description="Can pin, unpin, and re-order pins for datasette-pins",
            takes_database=False,
            takes_resource=False,
            default=False,
        ),
        Permission(
            name="datasette-pins-read",
            abbr=None,
            description="Can read pinned items.",
            takes_database=False,
            takes_resource=False,
            default=False,
        ),
    ]
```
The new `Action` does not have a `default=` parameter, and `takes_database` and `takes_resource` have been renamed to `takes_parent` and `takes_child. The new code would look like this:

```python
from datasette.permissions import Action

@hookimpl
def register_actions(datasette):
    return [
        Action(
            name="datasette-pins-write",
            abbr=None,
            description="Can pin, unpin, and re-order pins for datasette-pins",
            takes_parent=False,
            takes_child=False,
            default=False,
        ),
        Action(
            name="datasette-pins-read",
            abbr=None,
            description="Can read pinned items.",
            takes_parent=False,
            takes_child=False,
            default=False,
        ),
    ]
```

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
response = ds.client.get("/path")
```
