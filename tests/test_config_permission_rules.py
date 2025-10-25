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
