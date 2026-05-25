import pytest

from datasette.app import Datasette
from datasette.resources import DatabaseResource, QueryResource
from datasette.utils.asgi import Forbidden


@pytest.mark.asyncio
async def test_queries_internal_table_schema():
    ds = Datasette(memory=True)
    await ds.invoke_startup()
    internal_db = ds.get_internal_database()

    columns = [
        row["name"]
        for row in (
            await internal_db.execute("select name from pragma_table_info('queries')")
        )
    ]

    assert columns == [
        "database_name",
        "name",
        "sql",
        "title",
        "description",
        "description_html",
        "hide_sql",
        "fragment",
        "parameters",
        "is_write",
        "published",
        "source",
        "owner_id",
        "on_success_message",
        "on_success_message_sql",
        "on_success_redirect",
        "on_error_message",
        "on_error_redirect",
        "created_at",
        "updated_at",
    ]


@pytest.mark.asyncio
async def test_add_get_and_remove_query():
    ds = Datasette(memory=True)
    ds.add_memory_database("query_api", name="data")
    await ds.invoke_startup()

    await ds.add_query(
        "data",
        "top_customers",
        "select * from customers where region = :region",
        title="Top customers",
        description="Customers by region",
        hide_sql=True,
        fragment="chart",
        parameters=["region"],
        published=True,
        source="user",
        owner_id="alice",
    )

    query = await ds.get_query("data", "top_customers")
    assert query == {
        "database": "data",
        "name": "top_customers",
        "sql": "select * from customers where region = :region",
        "title": "Top customers",
        "description": "Customers by region",
        "description_html": None,
        "hide_sql": True,
        "fragment": "chart",
        "params": ["region"],
        "parameters": ["region"],
        "is_write": False,
        "write": False,
        "published": True,
        "source": "user",
        "owner_id": "alice",
        "on_success_message": None,
        "on_success_message_sql": None,
        "on_success_redirect": None,
        "on_error_message": None,
        "on_error_redirect": None,
    }

    assert await ds.get_queries("data") == {"top_customers": query}

    await ds.remove_query("data", "top_customers")
    assert await ds.get_query("data", "top_customers") is None
    assert await ds.get_queries("data") == {}


@pytest.mark.asyncio
async def test_update_query_only_updates_provided_fields():
    ds = Datasette(memory=True)
    ds.add_memory_database("query_api_update", name="data")
    await ds.invoke_startup()

    await ds.add_query(
        "data",
        "redirect",
        "select 1",
        title="Original",
        on_success_redirect="/original",
        parameters=["one"],
    )

    await ds.update_query(
        "data",
        "redirect",
        title="Updated",
        parameters=[],
        on_success_redirect=None,
    )

    query = await ds.get_query("data", "redirect")
    assert query["title"] == "Updated"
    assert query["parameters"] == []
    assert query["params"] == []
    assert query["on_success_redirect"] is None
    assert query["sql"] == "select 1"
    assert query["published"] is False


@pytest.mark.asyncio
async def test_config_queries_imported_to_internal_table():
    ds = Datasette(
        memory=True,
        config={
            "databases": {
                "data": {
                    "queries": {
                        "configured": {
                            "sql": "select :name as name",
                            "title": "Configured query",
                            "params": ["name"],
                        }
                    }
                }
            }
        },
    )
    ds.add_memory_database("query_config", name="data")
    await ds.invoke_startup()

    assert await ds.get_query("data", "configured") == {
        "database": "data",
        "name": "configured",
        "sql": "select :name as name",
        "title": "Configured query",
        "description": None,
        "description_html": None,
        "hide_sql": False,
        "fragment": None,
        "params": ["name"],
        "parameters": ["name"],
        "is_write": False,
        "write": False,
        "published": False,
        "source": "config",
        "owner_id": None,
        "on_success_message": None,
        "on_success_message_sql": None,
        "on_success_redirect": None,
        "on_error_message": None,
        "on_error_redirect": None,
    }


@pytest.mark.asyncio
async def test_query_resources_come_from_internal_table():
    ds = Datasette(memory=True)
    ds.add_memory_database("query_resources", name="data")
    await ds.invoke_startup()
    await ds.add_query("data", "internal_query", "select 1", source="user")

    page = await ds.allowed_resources("view-query", actor=None)

    assert [(r.parent, r.child) for r in page.resources] == [
        ("data", "internal_query")
    ]


@pytest.mark.asyncio
async def test_unpublished_query_requires_execute_sql_but_published_does_not():
    ds = Datasette(memory=True, settings={"default_allow_sql": False})
    ds.add_memory_database("query_permissions", name="data")
    await ds.invoke_startup()
    await ds.add_query("data", "unpublished", "select 1", published=False)
    await ds.add_query("data", "published", "select 1", published=True)

    assert not await ds.allowed(
        action="execute-sql",
        resource=DatabaseResource("data"),
        actor=None,
    )
    assert not await ds.allowed(
        action="view-query",
        resource=QueryResource("data", "unpublished"),
        actor=None,
    )
    assert await ds.allowed(
        action="view-query",
        resource=QueryResource("data", "published"),
        actor=None,
    )


@pytest.mark.asyncio
async def test_query_actions_are_registered():
    ds = Datasette()
    await ds.invoke_startup()

    assert ds.get_action("insert-query").resource_class is DatabaseResource
    assert ds.get_action("publish-query").resource_class is DatabaseResource
    assert ds.get_action("update-query").resource_class is QueryResource
    assert ds.get_action("delete-query").resource_class is QueryResource


@pytest.mark.asyncio
async def test_analyze_write_query_requires_table_permissions():
    ds = Datasette(memory=True, default_deny=True)
    db = ds.add_memory_database("query_write_permissions", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    actor = {"id": "writer"}
    await ds.add_query(
        "data",
        "write_dog",
        "insert into dogs (name) values (:name)",
        is_write=True,
        source="user",
        owner_id="writer",
    )

    with pytest.raises(Forbidden):
        await ds.ensure_query_write_permissions(
            "data",
            "insert into dogs (name) values (:name)",
            actor=actor,
        )

    ds.config = {
        "databases": {
            "data": {
                "tables": {
                    "dogs": {
                        "permissions": {
                            "insert-row": {"id": "writer"},
                        }
                    }
                }
            }
        }
    }

    await ds.ensure_query_write_permissions(
        "data",
        "insert into dogs (name) values (:name)",
        actor=actor,
    )


@pytest.mark.asyncio
async def test_analyze_write_query_rejects_writes_to_attached_databases():
    ds = Datasette(memory=True, default_deny=True)
    db = ds.add_memory_database("query_attached_writes", name="data")
    await db.execute_write("attach database ':memory:' as extra")
    await db.execute_write("create table extra.cats (id integer primary key)")
    await ds.invoke_startup()

    with pytest.raises(Forbidden):
        await ds.ensure_query_write_permissions(
            "data",
            "insert into extra.cats (id) values (1)",
            actor={"id": "writer"},
        )
