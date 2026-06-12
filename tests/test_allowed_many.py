"""
Tests for request-scoped permission check memoization and the
datasette.allowed_many() batch permission API.

Layer 1: per-request cache consulted by datasette.allowed()
Layer 2: allowed_many() resolves multiple actions in one internal-DB query
Layer 3: table/database views precompute all registered actions before
         invoking table_actions/database_actions plugin hooks
"""

import pytest
import pytest_asyncio
from datasette.app import Datasette
from datasette.permissions import (
    PermissionSQL,
    SkipPermissions,
    _permission_check_cache,
)
from datasette.resources import DatabaseResource, TableResource
from datasette import hookimpl


class CountingRulesPlugin:
    """Counts permission_resources_sql gathers and grants rules for alice."""

    def __init__(self):
        self.calls = []

    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        actor_id = actor.get("id") if actor else None
        self.calls.append((actor_id, action))
        if actor_id == "alice":
            return PermissionSQL(
                sql="SELECT NULL AS parent, NULL AS child, 1 AS allow, 'alice allowed' AS reason"
            )
        return None

    def count(self, actor_id=None, action=None):
        return len(
            [
                (a, c)
                for a, c in self.calls
                if (actor_id is None or a == actor_id)
                and (action is None or c == action)
            ]
        )


@pytest_asyncio.fixture
async def ds():
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("analytics")
    await db.execute_write("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)")
    await db.execute_write("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY)")
    await ds._refresh_schemas()
    return ds


@pytest_asyncio.fixture
async def counting_ds(ds):
    plugin = CountingRulesPlugin()
    ds.pm.register(plugin, name="counting")
    try:
        yield ds, plugin
    finally:
        ds.pm.unregister(name="counting")


# ----------------------------------------------------------------------
# Layer 1: request-scoped memoization
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_memoized_when_cache_active(counting_ds):
    ds, plugin = counting_ds
    resource = TableResource("analytics", "users")
    token = _permission_check_cache.set({})
    try:
        first = await ds.allowed(
            action="view-table", resource=resource, actor={"id": "alice"}
        )
        gathers_after_first = plugin.count(actor_id="alice", action="view-table")
        assert gathers_after_first > 0
        second = await ds.allowed(
            action="view-table", resource=resource, actor={"id": "alice"}
        )
        assert first is True
        assert second is True
        # The second identical check must not gather hooks again
        assert plugin.count(actor_id="alice", action="view-table") == (
            gathers_after_first
        )
    finally:
        _permission_check_cache.reset(token)


@pytest.mark.asyncio
async def test_allowed_not_memoized_without_cache(counting_ds):
    ds, plugin = counting_ds
    resource = TableResource("analytics", "users")
    assert _permission_check_cache.get() is None
    await ds.allowed(action="view-table", resource=resource, actor={"id": "alice"})
    first_count = plugin.count(actor_id="alice", action="view-table")
    await ds.allowed(action="view-table", resource=resource, actor={"id": "alice"})
    # No request cache active - hooks gathered again
    assert plugin.count(actor_id="alice", action="view-table") == first_count * 2


@pytest.mark.asyncio
async def test_cache_keyed_on_full_actor_identity(counting_ds):
    """Interleaved checks for different actors never share cache entries."""
    # Uses drop-table because default permissions deny it to non-root actors
    ds, plugin = counting_ds
    resource = TableResource("analytics", "users")
    token = _permission_check_cache.set({})
    try:
        assert (
            await ds.allowed(
                action="drop-table", resource=resource, actor={"id": "alice"}
            )
            is True
        )
        assert (
            await ds.allowed(
                action="drop-table", resource=resource, actor={"id": "bob"}
            )
            is False
        )
        # Repeat interleaved - cached results must stay correct per actor
        assert (
            await ds.allowed(
                action="drop-table", resource=resource, actor={"id": "alice"}
            )
            is True
        )
        assert (
            await ds.allowed(
                action="drop-table", resource=resource, actor={"id": "bob"}
            )
            is False
        )
        # Actors differing in fields beyond id must not collide either
        assert (
            await ds.allowed(
                action="drop-table",
                resource=resource,
                actor={"id": "alice", "_r": {"a": []}},
            )
            is False
        )
    finally:
        _permission_check_cache.reset(token)


@pytest.mark.asyncio
async def test_cache_keyed_on_resource(counting_ds):
    ds, plugin = counting_ds
    token = _permission_check_cache.set({})
    try:
        await ds.allowed(
            action="view-table",
            resource=TableResource("analytics", "users"),
            actor={"id": "alice"},
        )
        count = plugin.count(actor_id="alice", action="view-table")
        # Different resource - must not be served from cache
        await ds.allowed(
            action="view-table",
            resource=TableResource("analytics", "events"),
            actor={"id": "alice"},
        )
        assert plugin.count(actor_id="alice", action="view-table") == count * 2
    finally:
        _permission_check_cache.reset(token)


@pytest.mark.asyncio
async def test_skip_permission_checks_bypasses_cache(counting_ds):
    ds, plugin = counting_ds
    resource = TableResource("analytics", "users")
    token = _permission_check_cache.set({})
    try:
        with SkipPermissions():
            assert (
                await ds.allowed(
                    action="drop-table", resource=resource, actor={"id": "bob"}
                )
                is True
            )
        # The skip-mode True must not have been cached
        assert (
            await ds.allowed(
                action="drop-table", resource=resource, actor={"id": "bob"}
            )
            is False
        )
    finally:
        _permission_check_cache.reset(token)


# ----------------------------------------------------------------------
# Layer 2: allowed_many()
# ----------------------------------------------------------------------


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
    """40+ actions whose rules use identical param names must not collide."""
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
async def test_allowed_many_seeds_request_cache(counting_ds):
    ds, plugin = counting_ds
    resource = TableResource("analytics", "users")
    actions = ["view-table", "insert-row", "drop-table"]
    token = _permission_check_cache.set({})
    try:
        await ds.allowed_many(actions=actions, resource=resource, actor={"id": "alice"})
        gathers = plugin.count(actor_id="alice")
        assert gathers > 0
        for action in actions:
            await ds.allowed(action=action, resource=resource, actor={"id": "alice"})
        # Every allowed() call must have been served from the seeded cache
        assert plugin.count(actor_id="alice") == gathers
    finally:
        _permission_check_cache.reset(token)


@pytest.mark.asyncio
async def test_allowed_many_skip_permission_checks(ds):
    with SkipPermissions():
        results = await ds.allowed_many(
            actions=["view-table", "drop-table"],
            resource=TableResource("analytics", "users"),
            actor=None,
        )
    assert results == {"view-table": True, "drop-table": True}


# ----------------------------------------------------------------------
# Layer 3: precompute before table_actions / database_actions hooks
# ----------------------------------------------------------------------


class ActionHooksPlugin:
    """Plugin hooks that make allowed() checks, like real action plugins do."""

    @hookimpl
    def table_actions(self, datasette, actor, database, table):
        async def inner():
            links = []
            if await datasette.allowed(
                action="drop-table",
                resource=TableResource(database, table),
                actor=actor,
            ):
                links.append(
                    {"href": "/drop", "label": "Drop this table (test-plugin)"}
                )
            if await datasette.allowed(
                action="create-table",
                resource=DatabaseResource(database),
                actor=actor,
            ):
                links.append(
                    {"href": "/create", "label": "Create a table (test-plugin)"}
                )
            return links

        return inner

    @hookimpl
    def database_actions(self, datasette, actor, database):
        async def inner():
            if await datasette.allowed(
                action="create-table",
                resource=DatabaseResource(database),
                actor=actor,
            ):
                return [{"href": "/create", "label": "Create a table (test-plugin)"}]
            return []

        return inner


@pytest_asyncio.fixture
async def spying_ds(ds, monkeypatch):
    """ds with the ActionHooksPlugin plus a spy recording every batch of
    actions sent to check_permissions_for_actions."""
    from datasette.utils import actions_sql

    plugin = ActionHooksPlugin()
    ds.pm.register(plugin, name="action_hooks")
    ds.root_enabled = True
    recorded = []
    original = actions_sql.check_permissions_for_actions

    async def spy(**kwargs):
        recorded.append(kwargs["actions"])
        return await original(**kwargs)

    monkeypatch.setattr(actions_sql, "check_permissions_for_actions", spy)
    try:
        yield ds, recorded
    finally:
        ds.pm.unregister(name="action_hooks")


@pytest.mark.asyncio
async def test_table_page_precomputes_action_permissions(spying_ds):
    ds, recorded = spying_ds
    cookies = {"ds_actor": ds.client.actor_cookie({"id": "root"})}
    response = await ds.client.get("/analytics/users", cookies=cookies)
    assert response.status_code == 200
    # The plugin's permission checks were served from the precomputed batch
    assert "Drop this table (test-plugin)" in response.text
    assert "Create a table (test-plugin)" in response.text
    # One batch covered the table-level actions for the table resource,
    # and one covered the database-level actions for the database resource
    batches = [batch for batch in recorded if len(batch) > 1]
    assert any("drop-table" in batch for batch in batches)
    assert any("create-table" in batch for batch in batches)
    # The precompute is scoped to actions relevant to each resource:
    # no global or query-level actions in any batch, and no mixing of
    # table-level and database-level actions
    for batch in batches:
        assert "view-instance" not in batch
        assert "view-query" not in batch
        assert not ("drop-table" in batch and "create-table" in batch)
    # The hook's own allowed() calls hit the cache - no single-action
    # fallback queries for the actions it checked
    assert ["drop-table"] not in recorded
    assert ["create-table"] not in recorded


@pytest.mark.asyncio
async def test_database_page_precomputes_action_permissions(spying_ds):
    ds, recorded = spying_ds
    cookies = {"ds_actor": ds.client.actor_cookie({"id": "root"})}
    response = await ds.client.get("/analytics", cookies=cookies)
    assert response.status_code == 200
    assert "Create a table (test-plugin)" in response.text
    batches = [batch for batch in recorded if len(batch) > 1]
    assert any("create-table" in batch for batch in batches)
    # Scoped to database-level actions only
    for batch in batches:
        assert "view-instance" not in batch
        assert "drop-table" not in batch
    assert ["create-table"] not in recorded


@pytest.mark.asyncio
async def test_cache_does_not_leak_across_requests(counting_ds):
    ds, plugin = counting_ds
    cookies = {"ds_actor": ds.client.actor_cookie({"id": "alice"})}
    response = await ds.client.get("/analytics/users.json", cookies=cookies)
    assert response.status_code == 200
    first_request_gathers = plugin.count(actor_id="alice", action="view-table")
    assert first_request_gathers > 0
    response = await ds.client.get("/analytics/users.json", cookies=cookies)
    assert response.status_code == 200
    # Second request must re-gather (fresh cache), not reuse the first one
    assert (
        plugin.count(actor_id="alice", action="view-table") == first_request_gathers * 2
    )
