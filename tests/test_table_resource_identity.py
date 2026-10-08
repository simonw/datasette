"""Table permission identities must agree with SQLite identifier resolution."""

import uuid
from unittest.mock import AsyncMock

import pytest

from datasette import hookimpl
from datasette.app import Datasette
from datasette.default_permissions import restrictions_allow_action
from datasette.permissions import Action, PermissionSQL, _permission_check_cache
from datasette.resources import QueryResource, TableResource
from datasette.utils.actions_sql import explain_permission_for_resource
from datasette.utils.asgi import Forbidden
from datasette.utils.permissions import gather_permission_sql_from_hooks


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["table", "view"])
@pytest.mark.parametrize("spelling", ["Inventory", "inventory", "INVENTORY"])
@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("rule_spelling", ["Inventory", "iNvEnToRy"])
async def test_table_permission_identity(
    kind, spelling, allowed, rule_spelling, monkeypatch
):
    ds = Datasette(
        config={
            "permissions": {"view-table": not allowed, "insert-row": not allowed},
            "databases": {
                "data": {
                    "tables": {
                        rule_spelling: {
                            "permissions": {
                                "view-table": allowed,
                                "insert-row": allowed,
                            }
                        }
                    }
                }
            },
        }
    )
    db = ds.add_memory_database("identity_" + uuid.uuid4().hex, name="data")
    cache_token = _permission_check_cache.set({})
    try:
        await db.execute_write(
            "create table Inventory (id integer primary key)"
            if kind == "table"
            else "create view Inventory as select 1 as id"
        )
        await ds.invoke_startup()
        # Identity matching needs no target-schema lookup. Derived-table
        # permissions may still check the schema version. All spellings and
        # API entry points should share the existing permission result cache.
        target_execute = AsyncMock(wraps=db.execute)
        monkeypatch.setattr(db, "execute", target_execute)
        internal_execute = AsyncMock(wraps=ds.get_internal_database().execute)
        monkeypatch.setattr(ds.get_internal_database(), "execute", internal_execute)
        resource = TableResource("data", spelling)
        assert await ds.allowed_many(
            actions=["view-table", "insert-row"], resource=resource
        ) == {"view-table": allowed, "insert-row": allowed}
        assert await ds.allowed(action="view-table", resource=resource) is allowed
        assert await ds.check_visibility(None, "view-table", resource) == (
            allowed,
            False,
        )
        if allowed:
            await ds.ensure_permission(action="view-table", resource=resource)
        else:
            with pytest.raises(Forbidden):
                await ds.ensure_permission(action="view-table", resource=resource)
        assert resource.child == spelling  # Do not mutate caller-owned resources.
        for variant in ("Inventory", "inventory", "INVENTORY"):
            assert (
                await ds.allowed(
                    action="view-table", resource=TableResource("data", variant)
                )
                is allowed
            )
        assert internal_execute.await_count == 1
        assert all(
            call.args[0] == "PRAGMA schema_version"
            for call in target_execute.await_args_list
        )
        assert all(key[3] == "inventory" for key in _permission_check_cache.get())
    finally:
        _permission_check_cache.reset(cache_token)
        ds.close()


@pytest.mark.asyncio
async def test_other_permission_identities_are_preserved():
    ds = Datasette(
        config={
            "databases": {
                "data": {
                    "tables": {
                        "Äpfel": {"permissions": {"view-table": False}},
                        "Future": {"permissions": {"view-table": False}},
                    },
                    "queries": {
                        "Report": {
                            "sql": "select 1",
                            "permissions": {"view-query": False},
                        },
                        "report": {
                            "sql": "select 1",
                            "permissions": {"view-query": True},
                        },
                    },
                }
            }
        }
    )
    db = ds.add_memory_database("identity_" + uuid.uuid4().hex, name="data")
    try:
        await db.execute_write('create table "Äpfel" (id integer primary key)')
        await db.execute_write('create table "äpfel" (id integer primary key)')
        await db.execute_write("create table Report (id integer primary key)")
        await ds.invoke_startup()
        # SQLite folds ASCII identifier casing, not Unicode casing.
        for name, expected in [
            ("ÄPFEL", False),
            ("äPFEL", True),
            ("Future", False),
            ("future", False),
        ]:
            assert (
                await ds.allowed(
                    action="view-table", resource=TableResource("data", name)
                )
                is expected
            )
        # Query names remain case-sensitive even when a table has the same name.
        for name, expected in [("Report", False), ("report", True)]:
            assert (
                await ds.allowed(
                    action="view-query", resource=QueryResource("data", name)
                )
                is expected
            )
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("allow", [True, False, {"id": "reader"}])
async def test_table_listings_and_explanations(allow):
    ds = Datasette(
        config={
            "databases": {
                "data": {
                    "tables": {
                        "inventory": {"permissions": {"view-table": allow}},
                    }
                }
            }
        }
    )
    db = ds.add_memory_database("identity_" + uuid.uuid4().hex, name="data")
    try:
        await db.execute_write("create table Inventory (id integer primary key)")
        await db.execute_write("create view InventoryView as select id from Inventory")
        await ds.invoke_startup()
        for actor in (None, {"id": "reader"}):
            expected = allow is True or (isinstance(allow, dict) and actor == allow)
            explanation = await explain_permission_for_resource(
                datasette=ds,
                actor=actor,
                action="view-table",
                parent="data",
                child="INVENTORY",
            )
            assert explanation["allowed"] is expected
            assert explanation["winning_scope"] == "resource"
            assert any(
                "data/inventory" in rule["reason"]
                for rule in explanation["matched_rules"]
            )
            page = await ds.allowed_resources(
                "view-table",
                actor,
                parent="data",
                include_is_private=True,
                include_reasons=True,
                limit=1,
            )
            resources = [resource async for resource in page.all()]
            matching = [r for r in resources if r.child == "Inventory"]
            assert bool(matching) is expected
            assert len(matching) <= 1
            if matching:
                assert matching[0].private is isinstance(allow, dict)
            assert any(r.child == "InventoryView" for r in resources)
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("deny_first", [True, False])
async def test_case_variant_rules_deny_wins(deny_first):
    rules = [("inventory", False), ("INVENTORY", True)]
    if not deny_first:
        rules.reverse()
    ds = Datasette(
        config={
            "databases": {
                "data": {
                    "tables": {
                        name: {"permissions": {"view-table": allow}}
                        for name, allow in rules
                    }
                }
            }
        }
    )
    db = ds.add_memory_database("identity_" + uuid.uuid4().hex, name="data")
    try:
        await db.execute_write("create table Inventory (id integer primary key)")
        await ds.invoke_startup()
        assert not await ds.allowed(
            action="view-table", resource=TableResource("data", "Inventory")
        )
        assert not (
            await ds.allowed_resources(
                "view-table", parent="data", include_is_private=True
            )
        ).resources
        explanation = await explain_permission_for_resource(
            datasette=ds,
            actor=None,
            action="view-table",
            parent="data",
            child="Inventory",
        )
        assert not explanation["allowed"]
        assert any(
            rule["effect"] == "allow" and not rule["decisive"]
            for rule in explanation["matched_rules"]
        )
        assert any(
            rule["effect"] == "deny" and rule["decisive"]
            for rule in explanation["matched_rules"]
        )
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("config_style", ["allow", "permissions"])
@pytest.mark.parametrize("allowed", [True, False])
async def test_case_variant_token_restrictions(config_style, allowed):
    table_config = (
        {"allow": allowed}
        if config_style == "allow"
        else {"permissions": {"view-table": allowed}}
    )
    ds = Datasette(
        config={"databases": {"data": {"tables": {"Inventory": table_config}}}}
    )
    db = ds.add_memory_database("identity_" + uuid.uuid4().hex, name="data")
    actor = {"id": "reader", "_r": {"r": {"data": {"inventory": ["vt"]}}}}
    try:
        await db.execute_write("create table Inventory (id integer primary key)")
        await ds.invoke_startup()
        assert restrictions_allow_action(
            ds, actor["_r"], "view-table", ("data", "INVENTORY")
        )
        assert not restrictions_allow_action(
            ds, actor["_r"], "view-table", ("Data", "Inventory")
        )
        assert (
            await ds.allowed(
                action="view-table",
                resource=TableResource("data", "INVENTORY"),
                actor=actor,
            )
            is allowed
        )
        page = await ds.allowed_resources("view-table", actor, parent="data")
        assert [(r.parent, r.child) for r in page.resources] == (
            [("data", "Inventory")] if allowed else []
        )
        explanation = await explain_permission_for_resource(
            datasette=ds,
            actor=actor,
            action="view-table",
            parent="data",
            child="Inventory",
        )
        assert explanation["restriction_allowed"]
        assert explanation["allowed"] is allowed
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_plugin_restriction_intersection_and_dependencies():
    class Plugin:
        @hookimpl
        def register_actions(self, datasette):
            return [
                Action(
                    name="inspect-inventory",
                    description="Inspect inventory",
                    resource_class=TableResource,
                    also_requires="view-table",
                )
            ]

        @hookimpl
        def permission_resources_sql(self, action):
            if action not in ("view-table", "inspect-inventory"):
                return None
            return [
                PermissionSQL(
                    sql="SELECT 'data' AS parent, 'INVENTORY' AS child, 1 AS allow, 'inventory grant' AS reason",
                    restriction_sql="SELECT 'data' AS parent, 'inventory' AS child",
                ),
                PermissionSQL(
                    restriction_sql="SELECT 'data' AS parent, 'InVeNtOrY' AS child"
                ),
            ]

    ds = Datasette(default_deny=True)
    ds.pm.register(Plugin(), name="identity-test")
    db = ds.add_memory_database("identity_" + uuid.uuid4().hex, name="data")
    try:
        await db.execute_write("create table Inventory (id integer primary key)")
        await db.execute_write("create table Other (id integer primary key)")
        await ds.invoke_startup()
        for action in ("view-table", "inspect-inventory"):
            assert await ds.allowed(
                action=action, resource=TableResource("data", "Inventory")
            )
            assert not await ds.allowed(
                action=action, resource=TableResource("data", "Other")
            )
            resources = (
                await ds.allowed_resources(
                    action, parent="data", include_is_private=True
                )
            ).resources
            assert [r.child for r in resources] == ["Inventory"]
            explanation = await explain_permission_for_resource(
                datasette=ds,
                actor=None,
                action=action,
                parent="data",
                child="Inventory",
            )
            assert explanation["allowed"]
            assert all(item["allowed"] for item in explanation["restrictions"])
    finally:
        ds.pm.unregister(name="identity-test")
        ds.close()


@pytest.mark.asyncio
async def test_shared_plugin_rule_keeps_query_identity_and_original_sql():
    shared = PermissionSQL(
        sql="SELECT 'data' AS parent, 'Inventory' AS child, 0 AS allow, 'shared deny' AS reason"
    )
    original_sql = shared.sql

    class Plugin:
        @hookimpl
        def permission_resources_sql(self, action):
            if action in ("view-table", "view-query"):
                return shared

    ds = Datasette(
        config={
            "databases": {
                "data": {
                    "queries": {
                        "Inventory": "select 1",
                        "inventory": "select 1",
                    }
                }
            }
        }
    )
    ds.add_memory_database("identity_" + uuid.uuid4().hex, name="data")
    ds.pm.register(Plugin(), name="identity-test")
    try:
        await ds.invoke_startup()
        for _ in range(2):
            await gather_permission_sql_from_hooks(
                datasette=ds, actor=None, action="view-table"
            )
        assert shared.sql == original_sql
        assert not await ds.allowed(
            action="view-table", resource=TableResource("data", "inventory")
        )
        assert await ds.allowed(
            action="view-query", resource=QueryResource("data", "inventory")
        )
        assert not await ds.allowed(
            action="view-query", resource=QueryResource("data", "Inventory")
        )
        assert await ds.allowed(
            action="view-table", resource=TableResource("Data", "Inventory")
        )
        assert restrictions_allow_action(
            ds,
            {"r": {"data": {"Inventory": ["vq"]}}},
            "view-query",
            ("data", "Inventory"),
        )
        assert not restrictions_allow_action(
            ds,
            {"r": {"data": {"Inventory": ["vq"]}}},
            "view-query",
            ("data", "inventory"),
        )
    finally:
        ds.pm.unregister(name="identity-test")
        ds.close()
