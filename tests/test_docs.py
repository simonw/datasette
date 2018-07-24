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
markdown = (docs_path / 'config.rst').open().read()
setting_heading_re = re.compile(r'(\w+)\n\-+\n')
setting_headings = set(setting_heading_re.findall(markdown))


@pytest.mark.parametrize('config', app.CONFIG_OPTIONS)
def test_config_options_are_documented(config):
    assert config.name in setting_headings


@pytest.mark.parametrize('name,filename', (
    ('serve', 'datasette-serve-help.txt'),
    ('package', 'datasette-package-help.txt'),
    ('publish', 'datasette-publish-help.txt'),
))
def test_help_includes(name, filename):
    expected = open(docs_path / filename).read()
    runner = CliRunner()
    result = runner.invoke(cli, [name, '--help'], terminal_width=88)
    actual = '$ datasette {} --help\n\n{}'.format(
        name, result.output
    )
    # actual has "Usage: cli package [OPTIONS] FILES"
    # because it doesn't know that cli will be aliased to datasette
    expected = expected.replace('Usage: datasette', 'Usage: cli')
    assert expected == actual
