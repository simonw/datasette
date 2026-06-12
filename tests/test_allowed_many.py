"""
Tests for the datasette.allowed_many() batch permission API, which
resolves multiple actions against one resource in a single internal
database query. datasette.allowed() is implemented on top of it, so
both entry points share one resolution code path.
"""

import pytest
import pytest_asyncio
from datasette.app import Datasette
from datasette.permissions import PermissionSQL, SkipPermissions
from datasette.resources import DatabaseResource, TableResource
from datasette import hookimpl


@pytest_asyncio.fixture
async def ds():
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("analytics")
    await db.execute_write("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)")
    await db.execute_write("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY)")
    await ds._refresh_schemas()
    return ds


class MatrixRulesPlugin:
    """Different rules per action for actor carol, to exercise resolution."""

    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        if not actor or actor.get("id") != "carol":
            return None
        if action == "view-table":
            return PermissionSQL(sql="""
                SELECT NULL AS parent, NULL AS child, 1 AS allow, 'global allow' AS reason
                UNION ALL
                SELECT 'analytics' AS parent, 'sensitive' AS child, 0 AS allow, 'deny sensitive' AS reason
                """)
        if action == "insert-row":
            return PermissionSQL(
                sql="SELECT 'analytics' AS parent, NULL AS child, 1 AS allow, 'analytics writes' AS reason"
            )
        # Everything else: no opinion (implicit deny unless defaults allow)
        return None


@pytest.mark.asyncio
async def test_allowed_many_basic(ds):
    plugin = MatrixRulesPlugin()
    ds.pm.register(plugin, name="matrix")
    try:
        results = await ds.allowed_many(
            actions=["view-table", "insert-row", "drop-table"],
            resource=TableResource("analytics", "users"),
            actor={"id": "carol"},
        )
        assert results == {
            "view-table": True,
            "insert-row": True,
            "drop-table": False,
        }
        # Child-level deny beats global allow
        sensitive = await ds.allowed_many(
            actions=["view-table"],
            resource=TableResource("analytics", "sensitive"),
            actor={"id": "carol"},
        )
        assert sensitive == {"view-table": False}
    finally:
        ds.pm.unregister(name="matrix")


@pytest.mark.asyncio
async def test_allowed_many_matches_allowed(ds):
    """Every action resolved by allowed_many() must match allowed()."""
    plugin = MatrixRulesPlugin()
    ds.pm.register(plugin, name="matrix")
    try:
        all_actions = list(ds.actions)
        for resource in (
            TableResource("analytics", "users"),
            TableResource("analytics", "sensitive"),
            DatabaseResource("analytics"),
        ):
            batched = await ds.allowed_many(
                actions=all_actions, resource=resource, actor={"id": "carol"}
            )
            assert set(batched) == set(all_actions)
            for action in all_actions:
                individual = await ds.allowed(
                    action=action, resource=resource, actor={"id": "carol"}
                )
                assert (
                    batched[action] == individual
                ), f"Mismatch for {action} on {resource}"
    finally:
        ds.pm.unregister(name="matrix")


@pytest.mark.asyncio
async def test_allowed_many_unknown_action_raises(ds):
    with pytest.raises(ValueError, match="Unknown action"):
        await ds.allowed_many(
            actions=["view-table", "no-such-action"],
            resource=TableResource("analytics", "users"),
            actor=None,
        )


@pytest.mark.asyncio
async def test_allowed_many_empty_actions(ds):
    assert (
        await ds.allowed_many(
            actions=[], resource=TableResource("analytics", "users"), actor=None
        )
        == {}
    )


class AlsoRequiresRulesPlugin:
    """dave: store-query allowed but execute-sql explicitly denied.
    erin: store-query allowed (execute-sql stays default-allowed)."""

    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        actor_id = actor.get("id") if actor else None
        if actor_id == "dave":
            if action == "store-query":
                return PermissionSQL(
                    sql="SELECT NULL AS parent, NULL AS child, 1 AS allow, 'dave can store' AS reason"
                )
            if action == "execute-sql":
                return PermissionSQL(
                    sql="SELECT NULL AS parent, NULL AS child, 0 AS allow, 'dave no sql' AS reason"
                )
        if actor_id == "erin" and action == "store-query":
            return PermissionSQL(
                sql="SELECT NULL AS parent, NULL AS child, 1 AS allow, 'erin can store' AS reason"
            )
        return None


@pytest.mark.asyncio
async def test_allowed_many_also_requires(ds):
    # store-query also_requires execute-sql, which also_requires view-database
    plugin = AlsoRequiresRulesPlugin()
    ds.pm.register(plugin, name="also_requires")
    try:
        resource = DatabaseResource("analytics")
        dave = await ds.allowed_many(
            actions=["store-query", "execute-sql", "view-database"],
            resource=resource,
            actor={"id": "dave"},
        )
        # execute-sql denied, so store-query must be denied too
        assert dave == {
            "store-query": False,
            "execute-sql": False,
            "view-database": True,
        }
        erin = await ds.allowed_many(
            actions=["store-query"], resource=resource, actor={"id": "erin"}
        )
        assert erin == {"store-query": True}
        # Must match the single-check path
        assert (
            await ds.allowed(
                action="store-query", resource=resource, actor={"id": "dave"}
            )
            is False
        )
        assert (
            await ds.allowed(
                action="store-query", resource=resource, actor={"id": "erin"}
            )
            is True
        )
    finally:
        ds.pm.unregister(name="also_requires")


@pytest.mark.asyncio
async def test_allowed_many_respects_restrictions(ds):
    """Token-style _r restrictions are enforced within the batch."""
    actor = {"id": "root", "_r": {"d": {"analytics": ["vt"]}}}
    results = await ds.allowed_many(
        actions=["view-table", "drop-table"],
        resource=TableResource("analytics", "users"),
        actor=actor,
    )
    # root could normally do both, but the token only allows view-table
    # on the analytics database
    assert results == {"view-table": True, "drop-table": False}
    other_db = await ds.allowed_many(
        actions=["view-table"],
        resource=TableResource("production", "stuff"),
        actor=actor,
    )
    assert other_db == {"view-table": False}
    # Equivalence with allowed()
    assert (
        await ds.allowed(
            action="view-table",
            resource=TableResource("analytics", "users"),
            actor=actor,
        )
        is True
    )
    assert (
        await ds.allowed(
            action="drop-table",
            resource=TableResource("analytics", "users"),
            actor=actor,
        )
        is False
    )


class ParamCollisionPlugin:
    """Same parameter name with a different value for every action."""

    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        if not actor or actor.get("id") != "paula":
            return None
        flag = 1 if action in ("drop-table", "insert-row") else 0
        return PermissionSQL(
            sql="SELECT NULL AS parent, NULL AS child, :flag AS allow, 'flagged' AS reason",
            params={"flag": flag},
        )


@pytest.mark.asyncio
async def test_allowed_many_namespaces_params_across_actions(ds):
    """Many actions whose rules use identical param names must not collide."""
    plugin = ParamCollisionPlugin()
    ds.pm.register(plugin, name="collision")
    try:
        all_actions = list(ds.actions)
        assert len(all_actions) >= 15
        resource = TableResource("analytics", "users")
        results = await ds.allowed_many(
            actions=all_actions, resource=resource, actor={"id": "paula"}
        )
        # Spot-check: only the flagged actions resolve True
        assert results["drop-table"] is True
        assert results["create-table"] is False
        # Full equivalence against single checks
        for action in all_actions:
            assert results[action] == await ds.allowed(
                action=action, resource=resource, actor={"id": "paula"}
            ), f"Mismatch for {action}"
    finally:
        ds.pm.unregister(name="collision")


@pytest.mark.asyncio
async def test_allowed_many_single_internal_db_query(ds):
    internal_db = ds.get_internal_database()
    calls = []
    original_execute = internal_db.execute

    async def counting_execute(sql, params=None, **kwargs):
        calls.append(sql)
        return await original_execute(sql, params, **kwargs)

    internal_db.execute = counting_execute
    try:
        results = await ds.allowed_many(
            actions=["view-table", "insert-row", "delete-row", "drop-table"],
            resource=TableResource("analytics", "users"),
            actor={"id": "root", "_r": {"d": {"analytics": ["vt"]}}},
        )
        assert len(results) == 4
        assert len(calls) == 1
    finally:
        internal_db.execute = original_execute


@pytest.mark.asyncio
async def test_allowed_many_no_query_when_no_rules(ds):
    """Actions with no rules from any plugin are denied without SQL.

    Restrictions can only restrict, never grant, so an action with no
    rule rows is always False - it should not contribute to the query,
    and if no action has rules there should be no query at all."""
    internal_db = ds.get_internal_database()
    calls = []
    original_execute = internal_db.execute

    async def counting_execute(sql, params=None, **kwargs):
        calls.append(sql)
        return await original_execute(sql, params, **kwargs)

    internal_db.execute = counting_execute
    try:
        # bob gets no rules at all for these write actions
        results = await ds.allowed_many(
            actions=["drop-table", "delete-row"],
            resource=TableResource("analytics", "users"),
            actor={"id": "bob"},
        )
        assert results == {"drop-table": False, "delete-row": False}
        assert len(calls) == 0
        # A mixed batch still needs exactly one query
        calls.clear()
        results = await ds.allowed_many(
            actions=["view-table", "drop-table"],
            resource=TableResource("analytics", "users"),
            actor={"id": "bob"},
        )
        assert results == {"view-table": True, "drop-table": False}
        assert len(calls) == 1
    finally:
        internal_db.execute = original_execute


@pytest.mark.asyncio
async def test_allowed_many_global_actions_without_resource(ds):
    results = await ds.allowed_many(
        actions=["view-instance", "permissions-debug"],
        actor={"id": "root"},
    )
    assert results["view-instance"] is True
    # Equivalence with single checks for global actions
    for action in ("view-instance", "permissions-debug"):
        assert results[action] == await ds.allowed(action=action, actor={"id": "root"})
    anon = await ds.allowed_many(actions=["permissions-debug"], actor=None)
    assert anon == {"permissions-debug": False}


@pytest.mark.asyncio
async def test_allowed_many_skip_permission_checks(ds):
    with SkipPermissions():
        results = await ds.allowed_many(
            actions=["view-table", "drop-table"],
            resource=TableResource("analytics", "users"),
            actor=None,
        )
    assert results == {"view-table": True, "drop-table": True}
