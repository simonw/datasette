"""
Tests to ensure certain things are documented.
"""

from datasette import app, utils
import datasette.fixtures  # noqa: F401
from datasette.app import Datasette
from datasette.filters import Filters
from pathlib import Path
import pytest
import re

docs_path = Path(__file__).parent.parent / "docs"
label_re = re.compile(r"\.\. _([^\s:]+):")


def get_headings(content, underline="-"):
    heading_re = re.compile(r"(\w+)(\([^)]*\))?\n\{}+\n".format(underline))
    return {h[0] for h in heading_re.findall(content)}


def get_labels(filename):
    content = (docs_path / filename).read_text()
    return set(label_re.findall(content))


@pytest.fixture(scope="session")
def settings_headings():
    return get_headings((docs_path / "settings.rst").read_text(), "~")


def test_settings_are_documented(settings_headings, subtests):
    for setting in app.SETTINGS:
        with subtests.test(setting=setting.name):
            assert setting.name in settings_headings


@pytest.fixture(scope="session")
def plugin_hooks_content():
    return (docs_path / "plugin_hooks.rst").read_text()


def test_plugin_hooks_are_documented(plugin_hooks_content, subtests):
    headings = set()
    headings.update(get_headings(plugin_hooks_content, "-"))
    headings.update(get_headings(plugin_hooks_content, "~"))
    plugins = [name for name in dir(app.pm.hook) if not name.startswith("_")]
    for plugin in plugins:
        with subtests.test(plugin=plugin):
            assert plugin in headings
            hook_caller = getattr(app.pm.hook, plugin)
            arg_names = [a for a in hook_caller.spec.argnames if a != "__multicall__"]
            # Check for plugin_name(arg1, arg2, arg3)
            expected = f"{plugin}({', '.join(arg_names)})"
            assert (
                expected in plugin_hooks_content
            ), f"Missing from plugin hook documentation: {expected}"


@pytest.fixture(scope="session")
def documented_views():
    view_labels = set()
    for filename in docs_path.glob("*.rst"):
        for label in get_labels(filename):
            first_word = label.split("_")[0]
            if first_word.endswith("View"):
                view_labels.add(first_word)
    # We deliberately don't document these:
    view_labels.update(
        (
            "PatternPortfolioView",
            "AuthTokenView",
            "ApiExplorerView",
            "ExecuteWriteAnalyzeView",
            "ExecuteWriteView",
            "GlobalQueryListView",
            "QueryCreateAnalyzeView",
            "QueryDeleteView",
            "QueryDefinitionView",
            "QueryEditView",
            "QueryListView",
            "QueryParametersView",
            "QueryStoreView",
            "QueryUpdateView",
        )
    )
    return view_labels


def test_view_classes_are_documented(documented_views, subtests):
    view_classes = [v for v in dir(app) if v.endswith("View")]
    for view_class in view_classes:
        with subtests.test(view_class=view_class):
            assert view_class in documented_views


@pytest.fixture(scope="session")
def documented_table_filters():
    json_api_rst = (docs_path / "json_api.rst").read_text()
    section = json_api_rst.split(".. _table_arguments:")[-1]
    # Lines starting with ``?column__exact= are docs for filters
    return {
        line.split("__")[1].split("=")[0]
        for line in section.split("\n")
        if line.startswith("``?column__")
    }


def test_table_filters_are_documented(documented_table_filters, subtests):
    for f in Filters._filters:
        with subtests.test(filter=f.key):
            assert f.key in documented_table_filters


def test_table_extra_examples_are_documented():
    from datasette.views.table_extras import CountExtra

    assert CountExtra.example.path == "/fixtures/facetable.json?_extra=count"
    content = (docs_path / "json_api.rst").read_text()
    section = content.split(".. _json_api_extra:")[-1].split(".. _table_arguments:")[0]
    assert "GET /fixtures/facetable.json?_extra=count" in section
    assert ".. code-block:: json" in section


def test_render_cell_extra_example_explains_row_and_column_mapping():
    content = (docs_path / "json_api.rst").read_text()
    section = content.split("``render_cell``")[-1].split("``query``")[0]
    assert "same order as the ``rows`` array" in section
    assert '"rows": [' in section
    assert '"render_cell": [' in section


def test_debug_and_request_extra_examples_are_documented():
    content = (docs_path / "json_api.rst").read_text()
    section = content.split("Table JSON responses")[-1].split("Row JSON responses")[0]

    debug_section = section.split("``debug``")[-1].split("``request``")[0]
    assert "GET /fixtures/facetable.json?_extra=debug" in debug_section
    assert '"url_vars": {' in debug_section

    request_section = section.split("``request``")[-1].split("``query``")[0]
    assert "GET /fixtures/facetable.json?_extra=request" in request_section
    assert '"full_path":' in request_section


def test_row_and_query_extra_sections_are_documented():
    content = (docs_path / "json_api.rst").read_text()
    assert "Row JSON responses" in content
    assert (
        "``GET /fixtures/simple_primary_key/1.json?_extra=foreign_key_tables``"
        in content
    )
    assert "Query JSON responses" in content
    assert "``GET /fixtures/-/query.json?sql=select+1+as+one&_extra=query``" in content
    assert (
        "``GET /fixtures/neighborhood_search.json?text=town&_extra=query``" in content
    )


@pytest.fixture(scope="session")
def documented_labels():
    labels = set()
    for filename in docs_path.glob("*.rst"):
        labels.update(get_labels(filename.name))
    return labels


def test_functions_marked_with_documented_are_documented(documented_labels, subtests):
    for fn in utils.functions_marked_as_documented:
        with subtests.test(fn=fn.__name__):
            assert fn._datasette_docs_label in documented_labels


def test_display_actor_logic_is_documented():
    # The keys used by datasette.utils.display_actor should be reflected
    # in the authentication documentation - see issue #2002
    content = (docs_path / "authentication.rst").read_text()
    section = content.split(".. _authentication_actor_display:")[-1].split(
        ".. _authentication_root:"
    )[0]
    for key in utils.display_actor.__code__.co_consts:
        if isinstance(key, tuple):
            display_keys = key
            break
    else:
        raise AssertionError("Could not find display_actor key tuple")
    assert display_keys == ("display", "name", "username", "login", "id")
    for key in display_keys:
        assert f"``{key}``" in section, f"{key} not documented in actor display section"


def test_rst_heading_underlines_match_title_length():
    """Test that RST heading underlines are the same length as their titles."""
    # Common RST underline characters
    underline_chars = ["-", "=", "~", "^", "+", "*", "#"]

    errors = []

    for rst_file in docs_path.glob("*.rst"):
        content = rst_file.read_text()
        lines = content.split("\n")

        for i in range(len(lines) - 1):
            current_line = lines[i]
            next_line = lines[i + 1]

            # Check if next line is entirely made of a single underline character
            # and is at least 5 characters long (to avoid false positives)
            if (
                next_line
                and len(next_line) >= 5
                and len(set(next_line)) == 1
                and next_line[0] in underline_chars
            ):
                # Skip if the previous line is empty (blank line before underline)
                if not current_line:
                    continue

                # Check if this is an overline+underline style heading
                # Look at the line before current_line to see if it's also an underline
                if i > 0:
                    prev_line = lines[i - 1]
                    if (
                        prev_line
                        and len(prev_line) >= 5
                        and len(set(prev_line)) == 1
                        and prev_line[0] in underline_chars
                        and len(prev_line) == len(next_line)
                    ):
                        # This is overline+underline style, skip it
                        continue

                # This is a heading underline
                title_length = len(current_line)
                underline_length = len(next_line)

                if title_length != underline_length:
                    errors.append(
                        f"{rst_file.name}:{i+1}: Title length {title_length} != underline length {underline_length}\n"
                        f"  Title: {current_line!r}\n"
                        f"  Underline: {next_line!r}"
                    )

    if errors:
        raise AssertionError(
            f"Found {len(errors)} RST heading(s) with mismatched underline length:\n\n"
            + "\n\n".join(errors)
        )


# Tests for testing_plugins.rst documentation

# fmt: off
# -- start test_homepage --
@pytest.mark.asyncio
async def test_homepage():
    ds = Datasette(memory=True)
    response = await ds.client.get("/")
    html = response.text
    assert "<h1>" in html
# -- end test_homepage --


# -- start test_actor_is_null --
@pytest.mark.asyncio
async def test_actor_is_null():
    ds = Datasette(memory=True)
    response = await ds.client.get("/-/actor.json")
    assert response.json() == {"actor": None}
# -- end test_actor_is_null --


# -- start test_signed_cookie_actor --
@pytest.mark.asyncio
async def test_signed_cookie_actor():
    ds = Datasette(memory=True)
    cookies = {"ds_actor": ds.client.actor_cookie({"id": "root"})}
    response = await ds.client.get("/-/actor.json", cookies=cookies)
    assert response.json() == {"actor": {"id": "root"}}
# -- end test_signed_cookie_actor --
