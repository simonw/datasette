from bs4 import BeautifulSoup as Soup
from .fixtures import app_client
import pytest
import re
import urllib.parse

pytest.fixture(scope='module')(app_client)


def test_homepage(app_client):
    response = app_client.get('/', gather_request=False)
    assert response.status == 200
    assert 'test_tables' in response.text


def test_database_page(app_client):
    response = app_client.get('/test_tables', allow_redirects=False, gather_request=False)
    assert response.status == 302
    response = app_client.get('/test_tables', gather_request=False)
    assert 'test_tables' in response.text


def test_invalid_custom_sql(app_client):
    response = app_client.get(
        '/test_tables?sql=.schema',
        gather_request=False
    )
    assert response.status == 400
    assert 'Statement must be a SELECT' in response.text


def test_view(app_client):
    response = app_client.get('/test_tables/simple_view', gather_request=False)
    assert response.status == 200


def test_row(app_client):
    response = app_client.get(
        '/test_tables/simple_primary_key/1',
        allow_redirects=False,
        gather_request=False
    )
    assert response.status == 302
    assert response.headers['Location'].endswith('/1')
    response = app_client.get('/test_tables/simple_primary_key/1', gather_request=False)
    assert response.status == 200


def test_add_filter_redirects(app_client):
    filter_args = urllib.parse.urlencode({
        '_filter_column': 'content',
        '_filter_op': 'startswith',
        '_filter_value': 'x'
    })
    # First we need to resolve the correct path before testing more redirects
    path_base = app_client.get(
        '/test_tables/simple_primary_key', allow_redirects=False, gather_request=False
    ).headers['Location']
    path = path_base + '?' + filter_args
    response = app_client.get(path, allow_redirects=False, gather_request=False)
    assert response.status == 302
    assert response.headers['Location'].endswith('?content__startswith=x')

    # Adding a redirect to an existing querystring:
    path = path_base + '?foo=bar&' + filter_args
    response = app_client.get(path, allow_redirects=False, gather_request=False)
    assert response.status == 302
    assert response.headers['Location'].endswith('?content__startswith=x&foo=bar')

    # Test that op with a __x suffix overrides the filter value
    path = path_base + '?' + urllib.parse.urlencode({
        '_filter_column': 'content',
        '_filter_op': 'isnull__5',
        '_filter_value': 'x'
    })
    response = app_client.get(path, allow_redirects=False, gather_request=False)
    assert response.status == 302
    assert response.headers['Location'].endswith('?content__isnull=5')


def test_existing_filter_redirects(app_client):
    filter_args = {
        '_filter_column_1': 'name',
        '_filter_op_1': 'contains',
        '_filter_value_1': 'hello',
        '_filter_column_2': 'age',
        '_filter_op_2': 'gte',
        '_filter_value_2': '22',
        '_filter_column_3': 'age',
        '_filter_op_3': 'lt',
        '_filter_value_3': '30',
        '_filter_column_4': 'name',
        '_filter_op_4': 'contains',
        '_filter_value_4': 'world',
    }
    path_base = app_client.get(
        '/test_tables/simple_primary_key', allow_redirects=False, gather_request=False
    ).headers['Location']
    path = path_base + '?' + urllib.parse.urlencode(filter_args)
    response = app_client.get(path, allow_redirects=False, gather_request=False)
    assert response.status == 302
    assert response.headers['Location'].endswith(
        '?age__gte=22&age__lt=30&name__contains=hello&name__contains=world'
    )

    # Setting _filter_column_3 to empty string should remove *_3 entirely
    filter_args['_filter_column_3'] = ''
    path = path_base + '?' + urllib.parse.urlencode(filter_args)
    response = app_client.get(path, allow_redirects=False, gather_request=False)
    assert response.status == 302
    assert response.headers['Location'].endswith(
        '?age__gte=22&name__contains=hello&name__contains=world'
    )

    # ?_filter_op=exact should be removed if unaccompanied by _fiter_column
    response = app_client.get(path_base + '?_filter_op=exact', allow_redirects=False, gather_request=False)
    assert response.status == 302
    assert '?' not in response.headers['Location']


def test_empty_search_parameter_gets_removed(app_client):
    path_base = app_client.get(
        '/test_tables/simple_primary_key', allow_redirects=False, gather_request=False
    ).headers['Location']
    path = path_base + '?' + urllib.parse.urlencode({
        '_search': '',
        '_filter_column': 'name',
        '_filter_op': 'exact',
        '_filter_value': 'chidi',
    })
    response = app_client.get(path, allow_redirects=False, gather_request=False)
    assert response.status == 302
    assert response.headers['Location'].endswith(
        '?name__exact=chidi'
    )


def test_sort_by_desc_redirects(app_client):
    path_base = app_client.get(
        '/test_tables/sortable', allow_redirects=False, gather_request=False
    ).headers['Location']
    path = path_base + '?' + urllib.parse.urlencode({
        '_sort': 'sortable',
        '_sort_by_desc': '1',
    })
    response = app_client.get(path, allow_redirects=False, gather_request=False)
    assert response.status == 302
    assert response.headers['Location'].endswith('?_sort_desc=sortable')


@pytest.mark.parametrize('path,expected_classes', [
    ('/', ['index']),
    ('/test_tables', ['db', 'db-test_tables']),
    ('/test_tables/simple_primary_key', [
        'table', 'db-test_tables', 'table-simple_primary_key'
    ]),
    ('/test_tables/table%2Fwith%2Fslashes.csv', [
        'table', 'db-test_tables', 'table-tablewithslashescsv-fa7563'
    ]),
    ('/test_tables/simple_primary_key/1', [
        'row', 'db-test_tables', 'table-simple_primary_key'
    ]),
])
def test_css_classes_on_body(app_client, path, expected_classes):
    response = app_client.get(path, gather_request=False)
    classes = re.search(r'<body class="(.*)">', response.text).group(1).split()
    assert classes == expected_classes


def test_table_html_simple_primary_key(app_client):
    response = app_client.get('/test_tables/simple_primary_key', gather_request=False)
    table = Soup(response.body, 'html.parser').find('table')
    ths = table.findAll('th')
    assert 'Link' == ths[0].string.strip()
    for expected_col, th in zip(('pk', 'content'), ths[1:]):
        a = th.find('a')
        assert expected_col == a.string
        assert a['href'].endswith('/simple_primary_key?_sort={}'.format(
            expected_col
        ))
        assert ['nofollow'] == a['rel']
    assert [
        [
            '<td><a href="/test_tables/simple_primary_key/1">1</a></td>',
            '<td>1</td>',
            '<td>hello</td>'
        ], [
            '<td><a href="/test_tables/simple_primary_key/2">2</a></td>',
            '<td>2</td>',
            '<td>world</td>'
        ], [
            '<td><a href="/test_tables/simple_primary_key/3">3</a></td>',
            '<td>3</td>',
            '<td></td>'
        ]
    ] == [[str(td) for td in tr.select('td')] for tr in table.select('tbody tr')]


def test_row_html_simple_primary_key(app_client):
    response = app_client.get('/test_tables/simple_primary_key/1', gather_request=False)
    table = Soup(response.body, 'html.parser').find('table')
    assert [
        'pk', 'content'
    ] == [th.string.strip() for th in table.select('thead th')]
    assert [
        [
            '<td>1</td>',
            '<td>hello</td>'
        ]
    ] == [[str(td) for td in tr.select('td')] for tr in table.select('tbody tr')]


def test_table_not_exists(app_client):
    assert 'Table not found: blah' in app_client.get(
        '/test_tables/blah', gather_request=False
    ).body.decode('utf8')


def test_table_html_no_primary_key(app_client):
    response = app_client.get('/test_tables/no_primary_key', gather_request=False)
    table = Soup(response.body, 'html.parser').find('table')
    # We have disabled sorting for this table using metadata.json
    assert [
        'content', 'a', 'b', 'c'
    ] == [th.string.strip() for th in table.select('thead th')[2:]]
    expected = [
        [
            '<td><a href="/test_tables/no_primary_key/{}">{}</a></td>'.format(i, i),
            '<td>{}</td>'.format(i),
            '<td>{}</td>'.format(i),
            '<td>a{}</td>'.format(i),
            '<td>b{}</td>'.format(i),
            '<td>c{}</td>'.format(i),
        ] for i in range(1, 51)
    ]
    assert expected == [[str(td) for td in tr.select('td')] for tr in table.select('tbody tr')]


def test_row_html_no_primary_key(app_client):
    response = app_client.get('/test_tables/no_primary_key/1', gather_request=False)
    table = Soup(response.body, 'html.parser').find('table')
    assert [
        'rowid', 'content', 'a', 'b', 'c'
    ] == [th.string.strip() for th in table.select('thead th')]
    expected = [
        [
            '<td>1</td>',
            '<td>1</td>',
            '<td>a1</td>',
            '<td>b1</td>',
            '<td>c1</td>',
        ]
    ]
    assert expected == [[str(td) for td in tr.select('td')] for tr in table.select('tbody tr')]


def test_table_html_compound_primary_key(app_client):
    response = app_client.get('/test_tables/compound_primary_key', gather_request=False)
    table = Soup(response.body, 'html.parser').find('table')
    ths = table.findAll('th')
    assert 'Link' == ths[0].string.strip()
    for expected_col, th in zip(('pk1', 'pk2', 'content'), ths[1:]):
        a = th.find('a')
        assert expected_col == a.string
        assert a['href'].endswith('/compound_primary_key?_sort={}'.format(
            expected_col
        ))
    expected = [
        [
            '<td><a href="/test_tables/compound_primary_key/a,b">a,b</a></td>',
            '<td>a</td>',
            '<td>b</td>',
            '<td>c</td>',
        ]
    ]
    assert expected == [[str(td) for td in tr.select('td')] for tr in table.select('tbody tr')]


def test_row_html_compound_primary_key(app_client):
    response = app_client.get('/test_tables/compound_primary_key/a,b', gather_request=False)
    table = Soup(response.body, 'html.parser').find('table')
    assert [
        'pk1', 'pk2', 'content'
    ] == [th.string.strip() for th in table.select('thead th')]
    expected = [
        [
            '<td>a</td>',
            '<td>b</td>',
            '<td>c</td>',
        ]
    ]
    assert expected == [[str(td) for td in tr.select('td')] for tr in table.select('tbody tr')]


def test_view_html(app_client):
    response = app_client.get('/test_tables/simple_view', gather_request=False)
    table = Soup(response.body, 'html.parser').find('table')
    assert [
        'content', 'upper_content'
    ] == [th.string.strip() for th in table.select('thead th')]
    expected = [
        [
            '<td>hello</td>',
            '<td>HELLO</td>'
        ], [
            '<td>world</td>',
            '<td>WORLD</td>'
        ], [
            '<td></td>',
            '<td></td>'
        ]
    ]
    assert expected == [[str(td) for td in tr.select('td')] for tr in table.select('tbody tr')]


def test_index_metadata(app_client):
    response = app_client.get('/', gather_request=False)
    soup = Soup(response.body, 'html.parser')
    assert 'Datasette Title' == soup.find('h1').text
    assert 'Datasette Description' == inner_html(
        soup.find('div', {'class': 'metadata-description'})
    )
    assert_footer_links(soup)


def test_database_metadata(app_client):
    response = app_client.get('/test_tables', gather_request=False)
    soup = Soup(response.body, 'html.parser')
    # Page title should be the default
    assert 'test_tables' == soup.find('h1').text
    # Description should be custom
    assert 'Test tables description' == inner_html(
        soup.find('div', {'class': 'metadata-description'})
    )
    # The source/license should be inherited
    assert_footer_links(soup)


def test_table_metadata(app_client):
    response = app_client.get('/test_tables/simple_primary_key', gather_request=False)
    soup = Soup(response.body, 'html.parser')
    # Page title should be custom and should be HTML escaped
    assert 'This &lt;em&gt;HTML&lt;/em&gt; is escaped' == inner_html(soup.find('h1'))
    # Description should be custom and NOT escaped (we used description_html)
    assert 'Simple <em>primary</em> key' == inner_html(soup.find(
        'div', {'class': 'metadata-description'})
    )
    # The source/license should be inherited
    assert_footer_links(soup)


def assert_footer_links(soup):
    footer_links = soup.find('div', {'class': 'ft'}).findAll('a')
    assert 3 == len(footer_links)
    datasette_link, license_link, source_link = footer_links
    assert 'Datasette' == datasette_link.text.strip()
    assert 'Source' == source_link.text.strip()
    assert 'License' == license_link.text.strip()
    assert 'https://github.com/simonw/datasette' == datasette_link['href']
    assert 'http://www.example.com/source' == source_link['href']
    assert 'http://www.example.com/license' == license_link['href']


def inner_html(soup):
    html = str(soup)
    # This includes the parent tag - so remove that
    inner_html = html.split('>', 1)[1].rsplit('<', 1)[0]
    return inner_html.strip()
