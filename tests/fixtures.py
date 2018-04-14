from datasette.app import Datasette
import itertools
import os
import random
import sqlite3
import sys
import string
import tempfile
import time


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
            metadata=METADATA,
        )
        ds.sqlite_functions.append(
            ('sleep', 1, lambda n: time.sleep(float(n))),
        )
        yield ds.app().test_client


def generate_compound_rows(num):
    for a, b, c in itertools.islice(
        itertools.product(string.ascii_lowercase, repeat=3), num
    ):
        yield a, b, c, '{}-{}-{}'.format(a, b, c)


def generate_sortable_rows(num):
    rand = random.Random(42)
    for a, b in itertools.islice(
        itertools.product(string.ascii_lowercase, repeat=2), num
    ):
        yield {
            'pk1': a,
            'pk2': b,
            'content': '{}-{}'.format(a, b),
            'sortable': rand.randint(-100, 100),
            'sortable_with_nulls': rand.choice([
                None, rand.random(), rand.random()
            ]),
            'sortable_with_nulls_2': rand.choice([
                None, rand.random(), rand.random()
            ]),
        }


METADATA = {
    'title': 'Datasette Title',
    'description': 'Datasette Description',
    'license': 'License',
    'license_url': 'http://www.example.com/license',
    'source': 'Source',
    'source_url': 'http://www.example.com/source',
    'databases': {
        'test_tables': {
            'description': 'Test tables description',
            'tables': {
                'simple_primary_key': {
                    'description_html': 'Simple <em>primary</em> key',
                    'title': 'This <em>HTML</em> is escaped',
                },
                'sortable': {
                    'sortable_columns': [
                        'sortable',
                        'sortable_with_nulls',
                        'sortable_with_nulls_2',
                    ]
                },
                'no_primary_key': {
                    'sortable_columns': [],
                },
                'units': {
                    'units': {
                        'distance': 'm',
                        'frequency': 'Hz'
                    }
                },
            }
        },
    }
}


TABLES = '''
CREATE TABLE simple_primary_key (
  id varchar(30) primary key,
  content text
);

CREATE TABLE primary_key_multiple_columns (
  id varchar(30) primary key,
  content text,
  content2 text
);

CREATE TABLE compound_primary_key (
  pk1 varchar(30),
  pk2 varchar(30),
  content text,
  PRIMARY KEY (pk1, pk2)
);

INSERT INTO compound_primary_key VALUES ('a', 'b', 'c');

CREATE TABLE compound_three_primary_keys (
  pk1 varchar(30),
  pk2 varchar(30),
  pk3 varchar(30),
  content text,
  PRIMARY KEY (pk1, pk2, pk3)
);

CREATE TABLE foreign_key_references (
  pk varchar(30) primary key,
  foreign_key_with_label varchar(30),
  foreign_key_with_no_label varchar(30),
  FOREIGN KEY (foreign_key_with_label) REFERENCES simple_primary_key(id),
  FOREIGN KEY (foreign_key_with_no_label) REFERENCES primary_key_multiple_columns(id)
);

CREATE TABLE sortable (
  pk1 varchar(30),
  pk2 varchar(30),
  content text,
  sortable integer,
  sortable_with_nulls real,
  sortable_with_nulls_2 real,
  PRIMARY KEY (pk1, pk2)
);

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

CREATE TABLE units (
  pk integer primary key,
  distance int,
  frequency int
);

INSERT INTO units VALUES (1, 1, 100);
INSERT INTO units VALUES (2, 5000, 2500);
INSERT INTO units VALUES (3, 100000, 75000);

CREATE TABLE [select] (
  [group] text,
  [having] text,
  [and] text
);
INSERT INTO [select] VALUES ('group', 'having', 'and');

INSERT INTO simple_primary_key VALUES (1, 'hello');
INSERT INTO simple_primary_key VALUES (2, 'world');
INSERT INTO simple_primary_key VALUES (3, '');

INSERT INTO primary_key_multiple_columns VALUES (1, 'hey', 'world');

INSERT INTO foreign_key_references VALUES (1, 1, 1);

INSERT INTO complex_foreign_keys VALUES (1, 1, 2, 1);

INSERT INTO [table/with/slashes.csv] VALUES (3, 'hey');

CREATE VIEW simple_view AS
    SELECT content, upper(content) AS upper_content FROM simple_primary_key;

''' + '\n'.join([
    'INSERT INTO no_primary_key VALUES ({i}, "a{i}", "b{i}", "c{i}");'.format(i=i + 1)
    for i in range(201)
]) + '\n'.join([
    'INSERT INTO compound_three_primary_keys VALUES ("{a}", "{b}", "{c}", "{content}");'.format(
        a=a, b=b, c=c, content=content
    ) for a, b, c, content in generate_compound_rows(1001)
]) + '\n'.join([
    '''INSERT INTO sortable VALUES (
        "{pk1}", "{pk2}", "{content}", {sortable},
        {sortable_with_nulls}, {sortable_with_nulls_2});
    '''.format(
        **row
    ).replace('None', 'null') for row in generate_sortable_rows(201)
])

if __name__ == '__main__':
    filename = sys.argv[-1]
    if filename.endswith('.db'):
        conn = sqlite3.connect(filename)
        conn.executescript(TABLES)
        print('Test tables written to {}'.format(filename))
    else:
        print('Usage: {} name_of_file_to_write.db'.format(sys.argv[0]))
