"""
Tests to ensure certain things are documented.
"""
from click.testing import CliRunner
from datasette import app
from datasette.cli import cli
from pathlib import Path
import pytest
import re

docs_path = Path(__file__).parent.parent / 'docs'


def get_headings(filename, underline="-"):
    markdown = (docs_path / filename).open().read()
    heading_re = re.compile(r'(\S+)\n\{}+\n'.format(underline))
    return set(heading_re.findall(markdown))


@pytest.mark.parametrize('config', app.CONFIG_OPTIONS)
def test_config_options_are_documented(config):
    assert config.name in get_headings("config.rst")


@pytest.mark.parametrize("name,filename", (
    ("serve", "datasette-serve-help.txt"),
    ("package", "datasette-package-help.txt"),
    ("publish now", "datasette-publish-now-help.txt"),
    ("publish heroku", "datasette-publish-heroku-help.txt"),
))
def test_help_includes(name, filename):
    expected = open(str(docs_path / filename)).read()
    runner = CliRunner()
    result = runner.invoke(cli, name.split() + ["--help"], terminal_width=88)
    actual = "$ datasette {} --help\n\n{}".format(
        name, result.output
    )
    # actual has "Usage: cli package [OPTIONS] FILES"
    # because it doesn't know that cli will be aliased to datasette
    expected = expected.replace("Usage: datasette", "Usage: cli")
    assert expected == actual


@pytest.mark.parametrize('plugin', [
    name for name in dir(app.pm.hook) if not name.startswith('_')
])
def test_plugin_hooks_are_documented(plugin):
    headings = [
        s.split("(")[0] for s in get_headings("plugins.rst", "~")
    ]
    assert plugin in headings
