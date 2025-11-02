"""
Tests for the new Resource-based permission system.

These tests verify:
1. The new Datasette.allowed_resources() method (with pagination)
2. The new Datasette.allowed() method
3. The include_reasons parameter for debugging
4. That SQL does the heavy lifting (no Python filtering)
"""

import pytest
import pytest_asyncio
from datasette.app import Datasette
from datasette.plugins import pm
from datasette.permissions import PermissionSQL
from datasette.resources import TableResource
from datasette import hookimpl


# Test plugin that provides permission rules
class PermissionRulesPlugin:
    def __init__(self, rules_callback):
        self.rules_callback = rules_callback

    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        """Return permission rules based on the callback"""
        return self.rules_callback(datasette, actor, action)


@pytest_asyncio.fixture
async def test_ds():
    """Create a test Datasette instance with sample data"""
    ds = Datasette()
    await ds.invoke_startup()

    # Add test databases with some tables
    db = ds.add_memory_database("analytics")
    await db.execute_write("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)")
    await db.execute_write("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY)")
    await db.execute_write(
        "CREATE TABLE IF NOT EXISTS sensitive (id INTEGER PRIMARY KEY)"
    )

    db2 = ds.add_memory_database("production")
    await db2.execute_write(
        "CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY)"
    )
    await db2.execute_write(
        "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY)"
    )

    # Refresh schemas to populate catalog_tables in internal database
    await ds._refresh_schemas()

    return ds


@pytest.mark.asyncio
async def test_allowed_resources_global_allow(test_ds):
    """Test allowed_resources() with a global allow rule"""

    def rules_callback(datasette, actor, action):
        if actor and actor.get("id") == "alice":
            sql = "SELECT NULL AS parent, NULL AS child, 1 AS allow, 'global: alice has access' AS reason"
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        # Use the new allowed_resources() method
        result = await test_ds.allowed_resources("view-table", {"id": "alice"})
        tables = result.resources

        # Alice should see all tables
        assert len(tables) == 5
        assert all(isinstance(t, TableResource) for t in tables)

        # Check specific tables are present
        table_set = set((t.parent, t.child) for t in tables)
        assert ("analytics", "events") in table_set
        assert ("analytics", "users") in table_set
        assert ("analytics", "sensitive") in table_set
        assert ("production", "customers") in table_set
        assert ("production", "orders") in table_set

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_allowed_specific_resource(test_ds):
    """Test allowed() method checks specific resource efficiently"""

    def rules_callback(datasette, actor, action):
        if actor and actor.get("role") == "analyst":
            # Allow analytics database, deny everything else (global deny)
            sql = """
                SELECT NULL AS parent, NULL AS child, 0 AS allow, 'global deny' AS reason
                UNION ALL
                SELECT 'analytics' AS parent, NULL AS child, 1 AS allow, 'analyst access' AS reason
            """
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "bob", "role": "analyst"}

        # Check specific resources using allowed()
        # This should use SQL WHERE clause, not fetch all resources
        assert await test_ds.allowed(
            action="view-table",
            resource=TableResource("analytics", "users"),
            actor=actor,
        )
        assert await test_ds.allowed(
            action="view-table",
            resource=TableResource("analytics", "events"),
            actor=actor,
        )
        assert not await test_ds.allowed(
            action="view-table",
            resource=TableResource("production", "orders"),
            actor=actor,
        )

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_allowed_resources_include_reasons(test_ds):
    def rules_callback(datasette, actor, action):
        if actor and actor.get("role") == "analyst":
            sql = """
                SELECT 'analytics' AS parent, NULL AS child, 1 AS allow,
                       'parent: analyst access to analytics' AS reason
                UNION ALL
                SELECT 'analytics' AS parent, 'sensitive' AS child, 0 AS allow,
                       'child: sensitive data denied' AS reason
            """
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        # Use allowed_resources with include_reasons to get debugging info
        result = await test_ds.allowed_resources(
            "view-table", {"id": "bob", "role": "analyst"}, include_reasons=True
        )
        allowed = result.resources

        # Should get analytics tables except sensitive
        assert len(allowed) >= 2  # At least users and events

        # Check we can access both resource and reason
        for resource in allowed:
            assert isinstance(resource, TableResource)
            assert isinstance(resource.reasons, list)
            if resource.parent == "analytics":
                # Should mention parent-level reason in at least one of the reasons
                reasons_text = " ".join(resource.reasons).lower()
                assert "analyst access" in reasons_text

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_child_deny_overrides_parent_allow(test_ds):
    """Test that child-level DENY beats parent-level ALLOW"""

    def rules_callback(datasette, actor, action):
        if actor and actor.get("role") == "analyst":
            sql = """
                SELECT 'analytics' AS parent, NULL AS child, 1 AS allow,
                       'parent: allow analytics' AS reason
                UNION ALL
                SELECT 'analytics' AS parent, 'sensitive' AS child, 0 AS allow,
                       'child: deny sensitive' AS reason
            """
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "bob", "role": "analyst"}
        result = await test_ds.allowed_resources("view-table", actor)
        tables = result.resources

        # Should see analytics tables except sensitive
        analytics_tables = [t for t in tables if t.parent == "analytics"]
        assert len(analytics_tables) >= 2

        table_names = {t.child for t in analytics_tables}
        assert "users" in table_names
        assert "events" in table_names
        assert "sensitive" not in table_names

        # Verify with allowed() method
        assert await test_ds.allowed(
            action="view-table",
            resource=TableResource("analytics", "users"),
            actor=actor,
        )
        assert not await test_ds.allowed(
            action="view-table",
            resource=TableResource("analytics", "sensitive"),
            actor=actor,
        )

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_child_allow_overrides_parent_deny(test_ds):
    """Test that child-level ALLOW beats parent-level DENY"""

    def rules_callback(datasette, actor, action):
        if actor and actor.get("id") == "carol":
            sql = """
                SELECT 'production' AS parent, NULL AS child, 0 AS allow,
                       'parent: deny production' AS reason
                UNION ALL
                SELECT 'production' AS parent, 'orders' AS child, 1 AS allow,
                       'child: carol can see orders' AS reason
            """
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "carol"}
        result = await test_ds.allowed_resources("view-table", actor)
        tables = result.resources

        # Should only see production.orders
        production_tables = [t for t in tables if t.parent == "production"]
        assert len(production_tables) == 1
        assert production_tables[0].child == "orders"

        # Verify with allowed() method
        assert await test_ds.allowed(
            action="view-table",
            resource=TableResource("production", "orders"),
            actor=actor,
        )
        assert not await test_ds.allowed(
            action="view-table",
            resource=TableResource("production", "customers"),
            actor=actor,
        )

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_sql_does_filtering_not_python(test_ds):
    """
    Verify that allowed() uses SQL WHERE clause, not Python filtering.

    This test doesn't actually verify the SQL itself (that would require
    query introspection), but it demonstrates the API contract.
    """

    def rules_callback(datasette, actor, action):
        # Deny everything by default, allow only analytics.users specifically
        sql = """
            SELECT NULL AS parent, NULL AS child, 0 AS allow,
                   'global deny' AS reason
            UNION ALL
            SELECT 'analytics' AS parent, 'users' AS child, 1 AS allow,
                   'specific allow' AS reason
        """
        return PermissionSQL(sql=sql)

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        actor = {"id": "dave"}

        # allowed() should execute a targeted SQL query
        # NOT fetch all resources and filter in Python
        assert await test_ds.allowed(
            action="view-table",
            resource=TableResource("analytics", "users"),
            actor=actor,
        )
        assert not await test_ds.allowed(
            action="view-table",
            resource=TableResource("analytics", "events"),
            actor=actor,
        )

        # allowed_resources() should also use SQL filtering
        result = await test_ds.allowed_resources("view-table", actor)
        tables = result.resources
        assert len(tables) == 1
        assert tables[0].parent == "analytics"
        assert tables[0].child == "users"

    finally:
        pm.unregister(plugin, name="test_plugin")
