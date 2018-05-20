from .fixtures import (
    app_client,
    app_client_shorter_time_limit,
    app_client_returend_rows_matches_page_size,
    generate_compound_rows,
    generate_sortable_rows,
    METADATA,
)
import pytest

pytest.fixture(scope='module')(app_client)
pytest.fixture(scope='module')(app_client_shorter_time_limit)
pytest.fixture(scope='module')(app_client_returend_rows_matches_page_size)


def test_homepage(app_client):
    _, response = app_client.get('/.json')
    assert response.status == 200
    assert response.json.keys() == {'test_tables': 0}.keys()
    d = response.json['test_tables']
    assert d['name'] == 'test_tables'
    assert d['tables_count'] == 17


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
        'fts_table': None,
        'primary_keys': [],
    }, {
        'columns': ['pk', 'content'],
        'name': 'Table With Space In Name',
        'count': 0,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'fts_table': None,
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
        'fts_table': None,
        'primary_keys': ['pk'],
    }, {
        'columns': ['pk1', 'pk2', 'content'],
        'name': 'compound_primary_key',
        'count': 1,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'fts_table': None,
        'primary_keys': ['pk1', 'pk2'],
    }, {
        'columns': ['pk1', 'pk2', 'pk3', 'content'],
        'name': 'compound_three_primary_keys',
        'count': 1001,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'fts_table': None,
        'primary_keys': ['pk1', 'pk2', 'pk3'],
    }, {
        'columns': ['pk', 'foreign_key_with_custom_label'],
        'name': 'custom_foreign_key_label',
        'count': 1,
        'hidden': False,
        'foreign_keys': {
            'incoming': [],
            'outgoing':  [{
                'column': 'foreign_key_with_custom_label',
                'other_column': 'id',
                'other_table': 'primary_key_multiple_columns_explicit_label'
            }],
        },
        'label_column': None,
        'fts_table': None,
        'primary_keys': ['pk'],
    }, {
        'columns': ['id', 'name'],
        'name': 'facet_cities',
        'count': 4,
        'foreign_keys': {
            'incoming': [{
                'column': 'id',
                'other_column': 'city_id',
                'other_table': 'facetable',
            }],
            'outgoing': []
        },
        'fts_table': None,
        'hidden': False,
        'label_column': 'name',
        'primary_keys': ['id'],
    }, {
        'columns': ['pk', 'planet_int', 'state', 'city_id', 'neighborhood'],
        'name': 'facetable',
        'count': 15,
        'foreign_keys': {
            'incoming': [],
            'outgoing': [{
                'column': 'city_id',
                'other_column': 'id',
                'other_table': 'facet_cities'
            }],
        },
        'fts_table': None,
        'hidden': False,
        'label_column': None,
        'primary_keys': ['pk'],
    }, {
        'columns': ['pk', 'foreign_key_with_label', 'foreign_key_with_no_label'],
        'name': 'foreign_key_references',
        'count': 1,
        'hidden': False,
        'foreign_keys': {
            'incoming': [],
            'outgoing':  [{
                'column': 'foreign_key_with_no_label',
                'other_column': 'id',
                'other_table': 'primary_key_multiple_columns'
            }, {
                'column': 'foreign_key_with_label',
                'other_column': 'id',
                'other_table': 'simple_primary_key',
            }],
        },
        'label_column': None,
        'fts_table': None,
        'primary_keys': ['pk'],
    }, {
        'columns': ['id', 'content', 'content2'],
        'name': 'primary_key_multiple_columns',
        'count': 1,
        'foreign_keys': {
            'incoming': [{
                'column': 'id',
                'other_column': 'foreign_key_with_no_label',
                'other_table': 'foreign_key_references'
            }],
            'outgoing': []
        },
        'hidden': False,
        'label_column': None,
        'fts_table': None,
        'primary_keys': ['id']
    }, {
        'columns': ['id', 'content', 'content2'],
        'name': 'primary_key_multiple_columns_explicit_label',
        'count': 1,
        'foreign_keys': {
            'incoming': [{
                'column': 'id',
                'other_column': 'foreign_key_with_custom_label',
                'other_table': 'custom_foreign_key_label'
            }],
            'outgoing': []
        },
        'hidden': False,
        'label_column': None,
        'fts_table': None,
        'primary_keys': ['id']
    },  {
        'columns': ['pk', 'text1', 'text2', 'name with . and spaces'],
        'name': 'searchable',
        'count': 2,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'fts_table': 'searchable_fts',
        'hidden': False,
        'label_column': None,
        'primary_keys': ['pk'],
    }, {
        'columns': ['group', 'having', 'and'],
        'name': 'select',
        'count': 1,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'fts_table': None,
        'primary_keys': [],
    }, {
        'columns': ['id', 'content'],
        'name': 'simple_primary_key',
        'count': 3,
        'hidden': False,
        'foreign_keys': {
            'incoming': [{
                'column': 'id',
                'other_column': 'foreign_key_with_label',
                'other_table': 'foreign_key_references'
            }, {
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
        'label_column': 'content',
        'fts_table': None,
        'primary_keys': ['id'],
    }, {
        'columns': [
            'pk1', 'pk2', 'content', 'sortable', 'sortable_with_nulls',
            'sortable_with_nulls_2', 'text',
        ],
        'name': 'sortable',
        'count': 201,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'fts_table': None,
        'primary_keys': ['pk1', 'pk2'],
    }, {
        'columns': ['pk', 'content'],
        'name': 'table/with/slashes.csv',
        'count': 1,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'fts_table': None,
        'primary_keys': ['pk'],
    }, {
        'columns': ['pk', 'distance', 'frequency'],
        'name': 'units',
        'count': 3,
        'hidden': False,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'fts_table': None,
        'primary_keys': ['pk'],
    },  {
        'columns': ['content', 'a', 'b', 'c'],
        'name': 'no_primary_key',
        'count': 201,
        'hidden': True,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'label_column': None,
        'fts_table': None,
        'primary_keys': [],
    },  {
        'columns': ['text1', 'text2', 'name with . and spaces', 'content'],
        'count': 2,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'fts_table': 'searchable_fts',
        'hidden': True,
        'label_column': None,
        'name': 'searchable_fts',
        'primary_keys': []
    }, {
        'columns': ['docid', 'c0text1', 'c1text2', 'c2name with . and spaces', 'c3content'],
        'count': 2,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'fts_table': None,
        'hidden': True,
        'label_column': None,
        'name': 'searchable_fts_content',
        'primary_keys': ['docid']
    }, {
        'columns': [
            'level', 'idx', 'start_block', 'leaves_end_block',
            'end_block', 'root'
        ],
        'count': 1,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'fts_table': None,
        'hidden': True,
        'label_column': None,
        'name': 'searchable_fts_segdir',
        'primary_keys': ['level', 'idx']
    }, {
        'columns': ['blockid', 'block'],
        'count': 0,
        'foreign_keys': {'incoming': [], 'outgoing': []},
        'fts_table': None,
        'hidden': True,
        'label_column': None,
        'name': 'searchable_fts_segments',
        'primary_keys': ['blockid']
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


def test_sql_time_limit(app_client_shorter_time_limit):
    response = app_client_shorter_time_limit.get(
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
        '/test_tables.json?sql=select+sleep(0.01)&_timelimit=5',
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
    assert data['query']['sql'] == 'select * from simple_primary_key order by id limit 51'
    assert data['query']['params'] == {}
    assert data['rows'] == [{
        'id': '1',
        'content': 'hello',
    }, {
        'id': '2',
        'content': 'world',
    }, {
        'id': '3',
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


def test_table_shape_arrays(app_client):
    response = app_client.get(
        '/test_tables/simple_primary_key.json?_shape=arrays',
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
        'id': '1',
        'content': 'hello',
    }, {
        'id': '2',
        'content': 'world',
    }, {
        'id': '3',
        'content': '',
    }] == response.json['rows']


def test_table_shape_array(app_client):
    response = app_client.get(
        '/test_tables/simple_primary_key.json?_shape=array',
        gather_request=False
    )
    assert [{
        'id': '1',
        'content': 'hello',
    }, {
        'id': '2',
        'content': 'world',
    }, {
        'id': '3',
        'content': '',
    }] == response.json


def test_table_shape_invalid(app_client):
    response = app_client.get(
        '/test_tables/simple_primary_key.json?_shape=invalid',
        gather_request=False
    )
    assert {
        'ok': False,
        'error': 'Invalid _shape: invalid',
        'status': 400,
        'title': None,
    } == response.json


def test_table_shape_object(app_client):
    response = app_client.get(
        '/test_tables/simple_primary_key.json?_shape=object',
        gather_request=False
    )
    assert {
        '1': {
            'id': '1',
            'content': 'hello',
        },
        '2': {
            'id': '2',
            'content': 'world',
        },
        '3': {
            'id': '3',
            'content': '',
        }
    } == response.json


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
    } == response.json


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
    ('/test_tables/no_primary_key.json?_size=25', 201, 9),
    ('/test_tables/paginated_view.json?_size=25', 201, 9),
    ('/test_tables/paginated_view.json?_size=max', 201, 3),
    ('/test_tables/123_starts_with_digits.json', 0, 1),
])
def test_paginate_tables_and_views(app_client, path, expected_rows, expected_pages):
    fetched = []
    count = 0
    while path:
        response = app_client.get(path, gather_request=False)
        assert 200 == response.status
        count += 1
        fetched.extend(response.json['rows'])
        path = response.json['next_url']
        if path:
            assert response.json['next']
            assert '_next={}'.format(response.json['next']) in path
        assert count < 10, 'Possible infinite loop detected'

    assert expected_rows == len(fetched)
    assert expected_pages == count


@pytest.mark.parametrize('path,expected_error', [
    ('/test_tables/no_primary_key.json?_size=-4', '_size must be a positive integer'),
    ('/test_tables/no_primary_key.json?_size=dog', '_size must be a positive integer'),
    ('/test_tables/no_primary_key.json?_size=1001', '_size must be <= 100'),
])
def test_validate_page_size(app_client, path, expected_error):
    response = app_client.get(path, gather_request=False)
    assert expected_error == response.json['error']
    assert 400 == response.status


def test_page_size_zero(app_client):
    "For _size=0 we return the counts, empty rows and no continuation token"
    response = app_client.get('/test_tables/no_primary_key.json?_size=0', gather_request=False)
    assert 200 == response.status
    assert [] == response.json['rows']
    assert 201 == response.json['table_rows_count']
    assert 201 == response.json['filtered_table_rows_count']
    assert None is response.json['next']
    assert None is response.json['next_url']


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
    (
        '_sort=sortable_with_nulls',
        lambda row: (
            1 if row['sortable_with_nulls'] is not None else 0,
            row['sortable_with_nulls']
        ),
        'sorted by sortable_with_nulls'
    ),
    (
        '_sort_desc=sortable_with_nulls',
        lambda row: (
            1 if row['sortable_with_nulls'] is None else 0,
            -row['sortable_with_nulls'] if row['sortable_with_nulls'] is not None else 0,
            row['content']
        ),
        'sorted by sortable_with_nulls descending'
    ),
    # text column contains '$null' - ensure it doesn't confuse pagination:
    ('_sort=text', lambda row: row['text'], 'sorted by text'),
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
    ('/test_tables/searchable.json?_search=dog', [
        [1, 'barry cat', 'terry dog', 'panther'],
        [2, 'terry dog', 'sara weasel', 'puma'],
    ]),
    ('/test_tables/searchable.json?_search=weasel', [
        [2, 'terry dog', 'sara weasel', 'puma'],
    ]),
    ('/test_tables/searchable.json?_search_text2=dog', [
        [1, 'barry cat', 'terry dog', 'panther'],
    ]),
    ('/test_tables/searchable.json?_search_name%20with%20.%20and%20spaces=panther', [
        [1, 'barry cat', 'terry dog', 'panther'],
    ]),
])
def test_searchable(app_client, path, expected_rows):
    response = app_client.get(path, gather_request=False)
    assert expected_rows == response.json['rows']


def test_searchable_invalid_column(app_client):
    response = app_client.get(
        '/test_tables/searchable.json?_search_invalid=x',
        gather_request=False
    )
    assert 400 == response.status
    assert {
        'ok': False,
        'error': 'Cannot search by that column',
        'status': 400,
        'title': None
    } == response.json


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
    assert [{'id': '1', 'content': 'hello'}] == response.json['rows']


def test_row_foreign_key_tables(app_client):
    response = app_client.get('/test_tables/simple_primary_key/1.json?_extras=foreign_key_tables', gather_request=False)
    assert response.status == 200
    assert [{
        'column': 'id',
        'count': 1,
        'other_column': 'foreign_key_with_label',
        'other_table': 'foreign_key_references'
    }, {
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


def test_unit_filters(app_client):
    response = app_client.get(
        '/test_tables/units.json?distance__lt=75km&frequency__gt=1kHz',
        gather_request=False
    )
    assert response.status == 200
    data = response.json

    assert data['units']['distance'] == 'm'
    assert data['units']['frequency'] == 'Hz'

    assert len(data['rows']) == 1
    assert data['rows'][0][0] == 2


def test_plugins_dir_plugin(app_client):
    response = app_client.get(
        "/test_tables.json?sql=select+convert_units(100%2C+'m'%2C+'ft')",
        gather_request=False
    )
    assert pytest.approx(328.0839) == response.json['rows'][0][0]


def test_metadata_json(app_client):
    response = app_client.get(
        "/-/metadata.json",
        gather_request=False
    )
    assert METADATA == response.json


def test_inspect_json(app_client):
    response = app_client.get(
        "/-/inspect.json",
        gather_request=False
    )
    assert app_client.ds.inspect() == response.json


def test_plugins_json(app_client):
    response = app_client.get(
        "/-/plugins.json",
        gather_request=False
    )
    # This will include any plugins that have been installed into the
    # current virtual environment, so we only check for the presence of
    # the one we know will definitely be There
    assert {
        'name': 'my_plugin.py',
        'static': False,
        'templates': False,
        'version': None,
    } in response.json


def test_versions_json(app_client):
    response = app_client.get(
        "/-/versions.json",
        gather_request=False
    )
    assert 'python' in response.json
    assert 'version' in response.json['python']
    assert 'full' in response.json['python']
    assert 'datasette' in response.json
    assert 'version' in response.json['datasette']
    assert 'sqlite' in response.json
    assert 'version' in response.json['sqlite']
    assert 'fts_versions' in response.json['sqlite']


def test_config_json(app_client):
    response = app_client.get(
        "/-/config.json",
        gather_request=False
    )
    assert {
        "default_page_size": 50,
        "default_facet_size": 30,
        "facet_suggest_time_limit_ms": 50,
        "facet_time_limit_ms": 200,
        "max_returned_rows": 100,
        "sql_time_limit_ms": 200,
    } == response.json


def test_page_size_matching_max_returned_rows(app_client_returend_rows_matches_page_size):
    fetched = []
    path = '/test_tables/no_primary_key.json'
    while path:
        response = app_client_returend_rows_matches_page_size.get(
            path, gather_request=False
        )
        fetched.extend(response.json['rows'])
        assert len(response.json['rows']) in (1, 50)
        path = response.json['next_url']
    assert 201 == len(fetched)


@pytest.mark.parametrize('path,expected_facet_results', [
    (
        "/test_tables/facetable.json?_facet=state&_facet=city_id",
        {
            "state": {
                "name": "state",
                "results": [
                    {
                        "value": "CA",
                        "label": "CA",
                        "count": 10,
                        "toggle_url": "_facet=state&_facet=city_id&state=CA",
                        "selected": False,
                    },
                    {
                        "value": "MI",
                        "label": "MI",
                        "count": 4,
                        "toggle_url": "_facet=state&_facet=city_id&state=MI",
                        "selected": False,
                    },
                    {
                        "value": "MC",
                        "label": "MC",
                        "count": 1,
                        "toggle_url": "_facet=state&_facet=city_id&state=MC",
                        "selected": False,
                    }
                ],
                "truncated": False,
            },
            "city_id": {
                "name": "city_id",
                "results": [
                    {
                        "value": 1,
                        "label": "San Francisco",
                        "count": 6,
                        "toggle_url": "_facet=state&_facet=city_id&city_id=1",
                        "selected": False,
                    },
                    {
                        "value": 2,
                        "label": "Los Angeles",
                        "count": 4,
                        "toggle_url": "_facet=state&_facet=city_id&city_id=2",
                        "selected": False,
                    },
                    {
                        "value": 3,
                        "label": "Detroit",
                        "count": 4,
                        "toggle_url": "_facet=state&_facet=city_id&city_id=3",
                        "selected": False,
                    },
                    {
                        "value": 4,
                        "label": "Memnonia",
                        "count": 1,
                        "toggle_url": "_facet=state&_facet=city_id&city_id=4",
                        "selected": False,
                    }
                ],
                "truncated": False,
            }
        }
    ), (
        "/test_tables/facetable.json?_facet=state&_facet=city_id&state=MI",
        {
            "state": {
                "name": "state",
                "results": [
                    {
                        "value": "MI",
                        "label": "MI",
                        "count": 4,
                        "selected": True,
                        "toggle_url": "_facet=state&_facet=city_id",
                    },
                ],
                "truncated": False,
            },
            "city_id": {
                "name": "city_id",
                "results": [
                    {
                        "value": 3,
                        "label": "Detroit",
                        "count": 4,
                        "selected": False,
                        "toggle_url": "_facet=state&_facet=city_id&state=MI&city_id=3",
                    },
                ],
                "truncated": False,
            },
        },
    ), (
        "/test_tables/facetable.json?_facet=planet_int",
        {
            "planet_int": {
                "name": "planet_int",
                "results": [
                    {
                        "value": 1,
                        "label": 1,
                        "count": 14,
                        "selected": False,
                        "toggle_url": "_facet=planet_int&planet_int=1",
                    },
                    {
                        "value": 2,
                        "label": 2,
                        "count": 1,
                        "selected": False,
                        "toggle_url": "_facet=planet_int&planet_int=2",
                    },
                ],
                "truncated": False,
            }
        },
    ), (
        # planet_int is an integer field:
        "/test_tables/facetable.json?_facet=planet_int&planet_int=1",
        {
            "planet_int": {
                "name": "planet_int",
                "results": [
                    {
                        "value": 1,
                        "label": 1,
                        "count": 14,
                        "selected": True,
                        "toggle_url": "_facet=planet_int",
                    }
                ],
                "truncated": False,
            },
        },
    )
])
def test_facets(app_client, path, expected_facet_results):
    response = app_client.get(path, gather_request=False)
    facet_results = response.json['facet_results']
    # We only compare the querystring portion of the taggle_url
    for facet_name, facet_info in facet_results.items():
        assert facet_name == facet_info["name"]
        assert False is facet_info["truncated"]
        for facet_value in facet_info["results"]:
            facet_value['toggle_url'] = facet_value['toggle_url'].split('?')[1]
    assert expected_facet_results == facet_results
