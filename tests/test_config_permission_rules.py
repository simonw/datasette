import pytest

from datasette.app import Datasette
from datasette.database import Database
from datasette.resources import DatabaseResource, TableResource


async def setup_datasette(config=None, databases=None):
    ds = Datasette(memory=True, config=config)
    for name in databases or []:
        ds.add_database(Database(ds, memory_name=f"{name}_memory"), name=name)
    await ds.invoke_startup()
    await ds.refresh_schemas()
    return ds


@pytest.mark.asyncio
async def test_root_permissions_allow():
    config = {"permissions": {"execute-sql": {"id": "alice"}}}
    ds = await setup_datasette(config=config, databases=["content"])

    assert await ds.allowed(
        action="execute-sql",
        resource=DatabaseResource(database="content"),
        actor={"id": "alice"},
    )
    assert not await ds.allowed(
        action="execute-sql",
        resource=DatabaseResource(database="content"),
        actor={"id": "bob"},
    )


@pytest.mark.asyncio
async def test_database_permission():
    config = {
        "databases": {
            "content": {
                "permissions": {
                    "insert-row": {"id": "alice"},
                }
            }
        }
    }
    ds = await setup_datasette(config=config, databases=["content"])

    assert await ds.allowed(
        action="insert-row",
        resource=TableResource(database="content", table="repos"),
        actor={"id": "alice"},
    )
    assert not await ds.allowed(
        action="insert-row",
        resource=TableResource(database="content", table="repos"),
        actor={"id": "bob"},
    )


@pytest.mark.asyncio
async def test_table_permission():
    config = {
        "databases": {
            "content": {
                "tables": {"repos": {"permissions": {"delete-row": {"id": "alice"}}}}
            }
        }
    }
    ds = await setup_datasette(config=config, databases=["content"])

    assert await ds.allowed(
        action="delete-row",
        resource=TableResource(database="content", table="repos"),
        actor={"id": "alice"},
    )
    assert not await ds.allowed(
        action="delete-row",
        resource=TableResource(database="content", table="repos"),
        actor={"id": "bob"},
    )


@pytest.mark.asyncio
async def test_view_table_allow_block():
    config = {
        "databases": {"content": {"tables": {"repos": {"allow": {"id": "alice"}}}}}
    }
    ds = await setup_datasette(config=config, databases=["content"])

    assert await ds.allowed(
        action="view-table",
        resource=TableResource(database="content", table="repos"),
        actor={"id": "alice"},
    )
    assert not await ds.allowed(
        action="view-table",
        resource=TableResource(database="content", table="repos"),
        actor={"id": "bob"},
    )
    assert await ds.allowed(
        action="view-table",
        resource=TableResource(database="content", table="other"),
        actor={"id": "bob"},
    )


@pytest.mark.asyncio
async def test_view_table_allow_false_blocks():
    config = {"databases": {"content": {"tables": {"repos": {"allow": False}}}}}
    ds = await setup_datasette(config=config, databases=["content"])

    assert not await ds.allowed(
        action="view-table",
        resource=TableResource(database="content", table="repos"),
        actor={"id": "alice"},
    )


@pytest.mark.asyncio
async def test_allow_sql_blocks():
    config = {"allow_sql": {"id": "alice"}}
    ds = await setup_datasette(config=config, databases=["content"])

    assert await ds.allowed(
        action="execute-sql",
        resource=DatabaseResource(database="content"),
        actor={"id": "alice"},
    )
    assert not await ds.allowed(
        action="execute-sql",
        resource=DatabaseResource(database="content"),
        actor={"id": "bob"},
    )

    config = {"databases": {"content": {"allow_sql": {"id": "bob"}}}}
    ds = await setup_datasette(config=config, databases=["content"])

    assert await ds.allowed(
        action="execute-sql",
        resource=DatabaseResource(database="content"),
        actor={"id": "bob"},
    )
    assert not await ds.allowed(
        action="execute-sql",
        resource=DatabaseResource(database="content"),
        actor={"id": "alice"},
    )

    config = {"allow_sql": False}
    ds = await setup_datasette(config=config, databases=["content"])
    assert not await ds.allowed(
        action="execute-sql",
        resource=DatabaseResource(database="content"),
        actor={"id": "alice"},
    )


@pytest.mark.asyncio
async def test_view_instance_allow_block():
    config = {"allow": {"id": "alice"}}
    ds = await setup_datasette(config=config)

    assert await ds.allowed(action="view-instance", actor={"id": "alice"})
    assert not await ds.allowed(action="view-instance", actor={"id": "bob"})


@pytest.mark.asyncio
async def test_private_mode_denies_all_by_default():
    """Test --private flag blocks all access unless explicitly allowed"""
    ds = Datasette(memory=True, private=True)
    ds.add_database(Database(ds, memory_name="test_memory"), name="test")
    await ds.invoke_startup()
    await ds.refresh_schemas()

    # Unauthenticated access should be denied for all default actions
    assert not await ds.allowed(action="view-instance", actor=None)
    assert not await ds.allowed(
        action="view-database", resource=DatabaseResource(database="test"), actor=None
    )
    assert not await ds.allowed(
        action="view-table",
        resource=TableResource(database="test", table="test"),
        actor=None,
    )

    # Even authenticated users should be denied in private mode
    assert not await ds.allowed(action="view-instance", actor={"id": "alice"})
    assert not await ds.allowed(
        action="view-database",
        resource=DatabaseResource(database="test"),
        actor={"id": "alice"},
    )


@pytest.mark.asyncio
async def test_private_mode_with_explicit_allow():
    """Test --private flag allows explicitly configured permissions"""
    config = {"permissions": {"view-instance": {"id": "alice"}}}
    ds = Datasette(memory=True, private=True, config=config)
    ds.add_database(Database(ds, memory_name="test_memory"), name="test")
    await ds.invoke_startup()
    await ds.refresh_schemas()

    # Alice should be allowed due to explicit config
    assert await ds.allowed(action="view-instance", actor={"id": "alice"})

    # Bob should still be denied
    assert not await ds.allowed(action="view-instance", actor={"id": "bob"})

    # Unauthenticated should be denied
    assert not await ds.allowed(action="view-instance", actor=None)


@pytest.mark.asyncio
async def test_require_auth_mode_allows_authenticated():
    """Test --require-auth flag allows actors with id"""
    ds = Datasette(memory=True, require_auth=True)
    ds.add_database(Database(ds, memory_name="test_memory"), name="test")
    await ds.invoke_startup()
    await ds.refresh_schemas()

    # Authenticated users should be allowed
    assert await ds.allowed(action="view-instance", actor={"id": "alice"})
    assert await ds.allowed(
        action="view-database",
        resource=DatabaseResource(database="test"),
        actor={"id": "bob"},
    )
    assert await ds.allowed(
        action="view-table",
        resource=TableResource(database="test", table="test"),
        actor={"id": "charlie"},
    )

    # Unauthenticated access should be denied
    assert not await ds.allowed(action="view-instance", actor=None)
    assert not await ds.allowed(
        action="view-database", resource=DatabaseResource(database="test"), actor=None
    )

    # Actor without id should be denied
    assert not await ds.allowed(action="view-instance", actor={"name": "anonymous"})


@pytest.mark.asyncio
async def test_require_auth_mode_with_restrictions():
    """Test --require-auth mode works with actor restrictions"""
    # Test with actor that has restrictions
    ds = Datasette(memory=True, require_auth=True)
    ds.add_database(Database(ds, memory_name="test_memory"), name="test")
    await ds.invoke_startup()
    await ds.refresh_schemas()

    # Actor with restrictions should have those restrictions applied
    restricted_actor = {"id": "alice", "_r": {"a": ["view-table"]}}
    # This actor has restrictions, so default allow won't apply
    # Instead their restrictions define what they can do
    assert await ds.allowed(
        action="view-table",
        resource=TableResource(database="test", table="test"),
        actor=restricted_actor,
    )

    # Regular authenticated actor without restrictions should get default allow
    normal_actor = {"id": "bob"}
    assert await ds.allowed(
        action="view-database",
        resource=DatabaseResource(database="test"),
        actor=normal_actor,
    )


@pytest.mark.asyncio
async def test_normal_mode_allows_all():
    """Test default behavior without --private or --require-auth"""
    ds = Datasette(memory=True, private=False, require_auth=False)
    ds.add_database(Database(ds, memory_name="test_memory"), name="test")
    await ds.invoke_startup()
    await ds.refresh_schemas()

    # Everyone should be allowed in normal mode
    assert await ds.allowed(action="view-instance", actor=None)
    assert await ds.allowed(
        action="view-database", resource=DatabaseResource(database="test"), actor=None
    )
    assert await ds.allowed(action="view-instance", actor={"id": "alice"})
    assert await ds.allowed(
        action="view-database",
        resource=DatabaseResource(database="test"),
        actor={"id": "bob"},
    )
