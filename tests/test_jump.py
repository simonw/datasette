import pytest
import pytest_asyncio
from markupsafe import Markup

from datasette import hookimpl
from datasette.app import Datasette
from datasette.plugins import pm


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
        matches_by_type_and_name[("query", "content: recent_comments")]["display_name"]
        == "Recent comments"
    )
    assert (
        matches_by_type_and_name[("query", "content: recent_comments")]["url"]
        == "/content/recent_comments"
    )


@pytest.mark.asyncio
async def test_jump_searches_and_displays_canned_query_titles(ds_for_jump):
    response = await ds_for_jump.client.get(
        "/-/jump.json?q=datasette", actor={"id": "user"}
    )
    assert response.status_code == 200
    data = response.json()

    assert data["matches"] == [
        {
            "name": "content: release_notes",
            "display_name": "Recent Datasette releases",
            "url": "/content/release_notes",
            "type": "query",
            "description": "Canned query",
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
async def test_jump_uses_plugin_sql_with_namespaced_parameters(ds_for_jump):
    from datasette.jump import JumpSQL

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
                    NULL AS database_name,
                    NULL AS resource_name,
                    'plugin dashboard ' || :actor_id AS search_text,
                    80 AS sort_key,
                    'test-plugin' AS source,
                    'Plugin dashboard for ' || :actor_id AS display_name
                """,
                params={"actor_id": actor["id"] if actor else "anonymous"},
                has_display_name=True,
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
async def test_jump_start_hook_renders_empty_state_template(ds_for_jump):
    class JumpStartPlugin:
        @hookimpl
        def jump_start(self, datasette, actor, request):
            if not actor:
                return None
            return Markup(
                '<section class="agent-jump-start">'
                "<h3>Agent chat</h3>"
                '<a href="/-/agent/new">Start a new agent chat</a>'
                "</section>"
            )

    plugin = JumpStartPlugin()
    pm.register(plugin, name="test-jump-start-plugin")
    try:
        anonymous = await ds_for_jump.client.get("/")
        authenticated = await ds_for_jump.client.get("/", actor={"id": "alice"})
    finally:
        pm.unregister(name="test-jump-start-plugin")

    assert 'url="/-/jump"' in authenticated.text
    assert "<template data-jump-start>" not in anonymous.text
    assert "<template data-jump-start>" in authenticated.text
    assert "Start a new agent chat" in authenticated.text


@pytest.mark.asyncio
async def test_tables_endpoint_removed(ds_for_jump):
    response = await ds_for_jump.client.get("/-/tables.json")
    assert response.status_code == 404
