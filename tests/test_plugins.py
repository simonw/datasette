from bs4 import BeautifulSoup as Soup
from .fixtures import (
    app_client,
    make_app_client,
    TABLES,
    TEMP_PLUGIN_SECRET_FILE,
    TestClient as _TestClient,
)  # noqa
from click.testing import CliRunner
from datasette.app import Datasette
from datasette import cli, hookimpl
from datasette.filters import FilterArguments
from datasette.plugins import get_plugins, DEFAULT_PLUGINS, pm
from datasette.utils.sqlite import sqlite3
from datasette.utils import CustomRow
from jinja2.environment import Template
import base64
import importlib
import json
import os
import pathlib
import re
import textwrap
import pytest
import urllib

at_memory_re = re.compile(r" at 0x\w+")


@pytest.mark.parametrize(
    "plugin_hook", [name for name in dir(pm.hook) if not name.startswith("_")]
)
def test_plugin_hooks_have_tests(plugin_hook):
    """Every plugin hook should be referenced in this test module"""
    tests_in_this_module = [t for t in globals().keys() if t.startswith("test_hook_")]
    ok = False
    for test in tests_in_this_module:
        if plugin_hook in test:
            ok = True
    assert ok, f"Plugin hook is missing tests: {plugin_hook}"


def test_hook_plugins_dir_plugin_prepare_connection(app_client):
    response = app_client.get(
        "/fixtures.json?sql=select+convert_units(100%2C+'m'%2C+'ft')"
    )
    assert pytest.approx(328.0839) == response.json["rows"][0][0]


def test_hook_plugin_prepare_connection_arguments(app_client):
    response = app_client.get(
        "/fixtures.json?sql=select+prepare_connection_args()&_shape=arrayfirst"
    )
    assert [
        "database=fixtures, datasette.plugin_config(\"name-of-plugin\")={'depth': 'root'}"
    ] == response.json


@pytest.mark.parametrize(
    "path,expected_decoded_object",
    [
        (
            "/",
            {
                "template": "index.html",
                "database": None,
                "table": None,
                "view_name": "index",
                "request_path": "/",
                "added": 15,
                "columns": None,
            },
        ),
        (
            "/fixtures",
            {
                "template": "database.html",
                "database": "fixtures",
                "table": None,
                "view_name": "database",
                "request_path": "/fixtures",
                "added": 15,
                "columns": None,
            },
        ),
        (
            "/fixtures/sortable",
            {
                "template": "table.html",
                "database": "fixtures",
                "table": "sortable",
                "view_name": "table",
                "request_path": "/fixtures/sortable",
                "added": 15,
                "columns": [
                    "pk1",
                    "pk2",
                    "content",
                    "sortable",
                    "sortable_with_nulls",
                    "sortable_with_nulls_2",
                    "text",
                ],
            },
        ),
    ],
)
def test_hook_extra_css_urls(app_client, path, expected_decoded_object):
    response = app_client.get(path)
    assert response.status == 200
    links = Soup(response.body, "html.parser").findAll("link")
    special_href = [
        l for l in links if l.attrs["href"].endswith("/extra-css-urls-demo.css")
    ][0]["href"]
    # This link has a base64-encoded JSON blob in it
    encoded = special_href.split("/")[3]
    assert expected_decoded_object == json.loads(
        base64.b64decode(encoded).decode("utf8")
    )


def test_hook_extra_js_urls(app_client):
    response = app_client.get("/")
    scripts = Soup(response.body, "html.parser").findAll("script")
    script_attrs = [s.attrs for s in scripts]
    for attrs in [
        {
            "integrity": "SRIHASH",
            "crossorigin": "anonymous",
            "src": "https://plugin-example.datasette.io/jquery.js",
        },
        {
            "src": "https://plugin-example.datasette.io/plugin.module.js",
            "type": "module",
        },
    ]:
        assert any(s == attrs for s in script_attrs), "Expected: {}".format(attrs)


def test_plugins_with_duplicate_js_urls(app_client):
    # If two plugins both require jQuery, jQuery should be loaded only once
    response = app_client.get("/fixtures")
    # This test is a little tricky, as if the user has any other plugins in
    # their current virtual environment those may affect what comes back too.
    # What matters is that https://plugin-example.datasette.io/jquery.js is only there once
    # and it comes before plugin1.js and plugin2.js which could be in either
    # order
    scripts = Soup(response.body, "html.parser").findAll("script")
    srcs = [s["src"] for s in scripts if s.get("src")]
    # No duplicates allowed:
    assert len(srcs) == len(set(srcs))
    # jquery.js loaded once:
    assert 1 == srcs.count("https://plugin-example.datasette.io/jquery.js")
    # plugin1.js and plugin2.js are both there:
    assert 1 == srcs.count("https://plugin-example.datasette.io/plugin1.js")
    assert 1 == srcs.count("https://plugin-example.datasette.io/plugin2.js")
    # jquery comes before them both
    assert srcs.index("https://plugin-example.datasette.io/jquery.js") < srcs.index(
        "https://plugin-example.datasette.io/plugin1.js"
    )
    assert srcs.index("https://plugin-example.datasette.io/jquery.js") < srcs.index(
        "https://plugin-example.datasette.io/plugin2.js"
    )


def test_hook_render_cell_link_from_json(app_client):
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


def test_hook_render_cell_demo(app_client):
    response = app_client.get("/fixtures/simple_primary_key?id=4")
    soup = Soup(response.body, "html.parser")
    td = soup.find("td", {"class": "col-content"})
    assert json.loads(td.string) == {
        "row": {"id": "4", "content": "RENDER_CELL_DEMO"},
        "column": "content",
        "table": "simple_primary_key",
        "database": "fixtures",
        "config": {"depth": "table", "special": "this-is-simple_primary_key"},
    }


@pytest.mark.parametrize(
    "path", ("/fixtures?sql=select+'RENDER_CELL_ASYNC'", "/fixtures/simple_primary_key")
)
def test_hook_render_cell_async(app_client, path):
    response = app_client.get(path)
    assert b"RENDER_CELL_ASYNC_RESULT" in response.body


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
    with open(TEMP_PLUGIN_SECRET_FILE, "w") as fp:
        fp.write("FROM_FILE")
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
                "view_name": "index",
                "request_path": "/",
                "added": 15,
                "columns": None,
            },
        ),
        (
            "/fixtures",
            {
                "template": "database.html",
                "database": "fixtures",
                "table": None,
                "config": {"depth": "database"},
                "view_name": "database",
                "request_path": "/fixtures",
                "added": 15,
                "columns": None,
            },
        ),
        (
            "/fixtures/sortable",
            {
                "template": "table.html",
                "database": "fixtures",
                "table": "sortable",
                "config": {"depth": "table"},
                "view_name": "table",
                "request_path": "/fixtures/sortable",
                "added": 15,
                "columns": [
                    "pk1",
                    "pk2",
                    "content",
                    "sortable",
                    "sortable_with_nulls",
                    "sortable_with_nulls_2",
                    "text",
                ],
            },
        ),
    ],
)
def test_hook_extra_body_script(app_client, path, expected_extra_body_script):
    r = re.compile(r"<script type=\"module\">var extra_body_script = (.*?);</script>")
    json_data = r.search(app_client.get(path).text).group(1)
    actual_data = json.loads(json_data)
    assert expected_extra_body_script == actual_data


def test_hook_asgi_wrapper(app_client):
    response = app_client.get("/fixtures")
    assert "_internal, fixtures" == response.headers["x-databases"]


def test_hook_extra_template_vars(restore_working_directory):
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
            "columns": None,
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
        Datasette([db_path], template_dir=str(templates), plugins_dir=str(plugins))
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
    assert f"view_name:{view_name}" == response.text


def test_hook_register_output_renderer_no_parameters(app_client):
    response = app_client.get("/fixtures/facetable.testnone")
    assert 200 == response.status
    assert b"Hello" == response.body


def test_hook_register_output_renderer_all_parameters(app_client):
    response = app_client.get("/fixtures/facetable.testall")
    assert 200 == response.status
    # Lots of 'at 0x103a4a690' in here - replace those so we can do
    # an easy comparison
    body = at_memory_re.sub(" at 0xXXX", response.text)
    assert json.loads(body) == {
        "datasette": "<datasette.app.Datasette object at 0xXXX>",
        "columns": [
            "pk",
            "created",
            "planet_int",
            "on_earth",
            "state",
            "_city_id",
            "_neighborhood",
            "tags",
            "complex_array",
            "distinct_some_null",
            "n",
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
        "sql": "select pk, created, planet_int, on_earth, state, _city_id, _neighborhood, tags, complex_array, distinct_some_null, n from facetable order by pk limit 51",
        "query_name": None,
        "database": "fixtures",
        "table": "facetable",
        "request": '<asgi.Request method="GET" url="http://localhost/fixtures/facetable.testall">',
        "view_name": "table",
        "1+1": 2,
    }
    # Test that query_name is set correctly
    query_response = app_client.get("/fixtures/pragma_cache_size.testall")
    assert "pragma_cache_size" == json.loads(query_response.body)["query_name"]


def test_hook_register_output_renderer_custom_status_code(app_client):
    response = app_client.get("/fixtures/pragma_cache_size.testall?status_code=202")
    assert 202 == response.status


def test_hook_register_output_renderer_custom_content_type(app_client):
    response = app_client.get(
        "/fixtures/pragma_cache_size.testall?content_type=text/blah"
    )
    assert "text/blah" == response.headers["content-type"]


def test_hook_register_output_renderer_custom_headers(app_client):
    response = app_client.get(
        "/fixtures/pragma_cache_size.testall?header=x-wow:1&header=x-gosh:2"
    )
    assert "1" == response.headers["x-wow"]
    assert "2" == response.headers["x-gosh"]


def test_hook_register_output_renderer_returning_response(app_client):
    response = app_client.get("/fixtures/facetable.testresponse")
    assert 200 == response.status
    assert response.json == {"this_is": "json"}


def test_hook_register_output_renderer_returning_broken_value(app_client):
    response = app_client.get("/fixtures/facetable.testresponse?_broken=1")
    assert 500 == response.status
    assert "this should break should be dict or Response" in response.text


def test_hook_register_output_renderer_can_render(app_client):
    response = app_client.get("/fixtures/facetable?_no_can_render=1")
    assert response.status == 200
    links = (
        Soup(response.body, "html.parser")
        .find("p", {"class": "export-links"})
        .findAll("a")
    )
    actual = [l["href"] for l in links]
    # Should not be present because we sent ?_no_can_render=1
    assert "/fixtures/facetable.testall?_labels=on" not in actual
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
            "_city_id",
            "_neighborhood",
            "tags",
            "complex_array",
            "distinct_some_null",
            "n",
        ],
        "sql": "select pk, created, planet_int, on_earth, state, _city_id, _neighborhood, tags, complex_array, distinct_some_null, n from facetable order by pk limit 51",
        "query_name": None,
        "database": "fixtures",
        "table": "facetable",
        "view_name": "table",
    }.items() <= app_client.ds._can_render_saw.items()


@pytest.mark.asyncio
async def test_hook_prepare_jinja2_environment(app_client):
    app_client.ds._HELLO = "HI"
    await app_client.ds.invoke_startup()
    template = app_client.ds.jinja_env.from_string(
        "Hello there, {{ a|format_numeric }}, {{ a|to_hello }}, {{ b|select_times_three }}",
        {"a": 3412341, "b": 5},
    )
    rendered = await app_client.ds.render_template(template)
    assert "Hello there, 3,412,341, HI, 15" == rendered


def test_hook_publish_subcommand():
    # This is hard to test properly, because publish subcommand plugins
    # cannot be loaded using the --plugins-dir mechanism - they need
    # to be installed using "pip install". So I'm cheating and taking
    # advantage of the fact that cloudrun/heroku use the plugin hook
    # to register themselves as default plugins.
    assert ["cloudrun", "heroku"] == cli.publish.list_commands({})


def test_hook_register_facet_classes(app_client):
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


def test_hook_actor_from_request(app_client):
    app_client.get("/")
    # Should have no actor
    assert None == app_client.ds._last_request.scope["actor"]
    app_client.get("/?_bot=1")
    # Should have bot actor
    assert {"id": "bot"} == app_client.ds._last_request.scope["actor"]


def test_hook_actor_from_request_async(app_client):
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
async def test_hook_permission_allowed(app_client, action, expected):
    actual = await app_client.ds.permission_allowed(
        {"id": "actor"}, action, default=None
    )
    assert expected == actual


def test_actor_json(app_client):
    assert {"actor": None} == app_client.get("/-/actor.json").json
    assert {"actor": {"id": "bot2", "1+1": 2}} == app_client.get(
        "/-/actor.json?_bot2=1"
    ).json


@pytest.mark.parametrize(
    "path,body",
    [
        ("/one/", "2"),
        ("/two/Ray?greeting=Hail", "Hail Ray"),
        ("/not-async/", "This was not async"),
    ],
)
def test_hook_register_routes(app_client, path, body):
    response = app_client.get(path)
    assert 200 == response.status
    assert body == response.text


@pytest.mark.parametrize("configured_path", ("path1", "path2"))
def test_hook_register_routes_with_datasette(configured_path):
    with make_app_client(
        metadata={
            "plugins": {
                "register-route-demo": {
                    "path": configured_path,
                }
            }
        }
    ) as client:
        response = client.get(f"/{configured_path}/")
        assert response.status == 200
        assert configured_path.upper() == response.text
        # Other one should 404
        other_path = [p for p in ("path1", "path2") if configured_path != p][0]
        assert client.get(f"/{other_path}/", follow_redirects=True).status == 404


def test_hook_register_routes_override():
    "Plugins can over-ride default paths such as /db/table"
    with make_app_client(
        metadata={
            "plugins": {
                "register-route-demo": {
                    "path": "blah",
                }
            }
        }
    ) as client:
        response = client.get("/db/table")
        assert response.status == 200
        assert (
            response.text
            == "/db/table: [('db_name', 'db'), ('table_and_format', 'table')]"
        )


def test_hook_register_routes_post(app_client):
    response = app_client.post("/post/", {"this is": "post data"}, csrftoken_from=True)
    assert 200 == response.status
    assert "csrftoken" in response.json
    assert "post data" == response.json["this is"]


def test_hook_register_routes_csrftoken(restore_working_directory, tmpdir_factory):
    templates = tmpdir_factory.mktemp("templates")
    (templates / "csrftoken_form.html").write_text(
        "CSRFTOKEN: {{ csrftoken() }}", "utf-8"
    )
    with make_app_client(template_dir=templates) as client:
        response = client.get("/csrftoken-form/")
        expected_token = client.ds._last_request.scope["csrftoken"]()
        assert f"CSRFTOKEN: {expected_token}" == response.text


def test_hook_register_routes_asgi(app_client):
    response = app_client.get("/three/")
    assert {"hello": "world"} == response.json
    assert "1" == response.headers["x-three"]


def test_hook_register_routes_add_message(app_client):
    response = app_client.get("/add-message/")
    assert 200 == response.status
    assert "Added message" == response.text
    decoded = app_client.ds.unsign(response.cookies["ds_messages"], "messages")
    assert [["Hello from messages", 1]] == decoded


def test_hook_register_routes_render_message(restore_working_directory, tmpdir_factory):
    templates = tmpdir_factory.mktemp("templates")
    (templates / "render_message.html").write_text('{% extends "base.html" %}', "utf-8")
    with make_app_client(template_dir=templates) as client:
        response1 = client.get("/add-message/")
        response2 = client.get("/render-message/", cookies=response1.cookies)
        assert 200 == response2.status
        assert "Hello from messages" in response2.text


@pytest.mark.asyncio
async def test_hook_startup(app_client):
    await app_client.ds.invoke_startup()
    assert app_client.ds._startup_hook_fired
    assert 2 == app_client.ds._startup_hook_calculation


def test_hook_canned_queries(app_client):
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


def test_hook_canned_queries_non_async(app_client):
    response = app_client.get("/fixtures/from_hook.json?_shape=array")
    assert [{"1": 1, "actor_id": "null"}] == response.json


def test_hook_canned_queries_async(app_client):
    response = app_client.get("/fixtures/from_async_hook.json?_shape=array")
    assert [{"2": 2}] == response.json


def test_hook_canned_queries_actor(app_client):
    assert [{"1": 1, "actor_id": "bot"}] == app_client.get(
        "/fixtures/from_hook.json?_bot=1&_shape=array"
    ).json


def test_hook_register_magic_parameters(restore_working_directory):
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
                        "get_uuid": {
                            "sql": "select :_uuid_new",
                        },
                    }
                }
            }
        },
    ) as client:
        response = client.post("/data/runme", {}, csrftoken_from=True)
        assert 302 == response.status
        actual = client.get("/data/logs.json?_sort_desc=rowid&_shape=array").json
        assert [{"rowid": 1, "line": "1.1"}] == actual
        # Now try the GET request against get_uuid
        response_get = client.get("/data/get_uuid.json?_shape=array")
        assert 200 == response_get.status
        new_uuid = response_get.json[0][":_uuid_new"]
        assert 4 == new_uuid.count("-")


def test_hook_forbidden(restore_working_directory):
    with make_app_client(
        extra_databases={"data2.db": "create table logs (line text)"},
        metadata={"allow": {}},
    ) as client:
        response = client.get("/")
        assert 403 == response.status
        response2 = client.get("/data2")
        assert 302 == response2.status
        assert (
            response2.headers["Location"]
            == "/login?message=You do not have permission to view this database"
        )
        assert (
            client.ds._last_forbidden_message
            == "You do not have permission to view this database"
        )


def test_hook_handle_exception(app_client):
    app_client.get("/trigger-error?x=123")
    assert hasattr(app_client.ds, "_exception_hook_fired")
    request, exception = app_client.ds._exception_hook_fired
    assert request.url == "http://localhost/trigger-error?x=123"
    assert isinstance(exception, ZeroDivisionError)


@pytest.mark.parametrize("param", ("_custom_error", "_custom_error_async"))
def test_hook_handle_exception_custom_response(app_client, param):
    response = app_client.get("/trigger-error?{}=1".format(param))
    assert response.text == param


def test_hook_menu_links(app_client):
    def get_menu_links(html):
        soup = Soup(html, "html.parser")
        return [
            {"label": a.text, "href": a["href"]} for a in soup.select(".nav-menu a")
        ]

    response = app_client.get("/")
    assert get_menu_links(response.text) == []

    response_2 = app_client.get("/?_bot=1&_hello=BOB")
    assert get_menu_links(response_2.text) == [
        {"label": "Hello, BOB", "href": "/"},
        {"label": "Hello 2", "href": "/"},
    ]


@pytest.mark.parametrize("table_or_view", ["facetable", "simple_view"])
def test_hook_table_actions(app_client, table_or_view):
    def get_table_actions_links(html):
        soup = Soup(html, "html.parser")
        details = soup.find("details", {"class": "actions-menu-links"})
        if details is None:
            return []
        return [{"label": a.text, "href": a["href"]} for a in details.select("a")]

    response = app_client.get(f"/fixtures/{table_or_view}")
    assert get_table_actions_links(response.text) == []

    response_2 = app_client.get(f"/fixtures/{table_or_view}?_bot=1&_hello=BOB")
    assert sorted(
        get_table_actions_links(response_2.text), key=lambda l: l["label"]
    ) == [
        {"label": "Database: fixtures", "href": "/"},
        {"label": "From async BOB", "href": "/"},
        {"label": f"Table: {table_or_view}", "href": "/"},
    ]


def test_hook_database_actions(app_client):
    def get_table_actions_links(html):
        soup = Soup(html, "html.parser")
        details = soup.find("details", {"class": "actions-menu-links"})
        if details is None:
            return []
        return [{"label": a.text, "href": a["href"]} for a in details.select("a")]

    response = app_client.get("/fixtures")
    assert get_table_actions_links(response.text) == []

    response_2 = app_client.get("/fixtures?_bot=1&_hello=BOB")
    assert get_table_actions_links(response_2.text) == [
        {"label": "Database: fixtures - BOB", "href": "/"},
    ]


def test_hook_skip_csrf(app_client):
    cookie = app_client.actor_cookie({"id": "test"})
    csrf_response = app_client.post(
        "/post/",
        post_data={"this is": "post data"},
        csrftoken_from=True,
        cookies={"ds_actor": cookie},
    )
    assert csrf_response.status == 200
    missing_csrf_response = app_client.post(
        "/post/", post_data={"this is": "post data"}, cookies={"ds_actor": cookie}
    )
    assert missing_csrf_response.status == 403
    # But "/skip-csrf" should allow
    allow_csrf_response = app_client.post(
        "/skip-csrf", post_data={"this is": "post data"}, cookies={"ds_actor": cookie}
    )
    assert allow_csrf_response.status == 405  # Method not allowed
    # /skip-csrf-2 should not
    second_missing_csrf_response = app_client.post(
        "/skip-csrf-2", post_data={"this is": "post data"}, cookies={"ds_actor": cookie}
    )
    assert second_missing_csrf_response.status == 403


def test_hook_get_metadata(app_client):
    app_client.ds._metadata_local = {
        "title": "Testing get_metadata hook!",
        "databases": {"from-local": {"title": "Hello from local metadata"}},
    }
    og_pm_hook_get_metadata = pm.hook.get_metadata

    def get_metadata_mock(*args, **kwargs):
        return [
            {
                "databases": {
                    "from-hook": {"title": "Hello from the plugin hook"},
                    "from-local": {"title": "This will be overwritten!"},
                }
            }
        ]

    pm.hook.get_metadata = get_metadata_mock
    meta = app_client.ds.metadata()
    assert "Testing get_metadata hook!" == meta["title"]
    assert "Hello from local metadata" == meta["databases"]["from-local"]["title"]
    assert "Hello from the plugin hook" == meta["databases"]["from-hook"]["title"]
    pm.hook.get_metadata = og_pm_hook_get_metadata


def _extract_commands(output):
    lines = output.split("Commands:\n", 1)[1].split("\n")
    return {line.split()[0].replace("*", "") for line in lines if line.strip()}


def test_hook_register_commands():
    # Without the plugin should have seven commands
    runner = CliRunner()
    result = runner.invoke(cli.cli, "--help")
    commands = _extract_commands(result.output)
    assert commands == {
        "serve",
        "inspect",
        "install",
        "package",
        "plugins",
        "publish",
        "uninstall",
    }

    # Now install a plugin
    class VerifyPlugin:
        __name__ = "VerifyPlugin"

        @hookimpl
        def register_commands(self, cli):
            @cli.command()
            def verify():
                pass

            @cli.command()
            def unverify():
                pass

    pm.register(VerifyPlugin(), name="verify")
    importlib.reload(cli)
    result2 = runner.invoke(cli.cli, "--help")
    commands2 = _extract_commands(result2.output)
    assert commands2 == {
        "serve",
        "inspect",
        "install",
        "package",
        "plugins",
        "publish",
        "uninstall",
        "verify",
        "unverify",
    }
    pm.unregister(name="verify")
    importlib.reload(cli)


def test_hook_filters_from_request(app_client):
    class ReturnNothingPlugin:
        __name__ = "ReturnNothingPlugin"

        @hookimpl
        def filters_from_request(self, request):
            if request.args.get("_nothing"):
                return FilterArguments(["1 = 0"], human_descriptions=["NOTHING"])

    pm.register(ReturnNothingPlugin(), name="ReturnNothingPlugin")
    response = app_client.get("/fixtures/facetable?_nothing=1")
    assert "0 rows\n        where NOTHING" in response.text
    json_response = app_client.get("/fixtures/facetable.json?_nothing=1")
    assert json_response.json["rows"] == []
    pm.unregister(name="ReturnNothingPlugin")
