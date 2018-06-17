from bs4 import BeautifulSoup as Soup
from .fixtures import ( # noqa
    app_client,
)
import pytest


def test_plugins_dir_plugin(app_client):
    response = app_client.get(
        "/fixtures.json?sql=select+convert_units(100%2C+'m'%2C+'ft')"
    )
    assert pytest.approx(328.0839) == response.json['rows'][0][0]


def test_plugin_extra_css_urls(app_client):
    response = app_client.get('/')
    links = Soup(response.body, 'html.parser').findAll('link')
    assert [
        l for l in links
        if l.attrs == {
            'rel': ['stylesheet'],
            'href': 'https://example.com/app.css'
        }
    ]


def test_plugin_extra_js_urls(app_client):
    response = app_client.get('/')
    scripts = Soup(response.body, 'html.parser').findAll('script')
    assert [
        s for s in scripts
        if s.attrs == {
            'integrity': 'SRIHASH',
            'crossorigin': 'anonymous',
            'src': 'https://example.com/jquery.js'
        }
    ]


def test_plugins_with_duplicate_js_urls(app_client):
    # If two plugins both require jQuery, jQuery should be loaded only once
    response = app_client.get(
        "/fixtures"
    )
    # This test is a little tricky, as if the user has any other plugins in
    # their current virtual environment those may affect what comes back too.
    # What matters is that https://example.com/jquery.js is only there once
    # and it comes before plugin1.js and plugin2.js which could be in either
    # order
    scripts = Soup(response.body, 'html.parser').findAll('script')
    srcs = [s['src'] for s in scripts if s.get('src')]
    # No duplicates allowed:
    assert len(srcs) == len(set(srcs))
    # jquery.js loaded once:
    assert 1 == srcs.count('https://example.com/jquery.js')
    # plugin1.js and plugin2.js are both there:
    assert 1 == srcs.count('https://example.com/plugin1.js')
    assert 1 == srcs.count('https://example.com/plugin2.js')
    # jquery comes before them both
    assert srcs.index(
        'https://example.com/jquery.js'
    ) < srcs.index(
        'https://example.com/plugin1.js'
    )
    assert srcs.index(
        'https://example.com/jquery.js'
    ) < srcs.index(
        'https://example.com/plugin2.js'
    )
