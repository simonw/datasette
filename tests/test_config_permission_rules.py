import pytest

from datasette.app import Datasette
from datasette.database import Database


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

    assert await ds.permission_allowed_2({"id": "alice"}, "execute-sql", "content")
    assert not await ds.permission_allowed_2({"id": "bob"}, "execute-sql", "content")


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

    assert await ds.permission_allowed_2(
        {"id": "alice"}, "insert-row", ("content", "repos")
    )
    assert not await ds.permission_allowed_2(
        {"id": "bob"}, "insert-row", ("content", "repos")
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

    assert await ds.permission_allowed_2(
        {"id": "alice"}, "delete-row", ("content", "repos")
    )
    assert not await ds.permission_allowed_2(
        {"id": "bob"}, "delete-row", ("content", "repos")
    )


@pytest.mark.asyncio
async def test_view_table_allow_block():
    config = {
        "databases": {"content": {"tables": {"repos": {"allow": {"id": "alice"}}}}}
    }
    ds = await setup_datasette(config=config, databases=["content"])

    assert await ds.permission_allowed_2(
        {"id": "alice"}, "view-table", ("content", "repos")
    )
    assert not await ds.permission_allowed_2(
        {"id": "bob"}, "view-table", ("content", "repos")
    )
    assert await ds.permission_allowed_2(
        {"id": "bob"}, "view-table", ("content", "other")
    )


@pytest.mark.asyncio
async def test_view_table_allow_false_blocks():
    config = {"databases": {"content": {"tables": {"repos": {"allow": False}}}}}
    ds = await setup_datasette(config=config, databases=["content"])

    assert not await ds.permission_allowed_2(
        {"id": "alice"}, "view-table", ("content", "repos")
    )


@pytest.mark.asyncio
async def test_allow_sql_blocks():
    config = {"allow_sql": {"id": "alice"}}
    ds = await setup_datasette(config=config, databases=["content"])

    assert await ds.permission_allowed_2({"id": "alice"}, "execute-sql", "content")
    assert not await ds.permission_allowed_2({"id": "bob"}, "execute-sql", "content")

    config = {"databases": {"content": {"allow_sql": {"id": "bob"}}}}
    ds = await setup_datasette(config=config, databases=["content"])

    assert await ds.permission_allowed_2({"id": "bob"}, "execute-sql", "content")
    assert not await ds.permission_allowed_2({"id": "alice"}, "execute-sql", "content")

    config = {"allow_sql": False}
    ds = await setup_datasette(config=config, databases=["content"])
    assert not await ds.permission_allowed_2({"id": "alice"}, "execute-sql", "content")


@pytest.mark.asyncio
async def test_view_instance_allow_block():
    config = {"allow": {"id": "alice"}}
    ds = await setup_datasette(config=config)

    assert await ds.permission_allowed_2({"id": "alice"}, "view-instance")
    assert not await ds.permission_allowed_2({"id": "bob"}, "view-instance")
