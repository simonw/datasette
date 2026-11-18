"""
Tests for the ?_context=1 template debug page
"""

import sqlite3
from dataclasses import dataclass, field

import pytest
from bs4 import BeautifulSoup as Soup

from datasette.app import TEMPLATE_BASE_CONTEXT
from datasette.context_debug import (
    MAX_VALUE_LENGTH,
    build_context_debug,
    describe_variable,
    format_value,
    rst_to_html,
)
from datasette.views import Context

from .fixtures import make_ds


@pytest.mark.parametrize(
    "rst,expected",
    (
        ("Plain text", "Plain text"),
        ("The ``name`` key", "The <code>name</code> key"),
        ("See :ref:`the docs <some_label>`.", "See the docs."),
        (
            "See :ref:`plugin_hook_menu_links`",
            "See <code>menu_links()</code> plugin hook",
        ),
        ("See :ref:`json_api_pagination`", "See json api pagination documentation"),
        ("<script> & ``<b>``", "&lt;script&gt; &amp; <code>&lt;b&gt;</code>"),
        ("", ""),
        (None, ""),
    ),
)
def test_rst_to_html(rst, expected):
    assert str(rst_to_html(rst)) == expected


def test_format_value():
    @dataclass
    class Nested:
        name: str

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute("select 1 as id, 'one' as name").fetchone()
    assert format_value([1, 2]) == "[1, 2]"
    assert format_value(Nested(name="x")) == '{"name": "x"}'
    assert format_value(row) == '{"id": 1, "name": "one"}'
    assert format_value({"a": {1}}) == '{"a": [1]}'
    # Objects that only hold data show their public attributes

    class Holder:
        def __init__(self):
            self.cells = [1]
            self._hidden = 2

    class NotJustData:
        def __init__(self):
            self.holder = Holder()

    assert format_value([Holder()]) == '[{"cells": [1]}]'
    assert "NotJustData object at" in format_value([NotJustData()])
    # Values too long for one line are indented
    assert format_value({"key": "x" * 200}).startswith('{\n  "key": ')
    # Values that cannot be serialized fall back to their repr
    assert format_value({(1, 2): 3}) == "{(1, 2): 3}"


def test_describe_variable_value():
    variable = describe_variable(
        "table", "facetable", help="The ``table`` name", type_name="str"
    )
    assert variable["kind"] == "value"
    assert variable["snippet"] == "{{ table }}"
    assert variable["value"] == '"facetable"'
    assert variable["actual_type"] == "str"
    assert variable["documented_type"] == "str"
    assert str(variable["help"]) == "The <code>table</code> name"
    assert not variable["collapsed"]
    assert not variable["truncated"]


def test_describe_variable_long_values_are_collapsed_and_truncated():
    variable = describe_variable("rows", [{"id": i} for i in range(100)])
    assert variable["collapsed"]
    assert not variable["truncated"]
    huge = describe_variable("rows", ["x" * 100] * 1000)
    assert huge["truncated"]
    assert len(huge["value"]) == MAX_VALUE_LENGTH
    assert huge["value_length"] > MAX_VALUE_LENGTH


def test_describe_variable_function():
    async def actions(database, table=None):
        "Return the actions"
        return []

    variable = describe_variable("actions", actions)
    assert variable["kind"] == "function"
    assert variable["is_async"]
    assert variable["signature"] == "(database, table=None)"
    assert variable["doc"] == "Return the actions"
    assert variable["snippet"] == "{{ actions() }}"
    # A lambda has no docstring, and builtins such as zip() still work
    assert describe_variable("show", lambda: 1)["doc"] == ""
    assert describe_variable("zip", zip)["kind"] == "function"


def test_describe_variable_object_lists_its_members():
    class Helper:
        "Helps with things"

        def __init__(self):
            self.secret = "not listed"

        def path(self, path, format=None):
            "Return the ``path``"

        async def fetch(self):
            pass

        @property
        def name(self):
            "The name"

        def _private(self):
            pass

    variable = describe_variable("helper", Helper())
    assert variable["kind"] == "object"
    assert variable["doc"] == "Helps with things"
    assert variable["members"] == [
        {"name": "fetch", "signature": "()", "is_async": True, "doc": ""},
        {"name": "name", "signature": "", "is_async": False, "doc": "The name"},
        {
            "name": "path",
            "signature": "(path, format=None)",
            "is_async": False,
            "doc": "Return the <code>path</code>",
        },
    ]


def test_build_context_debug_sections():
    @dataclass
    class DemoContext(Context):
        name: str = field(metadata={"help": "The name"})

    sections = build_context_debug(
        {
            "name": "demo",
            "select_templates": ["*demo.html"],
            "from_plugin": 1,
            "base_url": "/",
        },
        context_class=DemoContext,
        plugin_keys={"from_plugin"},
        base_docs={"base_url": "The ``base_url`` setting"},
    )
    assert [
        (section["id"], [v["name"] for v in section["variables"]])
        for section in sections
    ] == [
        ("page", ["name"]),
        ("undocumented", ["select_templates"]),
        ("plugins", ["from_plugin"]),
        ("base", ["base_url"]),
    ]
    assert str(sections[0]["variables"][0]["help"]) == "The name"
    assert "DemoContext" in sections[0]["intro"]
    assert (
        str(sections[3]["variables"][0]["help"]) == "The <code>base_url</code> setting"
    )


def test_build_context_debug_plugins_can_replace_documented_variables():
    @dataclass
    class DemoContext(Context):
        name: str = field(metadata={"help": "The name"})

    sections = build_context_debug(
        {"name": "replaced"},
        context_class=DemoContext,
        plugin_keys={"name"},
        base_docs={},
    )
    assert [section["id"] for section in sections] == ["plugins"]


@pytest.fixture
async def debug_ds():
    return await make_ds(settings={"template_debug": True})


def _variables(soup, section_id):
    section = soup.select_one(f"section#context-{section_id}")
    if section is None:
        return {}
    return {dt["id"].removeprefix("var-"): dt for dt in section.select("dl > dt")}


async def test_context_debug_page_for_documented_page(debug_ds):
    response = await debug_ds.client.get("/fixtures/facetable?_size=2&_context=1")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    assert soup.select_one("h1").text == "Template context"
    summary = soup.select_one(".context-debug-summary").text
    assert "table.html" in summary
    assert "TableContext" in summary
    links = {a.text: a["href"] for a in soup.select(".context-debug-summary a")}
    assert links == {
        "View the page": "/fixtures/facetable?_size=2",
        "View this context as JSON": "/fixtures/facetable?_size=2&_context_json=1",
    }

    page = _variables(soup, "page")
    assert {"table", "rows", "actions", "display_rows"} <= set(page)
    table = page["table"]
    assert table.select_one(".context-debug-snippet code").text == "{{ table }}"
    copy_button = table.select_one("datasette-copy-button")
    assert copy_button["value"] == "{{ table }}"
    table_dd = table.find_next_sibling("dd")
    assert table_dd.select_one("pre").text == '"facetable"'
    # The description comes from the Context class
    assert table_dd.select_one("p").text == "Table name"
    # Functions show their signature rather than a value
    assert "async function" in page["actions"].text
    assert page["actions"].find_next_sibling("dd").select_one("pre").text == (
        "async actions()"
    )

    base = _variables(soup, "base")
    plugins = _variables(soup, "plugins")
    # Plugins in the test suite add extra_template_vars and replace request
    assert "extra_template_vars" in plugins
    assert set(base) == set(TEMPLATE_BASE_CONTEXT) - set(plugins)
    # urls is an object, so its methods and their docstrings are listed
    urls_dd = base["urls"].find_next_sibling("dd")
    members = urls_dd.select_one(".context-debug-members").text
    assert "urls.table(" in members
    assert "Return a modified copy of a URL" in members

    # Every variable is in exactly one section
    assert not set(page) & set(base)


async def test_context_debug_page_for_undocumented_page(debug_ds):
    response = await debug_ds.client.get("/-/messages?_context=1")
    assert response.status_code == 200
    soup = Soup(response.text, "html.parser")
    assert "plain dictionary" in soup.select_one(".context-debug-summary").text
    assert not _variables(soup, "page")
    assert "select_templates" in _variables(soup, "undocumented")
    assert set(_variables(soup, "base")) == set(TEMPLATE_BASE_CONTEXT) - set(
        _variables(soup, "plugins")
    )


async def test_context_debug_page_does_not_consume_messages(debug_ds):
    cookies = {"ds_actor": debug_ds.client.actor_cookie({"id": "root"})}
    response = await debug_ds.client.post(
        "/-/messages",
        data={"message": "Hello there", "message_type": "INFO"},
        cookies=cookies,
    )
    cookies["ds_messages"] = response.cookies["ds_messages"]
    debug_response = await debug_ds.client.get("/?_context=1", cookies=cookies)
    assert "Hello there" not in debug_response.text
    page_response = await debug_ds.client.get("/", cookies=cookies)
    assert "Hello there" in page_response.text


async def test_context_debug_page_escapes_values(debug_ds):
    response = await debug_ds.client.get(
        "/fixtures/-/query?sql=select+'<script>alert(1)</script>'&_context=1"
    )
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
