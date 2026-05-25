import pytest

from datasette.app import Datasette


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
