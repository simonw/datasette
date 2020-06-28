from datasette.app import Datasette
from datasette.utils import sqlite3, MultiParams
from asgiref.testing import ApplicationCommunicator
from asgiref.sync import async_to_sync
import click
import contextlib
from http.cookies import SimpleCookie
import itertools
import json
import os
import pathlib
import pytest
import random
import sys
import string
import tempfile
import textwrap
import time
from urllib.parse import unquote, quote, urlencode


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
            "extra_body_script",
            "extra_css_urls",
            "extra_js_urls",
            "extra_template_vars",
            "permission_allowed",
            "prepare_connection",
            "prepare_jinja2_environment",
            "register_facet_classes",
            "register_magic_parameters",
            "register_routes",
            "render_cell",
            "startup",
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
            "permission_allowed",
            "render_cell",
            "startup",
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
        "name": "view_name.py",
        "static": False,
        "templates": False,
        "version": None,
        "hooks": ["extra_template_vars"],
    },
]


class TestResponse:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def cookies(self):
        cookie = SimpleCookie()
        cookie.load(self.headers.get("set-cookie") or "")
        return {key: value.value for key, value in cookie.items()}

    @property
    def json(self):
        return json.loads(self.text)

    @property
    def text(self):
        return self.body.decode("utf8")


class TestClient:
    max_redirects = 5

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    def actor_cookie(self, actor):
        return self.ds.sign({"a": actor}, "actor")

    @async_to_sync
    async def get(
        self, path, allow_redirects=True, redirect_count=0, method="GET", cookies=None
    ):
        return await self._request(
            path, allow_redirects, redirect_count, method, cookies
        )

    @async_to_sync
    async def post(
        self,
        path,
        post_data=None,
        allow_redirects=True,
        redirect_count=0,
        content_type="application/x-www-form-urlencoded",
        cookies=None,
        csrftoken_from=None,
    ):
        cookies = cookies or {}
        post_data = post_data or {}
        # Maybe fetch a csrftoken first
        if csrftoken_from is not None:
            if csrftoken_from is True:
                csrftoken_from = path
            token_response = await self._request(csrftoken_from, cookies=cookies)
            csrftoken = token_response.cookies["ds_csrftoken"]
            cookies["ds_csrftoken"] = csrftoken
            post_data["csrftoken"] = csrftoken
        return await self._request(
            path,
            allow_redirects,
            redirect_count,
            "POST",
            cookies,
            post_data,
            content_type,
        )

    async def _request(
        self,
        path,
        allow_redirects=True,
        redirect_count=0,
        method="GET",
        cookies=None,
        post_data=None,
        content_type=None,
    ):
        query_string = b""
        if "?" in path:
            path, _, query_string = path.partition("?")
            query_string = query_string.encode("utf8")
        if "%" in path:
            raw_path = path.encode("latin-1")
        else:
            raw_path = quote(path, safe="/:,").encode("latin-1")
        headers = [[b"host", b"localhost"]]
        if content_type:
            headers.append((b"content-type", content_type.encode("utf-8")))
        if cookies:
            sc = SimpleCookie()
            for key, value in cookies.items():
                sc[key] = value
            headers.append([b"cookie", sc.output(header="").encode("utf-8")])
        scope = {
            "type": "http",
            "http_version": "1.0",
            "method": method,
            "path": unquote(path),
            "raw_path": raw_path,
            "query_string": query_string,
            "headers": headers,
        }
        instance = ApplicationCommunicator(self.asgi_app, scope)

        if post_data:
            body = urlencode(post_data, doseq=True).encode("utf-8")
            await instance.send_input({"type": "http.request", "body": body})
        else:
            await instance.send_input({"type": "http.request"})

        # First message back should be response.start with headers and status
        messages = []
        start = await instance.receive_output(2)
        messages.append(start)
        assert start["type"] == "http.response.start"
        response_headers = MultiParams(
            [(k.decode("utf8"), v.decode("utf8")) for k, v in start["headers"]]
        )
        status = start["status"]
        # Now loop until we run out of response.body
        body = b""
        while True:
            message = await instance.receive_output(2)
            messages.append(message)
            assert message["type"] == "http.response.body"
            body += message["body"]
            if not message.get("more_body"):
                break
        response = TestResponse(status, response_headers, body)
        if allow_redirects and response.status in (301, 302):
            assert (
                redirect_count < self.max_redirects
            ), "Redirected {} times, max_redirects={}".format(
                redirect_count, self.max_redirects
            )
            location = response.headers["Location"]
            return await self._request(
                location, allow_redirects=True, redirect_count=redirect_count + 1
            )
        return response


@contextlib.contextmanager
def make_app_client(
    sql_time_limit_ms=None,
    max_returned_rows=None,
    cors=False,
    memory=False,
    config=None,
    filename="fixtures.db",
    is_immutable=False,
    extra_databases=None,
    inspect_data=None,
    static_mounts=None,
    template_dir=None,
    metadata=None,
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
        if extra_databases is not None:
            for extra_filename, extra_sql in extra_databases.items():
                extra_filepath = os.path.join(tmpdir, extra_filename)
                sqlite3.connect(extra_filepath).executescript(extra_sql)
                files.append(extra_filepath)
        os.chdir(os.path.dirname(filepath))
        config = config or {}
        config.update(
            {
                "default_page_size": 50,
                "max_returned_rows": max_returned_rows or 100,
                "sql_time_limit_ms": sql_time_limit_ms or 200,
                # Default is 3 but this results in "too many open files"
                # errors when running the full test suite:
                "num_sql_threads": 1,
            }
        )
        ds = Datasette(
            files,
            immutables=immutables,
            memory=memory,
            cors=cors,
            metadata=metadata or METADATA,
            plugins_dir=PLUGINS_DIR,
            config=config,
            inspect_data=inspect_data,
            static_mounts=static_mounts,
            template_dir=template_dir,
        )
        ds.sqlite_functions.append(("sleep", 1, lambda n: time.sleep(float(n))))
        client = TestClient(ds.app())
        client.ds = ds
        yield client


@pytest.fixture(scope="session")
def app_client():
    with make_app_client() as client:
        yield client


@pytest.fixture(scope="session")
def app_client_no_files():
    ds = Datasette([])
    client = TestClient(ds.app())
    client.ds = ds
    yield client


@pytest.fixture(scope="session")
def app_client_two_attached_databases():
    with make_app_client(
        extra_databases={"extra database.db": EXTRA_DATABASE_SQL}
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
def app_client_with_hash():
    with make_app_client(config={"hash_urls": True}, is_immutable=True) as client:
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
    with make_app_client(config={"cache_size_kb": 2500}) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_csv_max_mb_one():
    with make_app_client(config={"max_csv_mb": 1}) as client:
        yield client


@pytest.fixture(scope="session")
def app_client_with_dot():
    with make_app_client(filename="fixtures.dot.db") as client:
        yield client


@pytest.fixture(scope="session")
def app_client_with_cors():
    with make_app_client(cors=True) as client:
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
        yield a, b, c, "{}-{}-{}".format(a, b, c)


def generate_sortable_rows(num):
    rand = random.Random(42)
    for a, b in itertools.islice(
        itertools.product(string.ascii_lowercase, repeat=2), num
    ):
        yield {
            "pk1": a,
            "pk2": b,
            "content": "{}-{}".format(a, b),
            "sortable": rand.randint(-100, 100),
            "sortable_with_nulls": rand.choice([None, rand.random(), rand.random()]),
            "sortable_with_nulls_2": rand.choice([None, rand.random(), rand.random()]),
            "text": rand.choice(["$null", "$blah"]),
        }


METADATA = {
    "title": "Datasette Fixtures",
    "description": "An example SQLite database demonstrating Datasette",
    "license": "Apache License 2.0",
    "license_url": "https://github.com/simonw/datasette/blob/master/LICENSE",
    "source": "tests/fixtures.py",
    "source_url": "https://github.com/simonw/datasette/blob/master/tests/fixtures.py",
    "about": "About Datasette",
    "about_url": "https://github.com/simonw/datasette",
    "plugins": {
        "name-of-plugin": {"depth": "root"},
        "env-plugin": {"foo": {"$env": "FOO_ENV"}},
        "env-plugin-list": [{"in_a_list": {"$env": "FOO_ENV"}}],
        "file-plugin": {"foo": {"$file": TEMP_PLUGIN_SECRET_FILE}},
    },
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
                "attraction_characteristic": {"sort_desc": "pk"},
                "facet_cities": {"sort": "name"},
                "paginated_view": {"size": 25},
            },
            "queries": {
                "𝐜𝐢𝐭𝐢𝐞𝐬": "select id, name from facet_cities order by id limit 1;",
                "pragma_cache_size": "PRAGMA cache_size;",
                "magic_parameters": "select :_header_user_agent as user_agent, :_timestamp_datetime_utc as datetime",
                "neighborhood_search": {
                    "sql": textwrap.dedent(
                        """
                        select neighborhood, facet_cities.name, state
                        from facetable
                            join facet_cities
                                on facetable.city_id = facet_cities.id
                        where neighborhood like '%' || :text || '%'
                        order by neighborhood;
                    """
                    ),
                    "title": "Search neighborhoods",
                    "description_html": "<b>Demonstrating</b> simple like search",
                    "fragment": "fragment-goes-here",
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
    USING FTS3 (text1, text2, [name with . and spaces], content="searchable");
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
    city_id integer,
    neighborhood text,
    tags text,
    complex_array text,
    distinct_some_null,
    FOREIGN KEY ("city_id") REFERENCES [facet_cities](id)
);
INSERT INTO facetable
    (created, planet_int, on_earth, state, city_id, neighborhood, tags, complex_array, distinct_some_null)
VALUES
    ("2019-01-14 08:00:00", 1, 1, 'CA', 1, 'Mission', '["tag1", "tag2"]', '[{"foo": "bar"}]', 'one'),
    ("2019-01-14 08:00:00", 1, 1, 'CA', 1, 'Dogpatch', '["tag1", "tag3"]', '[]', 'two'),
    ("2019-01-14 08:00:00", 1, 1, 'CA', 1, 'SOMA', '[]', '[]', null),
    ("2019-01-14 08:00:00", 1, 1, 'CA', 1, 'Tenderloin', '[]', '[]', null),
    ("2019-01-15 08:00:00", 1, 1, 'CA', 1, 'Bernal Heights', '[]', '[]', null),
    ("2019-01-15 08:00:00", 1, 1, 'CA', 1, 'Hayes Valley', '[]', '[]', null),
    ("2019-01-15 08:00:00", 1, 1, 'CA', 2, 'Hollywood', '[]', '[]', null),
    ("2019-01-15 08:00:00", 1, 1, 'CA', 2, 'Downtown', '[]', '[]', null),
    ("2019-01-16 08:00:00", 1, 1, 'CA', 2, 'Los Feliz', '[]', '[]', null),
    ("2019-01-16 08:00:00", 1, 1, 'CA', 2, 'Koreatown', '[]', '[]', null),
    ("2019-01-16 08:00:00", 1, 1, 'MI', 3, 'Downtown', '[]', '[]', null),
    ("2019-01-17 08:00:00", 1, 1, 'MI', 3, 'Greektown', '[]', '[]', null),
    ("2019-01-17 08:00:00", 1, 1, 'MI', 3, 'Corktown', '[]', '[]', null),
    ("2019-01-17 08:00:00", 1, 1, 'MI', 3, 'Mexicantown', '[]', '[]', null),
    ("2019-01-17 08:00:00", 2, 0, 'MC', 4, 'Arcadia Planitia', '[]', '[]', null)
;

CREATE TABLE binary_data (
    data BLOB
);

-- Many 2 Many demo: roadside attractions!

CREATE TABLE roadside_attractions (
    pk integer primary key,
    name text,
    address text,
    latitude real,
    longitude real
);
INSERT INTO roadside_attractions VALUES (
    1, "The Mystery Spot", "465 Mystery Spot Road, Santa Cruz, CA 95065",
    37.0167, -122.0024
);
INSERT INTO roadside_attractions VALUES (
    2, "Winchester Mystery House", "525 South Winchester Boulevard, San Jose, CA 95128",
    37.3184, -121.9511
);
INSERT INTO roadside_attractions VALUES (
    3, "Burlingame Museum of PEZ Memorabilia", "214 California Drive, Burlingame, CA 94010",
    37.5793, -122.3442
);
INSERT INTO roadside_attractions VALUES (
    4, "Bigfoot Discovery Museum", "5497 Highway 9, Felton, CA 95018",
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

INSERT INTO primary_key_multiple_columns VALUES (1, 'hey', 'world');
INSERT INTO primary_key_multiple_columns_explicit_label VALUES (1, 'hey', 'world2');

INSERT INTO foreign_key_references VALUES (1, 1, 1);
INSERT INTO foreign_key_references VALUES (2, null, null);

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
    ("insert into binary_data (data) values (?);", [b"this is binary data"])
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
            action, resource, json.dumps(list(datasette._permission_checks), indent=4),
        )


@click.command()
@click.argument(
    "db_filename",
    default="fixtures.db",
    type=click.Path(file_okay=True, dir_okay=False),
)
@click.argument("metadata", required=False)
@click.argument(
    "plugins_path", type=click.Path(file_okay=False, dir_okay=True), required=False
)
@click.option(
    "--recreate",
    is_flag=True,
    default=False,
    help="Delete and recreate database if it exists",
)
def cli(db_filename, metadata, plugins_path, recreate):
    "Write out the fixtures database used by Datasette's test suite"
    if metadata and not metadata.endswith(".json"):
        raise click.ClickException("Metadata should end with .json")
    if not db_filename.endswith(".db"):
        raise click.ClickException("Database file should end with .db")
    if pathlib.Path(db_filename).exists():
        if not recreate:
            raise click.ClickException(
                "{} already exists, use --recreate to reset it".format(db_filename)
            )
        else:
            pathlib.Path(db_filename).unlink()
    conn = sqlite3.connect(db_filename)
    conn.executescript(TABLES)
    for sql, params in TABLE_PARAMETERIZED_SQL:
        with conn:
            conn.execute(sql, params)
    print("Test tables written to {}".format(db_filename))
    if metadata:
        open(metadata, "w").write(json.dumps(METADATA, indent=4))
        print("- metadata written to {}".format(metadata))
    if plugins_path:
        path = pathlib.Path(plugins_path)
        if not path.exists():
            path.mkdir()
        test_plugins = pathlib.Path(__file__).parent / "plugins"
        for filepath in test_plugins.glob("*.py"):
            newpath = path / filepath.name
            newpath.write_text(filepath.open().read())
            print("  Wrote plugin: {}".format(newpath))


if __name__ == "__main__":
    cli()
