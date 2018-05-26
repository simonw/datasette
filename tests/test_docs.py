"""
Tests to ensure certain things are documented.
"""
from datasette import app
from pathlib import Path
import pytest
import re

markdown = (Path(__file__).parent.parent / 'docs' / 'config.rst').open().read()
setting_heading_re = re.compile(r'(\w+)\n\-+\n')
setting_headings = set(setting_heading_re.findall(markdown))


@pytest.mark.parametrize('config', app.CONFIG_OPTIONS)
def test_config_options_are_documented(config):
    assert config.name in setting_headings
