import pytest
import pytest_asyncio

from datasette import hookimpl
from datasette.app import Datasette
from datasette.jump import JumpSQL
from datasette.plugins import pm
from datasette.views.special import JumpView


@pytest_asyncio.fixture
async def ds_for_jump():
    ds = Datasette(
        config={
            "databases": {
                "content": {
                    "allow": {"id": "*"},
                    "tables": {
                        "articles": {"allow": {"id": "editor"}},
                        "comments": {"allow": True},
                    },
                    "queries": {
                        "recent_comments": {
                            "sql": "select * from comments",
                            "allow": {"id": "*"},
                            "title": "Recent comments",
                        },
                        "release_notes": {
                            "sql": "select 1",
                            "allow": {"id": "*"},
                            "title": "Recent Datasette releases",
                        },
                        "editor_report": {
                            "sql": "select * from articles",
                            "allow": {"id": "editor"},
                        },
                    },
                },
                "private": {
                    "allow": False,
                    "queries": {
                        "private_report": "select 1",
                    },
                },
            }
        }
    )
    await ds.invoke_startup()

    content_db = ds.add_memory_database("jump_test_content", name="content")
    await content_db.execute_write(
        "CREATE TABLE IF NOT EXISTS articles (id INTEGER PRIMARY KEY, title TEXT)"
    )
    await content_db.execute_write(
        "CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY, body TEXT)"
    )
    await content_db.execute_write(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT)"
    )
    await content_db.execute_write(
        "CREATE VIEW IF NOT EXISTS comment_summary AS SELECT body FROM comments"
    )

    private_db = ds.add_memory_database("jump_test_private", name="private")
    await private_db.execute_write(
        "CREATE TABLE IF NOT EXISTS secrets (id INTEGER PRIMARY KEY, data TEXT)"
    )

    public_db = ds.add_memory_database("jump_test_public", name="public")
    await public_db.execute_write(
        "CREATE TABLE IF NOT EXISTS articles (id INTEGER PRIMARY KEY, content TEXT)"
    )

    await ds._refresh_schemas()
    return ds


@pytest.mark.asyncio
async def test_jump_searches_tables_databases_views_and_canned_queries(ds_for_jump):
    response = await ds_for_jump.client.get(
        "/-/jump.json?q=content", actor={"id": "user"}
    )
    assert response.status_code == 200
    data = response.json()

    matches_by_type_and_name = {
        (match["type"], match["name"]): match for match in data["matches"]
    }
    assert ("database", "content") in matches_by_type_and_name
    assert ("table", "content: comments") in matches_by_type_and_name
    assert ("view", "content: comment_summary") in matches_by_type_and_name
    assert ("query", "content: recent_comments") in matches_by_type_and_name
    assert matches_by_type_and_name[("database", "content")]["url"] == "/content"
    assert (
        matches_by_type_and_name[("query", "content: recent_comments")]["url"]
        == "/content/recent_comments"
    )


@pytest.mark.asyncio
async def test_jump_uses_canned_query_names_not_titles(ds_for_jump):
    response = await ds_for_jump.client.get(
        "/-/jump.json?q=datasette", actor={"id": "user"}
    )
    assert response.status_code == 200
    assert response.json()["matches"] == []

    response = await ds_for_jump.client.get(
        "/-/jump.json?q=release", actor={"id": "user"}
    )
    assert response.status_code == 200
    assert response.json()["matches"] == [
        {
            "name": "content: release_notes",
            "url": "/content/release_notes",
            "type": "query",
            "description": None,
        }
    ]


@pytest.mark.asyncio
async def test_jump_respects_resource_permissions(ds_for_jump):
    regular = await ds_for_jump.client.get(
        "/-/jump.json?q=articles", actor={"id": "regular"}
    )
    editor = await ds_for_jump.client.get(
        "/-/jump.json?q=articles", actor={"id": "editor"}
    )
    private = await ds_for_jump.client.get(
        "/-/jump.json?q=secrets", actor={"id": "editor"}
    )

    assert {match["name"] for match in regular.json()["matches"]} == {
        "public: articles"
    }
    assert {match["name"] for match in editor.json()["matches"]} == {
        "content: articles",
        "public: articles",
    }
    assert private.json()["matches"] == []


@pytest.mark.asyncio
async def test_jump_sql_menu_item_helper(ds_for_jump):
    fragment = JumpSQL.menu_item(
        label="Plugin dashboard",
        url="/-/plugin-dashboard",
        description="Plugin tool",
        source="test-plugin",
        sort_key=70,
        search_text="dashboard plugin",
        display_name="Plugin Dashboard",
        item_type="plugin",
    )
    result = await ds_for_jump.get_internal_database().execute(
        fragment.sql, fragment.params
    )
    assert dict(result.first()) == {
        "type": "plugin",
        "label": "Plugin dashboard",
        "description": "Plugin tool",
        "url": "/-/plugin-dashboard",
        "search_text": "dashboard plugin",
        "sort_key": 70,
        "source": "test-plugin",
        "display_name": "Plugin Dashboard",
    }


@pytest.mark.asyncio
async def test_debug_menu_items_are_in_jump_for_debug_menu_permission():
    ds = Datasette(
        config={
            "permissions": {
                "debug-menu": {"id": "debugger"},
            }
        }
    )
    await ds.invoke_startup()
    response = await ds.client.get("/-/jump.json?q=debug", actor={"id": "debugger"})
    assert response.status_code == 200
    debug_matches = [
        match for match in response.json()["matches"] if match["type"] == "debug"
    ]
    assert {match["name"]: match["url"] for match in debug_matches} == {
        "Databases": "/-/databases",
        "Installed plugins": "/-/plugins",
        "Version info": "/-/versions",
        "Settings": "/-/settings",
        "Debug permissions": "/-/permissions",
        "Debug messages": "/-/messages",
        "Debug allow rules": "/-/allow-debug",
        "Debug threads": "/-/threads",
        "Debug actor": "/-/actor",
        "Pattern portfolio": "/-/patterns",
    }
    descriptions_by_name = {
        match["name"]: match["description"] for match in debug_matches
    }
    assert all(descriptions_by_name.values())
    assert descriptions_by_name["Databases"] == (
        "List of databases known to this Datasette instance"
    )


@pytest.mark.asyncio
async def test_debug_menu_items_are_hidden_without_debug_menu_permission():
    ds = Datasette()
    await ds.invoke_startup()
    response = await ds.client.get("/-/jump.json?q=debug", actor={"id": "regular"})
    assert response.status_code == 200
    assert [
        match for match in response.json()["matches"] if match["type"] == "debug"
    ] == []


@pytest.mark.asyncio
async def test_jump_uses_plugin_sql_with_namespaced_parameters(ds_for_jump):
    class JumpPlugin:
        @hookimpl
        def jump_items_sql(self, datasette, actor, request):
            return JumpSQL(
                sql="""
                SELECT
                    'plugin' AS type,
                    'plugin-dashboard: ' || :actor_id AS label,
                    'Plugin supplied item' AS description,
                    '/-/plugin-dashboard' AS url,
                    'plugin dashboard ' || :actor_id AS search_text,
                    80 AS sort_key,
                    'test-plugin' AS source,
                    'Plugin dashboard for ' || :actor_id AS display_name
                """,
                params={"actor_id": actor["id"] if actor else "anonymous"},
            )

    plugin = JumpPlugin()
    pm.register(plugin, name="test-jump-plugin")
    try:
        response = await ds_for_jump.client.get(
            "/-/jump.json?q=dashboard", actor={"id": "alice"}
        )
    finally:
        pm.unregister(name="test-jump-plugin")

    assert response.status_code == 200
    plugin_matches = [
        match for match in response.json()["matches"] if match["type"] == "plugin"
    ]
    assert plugin_matches == [
        {
            "name": "plugin-dashboard: alice",
            "display_name": "Plugin dashboard for alice",
            "url": "/-/plugin-dashboard",
            "type": "plugin",
            "description": "Plugin supplied item",
        }
    ]


@pytest.mark.asyncio
async def test_jump_resolves_url_descriptors_from_sql(ds_for_jump):
    class JumpPlugin:
        @hookimpl
        def jump_items_sql(self, datasette, actor, request):
            return JumpSQL(sql="""
                SELECT
                    'plugin' AS type,
                    'Table descriptor' AS label,
                    NULL AS description,
                    json_object(
                        'method', 'table',
                        'database', 'content',
                        'table', 'comments'
                    ) AS url,
                    'table descriptor comments' AS search_text,
                    80 AS sort_key,
                    'test-plugin' AS source,
                    NULL AS display_name
                """)

    plugin = JumpPlugin()
    pm.register(plugin, name="test-jump-url-descriptor-plugin")
    try:
        response = await ds_for_jump.client.get(
            "/-/jump.json?q=descriptor", actor={"id": "alice"}
        )
    finally:
        pm.unregister(name="test-jump-url-descriptor-plugin")

    assert response.status_code == 200
    plugin_matches = [
        match for match in response.json()["matches"] if match["type"] == "plugin"
    ]
    assert plugin_matches == [
        {
            "name": "Table descriptor",
            "url": "/content/comments",
            "type": "plugin",
            "description": None,
        }
    ]


@pytest.mark.asyncio
async def test_jump_url_descriptor_errors(ds_for_jump):
    view = JumpView(ds_for_jump)
    with pytest.raises(AttributeError):
        view._resolve_url('{"method": "not_a_url_method"}')
    with pytest.raises(TypeError):
        view._resolve_url(
            '{"method": "table", "database_name": "content", "table_name": "comments"}'
        )


@pytest.mark.asyncio
async def test_tables_endpoint_removed(ds_for_jump):
    response = await ds_for_jump.client.get("/-/tables.json")
    assert response.status_code == 404
