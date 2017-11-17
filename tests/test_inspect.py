from datasette.app import Datasette
import os
import pytest
import sqlite3
import tempfile


TABLES = '''
CREATE TABLE "election_results" (
  "county" INTEGER,
  "party" INTEGER,
  "office" INTEGER,
  "votes" INTEGER,
  FOREIGN KEY (county) REFERENCES county(id),
  FOREIGN KEY (party) REFERENCES party(id),
  FOREIGN KEY (office) REFERENCES office(id)
 );

CREATE TABLE "county" (
  "id" INTEGER PRIMARY KEY ,
  "name" TEXT
);

CREATE TABLE "party" (
  "id" INTEGER PRIMARY KEY ,
  "name" TEXT
);

CREATE TABLE "office" (
  "id" INTEGER PRIMARY KEY ,
  "name" TEXT
);
'''


@pytest.fixture(scope='module')
def ds_instance():
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, 'test_tables.db')
        conn = sqlite3.connect(filepath)
        conn.executescript(TABLES)
        yield Datasette([filepath])


def test_inspect(ds_instance):
    info = ds_instance.inspect()
    tables = info['test_tables']['tables']
    for table_name in ('county', 'party', 'office'):
        assert 0 == tables[table_name]['count']
        foreign_keys = tables[table_name]['foreign_keys']
        assert [] == foreign_keys['outgoing']
        assert [{
            'column': 'id',
            'other_column': table_name,
            'other_table': 'election_results'
        }] == foreign_keys['incoming']

    election_results = tables['election_results']
    assert 0 == election_results['count']
    assert sorted([{
        'column': 'county',
        'other_column': 'id',
        'other_table': 'county'
    }, {
        'column': 'party',
        'other_column': 'id',
        'other_table': 'party'
    }, {
        'column': 'office',
        'other_column': 'id',
        'other_table': 'office'
    }], key=lambda d: d['column']) == sorted(
        election_results['foreign_keys']['outgoing'],
        key=lambda d: d['column']
    )
    assert [] == election_results['foreign_keys']['incoming']
