from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
import pytest_asyncio

from datasette import hookimpl
from datasette.app import Datasette
from datasette.database import Database
from datasette.plugins import pm
from datasette.utils.asgi import Response


@pytest_asyncio.fixture
async def query_context_ds():
    ds = Datasette(
        memory=True,
        config={
            "databases": {
                "data": {
                    "queries": {
                        "by_category": "select id from records where category=:category order by id",
                        "by_actor": "select :_actor_id as actor_id",
                        "by_nonce": "select :_nonce_value as value",
                        "by_unknown": "select :_unknown_value as value",
                        "add_record": {
                            "sql": "insert into records(name) values (:name)",
                            "write": True,
                        },
                    }
                }
            }
        },
    )
    ds.remove_database("_memory")
    db = ds.add_database(Database(ds, memory_name=uuid4().hex), name="data")
    await db.execute_write_script("""
        create table records(id integer primary key, name text, category text);
        insert into records values
            (1, 'one', 'red'), (2, 'two', 'blue'), (3, 'three', 'red');
        """)
    await ds.invoke_startup()
    try:
        yield ds
    finally:
        ds.close()


QUERY_CASES = [
    ("/data/records?category=red&_sort_desc=id", [3, 1], None),
    ("/data/records?_size=1&_next=1", [2, 3], None),
    ("/data/records/2", [2], None),
    (
        "/data/-/query?sql=select+id+from+records+where+category%3D%3Acategory+order+by+id&category=red&unused=ignored",
        [1, 3],
        {"category": "red", "unused": "ignored"},
    ),
    ("/data/by_category?category=blue", [2], {"category": "blue"}),
    ("/data/by_category", [], {"category": ""}),
    ("/data/by_actor", ["tester"], {"_actor_id": "tester"}),
    ("/data/by_actor?_actor_id=impostor", ["tester"], {"_actor_id": "tester"}),
    ("/data/by_unknown?_unknown_value=ignored", [None], {"_unknown_value": None}),
    ("/data/-/query?sql=select+1", [1], {}),
    ("/data/-/query?sql=select+%40value&value=blue", ["blue"], {"value": "blue"}),
    ("/data/-/query?sql=select+%24value&value=blue", ["blue"], {"value": "blue"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hook_name",
    ["extra_template_vars", "extra_css_urls", "extra_js_urls", "extra_body_script"],
)
@pytest.mark.parametrize(
    "url,expected_values,expected_params",
    QUERY_CASES
    + [
        ("/data/records/-/fragment?category=red&_sort_desc=id", [3, 1], None),
        ("/", None, None),
        ("/data", None, None),
        ("/data/-/query", None, None),
        ("/data/add_record?name=four", None, None),
    ],
)
async def test_template_hooks_query_context(
    query_context_ds, hook_name, url, expected_values, expected_params
):
    ds = query_context_ds
    seen = []

    def capture(self, sql, params, database):
        seen.append((sql, params, database))
        if hook_name == "extra_template_vars":
            return {}
        if hook_name == "extra_body_script":
            return ""
        return []

    class Plugin:
        pass

    setattr(Plugin, hook_name, hookimpl(capture))
    plugin = Plugin()
    pm.register(plugin)
    try:
        response = await ds.client.get(
            url, cookies={"ds_actor": ds.client.actor_cookie({"id": "tester"})}
        )
        assert response.status_code == 200
        assert len(seen) == 1
        sql, params, database = seen[0]
        if expected_values is None:
            assert sql is None
            assert params is None
            assert (
                await ds.get_database("data").execute("select count(*) from records")
            ).single_value() == 3
        else:
            assert database == "data"
            assert sql
            assert params is not None
            # A plugin can replay exactly the page's query, including its
            # filters, pagination and resolved magic parameters.
            results = await ds.get_database(database).execute(sql, params)
            assert [row[0] for row in results] == expected_values
            if expected_params is not None:
                assert params == expected_params
    finally:
        pm.unregister(plugin)


@pytest.mark.asyncio
@pytest.mark.parametrize("context_type", [dict, "dataclass"])
@pytest.mark.parametrize("override_database", ["plugin-db", None])
async def test_render_template_explicit_plugin_context(
    query_context_ds, context_type, override_database
):
    ds = query_context_ds
    seen = []

    class Plugin:
        @hookimpl
        def extra_template_vars(self, database, table, columns, sql, params):
            seen.append((database, table, columns, sql, params))
            return {}

    plugin = Plugin()
    pm.register(plugin)
    try:
        context = {"database": "template-db", "marker": "template"}
        if context_type == "dataclass":

            @dataclass
            class TemplateContext:
                database: str
                marker: str

            context = TemplateContext(**context)
        plugin_context = {
            "database": override_database,
            "table": "records",
            "columns": ["value"],
            "sql": "select :value",
            "params": {"value": 7},
        }
        before = plugin_context.copy()
        template = ds.get_jinja_environment().from_string(
            "{{ database }}:{{ marker }}:{{ sql|default('missing') }}"
        )
        rendered = await ds.render_template(
            template, context, plugin_context=plugin_context
        )
        assert rendered == "template-db:template:missing"
        assert seen == [
            (override_database, "records", ["value"], "select :value", {"value": 7})
        ]
        assert plugin_context == before
    finally:
        pm.unregister(plugin)


@pytest.mark.asyncio
async def test_async_magic_parameters_are_resolved_once(query_context_ds):
    ds = query_context_ds
    magic_calls = []
    seen = []

    class Plugin:
        @hookimpl
        def register_magic_parameters(self):
            async def nonce(key, request):
                magic_calls.append(key)
                return len(magic_calls)

            return [("nonce", nonce)]

        @hookimpl
        def extra_template_vars(self, database, sql, params):
            async def inner():
                results = await ds.get_database(database).execute(sql, params)
                seen.append((params, results.single_value()))
                return {}

            return inner

        @hookimpl
        def register_output_renderer(self):
            async def can_render(rows, params):
                assert params == {"_nonce_value": 1, "_nonce_unused": "unused"}
                assert rows[0][0] == 1
                return True

            return {
                "extension": "nonce",
                "render": lambda: None,
                "can_render": can_render,
            }

    plugin = Plugin()
    pm.register(plugin)
    try:
        ds._register_renderers()
        response = await ds.client.get("/data/by_nonce?_nonce_unused=unused")
        assert response.status_code == 200
        assert magic_calls == ["value"]
        assert seen == [({"_nonce_value": 1, "_nonce_unused": "unused"}, 1)]
    finally:
        pm.unregister(plugin)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/data/records", "/data/records/2", "/data/by_actor"])
async def test_query_context_does_not_change_json_extras(query_context_ds, path):
    ds = query_context_ds
    cookies = {"ds_actor": ds.client.actor_cookie({"id": "tester"})}
    response = await ds.client.get(path + ".json", cookies=cookies)
    assert response.status_code == 200
    assert "query" not in response.json()
    response_with_extra = await ds.client.get(
        path + ".json?_extra=query", cookies=cookies
    )
    assert response_with_extra.status_code == 200
    assert "query" in response_with_extra.json()
    # Resolving plugin arguments must not add trusted magic values to the
    # public query extra.
    assert "_actor_id" not in response_with_extra.json()["query"]["params"]


@pytest.mark.asyncio
async def test_render_template_plugin_context_defaults(query_context_ds):
    ds = query_context_ds
    seen = []

    class Plugin:
        @hookimpl
        def extra_template_vars(self, database, table, columns, sql, params):
            seen.append((database, table, columns, sql, params))
            return {}

    plugin = Plugin()
    pm.register(plugin)
    try:
        context = {
            "database": "template-db",
            "table": "records",
            "columns": ["id"],
            "sql": "template variable",
            "params": {"template": "variable"},
        }
        template = ds.get_jinja_environment().from_string("{{ sql }}")
        assert await ds.render_template(template, context) == "template variable"
        assert seen == [("template-db", "records", ["id"], None, None)]
        await ds.render_template(
            template, context, plugin_context={"sql": "select 1", "params": {}}
        )
        assert seen[-1] == ("template-db", "records", ["id"], "select 1", {})
    finally:
        pm.unregister(plugin)


@pytest.mark.asyncio
async def test_failed_query_plugin_context(query_context_ds):
    ds = query_context_ds
    seen = []

    class Plugin:
        @hookimpl
        def extra_template_vars(self, sql, params):
            seen.append((sql, params))
            return {}

    plugin = Plugin()
    pm.register(plugin)
    try:
        response = await ds.client.get(
            "/data/-/query?sql=select+:value+from+missing&value=kept"
        )
        assert response.status_code == 200
        assert "no such table" in response.text
        assert seen == [("select :value from missing", {"value": "kept"})]
    finally:
        pm.unregister(plugin)


@pytest.mark.asyncio
@pytest.mark.parametrize("url,expected_values,expected_params", QUERY_CASES)
@pytest.mark.parametrize("callback", ["render", "can_render"])
async def test_output_renderer_query_parameters(
    query_context_ds, url, expected_values, expected_params, callback
):
    ds = query_context_ds
    seen = []

    def capture(sql, params):
        seen.append((sql, params))

    def render(sql, params):
        capture(sql, params)
        return Response.text("rendered")

    def can_render(sql, params):
        capture(sql, params)
        return True

    class Plugin:
        @hookimpl
        def register_output_renderer(self):
            return {
                "extension": "querycontext",
                "render": render,
                "can_render": can_render,
            }

    plugin = Plugin()
    pm.register(plugin)
    try:
        ds._register_renderers()
        if callback == "render":
            parts = urlsplit(url)
            url = urlunsplit(parts._replace(path=parts.path + ".querycontext"))
        response = await ds.client.get(
            url, cookies={"ds_actor": ds.client.actor_cookie({"id": "tester"})}
        )
        assert response.status_code == 200
        assert seen
        sql, params = seen[-1]
        results = await ds.get_database("data").execute(sql, params)
        assert [row[0] for row in results] == expected_values
        if expected_params is not None:
            assert params == expected_params
    finally:
        pm.unregister(plugin)
