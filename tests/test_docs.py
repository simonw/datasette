"""
Tests to ensure certain things are documented.
"""
from click.testing import CliRunner
from datasette import app
from datasette.cli import cli
from datasette.filters import Filters
from pathlib import Path
import pytest
import re

docs_path = Path(__file__).parent.parent / "docs"
label_re = re.compile(r"\.\. _([^\s:]+):")


def get_headings(filename, underline="-"):
    content = (docs_path / filename).open().read()
    heading_re = re.compile(r"(\w+)(\([^)]*\))?\n\{}+\n".format(underline))
    return set(h[0] for h in heading_re.findall(content))


def get_labels(filename):
    content = (docs_path / filename).open().read()
    return set(label_re.findall(content))


@pytest.mark.parametrize("config", app.CONFIG_OPTIONS)
def test_config_options_are_documented(config):
    assert config.name in get_headings("config.rst", "~")


@pytest.mark.parametrize(
    "name,filename",
    (
        ("serve", "datasette-serve-help.txt"),
        ("package", "datasette-package-help.txt"),
        ("publish heroku", "datasette-publish-heroku-help.txt"),
        ("publish cloudrun", "datasette-publish-cloudrun-help.txt"),
    ),
)
def test_help_includes(name, filename):
    expected = open(str(docs_path / filename)).read()
    runner = CliRunner()
    result = runner.invoke(cli, name.split() + ["--help"], terminal_width=88)
    actual = "$ datasette {} --help\n\n{}".format(name, result.output)
    # actual has "Usage: cli package [OPTIONS] FILES"
    # because it doesn't know that cli will be aliased to datasette
    expected = expected.replace("Usage: datasette", "Usage: cli")
    assert expected == actual


@pytest.mark.parametrize(
    "plugin", [name for name in dir(app.pm.hook) if not name.startswith("_")]
)
def test_plugin_hooks_are_documented(plugin):
    headings = [s.split("(")[0] for s in get_headings("plugin_hooks.rst", "-")]
    assert plugin in headings


@pytest.fixture(scope="session")
def documented_views():
    view_labels = set()
    for filename in docs_path.glob("*.rst"):
        for label in get_labels(filename):
            first_word = label.split("_")[0]
            if first_word.endswith("View"):
                view_labels.add(first_word)
    # We deliberately don't document these:
    view_labels.update(("PatternPortfolioView", "AuthTokenView"))
    return view_labels


@pytest.mark.parametrize("view_class", [v for v in dir(app) if v.endswith("View")])
def test_view_classes_are_documented(documented_views, view_class):
    assert view_class in documented_views


@pytest.fixture(scope="session")
def documented_table_filters():
    json_api_rst = (docs_path / "json_api.rst").read_text()
    section = json_api_rst.split(".. _table_arguments:")[-1]
    # Lines starting with ``?column__exact= are docs for filters
    return set(
        line.split("__")[1].split("=")[0]
        for line in section.split("\n")
        if line.startswith("``?column__")
    )


@pytest.mark.parametrize("filter", [f.key for f in Filters._filters])
def test_table_filters_are_documented(documented_table_filters, filter):
    assert filter in documented_table_filters
