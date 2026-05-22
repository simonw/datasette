from datasette.utils.sqlite import sqlite3
from datasette.utils import documented
import itertools
import random
import string

__all__ = [
    "EXTRA_DATABASE_SQL",
    "TABLES",
    "TABLE_PARAMETERIZED_SQL",
    "generate_compound_rows",
    "generate_sortable_rows",
    "populate_extra_database",
    "populate_fixture_database",
    "write_extra_database",
    "write_fixture_database",
]


def generate_compound_rows(num):
    """Generate rows for the compound_three_primary_keys fixture table."""
    for a, b, c in itertools.islice(
        itertools.product(string.ascii_lowercase, repeat=3), num
    ):
        yield a, b, c, f"{a}-{b}-{c}"


def generate_sortable_rows(num):
    """Generate rows for the sortable fixture table."""
    rand = random.Random(42)
    for a, b in itertools.islice(
        itertools.product(string.ascii_lowercase, repeat=2), num
    ):
        yield {
            "pk1": a,
            "pk2": b,
            "content": f"{a}-{b}",
            "sortable": rand.randint(-100, 100),
            "sortable_with_nulls": rand.choice([None, rand.random(), rand.random()]),
            "sortable_with_nulls_2": rand.choice([None, rand.random(), rand.random()]),
            "text": rand.choice(["$null", "$blah"]),
        }


TABLES = (
    """
CREATE TABLE simple_primary_key (
  id integer primary key,
  content text
);

CREATE TABLE primary_key_multiple_columns (
  id varchar(30) primary key,
  content text,
  content2 text
);

CREATE TABLE primary_key_multiple_columns_explicit_label (
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
INSERT INTO compound_primary_key VALUES ('a/b', '.c-d', 'c');
INSERT INTO compound_primary_key VALUES ('d', 'e', 'RENDER_CELL_DEMO');

CREATE TABLE compound_three_primary_keys (
  pk1 varchar(30),
  pk2 varchar(30),
  pk3 varchar(30),
  content text,
  PRIMARY KEY (pk1, pk2, pk3)
);
CREATE INDEX idx_compound_three_primary_keys_content ON compound_three_primary_keys(content);

CREATE TABLE foreign_key_references (
  pk varchar(30) primary key,
  foreign_key_with_label integer,
  foreign_key_with_blank_label integer,
  foreign_key_with_no_label varchar(30),
  foreign_key_compound_pk1 varchar(30),
  foreign_key_compound_pk2 varchar(30),
  FOREIGN KEY (foreign_key_with_label) REFERENCES simple_primary_key(id),
  FOREIGN KEY (foreign_key_with_blank_label) REFERENCES simple_primary_key(id),
  FOREIGN KEY (foreign_key_with_no_label) REFERENCES primary_key_multiple_columns(id)
  FOREIGN KEY (foreign_key_compound_pk1, foreign_key_compound_pk2) REFERENCES compound_primary_key(pk1, pk2)
);

CREATE TABLE sortable (
  pk1 varchar(30),
  pk2 varchar(30),
  content text,
  sortable integer,
  sortable_with_nulls real,
  sortable_with_nulls_2 real,
  text text,
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
  f1 integer,
  f2 integer,
  f3 integer,
  FOREIGN KEY ("f1") REFERENCES [simple_primary_key](id),
  FOREIGN KEY ("f2") REFERENCES [simple_primary_key](id),
  FOREIGN KEY ("f3") REFERENCES [simple_primary_key](id)
);

CREATE TABLE "custom_foreign_key_label" (
  pk varchar(30) primary key,
  foreign_key_with_custom_label text,
  FOREIGN KEY ("foreign_key_with_custom_label") REFERENCES [primary_key_multiple_columns_explicit_label](id)
);

CREATE TABLE tags (
    tag TEXT PRIMARY KEY
);

CREATE TABLE searchable (
  pk integer primary key,
  text1 text,
  text2 text,
  [name with . and spaces] text
);

CREATE TABLE searchable_tags (
    searchable_id integer,
    tag text,
    PRIMARY KEY (searchable_id, tag),
    FOREIGN KEY (searchable_id) REFERENCES searchable(pk),
    FOREIGN KEY (tag) REFERENCES tags(tag)
);

INSERT INTO searchable VALUES (1, 'barry cat', 'terry dog', 'panther');
INSERT INTO searchable VALUES (2, 'terry dog', 'sara weasel', 'puma');

INSERT INTO tags VALUES ("canine");
INSERT INTO tags VALUES ("feline");

INSERT INTO searchable_tags (searchable_id, tag) VALUES
    (1, "feline"),
    (2, "canine")
;

CREATE VIRTUAL TABLE "searchable_fts"
    USING FTS5 (text1, text2, [name with . and spaces], content="searchable", content_rowid="pk");
INSERT INTO "searchable_fts" (searchable_fts) VALUES ('rebuild');

CREATE TABLE [select] (
  [group] text,
  [having] text,
  [and] text,
  [json] text
);
INSERT INTO [select] VALUES ('group', 'having', 'and',
    '{"href": "http://example.com/", "label":"Example"}'
);

CREATE TABLE infinity (
    value REAL
);
INSERT INTO infinity VALUES
    (1e999),
    (-1e999),
    (1.5)
;

CREATE TABLE facet_cities (
    id integer primary key,
    name text
);
INSERT INTO facet_cities (id, name) VALUES
    (1, 'San Francisco'),
    (2, 'Los Angeles'),
    (3, 'Detroit'),
    (4, 'Memnonia')
;

CREATE TABLE facetable (
    pk integer primary key,
    created text,
    planet_int integer,
    on_earth integer,
    state text,
    _city_id integer,
    _neighborhood text,
    tags text,
    complex_array text,
    distinct_some_null,
    n text,
    FOREIGN KEY ("_city_id") REFERENCES [facet_cities](id)
);
INSERT INTO facetable
    (created, planet_int, on_earth, state, _city_id, _neighborhood, tags, complex_array, distinct_some_null, n)
VALUES
    ("2019-01-14 08:00:00", 1, 1, 'CA', 1, 'Mission', '["tag1", "tag2"]', '[{"foo": "bar"}]', 'one', 'n1'),
    ("2019-01-14 08:00:00", 1, 1, 'CA', 1, 'Dogpatch', '["tag1", "tag3"]', '[]', 'two', 'n2'),
    ("2019-01-14 08:00:00", 1, 1, 'CA', 1, 'SOMA', '[]', '[]', null, null),
    ("2019-01-14 08:00:00", 1, 1, 'CA', 1, 'Tenderloin', '[]', '[]', null, null),
    ("2019-01-15 08:00:00", 1, 1, 'CA', 1, 'Bernal Heights', '[]', '[]', null, null),
    ("2019-01-15 08:00:00", 1, 1, 'CA', 1, 'Hayes Valley', '[]', '[]', null, null),
    ("2019-01-15 08:00:00", 1, 1, 'CA', 2, 'Hollywood', '[]', '[]', null, null),
    ("2019-01-15 08:00:00", 1, 1, 'CA', 2, 'Downtown', '[]', '[]', null, null),
    ("2019-01-16 08:00:00", 1, 1, 'CA', 2, 'Los Feliz', '[]', '[]', null, null),
    ("2019-01-16 08:00:00", 1, 1, 'CA', 2, 'Koreatown', '[]', '[]', null, null),
    ("2019-01-16 08:00:00", 1, 1, 'MI', 3, 'Downtown', '[]', '[]', null, null),
    ("2019-01-17 08:00:00", 1, 1, 'MI', 3, 'Greektown', '[]', '[]', null, null),
    ("2019-01-17 08:00:00", 1, 1, 'MI', 3, 'Corktown', '[]', '[]', null, null),
    ("2019-01-17 08:00:00", 1, 1, 'MI', 3, 'Mexicantown', '[]', '[]', null, null),
    ("2019-01-17 08:00:00", 2, 0, 'MC', 4, 'Arcadia Planitia', '[]', '[]', null, null)
;

CREATE TABLE binary_data (
    data BLOB
);

-- Many 2 Many demo: roadside attractions!

CREATE TABLE roadside_attractions (
    pk integer primary key,
    name text,
    address text,
    url text,
    latitude real,
    longitude real
);
INSERT INTO roadside_attractions VALUES (
    1, "The Mystery Spot", "465 Mystery Spot Road, Santa Cruz, CA 95065", "https://www.mysteryspot.com/",
    37.0167, -122.0024
);
INSERT INTO roadside_attractions VALUES (
    2, "Winchester Mystery House", "525 South Winchester Boulevard, San Jose, CA 95128", "https://winchestermysteryhouse.com/",
    37.3184, -121.9511
);
INSERT INTO roadside_attractions VALUES (
    3, "Burlingame Museum of PEZ Memorabilia", "214 California Drive, Burlingame, CA 94010", null,
    37.5793, -122.3442
);
INSERT INTO roadside_attractions VALUES (
    4, "Bigfoot Discovery Museum", "5497 Highway 9, Felton, CA 95018", "https://www.bigfootdiscoveryproject.com/",
    37.0414, -122.0725
);

CREATE TABLE attraction_characteristic (
    pk integer primary key,
    name text
);
INSERT INTO attraction_characteristic VALUES (
    1, "Museum"
);
INSERT INTO attraction_characteristic VALUES (
    2, "Paranormal"
);

CREATE TABLE roadside_attraction_characteristics (
    attraction_id INTEGER REFERENCES roadside_attractions(pk),
    characteristic_id INTEGER REFERENCES attraction_characteristic(pk)
);
INSERT INTO roadside_attraction_characteristics VALUES (
    1, 2
);
INSERT INTO roadside_attraction_characteristics VALUES (
    2, 2
);
INSERT INTO roadside_attraction_characteristics VALUES (
    4, 2
);
INSERT INTO roadside_attraction_characteristics VALUES (
    3, 1
);
INSERT INTO roadside_attraction_characteristics VALUES (
    4, 1
);

INSERT INTO simple_primary_key VALUES (1, 'hello');
INSERT INTO simple_primary_key VALUES (2, 'world');
INSERT INTO simple_primary_key VALUES (3, '');
INSERT INTO simple_primary_key VALUES (4, 'RENDER_CELL_DEMO');
INSERT INTO simple_primary_key VALUES (5, 'RENDER_CELL_ASYNC');

INSERT INTO primary_key_multiple_columns VALUES (1, 'hey', 'world');
INSERT INTO primary_key_multiple_columns_explicit_label VALUES (1, 'hey', 'world2');

INSERT INTO foreign_key_references VALUES (1, 1, 3, 1, 'a', 'b');
INSERT INTO foreign_key_references VALUES (2, null, null, null, null, null);

INSERT INTO complex_foreign_keys VALUES (1, 1, 2, 1);
INSERT INTO custom_foreign_key_label VALUES (1, 1);

INSERT INTO [table/with/slashes.csv] VALUES (3, 'hey');

CREATE VIEW simple_view AS
    SELECT content, upper(content) AS upper_content FROM simple_primary_key;

CREATE VIEW searchable_view AS
    SELECT * from searchable;

CREATE VIEW searchable_view_configured_by_metadata AS
    SELECT * from searchable;

"""
    + "\n".join(
        [
            'INSERT INTO no_primary_key VALUES ({i}, "a{i}", "b{i}", "c{i}");'.format(
                i=i + 1
            )
            for i in range(201)
        ]
    )
    + '\nINSERT INTO no_primary_key VALUES ("RENDER_CELL_DEMO", "a202", "b202", "c202");\n'
    + "\n".join(
        [
            'INSERT INTO compound_three_primary_keys VALUES ("{a}", "{b}", "{c}", "{content}");'.format(
                a=a, b=b, c=c, content=content
            )
            for a, b, c, content in generate_compound_rows(1001)
        ]
    )
    + "\n".join(["""INSERT INTO sortable VALUES (
        "{pk1}", "{pk2}", "{content}", {sortable},
        {sortable_with_nulls}, {sortable_with_nulls_2}, "{text}");
    """.format(**row).replace("None", "null") for row in generate_sortable_rows(201)])
)

TABLE_PARAMETERIZED_SQL = [
    ("insert into binary_data (data) values (?);", [b"\x15\x1c\x02\xc7\xad\x05\xfe"]),
    ("insert into binary_data (data) values (?);", [b"\x15\x1c\x03\xc7\xad\x05\xfe"]),
    ("insert into binary_data (data) values (null);", []),
]

EXTRA_DATABASE_SQL = """
CREATE TABLE searchable (
  pk integer primary key,
  text1 text,
  text2 text
);

CREATE VIEW searchable_view AS SELECT * FROM searchable;

INSERT INTO searchable VALUES (1, 'barry cat', 'terry dog');
INSERT INTO searchable VALUES (2, 'terry dog', 'sara weasel');

CREATE VIRTUAL TABLE "searchable_fts"
    USING FTS3 (text1, text2, content="searchable");
INSERT INTO "searchable_fts" (rowid, text1, text2)
    SELECT rowid, text1, text2 FROM searchable;
"""


@documented(label="datasette_fixtures_populate_fixture_database")
def populate_fixture_database(conn):
    """Populate a SQLite connection with Datasette's test fixture tables."""
    conn.executescript(TABLES)
    for sql, params in TABLE_PARAMETERIZED_SQL:
        with conn:
            conn.execute(sql, params)


def populate_extra_database(conn):
    """Populate a SQLite connection with the extra database used in tests."""
    conn.executescript(EXTRA_DATABASE_SQL)


def write_fixture_database(db_filename):
    """Write Datasette's test fixture tables to a SQLite database file."""
    conn = sqlite3.connect(db_filename)
    try:
        populate_fixture_database(conn)
    finally:
        conn.close()


def write_extra_database(db_filename):
    """Write the extra test database tables to a SQLite database file."""
    conn = sqlite3.connect(db_filename)
    try:
        populate_extra_database(conn)
    finally:
        conn.close()
