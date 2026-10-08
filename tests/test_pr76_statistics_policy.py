"""Statistics access policy and plugin replacement coverage for PR #76."""

import uuid

import pytest

from datasette import hookimpl
from datasette.app import Datasette
from datasette.permissions import PermissionSQL
from datasette.resources import TableResource


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, "global", "database", "table", "root"])
async def test_statistics_denied_despite_allow_rules(scope):
    config = {"databases": {"data": {"tables": {"sqlite_stat1": {}}}}}
    grant = {"view-table": True}
    if scope == "global":
        config["permissions"] = grant
    elif scope == "database":
        config["databases"]["data"]["permissions"] = grant
    elif scope == "table":
        config["databases"]["data"]["tables"]["sqlite_stat1"]["permissions"] = grant
    ds = Datasette(memory=True, config=config)
    ds.root_enabled = scope == "root"
    actor = {"id": "root"} if scope == "root" else {"id": "reader"}
    db = ds.add_memory_database(uuid.uuid4().hex, name="data")
    await db.execute_write("create table items(value text)")
    await db.execute_write("create index items_value on items(value)")
    await db.execute_write("insert into items values ('example')")
    await db.execute_write("analyze")
    await ds.invoke_startup()
    try:
        assert "view-sqlite-statistics" not in ds.actions
        for name in ("sqlite_stat1", "SQLITE_STAT1"):
            assert not await ds.allowed(
                action="view-table", resource=TableResource("data", name), actor=actor
            )
        for suffix in ("", ".json", ".csv"):
            assert (
                await ds.client.get(f"/data/sqlite_stat1{suffix}", actor=actor)
            ).status_code == 403
        resources = await ds.allowed_resources("view-table", parent="data", actor=actor)
        assert "sqlite_stat1" not in {r.child for r in resources.resources}
        assert "items" in {r.child for r in resources.resources}
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table", ["sqlite_stat1", "sqlite_stat2", "sqlite_stat3", "sqlite_stat4"]
)
@pytest.mark.parametrize("default_deny", [False, True])
async def test_statistics_names_denied(table, default_deny):
    ds = Datasette(memory=True, default_deny=default_deny)
    ds.root_enabled = True
    await ds.invoke_startup()
    try:
        for name in (table, table.upper()):
            assert not await ds.allowed(
                action="view-table",
                resource=TableResource("_memory", name),
                actor={"id": "root"},
            )
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_plugin_can_replace_statistics_policy():
    class ReplacementPolicy:
        @hookimpl
        def permission_resources_sql(self, action, actor):
            if action == "view-table":
                return PermissionSQL(
                    sql="SELECT 'data' AS parent, 'sqlite_stat1' AS child, :statistics_allowed AS allow, 'custom statistics policy' AS reason",
                    params={"statistics_allowed": int(actor == {"id": "reader"})},
                )

    ds = Datasette(memory=True)
    db = ds.add_memory_database(uuid.uuid4().hex, name="data")
    await db.execute_write("create table items(value text)")
    await db.execute_write("analyze")
    await ds.invoke_startup()
    name = "datasette.default_permissions.sqlite_statistics"
    original = ds.pm.unregister(name=name)
    assert original is not None
    replacement = ReplacementPolicy()
    ds.pm.register(replacement, name="test-replacement-statistics-policy")
    try:
        actor = {"id": "reader"}
        assert await ds.allowed(
            action="view-table",
            resource=TableResource("data", "sqlite_stat1"),
            actor=actor,
        )
        assert not await ds.allowed(
            action="view-table", resource=TableResource("data", "sqlite_stat1")
        )
        resources = await ds.allowed_resources(
            "view-table", parent="data", actor=actor, include_is_private=True
        )
        stats = [r for r in resources.resources if r.child == "sqlite_stat1"]
        assert len(stats) == 1 and stats[0].private
        assert (
            await ds.client.get("/data/sqlite_stat1.json", actor=actor)
        ).status_code == 200
        assert (await ds.client.get("/data/sqlite_stat1.json")).status_code == 403
    finally:
        ds.pm.unregister(replacement)
        ds.pm.register(original, name=name)
        ds.close()
