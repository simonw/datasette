from datasette.app import Datasette
import os
import pytest
import sqlite3
import tempfile


@pytest.fixture(scope='module')
def three_table_app_client():
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, 'three_tables.db')
        conn = sqlite3.connect(filepath)
        conn.executescript(THREE_TABLES)
        os.chdir(os.path.dirname(filepath))
        yield Datasette([filepath]).app().test_client


def test_homepage(three_table_app_client):
    _, response = three_table_app_client.get('/')
    assert response.status == 200
    assert 'three_tables' in response.text

    # Now try the JSON
    _, response = three_table_app_client.get('/.json')
    assert response.status == 200
    assert response.json.keys() == {'three_tables': 0}.keys()
    d = response.json['three_tables']
    assert d['name'] == 'three_tables'
    assert d['tables_count'] == 3


def test_database_page(three_table_app_client):
    _, response = three_table_app_client.get('/three_tables', allow_redirects=False)
    assert response.status == 302
    _, response = three_table_app_client.get('/three_tables')
    assert 'three_tables' in response.text


def test_table_page(three_table_app_client):
    _, response = three_table_app_client.get('/three_tables/simple_primary_key')
    assert response.status == 200
    _, response = three_table_app_client.get('/three_tables/simple_primary_key.jsono')
    assert response.status == 200
    data = response.json
    assert data['query']['sql'] == 'select * from "simple_primary_key" order by pk limit 51'
    assert data['query']['params'] == {}
    assert data['rows'] == [{
        'pk': '1',
        'content': 'hello',
    }, {
        'pk': '2',
        'content': 'world',
    }]


THREE_TABLES = '''
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

CREATE TABLE no_primary_key (
  content text
);

INSERT INTO simple_primary_key VALUES (1, 'hello');
INSERT INTO simple_primary_key VALUES (2, 'world');
'''
