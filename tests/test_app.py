from bs4 import BeautifulSoup as Soup
from datasette.app import Datasette
import os
import pytest
import re
import sqlite3
import tempfile
import time
import urllib.parse


@pytest.fixture(scope='module')
def app_client():
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, 'test_tables.db')
        conn = sqlite3.connect(filepath)
        conn.executescript(TABLES)
        os.chdir(os.path.dirname(filepath))
        ds = Datasette(
            [filepath],
            page_size=50,
            max_returned_rows=100,
            sql_time_limit_ms=20,
        )
        ds.sqlite_functions.append(
            ('sleep', 1, lambda n: time.sleep(float(n))),
        )
        yield ds.app().test_client


def test_homepage(app_client):
    response = app_client.get('/', gather_request=False)
    assert response.status == 200
    assert 'test_tables' in response.text

    # Now try the JSON
    _, response = app_client.get('/.json')
    assert response.status == 200
    assert response.json.keys() == {'test_tables': 0}.keys()
    d = response.json['test_tables']
    assert d['name'] == 'test_tables'
    assert d['tables_count'] == 7


def test_database_page(app_client):
    response = app_client.get('/test_tables', allow_redirects=False, gather_request=False)
    assert response.status == 302
    response = app_client.get('/test_tables', gather_request=False)
    assert 'test_tables' in response.text
    # Test JSON list of tables
    response = app_client.get('/test_tables.json', gather_request=False)
    data = response.json
    assert 'test_tables' == data['database']
    assert [{
        'columns': ['content'],
        'name': '123_starts_with_digits',
        'count': 0,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
    }, {
        'columns': ['pk', 'content'],
        'name': 'Table With Space In Name',
        'count': 0,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
    }, {
        'columns': ['pk', 'f1', 'f2', 'f3'],
        'name': 'complex_foreign_keys',
        'count': 1,
        'foreign_keys': {
            'incoming': [],
            'outgoing': [{
                'column': 'f3',
                'other_column': 'id',
                'other_table': 'simple_primary_key'
            }, {
                'column': 'f2',
                'other_column': 'id',
                'other_table': 'simple_primary_key'
            }, {
                'column': 'f1',
                'other_column': 'id',
                'other_table': 'simple_primary_key'
            }],
        },
        'hidden': False,
        'label_column': None,
    }, {
        'columns': ['pk1', 'pk2', 'content'],
        'name': 'compound_primary_key',
        'count': 1,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
    }, {
        'columns': ['content', 'a', 'b', 'c'],
        'name': 'no_primary_key',
        'count': 201,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
    }, {
        'columns': ['pk', 'content'],
        'name': 'simple_primary_key',
        'count': 3,
        'hidden': False,
        'foreign_keys': {
            'incoming': [{
                'column': 'id',
                'other_column': 'f3',
                'other_table': 'complex_foreign_keys'
            }, {
                'column': 'id',
                'other_column': 'f2',
                'other_table': 'complex_foreign_keys'
            }, {
                'column': 'id',
                'other_column': 'f1',
                'other_table': 'complex_foreign_keys'
            }],
            'outgoing': [],
        },
        'label_column': None,
    }, {
        'columns': ['pk', 'content'],
        'name': 'table/with/slashes.csv',
        'count': 1,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
    }] == data['tables']


def test_custom_sql(app_client):
    response = app_client.get(
        '/test_tables.jsono?sql=select+content+from+simple_primary_key',
        gather_request=False
    )
    data = response.json
    assert {
        'sql': 'select content from simple_primary_key',
        'params': {}
    } == data['query']
    assert [
        {'content': 'hello'},
        {'content': 'world'},
        {'content': ''}
    ] == data['rows']
    assert ['content'] == data['columns']
    assert 'test_tables' == data['database']
    assert not data['truncated']


def test_sql_time_limit(app_client):
    response = app_client.get(
        '/test_tables.jsono?sql=select+sleep(0.5)',
        gather_request=False
    )
    assert 400 == response.status
    assert 'interrupted' == response.json['error']


def test_custom_sql_time_limit(app_client):
    response = app_client.get(
        '/test_tables.jsono?sql=select+sleep(0.01)',
        gather_request=False
    )
    assert 200 == response.status
    response = app_client.get(
        '/test_tables.jsono?sql=select+sleep(0.01)&_sql_time_limit_ms=5',
        gather_request=False
    )
    assert 400 == response.status
    assert 'interrupted' == response.json['error']


def test_invalid_custom_sql(app_client):
    response = app_client.get(
        '/test_tables?sql=.schema',
        gather_request=False
    )
    assert response.status == 400
    assert 'Statement must be a SELECT' in response.text
    response = app_client.get(
        '/test_tables.json?sql=.schema',
        gather_request=False
    )
    assert response.status == 400
    assert response.json['ok'] is False
    assert 'Statement must be a SELECT' == response.json['error']


def test_table_json(app_client):
    response = app_client.get('/test_tables/simple_primary_key.jsono', gather_request=False)
    assert response.status == 200
    data = response.json
    assert data['query']['sql'] == 'select * from simple_primary_key order by pk limit 51'
    assert data['query']['params'] == {}
    assert data['rows'] == [{
        'pk': '1',
        'content': 'hello',
    }, {
        'pk': '2',
        'content': 'world',
    }, {
        'pk': '3',
        'content': '',
    }]


def test_table_with_slashes_in_name(app_client):
    response = app_client.get('/test_tables/table%2Fwith%2Fslashes.csv', gather_request=False)
    assert response.status == 200
    response = app_client.get('/test_tables/table%2Fwith%2Fslashes.csv.jsono', gather_request=False)
    assert response.status == 200
    data = response.json
    assert data['rows'] == [{
        'pk': '3',
        'content': 'hey',
    }]


@pytest.mark.parametrize('path,expected_rows,expected_pages', [
    ('/test_tables/no_primary_key.jsono', 201, 5),
    ('/test_tables/paginated_view.jsono', 201, 5),
    ('/test_tables/123_starts_with_digits.jsono', 0, 1),
])
def test_paginate_tables_and_views(app_client, path, expected_rows, expected_pages):
    fetched = []
    count = 0
    while path:
        response = app_client.get(path, gather_request=False)
        count += 1
        fetched.extend(response.json['rows'])
        path = response.json['next_url']
        if path:
            assert response.json['next'] and path.endswith(response.json['next'])
        assert count < 10, 'Possible infinite loop detected'

    assert expected_rows == len(fetched)
    assert expected_pages == count


@pytest.mark.parametrize('path,expected_rows', [
    ('/test_tables/simple_primary_key.json?content=hello', [
        ['1', 'hello'],
    ]),
    ('/test_tables/simple_primary_key.json?content__contains=o', [
        ['1', 'hello'],
        ['2', 'world'],
    ]),
    ('/test_tables/simple_primary_key.json?content__exact=', [
        ['3', ''],
    ]),
    ('/test_tables/simple_primary_key.json?content__not=world', [
        ['1', 'hello'],
        ['3', ''],
    ]),
])
def test_table_filter_queries(app_client, path, expected_rows):
    response = app_client.get(path, gather_request=False)
    assert expected_rows == response.json['rows']


def test_max_returned_rows(app_client):
    response = app_client.get(
        '/test_tables.jsono?sql=select+content+from+no_primary_key',
        gather_request=False
    )
    data = response.json
    assert {
        'sql': 'select content from no_primary_key',
        'params': {}
    } == data['query']
    assert data['truncated']
    assert 100 == len(data['rows'])


def test_view(app_client):
    response = app_client.get('/test_tables/simple_view', gather_request=False)
    assert response.status == 200
    response = app_client.get('/test_tables/simple_view.jsono', gather_request=False)
    assert response.status == 200
    data = response.json
    assert data['rows'] == [{
        'upper_content': 'HELLO',
        'content': 'hello',
    }, {
        'upper_content': 'WORLD',
        'content': 'world',
    }, {
        'upper_content': '',
        'content': '',
    }]


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
    response = app_client.get('/test_tables/simple_primary_key/1.jsono', gather_request=False)
    assert response.status == 200
    assert [{'pk': '1', 'content': 'hello'}] == response.json['rows']


def test_row_foreign_key_tables(app_client):
    response = app_client.get('/test_tables/simple_primary_key/1.json?_extras=foreign_key_tables', gather_request=False)
    assert response.status == 200
    assert [{
        'column': 'id',
        'count': 1,
        'other_column': 'f3',
        'other_table': 'complex_foreign_keys'
    }, {
        'column': 'id',
        'count': 0,
        'other_column': 'f2',
        'other_table': 'complex_foreign_keys'
    }, {
        'column': 'id',
        'count': 1,
        'other_column': 'f1',
        'other_table': 'complex_foreign_keys'
    }] == response.json['foreign_key_tables']


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
    assert [
        'Link', 'pk', 'content'
    ] == [th.string for th in table.select('thead th')]
    assert [
        [
            '<td><a href="/test_tables-c0e2850/simple_primary_key/1">1</a></td>',
            '<td>1</td>',
            '<td>hello</td>'
        ], [
            '<td><a href="/test_tables-c0e2850/simple_primary_key/2">2</a></td>',
            '<td>2</td>',
            '<td>world</td>'
        ], [
            '<td><a href="/test_tables-c0e2850/simple_primary_key/3">3</a></td>',
            '<td>3</td>',
            '<td></td>'
        ]
    ] == [[str(td) for td in tr.select('td')] for tr in table.select('tbody tr')]


def test_row_html_simple_primary_key(app_client):
    response = app_client.get('/test_tables/simple_primary_key/1', gather_request=False)
    table = Soup(response.body, 'html.parser').find('table')
    assert [
        'pk', 'content'
    ] == [th.string for th in table.select('thead th')]
    assert [
        [
            '<td>1</td>',
            '<td>hello</td>'
        ]
    ] == [[str(td) for td in tr.select('td')] for tr in table.select('tbody tr')]


def test_table_html_no_primary_key(app_client):
    response = app_client.get('/test_tables/no_primary_key', gather_request=False)
    table = Soup(response.body, 'html.parser').find('table')
    assert [
        'Link', 'rowid', 'content', 'a', 'b', 'c'
    ] == [th.string for th in table.select('thead th')]
    expected = [
        [
            '<td><a href="/test_tables-c0e2850/no_primary_key/{}">{}</a></td>'.format(i, i),
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
    ] == [th.string for th in table.select('thead th')]
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
    assert [
        'Link', 'pk1', 'pk2', 'content'
    ] == [th.string for th in table.select('thead th')]
    expected = [
        [
            '<td><a href="/test_tables-c0e2850/compound_primary_key/a,b">a,b</a></td>',
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
    ] == [th.string for th in table.select('thead th')]
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
    ] == [th.string for th in table.select('thead th')]
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


TABLES = '''
CREATE TABLE simple_primary_key (
  pk varchar(30) primary key,
  content text
);

CREATE TABLE compound_primary_key (
  pk1 varchar(30),
  pk2 varchar(30),
  content text,
  PRIMARY KEY (pk1, pk2)
);

INSERT INTO compound_primary_key VALUES ('a', 'b', 'c');

CREATE TABLE no_primary_key (
  content text,
  a text,
  b text,
  c text
);

CREATE TABLE [123_starts_with_digits] (
  content text
);

CREATE VIEW paginated_view AS
    SELECT
        content,
        '- ' || content || ' -' AS content_extra
    FROM no_primary_key;

CREATE TABLE "Table With Space In Name" (
  pk varchar(30) primary key,
  content text
);

CREATE TABLE "table/with/slashes.csv" (
  pk varchar(30) primary key,
  content text
);

CREATE TABLE "complex_foreign_keys" (
  pk varchar(30) primary key,
  f1 text,
  f2 text,
  f3 text,
  FOREIGN KEY ("f1") REFERENCES [simple_primary_key](id),
  FOREIGN KEY ("f2") REFERENCES [simple_primary_key](id),
  FOREIGN KEY ("f3") REFERENCES [simple_primary_key](id)
);

INSERT INTO simple_primary_key VALUES (1, 'hello');
INSERT INTO simple_primary_key VALUES (2, 'world');
INSERT INTO simple_primary_key VALUES (3, '');

INSERT INTO complex_foreign_keys VALUES (1, 1, 2, 1);

INSERT INTO [table/with/slashes.csv] VALUES (3, 'hey');

CREATE VIEW simple_view AS
    SELECT content, upper(content) AS upper_content FROM simple_primary_key;

''' + '\n'.join([
    'INSERT INTO no_primary_key VALUES ({i}, "a{i}", "b{i}", "c{i}");'.format(i=i + 1)
    for i in range(201)
])
