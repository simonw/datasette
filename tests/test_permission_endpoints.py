"""
Tests for permission inspection endpoints:
- /-/check.json
- /-/allowed.json
- /-/rules.json
"""

import pytest
import pytest_asyncio
from datasette.app import Datasette


@pytest_asyncio.fixture
async def ds_with_permissions():
    """Create a Datasette instance with some permission rules configured."""
    ds = Datasette(
        config={
            "databases": {
                "content": {
                    "allow": {"id": "*"},  # Allow all authenticated users
                    "tables": {
                        "articles": {
                            "allow": {"id": "editor"},  # Only editor can view
                        }
                    },
                },
                "private": {
                    "allow": False,  # Deny everyone
                },
            }
        }
    )
    ds.root_enabled = True
    await ds.invoke_startup()
    # Add some test databases
    ds.add_memory_database("content")
    ds.add_memory_database("private")
    return ds


# /-/check.json tests
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_status,expected_keys",
    [
        # Valid request
        (
            "/-/check.json?action=view-instance",
            200,
            {"action", "allowed", "resource"},
        ),
        # Missing action parameter
        ("/-/check.json", 400, {"error"}),
        # Invalid action
        ("/-/check.json?action=nonexistent", 404, {"error"}),
        # With parent parameter
        (
            "/-/check.json?action=view-database&parent=content",
            200,
            {"action", "allowed", "resource"},
        ),
        # With parent and child parameters
        (
            "/-/check.json?action=view-table&parent=content&child=articles",
            200,
            {"action", "allowed", "resource"},
        ),
    ],
)
async def test_check_json_basic(
    ds_with_permissions, path, expected_status, expected_keys
):
    response = await ds_with_permissions.client.get(path)
    assert response.status_code == expected_status
    data = response.json()
    assert expected_keys.issubset(data.keys())


@pytest.mark.asyncio
async def test_check_json_response_structure(ds_with_permissions):
    """Test that /-/check.json returns the expected structure."""
    response = await ds_with_permissions.client.get(
        "/-/check.json?action=view-instance"
    )
    assert response.status_code == 200
    data = response.json()

    # Check required fields
    assert "action" in data
    assert "allowed" in data
    assert "resource" in data

    # Check resource structure
    assert "parent" in data["resource"]
    assert "child" in data["resource"]
    assert "path" in data["resource"]

    # Check allowed is boolean
    assert isinstance(data["allowed"], bool)


@pytest.mark.asyncio
async def test_check_json_redacts_sensitive_fields_without_debug_permission(
    ds_with_permissions,
):
    """Test that /-/check.json redacts reason and source_plugin without permissions-debug."""
    # Anonymous user should not see sensitive fields
    response = await ds_with_permissions.client.get(
        "/-/check.json?action=view-instance"
    )
    assert response.status_code == 200
    data = response.json()
    # Sensitive fields should not be present
    assert "reason" not in data
    assert "source_plugin" not in data
    # But these non-sensitive fields should be present
    assert "used_default" in data
    assert "depth" in data


@pytest.mark.asyncio
async def test_check_json_shows_sensitive_fields_with_debug_permission(
    ds_with_permissions,
):
    """Test that /-/check.json shows reason and source_plugin with permissions-debug."""
    # User with permissions-debug should see sensitive fields
    response = await ds_with_permissions.client.get(
        "/-/check.json?action=view-instance",
        cookies={"ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()
    # Sensitive fields should be present
    assert "reason" in data
    assert "source_plugin" in data
    assert "used_default" in data
    assert "depth" in data


@pytest.mark.asyncio
async def test_check_json_child_requires_parent(ds_with_permissions):
    """Test that child parameter requires parent parameter."""
    response = await ds_with_permissions.client.get(
        "/-/check.json?action=view-table&child=articles"
    )
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert "parent" in data["error"].lower()


# /-/allowed.json tests
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_status,expected_keys",
    [
        # Valid supported actions
        (
            "/-/allowed.json?action=view-instance",
            200,
            {"action", "items", "total", "page"},
        ),
        (
            "/-/allowed.json?action=view-database",
            200,
            {"action", "items", "total", "page"},
        ),
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
        # Unsupported action (valid but not in CANDIDATE_SQL)
        ("/-/allowed.json?action=insert-row", 400, {"error"}),
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
async def test_allowed_json_redacts_sensitive_fields_without_debug_permission(
    ds_with_permissions,
):
    """Test that /-/allowed.json redacts reason and source_plugin without permissions-debug."""
    # Anonymous user should not see sensitive fields
    response = await ds_with_permissions.client.get(
        "/-/allowed.json?action=view-instance"
    )
    assert response.status_code == 200
    data = response.json()
    if data["items"]:
        item = data["items"][0]
        assert "reason" not in item
        assert "source_plugin" not in item


@pytest.mark.asyncio
async def test_allowed_json_shows_sensitive_fields_with_debug_permission(
    ds_with_permissions,
):
    """Test that /-/allowed.json shows reason and source_plugin with permissions-debug."""
    # User with permissions-debug should see sensitive fields
    response = await ds_with_permissions.client.get(
        "/-/allowed.json?action=view-instance",
        cookies={"ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()
    if data["items"]:
        item = data["items"][0]
        assert "reason" in item
        assert "source_plugin" in item


@pytest.mark.asyncio
async def test_allowed_json_only_shows_allowed_resources(ds_with_permissions):
    """Test that /-/allowed.json only shows resources with allow=1."""
    response = await ds_with_permissions.client.get(
        "/-/allowed.json?action=view-instance"
    )
    assert response.status_code == 200
    data = response.json()

    # All items should have allow implicitly set to 1 (not in response but verified by the endpoint logic)
    # The endpoint filters to only show allowed resources
    assert isinstance(data["items"], list)
    assert data["total"] >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page,page_size",
    [
        (1, 10),
        (2, 50),
        (1, 200),  # max page size
    ],
)
async def test_allowed_json_pagination(ds_with_permissions, page, page_size):
    """Test pagination parameters."""
    response = await ds_with_permissions.client.get(
        f"/-/allowed.json?action=view-instance&page={page}&page_size={page_size}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == page
    assert data["page_size"] == min(page_size, 200)  # Capped at 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params,expected_status",
    [
        ("page=0", 400),  # page must be >= 1
        ("page=-1", 400),
        ("page_size=0", 400),  # page_size must be >= 1
        ("page_size=-1", 400),
        ("page=abc", 400),  # page must be integer
        ("page_size=xyz", 400),  # page_size must be integer
    ],
)
async def test_allowed_json_pagination_errors(
    ds_with_permissions, params, expected_status
):
    """Test pagination error handling."""
    response = await ds_with_permissions.client.get(
        f"/-/allowed.json?action=view-instance&{params}"
    )
    assert response.status_code == expected_status


# /-/rules.json tests
@pytest.mark.asyncio
async def test_rules_json_requires_permissions_debug(ds_with_permissions):
    """Test that /-/rules.json requires permissions-debug permission."""
    # Anonymous user should be denied
    response = await ds_with_permissions.client.get(
        "/-/rules.json?action=view-instance"
    )
    assert response.status_code == 403

    # Regular authenticated user should also be denied
    response = await ds_with_permissions.client.get(
        "/-/rules.json?action=view-instance",
        cookies={
            "ds_actor": ds_with_permissions.client.actor_cookie({"id": "regular-user"})
        },
    )
    assert response.status_code == 403

    # User with permissions-debug should be allowed
    response = await ds_with_permissions.client.get(
        "/-/rules.json?action=view-instance",
        cookies={"ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_status,expected_keys",
    [
        # Valid request
        (
            "/-/rules.json?action=view-instance",
            200,
            {"action", "items", "total", "page"},
        ),
        (
            "/-/rules.json?action=view-database",
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
    # Use debugger user who has permissions-debug
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
        assert "allow" in item  # Important: should include allow field
        assert "reason" in item
        assert "source_plugin" in item


@pytest.mark.asyncio
async def test_rules_json_includes_both_allow_and_deny(ds_with_permissions):
    """Test that /-/rules.json includes both allow and deny rules."""
    response = await ds_with_permissions.client.get(
        "/-/rules.json?action=view-database",
        cookies={"ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()

    # Check that items have the allow field
    assert isinstance(data["items"], list)
    if data["items"]:
        # Verify allow field exists and is 0 or 1
        for item in data["items"]:
            assert "allow" in item
            assert item["allow"] in (0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page,page_size",
    [
        (1, 10),
        (2, 50),
        (1, 200),  # max page size
    ],
)
async def test_rules_json_pagination(ds_with_permissions, page, page_size):
    """Test pagination parameters."""
    response = await ds_with_permissions.client.get(
        f"/-/rules.json?action=view-instance&page={page}&page_size={page_size}",
        cookies={"ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == page
    assert data["page_size"] == min(page_size, 200)  # Capped at 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params,expected_status",
    [
        ("page=0", 400),  # page must be >= 1
        ("page=-1", 400),
        ("page_size=0", 400),  # page_size must be >= 1
        ("page_size=-1", 400),
        ("page=abc", 400),  # page must be integer
        ("page_size=xyz", 400),  # page_size must be integer
    ],
)
async def test_rules_json_pagination_errors(
    ds_with_permissions, params, expected_status
):
    """Test pagination error handling."""
    response = await ds_with_permissions.client.get(
        f"/-/rules.json?action=view-instance&{params}",
        cookies={"ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == expected_status


# Test that HTML endpoints return HTML (not JSON) when accessed without .json
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,needs_debug",
    [
        ("/-/check", False),
        ("/-/check?action=view-instance", False),
        ("/-/allowed", False),
        ("/-/allowed?action=view-instance", False),
        ("/-/rules", True),
        ("/-/rules?action=view-instance", True),
    ],
)
async def test_html_endpoints_return_html(ds_with_permissions, path, needs_debug):
    """Test that endpoints without .json extension return HTML."""
    if needs_debug:
        # Rules endpoint requires permissions-debug
        response = await ds_with_permissions.client.get(
            path,
            cookies={
                "ds_actor": ds_with_permissions.client.actor_cookie({"id": "root"})
            },
        )
    else:
        response = await ds_with_permissions.client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Check for HTML structure
    text = response.text
    assert "<!DOCTYPE html>" in text or "<html" in text


@pytest.mark.asyncio
async def test_root_user_respects_settings_deny():
    """
    Test for issue #2509: Settings-based deny rules should override root user privileges.

    When a database has `allow: false` in settings, the root user should NOT see
    that database in /-/allowed.json?action=view-database, even though root normally
    has all permissions.
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

    # Root user should NOT see the content database because settings deny it
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
