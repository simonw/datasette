"""
Tests for cascading permission resolution logic.

These tests verify the core cascading semantics through the production
code paths (allowed_resources / allowed) rather than through a separate
test-only SQL builder.  Every test registers a lightweight plugin via
``ds.pm.register`` and calls the real ``Datasette.allowed_resources()``
and/or ``Datasette.allowed()`` methods.

Cascading semantics tested:
  1. child (depth 2) > parent (depth 1) > global (depth 0)
  2. DENY beats ALLOW at the same depth
  3. No matching rule → implicit deny
  4. Multiple plugins can contribute rules with independent parameters
  5. :actor, :actor_id, :action are available in SQL
"""

import pytest
import pytest_asyncio
from datasette.app import Datasette
from datasette.permissions import PermissionSQL
from datasette.resources import TableResource, DatabaseResource
from datasette import hookimpl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class PermissionRulesPlugin:
    """Thin shim that delegates to a callback for permission_resources_sql."""

    def __init__(self, rules_callback):
        self.rules_callback = rules_callback

    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        return self.rules_callback(datasette, actor, action)


@pytest_asyncio.fixture
async def ds():
    """
    Create a Datasette instance with catalog tables that mirror the
    original test_utils_permissions layout:

      databases: perm_accounting, perm_hr, perm_analytics
      tables per db: table01..table10
      special tables: perm_accounting/sales, perm_analytics/secret

    Uses default_deny=True so that only the test-registered plugins
    determine permission outcomes (no built-in default-allow rules).

    Database names are prefixed with ``perm_`` to avoid collisions with
    other test fixtures that create memory databases in the same process.
    """
    instance = Datasette(default_deny=True)
    await instance.invoke_startup()

    per_parent = 10
    parents = ["perm_accounting", "perm_hr", "perm_analytics"]
    specials = {
        "perm_accounting": ["sales"],
        "perm_analytics": ["secret"],
        "perm_hr": [],
    }

    for parent in parents:
        db = instance.add_memory_database(parent)
        base_tables = [f"table{i:02d}" for i in range(1, per_parent + 1)]
        for s in specials.get(parent, []):
            if s not in base_tables:
                base_tables[0] = s
        for tbl in base_tables:
            await db.execute_write(
                f"CREATE TABLE IF NOT EXISTS [{tbl}] (id INTEGER PRIMARY KEY)"
            )

    await instance._refresh_schemas()
    yield instance
    # Cleanup: remove databases to avoid polluting other tests
    for parent in parents:
        instance.remove_database(parent)


# ---------------------------------------------------------------------------
# Plugin factories — return callables suitable for PermissionRulesPlugin
# ---------------------------------------------------------------------------


def _cb_allow_all_for_user(user):
    """Global allow for a specific user."""

    def cb(datasette, actor, action):
        if not actor or actor.get("id") != user:
            return None
        return PermissionSQL(
            sql=(
                "SELECT NULL AS parent, NULL AS child, 1 AS allow, "
                "'global allow for ' || :_aau_user || ' on ' || :action AS reason"
            ),
            params={"_aau_user": user},
        )

    return cb


def _cb_deny_specific_table(user, parent, child):
    """Child-level deny for a specific user + table."""

    def cb(datasette, actor, action):
        if not actor or actor.get("id") != user:
            return None
        return PermissionSQL(
            sql=(
                "SELECT :_dst_parent AS parent, :_dst_child AS child, 0 AS allow, "
                "'deny ' || :_dst_parent || '/' || :_dst_child || ' for ' || :_dst_user AS reason"
            ),
            params={"_dst_parent": parent, "_dst_child": child, "_dst_user": user},
        )

    return cb


def _cb_org_policy_deny_parent(parent):
    """Unconditional parent-level deny (applies to all actors)."""

    def cb(datasette, actor, action):
        return PermissionSQL(
            sql=(
                "SELECT :_opd_parent AS parent, NULL AS child, 0 AS allow, "
                "'org policy: deny ' || :_opd_parent AS reason"
            ),
            params={"_opd_parent": parent},
        )

    return cb


def _cb_allow_parent_for_user(user, parent):
    """Parent-level allow for a specific user."""

    def cb(datasette, actor, action):
        if not actor or actor.get("id") != user:
            return None
        return PermissionSQL(
            sql=(
                "SELECT :_apu_parent AS parent, NULL AS child, 1 AS allow, "
                "'allow parent ' || :_apu_parent || ' for ' || :_apu_user AS reason"
            ),
            params={"_apu_parent": parent, "_apu_user": user},
        )

    return cb


def _cb_child_allow_for_user(user, parent, child):
    """Child-level allow for a specific user."""

    def cb(datasette, actor, action):
        if not actor or actor.get("id") != user:
            return None
        return PermissionSQL(
            sql=(
                "SELECT :_cau_parent AS parent, :_cau_child AS child, 1 AS allow, "
                "'allow child ' || :_cau_parent || '/' || :_cau_child || ' for ' || :_cau_user AS reason"
            ),
            params={"_cau_parent": parent, "_cau_child": child, "_cau_user": user},
        )

    return cb


def _cb_root_deny_for_all():
    """Unconditional global deny."""

    def cb(datasette, actor, action):
        return PermissionSQL(
            sql=(
                "SELECT NULL AS parent, NULL AS child, 0 AS allow, "
                "'root deny for all' AS reason"
            ),
        )

    return cb


def _cb_conflicting_same_child_rules(user, parent, child):
    """Two plugins: one allow + one deny at the same child level."""

    def cb_allow(datasette, actor, action):
        if not actor or actor.get("id") != user:
            return None
        return PermissionSQL(
            sql=(
                "SELECT :_csca_parent AS parent, :_csca_child AS child, 1 AS allow, "
                "'team grant at child' AS reason"
            ),
            params={"_csca_parent": parent, "_csca_child": child},
        )

    def cb_deny(datasette, actor, action):
        if not actor or actor.get("id") != user:
            return None
        return PermissionSQL(
            sql=(
                "SELECT :_cscd_parent AS parent, :_cscd_child AS child, 0 AS allow, "
                "'exception deny at child' AS reason"
            ),
            params={"_cscd_parent": parent, "_cscd_child": child},
        )

    return cb_allow, cb_deny


def _cb_allow_all_for_action(user, allowed_action):
    """Global allow for a specific user on a specific action only."""

    def cb(datasette, actor, action):
        if action != allowed_action:
            return None
        if not actor or actor.get("id") != user:
            return None
        return PermissionSQL(
            sql=(
                "SELECT NULL AS parent, NULL AS child, 1 AS allow, "
                "'global allow for ' || :_aafa_user || ' on ' || :action AS reason"
            ),
            params={"_aafa_user": user},
        )

    return cb


# ---------------------------------------------------------------------------
# Helpers for asserting results
# ---------------------------------------------------------------------------


def _allowed_set(resources):
    """Convert PaginatedResources.resources to {(parent, child), ...}."""
    return {(r.parent, r.child) for r in resources}


def _allowed_set_for_parent(resources, parent):
    return {(r.parent, r.child) for r in resources if r.parent == parent}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

VIEW_TABLE = "view-table"


@pytest.mark.asyncio
async def test_alice_global_allow_with_specific_denies(ds):
    """
    Alice has global allow, but:
      - accounting/sales is denied at child level
      - hr/* is denied at parent level
    She should see everything except those.
    """
    # Combine three plugin callbacks into one that returns a list
    deny_table_cb = _cb_deny_specific_table("alice", "perm_accounting", "sales")
    deny_parent_cb = _cb_org_policy_deny_parent("perm_hr")
    allow_cb = _cb_allow_all_for_user("alice")

    def combined(datasette, actor, action):
        results = []
        for cb in (allow_cb, deny_table_cb, deny_parent_cb):
            r = cb(datasette, actor, action)
            if r is not None:
                results.append(r)
        return results

    plugin = PermissionRulesPlugin(combined)
    ds.pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "alice"}
        result = await ds.allowed_resources(VIEW_TABLE, actor)
        allowed = _allowed_set(result.resources)

        # accounting/sales should be denied (child deny beats global allow)
        assert ("perm_accounting", "sales") not in allowed

        # All hr tables should be denied (parent deny beats global allow)
        hr_tables = {(p, c) for p, c in allowed if p == "perm_hr"}
        assert len(hr_tables) == 0

        # analytics tables should all be allowed
        analytics_tables = {(p, c) for p, c in allowed if p == "perm_analytics"}
        assert len(analytics_tables) == 10

        # accounting tables (except sales) should be allowed
        accounting_tables = {(p, c) for p, c in allowed if p == "perm_accounting"}
        assert len(accounting_tables) == 9  # 10 - 1 denied

        # Verify with allowed() single-resource checks
        assert not await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_accounting", "sales"),
            actor=actor,
        )
        assert not await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_hr", "table01"),
            actor=actor,
        )
        assert await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_analytics", "table01"),
            actor=actor,
        )
    finally:
        ds.pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_parent_allow_but_child_conflict_deny_wins(ds):
    """
    Carol has parent-level allow on analytics, but there are conflicting
    child-level allow + deny on analytics/secret.  DENY should win.
    hr is parent-level denied.
    """
    cb_allow, cb_deny = _cb_conflicting_same_child_rules(
        "carol", "perm_analytics", "secret"
    )
    allow_parent_cb = _cb_allow_parent_for_user("carol", "perm_analytics")
    deny_parent_cb = _cb_org_policy_deny_parent("perm_hr")

    def combined(datasette, actor, action):
        results = []
        for cb in (deny_parent_cb, allow_parent_cb, cb_allow, cb_deny):
            r = cb(datasette, actor, action)
            if r is not None:
                results.append(r)
        return results

    plugin = PermissionRulesPlugin(combined)
    ds.pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "carol"}
        result = await ds.allowed_resources(VIEW_TABLE, actor)
        analytics_allowed = _allowed_set_for_parent(result.resources, "perm_analytics")

        # analytics/secret should be denied (child deny beats child allow)
        assert ("perm_analytics", "secret") not in analytics_allowed
        # 10 analytics tables, 1 denied
        assert len(analytics_allowed) == 9

        # Verify via allowed()
        assert not await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_analytics", "secret"),
            actor=actor,
        )
        assert await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_analytics", "table02"),
            actor=actor,
        )
    finally:
        ds.pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_specificity_child_allow_overrides_parent_deny(ds):
    """
    analytics is parent-level denied, but alice has a child-level allow
    on analytics/table02.  Child beats parent.
    """
    allow_cb = _cb_allow_all_for_user("alice")
    deny_parent_cb = _cb_org_policy_deny_parent("perm_analytics")
    child_allow_cb = _cb_child_allow_for_user("alice", "perm_analytics", "table02")

    def combined(datasette, actor, action):
        results = []
        for cb in (allow_cb, deny_parent_cb, child_allow_cb):
            r = cb(datasette, actor, action)
            if r is not None:
                results.append(r)
        return results

    plugin = PermissionRulesPlugin(combined)
    ds.pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "alice"}
        result = await ds.allowed_resources(VIEW_TABLE, actor)
        analytics_allowed = _allowed_set_for_parent(result.resources, "perm_analytics")

        # table02 should be allowed (child allow beats parent deny)
        assert ("perm_analytics", "table02") in analytics_allowed
        # All other analytics tables should be denied (parent deny, no child rule)
        assert len(analytics_allowed) == 1

        # Verify via allowed()
        assert await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_analytics", "table02"),
            actor=actor,
        )
        assert not await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_analytics", "table01"),
            actor=actor,
        )
    finally:
        ds.pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_root_deny_all_but_parent_allow_rescues_specific_parent(ds):
    """
    Global deny for all, but bob has parent-level allow on accounting.
    Parent beats global.
    """
    deny_cb = _cb_root_deny_for_all()
    allow_cb = _cb_allow_parent_for_user("bob", "perm_accounting")

    def combined(datasette, actor, action):
        results = []
        for cb in (deny_cb, allow_cb):
            r = cb(datasette, actor, action)
            if r is not None:
                results.append(r)
        return results

    plugin = PermissionRulesPlugin(combined)
    ds.pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "bob"}
        result = await ds.allowed_resources(VIEW_TABLE, actor)
        allowed = _allowed_set(result.resources)

        # Only accounting tables should be allowed
        assert all(p == "perm_accounting" for p, c in allowed)
        assert len(allowed) == 10

        # Verify via allowed()
        assert await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_accounting", "table01"),
            actor=actor,
        )
        assert not await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_hr", "table01"),
            actor=actor,
        )
    finally:
        ds.pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_parent_scoped_action(ds):
    """
    For parent-scoped resources (databases), verify cascading.
    analytics allowed, hr denied, accounting implicitly denied.
    """
    deny_cb = _cb_org_policy_deny_parent("perm_hr")
    allow_cb = _cb_allow_parent_for_user("carol", "perm_analytics")

    def combined(datasette, actor, action):
        results = []
        for cb in (deny_cb, allow_cb):
            r = cb(datasette, actor, action)
            if r is not None:
                results.append(r)
        return results

    plugin = PermissionRulesPlugin(combined)
    ds.pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "carol"}
        result = await ds.allowed_resources("view-database", actor)
        allowed = {r.parent for r in result.resources}

        assert "perm_analytics" in allowed
        # hr is explicitly denied, accounting has no matching rule → implicit deny
        assert "perm_hr" not in allowed

        # Verify via allowed()
        assert await ds.allowed(
            action="view-database",
            resource=DatabaseResource("perm_analytics"),
            actor=actor,
        )
        assert not await ds.allowed(
            action="view-database",
            resource=DatabaseResource("perm_hr"),
            actor=actor,
        )
    finally:
        ds.pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_implicit_deny_when_no_rules(ds):
    """
    When no plugins return any rules, everything is denied (implicit deny).
    """

    def no_rules(datasette, actor, action):
        return None

    plugin = PermissionRulesPlugin(no_rules)
    ds.pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "erin"}
        result = await ds.allowed_resources(VIEW_TABLE, actor)
        assert len(result.resources) == 0

        # Single resource check too
        assert not await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_accounting", "table01"),
            actor=actor,
        )
    finally:
        ds.pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_action_specific_rules(ds):
    """
    Rules that only apply to view-table should not grant insert-row.
    """
    cb = _cb_allow_all_for_action("dana", VIEW_TABLE)
    plugin = PermissionRulesPlugin(cb)
    ds.pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "dana"}

        # view-table should be allowed
        result = await ds.allowed_resources(VIEW_TABLE, actor)
        assert len(result.resources) == 30  # 3 dbs x 10 tables

        assert await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_accounting", "table01"),
            actor=actor,
        )

        # insert-row should be denied (no rules for it)
        assert not await ds.allowed(
            action="insert-row",
            resource=TableResource("perm_accounting", "table01"),
            actor=actor,
        )
    finally:
        ds.pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_actor_parameters_available_in_sql(ds):
    """
    Test that :actor (JSON), :actor_id, and :action are all available in plugin SQL.
    """

    def cb(datasette, actor, action):
        return PermissionSQL(
            sql="""
                SELECT NULL AS parent, NULL AS child, 1 AS allow,
                       'Actor ID: ' || COALESCE(:actor_id, 'null') ||
                       ', Action: ' || :action AS reason
                WHERE :actor_id = 'test_user' AND :action = 'view-table'
                AND json_extract(:actor, '$.role') = 'admin'
            """,
            params={},  # :actor_id, :actor, :action are added by the framework
        )

    plugin = PermissionRulesPlugin(cb)
    ds.pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "test_user", "role": "admin"}

        # Should be allowed because the SQL conditions are met
        assert await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_accounting", "table01"),
            actor=actor,
        )

        # Different actor should be denied
        assert not await ds.allowed(
            action=VIEW_TABLE,
            resource=TableResource("perm_accounting", "table01"),
            actor={"id": "other_user", "role": "admin"},
        )

        # Verify allowed_resources also works
        result = await ds.allowed_resources(VIEW_TABLE, actor)
        assert len(result.resources) == 30  # all tables allowed
    finally:
        ds.pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_multiple_plugins_with_own_parameters(ds):
    """
    Multiple plugins can use their own parameter names without conflict.
    """

    def cb_one(datasette, actor, action):
        if action != VIEW_TABLE:
            return None
        return PermissionSQL(
            sql="""
                SELECT database_name AS parent, table_name AS child,
                       1 AS allow, 'Plugin one: ' || :p1_param AS reason
                FROM catalog_tables
                WHERE database_name = 'perm_accounting'
            """,
            params={"p1_param": "value1"},
        )

    def cb_two(datasette, actor, action):
        if action != VIEW_TABLE:
            return None
        return PermissionSQL(
            sql="""
                SELECT database_name AS parent, table_name AS child,
                       1 AS allow, 'Plugin two: ' || :p2_param AS reason
                FROM catalog_tables
                WHERE database_name = 'perm_hr'
            """,
            params={"p2_param": "value2"},
        )

    plugin_one = PermissionRulesPlugin(cb_one)
    plugin_two = PermissionRulesPlugin(cb_two)
    ds.pm.register(plugin_one, name="test_plugin_one")
    ds.pm.register(plugin_two, name="test_plugin_two")

    try:
        actor = {"id": "test_user"}
        result = await ds.allowed_resources(VIEW_TABLE, actor, include_reasons=True)
        allowed = _allowed_set(result.resources)

        # Both plugins should contribute — accounting from plugin one, hr from plugin two
        accounting_allowed = {(p, c) for p, c in allowed if p == "perm_accounting"}
        hr_allowed = {(p, c) for p, c in allowed if p == "perm_hr"}

        assert len(accounting_allowed) == 10
        assert len(hr_allowed) == 10

        # Check reasons contain the parameterized values
        for r in result.resources:
            if r.parent == "perm_accounting":
                assert any("value1" in reason for reason in r.reasons)
            elif r.parent == "perm_hr":
                assert any("value2" in reason for reason in r.reasons)
    finally:
        ds.pm.unregister(plugin_one, name="test_plugin_one")
        ds.pm.unregister(plugin_two, name="test_plugin_two")
