from datasette.app import Datasette
from datasette.utils.sqlite import sqlite3
from datasette.utils.testing import TestClient
import click
import contextlib
import itertools
import json
import os
import pathlib
import pytest
import random
import string
import tempfile
import textwrap


# This temp file is used by one of the plugin config tests
TEMP_PLUGIN_SECRET_FILE = os.path.join(tempfile.gettempdir(), "plugin-secret")

PLUGINS_DIR = str(pathlib.Path(__file__).parent / "plugins")

EXPECTED_PLUGINS = [
    {
        "name": "messages_output_renderer.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": ["register_output_renderer"],
    },
    {
        "name": "my_plugin.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": [
            "actor_from_request",
            "asgi_wrapper",
            "canned_queries",
            "database_actions",
            "extra_body_script",
            "extra_css_urls",
            "extra_js_urls",
            "extra_template_vars",
            "forbidden",
            "menu_links",
            "permission_allowed",
            "prepare_connection",
            "prepare_jinja2_environment",
            "register_facet_classes",
            "register_magic_parameters",
            "register_permissions",
            "register_routes",
            "render_cell",
            "skip_csrf",
            "startup",
            "table_actions",
        ],
    },
    {
        "name": "my_plugin_2.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": [
            "actor_from_request",
            "asgi_wrapper",
            "canned_queries",
            "extra_js_urls",
            "extra_template_vars",
            "handle_exception",
            "menu_links",
            "permission_allowed",
            "prepare_jinja2_environment",
            "register_routes",
            "render_cell",
            "startup",
            "table_actions",
        ],
    },
    {
        "name": "register_output_renderer.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": ["register_output_renderer"],
    },
    {
        "name": "sleep_sql_function.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": ["prepare_connection"],
    },
    {
        "name": "view_name.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": ["extra_template_vars"],
    },
]


@contextlib.contextmanager
def make_app_client(
    sql_time_limit_ms=None,
    max_returned_rows=None,
    cors=False,
    memory=False,
    settings=None,
    filename="fixtures.db",
    is_immutable=False,
    extra_databases=None,
    inspect_data=None,
    static_mounts=None,
    template_dir=None,
    config=None,
    metadata=None,
    crossdb=False,
):
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, filename)
        if is_immutable:
            files = []
            immutables = [filepath]
        else:
            files = [filepath]
            immutables = []
        conn = sqlite3.connect(filepath)
        conn.executescript(TABLES)
        for sql, params in TABLE_PARAMETERIZED_SQL:
            with conn:
                conn.execute(sql, params)
        # Close the connection to avoid "too many open files" errors
        conn.close()
        if extra_databases is not None:
            for extra_filename, extra_sql in extra_databases.items():
                extra_filepath = os.path.join(tmpdir, extra_filename)
                c2 = sqlite3.connect(extra_filepath)
                c2.executescript(extra_sql)
                c2.close()
                # Insert at start to help test /-/databases ordering:
                files.insert(0, extra_filepath)
        os.chdir(os.path.dirname(filepath))
        settings = settings or {}
        for key, value in {
            "default_page_size": 50,
            "max_returned_rows": max_returned_rows or 100,
            "sql_time_limit_ms": sql_time_limit_ms or 200,
            # Default is 3 but this results in "too many open files"
            # errors when running the full test suite:
            "num_sql_threads": 1,
        }.items():
            if key not in settings:
                settings[key] = value
        ds = Datasette(
            files,
            immutables=immutables,
            memory=memory,
            cors=cors,
            metadata=metadata or METADATA,
            config=config or CONFIG,
            plugins_dir=PLUGINS_DIR,
            settings=settings,
            inspect_data=inspect_data,
            static_mounts=static_mounts,
            template_dir=template_dir,
            crossdb=crossdb,
        )
        yield TestClient(ds)
        # Close as many database connections as possible
        # to try and avoid too many open files error
        for db in ds.databases.values():
            if not db.is_memory:
                db.close()


@pytest.fixture(scope="session")
def app_client():
    with make_app_client() as client:
        yield client


@pytest.fixture(scope="session")
def app_client_no_files():
    ds = Datasette([])
    yield TestClient(ds)


@pytest.fixture(scope="session")
def app_client_base_url_prefix():
    with make_app_client(settings={"base_url": "/prefix/"}) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_two_attached_databases():
    with make_app_client(
        extra_databases={"extra database.db": EXTRA_DATABASE_SQL}
    ) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_two_attached_databases_crossdb_enabled():
    with make_app_client(
        extra_databases={"extra database.db": EXTRA_DATABASE_SQL},
        crossdb=True,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_conflicting_database_names():
    with make_app_client(
        extra_databases={"foo.db": EXTRA_DATABASE_SQL, "foo-bar.db": EXTRA_DATABASE_SQL}
    ) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_two_attached_databases_one_immutable():
    with make_app_client(
        is_immutable=True, extra_databases={"extra database.db": EXTRA_DATABASE_SQL}
    ) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_with_trace():
    with make_app_client(settings={"trace_debug": True}, is_immutable=True) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_shorter_time_limit():
    with make_app_client(20) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_returned_rows_matches_page_size():
    with make_app_client(max_returned_rows=50) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_larger_cache_size():
    with make_app_client(settings={"cache_size_kb": 2500}) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_csv_max_mb_one():
    with make_app_client(settings={"max_csv_mb": 1}) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_with_dot():
    with make_app_client(filename="fixtures.dot.db") as client:
        yield client


@pytest.fixture(scope="session")
def app_client_with_cors():
    with make_app_client(is_immutable=True, cors=True) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_immutable_and_inspect_file():
    inspect_data = {"fixtures": {"tables": {"sortable": {"count": 100}}}}
    with make_app_client(is_immutable=True, inspect_data=inspect_data) as client:
        yield client


def generate_compound_rows(num):
    for a, b, c in itertools.islice(
        itertools.product(string.ascii_lowercase, repeat=3), num
    ):
        yield a, b, c, f"{a}-{b}-{c}"


def generate_sortable_rows(num):
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
CONFIG = {
  "plugins": {
      "name-of-plugin": {"depth": "root"},
      "env-plugin": {"foo": {"$env": "FOO_ENV"}},
      "env-plugin-list": [{"in_a_list": {"$env": "FOO_ENV"}}],
      "file-plugin": {"foo": {"$file": TEMP_PLUGIN_SECRET_FILE}},
  },
}

METADATA = {
    "title": "Datasette Fixtures",
    "description_html": 'An example SQLite database demonstrating Datasette. <a href="/login-as-root">Sign in as root user</a>',
    "license": "Apache License 2.0",
    "license_url": "https://github.com/simonw/datasette/blob/main/LICENSE",
    "source": "tests/fixtures.py",
    "source_url": "https://github.com/simonw/datasette/blob/main/tests/fixtures.py",
    "about": "About Datasette",
    "about_url": "https://github.com/simonw/datasette",
    "extra_css_urls": ["/static/extra-css-urls.css"],
    "databases": {
        "fixtures": {
            "description": "Test tables description",
            "plugins": {"name-of-plugin": {"depth": "database"}},
            "tables": {
                "simple_primary_key": {
                    "description_html": "Simple <em>primary</em> key",
                    "title": "This <em>HTML</em> is escaped",
                    "plugins": {
                        "name-of-plugin": {
                            "depth": "table",
                            "special": "this-is-simple_primary_key",
                        }
                    },
                },
                "sortable": {
                    "sortable_columns": [
                        "sortable",
                        "sortable_with_nulls",
                        "sortable_with_nulls_2",
                        "text",
                    ],
                    "plugins": {"name-of-plugin": {"depth": "table"}},
                },
                "no_primary_key": {"sortable_columns": [], "hidden": True},
                "units": {"units": {"distance": "m", "frequency": "Hz"}},
                "primary_key_multiple_columns_explicit_label": {
                    "label_column": "content2"
                },
                "simple_view": {"sortable_columns": ["content"]},
                "searchable_view_configured_by_metadata": {
                    "fts_table": "searchable_fts",
                    "fts_pk": "pk",
                },
                "roadside_attractions": {
                    "columns": {
                        "name": "The name of the attraction",
                        "address": "The street address for the attraction",
                    }
                },
                "attraction_characteristic": {"sort_desc": "pk"},
                "facet_cities": {"sort": "name"},
                "paginated_view": {"size": 25},
            },
            "queries": {
                "𝐜𝐢𝐭𝐢𝐞𝐬": "select id, name from facet_cities order by id limit 1;",
                "pragma_cache_size": "PRAGMA cache_size;",
                "magic_parameters": {
                    "sql": "select :_header_user_agent as user_agent, :_now_datetime_utc as datetime",
                },
                "neighborhood_search": {
                    "sql": textwrap.dedent(
                        """
                        select _neighborhood, facet_cities.name, state
                        from facetable
                            join facet_cities
                                on facetable._city_id = facet_cities.id
                        where _neighborhood like '%' || :text || '%'
                        order by _neighborhood;
                    """
                    ),
                    "title": "Search neighborhoods",
                    "description_html": "<b>Demonstrating</b> simple like search",
                    "fragment": "fragment-goes-here",
                    "hide_sql": True,
                },
            },
        }
    },
}

TABLES = (
    """
CREATE TABLE simple_primary_key (
  id varchar(30) primary key,
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
  foreign_key_with_label varchar(30),
  foreign_key_with_blank_label varchar(30),
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
  f1 text,
  f2 text,
  f3 text,
  FOREIGN KEY ("f1") REFERENCES [simple_primary_key](id),
  FOREIGN KEY ("f2") REFERENCES [simple_primary_key](id),
  FOREIGN KEY ("f3") REFERENCES [simple_primary_key](id)
);

CREATE TABLE "custom_foreign_key_label" (
  pk varchar(30) primary key,
  foreign_key_with_custom_label text,
  FOREIGN KEY ("foreign_key_with_custom_label") REFERENCES [primary_key_multiple_columns_explicit_label](id)
);

CREATE TABLE units (
  pk integer primary key,
  distance int,
  frequency int
);

INSERT INTO units VALUES (1, 1, 100);
INSERT INTO units VALUES (2, 5000, 2500);
INSERT INTO units VALUES (3, 100000, 75000);

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
    USING FTS4 (text1, text2, [name with . and spaces], content="searchable");
INSERT INTO "searchable_fts" (rowid, text1, text2, [name with . and spaces])
    SELECT rowid, text1, text2, [name with . and spaces] FROM searchable;

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
    + "\n".join(
        [
            'INSERT INTO compound_three_primary_keys VALUES ("{a}", "{b}", "{c}", "{content}");'.format(
                a=a, b=b, c=c, content=content
            )
            for a, b, c, content in generate_compound_rows(1001)
        ]
    )
    + "\n".join(
        [
            """INSERT INTO sortable VALUES (
        "{pk1}", "{pk2}", "{content}", {sortable},
        {sortable_with_nulls}, {sortable_with_nulls_2}, "{text}");
    """.format(
                **row
            ).replace(
                "None", "null"
            )
            for row in generate_sortable_rows(201)
        ]
    )
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


def assert_permissions_checked(datasette, actions):
    # actions is a list of "action" or (action, resource) tuples
    for action in actions:
        if isinstance(action, str):
            resource = None
        else:
            action, resource = action
        assert [
            pc
            for pc in datasette._permission_checks
            if pc["action"] == action and pc["resource"] == resource
        ], """Missing expected permission check: action={}, resource={}
        Permission checks seen: {}
        """.format(
            action,
            resource,
            json.dumps(list(datasette._permission_checks), indent=4),
        )


@click.command()
@click.argument(
    "db_filename",
    default="fixtures.db",
    type=click.Path(file_okay=True, dir_okay=False),
)
@click.argument("metadata", required=False)
@click.argument("config", required=False)
@click.argument(
    "plugins_path", type=click.Path(file_okay=False, dir_okay=True), required=False
)
@click.option(
    "--recreate",
    is_flag=True,
    default=False,
    help="Delete and recreate database if it exists",
)
@click.option(
    "--extra-db-filename",
    type=click.Path(file_okay=True, dir_okay=False),
    help="Write out second test DB to this file",
)
def cli(db_filename, config, metadata, plugins_path, recreate, extra_db_filename):
    """Write out the fixtures database used by Datasette's test suite"""
    if metadata and not metadata.endswith(".json"):
        raise click.ClickException("Metadata should end with .json")
    if not db_filename.endswith(".db"):
        raise click.ClickException("Database file should end with .db")
    if pathlib.Path(db_filename).exists():
        if not recreate:
            raise click.ClickException(
                f"{db_filename} already exists, use --recreate to reset it"
            )
        else:
            pathlib.Path(db_filename).unlink()
    conn = sqlite3.connect(db_filename)
    conn.executescript(TABLES)
    for sql, params in TABLE_PARAMETERIZED_SQL:
        with conn:
            conn.execute(sql, params)
    print(f"Test tables written to {db_filename}")
    if metadata:
        with open(metadata, "w") as fp:
            fp.write(json.dumps(METADATA, indent=4))
        print(f"- metadata written to {metadata}")
    if config:
        with open(config, "w") as fp:
            fp.write(json.dumps(CONFIG, indent=4))
        print(f"- config written to {config}")
    if plugins_path:
        path = pathlib.Path(plugins_path)
        if not path.exists():
            path.mkdir()
        test_plugins = pathlib.Path(__file__).parent / "plugins"
        for filepath in test_plugins.glob("*.py"):
            newpath = path / filepath.name
            newpath.write_text(filepath.read_text())
            print(f"  Wrote plugin: {newpath}")
    if extra_db_filename:
        if pathlib.Path(extra_db_filename).exists():
            if not recreate:
                raise click.ClickException(
                    f"{extra_db_filename} already exists, use --recreate to reset it"
                )
            else:
                pathlib.Path(extra_db_filename).unlink()
        conn = sqlite3.connect(extra_db_filename)
        conn.executescript(EXTRA_DATABASE_SQL)
        print(f"Test tables written to {extra_db_filename}")


if __name__ == "__main__":
    cli()
