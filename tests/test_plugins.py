from bs4 import BeautifulSoup as Soup
from .fixtures import (
    app_client,
    make_app_client,
    TABLES,
    TEMP_PLUGIN_SECRET_FILE,
    PLUGINS_DIR,
    TestClient as _TestClient,
)  # noqa
from click.testing import CliRunner
from datasette.app import Datasette
from datasette import cli, hookimpl, Permission
from datasette.filters import FilterArguments
from datasette.plugins import get_plugins, DEFAULT_PLUGINS, pm
from datasette.utils.sqlite import sqlite3
from datasette.utils import StartupError
from jinja2 import ChoiceLoader, FileSystemLoader
import base64
import datetime
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


@pytest.mark.asyncio
async def test_hook_plugins_dir_plugin_prepare_connection(ds_client):
    response = await ds_client.get(
        "/fixtures.json?_shape=arrayfirst&sql=select+convert_units(100%2C+'m'%2C+'ft')"
    )
    assert response.json()[0] == pytest.approx(328.0839)


@pytest.mark.asyncio
async def test_hook_plugin_prepare_connection_arguments(ds_client):
    response = await ds_client.get(
        "/fixtures.json?sql=select+prepare_connection_args()&_shape=arrayfirst"
    )
    assert [
        "database=fixtures, datasette.plugin_config(\"name-of-plugin\")={'depth': 'root'}"
    ] == response.json()


@pytest.mark.asyncio
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
async def test_hook_extra_css_urls(ds_client, path, expected_decoded_object):
    response = await ds_client.get(path)
    assert response.status_code == 200
    links = Soup(response.text, "html.parser").findAll("link")
    special_href = [
        link
        for link in links
        if link.attrs["href"].endswith("/extra-css-urls-demo.css")
    ][0]["href"]
    # This link has a base64-encoded JSON blob in it
    encoded = special_href.split("/")[3]
    actual_decoded_object = json.loads(base64.b64decode(encoded).decode("utf8"))
    assert expected_decoded_object == actual_decoded_object


@pytest.mark.asyncio
async def test_hook_extra_js_urls(ds_client):
    response = await ds_client.get("/")
    scripts = Soup(response.text, "html.parser").findAll("script")
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


@pytest.mark.asyncio
async def test_plugins_with_duplicate_js_urls(ds_client):
    # If two plugins both require jQuery, jQuery should be loaded only once
    response = await ds_client.get("/fixtures")
    # This test is a little tricky, as if the user has any other plugins in
    # their current virtual environment those may affect what comes back too.
    # What matters is that https://plugin-example.datasette.io/jquery.js is only there once
    # and it comes before plugin1.js and plugin2.js which could be in either
    # order
    scripts = Soup(response.text, "html.parser").findAll("script")
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


@pytest.mark.asyncio
async def test_hook_render_cell_link_from_json(ds_client):
    sql = """
        select '{"href": "http://example.com/", "label":"Example"}'
    """.strip()
    path = "/fixtures?" + urllib.parse.urlencode({"sql": sql})
    response = await ds_client.get(path)
    td = Soup(response.text, "html.parser").find("table").find("tbody").find("td")
    a = td.find("a")
    assert a is not None, str(a)
    assert a.attrs["href"] == "http://example.com/"
    assert a.attrs["data-database"] == "fixtures"
    assert a.text == "Example"


@pytest.mark.asyncio
async def test_hook_render_cell_demo(ds_client):
    response = await ds_client.get(
        "/fixtures/simple_primary_key?id=4&_render_cell_extra=1"
    )
    soup = Soup(response.text, "html.parser")
    td = soup.find("td", {"class": "col-content"})
    assert json.loads(td.string) == {
        "row": {"id": "4", "content": "RENDER_CELL_DEMO"},
        "column": "content",
        "table": "simple_primary_key",
        "database": "fixtures",
        "config": {"depth": "table", "special": "this-is-simple_primary_key"},
        "render_cell_extra": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ("/fixtures?sql=select+'RENDER_CELL_ASYNC'", "/fixtures/simple_primary_key")
)
async def test_hook_render_cell_async(ds_client, path):
    response = await ds_client.get(path)
    assert b"RENDER_CELL_ASYNC_RESULT" in response.content


@pytest.mark.asyncio
async def test_plugin_config(ds_client):
    assert {"depth": "table"} == ds_client.ds.plugin_config(
        "name-of-plugin", database="fixtures", table="sortable"
    )
    assert {"depth": "database"} == ds_client.ds.plugin_config(
        "name-of-plugin", database="fixtures", table="unknown_table"
    )
    assert {"depth": "database"} == ds_client.ds.plugin_config(
        "name-of-plugin", database="fixtures"
    )
    assert {"depth": "root"} == ds_client.ds.plugin_config(
        "name-of-plugin", database="unknown_database"
    )
    assert {"depth": "root"} == ds_client.ds.plugin_config("name-of-plugin")
    assert None is ds_client.ds.plugin_config("unknown-plugin")


@pytest.mark.asyncio
async def test_plugin_config_env(ds_client, monkeypatch):
    monkeypatch.setenv("FOO_ENV", "FROM_ENVIRONMENT")
    assert ds_client.ds.plugin_config("env-plugin") == {"foo": "FROM_ENVIRONMENT"}


@pytest.mark.asyncio
async def test_plugin_config_env_from_config(monkeypatch):
    monkeypatch.setenv("FOO_ENV", "FROM_ENVIRONMENT_2")
    datasette = Datasette(
        config={"plugins": {"env-plugin": {"setting": {"$env": "FOO_ENV"}}}}
    )
    assert datasette.plugin_config("env-plugin") == {"setting": "FROM_ENVIRONMENT_2"}


@pytest.mark.asyncio
async def test_plugin_config_env_from_list(ds_client):
    os.environ["FOO_ENV"] = "FROM_ENVIRONMENT"
    assert [{"in_a_list": "FROM_ENVIRONMENT"}] == ds_client.ds.plugin_config(
        "env-plugin-list"
    )
    del os.environ["FOO_ENV"]


@pytest.mark.asyncio
async def test_plugin_config_file(ds_client):
    with open(TEMP_PLUGIN_SECRET_FILE, "w") as fp:
        fp.write("FROM_FILE")
    assert {"foo": "FROM_FILE"} == ds_client.ds.plugin_config("file-plugin")
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


@pytest.mark.asyncio
async def test_hook_asgi_wrapper(ds_client):
    response = await ds_client.get("/fixtures")
    assert "fixtures" == response.headers["x-databases"]


def test_hook_extra_template_vars(restore_working_directory):
    with make_app_client(
        template_dir=str(pathlib.Path(__file__).parent / "test_templates")
    ) as client:
        response = client.get("/-/metadata")
        assert response.status_code == 200
        extra_template_vars = json.loads(
            Soup(response.text, "html.parser").select("pre.extra_template_vars")[0].text
        )
        assert {
            "template": "show_json.html",
            "scope_path": "/-/metadata",
            "columns": None,
        } == extra_template_vars
        extra_template_vars_from_awaitable = json.loads(
            Soup(response.text, "html.parser")
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
        assert response.status_code == 200
        extra_from_awaitable_function = (
            Soup(response.text, "html.parser")
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
    assert response.status_code == 200
    assert f"view_name:{view_name}" == response.text


@pytest.mark.asyncio
async def test_hook_register_output_renderer_no_parameters(ds_client):
    response = await ds_client.get("/fixtures/facetable.testnone")
    assert response.status_code == 200
    assert b"Hello" == response.content


@pytest.mark.asyncio
async def test_hook_register_output_renderer_all_parameters(ds_client):
    response = await ds_client.get("/fixtures/facetable.testall")
    assert response.status_code == 200
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
    query_response = await ds_client.get("/fixtures/pragma_cache_size.testall")
    assert query_response.json()["query_name"] == "pragma_cache_size"


@pytest.mark.asyncio
async def test_hook_register_output_renderer_custom_status_code(ds_client):
    response = await ds_client.get(
        "/fixtures/pragma_cache_size.testall?status_code=202"
    )
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_hook_register_output_renderer_custom_content_type(ds_client):
    response = await ds_client.get(
        "/fixtures/pragma_cache_size.testall?content_type=text/blah"
    )
    assert "text/blah" == response.headers["content-type"]


@pytest.mark.asyncio
async def test_hook_register_output_renderer_custom_headers(ds_client):
    response = await ds_client.get(
        "/fixtures/pragma_cache_size.testall?header=x-wow:1&header=x-gosh:2"
    )
    assert "1" == response.headers["x-wow"]
    assert "2" == response.headers["x-gosh"]


@pytest.mark.asyncio
async def test_hook_register_output_renderer_returning_response(ds_client):
    response = await ds_client.get("/fixtures/facetable.testresponse")
    assert response.status_code == 200
    assert response.json() == {"this_is": "json"}


@pytest.mark.asyncio
async def test_hook_register_output_renderer_returning_broken_value(ds_client):
    response = await ds_client.get("/fixtures/facetable.testresponse?_broken=1")
    assert response.status_code == 500
    assert "this should break should be dict or Response" in response.text


@pytest.mark.asyncio
async def test_hook_register_output_renderer_can_render(ds_client):
    response = await ds_client.get("/fixtures/facetable?_no_can_render=1")
    assert response.status_code == 200
    links = (
        Soup(response.text, "html.parser")
        .find("p", {"class": "export-links"})
        .findAll("a")
    )
    actual = [link["href"] for link in links]
    # Should not be present because we sent ?_no_can_render=1
    assert "/fixtures/facetable.testall?_labels=on" not in actual
    # Check that it was passed the values we expected
    assert hasattr(ds_client.ds, "_can_render_saw")
    assert {
        "datasette": ds_client.ds,
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
    }.items() <= ds_client.ds._can_render_saw.items()


@pytest.mark.asyncio
async def test_hook_prepare_jinja2_environment(ds_client):
    ds_client.ds._HELLO = "HI"
    await ds_client.ds.invoke_startup()
    environment = ds_client.ds.get_jinja_environment(None)
    template = environment.from_string(
        "Hello there, {{ a|format_numeric }}, {{ a|to_hello }}, {{ b|select_times_three }}",
        {"a": 3412341, "b": 5},
    )
    rendered = await ds_client.ds.render_template(template)
    assert "Hello there, 3,412,341, HI, 15" == rendered


def test_hook_publish_subcommand():
    # This is hard to test properly, because publish subcommand plugins
    # cannot be loaded using the --plugins-dir mechanism - they need
    # to be installed using "pip install". So I'm cheating and taking
    # advantage of the fact that cloudrun/heroku use the plugin hook
    # to register themselves as default plugins.
    assert ["cloudrun", "heroku"] == cli.publish.list_commands({})


@pytest.mark.asyncio
async def test_hook_register_facet_classes(ds_client):
    response = await ds_client.get(
        "/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_extra=suggested_facets"
    )
    assert response.json()["suggested_facets"] == [
        {
            "name": "pk1",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_extra=suggested_facets&_facet_dummy=pk1",
            "type": "dummy",
        },
        {
            "name": "pk2",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_extra=suggested_facets&_facet_dummy=pk2",
            "type": "dummy",
        },
        {
            "name": "pk3",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_extra=suggested_facets&_facet_dummy=pk3",
            "type": "dummy",
        },
        {
            "name": "content",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_extra=suggested_facets&_facet_dummy=content",
            "type": "dummy",
        },
        {
            "name": "pk1",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_extra=suggested_facets&_facet=pk1",
        },
        {
            "name": "pk2",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_extra=suggested_facets&_facet=pk2",
        },
        {
            "name": "pk3",
            "toggle_url": "http://localhost/fixtures/compound_three_primary_keys.json?_dummy_facet=1&_extra=suggested_facets&_facet=pk3",
        },
    ]


@pytest.mark.asyncio
async def test_hook_actor_from_request(ds_client):
    await ds_client.get("/")
    # Should have no actor
    assert ds_client.ds._last_request.scope["actor"] is None
    await ds_client.get("/?_bot=1")
    # Should have bot actor
    assert ds_client.ds._last_request.scope["actor"] == {"id": "bot"}


@pytest.mark.asyncio
async def test_hook_actor_from_request_async(ds_client):
    await ds_client.get("/")
    # Should have no actor
    assert ds_client.ds._last_request.scope["actor"] is None
    await ds_client.get("/?_bot2=1")
    # Should have bot2 actor
    assert ds_client.ds._last_request.scope["actor"] == {"id": "bot2", "1+1": 2}


@pytest.mark.asyncio
async def test_existing_scope_actor_respected(ds_client):
    await ds_client.get("/?_actor_in_scope=1")
    assert ds_client.ds._last_request.scope["actor"] == {"id": "from-scope"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,expected",
    [
        ("this_is_allowed", True),
        ("this_is_denied", False),
        ("this_is_allowed_async", True),
        ("this_is_denied_async", False),
    ],
)
async def test_hook_permission_allowed(action, expected):
    class TestPlugin:
        __name__ = "TestPlugin"

        @hookimpl
        def register_permissions(self):
            return [
                Permission(name, None, None, False, False, False)
                for name in (
                    "this_is_allowed",
                    "this_is_denied",
                    "this_is_allowed_async",
                    "this_is_denied_async",
                )
            ]

    pm.register(TestPlugin(), name="undo_register_extras")
    try:
        ds = Datasette(plugins_dir=PLUGINS_DIR)
        await ds.invoke_startup()
        actual = await ds.permission_allowed({"id": "actor"}, action)
        assert expected == actual
    finally:
        pm.unregister(name="undo_register_extras")


@pytest.mark.asyncio
async def test_actor_json(ds_client):
    assert (await ds_client.get("/-/actor.json")).json() == {"actor": None}
    assert (await ds_client.get("/-/actor.json?_bot2=1")).json() == {
        "actor": {"id": "bot2", "1+1": 2}
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,body",
    [
        ("/one/", "2"),
        ("/two/Ray?greeting=Hail", "Hail Ray"),
        ("/not-async/", "This was not async"),
    ],
)
async def test_hook_register_routes(ds_client, path, body):
    response = await ds_client.get(path)
    assert response.status_code == 200
    assert response.text == body


@pytest.mark.parametrize("configured_path", ("path1", "path2"))
def test_hook_register_routes_with_datasette(configured_path):
    with make_app_client(
        config={
            "plugins": {
                "register-route-demo": {
                    "path": configured_path,
                }
            }
        }
    ) as client:
        response = client.get(f"/{configured_path}/")
        assert response.status_code == 200
        assert configured_path.upper() == response.text
        # Other one should 404
        other_path = [p for p in ("path1", "path2") if configured_path != p][0]
        assert client.get(f"/{other_path}/", follow_redirects=True).status_code == 404


def test_hook_register_routes_override():
    "Plugins can over-ride default paths such as /db/table"
    with make_app_client(
        config={
            "plugins": {
                "register-route-demo": {
                    "path": "blah",
                }
            }
        }
    ) as client:
        response = client.get("/db/table")
        assert response.status_code == 200
        assert (
            response.text
            == "/db/table: [('db_name', 'db'), ('table_and_format', 'table')]"
        )


def test_hook_register_routes_post(app_client):
    response = app_client.post("/post/", {"this is": "post data"}, csrftoken_from=True)
    assert response.status_code == 200
    assert "csrftoken" in response.json
    assert response.json["this is"] == "post data"


def test_hook_register_routes_csrftoken(restore_working_directory, tmpdir_factory):
    templates = tmpdir_factory.mktemp("templates")
    (templates / "csrftoken_form.html").write_text(
        "CSRFTOKEN: {{ csrftoken() }}", "utf-8"
    )
    with make_app_client(template_dir=templates) as client:
        response = client.get("/csrftoken-form/")
        expected_token = client.ds._last_request.scope["csrftoken"]()
        assert f"CSRFTOKEN: {expected_token}" == response.text


@pytest.mark.asyncio
async def test_hook_register_routes_asgi(ds_client):
    response = await ds_client.get("/three/")
    assert {"hello": "world"} == response.json()
    assert "1" == response.headers["x-three"]


@pytest.mark.asyncio
async def test_hook_register_routes_add_message(ds_client):
    response = await ds_client.get("/add-message/")
    assert response.status_code == 200
    assert response.text == "Added message"
    decoded = ds_client.ds.unsign(response.cookies["ds_messages"], "messages")
    assert decoded == [["Hello from messages", 1]]


def test_hook_register_routes_render_message(restore_working_directory, tmpdir_factory):
    templates = tmpdir_factory.mktemp("templates")
    (templates / "render_message.html").write_text('{% extends "base.html" %}', "utf-8")
    with make_app_client(template_dir=templates) as client:
        response1 = client.get("/add-message/")
        response2 = client.get("/render-message/", cookies=response1.cookies)
        assert 200 == response2.status
        assert "Hello from messages" in response2.text


@pytest.mark.asyncio
async def test_hook_startup(ds_client):
    await ds_client.ds.invoke_startup()
    assert ds_client.ds._startup_hook_fired
    assert 2 == ds_client.ds._startup_hook_calculation


@pytest.mark.asyncio
async def test_hook_canned_queries(ds_client):
    queries = (await ds_client.get("/fixtures.json")).json()["queries"]
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


@pytest.mark.asyncio
async def test_hook_canned_queries_non_async(ds_client):
    response = await ds_client.get("/fixtures/from_hook.json?_shape=array")
    assert [{"1": 1, "actor_id": "null"}] == response.json()


@pytest.mark.asyncio
async def test_hook_canned_queries_async(ds_client):
    response = await ds_client.get("/fixtures/from_async_hook.json?_shape=array")
    assert [{"2": 2}] == response.json()


@pytest.mark.asyncio
async def test_hook_canned_queries_actor(ds_client):
    assert (
        await ds_client.get("/fixtures/from_hook.json?_bot=1&_shape=array")
    ).json() == [{"1": 1, "actor_id": "bot"}]


def test_hook_register_magic_parameters(restore_working_directory):
    with make_app_client(
        extra_databases={"data.db": "create table logs (line text)"},
        config={
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
        assert response.status_code == 302
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
        config={"allow": {}},
    ) as client:
        response = client.get("/")
        assert response.status_code == 403
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


@pytest.mark.asyncio
async def test_hook_handle_exception(ds_client):
    await ds_client.get("/trigger-error?x=123")
    assert hasattr(ds_client.ds, "_exception_hook_fired")
    request, exception = ds_client.ds._exception_hook_fired
    assert request.url == "http://localhost/trigger-error?x=123"
    assert isinstance(exception, ZeroDivisionError)


@pytest.mark.asyncio
@pytest.mark.parametrize("param", ("_custom_error", "_custom_error_async"))
async def test_hook_handle_exception_custom_response(ds_client, param):
    response = await ds_client.get("/trigger-error?{}=1".format(param))
    assert response.text == param


@pytest.mark.asyncio
async def test_hook_menu_links(ds_client):
    def get_menu_links(html):
        soup = Soup(html, "html.parser")
        return [
            {"label": a.text, "href": a["href"]} for a in soup.select(".nav-menu a")
        ]

    response = await ds_client.get("/")
    assert get_menu_links(response.text) == []

    response_2 = await ds_client.get("/?_bot=1&_hello=BOB")
    assert get_menu_links(response_2.text) == [
        {"label": "Hello, BOB", "href": "/"},
        {"label": "Hello 2", "href": "/"},
    ]


@pytest.mark.asyncio
async def test_hook_table_actions(ds_client):
    response = await ds_client.get("/fixtures/facetable")
    assert get_actions_links(response.text) == []
    response_2 = await ds_client.get("/fixtures/facetable?_bot=1&_hello=BOB")
    assert ">Table actions<" in response_2.text
    assert sorted(
        get_actions_links(response_2.text), key=lambda link: link["label"]
    ) == [
        {"label": "Database: fixtures", "href": "/", "description": None},
        {"label": "From async BOB", "href": "/", "description": None},
        {"label": "Table: facetable", "href": "/", "description": None},
    ]


@pytest.mark.asyncio
async def test_hook_view_actions(ds_client):
    response = await ds_client.get("/fixtures/simple_view")
    assert get_actions_links(response.text) == []
    response_2 = await ds_client.get(
        "/fixtures/simple_view",
        cookies={"ds_actor": ds_client.actor_cookie({"id": "bob"})},
    )
    assert ">View actions<" in response_2.text
    assert sorted(
        get_actions_links(response_2.text), key=lambda link: link["label"]
    ) == [
        {"label": "Database: fixtures", "href": "/", "description": None},
        {"label": "View: simple_view", "href": "/", "description": None},
    ]


def get_actions_links(html):
    soup = Soup(html, "html.parser")
    details = soup.find("details", {"class": "actions-menu-links"})
    if details is None:
        return []
    links = []
    for a_el in details.select("a"):
        description = None
        if a_el.find("p") is not None:
            description = a_el.find("p").text.strip()
            a_el.find("p").extract()
        label = a_el.text.strip()
        href = a_el["href"]
        links.append({"label": label, "href": href, "description": description})
    return links


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_url",
    (
        ("/fixtures?sql=select+1", "/fixtures?sql=explain+select+1"),
        (
            "/fixtures/pragma_cache_size",
            "/fixtures?sql=explain+PRAGMA+cache_size%3B",
        ),
        # Don't attempt to explain an explain
        ("/fixtures?sql=explain+select+1", None),
    ),
)
async def test_hook_query_actions(ds_client, path, expected_url):
    response = await ds_client.get(path)
    assert response.status_code == 200
    links = get_actions_links(response.text)
    if expected_url is None:
        assert links == []
    else:
        assert links == [
            {
                "label": "Explain this query",
                "href": expected_url,
                "description": "Runs a SQLite explain",
            }
        ]


@pytest.mark.asyncio
async def test_hook_row_actions(ds_client):
    response = await ds_client.get("/fixtures/facet_cities/1")
    assert get_actions_links(response.text) == []

    response_2 = await ds_client.get(
        "/fixtures/facet_cities/1",
        cookies={"ds_actor": ds_client.actor_cookie({"id": "sam"})},
    )
    assert get_actions_links(response_2.text) == [
        {
            "label": "Row details for sam",
            "href": "/",
            "description": '{"id": 1, "name": "San Francisco"}',
        }
    ]


@pytest.mark.asyncio
async def test_hook_database_actions(ds_client):
    response = await ds_client.get("/fixtures")
    assert get_actions_links(response.text) == []

    response_2 = await ds_client.get("/fixtures?_bot=1&_hello=BOB")
    assert get_actions_links(response_2.text) == [
        {"label": "Database: fixtures - BOB", "href": "/", "description": None},
    ]


@pytest.mark.asyncio
async def test_hook_homepage_actions(ds_client):
    response = await ds_client.get("/")
    # No button for anonymous users
    assert "<span>Homepage actions</span>" not in response.text
    # Signed in user gets an action
    response2 = await ds_client.get(
        "/", cookies={"ds_actor": ds_client.actor_cookie({"id": "troy"})}
    )
    assert "<span>Homepage actions</span>" in response2.text
    assert get_actions_links(response2.text) == [
        {
            "label": "Custom homepage for: troy",
            "href": "/-/custom-homepage",
            "description": None,
        },
    ]


def test_hook_skip_csrf(app_client):
    cookie = app_client.actor_cookie({"id": "test"})
    csrf_response = app_client.post(
        "/post/",
        post_data={"this is": "post data"},
        csrftoken_from=True,
        cookies={"ds_actor": cookie},
    )
    assert csrf_response.status_code == 200
    missing_csrf_response = app_client.post(
        "/post/", post_data={"this is": "post data"}, cookies={"ds_actor": cookie}
    )
    assert missing_csrf_response.status_code == 403
    # But "/skip-csrf" should allow
    allow_csrf_response = app_client.post(
        "/skip-csrf", post_data={"this is": "post data"}, cookies={"ds_actor": cookie}
    )
    assert allow_csrf_response.status_code == 405  # Method not allowed
    # /skip-csrf-2 should not
    second_missing_csrf_response = app_client.post(
        "/skip-csrf-2", post_data={"this is": "post data"}, cookies={"ds_actor": cookie}
    )
    assert second_missing_csrf_response.status_code == 403


@pytest.mark.asyncio
async def test_hook_get_metadata(ds_client):
    try:
        orig_metadata = ds_client.ds._metadata_local
        ds_client.ds._metadata_local = {
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
        meta = ds_client.ds.metadata()
        assert "Testing get_metadata hook!" == meta["title"]
        assert "Hello from local metadata" == meta["databases"]["from-local"]["title"]
        assert "Hello from the plugin hook" == meta["databases"]["from-hook"]["title"]
        pm.hook.get_metadata = og_pm_hook_get_metadata
    finally:
        ds_client.ds._metadata_local = orig_metadata


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
        "create-token",
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
        "create-token",
    }
    pm.unregister(name="verify")
    importlib.reload(cli)


@pytest.mark.asyncio
async def test_hook_filters_from_request(ds_client):
    class ReturnNothingPlugin:
        __name__ = "ReturnNothingPlugin"

        @hookimpl
        def filters_from_request(self, request):
            if request.args.get("_nothing"):
                return FilterArguments(["1 = 0"], human_descriptions=["NOTHING"])

    pm.register(ReturnNothingPlugin(), name="ReturnNothingPlugin")
    response = await ds_client.get("/fixtures/facetable?_nothing=1")
    assert "0 rows\n        where NOTHING" in response.text
    json_response = await ds_client.get("/fixtures/facetable.json?_nothing=1")
    assert json_response.json()["rows"] == []
    pm.unregister(name="ReturnNothingPlugin")


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_metadata", (False, True))
async def test_hook_register_permissions(extra_metadata):
    ds = Datasette(
        config=(
            {
                "plugins": {
                    "datasette-register-permissions": {
                        "permissions": [
                            {
                                "name": "extra-from-metadata",
                                "abbr": "efm",
                                "description": "Extra from metadata",
                                "takes_database": False,
                                "takes_resource": False,
                                "default": True,
                            }
                        ]
                    }
                }
            }
            if extra_metadata
            else None
        ),
        plugins_dir=PLUGINS_DIR,
    )
    await ds.invoke_startup()
    assert ds.permissions["permission-from-plugin"] == Permission(
        name="permission-from-plugin",
        abbr="np",
        description="New permission added by a plugin",
        takes_database=True,
        takes_resource=False,
        default=False,
    )
    if extra_metadata:
        assert ds.permissions["extra-from-metadata"] == Permission(
            name="extra-from-metadata",
            abbr="efm",
            description="Extra from metadata",
            takes_database=False,
            takes_resource=False,
            default=True,
        )
    else:
        assert "extra-from-metadata" not in ds.permissions


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", ("name", "abbr"))
async def test_hook_register_permissions_no_duplicates(duplicate):
    name1, name2 = "name1", "name2"
    abbr1, abbr2 = "abbr1", "abbr2"
    if duplicate == "name":
        name2 = "name1"
    if duplicate == "abbr":
        abbr2 = "abbr1"
    ds = Datasette(
        config={
            "plugins": {
                "datasette-register-permissions": {
                    "permissions": [
                        {
                            "name": name1,
                            "abbr": abbr1,
                            "description": None,
                            "takes_database": False,
                            "takes_resource": False,
                            "default": True,
                        },
                        {
                            "name": name2,
                            "abbr": abbr2,
                            "description": None,
                            "takes_database": False,
                            "takes_resource": False,
                            "default": True,
                        },
                    ]
                }
            }
        },
        plugins_dir=PLUGINS_DIR,
    )
    # This should error:
    with pytest.raises(StartupError) as ex:
        await ds.invoke_startup()
        assert "Duplicate permission {}".format(duplicate) in str(ex.value)


@pytest.mark.asyncio
async def test_hook_register_permissions_allows_identical_duplicates():
    ds = Datasette(
        config={
            "plugins": {
                "datasette-register-permissions": {
                    "permissions": [
                        {
                            "name": "name1",
                            "abbr": "abbr1",
                            "description": None,
                            "takes_database": False,
                            "takes_resource": False,
                            "default": True,
                        },
                        {
                            "name": "name1",
                            "abbr": "abbr1",
                            "description": None,
                            "takes_database": False,
                            "takes_resource": False,
                            "default": True,
                        },
                    ]
                }
            }
        },
        plugins_dir=PLUGINS_DIR,
    )
    await ds.invoke_startup()
    # Check that ds.permissions has only one of each
    assert len([p for p in ds.permissions.values() if p.abbr == "abbr1"]) == 1


@pytest.mark.asyncio
async def test_hook_actors_from_ids():
    # Without the hook should return default {"id": id} list
    ds = Datasette()
    await ds.invoke_startup()
    db = ds.add_memory_database("actors_from_ids")
    await db.execute_write(
        "create table actors (id text primary key, name text, age int)"
    )
    await db.execute_write(
        "insert into actors (id, name, age) values ('3', 'Cate Blanchett', 52)"
    )
    await db.execute_write(
        "insert into actors (id, name, age) values ('5', 'Rooney Mara', 36)"
    )
    await db.execute_write(
        "insert into actors (id, name, age) values ('7', 'Sarah Paulson', 46)"
    )
    await db.execute_write(
        "insert into actors (id, name, age) values ('9', 'Helena Bonham Carter', 55)"
    )
    table_names = await db.table_names()
    assert table_names == ["actors"]
    actors1 = await ds.actors_from_ids(["3", "5", "7"])
    assert actors1 == {
        "3": {"id": "3"},
        "5": {"id": "5"},
        "7": {"id": "7"},
    }

    class ActorsFromIdsPlugin:
        __name__ = "ActorsFromIdsPlugin"

        @hookimpl
        def actors_from_ids(self, datasette, actor_ids):
            db = datasette.get_database("actors_from_ids")

            async def inner():
                sql = "select id, name from actors where id in ({})".format(
                    ", ".join("?" for _ in actor_ids)
                )
                actors = {}
                result = await db.execute(sql, actor_ids)
                for row in result.rows:
                    actor = dict(row)
                    actors[actor["id"]] = actor
                return actors

            return inner

    try:
        pm.register(ActorsFromIdsPlugin(), name="ActorsFromIdsPlugin")
        actors2 = await ds.actors_from_ids(["3", "5", "7"])
        assert actors2 == {
            "3": {"id": "3", "name": "Cate Blanchett"},
            "5": {"id": "5", "name": "Rooney Mara"},
            "7": {"id": "7", "name": "Sarah Paulson"},
        }
    finally:
        pm.unregister(name="ReturnNothingPlugin")


@pytest.mark.asyncio
async def test_plugin_is_installed():
    datasette = Datasette(memory=True)

    class DummyPlugin:
        __name__ = "DummyPlugin"

        @hookimpl
        def actors_from_ids(self, datasette, actor_ids):
            return {}

    try:
        pm.register(DummyPlugin(), name="DummyPlugin")
        response = await datasette.client.get("/-/plugins.json")
        assert response.status_code == 200
        installed_plugins = {p["name"] for p in response.json()}
        assert "DummyPlugin" in installed_plugins

    finally:
        pm.unregister(name="DummyPlugin")


@pytest.mark.asyncio
async def test_hook_jinja2_environment_from_request(tmpdir):
    templates = pathlib.Path(tmpdir / "templates")
    templates.mkdir()
    (templates / "index.html").write_text("Hello museums!", "utf-8")

    class EnvironmentPlugin:
        @hookimpl
        def jinja2_environment_from_request(self, request, env):
            if request and request.host == "www.niche-museums.com":
                return env.overlay(
                    loader=ChoiceLoader(
                        [
                            FileSystemLoader(str(templates)),
                            env.loader,
                        ]
                    ),
                    enable_async=True,
                )
            return env

    datasette = Datasette(memory=True)

    try:
        pm.register(EnvironmentPlugin(), name="EnvironmentPlugin")
        response = await datasette.client.get("/")
        assert response.status_code == 200
        assert "Hello museums!" not in response.text
        # Try again with the hostname
        response2 = await datasette.client.get(
            "/", headers={"host": "www.niche-museums.com"}
        )
        assert response2.status_code == 200
        assert "Hello museums!" in response2.text
    finally:
        pm.unregister(name="EnvironmentPlugin")


class SlotPlugin:
    __name__ = "SlotPlugin"

    @hookimpl
    def top_homepage(self, request):
        return "Xtop_homepage:" + request.args["z"]

    @hookimpl
    def top_database(self, request, database):
        async def inner():
            return "Xtop_database:{}:{}".format(database, request.args["z"])

        return inner

    @hookimpl
    def top_table(self, request, database, table):
        return "Xtop_table:{}:{}:{}".format(database, table, request.args["z"])

    @hookimpl
    def top_row(self, request, database, table, row):
        return "Xtop_row:{}:{}:{}:{}".format(
            database, table, row["name"], request.args["z"]
        )

    @hookimpl
    def top_query(self, request, database, sql):
        return "Xtop_query:{}:{}:{}".format(database, sql, request.args["z"])

    @hookimpl
    def top_canned_query(self, request, database, query_name):
        return "Xtop_query:{}:{}:{}".format(database, query_name, request.args["z"])


@pytest.mark.asyncio
async def test_hook_top_homepage():
    try:
        pm.register(SlotPlugin(), name="SlotPlugin")
        datasette = Datasette(memory=True)
        response = await datasette.client.get("/?z=foo")
        assert response.status_code == 200
        assert "Xtop_homepage:foo" in response.text
    finally:
        pm.unregister(name="SlotPlugin")


@pytest.mark.asyncio
async def test_hook_top_database():
    try:
        pm.register(SlotPlugin(), name="SlotPlugin")
        datasette = Datasette(memory=True)
        response = await datasette.client.get("/_memory?z=bar")
        assert response.status_code == 200
        assert "Xtop_database:_memory:bar" in response.text
    finally:
        pm.unregister(name="SlotPlugin")


@pytest.mark.asyncio
async def test_hook_top_table(ds_client):
    try:
        pm.register(SlotPlugin(), name="SlotPlugin")
        response = await ds_client.get("/fixtures/facetable?z=baz")
        assert response.status_code == 200
        assert "Xtop_table:fixtures:facetable:baz" in response.text
    finally:
        pm.unregister(name="SlotPlugin")


@pytest.mark.asyncio
async def test_hook_top_row(ds_client):
    try:
        pm.register(SlotPlugin(), name="SlotPlugin")
        response = await ds_client.get("/fixtures/facet_cities/1?z=bax")
        assert response.status_code == 200
        assert "Xtop_row:fixtures:facet_cities:San Francisco:bax" in response.text
    finally:
        pm.unregister(name="SlotPlugin")


@pytest.mark.asyncio
async def test_hook_top_query(ds_client):
    try:
        pm.register(SlotPlugin(), name="SlotPlugin")
        response = await ds_client.get("/fixtures?sql=select+1&z=x")
        assert response.status_code == 200
        assert "Xtop_query:fixtures:select 1:x" in response.text
    finally:
        pm.unregister(name="SlotPlugin")


@pytest.mark.asyncio
async def test_hook_top_canned_query(ds_client):
    try:
        pm.register(SlotPlugin(), name="SlotPlugin")
        response = await ds_client.get("/fixtures/from_hook?z=xyz")
        assert response.status_code == 200
        assert "Xtop_query:fixtures:from_hook:xyz" in response.text
    finally:
        pm.unregister(name="SlotPlugin")


@pytest.mark.asyncio
async def test_hook_track_event():
    datasette = Datasette(memory=True)
    from .conftest import TrackEventPlugin

    await datasette.invoke_startup()
    await datasette.track_event(
        TrackEventPlugin.OneEvent(actor=None, extra="extra extra")
    )
    assert len(datasette._tracked_events) == 1
    assert isinstance(datasette._tracked_events[0], TrackEventPlugin.OneEvent)
    event = datasette._tracked_events[0]
    assert event.name == "one"
    assert event.properties() == {"extra": "extra extra"}
    # Should have a recent created as well
    created = event.created
    assert isinstance(created, datetime.datetime)
    assert created.tzinfo == datetime.timezone.utc


@pytest.mark.asyncio
async def test_hook_register_events():
    datasette = Datasette(memory=True)
    await datasette.invoke_startup()
    assert any(k.__name__ == "OneEvent" for k in datasette.event_classes)


@pytest.mark.parametrize(
    "metadata,config,expected_metadata,expected_config",
    (
        (
            # Instance level
            {"plugins": {"datasette-foo": "bar"}},
            {},
            {},
            {"plugins": {"datasette-foo": "bar"}},
        ),
        (
            # Database level
            {"databases": {"foo": {"plugins": {"datasette-foo": "bar"}}}},
            {},
            {},
            {"databases": {"foo": {"plugins": {"datasette-foo": "bar"}}}},
        ),
        (
            # Table level
            {
                "databases": {
                    "foo": {"tables": {"bar": {"plugins": {"datasette-foo": "bar"}}}}
                }
            },
            {},
            {},
            {
                "databases": {
                    "foo": {"tables": {"bar": {"plugins": {"datasette-foo": "bar"}}}}
                }
            },
        ),
        (
            # Keep other keys
            {"plugins": {"datasette-foo": "bar"}, "other": "key"},
            {"original_config": "original"},
            {"other": "key"},
            {"original_config": "original", "plugins": {"datasette-foo": "bar"}},
        ),
    ),
)
def test_metadata_plugin_config_treated_as_config(
    metadata, config, expected_metadata, expected_config
):
    ds = Datasette(metadata=metadata, config=config)
    actual_metadata = ds.metadata()
    assert "plugins" not in actual_metadata
    assert actual_metadata == expected_metadata
    assert ds.config == expected_config
