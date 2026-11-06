"""
Tests for loading CSV, TSV, JSON, URLs and stdin from the command line:
``datasette data.csv``, see datasette/imports/sources.py and scratch.py.
"""

import gzip
import html
import http.server
import json
import os
import sqlite3
import threading
import typing
import urllib.parse

import pytest
from click.testing import CliRunner

from datasette.cli import cli, serve
from datasette.imports import scratch as scratch_module
from datasette.imports.scratch import (
    ReloadSession,
    ScratchDatabase,
    TableExists,
    plan_loads,
)
from datasette.imports.sources import (
    Source,
    SourceError,
    assign_table_names,
    is_sqlite_file,
    maybe_source,
    parse_source,
    sanitize_table_name,
    save_command,
    suggested_table_name,
    table_name_for_url,
)
from datasette.utils.asgi import Forbidden

FORMATS = ["csv", "tsv", "json", "ndjson"]

CSV = "id,name,score\n1,Cleo,4.5\n2,Pancakes,3\n3,Fido,\n"
NDJSON = '{"id": 1, "owner": "Ann"}\n{"id": 2, "owner": "Bob"}\n'
JSON = json.dumps([{"id": 1, "city": "Lima"}, {"id": 3, "city": "Oslo"}])


# Argument parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    (
        ("data.csv", ("file", "data.csv", None, None)),
        ("events:data.csv", ("file", "data.csv", "events", None)),
        ("./b.json", ("file", "./b.json", None, None)),
        ("stats:./b.json", ("file", "./b.json", "stats", None)),
        (
            "https://example.com/a.csv",
            ("url", "https://example.com/a.csv", None, None),
        ),
        (
            "events:https://example.com/a.csv",
            ("url", "https://example.com/a.csv", "events", None),
        ),
        (
            "http://localhost:8000/x?y=1",
            ("url", "http://localhost:8000/x?y=1", None, None),
        ),
        # Windows drive letters are not table names
        ("C:\\data\\events.csv", ("file", "C:\\data\\events.csv", None, None)),
        ("t:C:\\events.csv", ("file", "C:\\events.csv", "t", None)),
        # A one-letter table name is fine when no slash follows
        ("e:events.csv", ("file", "events.csv", "e", None)),
        # stdin
        ("-", ("stdin", "-", None, None)),
        ("stdin", ("stdin", "-", None, None)),
        ("events:-", ("stdin", "-", "events", None)),
        # Format suffixes
        ("-:csv", ("stdin", "-", None, "csv")),
        ("stdin:CSV", ("stdin", "-", None, "csv")),
        ("data.txt:tsv", ("file", "data.txt", None, "tsv")),
        ("events:data.txt:csv", ("file", "data.txt", "events", "csv")),
        ("x.log:nl", ("file", "x.log", None, "ndjson")),
        ("x.log:jsonl", ("file", "x.log", None, "ndjson")),
        (
            "https://example.com/export:json",
            ("url", "https://example.com/export", None, "json"),
        ),
        # Not a known format, so it is a table name
        ("data:foo.csv", ("file", "foo.csv", "data", None)),
        # A table name can't contain a slash, so this is a path
        ("dir/a:b.csv", ("file", "dir/a:b.csv", None, None)),
    ),
)
def test_parse_source(spec, expected):
    source = parse_source(spec, FORMATS)
    assert (source.kind, source.location, source.name, source.format) == expected
    assert source.spec == spec


@pytest.mark.parametrize(
    "spec,message",
    (
        (":data.csv", "table name before ':' is empty"),
        ("events:", "nothing to load"),
        ("ftp://example.com/a.csv", "ftp:// URLs are not supported"),
        ("t:file:///tmp/a.csv", "file:// URLs are not supported"),
    ),
)
def test_parse_source_errors(spec, message):
    with pytest.raises(SourceError) as ex:
        parse_source(spec, FORMATS)
    assert message in str(ex.value)


def test_forward_slash_drive_letters_only_on_windows():
    windows = parse_source("c:/data/events.csv", FORMATS, windows=True)
    assert (windows.location, windows.name) == ("c:/data/events.csv", None)
    posix = parse_source("t:/data/events.csv", FORMATS, windows=False)
    assert (posix.location, posix.name) == ("/data/events.csv", "t")
    # A backslash after the letter is always a drive
    backslash = parse_source("C:\\data.csv", FORMATS, windows=False)
    assert (backslash.location, backslash.name) == ("C:\\data.csv", None)


def test_format_suffix_needs_known_format():
    # geojson is not a format until a plugin registers it
    assert parse_source("a.json:geojson", FORMATS).name == "a.json"
    source = parse_source("a.json:geojson", FORMATS + ["geojson"])
    assert (source.location, source.format) == ("a.json", "geojson")


@pytest.mark.parametrize(
    "name,expected",
    (
        ("events", "events"),
        ("events-100k", "events_100k"),
        ("My Data (2024)", "My_Data_2024"),
        ("café", "café"),
        ("2024", "2024"),
        ("--", "data"),
        ("😀", "data"),
        ("sqlite_stat1", "_sqlite_stat1"),
        ("a.b.c", "a_b_c"),
    ),
)
def test_sanitize_table_name(name, expected):
    assert sanitize_table_name(name) == expected


@pytest.mark.parametrize(
    "url,expected",
    (
        ("https://example.com/data/events.csv", "events"),
        ("https://example.com/data/events.csv?token=abc#x", "events"),
        ("https://example.com/my%20data.json", "my_data"),
        ("https://example.com/", "example_com"),
        ("https://example.com", "example_com"),
        ("https://example.com/api/v1/events", "example_com"),
        ("https://example.com/api/v1/events/", "example_com"),
        ("http://localhost:8000/download?id=3", "localhost"),
        ("https://user:pw@Data.Example.com:8443/x", "data_example_com"),
    ),
)
def test_table_name_for_url(url, expected):
    assert table_name_for_url(url) == expected


def _named(*specs):
    sources = [parse_source(spec, FORMATS) for spec in specs]
    assign_table_names(sources)
    return [source.table for source in sources]


def test_assign_table_names():
    assert _named("a/events.csv", "b/events.csv", "c/events.json") == [
        "events",
        "events_2",
        "events_3",
    ]
    assert _named(
        "https://example.com/", "https://example.com/api", "http://example.com"
    ) == ["example_com", "example_com_2", "example_com_3"]
    assert _named("-") == ["stdin"]
    assert _named("x.csv:csv", "-:csv") == ["x", "stdin"]
    # Explicit names are never renamed: a later explicit name wins
    assert _named("events.csv", "events:other.csv") == ["events_2", "events"]
    # SQLite table names are case-insensitive
    assert _named("Events.csv", "events.json") == ["Events", "events_2"]
    assert _named("C:\\data\\events.csv") == ["events"]


def test_assign_table_names_errors():
    with pytest.raises(SourceError) as ex:
        _named("t:a.csv", "T:b.csv")
    assert "would both create a table called 'T'" in str(ex.value)
    with pytest.raises(SourceError) as ex:
        _named("sqlite_master:a.csv")
    assert "reserved by SQLite" in str(ex.value)


def test_suggested_table_name_for_stdin():
    assert suggested_table_name(Source("-", "stdin", "-")) == "stdin"


def test_is_sqlite_file(tmp_path):
    db = tmp_path / "x.anything"
    sqlite3.connect(db).execute("create table t (id integer)").connection.close()
    assert is_sqlite_file(str(db))
    (tmp_path / "empty.db").write_bytes(b"")
    (tmp_path / "empty").write_bytes(b"")
    (tmp_path / "empty.csv").write_bytes(b"")
    (tmp_path / "data.db").write_text(CSV)
    assert is_sqlite_file(str(tmp_path / "empty.db"))
    assert is_sqlite_file(str(tmp_path / "empty"))
    assert not is_sqlite_file(str(tmp_path / "empty.csv"))
    # Content wins over the extension
    assert not is_sqlite_file(str(tmp_path / "data.db"))
    assert not is_sqlite_file(str(tmp_path))
    assert not is_sqlite_file(str(tmp_path / "missing.db"))


def test_maybe_source(tmp_path):
    (tmp_path / "a.csv").write_text(CSV)
    sqlite3.connect(tmp_path / "a.db").execute("vacuum").connection.close()
    assert maybe_source(str(tmp_path / "a.csv"))
    assert not maybe_source(str(tmp_path / "a.db"))
    assert not maybe_source(str(tmp_path))
    assert not maybe_source(str(tmp_path / "missing.db"))
    assert maybe_source("-")
    assert maybe_source("https://example.com/")
    assert maybe_source("t:" + str(tmp_path / "a.csv"))


def test_save_command_quoting():
    sources = [
        Source("my file.csv", "file", "my file.csv"),
        Source("https://e.com/a.csv?x=1&y=2", "url", "https://e.com/a.csv?x=1&y=2"),
        Source("-", "stdin", "-"),
        Source("t:it's.csv", "file", "it's.csv", name="t"),
    ]
    assert save_command(sources) == (
        "datasette 'my file.csv' 'https://e.com/a.csv?x=1&y=2' - "
        "'t:it'\"'\"'s.csv' --save data.db"
    )


# End to end with --get --------------------------------------------------------------


def run(args, input=None):
    runner = CliRunner()
    return runner.invoke(cli, args, input=input, catch_exceptions=False)


def get_json(args, path, input=None):
    result = run(list(args) + ["--get", path], input=input)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "dogs.csv").write_text(CSV)
    (tmp_path / "dogs.tsv").write_text(CSV.replace(",", "\t"))
    (tmp_path / "owners.ndjson").write_text(NDJSON)
    (tmp_path / "cities.json").write_text(JSON)
    return tmp_path


def test_csv(data_dir):
    rows = get_json([str(data_dir / "dogs.csv")], "/scratch/dogs.json?_shape=array")
    assert rows == [
        {"rowid": 1, "id": 1, "name": "Cleo", "score": 4.5},
        {"rowid": 2, "id": 2, "name": "Pancakes", "score": 3.0},
        {"rowid": 3, "id": 3, "name": "Fido", "score": None},
    ]


@pytest.mark.parametrize(
    "filename,table,first",
    (
        ("dogs.tsv", "dogs", {"id": 1, "name": "Cleo", "score": 4.5}),
        ("owners.ndjson", "owners", {"id": 1, "owner": "Ann"}),
        ("cities.json", "cities", {"id": 1, "city": "Lima"}),
    ),
)
def test_formats(data_dir, filename, table, first):
    rows = get_json([str(data_dir / filename)], f"/scratch/{table}.json?_shape=array")
    assert {k: v for k, v in rows[0].items() if k != "rowid"} == first


def test_stderr_summary(data_dir):
    result = run([str(data_dir / "dogs.csv"), "--get", "/-/versions.json"])
    assert result.exit_code == 0
    assert "Loaded 3 rows from" in result.stderr
    assert "dogs.csv (csv) into table dogs in " in result.stderr


def test_only_scratch_database(data_dir):
    databases = get_json([str(data_dir / "dogs.csv")], "/-/databases.json")
    assert [db["name"] for db in databases["databases"]] == ["scratch"]
    with_memory = get_json(
        [str(data_dir / "dogs.csv"), "--memory"], "/-/databases.json"
    )
    assert {db["name"] for db in with_memory["databases"]} == {"scratch", "_memory"}


def test_stdin(data_dir):
    rows = get_json(["-"], "/scratch/stdin.json?_shape=array", input=CSV)
    assert [row["name"] for row in rows] == ["Cleo", "Pancakes", "Fido"]


def test_stdin_with_name_and_format():
    rows = get_json(
        ["owners:-:ndjson"], "/scratch/owners.json?_shape=array", input=NDJSON
    )
    assert [row["owner"] for row in rows] == ["Ann", "Bob"]


def test_name_prefix_and_duplicate_stems(data_dir):
    other = data_dir / "other"
    other.mkdir()
    (other / "dogs.csv").write_text("a\n1\n")
    tables = get_json(
        [
            str(data_dir / "dogs.csv"),
            str(other / "dogs.csv"),
            "people:" + str(data_dir / "owners.ndjson"),
        ],
        "/scratch.json",
    )["tables"]
    assert sorted(t["name"] for t in tables) == ["dogs", "dogs_2", "people"]


def test_mixed_sqlite_and_csv_and_join(data_dir):
    db_path = data_dir / "existing.db"
    conn = sqlite3.connect(db_path)
    conn.execute("create table things (id integer primary key)")
    conn.close()
    args = [
        str(db_path),
        str(data_dir / "dogs.csv"),
        str(data_dir / "owners.ndjson"),
    ]
    databases = get_json(args, "/-/databases.json")["databases"]
    assert {db["name"] for db in databases} == {"existing", "scratch"}
    sql = (
        "select dogs.name, owners.owner from dogs "
        "join owners on dogs.id = owners.id order by dogs.id"
    )
    rows = get_json(
        args, "/scratch/-/query.json?_shape=array&sql=" + urllib.parse.quote(sql)
    )
    assert rows == [
        {"name": "Cleo", "owner": "Ann"},
        {"name": "Pancakes", "owner": "Bob"},
    ]


def test_empty_sqlite_file_still_a_database(tmp_path):
    (tmp_path / "empty.db").write_bytes(b"")
    databases = get_json([str(tmp_path / "empty.db")], "/-/databases.json")
    assert [db["name"] for db in databases["databases"]] == ["empty"]


def test_scratch_page_shows_save_command(data_dir):
    result = run(
        [
            str(data_dir / "dogs.csv"),
            "t:" + str(data_dir / "cities.json"),
            "--get",
            "/scratch",
        ]
    )
    assert result.exit_code == 0
    assert (
        f"datasette {data_dir / 'dogs.csv'} t:{data_dir / 'cities.json'} --save data.db"
        in result.stdout
    )
    assert 'href="/scratch/dogs"' in result.stdout
    assert "pipe the same data in" not in result.stdout
    stdin_result = run(["-", "--get", "/scratch"], input=CSV)
    assert "datasette - --save data.db" in stdin_result.stdout
    assert "pipe the same data in" in stdin_result.stdout


def test_scratch_file_removed_at_close(data_dir):
    ds = ds_from_cli([str(data_dir / "dogs.csv")])
    db = ds.get_database("scratch")
    assert isinstance(db, ScratchDatabase)
    path = db.path
    assert os.path.exists(path)
    assert sqlite3.connect(path).execute("pragma journal_mode").fetchone()[0] == "wal"
    ds.close()
    assert not any(os.path.exists(path + suffix) for suffix in ("", "-wal", "-shm"))


# Errors ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,message",
    (
        (b"", "is empty"),
        (b"   \n\n", "is empty"),
        (b"\x00\x01\x02\x03binary\xff\xfe" * 100, "does not look like any format"),
    ),
)
def test_unloadable_files(tmp_path, content, message):
    path = tmp_path / "bad.csv"
    path.write_bytes(content)
    result = run([str(path), "--get", "/"])
    assert result.exit_code == 1
    assert message in result.stderr
    assert str(path) in result.stderr


def test_error_part_way_through(tmp_path):
    path = tmp_path / "bad.ndjson"
    path.write_text("".join(f'{{"id": {i}}}\n' for i in range(3000)) + "{oops\n")
    result = run([str(path), "--get", "/"])
    assert result.exit_code == 1
    assert f"Error loading {path}" in result.stderr
    assert "line 3,001" in result.stderr or "3001" in result.stderr


def test_missing_file(tmp_path):
    result = run([str(tmp_path / "missing.csv"), "--get", "/"])
    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    result = run(["t:" + str(tmp_path / "missing.csv"), "--get", "/"])
    assert result.exit_code == 1
    assert f"Path '{tmp_path / 'missing.csv'}' does not exist." in result.stderr


def test_bad_url_scheme():
    result = run(["ftp://example.com/a.csv", "--get", "/"])
    assert result.exit_code == 1
    assert "ftp:// URLs are not supported" in result.stderr


# URLs ------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    routes: typing.ClassVar[dict] = {}

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        route = self.routes.get(path)
        if route is None:
            self.send_response(404)
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"Not found")
            return
        status, headers, body = route
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def http_server():
    gzipped = gzip.compress(CSV.encode())
    _Handler.routes = {
        "/dogs.csv": (200, {"Content-Length": str(len(CSV))}, CSV.encode()),
        # Content-Type is ignored: the format comes from the content
        "/owners.txt": (200, {"Content-Type": "text/csv"}, NDJSON.encode()),
        "/api/export": (200, {}, JSON.encode()),
        "/redirect": (302, {"Location": "/dogs.csv"}, b""),
        "/gzip.csv": (
            200,
            {"Content-Encoding": "gzip", "Content-Length": str(len(gzipped))},
            gzipped,
        ),
        "/broken": (500, {}, b"Oops"),
    }
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_url(http_server):
    rows = get_json([http_server + "/dogs.csv"], "/scratch/dogs.json?_shape=array")
    assert [row["name"] for row in rows] == ["Cleo", "Pancakes", "Fido"]


def test_url_names_redirects_and_encoding(http_server):
    tables = get_json(
        [
            http_server + "/owners.txt",
            http_server + "/api/export",
            "moved:" + http_server + "/redirect",
            http_server + "/gzip.csv",
        ],
        "/scratch.json",
    )["tables"]
    counts = {t["name"]: t["count"] for t in tables}
    assert counts == {"owners": 2, "127_0_0_1": 2, "moved": 3, "gzip": 3}


def test_url_save_command_and_stderr(http_server):
    url = http_server + "/dogs.csv?a=1&b=2"
    result = run([url, "--get", "/scratch"])
    assert result.exit_code == 0
    page = html.unescape(result.stdout)
    assert f"datasette '{url}' --save data.db" in page
    assert f"Downloaded {url} (" in result.stderr


@pytest.mark.parametrize(
    "path,message",
    (("/missing.csv", "returned HTTP 404 Not Found"), ("/broken", "HTTP 500")),
)
def test_url_errors(http_server, path, message):
    result = run([http_server + path, "--get", "/"])
    assert result.exit_code == 1
    assert message in result.stderr
    assert http_server + path in result.stderr


def test_url_connection_refused():
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    result = run([f"http://127.0.0.1:{port}/a.csv", "--get", "/"])
    assert result.exit_code == 1
    assert f"Could not download http://127.0.0.1:{port}/a.csv" in result.stderr


def test_download_removes_partial_file_on_error(http_server, tmp_path):
    import asyncio

    from datasette.imports.sources import DownloadError, download

    with pytest.raises(DownloadError):
        asyncio.run(download(http_server + "/missing.csv", str(tmp_path)))
    assert list(tmp_path.iterdir()) == []


def test_mixed_inputs(http_server, data_dir):
    tables = get_json(
        [
            str(data_dir / "dogs.csv"),
            str(data_dir / "owners.ndjson"),
            http_server + "/api/export",
            "-",
        ],
        "/scratch.json",
        input="x,y\n1,2\n",
    )["tables"]
    assert {t["name"]: t["count"] for t in tables} == {
        "dogs": 3,
        "owners": 2,
        "127_0_0_1": 2,
        "stdin": 1,
    }


# --save -------------------------------------------------------------------------


def test_save_creates_file(data_dir):
    save = data_dir / "saved.db"
    databases = get_json(
        [str(data_dir / "dogs.csv"), "--save", str(save)], "/-/databases.json"
    )["databases"]
    assert [db["name"] for db in databases] == ["saved"]
    conn = sqlite3.connect(save)
    assert conn.execute("select count(*) from dogs").fetchone()[0] == 3
    # Types were detected
    assert (
        "[score] REAL"
        in conn.execute("select sql from sqlite_master where name = 'dogs'").fetchone()[
            0
        ]
        or "score"
        in conn.execute("select sql from sqlite_master where name = 'dogs'").fetchone()[
            0
        ]
    )
    assert conn.execute("select typeof(score) from dogs where id = 1").fetchone() == (
        "real",
    )


def test_save_into_existing_database_and_with_serve_options(data_dir):
    save = data_dir / "existing.db"
    conn = sqlite3.connect(save)
    conn.execute("create table things (id integer primary key)")
    conn.close()
    result = run(
        [
            str(data_dir / "dogs.csv"),
            "--save",
            str(save),
            "-s",
            "default_page_size",
            "1",
            "--get",
            "/existing/dogs.json?_shape=array",
        ]
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 1
    tables = {
        row[0]
        for row in sqlite3.connect(save).execute(
            "select name from sqlite_master where type = 'table'"
        )
    }
    assert tables == {"things", "dogs"}


def test_save_same_file_also_passed_as_database(data_dir):
    save = data_dir / "both.db"
    sqlite3.connect(save).execute("vacuum").connection.close()
    databases = get_json(
        [str(save), str(data_dir / "dogs.csv"), "--save", str(save)],
        "/-/databases.json",
    )["databases"]
    assert [db["name"] for db in databases] == ["both"]


def test_save_existing_table_is_an_error(data_dir):
    save = data_dir / "existing.db"
    conn = sqlite3.connect(save)
    conn.execute("create table dogs (id integer primary key, keep text)")
    conn.execute("insert into dogs (keep) values ('me')")
    conn.commit()
    conn.close()
    before = save.read_bytes()
    result = run(
        [
            str(data_dir / "owners.ndjson"),
            str(data_dir / "dogs.csv"),
            "--save",
            str(save),
            "--get",
            "/",
        ]
    )
    assert result.exit_code == 1
    assert "Table 'dogs' already exists in" in result.stderr
    assert "NAME:" in result.stderr
    # Nothing was loaded, not even owners
    assert save.read_bytes() == before
    # A different name works
    result = run(
        [
            "dogs2:" + str(data_dir / "dogs.csv"),
            "--save",
            str(save),
            "--get",
            "/existing/dogs2.json?_shape=array",
        ]
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 3


def test_save_is_all_or_nothing(data_dir):
    save = data_dir / "partial.db"
    bad = data_dir / "bad.ndjson"
    bad.write_text("".join(f'{{"id": {i}}}\n' for i in range(2500)) + "{oops\n")
    result = run(
        [str(data_dir / "dogs.csv"), str(bad), "--save", str(save), "--get", "/"]
    )
    assert result.exit_code == 1
    assert f"Error loading {bad}" in result.stderr
    # The file did not exist before, so it is gone again
    assert not save.exists()
    # An existing file: dogs was loaded first, then dropped again
    sqlite3.connect(save).execute("create table keep (id)").connection.close()
    result = run(
        [str(data_dir / "dogs.csv"), str(bad), "--save", str(save), "--get", "/"]
    )
    assert result.exit_code == 1
    assert scratch_module.existing_tables(str(save)) == ["keep"]


def test_save_failing_before_load_creates_nothing(data_dir):
    save = data_dir / "never.db"
    (data_dir / "empty.csv").write_text("")
    result = run(
        [str(data_dir / "dogs.csv"), str(data_dir / "empty.csv"), "--save", str(save)]
    )
    assert result.exit_code == 1
    assert "empty.csv is empty" in result.stderr
    assert not save.exists()


def test_save_refuses_non_sqlite_file(data_dir):
    result = run(
        [
            str(data_dir / "owners.ndjson"),
            "--save",
            str(data_dir / "dogs.csv"),
            "--get",
            "/",
        ]
    )
    assert result.exit_code == 1
    assert "exists and is not a SQLite database" in result.stderr
    assert (data_dir / "dogs.csv").read_text() == CSV


def test_save_needs_inputs(tmp_path):
    result = run(["--save", str(tmp_path / "x.db"), "--get", "/"])
    assert result.exit_code == 1
    assert "--save needs at least one" in result.stderr


# Read-only scratch --------------------------------------------------------------------


def ds_from_cli(args):
    ctx = serve.make_context("serve", list(args))
    return serve.callback(**ctx.params, return_instance=True)


ROOT = {"id": "root"}


# These are built outside the tests' event loop, because loading runs one
@pytest.fixture
def scratch_ds(data_dir):
    ds = ds_from_cli([str(data_dir / "dogs.csv")])
    ds.root_enabled = True
    yield ds
    ds.close()


@pytest.fixture
def config_ds(data_dir):
    config = data_dir / "datasette.json"
    config.write_text(
        json.dumps(
            {
                "databases": {
                    "scratch": {
                        "queries": {"wipe": {"sql": "delete from dogs", "write": True}}
                    }
                }
            }
        )
    )
    ds = ds_from_cli([str(data_dir / "dogs.csv"), "-c", str(config)])
    ds.root_enabled = True
    yield ds
    ds.close()


@pytest.fixture
def save_ds(data_dir):
    ds = ds_from_cli([str(data_dir / "dogs.csv"), "--save", str(data_dir / "w.db")])
    ds.root_enabled = True
    yield ds
    ds.close()


@pytest.mark.asyncio
async def test_scratch_is_read_only_even_for_root(scratch_ds):
    ds = scratch_ds
    await ds.invoke_startup()
    db = ds.get_database("scratch")
    client = ds.client
    # Reads work
    response = await client.get("/scratch/dogs.json", actor=ROOT)
    assert response.status_code == 200
    attempts = [
        ("/scratch/dogs/-/insert", {"row": {"id": 4, "name": "New"}}),
        ("/scratch/dogs/-/upsert", {"rows": [{"rowid": 1, "name": "X"}]}),
        ("/scratch/dogs/1/-/update", {"update": {"name": "X"}}),
        ("/scratch/dogs/1/-/delete", {}),
        ("/scratch/dogs/-/drop", {"confirm": True}),
        ("/scratch/dogs/-/alter", {"rename": "cats"}),
        ("/scratch/-/create", {"table": "new", "columns": [{"name": "id"}]}),
        ("/scratch/-/execute-write", {"sql": "delete from dogs"}),
    ]
    for path, body in attempts:
        response = await client.post(path, json=body, actor=ROOT)
        assert response.status_code == 403, (path, response.text)
    assert (await db.execute("select count(*) from dogs")).first()[0] == 3
    assert await db.table_names() == ["dogs"]
    for action in ("insert-row", "update-row", "delete-row", "drop-table"):
        assert not await ds.allowed(
            action=action, resource=_table("scratch", "dogs"), actor=ROOT
        )
    assert await ds.allowed(
        action="view-table", resource=_table("scratch", "dogs"), actor=ROOT
    )
    # The pages offer no write actions
    page = await client.get("/scratch/dogs", actor=ROOT)
    assert "/-/insert" not in page.text and "/-/drop" not in page.text
    database_page = await client.get("/scratch", actor=ROOT)
    assert "/scratch/-/execute-write" not in database_page.text
    # Writes that skip permission checks are refused too
    with pytest.raises(Forbidden):
        await db.execute_write("delete from dogs")
    assert (await db.execute("select count(*) from dogs")).first()[0] == 3


def _table(database, table):
    from datasette.resources import TableResource

    return TableResource(database=database, table=table)


@pytest.mark.asyncio
async def test_trusted_write_query_cannot_change_scratch(config_ds):
    ds = config_ds
    await ds.invoke_startup()
    response = await ds.client.post(
        "/scratch/wipe?_json=1", data={"x": "1"}, actor=ROOT
    )
    data = response.json()
    assert not data["ok"], data
    assert "read-only" in json.dumps(data)
    db = ds.get_database("scratch")
    assert (await db.execute("select count(*) from dogs")).first()[0] == 3


@pytest.mark.asyncio
async def test_save_database_is_writable(save_ds):
    ds = save_ds
    await ds.invoke_startup()
    response = await ds.client.post(
        "/w/dogs/-/insert", json={"row": {"id": 4, "name": "New"}}, actor=ROOT
    )
    assert response.status_code == 201, response.text


# --reload sessions ------------------------------------------------------------------


def _sources(*paths):
    sources = [Source(str(p), "file", str(p)) for p in paths]
    assign_table_names(sources)
    return sources


@pytest.mark.asyncio
async def test_reload_session_keeps_unchanged_tables(data_dir, monkeypatch):
    from datasette.app import Datasette
    from datasette.imports.scratch import load_sources

    monkeypatch.delenv(ReloadSession.ENV, raising=False)
    session = ReloadSession.start()
    try:
        csv_path = data_dir / "dogs.csv"
        json_path = data_dir / "cities.json"

        async def worker():
            # What one --reload worker process does
            ds = Datasette()
            db = ds.add_database(
                ScratchDatabase(ds, session.scratch_path, keep=True), name="scratch"
            )
            sources = _sources(csv_path, json_path)
            plan = plan_loads(session.scratch_path, sources, session=session)
            for source in plan.load:
                source.path = source.location
            results = await load_sources(
                db, plan, session=session, target_path=session.scratch_path
            )
            ds.close()
            return {r.table: r.reused for r in results}

        assert await worker() == {"dogs": False, "cities": False}
        # A code change: nothing to load again
        assert await worker() == {"dogs": True, "cities": True}
        # An input file changed: just that table is loaded again
        csv_path.write_text(CSV + "4,Rex,1\n")
        assert await worker() == {"dogs": False, "cities": True}
        conn = sqlite3.connect(session.scratch_path)
        assert conn.execute("select count(*) from dogs").fetchone()[0] == 4
        conn.close()
    finally:
        os.environ.pop(ReloadSession.ENV, None)
        scratch_module.remove_tree(session.directory)


@pytest.mark.asyncio
async def test_load_source_never_drops_a_table_it_did_not_create(data_dir):
    from datasette.app import Datasette
    from datasette.imports.scratch import load_source

    ds = Datasette()
    db = ds.add_database(ScratchDatabase(ds), name="scratch")
    try:
        with scratch_module.import_writes():
            await db.execute_write("create table dogs (keep text)")
            await db.execute_write("insert into dogs values ('me')")
        source = _sources(data_dir / "dogs.csv")[0]
        source.path = source.location
        with pytest.raises(TableExists):
            await load_source(db, source)
        assert (await db.execute("select keep from dogs")).first()[0] == "me"
        # The import's own table is dropped when loading fails part way
        bad = data_dir / "bad.ndjson"
        bad.write_text('{"a": 1}\n' * 1500 + "{oops\n")
        source = _sources(bad)[0]
        with pytest.raises(scratch_module.ImportFailed):
            await load_source(db, source, workdir=str(data_dir))
        assert await db.table_names() == ["dogs"]
    finally:
        ds.close()


def test_plan_loads_save_conflict_and_session_ownership(data_dir, monkeypatch):
    save = str(data_dir / "s.db")
    conn = sqlite3.connect(save)
    conn.execute("create table dogs (id integer)")
    conn.close()
    sources = _sources(data_dir / "dogs.csv")
    with pytest.raises(TableExists):
        plan_loads(save, sources)
    monkeypatch.delenv(ReloadSession.ENV, raising=False)
    session = ReloadSession.start()
    try:
        # A table this session created earlier is replaced, not an error
        session.remember(save, sources[0], complete=False)
        plan = plan_loads(save, sources, session=session)
        assert plan.drop == ["dogs"] and plan.load == sources
    finally:
        os.environ.pop(ReloadSession.ENV, None)
        scratch_module.remove_tree(session.directory)


def test_reload_watches_input_files(data_dir, monkeypatch):
    watched = []

    class FakeReloader:
        def watch_files(self, files):
            watched.extend(files)

    import hupper

    monkeypatch.setattr(hupper, "start_reloader", lambda *a, **kw: FakeReloader())
    monkeypatch.setattr(hupper, "is_active", lambda: True)
    monkeypatch.delenv(ReloadSession.ENV, raising=False)
    ds = ds_from_cli(
        [str(data_dir / "dogs.csv"), "c:" + str(data_dir / "cities.json"), "--reload"]
    )
    ds.close()
    assert str(data_dir / "dogs.csv") in watched
    assert str(data_dir / "cities.json") in watched


def test_sigterm_interrupts_loading_then_handler_is_restored():
    import signal

    from datasette.cli import _sigterm_interrupts

    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(KeyboardInterrupt), _sigterm_interrupts():
        os.kill(os.getpid(), signal.SIGTERM)
        # The handler runs at the next bytecode boundary
        for _ in range(1000):
            pass
    assert signal.getsignal(signal.SIGTERM) is previous


@pytest.mark.asyncio
@pytest.mark.parametrize("exclusive", (True, False))
async def test_cancelling_a_load_drops_the_table(data_dir, exclusive):
    import asyncio

    from datasette.app import Datasette
    from datasette.imports.scratch import load_source

    path = data_dir / "many.ndjson"
    path.write_text("".join(f'{{"id": {i}}}\n' for i in range(200_000)))
    ds = Datasette()
    db = ds.add_database(ScratchDatabase(ds), name="scratch")
    loop = asyncio.get_running_loop()
    try:
        source = _sources(path)[0]
        seen = []

        def progress(rows):
            # Called from the write thread when exclusive
            seen.append(rows)
            if len(seen) == 2:
                loop.call_soon_threadsafe(task.cancel)

        task = asyncio.ensure_future(
            load_source(
                db,
                source,
                workdir=str(data_dir),
                progress=progress,
                exclusive=exclusive,
            )
        )
        with pytest.raises(asyncio.CancelledError):
            await task
        assert 0 < seen[-1] < 200_000
        assert await db.table_names() == []
    finally:
        ds.close()
