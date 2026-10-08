"""Permission rules are attributed to the plugin that returned them (#2978).

``gather_permission_sql_from_hooks()`` used to pair each hook result with a
hook implementation by index into ``get_hookimpls()``, but pluggy calls
implementations in reverse registration order — so every rule whose plugin
did not set ``PermissionSQL.source`` explicitly was attributed to whichever
plugin happened to sit at that index.
"""

import pytest

from datasette import hookimpl
from datasette.app import Datasette
from datasette.permissions import PermissionSQL
from datasette.utils.permissions import gather_permission_sql_from_hooks


class AlphaPlugin:
    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        if action == "view-table":
            return PermissionSQL(sql="select 'a' as parent, 'x' as child, 1 as allow, 'from-alpha' as reason")


class BetaPlugin:
    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        if action == "view-table":
            return PermissionSQL(sql="select 'b' as parent, 'y' as child, 1 as allow, 'from-beta' as reason")


@pytest.mark.asyncio
async def test_permission_sql_source_names_the_calling_plugin():
    ds = Datasette(memory=True)
    ds.pm.register(AlphaPlugin(), name="alpha")
    ds.pm.register(BetaPlugin(), name="beta")
    try:
        await ds.invoke_startup()
        sqls = await gather_permission_sql_from_hooks(datasette=ds, actor={"id": "alice"}, action="view-table")
    finally:
        ds.pm.unregister(name="beta")
        ds.pm.unregister(name="alpha")
        ds.close()
    by_plugin = {}
    for permission_sql in sqls:
        if "'a' as parent" in (permission_sql.sql or ""):
            by_plugin["alpha"] = permission_sql.source
        elif "'b' as parent" in (permission_sql.sql or ""):
            by_plugin["beta"] = permission_sql.source
    assert by_plugin == {"alpha": "alpha", "beta": "beta"}


@pytest.mark.asyncio
async def test_explicit_permission_sql_source_is_preserved():
    class ExplicitPlugin:
        @hookimpl
        def permission_resources_sql(self, datasette, actor, action):
            if action == "view-table":
                return PermissionSQL(sql="select 'e' as parent, 'z' as child, 1 as allow, 'explicit' as reason", source="my-source")

    ds = Datasette(memory=True)
    ds.pm.register(ExplicitPlugin(), name="explicit")
    try:
        await ds.invoke_startup()
        sqls = await gather_permission_sql_from_hooks(datasette=ds, actor={"id": "alice"}, action="view-table")
    finally:
        ds.pm.unregister(name="explicit")
        ds.close()
    sources = [permission_sql.source for permission_sql in sqls if "'e' as parent" in (permission_sql.sql or "")]
    assert sources == ["my-source"]
