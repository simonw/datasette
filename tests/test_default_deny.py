import pytest
from datasette.app import Datasette
from datasette.resources import DatabaseResource, TableResource


@pytest.mark.asyncio
async def test_default_deny_denies_default_permissions():
    """Test that default_deny=True denies default permissions"""
    # Without default_deny, anonymous users can view instance/database/tables
    ds_normal = Datasette()
    await ds_normal.invoke_startup()

    # Add a test database
    db = ds_normal.add_memory_database("test_db_normal")
    await db.execute_write("create table test_table (id integer primary key)")
    await ds_normal._refresh_schemas()  # Trigger catalog refresh

    # Test default behavior - anonymous user should be able to view
    response = await ds_normal.client.get("/")
    assert response.status_code == 200

    response = await ds_normal.client.get("/test_db_normal")
    assert response.status_code == 200

    response = await ds_normal.client.get("/test_db_normal/test_table")
    assert response.status_code == 200

    # With default_deny=True, anonymous users should be denied
    ds_deny = Datasette(default_deny=True)
    await ds_deny.invoke_startup()

    # Add the same test database
    db = ds_deny.add_memory_database("test_db_deny")
    await db.execute_write("create table test_table (id integer primary key)")
    await ds_deny._refresh_schemas()  # Trigger catalog refresh

    # Anonymous user should be denied
    response = await ds_deny.client.get("/")
    assert response.status_code == 403

    response = await ds_deny.client.get("/test_db_deny")
    assert response.status_code == 403

    response = await ds_deny.client.get("/test_db_deny/test_table")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_default_deny_with_root_user():
    """Test that root user still has access when default_deny=True"""
    ds = Datasette(default_deny=True)
    ds.root_enabled = True
    await ds.invoke_startup()

    root_actor = {"id": "root"}

    # Root user should have all permissions even with default_deny
    assert await ds.allowed(action="view-instance", actor=root_actor) is True
    assert (
        await ds.allowed(
            action="view-database",
            actor=root_actor,
            resource=DatabaseResource("test_db"),
        )
        is True
    )
    assert (
        await ds.allowed(
            action="view-table",
            actor=root_actor,
            resource=TableResource("test_db", "test_table"),
        )
        is True
    )
    assert (
        await ds.allowed(
            action="execute-sql", actor=root_actor, resource=DatabaseResource("test_db")
        )
        is True
    )


@pytest.mark.asyncio
async def test_default_deny_with_config_allow():
    """Test that config allow rules still work with default_deny=True"""
    ds = Datasette(default_deny=True, config={"allow": {"id": "user1"}})
    await ds.invoke_startup()

    # Anonymous user should be denied
    assert await ds.allowed(action="view-instance", actor=None) is False

    # Authenticated user with explicit permission should have access
    assert await ds.allowed(action="view-instance", actor={"id": "user1"}) is True

    # Different user should be denied
    assert await ds.allowed(action="view-instance", actor={"id": "user2"}) is False


@pytest.mark.asyncio
async def test_default_deny_basic_permissions():
    """Test that default_deny=True denies basic permissions"""
    ds = Datasette(default_deny=True)
    await ds.invoke_startup()

    # Anonymous user should be denied all default permissions
    assert await ds.allowed(action="view-instance", actor=None) is False
    assert (
        await ds.allowed(
            action="view-database", actor=None, resource=DatabaseResource("test_db")
        )
        is False
    )
    assert (
        await ds.allowed(
            action="view-table",
            actor=None,
            resource=TableResource("test_db", "test_table"),
        )
        is False
    )
    assert (
        await ds.allowed(
            action="execute-sql", actor=None, resource=DatabaseResource("test_db")
        )
        is False
    )

    # Authenticated user without explicit permission should also be denied
    assert await ds.allowed(action="view-instance", actor={"id": "user"}) is False
