import json
import re
from html import unescape

import pytest
from bs4 import BeautifulSoup as Soup

from datasette.app import Datasette
from datasette.resources import DatabaseResource, QueryResource
from datasette.stored_queries import StoredQuery, StoredQueryPage
from datasette.utils.asgi import Forbidden
from datasette.utils.sqlite import sqlite3, supports_returning

requires_sqlite_returning = pytest.mark.skipif(
    not supports_returning(), reason="SQLite does not support RETURNING"
)
EXPECTED_CREATE_TABLE_TEMPLATE_SQL = "\n".join(
    (
        "create table new_table (",
        "  id integer primary key,",
        "  name text",
        "  -- created text default (datetime('now'))",
        ")",
    )
)


def _template_option_attributes(html, table):
    match = re.search(r'<option value="{}"([^>]*)>'.format(table), html)
    assert match, "Could not find template option for {}".format(table)
    return match.group(1)


def _template_sql(html, table, operation):
    attrs = _template_option_attributes(html, table)
    match = re.search(r'data-template-{}-sql="([^"]*)"'.format(operation), attrs)
    assert match, "Could not find {} template for {}".format(operation, table)
    return unescape(match.group(1))


def _template_button_sql(html, operation):
    soup = Soup(html, "html.parser")
    button = soup.select_one('button[data-sql-template="{}"]'.format(operation))
    assert button, "Could not find {} template button".format(operation)
    assert button.get(
        "data-template-sql"
    ), "Could not find SQL for {} template button".format(operation)
    return button["data-template-sql"]


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
                            # Configured queries are always public; this is ignored.
                            "is_private": True,
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
async def test_query_store_api_creates_query_for_immutable_database(tmp_path):
    db_path = tmp_path / "immutable.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("create table dogs (id integer primary key, name text)")
    conn.commit()
    conn.close()

    ds = Datasette([], immutables=[str(db_path)], default_deny=True)
    ds.root_enabled = True
    await ds.invoke_startup()

    response = await ds.client.post(
        "/immutable/-/queries/store",
        actor={"id": "root"},
        json={
            "query": {
                "name": "by_name",
                "sql": "select * from dogs where name = :name",
            }
        },
    )

    ds.close()
    assert response.status_code == 201
    data = response.json()
    assert data["ok"] is True
    assert data["query"]["name"] == "by_name"
    assert data["query"]["parameters"] == ["name"]
    assert data["query"]["is_write"] is False


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
    no_results_response = await ds.client.get(
        "/data/-/queries?q=nope",
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
    assert no_results_response.status_code == 200
    assert "No queries found." in no_results_response.text
    assert 'class="query-list-filters core"' not in no_results_response.text
    assert 'id="query-search"' not in no_results_response.text
    assert 'class="query-list-facets"' not in no_results_response.text
    assert "<h2>Mode</h2>" not in no_results_response.text
    assert "<h2>Visibility</h2>" not in no_results_response.text


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


async def _make_ds_with_user_query(name, *, is_private=False, owner_id="owner"):
    ds = Datasette(memory=True, settings={"default_allow_sql": True})
    db = ds.add_memory_database(name, name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "saved",
        "select * from dogs",
        title="Saved query",
        description="A saved query",
        source="user",
        owner_id=owner_id,
        is_private=is_private,
    )
    return ds


@pytest.mark.asyncio
async def test_query_edit_form_renders_and_updates_for_owner():
    ds = await _make_ds_with_user_query("query_edit_owner")
    actor = {"id": "owner"}

    # GET renders the form pre-filled with existing values
    get_response = await ds.client.get("/data/saved/-/edit", actor=actor)
    assert get_response.status_code == 200
    assert 'value="Saved query"' in get_response.text
    assert ">A saved query</textarea>" in get_response.text
    assert "select * from dogs" in get_response.text
    # URL slug is shown but not editable
    assert 'name="name"' not in get_response.text

    # POST updates the query and redirects back to the query page
    post_response = await ds.client.post(
        "/data/saved/-/edit",
        actor=actor,
        data={
            "title": "Updated title",
            "description": "Updated description",
            "sql": "select id from dogs",
            "is_private": "1",
        },
    )
    assert post_response.status_code == 302
    assert post_response.headers["location"] == "/data/saved"

    query = await ds.get_query("data", "saved")
    assert query.title == "Updated title"
    assert query.description == "Updated description"
    assert query.sql == "select id from dogs"
    assert query.is_private is True


@pytest.mark.asyncio
async def test_query_edit_metadata_only_does_not_require_execute_sql():
    # An owner who can no longer execute SQL can still edit title/description
    ds = await _make_ds_with_user_query("query_edit_metadata_only")
    actor = {"id": "owner"}

    post_response = await ds.client.post(
        "/data/saved/-/edit",
        actor=actor,
        data={
            "title": "Renamed",
            "description": "A saved query",
            "sql": "select * from dogs",
        },
    )
    assert post_response.status_code == 302
    query = await ds.get_query("data", "saved")
    assert query.title == "Renamed"


@pytest.mark.asyncio
async def test_private_query_edit_delete_restricted_to_owner():
    ds = await _make_ds_with_user_query(
        "query_edit_private", is_private=True, owner_id="owner"
    )

    # A different actor cannot view, edit or delete the private query
    other = {"id": "intruder"}
    assert (await ds.client.get("/data/saved/-/edit", actor=other)).status_code == 403
    assert (await ds.client.get("/data/saved/-/delete", actor=other)).status_code == 403
    delete_attempt = await ds.client.post(
        "/data/saved/-/delete",
        actor=other,
        data={},
    )
    assert delete_attempt.status_code == 403
    assert await ds.get_query("data", "saved") is not None

    # The owner can edit and delete
    owner = {"id": "owner"}
    assert (await ds.client.get("/data/saved/-/edit", actor=owner)).status_code == 200


@pytest.mark.asyncio
async def test_non_private_query_editable_by_permitted_non_owner():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "execute-sql": {"id": "editor"},
                        "update-query": {"id": "editor"},
                        "delete-query": {"id": "editor"},
                    }
                }
            }
        },
    )
    db = ds.add_memory_database("query_non_private_editor", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "saved",
        "select * from dogs",
        title="Shared",
        source="user",
        owner_id="owner",
        is_private=False,
    )

    editor = {"id": "editor"}
    # Editor (not the owner) can edit because the query is not private
    post_response = await ds.client.post(
        "/data/saved/-/edit",
        actor=editor,
        data={
            "title": "Edited by editor",
            "description": "",
            "sql": "select * from dogs",
        },
    )
    assert post_response.status_code == 302
    query = await ds.get_query("data", "saved")
    assert query.title == "Edited by editor"

    # Editor can also delete it
    delete_response = await ds.client.post(
        "/data/saved/-/delete",
        actor=editor,
        data={},
    )
    assert delete_response.status_code == 302
    assert await ds.get_query("data", "saved") is None


@pytest.mark.asyncio
async def test_query_delete_confirmation_and_form_delete():
    ds = await _make_ds_with_user_query("query_delete_form")
    actor = {"id": "owner"}

    get_response = await ds.client.get("/data/saved/-/delete", actor=actor)
    assert get_response.status_code == 200
    assert "Are you sure" in get_response.text
    assert "select * from dogs" in get_response.text
    soup = Soup(get_response.text, "html.parser")
    form = soup.select_one("form.query-delete-form")
    assert form is not None
    assert "core" in form["class"]
    assert form.select_one('input[type="submit"][value="Delete query"]') is not None

    post_response = await ds.client.post(
        "/data/saved/-/delete",
        actor=actor,
        data={},
    )
    assert post_response.status_code == 302
    assert post_response.headers["location"] == "/data"
    assert await ds.get_query("data", "saved") is None


@pytest.mark.asyncio
async def test_query_action_menu_shows_edit_and_delete_for_owner():
    ds = await _make_ds_with_user_query("query_action_menu")

    owner_response = await ds.client.get("/data/saved", actor={"id": "owner"})
    assert owner_response.status_code == 200
    assert "/data/saved/-/edit" in owner_response.text
    assert "/data/saved/-/delete" in owner_response.text

    # A different actor (the query is public) cannot edit/delete by default
    other_response = await ds.client.get("/data/saved", actor={"id": "stranger"})
    assert other_response.status_code == 200
    assert "/data/saved/-/edit" not in other_response.text
    assert "/data/saved/-/delete" not in other_response.text


@pytest.mark.asyncio
async def test_query_edit_rejected_for_trusted_query():
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
                    "queries": {"trusted_report": {"sql": "select 1 as one"}},
                }
            }
        },
    )
    ds.add_memory_database("query_edit_trusted", name="data")
    await ds.invoke_startup()

    response = await ds.client.get(
        "/data/trusted_report/-/edit", actor={"id": "editor"}
    )
    assert response.status_code == 403
    # Edit/delete links should not appear on a trusted/config query page
    page = await ds.client.get("/data/trusted_report", actor={"id": "editor"})
    assert "/data/trusted_report/-/edit" not in page.text


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
async def test_create_query_supports_recursive_cte():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_create_recursive_cte", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    sql = """
    with recursive dog_tree(id, name) as (
        select id, name from dogs
        union all
        select id + 1, name from dog_tree where id < 3
    )
    select name from dog_tree
    """.strip()

    analysis_response = await ds.client.get(
        "/data/-/queries/analyze",
        actor={"id": "root"},
        params={"sql": sql},
    )
    form_response = await ds.client.get(
        "/data/-/queries/store",
        actor={"id": "root"},
        params={"sql": sql},
    )
    store_response = await ds.client.post(
        "/data/-/queries/store",
        actor={"id": "root"},
        data={
            "name": "dog-tree",
            "title": "Dog tree",
            "sql": sql,
            "is_private": "1",
        },
    )

    assert analysis_response.status_code == 200
    analysis_data = analysis_response.json()
    assert analysis_data["ok"] is True
    assert analysis_data["analysis_error"] is None
    assert analysis_data["analysis_is_write"] is False
    assert analysis_data["save_disabled"] is False

    assert form_response.status_code == 200
    soup = Soup(form_response.text, "html.parser")
    submit = soup.select_one("[data-query-create-submit]")
    assert submit is not None
    assert not submit.has_attr("disabled")
    assert "This is a read-only query." in form_response.text

    assert store_response.status_code == 302
    assert store_response.headers["location"] == "/data/dog-tree"
    query = await ds.get_query("data", "dog-tree")
    assert query is not None
    assert query.sql == sql
    assert query.is_write is False


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
    assert 'data-sql-template="create"' in response.text
    assert _template_button_sql(response.text, "create") == (
        EXPECTED_CREATE_TABLE_TEMPLATE_SQL
    )
    assert ">Create table</button>" in response.text
    assert '<label for="execute-write-template-table">or table:</label>' in (
        response.text
    )
    assert '<option value="dogs"' in response.text
    assert "data-template-insert-sql=" in response.text
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
    assert "window.editor.dispatch" in response.text
    assert "window.history.replaceState" in response.text
    assert "window.location.href = url.toString();" not in response.text
    assert "input[data-execute-write-submit]:disabled" in response.text
    assert (
        'data-execute-write-disabled-reason aria-live="polite" hidden' in response.text
    )
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
    assert '<p class="sql-editor sql-editor-min-lines">' in empty_response.text
    assert '<textarea id="sql-editor" name="sql"></textarea>' in empty_response.text
    assert "min-height: calc(5.6em + 8px);" in empty_response.text
    assert 'executeWriteSqlInput.value = "\\n\\n\\n";' not in empty_response.text
    assert "Enter writable SQL before executing." in empty_response.text
    assert 'data-save-query-base-url="/data/-/queries/store"' in empty_response.text
    assert '<a href="/data/-/queries/store' not in empty_response.text

    read_only_response = await ds.client.get(
        "/data/-/execute-write?sql=select+*+from+dogs",
        actor={"id": "root"},
    )
    assert (
        "Use /-/query for read-only SQL; this endpoint only executes writes"
        in read_only_response.text
    )
    assert (
        '<input type="submit" value="Execute" data-execute-write-submit '
        'aria-describedby="execute-write-disabled-reason" disabled>'
    ) in read_only_response.text
    assert 'data-save-query-base-url="/data/-/queries/store"' in read_only_response.text
    assert '<a href="/data/-/queries/store' not in read_only_response.text


@pytest.mark.asyncio
async def test_execute_write_disabled_submit_explains_denied_operations():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": "writer"},
                        "execute-sql": {"id": "writer"},
                        "execute-write-sql": {"id": "writer"},
                        "store-query": {"id": "writer"},
                    }
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_denied_submit", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.get(
        "/data/-/execute-write?sql=insert+into+dogs+(name)+values+('Cleo')",
        actor={"id": "writer"},
    )
    analysis_response = await ds.client.get(
        "/data/-/execute-write/analyze",
        actor={"id": "writer"},
        params={"sql": "insert into dogs (name) values ('Cleo')"},
    )

    assert response.status_code == 200
    assert (
        '<input type="submit" value="Execute" data-execute-write-submit '
        'aria-describedby="execute-write-disabled-reason" disabled>'
    ) in response.text
    assert (
        '<span id="execute-write-disabled-reason" '
        'class="execute-write-disabled-reason" '
        'data-execute-write-disabled-reason aria-live="polite">'
        "You do not have permission for every operation listed above.</span>"
    ) in response.text
    assert '<span class="execute-write-analysis-denied">no</span>' in response.text
    assert 'data-save-query-base-url="/data/-/queries/store"' in response.text
    assert '<a href="/data/-/queries/store' not in response.text

    assert analysis_response.status_code == 200
    data = analysis_response.json()
    assert data["execute_disabled"] is True
    assert data["execute_disabled_reason"] == (
        "You do not have permission for every operation listed above."
    )


@pytest.mark.asyncio
async def test_execute_write_templates_are_filtered_by_permission_and_server_generated():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": ["writer", "deleter", "viewer"]},
                        "execute-write-sql": {"id": ["writer", "deleter", "viewer"]},
                    },
                    "tables": {
                        "dogs": {
                            "permissions": {
                                "view-table": {"id": ["writer", "deleter"]},
                                "insert-row": {"id": "writer"},
                                "update-row": {"id": "writer"},
                                "delete-row": {"id": ["writer", "deleter"]},
                            }
                        },
                        "manual": {
                            "permissions": {
                                "view-table": {"id": "writer"},
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
    db = ds.add_memory_database("execute_write_templates", name="data")
    await db.execute_write(
        "create table dogs (id integer primary key, name text, age integer)"
    )
    await db.execute_write("create table manual (id text primary key, name text)")
    await db.execute_write("create table cats (id integer primary key, name text)")
    await ds.invoke_startup()

    writer_response = await ds.client.get(
        "/data/-/execute-write", actor={"id": "writer"}
    )
    deleter_response = await ds.client.get(
        "/data/-/execute-write", actor={"id": "deleter"}
    )
    viewer_response = await ds.client.get(
        "/data/-/execute-write", actor={"id": "viewer"}
    )

    assert writer_response.status_code == 200
    assert "<summary>Start with a template</summary>" in writer_response.text
    assert "You don't currently have permission" not in writer_response.text
    assert '<option value="dogs"' in writer_response.text
    assert '<option value="manual"' in writer_response.text
    assert '<option value="cats"' not in writer_response.text
    assert 'data-sql-template="create"' not in writer_response.text
    assert "function insertSql(" not in writer_response.text
    assert "function updateSql(" not in writer_response.text
    assert "function deleteSql(" not in writer_response.text

    dogs_insert_sql = _template_sql(writer_response.text, "dogs", "insert")
    assert '"id"' not in dogs_insert_sql
    assert '"name"' in dogs_insert_sql
    assert '"age"' in dogs_insert_sql
    assert ":name" in dogs_insert_sql
    assert ":age" in dogs_insert_sql

    dogs_update_sql = _template_sql(writer_response.text, "dogs", "update")
    assert 'where "id" = :id' in dogs_update_sql
    assert '"id" = :new_id' not in dogs_update_sql

    manual_insert_sql = _template_sql(writer_response.text, "manual", "insert")
    assert '"id"' in manual_insert_sql
    assert ":id" in manual_insert_sql

    assert deleter_response.status_code == 200
    assert "<summary>Start with a template</summary>" in deleter_response.text
    assert '<option value="dogs"' in deleter_response.text
    dogs_attrs = _template_option_attributes(deleter_response.text, "dogs")
    assert "data-template-delete-sql" in dogs_attrs
    assert "data-template-insert-sql" not in dogs_attrs
    assert "data-template-update-sql" not in dogs_attrs
    assert 'data-sql-template="delete"' in deleter_response.text
    assert 'data-sql-template="insert"' not in deleter_response.text
    assert 'data-sql-template="update"' not in deleter_response.text
    assert 'data-sql-template="create"' not in deleter_response.text

    assert viewer_response.status_code == 200
    assert "<summary>Start with a template</summary>" not in viewer_response.text
    assert "There are no tables that you can currently edit." in viewer_response.text
    assert "data-template-insert-sql" not in viewer_response.text
    assert "data-template-update-sql" not in viewer_response.text
    assert "data-template-delete-sql" not in viewer_response.text


@pytest.mark.asyncio
async def test_execute_write_create_table_template_is_filtered_by_permission():
    ds = Datasette(
        memory=True,
        default_deny=True,
        config={
            "databases": {
                "data": {
                    "permissions": {
                        "view-database": {"id": ["creator", "editor", "both"]},
                        "execute-write-sql": {"id": ["creator", "editor", "both"]},
                        "create-table": {"id": ["creator", "both"]},
                    },
                    "tables": {
                        "dogs": {
                            "permissions": {
                                "view-table": {"id": ["editor", "both"]},
                                "insert-row": {"id": ["editor", "both"]},
                                "update-row": {"id": ["editor", "both"]},
                                "delete-row": {"id": ["editor", "both"]},
                            }
                        },
                    },
                }
            }
        },
    )
    db = ds.add_memory_database("execute_write_create_template", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    creator_response = await ds.client.get(
        "/data/-/execute-write", actor={"id": "creator"}
    )
    editor_response = await ds.client.get(
        "/data/-/execute-write", actor={"id": "editor"}
    )
    both_response = await ds.client.get("/data/-/execute-write", actor={"id": "both"})

    assert creator_response.status_code == 200
    assert "<summary>Start with a template</summary>" in creator_response.text
    assert _template_button_sql(creator_response.text, "create") == (
        EXPECTED_CREATE_TABLE_TEMPLATE_SQL
    )
    assert "There are no tables that you can currently edit." not in (
        creator_response.text
    )
    assert 'id="execute-write-template-table"' not in creator_response.text
    assert 'data-sql-template="insert"' not in creator_response.text
    assert 'data-sql-template="update"' not in creator_response.text
    assert 'data-sql-template="delete"' not in creator_response.text

    assert editor_response.status_code == 200
    assert 'data-sql-template="create"' not in editor_response.text
    assert '<label for="execute-write-template-table">Table</label>' in (
        editor_response.text
    )
    assert 'data-sql-template="insert"' in editor_response.text
    assert 'data-sql-template="update"' in editor_response.text
    assert 'data-sql-template="delete"' in editor_response.text

    assert both_response.status_code == 200
    assert _template_button_sql(both_response.text, "create") == (
        EXPECTED_CREATE_TABLE_TEMPLATE_SQL
    )
    assert '<label for="execute-write-template-table">or table:</label>' in (
        both_response.text
    )
    assert 'data-sql-template="insert"' in both_response.text
    assert 'data-sql-template="update"' in both_response.text
    assert 'data-sql-template="delete"' in both_response.text


@pytest.mark.asyncio
async def test_execute_write_create_table_refreshes_template_tables():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_create_template_refresh", name="data")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        data={"sql": "create table selectable (id integer primary key, name text)"},
    )

    assert response.status_code == 200
    assert "Query executed" in response.text
    assert '<option value="selectable"' in response.text
    assert _template_sql(response.text, "selectable", "insert") == (
        'insert into "selectable" (\n' '  "name"\n' ")\n" "values (\n" "  :name\n" ")"
    )
    assert await db.table_exists("selectable")


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
    assert data["execute_disabled_reason"] is None
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
    assert function_data["execute_disabled_reason"] is None
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
    assert read_only_data["execute_disabled_reason"] == (
        "Use /-/query for read-only SQL; this endpoint only executes writes"
    )


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
    assert allowed.json()["rows"] == []
    assert allowed.json()["truncated"] is False
    assert allowed.json()["analysis"][0]["operation"] == "insert"
    assert (await db.execute("select name from dogs")).first()[0] == "Cleo"


@pytest.mark.asyncio
@requires_sqlite_returning
async def test_execute_write_json_includes_returning_rows():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_returning_json", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        json={
            "sql": "insert into dogs (name) values (:name) returning id, name",
            "params": {"name": "Cleo"},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["message"] == "Query executed, 1 row affected"
    assert data["rowcount"] == 1
    assert data["rows"] == [{"id": 1, "name": "Cleo"}]
    assert data["truncated"] is False
    assert [row["operation"] for row in data["analysis"]] == ["insert", "read"]
    assert (await db.execute("select id, name from dogs")).dicts() == [
        {"id": 1, "name": "Cleo"}
    ]


@pytest.mark.asyncio
@requires_sqlite_returning
async def test_execute_write_json_returning_rows_can_be_truncated():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_returning_json_truncated", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    for index in range(1, 12):
        await db.execute_write(
            "insert into dogs (name) values (?)", ["Dog {}".format(index)]
        )
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        json={"sql": "update dogs set name = name || '!' returning id, name"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["message"] == "Query executed"
    assert data["rowcount"] == -1
    assert data["rows"] == [
        {"id": index, "name": "Dog {}!".format(index)} for index in range(1, 11)
    ]
    assert data["truncated"] is True
    assert (await db.execute("select count(*) from dogs where name like '%!'")).first()[
        0
    ] == 11


@pytest.mark.asyncio
@requires_sqlite_returning
async def test_execute_write_html_displays_returning_rows():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_returning_html", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        data={
            "sql": "insert into dogs (name) values (:name) returning id, name",
            "name": "Cleo",
        },
    )
    non_returning_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        data={"sql": "insert into dogs (name) values ('Pancakes')"},
    )

    assert response.status_code == 200
    assert "Query executed, 1 row affected" in response.text
    assert "<h2>Returned rows</h2>" in response.text
    assert '<table class="rows-and-columns">' in response.text
    assert '<th class="col-id" scope="col">id</th>' in response.text
    assert '<th class="col-name" scope="col">name</th>' in response.text
    assert '<td class="col-id">1</td>' in response.text
    assert '<td class="col-name">Cleo</td>' in response.text

    assert non_returning_response.status_code == 200
    assert "Query executed, 1 row affected" in non_returning_response.text
    assert "<h2>Returned rows</h2>" not in non_returning_response.text
    assert '<p class="zero-results">0 results</p>' not in non_returning_response.text


@pytest.mark.asyncio
@requires_sqlite_returning
async def test_execute_write_html_returning_rows_can_be_truncated():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_returning_html_truncated", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    for index in range(1, 12):
        await db.execute_write(
            "insert into dogs (name) values (?)", ["Dog {}".format(index)]
        )
    await ds.invoke_startup()

    response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        data={"sql": "update dogs set name = name || '!' returning id, name"},
    )

    assert response.status_code == 200
    assert "<h2>Returned rows</h2>" in response.text
    assert "Only the first 10 returned rows are shown." in response.text
    assert '<td class="col-id">1</td>' in response.text
    assert '<td class="col-name">Dog 1!</td>' in response.text
    assert '<td class="col-id">10</td>' in response.text
    assert '<td class="col-name">Dog 10!</td>' in response.text
    assert '<td class="col-id">11</td>' not in response.text
    assert '<td class="col-name">Dog 11!</td>' not in response.text


@pytest.mark.parametrize(
    "database_name, sql",
    (
        (
            "execute_write_insert_or_replace",
            "insert or replace into users(id, email) values (3, 'b@example.com')",
        ),
        (
            "execute_write_update_or_replace",
            "update or replace users set email = 'b@example.com' where id = 1",
        ),
    ),
    ids=("insert-or-replace", "update-or-replace"),
)
@pytest.mark.asyncio
async def test_execute_write_replace_requires_delete_row_permission(database_name, sql):
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
    db = ds.add_memory_database(database_name, name="data")
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
        json={"sql": sql},
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


@pytest.mark.parametrize(
    (
        "database_name",
        "setup_sqls",
        "write_sql",
        "expected_error",
        "verification_sql",
        "expected_count",
    ),
    (
        (
            "execute_write_virtual_table_control",
            (
                "create virtual table docs using fts5(title, body, content='')",
                "insert into docs(rowid, title, body) values (1, 'hello', 'world')",
            ),
            "insert into docs(docs) values('delete-all')",
            "Writes to virtual tables are not allowed in user-supplied SQL",
            "select count(*) from docs where docs match 'hello'",
            1,
        ),
        (
            "execute_write_virtual_table_insert",
            ("create virtual table docs using fts5(title, body)",),
            "insert into docs(rowid, title, body) values (1, 'a', 'b')",
            "Writes to virtual tables are not allowed in user-supplied SQL",
            "select count(*) from docs",
            0,
        ),
        (
            "execute_write_shadow_table_insert",
            ("create virtual table docs using fts5(title, body)",),
            "insert into docs_config(k, v) values ('x', 1)",
            "Writes to shadow tables are not allowed in user-supplied SQL",
            "select count(*) from docs_config",
            1,
        ),
    ),
    ids=("control-insert", "virtual-table", "shadow-table"),
)
@pytest.mark.asyncio
async def test_execute_write_rejects_virtual_and_shadow_table_writes(
    database_name,
    setup_sqls,
    write_sql,
    expected_error,
    verification_sql,
    expected_count,
):
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database(database_name, name="data")
    for setup_sql in setup_sqls:
        await db.execute_write(setup_sql)
    await ds.invoke_startup()

    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        json={"sql": write_sql},
    )

    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [expected_error]
    assert (await db.execute(verification_sql)).first()[0] == expected_count


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
async def test_execute_write_create_view_uses_create_view_permission():
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
                        "create-view": {"id": "creator"},
                    }
                }
            },
        },
    )
    db = ds.add_memory_database("execute_write_create_view", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()

    analysis_response = await ds.client.get(
        "/data/-/execute-write/analyze",
        actor={"id": "creator"},
        params={"sql": "create view dog_names as select id, name from dogs"},
    )
    allowed_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "creator"},
        json={"sql": "create view dog_names as select id, name from dogs"},
    )
    row_permission_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "row-writer"},
        json={"sql": "create view should_not_exist as select id from dogs"},
    )

    assert analysis_response.status_code == 200
    analysis_data = analysis_response.json()
    assert analysis_data["ok"] is True
    assert analysis_data["execute_disabled"] is False
    assert analysis_data["analysis_rows"] == [
        {
            "operation": "create",
            "database": "data",
            "table": "dog_names",
            "required_permission": "create-view",
            "source": None,
            "allowed": True,
        }
    ]

    assert allowed_response.status_code == 200
    assert allowed_response.json()["ok"] is True
    assert allowed_response.json()["message"] == "Query executed"
    assert await db.view_exists("dog_names")

    assert row_permission_response.status_code == 403
    assert row_permission_response.json()["errors"] == [
        "Permission denied: need create-view on data"
    ]
    assert not await db.view_exists("should_not_exist")


@pytest.mark.parametrize(
    (
        "database_name",
        "allowed_actor",
        "allowed_sql",
        "denied_sql",
        "expected_error",
        "setup_sqls",
        "expected_state",
    ),
    (
        (
            "execute_write_alter_table",
            "alterer",
            "alter table dogs add column age integer",
            "alter table cats add column age integer",
            "Permission denied: need alter-table on data/cats",
            (),
            "alter-table",
        ),
        (
            "execute_write_create_index",
            "alterer",
            "create index idx_dogs_name on dogs(name)",
            "create index idx_cats_name on cats(name)",
            "Permission denied: need alter-table on data/cats",
            (),
            "create-index",
        ),
        (
            "execute_write_drop_index",
            "alterer",
            "drop index idx_dogs_name",
            "drop index idx_cats_name",
            "Permission denied: need alter-table on data/cats",
            (
                "create index idx_dogs_name on dogs(name)",
                "create index idx_cats_name on cats(name)",
            ),
            "drop-index",
        ),
        (
            "execute_write_drop_table",
            "dropper",
            "drop table dogs",
            "drop table cats",
            "Permission denied: need drop-table on data/cats",
            (),
            "drop-table",
        ),
        (
            "execute_write_drop_view",
            "dropper",
            "drop view dogs_view",
            "drop view cats_view",
            "Permission denied: need drop-view on data/cats_view",
            (
                "create view dogs_view as select * from dogs",
                "create view cats_view as select * from cats",
            ),
            "drop-view",
        ),
    ),
    ids=("alter-table", "create-index", "drop-index", "drop-table", "drop-view"),
)
@pytest.mark.asyncio
async def test_execute_write_schema_operations_use_schema_permissions(
    database_name,
    allowed_actor,
    allowed_sql,
    denied_sql,
    expected_error,
    setup_sqls,
    expected_state,
):
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
                        },
                        "dogs_view": {
                            "permissions": {
                                "drop-view": {"id": "dropper"},
                            }
                        },
                    },
                }
            },
        },
    )
    db = ds.add_memory_database(database_name, name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await db.execute_write("create table cats (id integer primary key, name text)")
    for setup_sql in setup_sqls:
        await db.execute_write(setup_sql)
    await ds.invoke_startup()

    async def index_exists(index_name):
        row = (
            await db.execute(
                "select 1 from sqlite_master where type = 'index' and name = ?",
                [index_name],
            )
        ).first()
        return row is not None

    allowed_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": allowed_actor},
        json={"sql": allowed_sql},
    )
    denied_response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "row-writer"},
        json={"sql": denied_sql},
    )

    assert allowed_response.status_code == 200
    assert denied_response.status_code == 403
    assert denied_response.json()["errors"] == [expected_error]

    if expected_state == "alter-table":
        assert "age" in [
            column.name for column in await db.table_column_details("dogs")
        ]
        assert "age" not in [
            column.name for column in await db.table_column_details("cats")
        ]
    elif expected_state == "create-index":
        assert await index_exists("idx_dogs_name")
        assert not await index_exists("idx_cats_name")
    elif expected_state == "drop-index":
        assert not await index_exists("idx_dogs_name")
        assert await index_exists("idx_cats_name")
    elif expected_state == "drop-table":
        assert not await db.table_exists("dogs")
        assert await db.table_exists("cats")
    elif expected_state == "drop-view":
        assert not await db.view_exists("dogs_view")
        assert await db.view_exists("cats_view")


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


@pytest.mark.parametrize("action", ("view-query", "update-query", "delete-query"))
@pytest.mark.asyncio
async def test_query_owner_gets_update_delete_and_writable_view_defaults(action):
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


@pytest.mark.parametrize(
    "action, path_suffix, request_json, expected_public_title",
    (
        (
            "update-query",
            "-/update",
            {"update": {"title": "Bob can edit public queries"}},
            "Bob can edit public queries",
        ),
        ("delete-query", "-/delete", {}, None),
    ),
    ids=("update-query", "delete-query"),
)
@pytest.mark.asyncio
async def test_private_query_restricts_broad_update_delete_permissions(
    action, path_suffix, request_json, expected_public_title
):
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

    private_response = await ds.client.post(
        "/data/alice_private/{}".format(path_suffix),
        actor={"id": "bob"},
        json=request_json,
    )
    public_response = await ds.client.post(
        "/data/alice_public/{}".format(path_suffix),
        actor={"id": "bob"},
        json=request_json,
    )

    assert private_response.status_code == 403
    assert public_response.status_code == 200
    assert await ds.get_query("data", "alice_private") is not None
    public_query = await ds.get_query("data", "alice_public")
    if expected_public_title is None:
        assert public_query is None
    else:
        assert public_query.title == expected_public_title


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


@pytest.mark.asyncio
@requires_sqlite_returning
async def test_stored_write_query_with_returning():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_write_returning", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "insert_dog",
        "insert into dogs (name) values (:name) returning id, name",
        is_write=True,
        source="user",
        owner_id="root",
    )

    response = await ds.client.post(
        "/data/insert_dog?_json=1",
        actor={"id": "root"},
        data={"name": "Cleo"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert (await db.execute("select id, name from dogs")).dicts() == [
        {"id": 1, "name": "Cleo"}
    ]


@pytest.mark.asyncio
@requires_sqlite_returning
async def test_stored_write_query_with_truncated_returning_message():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("query_write_truncated_returning", name="data")
    await db.execute_write("create table dogs (id integer primary key, name text)")
    await db.execute_write_many(
        "insert into dogs (name) values (?)",
        [("Cleo",) for _ in range(20)],
    )
    await ds.invoke_startup()
    await ds.add_query(
        "data",
        "update_dogs",
        "update dogs set name = name returning id",
        is_write=True,
        source="user",
        owner_id="root",
    )

    response = await ds.client.post(
        "/data/update_dogs?_json=1",
        actor={"id": "root"},
        data={},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["message"] == "Query executed"
