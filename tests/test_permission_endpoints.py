"""
Tests for permission endpoints:
- /-/allowed.json
- /-/rules.json
"""

import pytest
import pytest_asyncio
from datasette.app import Datasette


@pytest_asyncio.fixture
async def ds_with_permissions():
    """Create a Datasette instance with test data and permissions."""
    ds = Datasette()
    ds.root_enabled = True
    await ds.invoke_startup()

    # Add some test databases and tables
    db = ds.add_memory_database("analytics")
    await db.execute_write(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)"
    )
    await db.execute_write(
        "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, event_type TEXT, user_id INTEGER)"
    )

    db2 = ds.add_memory_database("production")
    await db2.execute_write(
        "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, total REAL)"
    )
    await db2.execute_write(
        "CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name TEXT)"
    )

    await ds.refresh_schemas()

    return ds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_status,expected_keys",
    [
        # Instance level permission
        (
            "/-/allowed.json?action=view-instance",
            200,
            {"action", "items", "total", "page"},
        ),
        # Database level permission
        (
            "/-/allowed.json?action=view-database",
            200,
            {"action", "items", "total", "page"},
        ),
        # Table level permission
        (
            "/-/allowed.json?action=view-table",
            200,
            {"action", "items", "total", "page"},
        ),
        (
            "/-/allowed.json?action=execute-sql",
            200,
            {"action", "items", "total", "page"},
        ),
        # Missing action parameter
        ("/-/allowed.json", 400, {"error"}),
        # Invalid action
        ("/-/allowed.json?action=nonexistent", 404, {"error"}),
        # Any valid action works, even if no permission rules exist for it
        (
            "/-/allowed.json?action=insert-row",
            200,
            {"action", "items", "total", "page"},
        ),
    ],
)
async def test_allowed_json_basic(
    ds_with_permissions, path, expected_status, expected_keys
):
    response = await ds_with_permissions.client.get(path)
    assert response.status_code == expected_status
    data = response.json()
    assert expected_keys.issubset(data.keys())


@pytest.mark.asyncio
async def test_allowed_json_response_structure(ds_with_permissions):
    """Test that /-/allowed.json returns the expected structure."""
    response = await ds_with_permissions.client.get(
        "/-/allowed.json?action=view-instance"
    )
    assert response.status_code == 200
    data = response.json()

    # Check required fields
    assert "action" in data
    assert "actor_id" in data
    assert "page" in data
    assert "page_size" in data
    assert "total" in data
    assert "items" in data

    # Check items structure
    assert isinstance(data["items"], list)
    if data["items"]:
        item = data["items"][0]
        assert "parent" in item
        assert "child" in item
        assert "resource" in item


@pytest.mark.asyncio
async def test_allowed_json_with_actor(ds_with_permissions):
    """Test /-/allowed.json includes actor information."""
    response = await ds_with_permissions.client.get(
        "/-/allowed.json?action=view-table",
        cookies={
            "ds_actor": ds_with_permissions.client.actor_cookie({"id": "test_user"})
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["actor_id"] == "test_user"


@pytest.mark.asyncio
async def test_allowed_json_pagination():
    """Test that /-/allowed.json pagination works."""
    ds = Datasette()
    await ds.invoke_startup()

    # Create many tables to test pagination
    db = ds.add_memory_database("test")
    for i in range(30):
        await db.execute_write(f"CREATE TABLE table{i:02d} (id INTEGER PRIMARY KEY)")
    await ds.refresh_schemas()

    # Test page 1
    response = await ds.client.get(
        "/-/allowed.json?action=view-table&page_size=10&page=1"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert len(data["items"]) == 10

    # Test page 2
    response = await ds.client.get(
        "/-/allowed.json?action=view-table&page_size=10&page=2"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 2
    assert len(data["items"]) == 10

    # Verify items are different between pages
    response1 = await ds.client.get(
        "/-/allowed.json?action=view-table&page_size=10&page=1"
    )
    response2 = await ds.client.get(
        "/-/allowed.json?action=view-table&page_size=10&page=2"
    )
    items1 = {(item["parent"], item["child"]) for item in response1.json()["items"]}
    items2 = {(item["parent"], item["child"]) for item in response2.json()["items"]}
    assert items1 != items2


@pytest.mark.asyncio
async def test_allowed_json_total_count(tmp_path_factory):
    """Test that /-/allowed.json returns correct total count."""
    from datasette.database import Database

    # Use temporary file databases to avoid leakage from other tests
    tmp_dir = tmp_path_factory.mktemp("test_allowed_json_total_count")

    ds = Datasette()
    await ds.invoke_startup()

    # Create test databases with tables
    analytics_db = ds.add_database(
        Database(ds, path=str(tmp_dir / "analytics.db")), name="analytics"
    )
    await analytics_db.execute_write(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)"
    )
    await analytics_db.execute_write(
        "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, event_type TEXT, user_id INTEGER)"
    )

    production_db = ds.add_database(
        Database(ds, path=str(tmp_dir / "production.db")), name="production"
    )
    await production_db.execute_write(
        "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, total REAL)"
    )
    await production_db.execute_write(
        "CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name TEXT)"
    )

    await ds.refresh_schemas()

    response = await ds.client.get("/-/allowed.json?action=view-table")
    assert response.status_code == 200
    data = response.json()

    # We created 4 tables total (2 in analytics, 2 in production)
    import json

    assert (
        data["total"] == 4
    ), f"Expected total=4, got: {json.dumps(data, separators=(',', ':'))}"


# /-/rules.json tests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_status,expected_keys",
    [
        # Instance level rules
        (
            "/-/rules.json?action=view-instance",
            200,
            {"action", "items", "total", "page"},
        ),
        # Database level rules
        (
            "/-/rules.json?action=view-database",
            200,
            {"action", "items", "total", "page"},
        ),
        # Table level rules
        (
            "/-/rules.json?action=view-table",
            200,
            {"action", "items", "total", "page"},
        ),
        # Missing action parameter
        ("/-/rules.json", 400, {"error"}),
        # Invalid action
        ("/-/rules.json?action=nonexistent", 404, {"error"}),
    ],
)
async def test_rules_json_basic(
    ds_with_permissions, path, expected_status, expected_keys
):
    # Use root actor for rules endpoint (requires permissions-debug)
    response = await ds_with_permissions.client.get(
        path,
        cookies={"ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == expected_status
    data = response.json()
    assert expected_keys.issubset(data.keys())


@pytest.mark.asyncio
async def test_rules_json_response_structure(ds_with_permissions):
    """Test that /-/rules.json returns the expected structure."""
    response = await ds_with_permissions.client.get(
        "/-/rules.json?action=view-instance",
        cookies={"ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()

    # Check required fields
    assert "action" in data
    assert "actor_id" in data
    assert "page" in data
    assert "page_size" in data
    assert "total" in data
    assert "items" in data

    # Check items structure
    assert isinstance(data["items"], list)
    if data["items"]:
        item = data["items"][0]
        assert "parent" in item
        assert "child" in item
        assert "resource" in item
        assert "allow" in item
        assert "reason" in item


@pytest.mark.asyncio
async def test_rules_json_includes_all_rules(ds_with_permissions):
    """Test that /-/rules.json includes both allowed and denied resources."""
    # Root user should see rules for everything
    response = await ds_with_permissions.client.get(
        "/-/rules.json?action=view-table",
        cookies={"ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()

    # Should have items (root has global allow)
    assert len(data["items"]) > 0

    # Each item should have allow field (0 or 1)
    for item in data["items"]:
        assert "allow" in item
        assert item["allow"] in [0, 1]


@pytest.mark.asyncio
async def test_rules_json_pagination():
    """Test that /-/rules.json pagination works."""
    ds = Datasette()
    ds.root_enabled = True
    await ds.invoke_startup()

    # Create some tables
    db = ds.add_memory_database("test")
    for i in range(5):
        await db.execute_write(
            f"CREATE TABLE IF NOT EXISTS table{i:02d} (id INTEGER PRIMARY KEY)"
        )
    await ds.refresh_schemas()

    # Test basic pagination structure - just verify it returns paginated results
    response = await ds.client.get(
        "/-/rules.json?action=view-table&page_size=2&page=1",
        cookies={"ds_actor": ds.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 2
    # Verify items is a list (may have fewer items than page_size if there aren't many rules)
    assert isinstance(data["items"], list)
    assert "total" in data


@pytest.mark.asyncio
async def test_rules_json_with_actor(ds_with_permissions):
    """Test /-/rules.json includes actor information."""
    # Use root actor (rules endpoint requires permissions-debug)
    response = await ds_with_permissions.client.get(
        "/-/rules.json?action=view-table",
        cookies={"ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["actor_id"] == "root"


@pytest.mark.asyncio
async def test_root_user_respects_settings_deny():
    """
    Test for issue #2509: Settings-based deny rules should override root user privileges.

    When a database has `allow: false` in settings, the root user should NOT see
    that database in /-/allowed.json?action=view-database.
    """
    ds = Datasette(
        config={
            "databases": {
                "content": {
                    "allow": False,  # Deny everyone, including root
                }
            }
        }
    )
    ds.root_enabled = True
    await ds.invoke_startup()
    ds.add_memory_database("content")

    # Root user should NOT see the denied database
    response = await ds.client.get(
        "/-/allowed.json?action=view-database",
        cookies={"ds_actor": ds.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()

    # Check that content database is NOT in the allowed list
    allowed_databases = [item["parent"] for item in data["items"]]
    assert "content" not in allowed_databases, (
        f"Root user should not see 'content' database when settings deny it, "
        f"but found it in: {allowed_databases}"
    )


@pytest.mark.asyncio
async def test_root_user_respects_settings_deny_tables():
    """
    Test for issue #2509: Settings-based deny rules should override root for tables too.

    When a database has `allow: false` in settings, the root user should NOT see
    tables from that database in /-/allowed.json?action=view-table.
    """
    ds = Datasette(
        config={
            "databases": {
                "content": {
                    "allow": False,  # Deny everyone, including root
                }
            }
        }
    )
    ds.root_enabled = True
    await ds.invoke_startup()

    # Add a database with a table
    db = ds.add_memory_database("content")
    await db.execute_write("CREATE TABLE repos (id INTEGER PRIMARY KEY, name TEXT)")
    await ds.refresh_schemas()

    # Root user should NOT see tables from the content database
    response = await ds.client.get(
        "/-/allowed.json?action=view-table",
        cookies={"ds_actor": ds.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()

    # Check that content.repos table is NOT in the allowed list
    content_tables = [
        item["child"] for item in data["items"] if item["parent"] == "content"
    ]
    assert "repos" not in content_tables, (
        f"Root user should not see tables from 'content' database when settings deny it, "
        f"but found: {content_tables}"
    )


@pytest.mark.asyncio
async def test_execute_sql_requires_view_database():
    """
    Test for issue #2527: execute-sql permission should require view-database permission.

    A user who has execute-sql permission but not view-database permission should not
    be able to execute SQL on that database.
    """
    from datasette.permissions import PermissionSQL
    from datasette.plugins import pm
    from datasette import hookimpl

    class TestPermissionPlugin:
        __name__ = "TestPermissionPlugin"

        @hookimpl
        def permission_resources_sql(self, datasette, actor, action):
            if actor is None or actor.get("id") != "test_user":
                return []

            if action == "execute-sql":
                # Grant execute-sql on the "secret" database
                return PermissionSQL(
                    sql="SELECT 'secret' AS parent, NULL AS child, 1 AS allow, 'can execute sql' AS reason",
                )
            elif action == "view-database":
                # Deny view-database on the "secret" database
                return PermissionSQL(
                    sql="SELECT 'secret' AS parent, NULL AS child, 0 AS allow, 'cannot view db' AS reason",
                )

            return []

    plugin = TestPermissionPlugin()
    pm.register(plugin, name="test_plugin")

    try:
        ds = Datasette()
        await ds.invoke_startup()
        ds.add_memory_database("secret")
        await ds.refresh_schemas()

        # User should NOT have execute-sql permission because view-database is denied
        response = await ds.client.get(
            "/-/allowed.json?action=execute-sql",
            cookies={"ds_actor": ds.client.actor_cookie({"id": "test_user"})},
        )
        assert response.status_code == 200
        data = response.json()

        # The "secret" database should NOT be in the allowed list for execute-sql
        allowed_databases = [item["parent"] for item in data["items"]]
        assert "secret" not in allowed_databases, (
            f"User should not have execute-sql permission without view-database, "
            f"but found 'secret' in: {allowed_databases}"
        )

        # Also verify that attempting to execute SQL on the database is denied
        # (may be 403 or 302 redirect to login/error page depending on middleware)
        response = await ds.client.get(
            "/secret?sql=SELECT+1",
            cookies={"ds_actor": ds.client.actor_cookie({"id": "test_user"})},
        )
        assert response.status_code in (302, 403), (
            f"Expected 302 or 403 when trying to execute SQL without view-database permission, "
            f"but got {response.status_code}"
        )
    finally:
        pm.unregister(plugin)
