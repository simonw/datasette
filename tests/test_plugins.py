from bs4 import BeautifulSoup as Soup
from .fixtures import app_client, make_app_client, TEMP_PLUGIN_SECRET_FILE  # noqa
import base64
import json
import os
import pathlib
import re
import pytest
import urllib


def test_plugins_dir_plugin(app_client):
    response = app_client.get(
        "/fixtures.json?sql=select+convert_units(100%2C+'m'%2C+'ft')"
    )
    assert pytest.approx(328.0839) == response.json["rows"][0][0]


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
            "src": "https://example.com/jquery.js",
        }
    ]


def test_plugins_with_duplicate_js_urls(app_client):
    # If two plugins both require jQuery, jQuery should be loaded only once
    response = app_client.get("/fixtures")
    # This test is a little tricky, as if the user has any other plugins in
    # their current virtual environment those may affect what comes back too.
    # What matters is that https://example.com/jquery.js is only there once
    # and it comes before plugin1.js and plugin2.js which could be in either
    # order
    scripts = Soup(response.body, "html.parser").findAll("script")
    srcs = [s["src"] for s in scripts if s.get("src")]
    # No duplicates allowed:
    assert len(srcs) == len(set(srcs))
    # jquery.js loaded once:
    assert 1 == srcs.count("https://example.com/jquery.js")
    # plugin1.js and plugin2.js are both there:
    assert 1 == srcs.count("https://example.com/plugin1.js")
    assert 1 == srcs.count("https://example.com/plugin2.js")
    # jquery comes before them both
    assert srcs.index("https://example.com/jquery.js") < srcs.index(
        "https://example.com/plugin1.js"
    )
    assert srcs.index("https://example.com/jquery.js") < srcs.index(
        "https://example.com/plugin2.js"
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
    json_data = r.search(app_client.get(path).body.decode("utf8")).group(1)
    actual_data = json.loads(json_data)
    assert expected_extra_body_script == actual_data


def test_plugins_asgi_wrapper(app_client):
    response = app_client.get("/fixtures")
    assert "fixtures" == response.headers["x-databases"]


def test_plugins_extra_template_vars(restore_working_directory):
    for client in make_app_client(
        template_dir=str(pathlib.Path(__file__).parent / "test_templates")
    ):
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
