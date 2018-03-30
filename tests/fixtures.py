from datasette.app import Datasette
import itertools
import os
import sqlite3
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
                }
            }
        }
    }
}


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


CREATE TABLE compound_three_primary_keys (
  pk1 varchar(30),
  pk2 varchar(30),
  pk3 varchar(30),
  content text,
  PRIMARY KEY (pk1, pk2, pk3)
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
]) + '\n'.join([
    'INSERT INTO compound_three_primary_keys VALUES ("{a}", "{b}", "{c}", "{a}-{b}-{c}");'.format(
        a=a, b=b, c=c
    ) for a, b, c in itertools.islice(
        itertools.product(string.ascii_lowercase, repeat=3), 301
    )
])
