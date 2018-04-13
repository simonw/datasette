from .fixtures import (
    app_client,
    generate_compound_rows,
    generate_sortable_rows,
)
import pytest

pytest.fixture(scope='module')(app_client)


def test_homepage(app_client):
    _, response = app_client.get('/.json')
    assert response.status == 200
    assert response.json.keys() == {'test_tables': 0}.keys()
    d = response.json['test_tables']
    assert d['name'] == 'test_tables'
    assert d['tables_count'] == 10


def test_database_page(app_client):
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
        'primary_keys': [],
    }, {
        'columns': ['pk', 'content'],
        'name': 'Table With Space In Name',
        'count': 0,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'primary_keys': ['pk'],
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
        'primary_keys': ['pk'],
    }, {
        'columns': ['pk1', 'pk2', 'content'],
        'name': 'compound_primary_key',
        'count': 1,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'primary_keys': ['pk1', 'pk2'],
    }, {
        'columns': ['pk1', 'pk2', 'pk3', 'content'],
        'name': 'compound_three_primary_keys',
        'count': 1001,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'primary_keys': ['pk1', 'pk2', 'pk3'],
    }, {
        'columns': ['content', 'a', 'b', 'c'],
        'name': 'no_primary_key',
        'count': 201,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'primary_keys': [],
    }, {
        'columns': ['group', 'having', 'and'],
        'name': 'select',
        'count': 1,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'primary_keys': [],
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
        'primary_keys': ['pk'],
    }, {
        'columns': [
            'pk1', 'pk2', 'content', 'sortable', 'sortable_with_nulls',
            'sortable_with_nulls_2'
        ],
        'name': 'sortable',
        'count': 201,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'primary_keys': ['pk1', 'pk2'],
    }, {
        'columns': ['pk', 'content'],
        'name': 'table/with/slashes.csv',
        'count': 1,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'primary_keys': ['pk'],
    }] == data['tables']


def test_custom_sql(app_client):
    response = app_client.get(
        '/test_tables.json?sql=select+content+from+simple_primary_key&_shape=objects',
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
        '/test_tables.json?sql=select+sleep(0.5)',
        gather_request=False
    )
    assert 400 == response.status
    assert 'interrupted' == response.json['error']


def test_custom_sql_time_limit(app_client):
    response = app_client.get(
        '/test_tables.json?sql=select+sleep(0.01)',
        gather_request=False
    )
    assert 200 == response.status
    response = app_client.get(
        '/test_tables.json?sql=select+sleep(0.01)&_sql_time_limit_ms=5',
        gather_request=False
    )
    assert 400 == response.status
    assert 'interrupted' == response.json['error']


def test_invalid_custom_sql(app_client):
    response = app_client.get(
        '/test_tables.json?sql=.schema',
        gather_request=False
    )
    assert response.status == 400
    assert response.json['ok'] is False
    assert 'Statement must be a SELECT' == response.json['error']


def test_table_json(app_client):
    response = app_client.get('/test_tables/simple_primary_key.json?_shape=objects', gather_request=False)
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


def test_table_not_exists_json(app_client):
    assert {
        'ok': False,
        'error': 'Table not found: blah',
        'status': 404,
        'title': None,
    } == app_client.get(
        '/test_tables/blah.json', gather_request=False
    ).json


def test_jsono_redirects_to_shape_objects(app_client):
    response_1 = app_client.get(
        '/test_tables/simple_primary_key.jsono',
        allow_redirects=False,
        gather_request=False
    )
    response = app_client.get(
        response_1.headers['Location'],
        allow_redirects=False,
        gather_request=False
    )
    assert response.status == 302
    assert response.headers['Location'].endswith('?_shape=objects')


def test_table_shape_lists(app_client):
    response = app_client.get(
        '/test_tables/simple_primary_key.json?_shape=lists',
        gather_request=False
    )
    assert [
        ['1', 'hello'],
        ['2', 'world'],
        ['3', ''],
    ] == response.json['rows']


def test_table_shape_objects(app_client):
    response = app_client.get(
        '/test_tables/simple_primary_key.json?_shape=objects',
        gather_request=False
    )
    assert [{
        'pk': '1',
        'content': 'hello',
    }, {
        'pk': '2',
        'content': 'world',
    }, {
        'pk': '3',
        'content': '',
    }] == response.json['rows']


def test_table_shape_object(app_client):
    response = app_client.get(
        '/test_tables/simple_primary_key.json?_shape=object',
        gather_request=False
    )
    assert {
        '1': {
            'pk': '1',
            'content': 'hello',
        },
        '2': {
            'pk': '2',
            'content': 'world',
        },
        '3': {
            'pk': '3',
            'content': '',
        }
    } == response.json['rows']


def test_table_shape_object_compound_primary_Key(app_client):
    response = app_client.get(
        '/test_tables/compound_primary_key.json?_shape=object',
        gather_request=False
    )
    assert {
        'a,b': {
            'pk1': 'a',
            'pk2': 'b',
            'content': 'c',
        }
    } == response.json['rows']


def test_table_with_slashes_in_name(app_client):
    response = app_client.get('/test_tables/table%2Fwith%2Fslashes.csv.json?_shape=objects', gather_request=False)
    assert response.status == 200
    data = response.json
    assert data['rows'] == [{
        'pk': '3',
        'content': 'hey',
    }]


def test_table_with_reserved_word_name(app_client):
    response = app_client.get('/test_tables/select.json?_shape=objects', gather_request=False)
    assert response.status == 200
    data = response.json
    assert data['rows'] == [{
        'rowid': 1,
        'group': 'group',
        'having': 'having',
        'and': 'and',
    }]


@pytest.mark.parametrize('path,expected_rows,expected_pages', [
    ('/test_tables/no_primary_key.json', 201, 5),
    ('/test_tables/paginated_view.json', 201, 5),
    ('/test_tables/123_starts_with_digits.json', 0, 1),
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


def test_paginate_compound_keys(app_client):
    fetched = []
    path = '/test_tables/compound_three_primary_keys.json?_shape=objects'
    page = 0
    while path:
        page += 1
        response = app_client.get(path, gather_request=False)
        fetched.extend(response.json['rows'])
        path = response.json['next_url']
        assert page < 100
    assert 1001 == len(fetched)
    assert 21 == page
    # Should be correctly ordered
    contents = [f['content'] for f in fetched]
    expected = [r[3] for r in generate_compound_rows(1001)]
    assert expected == contents


def test_paginate_compound_keys_with_extra_filters(app_client):
    fetched = []
    path = '/test_tables/compound_three_primary_keys.json?content__contains=d&_shape=objects'
    page = 0
    while path:
        page += 1
        assert page < 100
        response = app_client.get(path, gather_request=False)
        fetched.extend(response.json['rows'])
        path = response.json['next_url']
    assert 2 == page
    expected = [
        r[3] for r in generate_compound_rows(1001)
        if 'd' in r[3]
    ]
    assert expected == [f['content'] for f in fetched]


@pytest.mark.parametrize('query_string,sort_key,human_description_en', [
    ('_sort=sortable', lambda row: row['sortable'], 'sorted by sortable'),
    ('_sort_desc=sortable', lambda row: -row['sortable'], 'sorted by sortable descending'),
])
def test_sortable(app_client, query_string, sort_key, human_description_en):
    path = '/test_tables/sortable.json?_shape=objects&{}'.format(query_string)
    fetched = []
    page = 0
    while path:
        page += 1
        assert page < 100
        response = app_client.get(path, gather_request=False)
        assert human_description_en == response.json['human_description_en']
        fetched.extend(response.json['rows'])
        path = response.json['next_url']
    assert 5 == page
    expected = list(generate_sortable_rows(201))
    expected.sort(key=sort_key)
    assert [
        r['content'] for r in expected
    ] == [
        r['content'] for r in fetched
    ]


def test_sortable_and_filtered(app_client):
    path = (
        '/test_tables/sortable.json'
        '?content__contains=d&_sort_desc=sortable&_shape=objects'
    )
    response = app_client.get(path, gather_request=False)
    fetched = response.json['rows']
    assert 'where content contains "d" sorted by sortable descending' \
        == response.json['human_description_en']
    expected = [
        row for row in generate_sortable_rows(201)
        if 'd' in row['content']
    ]
    assert len(expected) == response.json['filtered_table_rows_count']
    assert 201 == response.json['table_rows_count']
    expected.sort(key=lambda row: -row['sortable'])
    assert [
        r['content'] for r in expected
    ] == [
        r['content'] for r in fetched
    ]


def test_sortable_argument_errors(app_client):
    response = app_client.get(
        '/test_tables/sortable.json?_sort=badcolumn',
        gather_request=False
    )
    assert 'Cannot sort table by badcolumn' == response.json['error']
    response = app_client.get(
        '/test_tables/sortable.json?_sort_desc=badcolumn2',
        gather_request=False
    )
    assert 'Cannot sort table by badcolumn2' == response.json['error']
    response = app_client.get(
        '/test_tables/sortable.json?_sort=sortable_with_nulls&_sort_desc=sortable',
        gather_request=False
    )
    assert 'Cannot use _sort and _sort_desc at the same time' == response.json['error']


def test_sortable_columns_metadata(app_client):
    response = app_client.get(
        '/test_tables/sortable.json?_sort=content',
        gather_request=False
    )
    assert 'Cannot sort table by content' == response.json['error']
    # no_primary_key has ALL sort options disabled
    for column in ('content', 'a', 'b', 'c'):
        response = app_client.get(
            '/test_tables/sortable.json?_sort={}'.format(column),
            gather_request=False
        )
        assert 'Cannot sort table by {}'.format(column) == response.json['error']


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
        '/test_tables.json?sql=select+content+from+no_primary_key',
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
    response = app_client.get('/test_tables/simple_view.json?_shape=objects', gather_request=False)
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
    response = app_client.get('/test_tables/simple_primary_key/1.json?_shape=objects', gather_request=False)
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
