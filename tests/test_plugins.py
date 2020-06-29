from bs4 import BeautifulSoup as Soup
from .fixtures import (
    app_client,
    make_app_client,
    TABLES,
    TEMP_PLUGIN_SECRET_FILE,
    TestClient as _TestClient,
)  # noqa
from datasette.app import Datasette
from datasette import cli
from datasette.plugins import get_plugins, DEFAULT_PLUGINS, pm
from datasette.utils import sqlite3, CustomRow
from jinja2.environment import Template
import base64
import json
import os
import pathlib
import re
import sqlite3
import textwrap
import pytest
import urllib

at_memory_re = re.compile(r" at 0x\w+")


@pytest.mark.parametrize(
    "plugin_hook", [name for name in dir(pm.hook) if not name.startswith("_")]
)
def test_plugin_hooks_have_tests(plugin_hook):
    "Every plugin hook should be referenced in this test module"
    tests_in_this_module = [t for t in globals().keys() if t.startswith("test_")]
    ok = False
    for test in tests_in_this_module:
        if plugin_hook in test:
            ok = True
    assert ok, "Plugin hook is missing tests: {}".format(plugin_hook)


def test_plugins_dir_plugin_prepare_connection(app_client):
    response = app_client.get(
        "/fixtures.json?sql=select+convert_units(100%2C+'m'%2C+'ft')"
    )
    assert pytest.approx(328.0839) == response.json["rows"][0][0]


def test_plugin_prepare_connection_arguments(app_client):
    response = app_client.get(
        "/fixtures.json?sql=select+prepare_connection_args()&_shape=arrayfirst"
    )
    assert [
        "database=fixtures, datasette.plugin_config(\"name-of-plugin\")={'depth': 'root'}"
    ] == response.json


@pytest.mark.parametrize(
    "path,expected_decoded_object",
    [
        ("/", {"template": "index.html", "database": None, "table": None}),
        (
            "/fixtures/",
            {"template": "database.html", "database": "fixtures", "table": None},
        ),
        (
            "/fixtures/sortable",
            {"template": "table.html", "database": "fixtures", "table": "sortable"},
        ),
    ],
)
def test_plugin_extra_css_urls(app_client, path, expected_decoded_object):
    response = app_client.get(path)
    links = Soup(response.body, "html.parser").findAll("link")
    special_href = [
        l for l in links if l.attrs["href"].endswith("/extra-css-urls-demo.css")
    ][0]["href"]
    # This link has a base64-encoded JSON blob in it
    encoded = special_href.split("/")[3]
    assert expected_decoded_object == json.loads(
        base64.b64decode(encoded).decode("utf8")
    )


def test_plugin_extra_js_urls(app_client):
    response = app_client.get("/")
    scripts = Soup(response.body, "html.parser").findAll("script")
    assert [
        s
        for s in scripts
        if s.attrs
        == {
            "integrity": "SRIHASH",
            "crossorigin": "anonymous",
            "src": "https://plugin-example.com/jquery.js",
        }
    ]


def test_plugins_with_duplicate_js_urls(app_client):
    # If two plugins both require jQuery, jQuery should be loaded only once
    response = app_client.get("/fixtures")
    # This test is a little tricky, as if the user has any other plugins in
    # their current virtual environment those may affect what comes back too.
    # What matters is that https://plugin-example.com/jquery.js is only there once
    # and it comes before plugin1.js and plugin2.js which could be in either
    # order
    scripts = Soup(response.body, "html.parser").findAll("script")
    srcs = [s["src"] for s in scripts if s.get("src")]
    # No duplicates allowed:
    assert len(srcs) == len(set(srcs))
    # jquery.js loaded once:
    assert 1 == srcs.count("https://plugin-example.com/jquery.js")
    # plugin1.js and plugin2.js are both there:
    assert 1 == srcs.count("https://plugin-example.com/plugin1.js")
    assert 1 == srcs.count("https://plugin-example.com/plugin2.js")
    # jquery comes before them both
    assert srcs.index("https://plugin-example.com/jquery.js") < srcs.index(
        "https://plugin-example.com/plugin1.js"
    )
    assert srcs.index("https://plugin-example.com/jquery.js") < srcs.index(
        "https://plugin-example.com/plugin2.js"
    )


def test_plugins_render_cell_link_from_json(app_client):
    sql = """
        select '{"href": "http://example.com/", "label":"Example"}'
    """.strip()
    path = "/fixtures?" + urllib.parse.urlencode({"sql": sql})
    response = app_client.get(path)
    td = Soup(response.body, "html.parser").find("table").find("tbody").find("td")
    a = td.find("a")
    assert a is not None, str(a)
    assert a.attrs["href"] == "http://example.com/"
    assert a.attrs["data-database"] == "fixtures"
    assert a.text == "Example"


def test_plugins_render_cell_demo(app_client):
    response = app_client.get("/fixtures/simple_primary_key?id=4")
    soup = Soup(response.body, "html.parser")
    td = soup.find("td", {"class": "col-content"})
    assert {
        "column": "content",
        "table": "simple_primary_key",
        "database": "fixtures",
        "config": {"depth": "table", "special": "this-is-simple_primary_key"},
    } == json.loads(td.string)


def test_plugin_config(app_client):
    assert {"depth": "table"} == app_client.ds.plugin_config(
        "name-of-plugin", database="fixtures", table="sortable"
    )
    assert {"depth": "database"} == app_client.ds.plugin_config(
        "name-of-plugin", database="fixtures", table="unknown_table"
    )
    assert {"depth": "database"} == app_client.ds.plugin_config(
        "name-of-plugin", database="fixtures"
    )
    assert {"depth": "root"} == app_client.ds.plugin_config(
        "name-of-plugin", database="unknown_database"
    )
    assert {"depth": "root"} == app_client.ds.plugin_config("name-of-plugin")
    assert None is app_client.ds.plugin_config("unknown-plugin")


def test_plugin_config_env(app_client):
    os.environ["FOO_ENV"] = "FROM_ENVIRONMENT"
    assert {"foo": "FROM_ENVIRONMENT"} == app_client.ds.plugin_config("env-plugin")
    # Ensure secrets aren't visible in /-/metadata.json
    metadata = app_client.get("/-/metadata.json")
    assert {"foo": {"$env": "FOO_ENV"}} == metadata.json["plugins"]["env-plugin"]
    del os.environ["FOO_ENV"]


def test_plugin_config_env_from_list(app_client):
    os.environ["FOO_ENV"] = "FROM_ENVIRONMENT"
    assert [{"in_a_list": "FROM_ENVIRONMENT"}] == app_client.ds.plugin_config(
        "env-plugin-list"
    )
    # Ensure secrets aren't visible in /-/metadata.json
    metadata = app_client.get("/-/metadata.json")
    assert [{"in_a_list": {"$env": "FOO_ENV"}}] == metadata.json["plugins"][
        "env-plugin-list"
    ]
    del os.environ["FOO_ENV"]


def test_plugin_config_file(app_client):
    open(TEMP_PLUGIN_SECRET_FILE, "w").write("FROM_FILE")
    assert {"foo": "FROM_FILE"} == app_client.ds.plugin_config("file-plugin")
    # Ensure secrets aren't visible in /-/metadata.json
    metadata = app_client.get("/-/metadata.json")
    assert {"foo": {"$file": TEMP_PLUGIN_SECRET_FILE}} == metadata.json["plugins"][
        "file-plugin"
    ]
    os.remove(TEMP_PLUGIN_SECRET_FILE)


@pytest.mark.parametrize(
    "path,expected_extra_body_script",
    [
        (
            "/",
            {
                "template": "index.html",
                "database": None,
                "table": None,
                "config": {"depth": "root"},
            },
        ),
        (
            "/fixtures/",
            {
                "template": "database.html",
                "database": "fixtures",
                "table": None,
                "config": {"depth": "database"},
            },
        ),
        (
            "/fixtures/sortable",
            {
                "template": "table.html",
                "database": "fixtures",
                "table": "sortable",
                "config": {"depth": "table"},
            },
        ),
    ],
)
def test_plugins_extra_body_script(app_client, path, expected_extra_body_script):
    r = re.compile(r"<script>var extra_body_script = (.*?);</script>")
    json_data = r.search(app_client.get(path).text).group(1)
    actual_data = json.loads(json_data)
    assert expected_extra_body_script == actual_data


def test_plugins_asgi_wrapper(app_client):
    response = app_client.get("/fixtures")
    assert "fixtures" == response.headers["x-databases"]


def test_plugins_extra_template_vars(restore_working_directory):
    with make_app_client(
        template_dir=str(pathlib.Path(__file__).parent / "test_templates")
    ) as client:
        response = client.get("/-/metadata")
        assert response.status == 200
        extra_template_vars = json.loads(
            Soup(response.body, "html.parser").select("pre.extra_template_vars")[0].text
        )
        assert {
            "template": "show_json.html",
            "scope_path": "/-/metadata",
        } == extra_template_vars
        extra_template_vars_from_awaitable = json.loads(
            Soup(response.body, "html.parser")
            .select("pre.extra_template_vars_from_awaitable")[0]
            .text
        )
        assert {
            "template": "show_json.html",
            "awaitable": True,
            "scope_path": "/-/metadata",
        } == extra_template_vars_from_awaitable


def test_plugins_async_template_function(restore_working_directory):
    with make_app_client(
        template_dir=str(pathlib.Path(__file__).parent / "test_templates")
    ) as client:
        response = client.get("/-/metadata")
        assert response.status == 200
        extra_from_awaitable_function = (
            Soup(response.body, "html.parser")
            .select("pre.extra_from_awaitable_function")[0]
            .text
        )
        expected = (
            sqlite3.connect(":memory:").execute("select sqlite_version()").fetchone()[0]
        )
        assert expected == extra_from_awaitable_function


def test_default_plugins_have_no_templates_path_or_static_path():
    # The default plugins that ship with Datasette should have their static_path and
    # templates_path all set to None
    plugins = get_plugins()
    for plugin in plugins:
        if plugin["name"] in DEFAULT_PLUGINS:
            assert None is plugin["static_path"]
            assert None is plugin["templates_path"]


@pytest.fixture(scope="session")
def view_names_client(tmp_path_factory):
    tmpdir = tmp_path_factory.mktemp("test-view-names")
    templates = tmpdir / "templates"
    templates.mkdir()
    plugins = tmpdir / "plugins"
    plugins.mkdir()
    for template in (
        "index.html",
        "database.html",
        "table.html",
        "row.html",
        "show_json.html",
        "query.html",
    ):
        (templates / template).write_text("view_name:{{ view_name }}", "utf-8")
    (plugins / "extra_vars.py").write_text(
        textwrap.dedent(
            """
        from datasette import hookimpl
        @hookimpl
        def extra_template_vars(view_name):
            return {"view_name": view_name}
    """
        ),
        "utf-8",
    )
    db_path = str(tmpdir / "fixtures.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(TABLES)
    return _TestClient(
        Datasette(
            [db_path], template_dir=str(templates), plugins_dir=str(plugins)
        ).app()
    )


@pytest.mark.parametrize(
    "path,view_name",
    (
        ("/", "index"),
        ("/fixtures", "database"),
        ("/fixtures/units", "table"),
        ("/fixtures/units/1", "row"),
        ("/-/metadata", "json_data"),
        ("/fixtures?sql=select+1", "database"),
    ),
)
def test_view_names(view_names_client, path, view_name):
    response = view_names_client.get(path)
    assert response.status == 200
    assert "view_name:{}".format(view_name) == response.text


def test_register_output_renderer_no_parameters(app_client):
    response = app_client.get("/fixtures/facetable.testnone")
    assert 200 == response.status
    assert b"Hello" == response.body


def test_register_output_renderer_all_parameters(app_client):
    response = app_client.get("/fixtures/facetable.testall")
    assert 200 == response.status
    # Lots of 'at 0x103a4a690' in here - replace those so we can do
    # an easy comparison
    body = at_memory_re.sub(" at 0xXXX", response.text)
    assert {
        "1+1": 2,
        "datasette": "<datasette.app.Datasette object at 0xXXX>",
        "columns": [
            "pk",
            "created",
            "planet_int",
            "on_earth",
            "state",
            "city_id",
            "neighborhood",
            "tags",
            "complex_array",
            "distinct_some_null",
        ],
        "rows": [
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
            "<sqlite3.Row object at 0xXXX>",
        ],
        "sql": "select pk, created, planet_int, on_earth, state, city_id, neighborhood, tags, complex_array, distinct_some_null from facetable order by pk limit 51",
        "query_name": None,
        "database": "fixtures",
        "table": "facetable",
        "request": "<datasette.utils.asgi.Request object at 0xXXX>",
        "view_name": "table",
    } == json.loads(body)
    # Test that query_name is set correctly
    query_response = app_client.get("/fixtures/pragma_cache_size.testall")
    assert "pragma_cache_size" == json.loads(query_response.body)["query_name"]


def test_register_output_renderer_custom_status_code(app_client):
    response = app_client.get("/fixtures/pragma_cache_size.testall?status_code=202")
    assert 202 == response.status


def test_register_output_renderer_custom_content_type(app_client):
    response = app_client.get(
        "/fixtures/pragma_cache_size.testall?content_type=text/blah"
    )
    assert "text/blah" == response.headers["content-type"]


def test_register_output_renderer_custom_headers(app_client):
    response = app_client.get(
        "/fixtures/pragma_cache_size.testall?header=x-wow:1&header=x-gosh:2"
    )
    assert "1" == response.headers["x-wow"]
    assert "2" == response.headers["x-gosh"]


def test_register_output_renderer_can_render(app_client):
    response = app_client.get("/fixtures/facetable?_no_can_render=1")
    assert response.status == 200
    links = (
        Soup(response.body, "html.parser")
        .find("p", {"class": "export-links"})
        .findAll("a")
    )
    actual = [l["href"].split("/")[-1] for l in links]
    # Should not be present because we sent ?_no_can_render=1
    assert "facetable.testall?_labels=on" not in actual
    # Check that it was passed the values we expected
    assert hasattr(app_client.ds, "_can_render_saw")
    assert {
        "datasette": app_client.ds,
        "columns": [
            "pk",
            "created",
            "planet_int",
            "on_earth",
            "state",
            "city_id",
            "neighborhood",
            "tags",
            "complex_array",
            "distinct_some_null",
        ],
        "sql": "select pk, created, planet_int, on_earth, state, city_id, neighborhood, tags, complex_array, distinct_some_null from facetable order by pk limit 51",
        "query_name": None,
        "database": "fixtures",
        "table": "facetable",
        "view_name": "table",
    }.items() <= app_client.ds._can_render_saw.items()


@pytest.mark.asyncio
async def test_prepare_jinja2_environment(app_client):
    template = app_client.ds.jinja_env.from_string(
        "Hello there, {{ a|format_numeric }}", {"a": 3412341}
    )
    rendered = await app_client.ds.render_template(template)
    assert "Hello there, 3,412,341" == rendered


def test_publish_subcommand():
    # This is hard to test properly, because publish subcommand plugins
    # cannot be loaded using the --plugins-dir mechanism - they need
    # to be installed using "pip install". So I'm cheating and taking
    # advantage of the fact that cloudrun/heroku use the plugin hook
    # to register themselves as default plugins.
    assert ["cloudrun", "heroku"] == cli.publish.list_commands({})


def test_register_facet_classes(app_client):
    response = app_client.get(
        "/fixtures/compound_three_primary_keys.json?_dummy_facet=1"
    )
    assert [
        {
            "name": "pk1",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_facet_dummy=pk1",
            "type": "dummy",
        },
        {
            "name": "pk2",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_facet_dummy=pk2",
            "type": "dummy",
        },
        {
            "name": "pk3",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_facet_dummy=pk3",
            "type": "dummy",
        },
        {
            "name": "content",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_facet_dummy=content",
            "type": "dummy",
        },
        {
            "name": "pk1",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_facet=pk1",
        },
        {
            "name": "pk2",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_facet=pk2",
        },
        {
            "name": "pk3",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_facet=pk3",
        },
    ] == response.json["suggested_facets"]


def test_actor_from_request(app_client):
    app_client.get("/")
    # Should have no actor
    assert None == app_client.ds._last_request.scope["actor"]
    app_client.get("/?_bot=1")
    # Should have bot actor
    assert {"id": "bot"} == app_client.ds._last_request.scope["actor"]


def test_actor_from_request_async(app_client):
    app_client.get("/")
    # Should have no actor
    assert None == app_client.ds._last_request.scope["actor"]
    app_client.get("/?_bot2=1")
    # Should have bot2 actor
    assert {"id": "bot2", "1+1": 2} == app_client.ds._last_request.scope["actor"]


def test_existing_scope_actor_respected(app_client):
    app_client.get("/?_actor_in_scope=1")
    assert {"id": "from-scope"} == app_client.ds._last_request.scope["actor"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,expected",
    [
        ("this_is_allowed", True),
        ("this_is_denied", False),
        ("this_is_allowed_async", True),
        ("this_is_denied_async", False),
        ("no_match", None),
    ],
)
async def test_permission_allowed(app_client, action, expected):
    actual = await app_client.ds.permission_allowed(
        {"id": "actor"}, action, default=None
    )
    assert expected == actual


def test_actor_json(app_client):
    assert {"actor": None} == app_client.get("/-/actor.json").json
    assert {"actor": {"id": "bot2", "1+1": 2}} == app_client.get(
        "/-/actor.json/?_bot2=1"
    ).json


@pytest.mark.parametrize(
    "path,body",
    [
        ("/one/", "2"),
        ("/two/Ray?greeting=Hail", "Hail Ray"),
        ("/not-async/", "This was not async"),
    ],
)
def test_register_routes(app_client, path, body):
    response = app_client.get(path)
    assert 200 == response.status
    assert body == response.text


def test_register_routes_post(app_client):
    response = app_client.post("/post/", {"this is": "post data"}, csrftoken_from=True)
    assert 200 == response.status
    assert "csrftoken" in response.json
    assert "post data" == response.json["this is"]


def test_register_routes_csrftoken(restore_working_directory, tmpdir_factory):
    templates = tmpdir_factory.mktemp("templates")
    (templates / "csrftoken_form.html").write_text(
        "CSRFTOKEN: {{ csrftoken() }}", "utf-8"
    )
    with make_app_client(template_dir=templates) as client:
        response = client.get("/csrftoken-form/")
        expected_token = client.ds._last_request.scope["csrftoken"]()
        assert "CSRFTOKEN: {}".format(expected_token) == response.text


def test_register_routes_asgi(app_client):
    response = app_client.get("/three/")
    assert {"hello": "world"} == response.json
    assert "1" == response.headers["x-three"]


def test_register_routes_add_message(app_client):
    response = app_client.get("/add-message/")
    assert 200 == response.status
    assert "Added message" == response.text
    decoded = app_client.ds.unsign(response.cookies["ds_messages"], "messages")
    assert [["Hello from messages", 1]] == decoded


def test_register_routes_render_message(restore_working_directory, tmpdir_factory):
    templates = tmpdir_factory.mktemp("templates")
    (templates / "render_message.html").write_text('{% extends "base.html" %}', "utf-8")
    with make_app_client(template_dir=templates) as client:
        response1 = client.get("/add-message/")
        response2 = client.get("/render-message/", cookies=response1.cookies)
        assert 200 == response2.status
        assert "Hello from messages" in response2.text


@pytest.mark.asyncio
async def test_startup(app_client):
    await app_client.ds.invoke_startup()
    assert app_client.ds._startup_hook_fired
    assert 2 == app_client.ds._startup_hook_calculation


def test_canned_queries(app_client):
    queries = app_client.get("/fixtures.json").json["queries"]
    queries_by_name = {q["name"]: q for q in queries}
    assert {
        "sql": "select 2",
        "name": "from_async_hook",
        "private": False,
    } == queries_by_name["from_async_hook"]
    assert {
        "sql": "select 1, 'null' as actor_id",
        "name": "from_hook",
        "private": False,
    } == queries_by_name["from_hook"]


def test_canned_queries_non_async(app_client):
    response = app_client.get("/fixtures/from_hook.json?_shape=array")
    assert [{"1": 1, "actor_id": "null"}] == response.json


def test_canned_queries_async(app_client):
    response = app_client.get("/fixtures/from_async_hook.json?_shape=array")
    assert [{"2": 2}] == response.json


def test_canned_queries_actor(app_client):
    assert [{"1": 1, "actor_id": "bot"}] == app_client.get(
        "/fixtures/from_hook.json?_bot=1&_shape=array"
    ).json


def test_register_magic_parameters(restore_working_directory):
    with make_app_client(
        extra_databases={"data.db": "create table logs (line text)"},
        metadata={
            "databases": {
                "data": {
                    "queries": {
                        "runme": {
                            "sql": "insert into logs (line) values (:_request_http_version)",
                            "write": True,
                        },
                        "get_uuid": {"sql": "select :_uuid_new",},
                    }
                }
            }
        },
    ) as client:
        response = client.post("/data/runme", {}, csrftoken_from=True)
        assert 200 == response.status
        actual = client.get("/data/logs.json?_sort_desc=rowid&_shape=array").json
        assert [{"rowid": 1, "line": "1.0"}] == actual
        # Now try the GET request against get_uuid
        response_get = client.get("/data/get_uuid.json?_shape=array")
        assert 200 == response_get.status
        new_uuid = response_get.json[0][":_uuid_new"]
        assert 4 == new_uuid.count("-")
