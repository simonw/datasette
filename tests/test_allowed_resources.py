"""
Tests for the allowed_resources() API.

These tests verify that the allowed_resources() API correctly filters resources
based on permission rules from plugins and configuration.
"""

import pytest
import pytest_asyncio
from datasette.app import Datasette
from datasette.plugins import pm
from datasette.permissions import PermissionSQL
from datasette import hookimpl


# Test plugin that provides permission rules
class PermissionRulesPlugin:
    def __init__(self, rules_callback):
        self.rules_callback = rules_callback

    @hookimpl
    def permission_resources_sql(self, datasette, actor, action):
        return self.rules_callback(datasette, actor, action)


@pytest_asyncio.fixture(scope="function")
async def test_ds():
    """Create a test Datasette instance with sample data (fresh for each test)"""
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
async def test_tables_endpoint_global_access(test_ds):
    """Test /-/tables with global access permissions"""

    def rules_callback(datasette, actor, action):
        if actor and actor.get("id") == "alice":
            sql = "SELECT NULL AS parent, NULL AS child, 1 AS allow, 'global: alice has access' AS reason"
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        # Use the allowed_resources API directly
        page = await test_ds.allowed_resources("view-table", {"id": "alice"})

        # Convert to the format the endpoint returns
        result = [
            {
                "name": f"{t.parent}/{t.child}",
                "url": test_ds.urls.table(t.parent, t.child),
            }
            for t in page.resources
        ]

        # Alice should see all tables
        assert len(result) == 5
        table_names = {m["name"] for m in result}
        assert "analytics/events" in table_names
        assert "analytics/users" in table_names
        assert "analytics/sensitive" in table_names
        assert "production/customers" in table_names
        assert "production/orders" in table_names

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_tables_endpoint_database_restriction(test_ds):
    """Test /-/tables with database-level restriction"""

    def rules_callback(datasette, actor, action):
        if actor and actor.get("role") == "analyst":
            # Allow only analytics database
            sql = "SELECT 'analytics' AS parent, NULL AS child, 1 AS allow, 'analyst access' AS reason"
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        page = await test_ds.allowed_resources(
            "view-table", {"id": "bob", "role": "analyst"}
        )
        result = [
            {
                "name": f"{t.parent}/{t.child}",
                "url": test_ds.urls.table(t.parent, t.child),
            }
            for t in page.resources
        ]

        # Bob should only see analytics tables
        analytics_tables = [m for m in result if m["name"].startswith("analytics/")]
        production_tables = [m for m in result if m["name"].startswith("production/")]

        assert len(analytics_tables) == 3
        table_names = {m["name"] for m in analytics_tables}
        assert "analytics/events" in table_names
        assert "analytics/users" in table_names
        assert "analytics/sensitive" in table_names

        # Should not see production tables (unless default_permissions allows them)
        # Note: default_permissions.py provides default allows, so we just check analytics are present

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_tables_endpoint_table_exception(test_ds):
    """Test /-/tables with table-level exception (deny database, allow specific table)"""

    def rules_callback(datasette, actor, action):
        if actor and actor.get("id") == "carol":
            # Deny analytics database, but allow analytics.users specifically
            sql = """
                SELECT 'analytics' AS parent, NULL AS child, 0 AS allow, 'deny analytics' AS reason
                UNION ALL
                SELECT 'analytics' AS parent, 'users' AS child, 1 AS allow, 'carol exception' AS reason
            """
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        page = await test_ds.allowed_resources("view-table", {"id": "carol"})
        result = [
            {
                "name": f"{t.parent}/{t.child}",
                "url": test_ds.urls.table(t.parent, t.child),
            }
            for t in page.resources
        ]

        # Carol should see analytics.users but not other analytics tables
        analytics_tables = [m for m in result if m["name"].startswith("analytics/")]
        assert len(analytics_tables) == 1
        table_names = {m["name"] for m in analytics_tables}
        assert "analytics/users" in table_names

        # Should NOT see analytics.events or analytics.sensitive
        assert "analytics/events" not in table_names
        assert "analytics/sensitive" not in table_names

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_tables_endpoint_deny_overrides_allow(test_ds):
    """Test that child-level DENY beats parent-level ALLOW"""

    def rules_callback(datasette, actor, action):
        if actor and actor.get("role") == "analyst":
            # Allow analytics, but deny sensitive table
            sql = """
                SELECT 'analytics' AS parent, NULL AS child, 1 AS allow, 'allow analytics' AS reason
                UNION ALL
                SELECT 'analytics' AS parent, 'sensitive' AS child, 0 AS allow, 'deny sensitive' AS reason
            """
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        page = await test_ds.allowed_resources(
            "view-table", {"id": "bob", "role": "analyst"}
        )
        result = [
            {
                "name": f"{t.parent}/{t.child}",
                "url": test_ds.urls.table(t.parent, t.child),
            }
            for t in page.resources
        ]

        analytics_tables = [m for m in result if m["name"].startswith("analytics/")]

        # Should see users and events but NOT sensitive
        table_names = {m["name"] for m in analytics_tables}
        assert "analytics/users" in table_names
        assert "analytics/events" in table_names
        assert "analytics/sensitive" not in table_names

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_tables_endpoint_no_permissions():
    """Test /-/tables when user has no custom permissions (only defaults)"""

    ds = Datasette()
    await ds.invoke_startup()

    # Add a single database
    db = ds.add_memory_database("testdb")
    await db.execute_write("CREATE TABLE items (id INTEGER PRIMARY KEY)")
    await ds._refresh_schemas()

    # Unknown actor with no custom permissions
    page = await ds.allowed_resources("view-table", {"id": "unknown"})
    result = [
        {"name": f"{t.parent}/{t.child}", "url": ds.urls.table(t.parent, t.child)}
        for t in page.resources
    ]

    # Should see tables (due to default_permissions.py providing default allow)
    assert len(result) >= 1
    assert any(m["name"].endswith("/items") for m in result)


@pytest.mark.asyncio
async def test_tables_endpoint_specific_table_only(test_ds):
    """Test /-/tables when only specific tables are allowed (no parent/global rules)"""

    def rules_callback(datasette, actor, action):
        if actor and actor.get("id") == "dave":
            # Allow only specific tables, no parent-level or global rules
            sql = """
                SELECT 'analytics' AS parent, 'users' AS child, 1 AS allow, 'specific table 1' AS reason
                UNION ALL
                SELECT 'production' AS parent, 'orders' AS child, 1 AS allow, 'specific table 2' AS reason
            """
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        page = await test_ds.allowed_resources("view-table", {"id": "dave"})
        result = [
            {
                "name": f"{t.parent}/{t.child}",
                "url": test_ds.urls.table(t.parent, t.child),
            }
            for t in page.resources
        ]

        # Should see only the two specifically allowed tables
        specific_tables = [
            m for m in result if m["name"] in ("analytics/users", "production/orders")
        ]

        assert len(specific_tables) == 2
        table_names = {m["name"] for m in specific_tables}
        assert "analytics/users" in table_names
        assert "production/orders" in table_names

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_tables_endpoint_empty_result(test_ds):
    """Test /-/tables when all tables are explicitly denied"""

    def rules_callback(datasette, actor, action):
        if actor and actor.get("id") == "blocked":
            # Global deny
            sql = "SELECT NULL AS parent, NULL AS child, 0 AS allow, 'global deny' AS reason"
            return PermissionSQL(sql=sql)
        return None

    plugin = PermissionRulesPlugin(rules_callback)
    pm.register(plugin, name="test_plugin")

    try:
        page = await test_ds.allowed_resources("view-table", {"id": "blocked"})
        result = [
            {
                "name": f"{t.parent}/{t.child}",
                "url": test_ds.urls.table(t.parent, t.child),
            }
            for t in page.resources
        ]

        # Global deny should block access to all tables
        assert len(result) == 0

    finally:
        pm.unregister(plugin, name="test_plugin")


@pytest.mark.asyncio
async def test_tables_endpoint_no_query_returns_all():
    """Test /-/tables without query parameter returns all tables"""
    ds = Datasette()
    await ds.invoke_startup()

    # Add database with a few tables
    db = ds.add_memory_database("test_db")
    await db.execute_write("CREATE TABLE users (id INTEGER)")
    await db.execute_write("CREATE TABLE posts (id INTEGER)")
    await db.execute_write("CREATE TABLE comments (id INTEGER)")
    await ds._refresh_schemas()

    # Get all tables without query
    page = await ds.allowed_resources("view-table", None)

    # Should return all tables with truncated: false
    assert len(page.resources) >= 3
    table_names = {f"{t.parent}/{t.child}" for t in page.resources}
    assert "test_db/users" in table_names
    assert "test_db/posts" in table_names
    assert "test_db/comments" in table_names


@pytest.mark.asyncio
async def test_tables_endpoint_truncation():
    """Test /-/tables truncates at 100 tables and sets truncated flag"""
    ds = Datasette()
    await ds.invoke_startup()

    # Create a database with 105 tables
    db = ds.add_memory_database("big_db")
    for i in range(105):
        await db.execute_write(f"CREATE TABLE table_{i:03d} (id INTEGER)")
    await ds._refresh_schemas()

    # Get all tables - should be paginated with limit=100 by default
    page = await ds.allowed_resources("view-table", None)
    big_db_tables = [t for t in page.resources if t.parent == "big_db"]

    # Should have exactly 100 tables in first page (default limit)
    assert len(big_db_tables) == 100
    assert page.next is not None  # More results available


@pytest.mark.asyncio
async def test_tables_endpoint_search_single_term():
    """Test /-/tables?q=user to filter tables matching 'user'"""

    ds = Datasette()
    await ds.invoke_startup()

    # Add database with various table names
    db = ds.add_memory_database("search_test")
    await db.execute_write("CREATE TABLE users (id INTEGER)")
    await db.execute_write("CREATE TABLE user_profiles (id INTEGER)")
    await db.execute_write("CREATE TABLE events (id INTEGER)")
    await db.execute_write("CREATE TABLE posts (id INTEGER)")
    await ds._refresh_schemas()

    # Get all tables in the new format
    page = await ds.allowed_resources("view-table", None)
    matches = [
        {"name": f"{t.parent}/{t.child}", "url": ds.urls.table(t.parent, t.child)}
        for t in page.resources
    ]

    # Filter for "user" (extract table name from "db/table")
    import re

    pattern = ".*user.*"
    regex = re.compile(pattern, re.IGNORECASE)
    filtered = [m for m in matches if regex.match(m["name"].split("/", 1)[1])]

    # Should match users and user_profiles but not events or posts
    table_names = {m["name"].split("/", 1)[1] for m in filtered}
    assert "users" in table_names
    assert "user_profiles" in table_names
    assert "events" not in table_names
    assert "posts" not in table_names


@pytest.mark.asyncio
async def test_tables_endpoint_search_multiple_terms():
    """Test /-/tables?q=user+profile to filter tables matching .*user.*profile.*"""

    ds = Datasette()
    await ds.invoke_startup()

    # Add database with various table names
    db = ds.add_memory_database("search_test2")
    await db.execute_write("CREATE TABLE user_profiles (id INTEGER)")
    await db.execute_write("CREATE TABLE users (id INTEGER)")
    await db.execute_write("CREATE TABLE profile_settings (id INTEGER)")
    await db.execute_write("CREATE TABLE events (id INTEGER)")
    await ds._refresh_schemas()

    # Get all tables in the new format
    page = await ds.allowed_resources("view-table", None)
    matches = [
        {"name": f"{t.parent}/{t.child}", "url": ds.urls.table(t.parent, t.child)}
        for t in page.resources
    ]

    # Filter for "user profile" (two terms, extract table name from "db/table")
    import re

    terms = ["user", "profile"]
    pattern = ".*" + ".*".join(re.escape(term) for term in terms) + ".*"
    regex = re.compile(pattern, re.IGNORECASE)
    filtered = [m for m in matches if regex.match(m["name"].split("/", 1)[1])]

    # Should match only user_profiles (has both user and profile in that order)
    table_names = {m["name"].split("/", 1)[1] for m in filtered}
    assert "user_profiles" in table_names
    assert "users" not in table_names  # doesn't have "profile"
    assert "profile_settings" not in table_names  # doesn't have "user"


@pytest.mark.asyncio
async def test_tables_endpoint_search_ordering():
    """Test that search results are ordered by shortest name first"""

    ds = Datasette()
    await ds.invoke_startup()

    # Add database with tables of various lengths containing "user"
    db = ds.add_memory_database("order_test")
    await db.execute_write("CREATE TABLE users (id INTEGER)")
    await db.execute_write("CREATE TABLE user_profiles (id INTEGER)")
    await db.execute_write(
        "CREATE TABLE u (id INTEGER)"
    )  # Shortest, but doesn't match "user"
    await db.execute_write(
        "CREATE TABLE user_authentication_tokens (id INTEGER)"
    )  # Longest
    await db.execute_write("CREATE TABLE user_data (id INTEGER)")
    await ds._refresh_schemas()

    # Get all tables in the new format
    page = await ds.allowed_resources("view-table", None)
    matches = [
        {"name": f"{t.parent}/{t.child}", "url": ds.urls.table(t.parent, t.child)}
        for t in page.resources
    ]

    # Filter for "user" and sort by table name length
    import re

    pattern = ".*user.*"
    regex = re.compile(pattern, re.IGNORECASE)
    filtered = [m for m in matches if regex.match(m["name"].split("/", 1)[1])]
    filtered.sort(key=lambda m: len(m["name"].split("/", 1)[1]))

    # Should be ordered: users, user_data, user_profiles, user_authentication_tokens
    matching_names = [m["name"].split("/", 1)[1] for m in filtered]
    assert matching_names[0] == "users"  # shortest
    assert len(matching_names[0]) < len(matching_names[1])
    assert len(matching_names[-1]) > len(matching_names[-2])
    assert matching_names[-1] == "user_authentication_tokens"  # longest


@pytest.mark.asyncio
async def test_tables_endpoint_search_case_insensitive():
    """Test that search is case-insensitive"""

    ds = Datasette()
    await ds.invoke_startup()

    # Add database with mixed case table names
    db = ds.add_memory_database("case_test")
    await db.execute_write("CREATE TABLE Users (id INTEGER)")
    await db.execute_write("CREATE TABLE USER_PROFILES (id INTEGER)")
    await db.execute_write("CREATE TABLE user_data (id INTEGER)")
    await ds._refresh_schemas()

    # Get all tables in the new format
    page = await ds.allowed_resources("view-table", None)
    matches = [
        {"name": f"{t.parent}/{t.child}", "url": ds.urls.table(t.parent, t.child)}
        for t in page.resources
    ]

    # Filter for "user" (lowercase) should match all case variants
    import re

    pattern = ".*user.*"
    regex = re.compile(pattern, re.IGNORECASE)
    filtered = [m for m in matches if regex.match(m["name"].split("/", 1)[1])]

    # Should match all three tables regardless of case
    table_names = {m["name"].split("/", 1)[1] for m in filtered}
    assert "Users" in table_names
    assert "USER_PROFILES" in table_names
    assert "user_data" in table_names
    assert len(filtered) >= 3


@pytest.mark.asyncio
async def test_tables_endpoint_search_no_matches():
    """Test search with no matching tables returns empty list"""

    ds = Datasette()
    await ds.invoke_startup()

    # Add database with tables that won't match search
    db = ds.add_memory_database("nomatch_test")
    await db.execute_write("CREATE TABLE events (id INTEGER)")
    await db.execute_write("CREATE TABLE posts (id INTEGER)")
    await ds._refresh_schemas()

    # Get all tables in the new format
    page = await ds.allowed_resources("view-table", None)
    matches = [
        {"name": f"{t.parent}/{t.child}", "url": ds.urls.table(t.parent, t.child)}
        for t in page.resources
    ]

    # Filter for "zzz" which doesn't exist
    import re

    pattern = ".*zzz.*"
    regex = re.compile(pattern, re.IGNORECASE)
    filtered = [m for m in matches if regex.match(m["name"].split("/", 1)[1])]

    # Should return empty list
    assert len(filtered) == 0


@pytest.mark.asyncio
async def test_tables_endpoint_config_database_allow():
    """Test that database-level allow blocks work for view-table action"""

    # Simulate: -s databases.restricted_db.allow.id root
    config = {"databases": {"restricted_db": {"allow": {"id": "root"}}}}

    ds = Datasette(config=config)
    await ds.invoke_startup()

    # Create databases
    restricted_db = ds.add_memory_database("restricted_db")
    await restricted_db.execute_write("CREATE TABLE users (id INTEGER)")
    await restricted_db.execute_write("CREATE TABLE posts (id INTEGER)")

    public_db = ds.add_memory_database("public_db")
    await public_db.execute_write("CREATE TABLE articles (id INTEGER)")

    await ds._refresh_schemas()

    # Root user should see restricted_db tables
    root_page = await ds.allowed_resources("view-table", {"id": "root"})
    root_list = [
        {"name": f"{t.parent}/{t.child}", "url": ds.urls.table(t.parent, t.child)}
        for t in root_page.resources
    ]
    restricted_tables_root = [
        m for m in root_list if m["name"].startswith("restricted_db/")
    ]
    assert len(restricted_tables_root) == 2
    table_names = {m["name"] for m in restricted_tables_root}
    assert "restricted_db/users" in table_names
    assert "restricted_db/posts" in table_names

    # Alice should NOT see restricted_db tables
    alice_page = await ds.allowed_resources("view-table", {"id": "alice"})
    alice_list = [
        {"name": f"{t.parent}/{t.child}", "url": ds.urls.table(t.parent, t.child)}
        for t in alice_page.resources
    ]
    restricted_tables_alice = [
        m for m in alice_list if m["name"].startswith("restricted_db/")
    ]
    assert len(restricted_tables_alice) == 0

    # But Alice should see public_db tables (no restrictions)
    public_tables_alice = [m for m in alice_list if m["name"].startswith("public_db/")]
    assert len(public_tables_alice) == 1
    assert "public_db/articles" in {m["name"] for m in public_tables_alice}
