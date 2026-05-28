import json

import pytest

from datasette.app import Datasette
from datasette.resources import DatabaseResource, QueryResource
from datasette.stored_queries import StoredQuery, StoredQueryPage
from datasette.utils.asgi import Forbidden


async def add_numbered_queries(ds, database, count):
    for i in range(1, count + 1):
        await ds.add_query(
            database,
            "demo_query_{:02d}".format(i),
            "select {} as query_number".format(i),
            title="Demo query {:02d}".format(i),
            description="Seeded demo query number {:02d}".format(i),
            source="user",
            owner_id="root",
        )


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
        "options",
        "parameters",
        "is_write",
        "is_private",
        "is_trusted",
        "source",
        "owner_id",
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
        is_trusted=True,
        source="user",
        owner_id="alice",
    )

    options_row = (
        await ds.get_internal_database().execute(
            """
            SELECT options FROM queries
            WHERE database_name = ? AND name = ?
            """,
            ["data", "top_customers"],
        )
    ).first()
    assert json.loads(options_row["options"]) == {
        "fragment": "chart",
        "hide_sql": True,
    }

    query = await ds.get_query("data", "top_customers")
    assert query == StoredQuery(
        database="data",
        name="top_customers",
        sql="select * from customers where region = :region",
        title="Top customers",
        description="Customers by region",
        description_html=None,
        hide_sql=True,
        fragment="chart",
        parameters=["region"],
        is_write=False,
        is_private=False,
        is_trusted=True,
        source="user",
        owner_id="alice",
        on_success_message=None,
        on_success_message_sql=None,
        on_success_redirect=None,
        on_error_message=None,
        on_error_redirect=None,
    )

    queries_page = await ds.list_queries("data", actor=None)
    assert queries_page == StoredQueryPage(
        queries=[query],
        next=None,
        has_more=False,
        limit=50,
    )

    await ds.remove_query("data", "top_customers")
    assert await ds.get_query("data", "top_customers") is None
    queries_page = await ds.list_queries("data", actor=None)
    assert queries_page.queries == []
    assert queries_page.next is None


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

    options_row = (
        await ds.get_internal_database().execute(
            """
            SELECT options FROM queries
            WHERE database_name = ? AND name = ?
            """,
            ["data", "redirect"],
        )
    ).first()
    assert json.loads(options_row["options"]) == {"on_success_redirect": "/original"}

    await ds.update_query(
        "data",
        "redirect",
        title="Updated",
        parameters=[],
        on_success_redirect=None,
    )

    query = await ds.get_query("data", "redirect")
    assert query.title == "Updated"
    assert query.parameters == []
    assert query.on_success_redirect is None
    assert query.sql == "select 1"
    assert query.is_private is False
    assert query.is_trusted is False
    options_row = (
        await ds.get_internal_database().execute(
            """
            SELECT options FROM queries
            WHERE database_name = ? AND name = ?
            """,
            ["data", "redirect"],
        )
    ).first()
    assert json.loads(options_row["options"]) == {}


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
                            "description_html": "<p>Configured HTML</p>",
                            "params": ["name"],
                            "on_success_message_sql": "select 'Hello ' || :name",
                        }
                    }
                }
            }
        },
    )
    ds.add_memory_database("query_config", name="data")
    await ds.invoke_startup()

    assert await ds.get_query("data", "configured") == StoredQuery(
        database="data",
        name="configured",
        sql="select :name as name",
        title="Configured query",
        description=None,
        description_html="<p>Configured HTML</p>",
        hide_sql=False,
        fragment=None,
        parameters=["name"],
        is_write=False,
        is_private=False,
        is_trusted=True,
        source="config",
        owner_id=None,
        on_success_message=None,
        on_success_message_sql="select 'Hello ' || :name",
        on_success_redirect=None,
        on_error_message=None,
        on_error_redirect=None,
    )


@pytest.mark.asyncio
async def test_query_resources_come_from_internal_table():
    ds = Datasette(memory=True)
    ds.add_memory_database("query_resources", name="data")
    await ds.invoke_startup()
    await ds.add_query("data", "internal_query", "select 1", source="user")

    page = await ds.allowed_resources("view-query", actor=None)

    assert [(r.parent, r.child) for r in page.resources] == [("data", "internal_query")]


@pytest.mark.asyncio
async def test_default_deny_blocks_view_query_even_for_trusted_query():
    ds = Datasette(memory=True, default_deny=True)
    ds.add_memory_database("query_permissions", name="data")
    await ds.invoke_startup()
    await ds.add_query("data", "trusted", "select 1", is_trusted=True)

    assert not await ds.allowed(
        action="view-query",
        resource=QueryResource("data", "trusted"),
        actor=None,
    )


@pytest.mark.asyncio
async def test_view_query_default_allow_still_respects_private_restriction():
    ds = Datasette(memory=True)
    ds.add_memory_database("default_view_query_permissions", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "private_report",
        "select 1",
        is_private=True,
        source="user",
        owner_id="alice",
    )
    await ds.add_query(
        "data",
        "shared_report",
        "select 2",
        is_private=False,
        source="user",
        owner_id="alice",
    )

    assert await ds.allowed(
        action="view-query",
        resource=QueryResource("data", "shared_report"),
        actor=None,
    )
    assert await ds.allowed(
        action="view-query",
        resource=QueryResource("data", "private_report"),
        actor={"id": "alice"},
    )
    assert not await ds.allowed(
        action="view-query",
        resource=QueryResource("data", "private_report"),
        actor={"id": "bob"},
    )


@pytest.mark.asyncio
async def test_private_query_restriction_blocks_broad_view_query_permission():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-query": {"id": "*"},
                    }
                }
            }
        },
    )
    ds.add_memory_database("private_query_permissions", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "private_report",
        "select 1",
        is_private=True,
        source="user",
        owner_id="alice",
    )
    await ds.add_query(
        "data",
        "shared_report",
        "select 2",
        is_private=False,
        source="user",
        owner_id="alice",
    )

    assert await ds.allowed(
        action="view-query",
        resource=QueryResource("data", "private_report"),
        actor={"id": "alice"},
    )
    assert not await ds.allowed(
        action="view-query",
        resource=QueryResource("data", "private_report"),
        actor={"id": "bob"},
    )
    assert await ds.allowed(
        action="view-query",
        resource=QueryResource("data", "shared_report"),
        actor={"id": "bob"},
    )


@pytest.mark.asyncio
async def test_config_query_restriction_does_not_override_private_internal_query():
    ds = Datasette(memory=True, default_deny=True)
    ds.add_memory_database("private_query_with_config_name", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "private_report",
        "select 1",
        is_private=True,
        source="user",
        owner_id="alice",
    )
    ds.config = {
        "databases": {
            "data": {
                "permissions": {"view-query": {"id": "*"}},
                "queries": {"private_report": {"sql": "select 2"}},
            }
        }
    }

    assert not await ds.allowed(
        action="view-query",
        resource=QueryResource("data", "private_report"),
        actor={"id": "bob"},
    )


@pytest.mark.asyncio
async def test_untrusted_shared_query_execution_requires_execute_sql():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "viewer"},
                        "view-query": {"id": "viewer"},
                    }
                }
            }
        },
    )
    ds.add_memory_database("untrusted_query_execution", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "shared_report",
        "select 1 as one",
        is_private=False,
        is_trusted=False,
        source="user",
        owner_id="alice",
    )

    denied_get = await ds.client.get("/data/shared_report.json", actor={"id": "viewer"})
    denied_post = await ds.client.post(
        "/data/shared_report",
        actor={"id": "viewer"},
        data={},
    )
    assert denied_get.status_code == 403
    assert denied_post.status_code == 403

    ds.config["databases"]["data"]["permissions"]["execute-sql"] = {"id": "viewer"}
    allowed = await ds.client.get("/data/shared_report.json", actor={"id": "viewer"})
    assert allowed.status_code == 200
    assert allowed.json()["rows"] == [{"one": 1}]


@pytest.mark.asyncio
async def test_config_queries_are_trusted_by_default_but_can_opt_out():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-query": {"id": "viewer"},
                    },
                    "queries": {
                        "trusted_report": {"sql": "select 1 as one"},
                        "untrusted_report": {
                            "sql": "select 2 as two",
                            "is_trusted": False,
                        },
                    },
                }
            }
        },
    )
    ds.add_memory_database("trusted_query_config", name="data")
    await ds.invoke_startup()

    trusted = await ds.client.get("/data/trusted_report.json", actor={"id": "viewer"})
    untrusted = await ds.client.get(
        "/data/untrusted_report.json", actor={"id": "viewer"}
    )

    assert trusted.status_code == 200
    assert trusted.json()["rows"] == [{"one": 1}]
    assert untrusted.status_code == 403


@pytest.mark.asyncio
async def test_database_page_query_preview_is_limited():
    ds = Datasette(memory=True)
    ds.add_memory_database("query_preview", name="data")
    await ds.invoke_startup()
    await add_numbered_queries(ds, "data", 25)

    html_response = await ds.client.get("/data")
    json_response = await ds.client.get("/data.json")

    assert html_response.status_code == 200
    assert "Demo query 05" in html_response.text
    assert "Demo query 06" not in html_response.text
    assert '<a href="/data/-/queries">View 25 queries</a>' in html_response.text
    assert len(json_response.json()["queries"]) == 5
    assert json_response.json()["queries_more"] is True
    assert json_response.json()["queries_count"] == 25


@pytest.mark.asyncio
async def test_query_actions_are_registered():
    ds = Datasette()
    await ds.invoke_startup()

    assert ds.get_action("execute-write-sql").resource_class is DatabaseResource
    assert ds.get_action("store-query").resource_class is DatabaseResource
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
                            "update-row": {"id": "writer"},
                            "delete-row": {"id": "writer"},
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
async def test_query_store_api_creates_read_only_query():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_store_api", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/queries/store",
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
    await add_numbered_queries(ds, "data", 12)

    list_response = await ds.client.get(
        "/data/-/queries.json?_size=5",
        actor={"id": "root"},
    )
    next_response = await ds.client.get(
        "/data/-/queries.json?_size=5&_next={}".format(list_response.json()["next"]),
        actor={"id": "root"},
    )
    definition_response = await ds.client.get(
        "/data/demo_query_01/-/definition",
        actor={"id": "root"},
    )

    assert list_response.status_code == 200
    assert [query["name"] for query in list_response.json()["queries"]] == [
        "demo_query_01",
        "demo_query_02",
        "demo_query_03",
        "demo_query_04",
        "demo_query_05",
    ]
    assert list_response.json()["next"]
    assert [query["name"] for query in next_response.json()["queries"]] == [
        "demo_query_06",
        "demo_query_07",
        "demo_query_08",
        "demo_query_09",
        "demo_query_10",
    ]
    assert definition_response.status_code == 200
    assert definition_response.json()["query"]["title"] == "Demo query 01"


@pytest.mark.asyncio
async def test_query_page_does_not_show_internal_source():
    ds = Datasette(memory=True)
    ds.add_memory_database("query_page_source", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "stored_report",
        "select 1 as one",
        title="Stored report",
        source="user",
        owner_id="root",
    )

    response = await ds.client.get("/data/stored_report", actor={"id": "root"})

    assert response.status_code == 200
    assert "Stored report" in response.text
    assert "Data source:" not in response.text


@pytest.mark.asyncio
async def test_query_list_search_filter_and_html():
    ds = Datasette(memory=True)
    ds.root_enabled = True
    ds.add_memory_database("query_list_html", name="data")
    await ds.invoke_startup()
    await add_numbered_queries(ds, "data", 3)
    await ds.add_query(
        "data",
        "private_query",
        "select 'private'",
        title="Private query",
        is_private=True,
        source="user",
        owner_id="root",
    )
    await ds.add_query(
        "data",
        "trusted_query",
        "select 'trusted'",
        title="Trusted query",
        is_trusted=True,
        source="config",
    )
    await ds.add_query(
        "data",
        "writable_query",
        "insert into dogs (name) values (:name)",
        title="Writable query",
        is_write=True,
        source="user",
        owner_id="root",
    )

    html_response = await ds.client.get(
        "/data/-/queries?q=02",
        actor={"id": "root"},
    )
    flags_response = await ds.client.get(
        "/data/-/queries",
        actor={"id": "root"},
    )
    json_response = await ds.client.get(
        "/data/-/queries.json?q=02",
        actor={"id": "root"},
    )
    filtered_response = await ds.client.get(
        "/data/-/queries.json?is_private=1",
        actor={"id": "root"},
    )
    filtered_write_response = await ds.client.get(
        "/data/-/queries?is_write=1",
        actor={"id": "root"},
    )
    filtered_private_response = await ds.client.get(
        "/data/-/queries?is_private=1",
        actor={"id": "root"},
    )

    assert html_response.status_code == 200
    assert "Demo query 02" in html_response.text
    assert "Demo query 01" not in html_response.text
    assert 'class="query-list-results"' in html_response.text
    assert 'class="query-list-facets"' in html_response.text
    assert 'type="radio"' not in html_response.text
    assert "Only the owning actor can view this query." not in html_response.text
    assert (
        "Execution skips the usual SQL and write permission checks"
        not in html_response.text
    )
    assert flags_response.status_code == 200
    assert '<th scope="col">Owner</th>' in flags_response.text
    assert '<th scope="col">Flags</th>' in flags_response.text
    assert '<th scope="col">Mode</th>' not in flags_response.text
    assert 'class="query-list-owner">root</td>' in flags_response.text
    assert 'class="query-list-pill">Read-only</span>' in flags_response.text
    assert (
        'class="query-list-pill query-list-pill-write">Writable</span>'
        in flags_response.text
    )
    assert (
        'class="query-list-pill query-list-pill-private">Private</span>'
        in flags_response.text
    )
    assert (
        'class="query-list-pill query-list-pill-trusted">Trusted</span>'
        in flags_response.text
    )
    assert (
        'href="/data/-/queries?is_write=0"><span>Read-only</span><span class="query-list-facet-count">5</span>'
        in flags_response.text
    )
    assert (
        'href="/data/-/queries?is_write=1"><span>Writable</span><span class="query-list-facet-count">1</span>'
        in flags_response.text
    )
    assert (
        'href="/data/-/queries?is_private=0"><span>Not private</span><span class="query-list-facet-count">5</span>'
        in flags_response.text
    )
    assert (
        'href="/data/-/queries?is_private=1"><span>Private</span><span class="query-list-facet-count">1</span>'
        in flags_response.text
    )
    assert "Only the owning actor can view this query." in flags_response.text
    assert (
        "Execution skips the usual SQL and write permission checks"
        in flags_response.text
    )
    assert json_response.json()["queries"][0]["name"] == "demo_query_02"
    assert [query["name"] for query in filtered_response.json()["queries"]] == [
        "private_query"
    ]
    assert "Writable query" in filtered_write_response.text
    assert "Demo query 01" not in filtered_write_response.text
    assert (
        'query-list-facet-link query-list-facet-link-active" href="/data/-/queries"'
        in filtered_write_response.text
    )
    assert (
        '<span class="query-list-facet-link query-list-facet-disabled"><span>Read-only</span><span class="query-list-facet-count">0</span></span>'
        not in filtered_write_response.text
    )
    assert (
        'href="/data/-/queries?is_write=1&amp;is_private=0"><span>Not private</span><span class="query-list-facet-count">1</span>'
        in filtered_write_response.text
    )
    assert (
        '<span class="query-list-facet-link query-list-facet-disabled"><span>Private</span><span class="query-list-facet-count">0</span></span>'
        not in filtered_write_response.text
    )
    assert "Private query" in filtered_private_response.text
    assert "Demo query 01" not in filtered_private_response.text
    assert (
        'href="/data/-/queries?is_private=1&amp;is_write=0"><span>Read-only</span><span class="query-list-facet-count">1</span>'
        in filtered_private_response.text
    )
    assert (
        '<span class="query-list-facet-link query-list-facet-disabled"><span>Writable</span><span class="query-list-facet-count">0</span></span>'
        not in filtered_private_response.text
    )
    assert (
        '<span class="query-list-facet-link query-list-facet-disabled"><span>Not private</span><span class="query-list-facet-count">0</span></span>'
        not in filtered_private_response.text
    )


@pytest.mark.asyncio
async def test_query_list_html_defaults_to_twenty_and_shows_pagination():
    ds = Datasette(memory=True)
    ds.root_enabled = True
    ds.add_memory_database("query_list_html_pagination", name="data")
    await ds.invoke_startup()
    await add_numbered_queries(ds, "data", 25)

    response = await ds.client.get("/data/-/queries", actor={"id": "root"})
    json_response = await ds.client.get("/data/-/queries.json", actor={"id": "root"})

    assert response.status_code == 200
    assert response.text.count('aria-label="Query pagination"') == 1
    assert "Demo query 20" in response.text
    assert "Demo query 21" not in response.text
    assert 'href="/data/-/queries?_next=' in response.text
    assert len(json_response.json()["queries"]) == 25


@pytest.mark.asyncio
async def test_global_query_list_api_and_html():
    ds = Datasette(memory=True)
    ds.root_enabled = True
    ds.add_memory_database("query_list_global_alpha", name="alpha")
    ds.add_memory_database("query_list_global_beta", name="beta")
    await ds.invoke_startup()
    await ds.add_query(
        "alpha",
        "alpha_first",
        "select 1",
        title="Alpha first",
        source="user",
        owner_id="root",
    )
    await ds.add_query(
        "alpha",
        "alpha_second",
        "select 2",
        title="Alpha second",
        source="user",
        owner_id="root",
    )
    await ds.add_query(
        "beta",
        "beta_first",
        "select 3",
        title="Beta first",
        source="user",
        owner_id="root",
    )

    list_response = await ds.client.get(
        "/-/queries.json?_size=2",
        actor={"id": "root"},
    )
    next_response = await ds.client.get(
        "/-/queries.json?_size=2&_next={}".format(list_response.json()["next"]),
        actor={"id": "root"},
    )
    html_response = await ds.client.get(
        "/-/queries?q=Beta",
        actor={"id": "root"},
    )

    assert list_response.status_code == 200
    assert [
        (query["database"], query["name"]) for query in list_response.json()["queries"]
    ] == [
        ("alpha", "alpha_first"),
        ("alpha", "alpha_second"),
    ]
    assert list_response.json()["next"]
    assert [
        (query["database"], query["name"]) for query in next_response.json()["queries"]
    ] == [
        ("beta", "beta_first"),
    ]
    assert html_response.status_code == 200
    assert '<th scope="col">Database</th>' in html_response.text
    assert 'class="query-list-database" href="/beta">beta</a>' in html_response.text
    assert "Beta first" in html_response.text
    assert "Alpha first" not in html_response.text


@pytest.mark.asyncio
async def test_query_store_api_rejects_is_trusted():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-sql": {"id": "writer"},
                        "store-query": {"id": "writer"},
                    }
                }
            }
        },
    )
    ds.add_memory_database("query_trusted_api", name="data")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/queries/store",
        actor={"id": "writer"},
        json={"query": {"name": "trusted", "sql": "select 1", "is_trusted": True}},
    )

    assert response.status_code == 400
    assert response.json()["errors"] == ["Invalid keys: is_trusted"]


@pytest.mark.asyncio
async def test_query_store_rejects_config_only_fields():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    ds.add_memory_database("query_config_only_fields_api", name="data")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/queries/store",
        actor={"id": "root"},
        json={
            "query": {
                "name": "unsafe",
                "sql": "select 1",
                "description_html": "<script>window.XSS=1</script>",
                "on_success_message_sql": "select 'secret'",
            }
        },
    )
    form_response = await ds.client.post(
        "/data/-/queries/store",
        actor={"id": "root"},
        data={
            "name": "unsafe_form",
            "sql": "select 1",
            "description_html": "<script>window.XSS=1</script>",
        },
    )

    assert response.status_code == 400
    assert response.json()["errors"] == [
        "Invalid keys: description_html, on_success_message_sql"
    ]
    assert form_response.status_code == 400
    assert "Invalid keys: description_html" in form_response.text
    assert await ds.get_query("data", "unsafe") is None
    assert await ds.get_query("data", "unsafe_form") is None


@pytest.mark.asyncio
async def test_query_store_api_creates_writable_query():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_write_api", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/queries/store",
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
    assert query["is_private"] is True
    assert query["is_trusted"] is False
    assert query["parameters"] == ["name"]


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
async def test_query_update_api_rejects_config_only_fields():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_update_config_only_fields", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "editable",
        "insert into dogs (name) values (:name)",
        is_write=True,
        source="user",
        owner_id="root",
    )

    response = await ds.client.post(
        "/data/editable/-/update",
        actor={"id": "root"},
        json={
            "update": {
                "description_html": "<script>window.XSS=1</script>",
                "on_success_message_sql": "select 'secret'",
            }
        },
    )

    assert response.status_code == 400
    assert response.json()["errors"] == [
        "Invalid keys: description_html, on_success_message_sql"
    ]
    query = await ds.get_query("data", "editable")
    assert query.description_html is None
    assert query.on_success_message_sql is None


@pytest.mark.asyncio
async def test_query_update_api_rejects_trusted_queries_but_internal_update_allowed():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "execute-sql": {"id": "editor"},
                        "update-query": {"id": "editor"},
                    },
                    "queries": {
                        "trusted_report": {
                            "sql": "select 1 as one",
                            "title": "Original",
                        },
                    },
                }
            }
        },
    )
    ds.add_memory_database("query_update_trusted_api", name="data")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/trusted_report/-/update",
        actor={"id": "editor"},
        json={"update": {"sql": "select 2 as two", "title": "Edited"}},
    )

    assert response.status_code == 403
    assert response.json()["errors"] == [
        "Trusted queries cannot be updated using the API"
    ]
    query = await ds.get_query("data", "trusted_report")
    assert query.is_trusted is True
    assert query.sql == "select 1 as one"
    assert query.title == "Original"

    await ds.update_query(
        "data",
        "trusted_report",
        sql="select 3 as three",
        title="Internal",
    )
    query = await ds.get_query("data", "trusted_report")
    assert query.is_trusted is True
    assert query.sql == "select 3 as three"
    assert query.title == "Internal"


@pytest.mark.asyncio
async def test_query_store_api_rejects_magic_parameters():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    ds.add_memory_database("query_magic_api", name="data")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/queries/store",
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
        "/data/-/queries/store?sql=select+*+from+dogs",
        actor={"id": "root"},
    )
    write_create_response = await ds.client.get(
        "/data/-/queries/store?sql=insert+into+dogs+(name)+values+('Cleo')",
        actor={"id": "root"},
    )
    blank_create_response = await ds.client.get(
        "/data/-/queries/store",
        actor={"id": "root"},
    )
    old_insert_response = await ds.client.get(
        "/data/-/queries/insert?sql=select+*+from+dogs",
        actor={"id": "root"},
    )
    old_create_response = await ds.client.get(
        "/data/-/queries/-/create?sql=select+*+from+dogs",
        actor={"id": "root"},
    )
    query_response = await ds.client.get(
        "/data/-/query?sql=select+*+from+dogs",
        actor={"id": "root"},
    )

    assert create_response.status_code == 200
    assert "Create query" in create_response.text
    assert 'type="radio"' not in create_response.text
    assert 'name="parameters"' not in create_response.text
    assert 'id="query-parameters"' not in create_response.text
    assert 'class="query-create-field"' in create_response.text
    assert '<label for="query-name">Name</label>' not in create_response.text
    assert '<label for="query-title">Title</label>' in create_response.text
    assert '<label for="query-url-slug">URL</label>' in create_response.text
    assert '<span class="query-create-url-prefix">/data/</span>' in create_response.text
    assert (
        '<input id="query-url-slug" name="name" type="text" value="">'
        in create_response.text
    )
    assert "function slugify(value)" in create_response.text
    assert 'data-analyze-url="/data/-/queries/analyze"' in create_response.text
    assert "setupSqlParameterRefresh" in create_response.text
    assert "renderParameters: false" in create_response.text
    assert "datasetteSqlAnalysis.renderAnalysis" in create_response.text
    assert "data-query-create-submit" in create_response.text
    assert "data-query-create-writable" not in create_response.text
    assert "data-query-create-sql-type" not in create_response.text
    assert "data-query-create-analysis-note" in create_response.text
    assert "SQL type:" not in create_response.text
    assert (
        '<span class="query-create-analysis-note" data-query-create-analysis-note aria-live="polite">This is a read-only query.</span>'
        in create_response.text
    )
    assert "disabled> Writable</label>" not in create_response.text
    assert (
        "Queries marked private can only be seen by you, their creator."
        in create_response.text
    )
    assert create_response.text.index(
        "This is a read-only query."
    ) < create_response.text.index('<input type="hidden" name="is_private" value="0">')
    assert "<h2>Query operations</h2>" in create_response.text
    assert '<table class="execute-write-analysis">' in create_response.text
    assert '<th scope="col">Required permission</th>' in create_response.text
    assert '<th scope="col">Source</th>' not in create_response.text
    assert "<td><code>read</code></td>" in create_response.text
    assert "<td><code>view-table</code></td>" in create_response.text
    assert (
        '<td><span class="execute-write-analysis-na">n/a</span></td>'
        not in create_response.text
    )
    assert create_response.text.index(
        'value="Save query"'
    ) < create_response.text.index("<h2>Query operations</h2>")
    assert blank_create_response.status_code == 200
    assert (
        '<div class="query-create-analysis" id="query-create-analysis-section" hidden>'
        in blank_create_response.text
    )
    assert "<h2>Query operations</h2>" not in blank_create_response.text
    assert (
        "<p>Analysis will show each affected table and required permission.</p>"
        not in blank_create_response.text
    )
    assert "Enter SQL to analyze this query." in blank_create_response.text
    assert write_create_response.status_code == 200
    assert (
        '<span class="query-create-analysis-note" data-query-create-analysis-note aria-live="polite">This query updates data in the database.</span>'
        in write_create_response.text
    )
    assert query_response.status_code == 200
    assert "Save this query" in query_response.text
    assert "/data/-/queries/store?sql=select+%2A+from+dogs" in query_response.text
    assert old_insert_response.status_code == 404
    assert old_create_response.status_code == 404


@pytest.mark.asyncio
async def test_create_query_analyze_endpoint_uses_sql_only():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_create_analyze", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.get(
        "/data/-/queries/analyze",
        actor={"id": "root"},
        params={"sql": "select * from dogs where name = :name"},
    )
    write_response = await ds.client.get(
        "/data/-/queries/analyze",
        actor={"id": "root"},
        params={"sql": "insert into dogs (name) values (:name)"},
    )
    blank_response = await ds.client.get(
        "/data/-/queries/analyze",
        actor={"id": "root"},
        params={"sql": ""},
    )
    old_analyze_response = await ds.client.get(
        "/data/-/queries/-/create/analyze",
        actor={"id": "root"},
        params={"sql": "select * from dogs"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["parameters"] == ["name"]
    assert data["analysis_error"] is None
    assert data["has_sql"] is True
    assert data["analysis_is_write"] is False
    assert data["save_disabled"] is False
    assert data["analysis_rows"] == [
        {
            "operation": "read",
            "database": "data",
            "table": "dogs",
            "required_permission": "view-table",
            "source": None,
            "allowed": True,
        }
    ]

    assert write_response.status_code == 200
    write_data = write_response.json()
    assert write_data["parameters"] == ["name"]
    assert write_data["has_sql"] is True
    assert write_data["analysis_is_write"] is True
    assert write_data["save_disabled"] is False
    assert write_data["analysis_rows"][0]["operation"] == "insert"

    assert blank_response.status_code == 200
    blank_data = blank_response.json()
    assert blank_data["has_sql"] is False
    assert blank_data["parameters"] == []
    assert blank_data["analysis_rows"] == []
    assert blank_data["save_disabled"] is True
    assert old_analyze_response.status_code == 404


@pytest.mark.asyncio
async def test_create_query_form_error_redisplays_form_with_values():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_create_form_error", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/queries/store",
        actor={"id": "root"},
        data={
            "name": "dogs",
            "title": "Dog lookup",
            "description": "Find dogs by name",
            "sql": "select * from dogs where name = :name",
            "is_private": "1",
        },
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "URL conflicts with an existing table or view" in response.text
    assert "Query name conflicts with a table or view" not in response.text
    assert '{"ok": false' not in response.text
    assert 'value="Dog lookup"' in response.text
    assert 'value="dogs"' in response.text
    assert ">Find dogs by name</textarea>" in response.text
    assert "select * from dogs where name = :name" in response.text
    assert 'name="is_private" value="1" checked' in response.text

    public_response = await ds.client.post(
        "/data/-/queries/store",
        actor={"id": "root"},
        data={
            "name": "dogs",
            "title": "Public dog lookup",
            "description": "Keep this public setting",
            "sql": "select * from dogs",
            "is_private": "0",
        },
    )

    assert public_response.status_code == 400
    assert 'name="is_private" value="1" checked' not in public_response.text
    assert 'name="is_private" value="0"' in public_response.text


@pytest.mark.asyncio
async def test_execute_write_get_prepopulates_without_executing():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_get", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await db.execute_write("create table cats (id integer primary key, name text)")
    await db.execute_write("create table log (message text)")
    await db.execute_write("""
        create trigger dogs_after_insert after insert on dogs begin
            update cats set name = new.name where id = new.id;
            insert into log (message) values (new.name);
        end
    """)
    await ds.invoke_startup()

    response = await ds.client.get(
        "/data/-/execute-write?sql=insert+into+dogs+(name)+values+('Cleo')",
        actor={"id": "root"},
    )

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert response.headers["x-frame-options"] == "DENY"
    assert "Write to this database" in response.text
    assert (
        "Execute SQL to insert, update or delete rows in this database."
        in response.text
    )
    assert "<h2>Query operations</h2>" in response.text
    assert "<summary>Start with a template</summary>" in response.text
    assert '<option value="dogs">dogs</option>' in response.text
    assert 'data-sql-template="insert"' in response.text
    assert 'data-sql-template="update"' in response.text
    assert 'data-sql-template="delete"' in response.text
    assert 'data-analyze-url="/data/-/execute-write/analyze"' in response.text
    assert 'data-save-query-base-url="/data/-/queries/store"' in response.text
    assert "Save this query" in response.text
    assert (
        "/data/-/queries/store?sql=insert+into+dogs+%28name%29+values+%28%27Cleo%27%29"
        in response.text
    )
    assert 'addEventListener("paste"' in response.text
    assert "setupSqlParameterRefresh" in response.text
    assert "datasetteSqlAnalysis.renderAnalysis" in response.text
    assert '<table class="execute-write-analysis">' in response.text
    assert '<th scope="col">Required permission</th>' in response.text
    assert "<td><code>insert</code></td>" in response.text
    assert "<td><code>update</code></td>" in response.text
    assert "<td><code>read</code></td>" in response.text
    assert "<td><code>view-table</code></td>" in response.text
    assert 'action="/data/-/execute-write"' in response.text
    assert "insert into dogs (name) values (&#39;Cleo&#39;)" in response.text
    assert (await db.execute("select count(*) from dogs")).first()[0] == 0

    empty_response = await ds.client.get(
        "/data/-/execute-write",
        actor={"id": "root"},
    )
    assert '<textarea id="sql-editor" name="sql"></textarea>' in empty_response.text
    assert 'executeWriteSqlInput.value = "\\n\\n\\n";' in empty_response.text
    assert "hidden>Save this query</a>" in empty_response.text

    read_only_response = await ds.client.get(
        "/data/-/execute-write?sql=select+*+from+dogs",
        actor={"id": "root"},
    )
    assert (
        "Use /-/query for read-only SQL; this endpoint only executes writes"
        in read_only_response.text
    )
    assert "hidden>Save this query</a>" in read_only_response.text


@pytest.mark.asyncio
async def test_execute_write_analyze_endpoint_uses_sql_only():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_analyze", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.get(
        "/data/-/execute-write/analyze",
        actor={"id": "root"},
        params={"sql": "insert into dogs (name) values (:name)"},
    )
    function_response = await ds.client.get(
        "/data/-/execute-write/analyze",
        actor={"id": "root"},
        params={"sql": "insert into dogs (name) values (upper(:name))"},
    )
    read_only_response = await ds.client.get(
        "/data/-/execute-write/analyze",
        actor={"id": "root"},
        params={"sql": "select * from dogs where name = :name"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["parameters"] == ["name"]
    assert data["analysis_error"] is None
    assert data["execute_disabled"] is False
    assert data["analysis_rows"] == [
        {
            "operation": "insert",
            "database": "data",
            "table": "dogs",
            "required_permission": "insert-row, update-row, delete-row",
            "source": None,
            "allowed": True,
        }
    ]
    assert "params" not in data

    assert function_response.status_code == 200
    function_data = function_response.json()
    assert function_data["ok"] is True
    assert function_data["parameters"] == ["name"]
    assert function_data["execute_disabled"] is False
    assert function_data["analysis_rows"] == [
        {
            "operation": "insert",
            "database": "data",
            "table": "dogs",
            "required_permission": "insert-row, update-row, delete-row",
            "source": None,
            "allowed": True,
        }
    ]

    assert read_only_response.status_code == 200
    read_only_data = read_only_response.json()
    assert read_only_data["ok"] is False
    assert read_only_data["parameters"] == ["name"]
    assert read_only_data["analysis_error"] == (
        "Use /-/query for read-only SQL; this endpoint only executes writes"
    )
    assert read_only_data["execute_disabled"] is True


@pytest.mark.asyncio
async def test_query_parameters_endpoint_uses_get_sql_only():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_parameters", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.get(
        "/data/-/query/parameters",
        actor={"id": "root"},
        params={
            "sql": "select * from dogs where name = :name and id = :id",
        },
    )
    permission_denied_response = await ds.client.get(
        "/data/-/query/parameters",
        actor={"id": "not-root"},
        params={"sql": "select * from dogs where name = :name"},
    )
    magic_parameter_response = await ds.client.get(
        "/data/-/query/parameters",
        actor={"id": "root"},
        params={"sql": "select :_actor_id"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "parameters": ["name", "id"]}
    assert permission_denied_response.status_code == 403
    assert permission_denied_response.json()["errors"] == [
        "Permission denied: need execute-sql"
    ]
    assert magic_parameter_response.status_code == 400
    assert magic_parameter_response.json()["errors"] == [
        "Magic parameters are not allowed"
    ]


@pytest.mark.asyncio
async def test_database_action_menu_links_to_execute_write_for_permitted_actor():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {
                            "id": ["writer", "viewer"],
                        },
                        "execute-write-sql": {"id": "writer"},
                    }
                }
            }
        },
    )
    ds.add_memory_database("execute_write_menu", name="data")
    await ds.invoke_startup()

    anonymous_response = await ds.client.get("/data")
    viewer_response = await ds.client.get("/data", actor={"id": "viewer"})
    writer_response = await ds.client.get("/data", actor={"id": "writer"})

    assert anonymous_response.status_code == 403
    assert viewer_response.status_code == 200
    assert "Execute write SQL" not in viewer_response.text
    assert writer_response.status_code == 200
    assert "Database actions" in writer_response.text
    assert 'href="/data/-/execute-write"' in writer_response.text
    assert "Execute write SQL" in writer_response.text


@pytest.mark.asyncio
async def test_database_action_menu_hides_execute_write_for_immutable_database():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    }
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_menu_immutable", name="data")
    db.is_mutable = False
    await ds.invoke_startup()

    response = await ds.client.get("/data", actor={"id": "writer"})

    assert response.status_code == 200
    assert "Execute write SQL" not in response.text
    assert 'href="/data/-/execute-write"' not in response.text


@pytest.mark.asyncio
async def test_execute_write_get_rejects_immutable_database():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_get_immutable", name="data")
    db.is_mutable = False
    await ds.invoke_startup()

    response = await ds.client.get(
        "/data/-/execute-write?sql=insert+into+dogs+(name)+values+('Cleo')",
        actor={"id": "root"},
    )

    assert response.status_code == 403
    assert response.json()["errors"] == [
        "Cannot execute write SQL because this database is immutable."
    ]


@pytest.mark.asyncio
async def test_execute_write_post_requires_database_and_table_permissions():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    }
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_permissions", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    no_database_permission = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "outsider"},
        json={
            "sql": "insert into dogs (name) values (:name)",
            "params": {"name": "Cleo"},
        },
    )
    no_table_permission = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={
            "sql": "insert into dogs (name) values (:name)",
            "params": {"name": "Cleo"},
        },
    )

    assert no_database_permission.status_code == 403
    assert no_database_permission.json()["errors"] == [
        "Permission denied: need execute-write-sql"
    ]
    assert no_table_permission.status_code == 403
    assert no_table_permission.json()["errors"] == [
        "Permission denied: need insert-row on data/dogs"
    ]

    ds.config = {
        "databases": {
            "data": {
                "permissions": {
                    "view-database": {"id": "writer"},
                    "execute-write-sql": {"id": "writer"},
                },
                "tables": {
                    "dogs": {
                        "permissions": {
                            "insert-row": {"id": "writer"},
                        }
                    }
                },
            }
        }
    }
    missing_update_permission = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={
            "sql": "insert into dogs (name) values (:name)",
            "params": {"name": "Cleo"},
        },
    )

    assert missing_update_permission.status_code == 403
    assert missing_update_permission.json()["errors"] == [
        "Permission denied: need update-row on data/dogs"
    ]

    ds.config["databases"]["data"]["tables"]["dogs"]["permissions"]["update-row"] = {
        "id": "writer"
    }
    missing_delete_permission = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={
            "sql": "insert into dogs (name) values (:name)",
            "params": {"name": "Cleo"},
        },
    )

    assert missing_delete_permission.status_code == 403
    assert missing_delete_permission.json()["errors"] == [
        "Permission denied: need delete-row on data/dogs"
    ]

    ds.config["databases"]["data"]["tables"]["dogs"]["permissions"]["delete-row"] = {
        "id": "writer"
    }
    allowed = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={
            "sql": "insert into dogs (name) values (:name)",
            "params": {"name": "Cleo"},
        },
    )

    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True
    assert allowed.json()["rowcount"] == 1
    assert allowed.json()["analysis"][0]["operation"] == "insert"
    assert (await db.execute("select name from dogs")).first()[0] == "Cleo"


@pytest.mark.asyncio
async def test_execute_write_insert_or_replace_requires_delete_row_permission():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    },
                    "tables": {
                        "users": {
                            "permissions": {
                                "insert-row": {"id": "writer"},
                                "update-row": {"id": "writer"},
                                "view-table": {"id": "writer"},
                            }
                        }
                    },
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_insert_or_replace", name="data")
    await db.execute_write(
        "create table users (id integer primary key, email text unique)"
    )
    await db.execute_write(
        "insert into users (id, email) values "
        "(1, 'a@example.com'), (2, 'b@example.com')"
    )
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={
            "sql": (
                "insert or replace into users(id, email) " "values (3, 'b@example.com')"
            )
        },
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [
        "Permission denied: need delete-row on data/users"
    ]
    assert (await db.execute("select id, email from users order by id")).dicts() == [
        {"id": 1, "email": "a@example.com"},
        {"id": 2, "email": "b@example.com"},
    ]


@pytest.mark.asyncio
async def test_execute_write_update_or_replace_requires_delete_row_permission():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    },
                    "tables": {
                        "users": {
                            "permissions": {
                                "insert-row": {"id": "writer"},
                                "update-row": {"id": "writer"},
                                "view-table": {"id": "writer"},
                            }
                        }
                    },
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_update_or_replace", name="data")
    await db.execute_write(
        "create table users (id integer primary key, email text unique)"
    )
    await db.execute_write(
        "insert into users (id, email) values "
        "(1, 'a@example.com'), (2, 'b@example.com')"
    )
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={
            "sql": "update or replace users set email = 'b@example.com' where id = 1"
        },
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [
        "Permission denied: need delete-row on data/users"
    ]
    assert (await db.execute("select id, email from users order by id")).dicts() == [
        {"id": 1, "email": "a@example.com"},
        {"id": 2, "email": "b@example.com"},
    ]


@pytest.mark.asyncio
async def test_execute_write_update_requires_insert_row_permission():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    },
                    "tables": {
                        "users": {
                            "permissions": {
                                "update-row": {"id": "writer"},
                                "delete-row": {"id": "writer"},
                                "view-table": {"id": "writer"},
                            }
                        }
                    },
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_update_requires_insert", name="data")
    await db.execute_write("create table users (id integer primary key, name text)")
    await db.execute_write("insert into users (id, name) values (1, 'Alice')")
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={"sql": "update users set name = 'Alicia' where id = 1"},
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [
        "Permission denied: need insert-row on data/users"
    ]
    assert (await db.execute("select name from users where id = 1")).first()[
        0
    ] == "Alice"


@pytest.mark.asyncio
async def test_execute_write_insert_select_requires_view_table_on_source():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    },
                    "tables": {
                        "secret": {
                            "permissions": {"view-table": {"id": "someone-else"}}
                        },
                        "public_log": {
                            "permissions": {
                                "insert-row": {"id": "writer"},
                                "update-row": {"id": "writer"},
                                "delete-row": {"id": "writer"},
                            }
                        },
                    },
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_insert_select_source", name="data")
    await db.execute_write("create table secret (value text)")
    await db.execute_write("create table public_log (value text)")
    await db.execute_write("insert into secret values ('sensitive')")
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={"sql": "insert into public_log(value) select value from secret"},
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [
        "Permission denied: need view-table on data/secret"
    ]
    assert (await db.execute("select value from public_log")).dicts() == []


@pytest.mark.asyncio
async def test_execute_write_rejects_sqlite_master_reads():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    },
                    "tables": {
                        "secret": {
                            "permissions": {"view-table": {"id": "someone-else"}}
                        },
                        "log": {
                            "permissions": {
                                "insert-row": {"id": "writer"},
                                "update-row": {"id": "writer"},
                                "delete-row": {"id": "writer"},
                            }
                        },
                    },
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_sqlite_master_read", name="data")
    await db.execute_write("create table secret (value text)")
    await db.execute_write("create table log (value text)")
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={
            "sql": (
                "insert into log " "select sql from sqlite_master where name = 'secret'"
            )
        },
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [
        "Unsupported SQL operation: read schema"
    ]
    assert (await db.execute("select value from log")).dicts() == []


@pytest.mark.asyncio
async def test_execute_write_create_table_as_select_requires_view_table_on_source():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "creator"},
                        "execute-write-sql": {"id": "creator"},
                        "create-table": {"id": "creator"},
                    },
                    "tables": {
                        "secret": {
                            "permissions": {"view-table": {"id": "someone-else"}}
                        }
                    },
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_create_as_select_source", name="data")
    await db.execute_write("create table secret (value text)")
    await db.execute_write("insert into secret values ('sensitive')")
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "creator"},
        json={"sql": "create table copied_secret as select value from secret"},
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [
        "Permission denied: need view-table on data/secret"
    ]
    assert not await db.table_exists("copied_secret")


@pytest.mark.asyncio
async def test_execute_write_allows_function_operations():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    },
                    "tables": {
                        "dogs": {
                            "permissions": {
                                "insert-row": {"id": "writer"},
                                "update-row": {"id": "writer"},
                                "delete-row": {"id": "writer"},
                            }
                        }
                    },
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_function_operation", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={"sql": "insert into dogs (name) values (upper('cleo'))"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert (await db.execute("select name from dogs")).dicts() == [{"name": "CLEO"}]


@pytest.mark.asyncio
async def test_untrusted_stored_write_query_allows_function_operations():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "view-query": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    },
                    "tables": {
                        "dogs": {
                            "permissions": {
                                "insert-row": {"id": "writer"},
                                "update-row": {"id": "writer"},
                                "delete-row": {"id": "writer"},
                            }
                        }
                    },
                }
            }
        },
    )
    db = ds.add_memory_database("stored_query_function_operation", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "insert_dog",
        "insert into dogs (name) values (upper(:name))",
        is_write=True,
        is_trusted=False,
        source="user",
        owner_id="writer",
    )

    response = await ds.client.post(
        "/data/insert_dog?_json=1",
        actor={"id": "writer"},
        data={"name": "cleo"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert (await db.execute("select name from dogs")).dicts() == [{"name": "CLEO"}]


@pytest.mark.asyncio
async def test_execute_write_rejects_vacuum_operation():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    }
                }
            }
        },
    )
    ds.add_memory_database("execute_write_vacuum_operation", name="data")
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        json={"sql": "vacuum"},
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [
        "VACUUM is not allowed in user-supplied SQL"
    ]


@pytest.mark.asyncio
async def test_execute_write_form_rejects_vacuum_operation_with_flash_error():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    }
                }
            }
        },
    )
    ds.add_memory_database("execute_write_vacuum_operation_form", name="data")
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "writer"},
        data={"sql": "vacuum"},
    )

    assert denied_response.status_code == 403
    assert (
        '<p class="message-error">VACUUM is not allowed in user-supplied SQL</p>'
        in denied_response.text
    )
    assert denied_response.text.count("VACUUM is not allowed in user-supplied SQL") == 1


@pytest.mark.asyncio
async def test_untrusted_stored_write_query_rejects_vacuum_operation():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "view-query": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    }
                }
            }
        },
    )
    ds.add_memory_database("stored_query_vacuum_operation", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "vacuum_db",
        "vacuum",
        is_write=True,
        is_trusted=False,
        source="user",
        owner_id="writer",
    )

    denied_response = await ds.client.post(
        "/data/vacuum_db?_json=1",
        actor={"id": "writer"},
        data={},
    )

    assert denied_response.status_code == 403
    assert "VACUUM is not allowed in user-supplied SQL" in denied_response.text


@pytest.mark.asyncio
async def test_untrusted_stored_write_query_rejects_vacuum_operation_with_flash_error():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "view-query": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                    }
                }
            }
        },
    )
    ds.add_memory_database("stored_query_vacuum_operation_form", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "vacuum_db",
        "vacuum",
        is_write=True,
        is_trusted=False,
        source="user",
        owner_id="writer",
    )

    denied_response = await ds.client.post(
        "/data/vacuum_db",
        actor={"id": "writer"},
        data={},
    )

    assert denied_response.status_code == 302
    assert denied_response.headers["location"] == "/data/vacuum_db"
    assert ds.unsign(denied_response.cookies["ds_messages"], "messages") == [
        ["VACUUM is not allowed in user-supplied SQL", ds.ERROR]
    ]


@pytest.mark.asyncio
async def test_trusted_stored_write_query_skips_vacuum_filtering():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "view-query": {"id": "writer"},
                    }
                }
            }
        },
    )
    ds.add_memory_database("trusted_stored_query_vacuum", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "trusted_vacuum",
        "vacuum",
        is_write=True,
        is_trusted=True,
        source="config",
    )

    response = await ds.client.post(
        "/data/trusted_vacuum?_json=1",
        actor={"id": "writer"},
        data={},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_execute_write_rejects_virtual_table_control_insert():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_virtual_table_control", name="data")
    await db.execute_write("""
        create virtual table docs using fts5(title, body, content='')
    """)
    await db.execute_write("""
        insert into docs(rowid, title, body) values (1, 'hello', 'world')
    """)
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        json={"sql": "insert into docs(docs) values('delete-all')"},
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [
        "Writes to virtual tables are not allowed in user-supplied SQL"
    ]
    assert (
        await db.execute("select count(*) from docs where docs match 'hello'")
    ).first()[0] == 1


@pytest.mark.asyncio
async def test_execute_write_rejects_regular_virtual_table_insert():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_virtual_table_insert", name="data")
    await db.execute_write("create virtual table docs using fts5(title, body)")
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        json={"sql": "insert into docs(rowid, title, body) values (1, 'a', 'b')"},
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [
        "Writes to virtual tables are not allowed in user-supplied SQL"
    ]
    assert (await db.execute("select count(*) from docs")).first()[0] == 0


@pytest.mark.asyncio
async def test_execute_write_rejects_shadow_table_insert():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_shadow_table_insert", name="data")
    await db.execute_write("create virtual table docs using fts5(title, body)")
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        json={"sql": "insert into docs_config(k, v) values ('x', 1)"},
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [
        "Writes to shadow tables are not allowed in user-supplied SQL"
    ]
    assert (await db.execute("select count(*) from docs_config")).first()[0] == 1


@pytest.mark.asyncio
async def test_untrusted_stored_write_query_rejects_virtual_table_control_insert():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("stored_query_virtual_table_control", name="data")
    await db.execute_write("""
        create virtual table docs using fts5(title, body, content='')
    """)
    await db.execute_write("""
        insert into docs(rowid, title, body) values (1, 'hello', 'world')
    """)
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "delete_all_docs",
        "insert into docs(docs) values('delete-all')",
        is_write=True,
        is_trusted=False,
        source="user",
        owner_id="root",
    )

    denied_response = await ds.client.post(
        "/data/delete_all_docs?_json=1",
        actor={"id": "root"},
        data={},
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["message"] == (
        "Writes to virtual tables are not allowed in user-supplied SQL"
    )
    assert (
        await db.execute("select count(*) from docs where docs match 'hello'")
    ).first()[0] == 1


@pytest.mark.asyncio
async def test_trusted_stored_write_query_can_write_virtual_table():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "view-query": {"id": "writer"},
                    }
                }
            }
        },
    )
    db = ds.add_memory_database("trusted_stored_query_virtual_table", name="data")
    await db.execute_write("""
        create virtual table docs using fts5(title, body, content='')
    """)
    await db.execute_write("""
        insert into docs(rowid, title, body) values (1, 'hello', 'world')
    """)
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "trusted_delete_all",
        "insert into docs(docs) values('delete-all')",
        is_write=True,
        is_trusted=True,
        source="config",
    )

    response = await ds.client.post(
        "/data/trusted_delete_all?_json=1",
        actor={"id": "writer"},
        data={},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert (
        await db.execute("select count(*) from docs where docs match 'hello'")
    ).first()[0] == 0


@pytest.mark.asyncio
async def test_execute_write_create_table_uses_create_table_permission():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "permissions": {
                "insert-row": {"id": "row-writer"},
                "update-row": {"id": "row-writer"},
            },
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": ["creator", "row-writer"]},
                        "execute-write-sql": {"id": ["creator", "row-writer"]},
                        "create-table": {"id": "creator"},
                    }
                }
            },
        },
    )
    db = ds.add_memory_database("execute_write_create_table", name="data")
    await ds.invoke_startup()

    analysis_response = await ds.client.get(
        "/data/-/execute-write/analyze",
        actor={"id": "creator"},
        params={"sql": "create table foobar (id integer primary key, name text)"},
    )
    allowed_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "creator"},
        json={"sql": "create table foobar (id integer primary key, name text)"},
    )
    row_permission_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "row-writer"},
        json={"sql": "create table should_not_exist (id integer primary key)"},
    )

    assert analysis_response.status_code == 200
    analysis_data = analysis_response.json()
    assert analysis_data["ok"] is True
    assert analysis_data["execute_disabled"] is False
    assert analysis_data["analysis_rows"] == [
        {
            "operation": "create",
            "database": "data",
            "table": "foobar",
            "required_permission": "create-table",
            "source": None,
            "allowed": True,
        }
    ]

    assert allowed_response.status_code == 200
    assert allowed_response.json()["ok"] is True
    assert allowed_response.json()["message"] == "Query executed"
    assert await db.table_exists("foobar")

    assert row_permission_response.status_code == 403
    assert row_permission_response.json()["errors"] == [
        "Permission denied: need create-table on data"
    ]
    assert not await db.table_exists("should_not_exist")


@pytest.mark.asyncio
async def test_execute_write_alter_and_drop_table_use_schema_permissions():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "permissions": {
                "delete-row": {"id": "row-writer"},
                "update-row": {"id": "row-writer"},
            },
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": ["alterer", "dropper", "row-writer"]},
                        "execute-write-sql": {
                            "id": ["alterer", "dropper", "row-writer"]
                        },
                    },
                    "tables": {
                        "dogs": {
                            "permissions": {
                                "alter-table": {"id": "alterer"},
                                "drop-table": {"id": "dropper"},
                                "view-table": {"id": "alterer"},
                            }
                        }
                    },
                }
            },
        },
    )
    db = ds.add_memory_database("execute_write_alter_drop_table", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await db.execute_write("create table cats (id integer primary key, name text)")
    await ds.invoke_startup()

    alter_allowed_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "alterer"},
        json={"sql": "alter table dogs add column age integer"},
    )
    alter_row_permission_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "row-writer"},
        json={"sql": "alter table cats add column age integer"},
    )

    assert alter_allowed_response.status_code == 200
    assert "age" in [column.name for column in await db.table_column_details("dogs")]
    assert alter_row_permission_response.status_code == 403
    assert alter_row_permission_response.json()["errors"] == [
        "Permission denied: need alter-table on data/cats"
    ]
    assert "age" not in [
        column.name for column in await db.table_column_details("cats")
    ]

    create_index_allowed_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "alterer"},
        json={"sql": "create index idx_dogs_name on dogs(name)"},
    )
    create_index_row_permission_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "row-writer"},
        json={"sql": "create index idx_cats_name on cats(name)"},
    )
    drop_index_allowed_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "alterer"},
        json={"sql": "drop index idx_dogs_name"},
    )

    assert create_index_allowed_response.status_code == 200
    assert create_index_row_permission_response.status_code == 403
    assert create_index_row_permission_response.json()["errors"] == [
        "Permission denied: need alter-table on data/cats"
    ]
    assert drop_index_allowed_response.status_code == 200

    drop_allowed_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "dropper"},
        json={"sql": "drop table dogs"},
    )
    drop_row_permission_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "row-writer"},
        json={"sql": "drop table cats"},
    )

    assert drop_allowed_response.status_code == 200
    assert not await db.table_exists("dogs")
    assert drop_row_permission_response.status_code == 403
    assert drop_row_permission_response.json()["errors"] == [
        "Permission denied: need drop-table on data/cats"
    ]
    assert await db.table_exists("cats")


@pytest.mark.asyncio
async def test_execute_write_insert_links_to_inserted_row():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_insert_link", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await db.execute_write("create table log (id integer primary key, message text)")
    await db.execute_write("insert into log (message) values ('existing')")
    await db.execute_write("""
        create trigger dogs_after_insert after insert on dogs begin
            insert into log (message) values (new.name);
        end
    """)
    await ds.invoke_startup()

    insert_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        data={
            "sql": "insert into dogs (name) values (:name)",
            "name": "Cleo",
        },
    )
    update_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        data={
            "sql": "update dogs set name = :name where id = :id",
            "name": "Cleo 2",
            "id": "1",
        },
    )

    assert insert_response.status_code == 200
    assert "Query executed, 1 row affected" in insert_response.text
    assert '<a href="/data/dogs/1">View row</a>' in insert_response.text
    assert "/data/log/2" not in insert_response.text
    assert update_response.status_code == 200
    assert "Query executed, 1 row affected" in update_response.text
    assert "View row" not in update_response.text


@pytest.mark.asyncio
async def test_execute_write_post_rejects_read_only_sql():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_read_only", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        json={"sql": "select * from dogs"},
    )

    assert response.status_code == 400
    assert response.json()["errors"] == [
        "Use /-/query for read-only SQL; this endpoint only executes writes"
    ]


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
async def test_private_query_restricts_broad_update_delete_permissions():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "update-query": {"id": "bob"},
                        "delete-query": {"id": "bob"},
                    },
                },
            },
        },
    )
    ds.add_memory_database("query_broad_update_delete", name="data")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "alice_private",
        "select 1",
        is_private=True,
        source="user",
        owner_id="alice",
    )
    await ds.add_query(
        "data",
        "alice_public",
        "select 2",
        is_private=False,
        source="user",
        owner_id="alice",
    )

    for action in ("update-query", "delete-query"):
        assert await ds.allowed(
            action=action,
            resource=QueryResource("data", "alice_private"),
            actor={"id": "alice"},
        )
        assert not await ds.allowed(
            action=action,
            resource=QueryResource("data", "alice_private"),
            actor={"id": "bob"},
        )
        assert await ds.allowed(
            action=action,
            resource=QueryResource("data", "alice_public"),
            actor={"id": "bob"},
        )

    private_update_response = await ds.client.post(
        "/data/alice_private/-/update",
        actor={"id": "bob"},
        json={"update": {"title": "Nope"}},
    )
    private_delete_response = await ds.client.post(
        "/data/alice_private/-/delete",
        actor={"id": "bob"},
        json={},
    )
    public_update_response = await ds.client.post(
        "/data/alice_public/-/update",
        actor={"id": "bob"},
        json={"update": {"title": "Bob can edit public queries"}},
    )
    public_delete_response = await ds.client.post(
        "/data/alice_public/-/delete",
        actor={"id": "bob"},
        json={},
    )

    assert private_update_response.status_code == 403
    assert private_delete_response.status_code == 403
    assert public_update_response.status_code == 200
    assert public_delete_response.status_code == 200
    assert await ds.get_query("data", "alice_private") is not None
    assert await ds.get_query("data", "alice_public") is None


@pytest.mark.asyncio
async def test_user_writable_query_execution_rechecks_table_permissions():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": ["alice", "bob"]},
                        "execute-write-sql": {"id": ["alice", "bob"]},
                    },
                    "tables": {
                        "dogs": {
                            "permissions": {
                                "insert-row": {"id": "alice"},
                                "update-row": {"id": "alice"},
                                "delete-row": {"id": "alice"},
                            }
                        }
                    },
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
