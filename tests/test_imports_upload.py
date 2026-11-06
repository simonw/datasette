"""
Tests for ``POST /<database>/-/upload``: creating a table from an uploaded
file, imported in the background. See datasette/views/upload.py.
"""

import asyncio
import contextlib
import io
import json
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.parse

import pytest
import pytest_asyncio

from datasette.app import Datasette
from datasette.imports import subprocess_import
from datasette.imports.background import TerminalReporter, import_registry
from datasette.imports.loaders import get_loaders
from datasette.imports.scratch import ScratchDatabase
from datasette.imports.subprocess_import import worker_slots
from datasette.plugins import pm
from datasette.tokens import TokenRestrictions
from datasette.views.upload import (
    default_table_name,
    display_filename,
    max_upload_bytes,
)

CSV = b"id,name,score\n1,Cleo,4.5\n2,Pancakes,3\n3,Fido,\n"
ROOT = {"id": "root"}
# create-table and insert-row on the data database
WRITER = {"id": "writer"}
# create-table on the data database, insert-row only on creator_table
CREATOR = {"id": "creator"}
GEOJSON_PLUGIN = pathlib.Path(__file__).parent / "geojson_loader_plugin.py"
GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": i,
            "properties": {"name": f"place {i}"},
            "geometry": {"type": "Point", "coordinates": [i, -i]},
        }
        for i in range(1, 3)
    ],
}
CONFIG = {
    "databases": {
        "data": {
            "permissions": {
                "create-table": {"id": ["root", "writer", "creator"]},
                "insert-row": {"id": ["root", "writer"]},
            },
            "tables": {
                "creator_table": {
                    "permissions": {"insert-row": {"id": ["root", "creator"]}}
                }
            },
        },
        "frozen": {
            "permissions": {
                "create-table": {"id": ["root", "writer"]},
                "insert-row": {"id": ["root", "writer"]},
            }
        },
    }
}


def make_ds(tmp_path, *, settings=None, plugins_dir=None):
    data = tmp_path / "data.db"
    conn = sqlite3.connect(data)
    conn.executescript("""
        create table existing (id integer primary key);
        create view existing_view as select 1 as one;
        create index existing_index on existing (id);
        """)
    conn.close()
    frozen = tmp_path / "frozen.db"
    conn = sqlite3.connect(frozen)
    conn.execute("create table t (id integer)")
    conn.close()
    ds = Datasette(
        [str(data)],
        immutables=[str(frozen)],
        settings=settings,
        plugins_dir=plugins_dir,
        config=CONFIG,
    )
    ds.root_enabled = True
    return ds


@pytest.fixture
def temp_dir(tmp_path, monkeypatch):
    "The temporary directory, where uploads are saved while they import."
    directory = tmp_path / "tmp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


async def _teardown(ds):
    registry = import_registry(ds)
    registry.cancel_all()
    await registry.wait(timeout=10)
    ds.close()


@pytest_asyncio.fixture
async def ds(tmp_path, temp_dir):
    ds = make_ds(tmp_path)
    yield ds
    await _teardown(ds)


def upload_dirs(temp_dir):
    return sorted(p.name for p in temp_dir.glob("datasette-upload-*"))


async def upload(
    ds,
    content=CSV,
    filename="dogs.csv",
    *,
    database="data",
    actor=ROOT,
    headers=None,
    **fields,
):
    return await ds.client.post(
        f"/{database}/-/upload",
        files={"file": (filename, content, "application/octet-stream")},
        data=fields,
        actor=actor,
        headers=headers,
    )


async def finished(ds, response, timeout=30):
    "Wait for the import an upload started, and return it."
    assert response.status_code == 202, response.text
    data = response.json()
    job = import_registry(ds).get(data["database"], data["table"])
    assert job is not None
    assert await job.wait(timeout)
    return job


async def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting")
        await asyncio.sleep(0.02)


def path_of(url):
    "The path and query string of an absolute URL, for ds.client."
    parts = urllib.parse.urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def events(ds, table):
    return [
        event
        for event in getattr(ds, "_tracked_events", [])
        if getattr(event, "table", None) == table
    ]


async def rows(ds, table):
    response = await ds.client.get(f"/data/{table}.json?_shape=array", actor=ROOT)
    assert response.status_code == 200, response.text
    return response.json()


@contextlib.asynccontextmanager
async def busy_workers(ds):
    "Hold every import worker slot, so imports queue until this exits."
    slots = worker_slots(ds)
    held = 0
    while not slots.locked():
        await slots.acquire()
        held += 1
    try:
        yield
    finally:
        for _ in range(held):
            slots.release()


async def queued(ds, response):
    assert response.status_code == 202, response.text
    data = response.json()
    job = import_registry(ds).get("data", data["table"])
    await wait_for(lambda: job.state == "queued" and job.waiting_for == "worker")
    return job


@pytest.fixture
def stalling_imports(ds, tmp_path, monkeypatch):
    """
    Uploads into ``ds`` are imported by a stand-in for the import worker
    that sends four rows - committed two at a time, so the table is
    created - and then waits to be killed: an import that stays
    "importing", with its table, for as long as a test needs.
    """
    script = tmp_path / "stalling_worker.py"
    script.write_text(
        "import json, sys, time\n"
        "def send(m): sys.stdout.write(json.dumps(m) + '\\n')\n"
        "send({'type': 'header', 'protocol': 1, 'loader': 'csv', 'size': 1000})\n"
        "send({'type': 'columns', 'add': ['n']})\n"
        "for i in range(4): send([i])\n"
        "send({'type': 'progress', 'rows': 4, 'bytes': 400, 'size': 1000})\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    registry = import_registry(ds)
    start = registry.start

    def start_stalling(*args, **kwargs):
        kwargs.update(
            sample_size=2,
            batch_size=2,
            use_worker=True,
            worker_options={"argv": [sys.executable, str(script)]},
        )
        return start(*args, **kwargs)

    monkeypatch.setattr(registry, "start", start_stalling)


# Importing -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_csv(ds, temp_dir):
    response = await upload(ds)
    assert response.status_code == 202
    data = response.json()
    assert data["ok"] is True
    assert data["database"] == "data"
    assert data["table"] == "dogs"
    assert data["table_url"] == "http://localhost/data/dogs"
    assert data["imports_url"] == (
        "http://localhost/-/imports.json?database=data&table=dogs"
    )
    assert data["cancel_url"] == "http://localhost/data/dogs/-/cancel-import"
    job_data = data["import"]
    assert job_data["state"] == "queued"
    assert job_data["source"] == "dogs.csv"
    assert job_data["loader"] == "csv"
    assert job_data["on_error"] == "drop"
    assert job_data["table"] == "dogs"
    # The file is saved under a fixed name in a directory of its own
    job = import_registry(ds).get("data", "dogs")
    saved = pathlib.Path(job.source.path)
    assert saved.name == "upload"
    assert saved.parent.parent == temp_dir
    assert saved.read_bytes() == CSV

    job = await finished(ds, response)
    assert job.state == "done", job.error
    assert job.rows == 3
    assert await rows(ds, "dogs") == [
        {"rowid": 1, "id": 1, "name": "Cleo", "score": 4.5},
        {"rowid": 2, "id": 2, "name": "Pancakes", "score": 3.0},
        {"rowid": 3, "id": 3, "name": "Fido", "score": None},
    ]
    types = (
        await ds.get_database("data").execute(
            "select name, type from pragma_table_info('dogs')"
        )
    ).rows
    assert [tuple(row) for row in types] == [
        ("id", "INTEGER"),
        ("name", "TEXT"),
        ("score", "REAL"),
    ]
    # Removed once the import has finished
    assert upload_dirs(temp_dir) == []
    # Followed with the URLs in the response
    imports = (await ds.client.get(path_of(data["imports_url"]), actor=ROOT)).json()
    assert [(i["table"], i["state"], i["rows"]) for i in imports["imports"]] == [
        ("dogs", "done", 3)
    ]


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("dogs.csv", "dogs"),
        ("My Dogs (2024).csv", "My_Dogs_2024"),
        ("archive.tar.gz", "archive_tar"),
        ("C:\\Users\\me\\data.tsv", "data"),
        ("../../etc/passwd", "passwd"),
        (".hidden", "hidden"),
        ("sqlite_master.csv", "_sqlite_master"),
        ("Café.json", "Café"),
        ("a\x9bb\u202ec.csv", "abc"),
        ("", "data"),
        (None, "data"),
    ],
)
def test_default_table_name(filename, expected):
    assert default_table_name(filename) == expected


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("dogs.csv", "dogs.csv"),
        ("C:\\fakepath\\dogs.csv", "dogs.csv"),
        ("../../etc/passwd", "passwd"),
        ("a\x00b\nc.csv", "abc.csv"),
        # C1 controls (an 8-bit CSI), bidirectional overrides and isolates,
        # zero-width and line separator characters
        ("c1\x9b31m\u202ered.csv", "c131mred.csv"),
        ("\u2066evil\u2069\u200b\u2028.csv", "evil.csv"),
        ("\u202e", "uploaded file"),
        ("données été.csv", "données été.csv"),
        ("", "uploaded file"),
        ("x" * 300, "x" * 197 + "..."),
    ],
)
def test_display_filename(filename, expected):
    assert display_filename(filename) == expected


@pytest.mark.asyncio
async def test_default_table_name_from_upload(ds):
    response = await upload(ds, filename="My Dogs (2024).csv")
    assert response.json()["table"] == "My_Dogs_2024"
    job = await finished(ds, response)
    assert job.state == "done"
    assert job.title == "My Dogs (2024).csv"
    assert len(await rows(ds, "My_Dogs_2024")) == 3


@pytest.mark.asyncio
async def test_control_characters_in_the_filename(ds):
    "Nothing that could drive a terminal or disguise the name gets through."
    # What the command line prints for imports, uploads included
    printed = io.StringIO()
    import_registry(ds).add_listener(TerminalReporter(printed))
    response = await upload(ds, filename="c1\x9b31m\u202ered.csv")
    data = response.json()
    assert (data["table"], data["import"]["source"]) == ("c131mred", "c131mred.csv")
    job = await finished(ds, response)
    assert job.title == "c131mred.csv"
    assert printed.getvalue().splitlines()[0] == (
        f"Importing c131mred.csv (csv, {len(CSV)} bytes) into table c131mred"
    )


@pytest.mark.asyncio
async def test_explicit_table_name(ds):
    response = await upload(ds, table="  pets ")
    assert response.json()["table"] == "pets"
    job = await finished(ds, response)
    assert job.state == "done"
    assert len(await rows(ds, "pets")) == 3
    # A blank table field means the default
    response = await upload(ds, table="  ")
    assert response.json()["table"] == "dogs"
    await finished(ds, response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "format,loader",
    [("csv", "csv"), ("CSV", "csv"), ("jsonl", "ndjson"), ("ndjson", "ndjson")],
)
async def test_format_forces_the_loader(ds, format, loader):
    content = b'{"a": 1}\n{"a": 2}\n'
    response = await upload(ds, content, "data.txt", format=format)
    assert response.json()["import"]["loader"] == loader
    job = await finished(ds, response)
    assert job.state == "done", job.error
    assert job.loader == loader
    if loader == "csv":
        # The first line is the header
        assert await rows(ds, "data") == [{"rowid": 1, '{"a": 1}': '{"a": 2}'}]
    else:
        assert await rows(ds, "data") == [
            {"rowid": 1, "a": 1},
            {"rowid": 2, "a": 2},
        ]


@pytest.mark.asyncio
async def test_detects_tsv_and_json(ds):
    response = await upload(ds, b"a\tb\n1\t2\n", "one.txt")
    assert response.json()["import"]["loader"] == "tsv"
    response2 = await upload(ds, b'[{"x": 1.5}, {"x": 2}]', "two")
    assert response2.json()["import"]["loader"] == "json"
    assert (await finished(ds, response)).state == "done"
    assert (await finished(ds, response2)).state == "done"
    assert await rows(ds, "one") == [{"rowid": 1, "a": 1, "b": 2}]
    assert await rows(ds, "two") == [{"rowid": 1, "x": 1.5}, {"rowid": 2, "x": 2.0}]


@pytest.mark.asyncio
async def test_plugin_loader(tmp_path, temp_dir):
    "A loader from a plugin - GeoJSON here - is used for uploads."
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    shutil.copy(GEOJSON_PLUGIN, plugins / "upload_geojson_plugin.py")
    ds = make_ds(tmp_path, plugins_dir=str(plugins))
    try:
        response = await upload(ds, json.dumps(GEOJSON).encode(), "places.geojson")
        assert response.json()["import"]["loader"] == "geojson"
        job = await finished(ds, response)
        assert job.state == "done", job.error
        assert await rows(ds, "places") == [
            {
                "rowid": 1,
                "id": 1,
                "geometry": '{"type": "Point", "coordinates": [1, -1]}',
                "name": "place 1",
            },
            {
                "rowid": 2,
                "id": 2,
                "geometry": '{"type": "Point", "coordinates": [2, -2]}',
                "name": "place 2",
            },
        ]
        # Offered by name too
        response = await upload(
            ds, json.dumps(GEOJSON).encode(), "x.json", format="geojson"
        )
        assert (await finished(ds, response)).loader == "geojson"
        # The create table dialog lists it
        page = await database_page_data(ds, ROOT)
        names = [f["name"] for f in page["createTable"]["upload"]["formats"]]
        assert names == ["csv", "geojson", "json", "ndjson", "tsv"]
    finally:
        await _teardown(ds)
        pm.unregister(name="upload_geojson_plugin.py")


@pytest.mark.asyncio
async def test_failed_import_drops_the_table(ds, temp_dir):
    # Fails at line 1,501, after the first chunk of rows has been committed
    content = b'{"a": 1}\n' * 1500 + b"{oops\n"
    response = await upload(ds, content, "bad.ndjson", actor=WRITER)
    job = await finished(ds, response)
    assert job.state == "failed"
    assert job.error.startswith("Line 1501 is not valid JSON"), job.error
    # Nothing half-imported is left behind
    assert not await ds.get_database("data").table_exists("bad")
    assert job.table_exists is False
    assert job.created_table is True
    assert upload_dirs(temp_dir) == []
    await wait_for(lambda: len(events(ds, "bad")) == 2)
    assert [(event.name, event.actor) for event in events(ds, "bad")] == [
        ("create-table", WRITER),
        # The uploader's upload failed: its table is dropped on their behalf
        ("drop-table", WRITER),
    ]


# Errors ----------------------------------------------------------------------------


def _errors(response):
    data = response.json()
    assert data["ok"] is False
    assert data["status"] == response.status_code
    return data["errors"]


@pytest.mark.asyncio
async def test_no_file(ds):
    response = await ds.client.post(
        "/data/-/upload", files={"table": (None, "dogs")}, actor=ROOT
    )
    assert response.status_code == 400
    assert _errors(response) == [
        "No file uploaded: send it as multipart/form-data, in a field called file"
    ]


@pytest.mark.asyncio
async def test_urls_are_not_accepted(ds):
    response = await ds.client.post(
        "/data/-/upload",
        data={"file": "https://example.com/data.csv"},
        actor=ROOT,
    )
    assert response.status_code == 400
    assert _errors(response) == [
        "file must be an uploaded file - URLs and pasted text are not accepted"
    ]


@pytest.mark.asyncio
async def test_not_multipart(ds):
    response = await ds.client.post(
        "/data/-/upload", json={"file": "dogs.csv"}, actor=ROOT
    )
    assert response.status_code == 400
    assert _errors(response) == [
        "Send the file as multipart/form-data, in a field called file"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,filename,message",
    [
        (b"", "dogs.csv", "dogs.csv is empty"),
        (b"", "", "No file uploaded: the file field is empty"),
        (b"  \n\n \t\n", "blank.csv", "blank.csv is empty"),
    ],
)
async def test_empty_file(ds, temp_dir, content, filename, message):
    response = await upload(ds, content, filename)
    assert response.status_code == 400
    assert _errors(response) == [message]
    assert upload_dirs(temp_dir) == []


@pytest.mark.asyncio
async def test_unrecognized_format(ds, temp_dir):
    response = await upload(ds, b"\x00\x01\x02\xff" * 100, "mystery.bin")
    assert response.status_code == 400
    (message,) = _errors(response)
    assert message.startswith(
        "Could not import mystery.bin: it does not look like any format "
        "Datasette can read (csv, json, ndjson, tsv)."
    )
    assert upload_dirs(temp_dir) == []
    assert import_registry(ds).imports() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,format",
    [
        (b"[]", None),
        (b" [ ]\n", None),
        (b"[{}, {}]", None),
        (b"[]", "json"),
    ],
)
async def test_json_array_without_rows(ds, temp_dir, content, format):
    "It would import no rows and create no table, so it is refused at once."
    fields = {"format": format} if format else {}
    response = await upload(ds, content, "nothing.json", **fields)
    assert response.status_code == 400
    assert _errors(response) == [
        (
            "nothing.json has no rows to import: it is a JSON array with no "
            "objects in it, or only empty ones"
        )
    ]
    assert upload_dirs(temp_dir) == []
    assert import_registry(ds).imports() == []


@pytest.mark.asyncio
async def test_sqlite_database_is_refused(ds, tmp_path, temp_dir):
    path = tmp_path / "other.db"
    sqlite3.connect(path).execute("create table t (id integer)").connection.close()
    response = await upload(ds, path.read_bytes(), "other.db")
    assert response.status_code == 400
    assert _errors(response) == [
        (
            "other.db is a SQLite database. Uploads create a table from a data "
            "file - to serve a database file, start Datasette with it"
        )
    ]
    assert upload_dirs(temp_dir) == []


@pytest.mark.asyncio
async def test_unknown_format(ds, temp_dir):
    response = await upload(ds, format="kml")
    assert response.status_code == 400
    assert _errors(response) == [
        "Unknown format 'kml', available formats: csv, json, ndjson, tsv"
    ]
    assert upload_dirs(temp_dir) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,message",
    [
        ("sqlite_stuff", "Table names starting with sqlite_ are reserved by SQLite"),
        ("SQLite_stuff", "Table names starting with sqlite_ are reserved by SQLite"),
        ("a\nb", "Table name cannot contain newlines"),
        ("a\x00b", "Table name cannot contain NUL characters"),
    ],
)
async def test_invalid_table_name(ds, table, message):
    response = await upload(ds, table=table)
    assert response.status_code == 400
    assert _errors(response) == [message]


@pytest.mark.asyncio
async def test_database_not_found(ds):
    response = await upload(ds, database="nope")
    assert response.status_code == 404
    assert _errors(response) == ["Database not found"]


@pytest.mark.asyncio
async def test_get_is_not_allowed(ds):
    response = await ds.client.get("/data/-/upload", actor=ROOT)
    assert response.status_code == 405


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,kind",
    [
        ("existing", "Table"),
        ("EXISTING", "Table"),
        ("existing_view", "View"),
        ("Existing_View", "View"),
        # Tables and indexes share one namespace in SQLite
        ("existing_index", "Index"),
        ("EXISTING_INDEX", "Index"),
    ],
)
async def test_table_exists(ds, temp_dir, table, kind):
    response = await upload(ds, table=table)
    assert response.status_code == 409
    assert _errors(response) == [
        f"{kind} {table!r} already exists in data: choose another table name"
    ]
    assert upload_dirs(temp_dir) == []
    assert import_registry(ds).imports() == []


@pytest.mark.asyncio
async def test_table_being_imported(ds, temp_dir):
    async with busy_workers(ds):
        first = await queued(ds, await upload(ds))
        response = await upload(ds, table="DOGS")
        assert response.status_code == 409
        assert _errors(response) == ["Table 'DOGS' is already being imported into data"]
        # Only the first upload's file is waiting
        assert len(upload_dirs(temp_dir)) == 1
    assert await first.wait(30)
    assert first.state == "done"
    # Once it has finished, the table exists
    response = await upload(ds)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_uploads_unavailable(ds, monkeypatch, temp_dir):
    "Where subprocesses cannot run (Pyodide), uploads are turned off."
    monkeypatch.setattr(subprocess_import, "subprocesses_available", lambda: False)
    response = await upload(ds)
    assert response.status_code == 400
    assert _errors(response) == [
        (
            "Uploads are not available here: importing a file needs a worker "
            "subprocess, which this platform cannot start"
        )
    ]
    assert upload_dirs(temp_dir) == []
    # The create table dialog reads files in the browser instead
    page = await database_page_data(ds, ROOT)
    assert "upload" not in page["createTable"]


# Size limit ------------------------------------------------------------------------


def test_max_upload_bytes():
    assert max_upload_bytes(Datasette()) == 100 * 1024 * 1024
    assert max_upload_bytes(Datasette(settings={"max_upload_mb": 3})) == (
        3 * 1024 * 1024
    )
    assert max_upload_bytes(Datasette(settings={"max_upload_mb": 0})) is None


@pytest.mark.asyncio
async def test_max_upload_mb(tmp_path, temp_dir):
    ds = make_ds(tmp_path, settings={"max_upload_mb": 1})
    limit = 1024 * 1024
    try:
        header = b"n\n"
        exactly = header + b"1\n" * ((limit - len(header)) // 2)
        assert len(exactly) == limit
        message = ["File too large: uploads are limited to 1 MB"]
        # Content-Length says it is too large: refused before reading it
        response = await upload(ds, exactly + b"1\n" * 50_000, "big.csv")
        assert response.status_code == 413
        assert _errors(response) == message
        # Within the room allowed for the rest of the form, so only the
        # parser finds the file is too large
        response = await upload(ds, exactly + b"1\n", "big.csv")
        assert response.status_code == 413
        assert _errors(response) == message
        assert upload_dirs(temp_dir) == []
        # The limit itself is fine
        response = await upload(ds, exactly, "big.csv")
        job = await finished(ds, response)
        assert job.state == "done", job.error
        assert job.rows == (limit - len(header)) // 2
        page = await database_page_data(ds, ROOT)
        assert page["createTable"]["upload"]["maxBytes"] == limit
    finally:
        await _teardown(ds)


@pytest.mark.asyncio
async def test_max_upload_mb_zero_means_no_limit(tmp_path, temp_dir):
    ds = make_ds(tmp_path, settings={"max_upload_mb": 0})
    try:
        page = await database_page_data(ds, ROOT)
        assert page["createTable"]["upload"]["maxBytes"] is None
        response = await upload(ds, b"n\n" + b"1\n" * 600_000, "big.csv")
        assert (await finished(ds, response)).rows == 600_000
    finally:
        await _teardown(ds)


# Permissions -----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,table,status,message",
    [
        (None, None, 403, "Permission denied: need create-table"),
        ({"id": "someone"}, None, 403, "Permission denied: need create-table"),
        (CREATOR, None, 403, "Permission denied: need insert-row"),
        # insert-row granted for this one table
        (CREATOR, "creator_table", 202, None),
        (WRITER, None, 202, None),
        (ROOT, None, 202, None),
    ],
)
async def test_permissions(ds, temp_dir, actor, table, status, message):
    fields = {"table": table} if table else {}
    response = await upload(ds, actor=actor, **fields)
    assert response.status_code == status, response.text
    if message:
        assert _errors(response) == [message]
        assert upload_dirs(temp_dir) == []
    else:
        job = await finished(ds, response)
        assert job.state == "done"


@pytest.mark.asyncio
async def test_token_restricted_to_other_actions_is_refused(ds):
    # The signed handler: test plugins can register others that sort first
    token = await ds.create_token(
        "root",
        restrictions=TokenRestrictions()
        .allow_all("view-instance")
        .allow_all("create-table"),
        handler="signed",
    )
    response = await ds.client.post(
        "/data/-/upload",
        files={"file": ("dogs.csv", CSV, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert _errors(response) == ["Permission denied: need insert-row"]
    token = await ds.create_token(
        "root",
        restrictions=TokenRestrictions()
        .allow_database("data", "create-table")
        .allow_database("data", "insert-row"),
        handler="signed",
    )
    response = await ds.client.post(
        "/data/-/upload",
        files={"file": ("dogs.csv", CSV, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert (await finished(ds, response)).state == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [ROOT, WRITER])
async def test_immutable_database(ds, actor):
    response = await upload(ds, database="frozen", actor=actor)
    assert response.status_code == 403
    assert _errors(response) == ["Database is immutable"]


@pytest.mark.asyncio
async def test_scratch_is_refused(ds, temp_dir):
    scratch = ds.add_database(ScratchDatabase(ds), name="scratch")
    try:
        response = await upload(ds, database="scratch")
        assert response.status_code == 403
        assert _errors(response) == [
            (
                "scratch is a temporary, read-only database: upload into "
                "another database"
            )
        ]
        assert upload_dirs(temp_dir) == []
    finally:
        ds.remove_database("scratch")
        scratch.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://evil.example"},
    ],
)
async def test_cross_site_upload_is_refused(ds, temp_dir, headers):
    response = await upload(ds, headers=headers)
    assert response.status_code == 403
    assert "text/html" in response.headers["content-type"]
    assert import_registry(ds).imports() == []
    assert upload_dirs(temp_dir) == []


@pytest.mark.asyncio
async def test_same_origin_upload(ds):
    response = await upload(
        ds,
        headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},
    )
    assert (await finished(ds, response)).state == "done"


# Queueing, cancelling, shutting down ------------------------------------------------


@pytest.mark.asyncio
async def test_uploads_queue_for_a_worker(ds, temp_dir):
    async with busy_workers(ds):
        job = await queued(ds, await upload(ds))
        imports = (
            await ds.client.get("/-/imports.json?database=data", actor=ROOT)
        ).json()
        assert [(i["state"], i["status"]) for i in imports["imports"]] == [
            ("queued", "queued, waiting for an import worker")
        ]
        assert len(upload_dirs(temp_dir)) == 1
    assert await job.wait(30)
    assert job.state == "done"
    assert job.waited_seconds > 0
    assert upload_dirs(temp_dir) == []


@pytest.mark.asyncio
async def test_cancel_removes_the_file(ds, temp_dir):
    async with busy_workers(ds):
        response = await upload(ds)
        job = await queued(ds, response)
        cancelled = await ds.client.post(
            path_of(response.json()["cancel_url"]), actor=ROOT
        )
        assert cancelled.status_code == 200, cancelled.text
        assert job.state == "cancelled"
        assert upload_dirs(temp_dir) == []
    assert not await ds.get_database("data").table_exists("dogs")
    # Nothing was created, so nothing to tell anyone
    assert events(ds, "dogs") == []


@pytest.mark.asyncio
async def test_cancelled_by_someone_else(ds, temp_dir, stalling_imports):
    "The drop-table event is not put down to the uploader."
    response = await upload(ds, actor=WRITER)
    assert response.status_code == 202, response.text
    job = import_registry(ds).get("data", "dogs")
    await wait_for(lambda: job.state == "importing" and job.table_exists)
    cancelled = await ds.client.post(path_of(response.json()["cancel_url"]), actor=ROOT)
    assert cancelled.status_code == 200, cancelled.text
    assert job.state == "cancelled"
    assert not await ds.get_database("data").table_exists("dogs")
    assert upload_dirs(temp_dir) == []
    await wait_for(lambda: len(events(ds, "dogs")) == 2)
    assert [(event.name, event.actor) for event in events(ds, "dogs")] == [
        ("create-table", WRITER),
        # Root cancelled it, or Datasette shut down: the registry does not
        # say which, so the drop is no actor's
        ("drop-table", None),
    ]


@pytest.mark.asyncio
async def test_shutdown_drops_a_partial_table(ds, tmp_path, temp_dir, stalling_imports):
    response = await upload(ds, actor=WRITER)
    assert response.status_code == 202, response.text
    job = import_registry(ds).get("data", "dogs")
    await wait_for(lambda: job.state == "importing" and job.table_exists)
    await ds.invoke_shutdown()
    assert await job.wait(10)
    assert job.state == "cancelled"
    # Datasette has closed its databases by now
    conn = sqlite3.connect(tmp_path / "data.db")
    try:
        tables = [row[0] for row in conn.execute("select name from sqlite_master")]
    finally:
        conn.close()
    assert "dogs" not in tables
    assert "existing" in tables
    assert upload_dirs(temp_dir) == []
    await wait_for(lambda: len(events(ds, "dogs")) == 2)
    assert [(event.name, event.actor) for event in events(ds, "dogs")] == [
        ("create-table", WRITER),
        ("drop-table", None),
    ]


async def raw_upload(ds, *, disconnect):
    """
    Send an upload straight to the ASGI app. Once the body has been read,
    ``receive()`` answers ``http.disconnect`` at once if ``disconnect``,
    as servers do for a client that closed the connection after sending
    it, and otherwise waits for the response, as for a client that stays.
    """
    boundary = "datasetteuploadtest"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="gone.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode()
        + CSV
        + f"\r\n--{boundary}--\r\n".encode()
    )
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/data/-/upload",
        "raw_path": b"/data/-/upload",
        "query_string": b"",
        "root_path": "",
        "server": ("localhost", 80),
        "client": ("127.0.0.1", 12345),
        "headers": [
            (b"host", b"localhost"),
            (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
            (b"content-length", str(len(body)).encode()),
            (b"cookie", f"ds_actor={ds.client.actor_cookie(ROOT)}".encode()),
        ],
    }
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    responded = asyncio.Event()
    sent = []

    async def receive():
        if messages:
            return messages.pop(0)
        if not disconnect:
            await responded.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body"):
            responded.set()

    await ds.invoke_startup()
    await ds.app()(scope, receive, send)
    status = sent[0]["status"]
    body = b"".join(m.get("body", b"") for m in sent[1:])
    return status, json.loads(body)


@pytest.mark.asyncio
async def test_client_gone_before_the_import_started(ds, temp_dir):
    "Nobody is told about it, so it is not started."
    status, data = await raw_upload(ds, disconnect=True)
    assert status == 400
    assert data["errors"] == ["The client disconnected before the import started"]
    assert import_registry(ds).imports() == []
    assert upload_dirs(temp_dir) == []
    # A client that stays gets its import
    status, data = await raw_upload(ds, disconnect=False)
    assert status == 202, data
    job = import_registry(ds).get("data", "gone")
    assert await job.wait(30)
    assert (job.state, job.rows) == ("done", 3)


@pytest.mark.asyncio
async def test_shutdown_removes_the_file(tmp_path, temp_dir):
    ds = make_ds(tmp_path)
    async with busy_workers(ds):
        job = await queued(ds, await upload(ds))
        assert len(upload_dirs(temp_dir)) == 1
        await ds.invoke_shutdown()
        assert await job.wait(10)
        assert job.state == "cancelled"
        assert upload_dirs(temp_dir) == []


# Events ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events(ds):
    response = await upload(ds, actor=WRITER)
    await finished(ds, response)
    await wait_for(lambda: len(events(ds, "dogs")) == 2)
    create, insert = events(ds, "dogs")
    assert create.name == "create-table"
    assert create.actor == WRITER
    assert create.database == "data"
    assert create.schema.startswith("CREATE TABLE")
    assert '"score" REAL' in create.schema or "[score] REAL" in create.schema
    assert insert.name == "insert-rows"
    assert insert.actor == WRITER
    assert (insert.num_rows, insert.ignore, insert.replace) == (3, False, False)


@pytest.mark.asyncio
async def test_events_header_only(ds):
    "A file with columns but no rows creates a table, and inserts nothing."
    response = await upload(ds, b"id,name\n", "empty_dogs.csv")
    job = await finished(ds, response)
    assert job.state == "done"
    assert job.table_exists
    await wait_for(lambda: len(events(ds, "empty_dogs")) == 1)
    await asyncio.sleep(0.1)
    assert [event.name for event in events(ds, "empty_dogs")] == ["create-table"]


@pytest.mark.asyncio
async def test_events_when_a_column_is_widened(ds):
    "A value after the sample that changes a column's type is an alter-table."
    content = b"n\n" + b"1\n" * 1500 + b"one\n"
    response = await upload(ds, content, "numbers.csv")
    job = await finished(ds, response)
    assert job.state == "done", job.error
    assert [(w.column, w.from_type, w.to_type) for w in job.widenings] == [
        ("n", "integer", "text")
    ]
    await wait_for(lambda: len(events(ds, "numbers")) == 3)
    create, alter, insert = events(ds, "numbers")
    assert (create.name, alter.name, insert.name) == (
        "create-table",
        "alter-table",
        "insert-rows",
    )
    assert alter.before_schema == create.schema
    assert "INTEGER" in alter.before_schema
    assert "TEXT" in alter.after_schema
    assert insert.num_rows == 1501


# The create table dialog -----------------------------------------------------------


async def database_page_data(ds, actor):
    response = await ds.client.get("/data", actor=actor)
    assert response.status_code == 200
    match = re.search(r"window._datasetteDatabaseData = (.*?);</script>", response.text)
    return json.loads(match.group(1)) if match else None


@pytest.mark.asyncio
async def test_dialog_data(ds):
    page = await database_page_data(ds, ROOT)
    assert page["createTable"]["upload"] == {
        "path": "/data/-/upload",
        "maxBytes": 100 * 1024 * 1024,
        "formats": [
            {"name": loader.name, "description": loader.description}
            for loader in sorted(get_loaders(), key=lambda loader: loader.name)
        ],
    }
    names = [f["name"] for f in page["createTable"]["upload"]["formats"]]
    assert names == ["csv", "json", "ndjson", "tsv"]
    assert (await database_page_data(ds, WRITER))["createTable"]["upload"]
    # Can create tables but not insert rows: no data, so no files either
    creator = await database_page_data(ds, CREATOR)
    assert creator["createTable"]["canInsertRows"] is False
    assert "upload" not in creator["createTable"]
    # No create table dialog at all
    assert await database_page_data(ds, None) is None
