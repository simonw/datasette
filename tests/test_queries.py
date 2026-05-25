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

    assert [(r.parent, r.child) for r in page.resources] == [("data", "internal_query")]


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


@pytest.mark.asyncio
async def test_query_insert_api_creates_read_only_query():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_insert_api", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/queries/-/insert",
        actor={"id": "root"},
        json={
            "query": {
                "name": "by_name",
                "sql": "select * from dogs where name = :name",
                "title": "By name",
            }
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["ok"] is True
    assert data["query"]["name"] == "by_name"
    assert data["query"]["parameters"] == ["name"]
    assert data["query"]["is_write"] is False
    assert data["query"]["source"] == "user"
    assert data["query"]["owner_id"] == "root"


@pytest.mark.asyncio
async def test_query_list_and_definition_api():
    ds = Datasette(memory=True)
    ds.root_enabled = True
    ds.add_memory_database("query_list_api", name="data")
    await ds.invoke_startup()
    await ds.add_query("data", "listed", "select 1", title="Listed", published=True)

    list_response = await ds.client.get(
        "/data/-/queries",
        actor={"id": "root"},
    )
    definition_response = await ds.client.get(
        "/data/listed/-/definition",
        actor={"id": "root"},
    )

    assert list_response.status_code == 200
    assert list_response.json()["queries"][0]["name"] == "listed"
    assert definition_response.status_code == 200
    assert definition_response.json()["query"]["title"] == "Listed"


@pytest.mark.asyncio
async def test_query_insert_api_publish_requires_publish_query():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-sql": {"id": "writer"},
                        "insert-query": {"id": "writer"},
                    }
                }
            }
        },
    )
    ds.add_memory_database("query_publish_api", name="data")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/queries/-/insert",
        actor={"id": "writer"},
        json={"query": {"name": "public", "sql": "select 1", "published": True}},
    )

    assert response.status_code == 403
    assert response.json()["errors"] == ["Permission denied: need publish-query"]


@pytest.mark.asyncio
async def test_query_insert_api_creates_writable_query():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_write_api", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/queries/-/insert",
        actor={"id": "root"},
        json={
            "query": {
                "name": "insert_dog",
                "sql": "insert into dogs (name) values (:name)",
            }
        },
    )

    assert response.status_code == 201
    query = response.json()["query"]
    assert query["is_write"] is True
    assert query["published"] is False
    assert query["parameters"] == ["name"]

    bad_response = await ds.client.post(
        "/data/-/queries/-/insert",
        actor={"id": "root"},
        json={
            "query": {
                "name": "published_insert",
                "sql": "insert into dogs (name) values (:name)",
                "published": True,
            }
        },
    )

    assert bad_response.status_code == 400
    assert bad_response.json()["errors"] == ["Writable queries cannot be published"]


@pytest.mark.asyncio
async def test_query_update_and_delete_api():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    ds.add_memory_database("query_update_api", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "editable",
        "select 1",
        title="Original",
        source="user",
        owner_id="root",
    )

    update_response = await ds.client.post(
        "/data/editable/-/update",
        actor={"id": "root"},
        json={
            "update": {
                "title": "Updated",
                "description": "Fresh",
                "on_success_redirect": None,
            },
            "return": True,
        },
    )

    assert update_response.status_code == 200
    updated = update_response.json()["query"]
    assert updated["title"] == "Updated"
    assert updated["description"] == "Fresh"
    assert updated["on_success_redirect"] is None

    delete_response = await ds.client.post(
        "/data/editable/-/delete",
        actor={"id": "root"},
        json={},
    )

    assert delete_response.status_code == 200
    assert delete_response.json() == {"ok": True}
    assert await ds.get_query("data", "editable") is None


@pytest.mark.asyncio
async def test_query_insert_api_rejects_magic_parameters():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    ds.add_memory_database("query_magic_api", name="data")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/queries/-/insert",
        actor={"id": "root"},
        json={"query": {"name": "magic", "sql": "select :_actor_id"}},
    )

    assert response.status_code == 400
    assert response.json()["errors"] == ["Magic parameters are not allowed"]


@pytest.mark.asyncio
async def test_create_query_ui_and_arbitrary_sql_save_link():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_create_ui", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    create_response = await ds.client.get(
        "/data/-/queries/-/create?sql=select+*+from+dogs",
        actor={"id": "root"},
    )
    query_response = await ds.client.get(
        "/data/-/query?sql=select+*+from+dogs",
        actor={"id": "root"},
    )

    assert create_response.status_code == 200
    assert "Create query" in create_response.text
    assert "Read-only" in create_response.text
    assert "Writable" in create_response.text
    assert "required permission" in create_response.text
    assert query_response.status_code == 200
    assert "Save query" in query_response.text
    assert "/data/-/queries/-/create?sql=select+%2A+from+dogs" in query_response.text


@pytest.mark.asyncio
async def test_query_owner_gets_update_delete_and_writable_view_defaults():
    ds = Datasette(memory=True, default_deny=True)
    ds.add_memory_database("query_owner_defaults", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "insert_dog",
        "insert into dogs (name) values (:name)",
        is_write=True,
        source="user",
        owner_id="alice",
    )

    for action in ("view-query", "update-query", "delete-query"):
        assert await ds.allowed(
            action=action,
            resource=QueryResource("data", "insert_dog"),
            actor={"id": "alice"},
        )
        assert not await ds.allowed(
            action=action,
            resource=QueryResource("data", "insert_dog"),
            actor={"id": "bob"},
        )


@pytest.mark.asyncio
async def test_user_writable_query_execution_rechecks_table_permissions():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "tables": {
                        "dogs": {
                            "permissions": {
                                "insert-row": {"id": "alice"},
                            }
                        }
                    }
                }
            }
        },
    )
    db = ds.add_memory_database("query_write_execution", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "insert_dog",
        "insert into dogs (name) values (:name)",
        is_write=True,
        source="user",
        owner_id="alice",
    )
    await ds.add_query(
        "data",
        "insert_cat",
        "insert into dogs (name) values (:name)",
        is_write=True,
        source="user",
        owner_id="bob",
    )

    allowed_response = await ds.client.post(
        "/data/insert_dog?_json=1",
        actor={"id": "alice"},
        data={"name": "Cleo"},
    )
    denied_response = await ds.client.post(
        "/data/insert_cat?_json=1",
        actor={"id": "bob"},
        data={"name": "Milo"},
    )

    assert allowed_response.status_code == 200
    assert allowed_response.json()["ok"] is True
    assert denied_response.status_code == 403
    rows = (await db.execute("select name from dogs")).dicts()
    assert rows == [{"name": "Cleo"}]
