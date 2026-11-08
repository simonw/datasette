"""
Tests for the import worker subprocess (datasette/imports/worker.py) and the
parent that supervises it (datasette/imports/subprocess_import.py).
"""

import asyncio
import datetime
import decimal
import io
import json
import math
import os
import random
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from datasette.app import Datasette
from datasette.database import Database
from datasette.hookspecs import hookimpl
from datasette.imports import worker as worker_module
from datasette.imports.background import (
    DEFAULT_MAX_WORKERS,
    import_registry,
    worker_slots,
)
from datasette.imports.inserter import TypedInserter, import_rows
from datasette.imports.loaders import load_path
from datasette.imports.scratch import ScratchDatabase
from datasette.imports.sources import Source
from datasette.imports.subprocess_import import (
    ProtocolDecoder,
    ProtocolError,
    WorkerImportError,
    import_file,
    import_file_in_process,
    import_file_via_worker,
)
from datasette.plugins import pm
from datasette.utils import module_from_path

WORKER = [sys.executable, "-P", "-m", "datasette.imports.worker"]


@pytest.fixture
def db():
    ds = Datasette()
    db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
    # Switch the new file to WAL before any thread connects: connections
    # opened at the same time to a fresh is_temp_disk Database race on
    # PRAGMA journal_mode=WAL and one can fail with "database is locked"
    # (about 2 in 100 fresh databases with 4 reads and a write at once)
    db.connect()
    yield db
    db.close()


def write(path, content):
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode) as fp:
        fp.write(content)
    return str(path)


def events_csv(path, rows=3000):
    lines = ["id,name,qty,price,late"]
    for i in range(rows):
        # late becomes text in the last row: widening after the sample
        late = "n/a" if i == rows - 1 else str(i)
        lines.append(f"{i},name {i} é\U0001f600,{i % 7},{i / 4},{late}")
    return write(path, "\n".join(lines) + "\n")


def plugin_dir(tmp_path, source):
    "A --plugins-dir directory holding one plugin, ``source``."
    directory = tmp_path / "plugins"
    directory.mkdir(exist_ok=True)
    write(directory / "test_loader_plugin.py", textwrap.dedent(source))
    return str(directory)


# A loader plugin for the worker: files starting with "#rows" are read by
# this loader, which yields N rows (from the second line) and then does
# whatever the third line says
BEHAVIOUR_PLUGIN = """
    import os
    import signal
    import time

    from datasette import hookimpl
    from datasette.imports.loaders import Loader, LoaderError

    class BehaviourLoader(Loader):
        name = "behaviour"

        def sniff(self, first_bytes):
            return 1.0 if first_bytes.startswith(b"#rows") else 0.0

        def load(self, path):
            with open(path) as fp:
                lines = fp.read().split("\\n")
            count, action = int(lines[1]), lines[2]
            for i in range(count):
                row = {"id": i, "name": f"row {i}", "data": b"\\x00\\xff" if action == "bytes" else None}
                if action in ("spellings", "slowspellings"):
                    # "1.50" is stored as a float: the inserter keeps the
                    # text in a TEMP table until the import finishes
                    row["code"] = f"{i}.50"
                if action == "slowspellings":
                    time.sleep(0.001)
                yield row
            if action == "raise":
                raise LoaderError(f"Broken after {count} rows")
            if action == "exit3":
                os._exit(3)
            if action == "exit0":
                os._exit(0)
            if action == "sysexit":
                import sys
                sys.exit(0)
            if action == "abort":
                os.abort()
            if action in ("sleep", "spellings"):
                time.sleep(60)
            if action == "allocate":
                hog = []
                while True:
                    hog.append(bytearray(50 * 1024 * 1024))
            if action == "print":
                print("hello from a plugin")
                os.write(1, b"raw write to fd 1\\n")

    @hookimpl
    def register_import_loaders():
        return [BehaviourLoader]
"""


def behaviour_file(tmp_path, count, action="none", name="input.rows"):
    return write(tmp_path / name, f"#rows\n{count}\n{action}\n")


def gone(pid):
    "True if ``pid`` has exited and been reaped (no zombie)."
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        return False
    return not os.path.exists(f"/proc/{pid}") or sys.platform != "linux"


def decode_all(data, chunk_sizes=None):
    decoder = ProtocolDecoder()
    messages = []
    position = 0
    rng = random.Random(0)
    while position < len(data):
        size = rng.choice(chunk_sizes) if chunk_sizes else len(data)
        messages.extend(decoder.feed(data[position : position + size]))
        position += size
    assert decoder.close() == b""
    return decoder, messages


def rows_of(messages, decoder):
    "The rows in ``messages`` as dictionaries: the decoder hands over lists."
    return [
        row if isinstance(row, dict) else dict(zip(decoder.columns, row))
        for kind, payload in messages
        if kind == "rows"
        for row in payload
    ]


# Protocol ------------------------------------------------------------------

ROUND_TRIP_ROWS = [
    {"id": 1, "text": "plain", "n": 1.5},
    {"id": 2, "text": "é中文\U0001f600 combining é", "n": -0.0},
    {"id": 3, "text": "lone \ud800 surrogate", "n": 2**63},
    {"id": 4, "text": None, "n": -(2**63) - 1, "nested": {"$base64": True, "a": [1]}},
    {"id": 5, "text": "", "n": 2**200, "nested": [1, "two", None, {"x": []}]},
    {"id": 6, "text": "bytes", "blob": b"\x00\xff\x80binary", "n": True},
    {"id": 7, "text": "x", "nested": '{"looks": "like json"}', "n": 1e300},
    {"text": "missing keys"},
    {"new_key": "a key no earlier row had", "id": 9},
]


def as_stored(value):
    "What the inserter stores for a nested value (inserter._storable)."
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return value


def test_protocol_round_trip():
    out = io.BytesIO()
    writer = worker_module._Writer(out)
    writer.message({"type": "header", "protocol": 1, "loader": "test", "size": 0})
    streamer = worker_module.RowStreamer(
        writer, worker_module.InputPosition(__file__), 0
    )
    assert streamer.send(ROUND_TRIP_ROWS) == len(ROUND_TRIP_ROWS)
    writer.message({"type": "end", "rows": len(ROUND_TRIP_ROWS)})
    writer.flush()
    data = out.getvalue()
    # Every row line is an array, every other line an object
    for line in data.splitlines():
        assert line[:1] in (b"[", b"{")
    for chunk_sizes in (None, [1, 2, 3, 7, 50]):
        decoder, messages = decode_all(data, chunk_sizes)
        # Positional: lists of values in decoder.columns order, as
        # import_rows(columns=...) takes them - the row with bytes too
        raw = [row for kind, payload in messages if kind == "rows" for row in payload]
        assert {type(row) for row in raw} == {list}
        rows = rows_of(messages, decoder)
        assert len(rows) == len(ROUND_TRIP_ROWS)
        for sent, received in zip(ROUND_TRIP_ROWS, rows):
            # Missing keys come back as None, which the inserter treats the
            # same; nested values come as the JSON text the inserter stores
            assert {k: v for k, v in received.items() if v is not None} == {
                k: as_stored(v) for k, v in sent.items() if v is not None
            }
        assert type(rows[5]["blob"]) is bytes
        # A nested object with a "$base64" key is just data
        assert rows[3]["nested"] == '{"$base64": true, "a": [1]}'
        assert rows[4]["nested"] == '[1, "two", null, {"x": []}]'
        assert decoder.columns == ["id", "text", "n", "nested", "blob", "new_key"]
        assert messages[-1] == ("end", {"type": "end", "rows": len(ROUND_TRIP_ROWS)})


def test_protocol_special_values():
    values = [
        math.nan,
        math.inf,
        -math.inf,
        10**5000,
        decimal.Decimal("1.50"),
        datetime.date(2024, 1, 2),
        bytearray(b"ab"),
    ]
    line = worker_module.encode_row(values)
    assert line.startswith('{"type":"row"')
    decoder = ProtocolDecoder()
    decoder.feed(
        b'{"type":"header","protocol":1}\n{"type":"columns","add":[0,1,2,3,4,5,6]}\n'
    )
    [(_, [row])] = decoder.feed(line.encode("utf-8", "surrogatepass") + b"\n")
    assert math.isnan(row[0])
    assert row[1] == math.inf and row[2] == -math.inf
    # Too many digits for int(): sent as the text the inserter would store
    assert row[3] == "1" + "0" * 5000
    assert row[4] == "1.50"
    assert row[5] == "2024-01-02"
    assert row[6] == b"ab"
    # Plain rows stay plain
    assert worker_module.encode_row([1, "a", None]) == '[1,"a",null]'


HEADER_A = b'{"type":"header","protocol":1}\n{"type":"columns","add":["a"]}\n'
HEADER_AB = b'{"type":"header","protocol":1}\n{"type":"columns","add":["a","b"]}\n'


@pytest.mark.parametrize(
    "data,error",
    (
        (b"[1]\n", "a row before the header"),
        (b'{"type":"progress"}\n', "expected the header"),
        (b'{"type":"header","protocol":99}\n', "Unsupported worker protocol 99"),
        (b'{"type":"header","protocol":1}\nhello\n', "not a row or a message"),
        (
            b'{"type":"header","protocol":1}\n{"type":"columns","add":["a"]}\n[1\n',
            "not valid JSON",
        ),
        (
            b'{"type":"header","protocol":1}\n{"type":"columns","add":["a"]}\n[1],[2]\n',
            "Line 3 of the worker's output has extra data after its JSON value",
        ),
        (
            b'{"type":"header","protocol":1}\n{"type":"columns","add":["a"]}\n[1,\n[2]]\n',
            "Line 3 of the worker's output is not a complete JSON value",
        ),
        # Lines that are not one value each but would parse as the right
        # number of rows if they were joined with commas: the batched
        # json.loads() the first version used accepted these
        (HEADER_A + b"[[1]],[[2]\n[3]]\n", "Line 3 of the worker's output has extra"),
        (HEADER_A + b"[1]\n[2],[3\n[4]]\n", "Line 4 of the worker's output has extra"),
        (
            HEADER_A + b'{"type":"end","rows":0},[1\n[2]]\n',
            "Line 3 of the worker's output has extra data",
        ),
        (
            HEADER_A + b"[1]\n[\xff]\n",
            "Line 4 of the worker's output is not valid UTF-8",
        ),
        (
            HEADER_AB + b'{"type":"row","at":[0,0],"values":[1,2]}\n',
            "bad column positions",
        ),
        (HEADER_AB + b'{"type":"row","at":[2],"values":[1]}\n', "bad column positions"),
        (
            HEADER_AB + b'{"type":"row","at":[-1],"values":[1]}\n',
            "bad column positions",
        ),
        (
            HEADER_AB + b'{"type":"row","at":[0],"values":[1,2]}\n',
            "bad column positions",
        ),
        (b'{"type":"row","values":[]}\n', "a row before the header"),
        (b'["x"]\n', "a row before the header"),
        (b"\n\n  [1]\n", "Line 3 of the worker's output is not a row or a message"),
        (
            b'{"type":"header","protocol":1}\n{"type":"columns","add":["a"]}\n[1,2]\n',
            "more values than",
        ),
        (
            b'{"type":"header","protocol":1}\n{"type":"columns","add":["a"]}\n[1]\n{"type":"schema","columns":{}}\n',
            "before any row",
        ),
        (
            b'{"type":"header","protocol":1}\n{"type":"end","rows":0}\n[1]\n',
            "a row after the end",
        ),
        (
            b'{"type":"header","protocol":1}\n{"type":"end","rows":0}\n{"type":"end"}\n',
            "after the end",
        ),
        (
            b'{"type":"header","protocol":1}\n{"type":"header","protocol":1}\n',
            "a second header",
        ),
        (
            b'{"type":"header","protocol":1}\n["no columns"]\n',
            "more values than the 0 columns",
        ),
        (b'{"type":"header","protocol":1}\n{"no":"type"}\n', "needs a type"),
        (
            b'{"type":"header","protocol":1}\n{"type":"columns","add":["a"]}\n{"type":"row","values":["!"],"base64":[0]}\n',
            "bad base64",
        ),
    ),
)
def test_protocol_errors(data, error):
    decoder = ProtocolDecoder()
    with pytest.raises(ProtocolError) as ex:
        decoder.feed(data)
    assert error in str(ex.value)


def test_protocol_error_keeps_messages_before_the_bad_line():
    decoder = ProtocolDecoder()
    with pytest.raises(ProtocolError) as ex:
        decoder.feed(
            b'{"type":"header","protocol":1}\n{"type":"columns","add":["a"]}\n'
            b'[1]\n[2]\n{"type":"progress","rows":2}\nnonsense\n[3]\n'
        )
    assert [kind for kind, _ in ex.value.messages] == ["header", "rows", "progress"]
    assert rows_of(ex.value.messages, decoder) == [{"a": 1}, {"a": 2}]


def test_unknown_message_types_are_passed_through():
    decoder = ProtocolDecoder()
    messages = decoder.feed(
        b'{"type":"header","protocol":1}\n{"type":"from_the_future","x":1}\n'
    )
    assert messages[1] == ("from_the_future", {"type": "from_the_future", "x": 1})


# The worker on its own ---------------------------------------------------


def run_worker(*args):
    process = subprocess.run(
        WORKER + [str(a) for a in args], check=False, capture_output=True, timeout=60
    )
    messages = [json.loads(line) for line in process.stdout.splitlines()]
    return process.returncode, messages, process.stderr.decode()


def test_worker_header_only_csv_sends_schema(tmp_path):
    path = write(tmp_path / "header.csv", "id,name\n")
    returncode, messages, _ = run_worker(path)
    assert returncode == 0
    header, schema, end = messages
    assert header["type"] == "header"
    assert header["protocol"] == worker_module.PROTOCOL_VERSION
    assert header["loader"] == "csv"
    assert header["size"] == 8
    assert schema == {"type": "schema", "columns": {"id": "text", "name": "text"}}
    assert end["type"] == "end"
    assert end["rows"] == 0


def test_worker_progress_messages(tmp_path):
    path = events_csv(tmp_path / "events.csv", rows=60000)
    size = os.path.getsize(path)
    returncode, messages, _ = run_worker(path, "--progress-interval", "0")
    assert returncode == 0
    progress = [m for m in messages if isinstance(m, dict) and m["type"] == "progress"]
    assert len(progress) > 5
    positions = [m["bytes"] for m in progress]
    assert positions == sorted(positions)
    assert 0 < positions[0] < size
    assert all(m["size"] == size for m in progress)
    assert [m["rows"] for m in progress] == sorted(m["rows"] for m in progress)
    end = messages[-1]
    assert end["type"] == "end"
    assert end["rows"] == 60000
    assert end["bytes"] == size
    assert sum(1 for m in messages if isinstance(m, list)) == 60000


def test_worker_error_message_after_rows(tmp_path):
    lines = [json.dumps({"id": i}) for i in range(10)] + ["{not json"]
    path = write(tmp_path / "bad.ndjson", "\n".join(lines) + "\n")
    returncode, messages, stderr = run_worker(path)
    assert returncode == worker_module.EXIT_ERROR
    assert sum(1 for m in messages if isinstance(m, list)) == 10
    error = messages[-1]
    assert error["type"] == "error"
    assert error["error"] == "LoaderError"
    assert error["rows"] == 10
    assert error["message"].startswith("Line 11 is not valid JSON")
    assert "Traceback" in stderr


def test_worker_error_before_header(tmp_path):
    returncode, messages, _ = run_worker(tmp_path / "missing.csv")
    assert returncode == worker_module.EXIT_ERROR
    assert [m["type"] for m in messages] == ["error"]
    assert messages[0]["error"] == "FileNotFoundError"


def test_worker_keeps_plugin_output_off_the_protocol(tmp_path):
    plugins = plugin_dir(tmp_path, BEHAVIOUR_PLUGIN)
    path = behaviour_file(tmp_path, 3, "print")
    returncode, messages, stderr = run_worker(path, "--plugins-dir", plugins)
    assert returncode == 0
    assert messages[0]["loader"] == "behaviour"
    assert messages[-1] == {**messages[-1], "type": "end", "rows": 3}
    assert "hello from a plugin" in stderr
    assert "raw write to fd 1" in stderr


def test_worker_sends_loader_notes_as_marked_log_messages(tmp_path):
    rows = [{"a": 1}, {"a": 2}]
    path = write(tmp_path / "api.json", json.dumps({"total": 2, "results": rows}))
    returncode, messages, _ = run_worker(path)
    assert returncode == 0
    logs = [m for m in messages if isinstance(m, dict) and m["type"] == "log"]
    assert logs == [
        {
            "type": "log",
            "level": "WARNING",
            "logger": "datasette.imports.loaders",
            "message": "Importing the 2 objects in 'results', ignoring 'total'",
            "note": True,
        }
    ]
    # A note about the whole file comes after the last row, in order
    lines = ["a,b"] + ["1,2"] * 20 + ["3,4,5"]
    path = write(tmp_path / "long.csv", "\n".join(lines) + "\n")
    returncode, messages, _ = run_worker(path)
    assert returncode == 0
    kinds = [m["type"] if isinstance(m, dict) else "row" for m in messages]
    assert kinds == ["header", "columns"] + ["row"] * 20 + [
        "columns",
        "row",
        "log",
        "end",
    ]
    assert messages[-2]["note"] is True
    assert messages[-2]["message"].startswith("1 row has more fields")
    # The worker's copy of the attribute name matches the loaders module
    from datasette.imports import loaders

    assert worker_module._NOTE_LOG_ATTRIBUTE == loaders.NOTE_LOG_ATTRIBUTE


def test_worker_format_option(tmp_path):
    path = write(tmp_path / "data.txt", "a\tb\n1\t2\n")
    returncode, messages, _ = run_worker(path, "--format", "csv")
    assert returncode == 0
    assert messages[0]["loader"] == "csv"
    assert messages[0]["scores"] == {}
    # Read as CSV, the tab is part of the single column
    assert messages[1] == {"type": "columns", "add": ["a\tb"]}


# The parent ----------------------------------------------------------------


async def table_rows(db, table):
    return [tuple(row) for row in (await db.execute(f"select * from [{table}]")).rows]


def widenings(import_result):
    return [
        (w.column, w.from_type, w.to_type, w.at_row, w.method)
        for w in import_result.widenings
    ]


@pytest.mark.asyncio
async def test_import_matches_in_process_import(db, tmp_path):

    path = events_csv(tmp_path / "events.csv")
    progress = []
    result = await import_file_via_worker(
        db, "via_worker", path, batch_size=500, progress=progress.append
    )
    loaded = load_path(path)
    expected = await import_rows(db, "in_process", loaded.rows, batch_size=500)
    assert result.complete
    assert result.table_exists
    assert result.loader == "csv"
    assert result.rows == result.rows_read == 3000
    assert result.returncode == 0
    assert result.worker["rows"] == 3000
    assert result.import_result.types == expected.types
    assert result.import_result.types["late"] == "text"
    assert widenings(result.import_result) == widenings(expected)
    assert await table_rows(db, "via_worker") == await table_rows(db, "in_process")
    # Progress: rows written after each chunk, bytes from the worker
    written = [p.rows_written for p in progress]
    assert written == sorted(written)
    assert written[-1] == 3000
    assert progress[-1].bytes == progress[-1].size == os.path.getsize(path)
    assert progress[-1].fraction == 1.0
    assert all(p.pid == progress[0].pid for p in progress)
    assert gone(progress[0].pid)


@pytest.mark.asyncio
async def test_import_ndjson_with_varying_keys_and_bytes(db, tmp_path):

    rows = [{"a": 1}, {"b": "x", "a": 2}, {"c": [1, 2], "a": 3}, {"a": 2**70}]
    path = write(tmp_path / "rows.ndjson", "\n".join(map(json.dumps, rows)) + "\n")
    result = await import_file_via_worker(db, "t", path)
    assert result.import_result.types == {"a": "text", "b": "text", "c": "text"}
    assert await table_rows(db, "t") == [
        ("1", None, None),
        ("2", "x", None),
        ("3", None, "[1, 2]"),
        (str(2**70), None, None),
    ]


@pytest.mark.asyncio
async def test_bytes_values_become_blobs(db, tmp_path):

    plugins = plugin_dir(tmp_path, BEHAVIOUR_PLUGIN)
    path = behaviour_file(tmp_path, 5, "bytes")
    result = await import_file_via_worker(db, "t", path, plugins_dir=plugins)
    assert result.import_result.types["data"] == "blob"
    values = (await db.execute("select data from t")).rows
    assert [row[0] for row in values] == [b"\x00\xff"] * 5


GEOJSON_PLUGIN = """
    import json

    from datasette import hookimpl
    from datasette.imports.loaders import Loader, Schema

    class TestGeoLoader(Loader):
        name = "testgeo"

        def sniff(self, first_bytes):
            return 0.99 if b'"FeatureCollection"' in first_bytes else 0.0

        def load(self, path):
            with open(path) as fp:
                doc = json.load(fp)
            yield Schema({"id": "integer", "name": "text", "geometry": "text"})
            for feature in doc["features"]:
                yield {
                    "id": feature["id"],
                    "name": feature["properties"]["name"],
                    "geometry": json.dumps(feature["geometry"]),
                }

    @hookimpl
    def register_import_loaders():
        return [TestGeoLoader]
"""


@pytest.mark.asyncio
async def test_plugins_dir_loader_is_visible_in_the_worker(db, tmp_path):

    plugins = plugin_dir(tmp_path, GEOJSON_PLUGIN)
    doc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": i,
                "properties": {"name": f"place {i}"},
                "geometry": {"type": "Point", "coordinates": [i, -i]},
            }
            for i in range(3)
        ],
    }
    path = write(tmp_path / "places.geojson", json.dumps(doc))
    # Without the plugin the JSON loader unwraps "features"
    plain = await import_file_via_worker(db, "plain", path)
    assert plain.loader == "json"
    result = await import_file_via_worker(db, "places", path, plugins_dir=plugins)
    assert result.loader == "testgeo"
    assert result.schema == {"id": "integer", "name": "text", "geometry": "text"}
    assert result.import_result.types == result.schema
    assert await table_rows(db, "places") == [
        (i, f"place {i}", json.dumps({"type": "Point", "coordinates": [i, -i]}))
        for i in range(3)
    ]


@pytest.mark.asyncio
async def test_header_only_csv_creates_table_from_schema(db, tmp_path):

    path = write(tmp_path / "header.csv", "id,name\n")
    result = await import_file_via_worker(db, "t", path)
    assert result.complete
    assert result.rows == 0
    assert result.import_result.types == {"id": "text", "name": "text"}
    assert await db.table_exists("t")


@pytest.mark.asyncio
async def test_warnings_from_the_worker(db, tmp_path):

    path = write(
        tmp_path / "api.json", json.dumps({"total": 2, "results": [{"a": 1}, {"a": 2}]})
    )
    result = await import_file_via_worker(db, "t", path)
    assert result.rows == 2
    # A loader's note is a note, not just a warning
    assert result.notes == ["Importing the 2 objects in 'results', ignoring 'total'"]
    assert result.warnings == []
    # The same in-process
    in_process = await import_file_in_process(db, "t2", path)
    assert (in_process.notes, in_process.warnings) == (result.notes, [])


@pytest.mark.asyncio
@pytest.mark.parametrize("on_error", ("keep", "drop"))
async def test_worker_error_keeps_rows_sent_before_it(db, tmp_path, on_error):

    lines = [json.dumps({"id": i}) for i in range(10)] + ["{not json"]
    path = write(tmp_path / "bad.ndjson", "\n".join(lines) + "\n")
    with pytest.raises(WorkerImportError) as ex:
        await import_file_via_worker(db, "t", path, on_error=on_error)
    error = ex.value
    assert error.kind == "error"
    assert error.error_type == "LoaderError"
    assert str(error).startswith("Line 11 is not valid JSON")
    assert error.returncode == 1
    assert "Traceback" in error.stderr
    assert error.result.rows_read == 10
    assert not error.result.complete
    if on_error == "keep":
        # Fewer rows than one sample: all of them were still inserted
        assert error.result.rows == 10
        assert error.result.table_exists
        assert await table_rows(db, "t") == [(i,) for i in range(10)]
    else:
        assert error.result.rows == 0
        assert not error.result.table_exists
        assert not await db.table_exists("t")


@pytest.mark.asyncio
@pytest.mark.parametrize("in_process", (False, True))
async def test_loader_error_with_drop_removes_committed_rows(db, tmp_path, in_process):
    # A full sample is committed before the loader fails: on_error="drop"
    # has import_rows() drop the table it created, and says how many rows
    # went with it
    lines = [json.dumps({"id": i}) for i in range(1500)] + ["{not json"]
    path = write(tmp_path / "bad.ndjson", "\n".join(lines) + "\n")
    importer = import_file_in_process if in_process else import_file_via_worker
    with pytest.raises(WorkerImportError) as ex:
        await importer(db, "t", path, batch_size=1000, on_error="drop")
    result = ex.value.result
    assert ex.value.kind == "error" and ex.value.error_type == "LoaderError"
    assert (result.rows, result.table_exists) == (0, False)
    assert result.rows_written == 1000
    assert not await db.table_exists("t")
    assert await temp_tables(db) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("in_process", (False, True))
async def test_progress_while_finishing(db, tmp_path, in_process, monkeypatch):
    # A column that widens to text in the last chunk leaves stored numbers
    # to convert once every row is in: progress goes on reporting that
    monkeypatch.setattr(TypedInserter, "_convert_rows", 500)
    path = events_csv(tmp_path / "events.csv", rows=3000)
    seen = []
    importer = import_file_in_process if in_process else import_file_via_worker
    result = await importer(
        db,
        "t",
        path,
        batch_size=1000,
        progress=lambda p: seen.append((p.rows_written, p.finishing, p.backlog)),
    )
    assert result.rows == 3000
    assert result.import_result.types["late"] == "text"
    finishing = [(rows, backlog) for rows, done, backlog in seen if done]
    backlogs = [backlog for _, backlog in finishing]
    assert backlogs[0] > 0 and backlogs[-1] == 0
    assert backlogs == sorted(set(backlogs), reverse=True)
    assert {rows for rows, _ in finishing} == {3000}
    # Not finishing until every row was in
    assert all(rows == 3000 for rows, done, _ in seen if done)
    assert any(not done for _, done, _ in seen)


@pytest.mark.asyncio
async def test_unrecognized_format(db, tmp_path):

    path = write(tmp_path / "binary.bin", bytes(range(256)) * 10)
    with pytest.raises(WorkerImportError) as ex:
        await import_file_via_worker(db, "t", path)
    assert ex.value.kind == "error"
    assert ex.value.error_type == "UnrecognizedFormat"
    assert ex.value.result.loader is None
    assert not ex.value.result.table_exists
    assert not await db.table_exists("t")


FAILING_PLUGIN = """
    from datasette import hookimpl

    @hookimpl
    def register_import_loaders():
        raise RuntimeError("this plugin is broken")
"""


@pytest.mark.asyncio
async def test_plugin_that_fails_to_register_loaders(db, tmp_path):
    # The worker logs the failure while choosing a loader, before its
    # header: a warning, not a protocol error that hides what happened
    plugins = plugin_dir(tmp_path, FAILING_PLUGIN)
    path = write(tmp_path / "binary.bin", bytes(range(256)) * 10)
    with pytest.raises(WorkerImportError) as ex:
        await import_file_via_worker(db, "t", path, plugins_dir=plugins)
    assert ex.value.kind == "error"
    assert ex.value.error_type == "UnrecognizedFormat"
    assert "register_import_loaders() failed" in " ".join(ex.value.result.warnings)
    # The core loaders still work
    csv_path = write(tmp_path / "ok.csv", "a,b\n1,2\n")
    result = await import_file_via_worker(db, "t", csv_path, plugins_dir=plugins)
    assert result.rows == 1
    assert any("register_import_loaders() failed" in w for w in result.warnings)


def test_log_before_the_header():
    decoder = ProtocolDecoder()
    log = {"type": "log", "level": "WARNING", "message": "Plugin x failed"}
    events = decoder.feed(json.dumps(log).encode() + b"\n" + HEADER_A + b"[1]\n")
    assert [kind for kind, _ in events] == ["log", "header", "rows"]


@pytest.mark.asyncio
async def test_existing_table_is_an_error(db, tmp_path):

    await db.execute_write("create table t (id integer)")
    with pytest.raises(ValueError, match="already exists"):
        await import_file_via_worker(db, "t", events_csv(tmp_path / "e.csv"))
    # Even with on_error="drop", someone else's table is left alone
    with pytest.raises(ValueError):
        await import_file_via_worker(
            db, "t", events_csv(tmp_path / "e.csv"), on_error="drop"
        )
    assert await db.table_exists("t")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,kind,returncode",
    (
        ("raise", "error", 1),
        ("exit3", "crashed", 3),
        ("exit0", "truncated", 0),
    ),
)
async def test_worker_failures(db, tmp_path, action, kind, returncode):

    plugins = plugin_dir(tmp_path, BEHAVIOUR_PLUGIN)
    path = behaviour_file(tmp_path, 10000, action)
    with pytest.raises(WorkerImportError) as ex:
        await import_file_via_worker(db, "t", path, plugins_dir=plugins, batch_size=500)
    error = ex.value
    assert error.kind == kind
    assert error.returncode == returncode
    result = error.result
    # Every row that arrived is in the table
    count = (await db.execute("select count(*) from t")).single_value()
    assert count == result.rows == result.rows_read
    if action == "raise":
        # The worker sends everything before its error message
        assert count == 10000
        assert str(error) == "Broken after 10000 rows"
    else:
        # os._exit() loses whatever the worker had not flushed yet
        assert 0 < count <= 10000
    # The server is fine
    assert (await db.execute("select 1")).single_value() == 1


@pytest.mark.asyncio
async def test_worker_killed_by_signal(db, tmp_path):

    path = events_csv(tmp_path / "events.csv", rows=200000)
    killed = []

    def progress(p):
        if p.rows_written >= 5000 and not killed:
            os.kill(p.pid, signal.SIGKILL)
            killed.append(p.pid)

    with pytest.raises(WorkerImportError) as ex:
        await import_file_via_worker(db, "t", path, progress=progress)
    error = ex.value
    assert error.kind == "killed"
    assert error.signal == signal.SIGKILL
    assert "killed by SIGKILL" in str(error)
    count = (await db.execute("select count(*) from t")).single_value()
    assert 5000 <= count == error.result.rows == error.result.rows_read < 200000
    assert gone(killed[0])


@pytest.mark.asyncio
async def test_malformed_output_kills_the_worker(db, tmp_path):

    script = write(
        tmp_path / "fake_worker.py",
        textwrap.dedent("""
            import sys, time
            out = sys.stdout
            out.write('{"type":"header","protocol":1,"loader":"fake","size":10}\\n')
            out.write('{"type":"columns","add":["a"]}\\n')
            for i in range(5):
                out.write(f"[{i}]\\n")
            out.write("garbage\\n")
            out.flush()
            time.sleep(60)
            """),
    )
    pids = []
    with pytest.raises(WorkerImportError) as ex:
        await import_file_via_worker(
            db,
            "t",
            "unused",
            _argv=[sys.executable, script],
            progress=lambda p: pids.append(p.pid),
        )
    assert ex.value.kind == "protocol"
    assert "Line 8 of the worker's output is not a row or a message" in str(ex.value)
    # The rows before the bad line were inserted, and the worker was killed
    assert await table_rows(db, "t") == [(i,) for i in range(5)]
    assert ex.value.returncode == -signal.SIGKILL
    assert gone(pids[0])


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is enforced on Linux")
async def test_memory_limit(db, tmp_path):

    plugins = plugin_dir(tmp_path, BEHAVIOUR_PLUGIN)
    path = behaviour_file(tmp_path, 100, "allocate")
    with pytest.raises(WorkerImportError) as ex:
        await import_file_via_worker(
            db, "t", path, plugins_dir=plugins, memory_limit=600 * 1024 * 1024
        )
    assert ex.value.kind == "error"
    assert ex.value.error_type == "MemoryError"
    # The rows sent before the loader ran out of memory are there
    assert ex.value.result.rows == 100
    assert (await db.execute("select count(*) from t")).single_value() == 100


@pytest.mark.asyncio
async def test_failed_insert_kills_the_worker(db, tmp_path):

    path = events_csv(tmp_path / "events.csv", rows=20000)
    writes = []

    class FailingWrites:
        __name__ = "FailingWrites"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            if database != "scratch":
                return None

            def wrapper(conn):
                writes.append(1)
                if len(writes) == 3:
                    raise RuntimeError("disk full, pretend")
                yield

            return wrapper

    pids = []
    pm.register(FailingWrites(), name="test_imports_worker_failing_writes")
    try:
        with pytest.raises(RuntimeError, match="disk full"):
            await import_file_via_worker(
                db, "t", path, batch_size=1000, progress=lambda p: pids.append(p.pid)
            )
    finally:
        pm.unregister(name="test_imports_worker_failing_writes")
    assert gone(pids[0])
    # The two chunks committed before the failure are kept
    assert (await db.execute("select count(*) from t")).single_value() == 2000


@pytest.mark.asyncio
async def test_write_wrapper_sees_every_chunk(db, tmp_path):

    path = events_csv(tmp_path / "events.csv", rows=5000)
    seen = []

    class CountWrites:
        __name__ = "CountWrites"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            def wrapper(conn):
                yield
                seen.append(
                    (database, conn.execute("select count(*) from t").fetchone()[0])
                )

            return wrapper

    pm.register(CountWrites(), name="test_imports_worker_count_writes")
    try:
        result = await import_file_via_worker(db, "t", path, batch_size=1000)
    finally:
        pm.unregister(name="test_imports_worker_count_writes")
    # Sample of 1000, four batches of 1000, then finish()
    assert seen == [("scratch", n) for n in (1000, 2000, 3000, 4000, 5000, 5000)]
    assert result.stats["writes"] == 6


@pytest.mark.asyncio
async def test_cancelling_the_import_stops_the_worker(db, tmp_path):

    plugins = plugin_dir(tmp_path, BEHAVIOUR_PLUGIN)
    # 3000 rows, then the loader hangs
    path = behaviour_file(tmp_path, 3000, "sleep")
    started = asyncio.Event()
    pids = []

    def progress(p):
        pids.append(p.pid)
        if p.rows_written >= 1000:
            started.set()

    task = asyncio.ensure_future(
        import_file_via_worker(
            db,
            "t",
            path,
            plugins_dir=plugins,
            progress=progress,
            stop_timeout=0.5,
        )
    )
    await asyncio.wait_for(started.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gone(pids[0])
    # Rows committed before the cancellation stay
    assert (await db.execute("select count(*) from t")).single_value() >= 1000


@pytest.mark.asyncio
async def test_cancel_with_drop_removes_the_table(db, tmp_path):

    plugins = plugin_dir(tmp_path, BEHAVIOUR_PLUGIN)
    path = behaviour_file(tmp_path, 3000, "sleep")
    started = asyncio.Event()

    def progress(p):
        if p.rows_written >= 1000:
            started.set()

    task = asyncio.ensure_future(
        import_file_via_worker(
            db, "t", path, plugins_dir=plugins, progress=progress, on_error="drop"
        )
    )
    await asyncio.wait_for(started.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await db.table_exists("t")


@pytest.mark.asyncio
async def test_failed_import_never_drops_a_table_it_did_not_create(db, tmp_path):
    # Both imports check that "t" does not exist, then race to create it:
    # the loser's first write fails, and on_error="drop" must not drop the
    # winner's table
    first = events_csv(tmp_path / "first.csv", rows=3000)
    second = events_csv(tmp_path / "second.csv", rows=3000)
    results = await asyncio.gather(
        import_file_via_worker(db, "t", first, on_error="drop"),
        import_file_via_worker(db, "t", second, on_error="drop"),
        return_exceptions=True,
    )
    succeeded = [r for r in results if not isinstance(r, BaseException)]
    failed = [r for r in results if isinstance(r, BaseException)]
    assert len(succeeded) == 1
    assert len(failed) == 1
    assert "already exists" in str(failed[0])
    assert (await db.execute("select count(*) from t")).single_value() == 3000


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux only")
def test_worker_dies_with_its_parent(tmp_path):
    # A parent process starts a worker whose loader is stuck, then is killed
    plugins = plugin_dir(tmp_path, BEHAVIOUR_PLUGIN)
    path = behaviour_file(tmp_path, 10, "sleep")
    parent_script = write(
        tmp_path / "parent.py",
        textwrap.dedent(f"""
            import os, subprocess, sys, time
            worker = subprocess.Popen(
                {WORKER!r} + [{path!r}, "--plugins-dir", {plugins!r},
                "--parent-pid", str(os.getpid()), "--progress-interval", "60"],
                stdout=subprocess.PIPE,
            )
            worker.stdout.readline()
            print(worker.pid, flush=True)
            time.sleep(60)
            """),
    )
    parent = subprocess.Popen(
        [sys.executable, parent_script], stdout=subprocess.PIPE, text=True
    )
    try:
        worker_pid = int(parent.stdout.readline())
        assert os.path.exists(f"/proc/{worker_pid}")
    finally:
        parent.kill()
        parent.wait()
        parent.stdout.close()
    for _ in range(200):
        try:
            with open(f"/proc/{worker_pid}/stat") as fp:
                state = fp.read().rsplit(")", 1)[1].split()[0]
        except OSError:
            break
        if state == "Z":
            # Exited, waiting for init to reap it
            break
        time.sleep(0.01)
    else:
        os.kill(worker_pid, signal.SIGKILL)
        raise AssertionError("The worker outlived its parent")


# Regressions ---------------------------------------------------------------


def test_long_line_decodes_in_linear_time():
    # The first decoder re-joined and re-split the partial line on every
    # read: a 32MB line in 64KB reads took seconds, quadratic in its length
    value = "x" * (32 * 1024 * 1024)
    data = HEADER_A + json.dumps([value]).encode() + b"\n" + b'["y"]\n'
    decoder = ProtocolDecoder()
    rows = []
    start = time.perf_counter()
    for i in range(0, len(data), 65536):
        rows.extend(rows_of(decoder.feed(data[i : i + 65536]), decoder))
    elapsed = time.perf_counter() - start
    assert decoder.close() == b""
    assert [len(row["a"]) for row in rows] == [len(value), 1]
    # About 0.1s; the old decoder took 5-10s for this
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_huge_value_through_the_worker(db, tmp_path):
    big = "é" * (4 * 1024 * 1024)
    path = write(tmp_path / "huge.csv", f"a,b\n1,{big}\n2,y\n")
    result = await import_file_via_worker(db, "t", path)
    assert result.rows == 2
    assert await table_rows(db, "t") == [(1, big), (2, "y")]


def sparse_rows(count=300, keys=200, per_row=4):
    rng = random.Random(1)
    names = [f"k{i}" for i in range(keys)]
    rows = []
    for i in range(count):
        row = {"id": i}
        for key in rng.sample(names, per_row):
            row[key] = rng.choice([None, i, "text", 1.5, [1, {"x": None}]])
        rows.append(row)
    # Keys in a different order from the column list, a None-only new key,
    # a row with no keys at all, bytes in a sparse row
    rows.append({"k5": 1, "id": -1, "only_none": None})
    rows.append({})
    rows.append({"id": -2, "blob_key": b"\x00\x01"})
    return rows


def test_sparse_rows_are_sent_as_positions():
    rows = sparse_rows()
    out = io.BytesIO()
    writer = worker_module._Writer(out)
    writer.message({"type": "header", "protocol": 1, "loader": "test", "size": 0})
    streamer = worker_module.RowStreamer(
        writer, worker_module.InputPosition(__file__), 0
    )
    streamer.send(rows)
    writer.flush()
    data = out.getvalue()
    lines = data.splitlines()
    sparse = [line for line in lines if line.startswith(b'{"type":"row","at":')]
    # Most rows have 5 of ~200 keys: positions, not 200 nulls each
    assert len(sparse) > len(rows) * 0.8
    assert len(data) / len(rows) < 120
    decoder, messages = decode_all(data, [1, 7, 4096])
    raw = [row for kind, payload in messages if kind == "rows" for row in payload]
    # Sparse rows are dictionaries, the others lists
    assert {type(row) for row in raw} == {dict, list}
    received = rows_of(messages, decoder)
    assert len(received) == len(rows)
    for sent, got in zip(rows, received):
        # Same values; keys come in the row's own order
        assert {k: v for k, v in got.items() if v is not None} == {
            k: as_stored(v) for k, v in sent.items() if v is not None
        }
    assert received[-1]["blob_key"] == b"\x00\x01"
    # A column that only ever holds None still reaches the parent
    assert "only_none" in received[-3]


def test_trailing_nulls_are_trimmed_but_new_columns_are_kept():
    out = io.BytesIO()
    writer = worker_module._Writer(out)
    streamer = worker_module.RowStreamer(
        writer, worker_module.InputPosition(__file__), 0
    )
    streamer.send([{"a": 1, "b": 2, "c": 3}, {"a": 4}, {"b": None, "a": 5, "d": None}])
    writer.flush()
    assert out.getvalue().splitlines() == [
        b'{"type":"columns","add":["a","b","c"]}',
        b"[1,2,3]",
        b"[4]",
        b'{"type":"columns","add":["d"]}',
        # d is new: sent, even though it is None
        b"[5,null,null,null]",
    ]


async def table_shape(db, table):
    columns = [
        (row["name"], row["type"])
        for row in (await db.execute(f"pragma table_info([{table}])")).rows
    ]
    rows = (await db.execute(f"select * from [{table}] order by rowid")).rows
    return columns, [tuple(row) for row in rows]


@pytest.mark.asyncio
async def test_sparse_ndjson_matches_in_process_import(db, tmp_path):
    rows = [
        row for row in sparse_rows() if not any(type(v) is bytes for v in row.values())
    ]
    path = write(tmp_path / "sparse.ndjson", "\n".join(map(json.dumps, rows)) + "\n")
    result = await import_file_via_worker(db, "worker", path)
    loaded = load_path(path)
    expected = await import_rows(db, "in_process", loaded.rows, batch_size=1000)
    assert result.rows == len(rows)
    assert await table_shape(db, "worker") == await table_shape(db, "in_process")
    assert widenings(result.import_result) == widenings(expected)
    # Far fewer bytes than one null per known column per row
    assert result.stats["bytes_read"] < os.path.getsize(path) * 2


@pytest.mark.asyncio
async def test_keys_that_turn_up_late_match_in_process_import(db, tmp_path):
    # The parent decodes a whole read of the pipe before it writes a chunk,
    # so it knows of keys that only later rows have. Those must not become
    # columns early: all NULL, INTEGER, then widened to text, converting
    # every row before them
    rows = []
    for i in range(20_000):
        row = {"id": i, "pad": "x" * 100}
        for name, start in (("late_a", 5_500), ("late_b", 10_700), ("late_c", 15_900)):
            if i >= start:
                row[name] = f"s{i}"
        rows.append(row)
    path = write(tmp_path / "late.ndjson", "\n".join(map(json.dumps, rows)) + "\n")
    via_worker = await import_file_via_worker(db, "worker", path)
    in_process = await import_file_in_process(db, "in_process", path)
    assert via_worker.rows == in_process.rows == 20_000
    assert widenings(via_worker.import_result) == []
    assert widenings(in_process.import_result) == []
    assert via_worker.import_result.convert_seconds == 0
    assert await table_shape(db, "worker") == await table_shape(db, "in_process")
    types = await db.execute("select name, type from pragma_table_info('worker')")
    assert [tuple(row) for row in types] == [
        ("id", "INTEGER"),
        ("pad", "TEXT"),
        ("late_a", "TEXT"),
        ("late_b", "TEXT"),
        ("late_c", "TEXT"),
    ]


def test_int_digit_limit_is_the_same_with_or_without_bytes():
    limit = sys.get_int_max_str_digits()
    at_limit = 10 ** (limit - 1)
    too_long = 10**limit
    decoder = ProtocolDecoder()
    decoder.feed(HEADER_AB)
    for values in ([at_limit, None], [at_limit, b"x"]):
        line = worker_module.encode_row(values)
        [(_, [row])] = decoder.feed(line.encode() + b"\n")
        # Within the limit: a number either way
        assert row[0] == at_limit
    for values in ([too_long, None], [too_long, b"x"]):
        line = worker_module.encode_row(values)
        [(_, [row])] = decoder.feed(line.encode() + b"\n")
        assert row[0] == "1" + "0" * limit


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ("-data.csv", "--help.csv"))
async def test_path_that_looks_like_an_option(db, tmp_path, monkeypatch, name):
    write(tmp_path / name, "a,b\n1,2\n")
    monkeypatch.chdir(tmp_path)
    result = await import_file_via_worker(db, "t", name)
    assert result.rows == 1
    assert await table_rows(db, "t") == [(1, 2)]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ("sysexit", "abort"))
async def test_worker_exits_part_way(db, tmp_path, action):
    plugins = plugin_dir(tmp_path, BEHAVIOUR_PLUGIN)
    # About 200KB of rows: more than the worker's 64KB buffer
    path = behaviour_file(tmp_path, 10000, action)
    with pytest.raises(WorkerImportError) as ex:
        await import_file_via_worker(db, "t", path, plugins_dir=plugins)
    error = ex.value
    count = (await db.execute("select count(*) from t")).single_value()
    assert count == error.result.rows == error.result.rows_read
    if action == "sysexit":
        # A plugin's sys.exit() is reported like any error, after every row
        # (the first version lost the buffered rows and said "truncated")
        assert error.kind == "error"
        assert error.error_type == "SystemExit"
        assert str(error) == "The loader called sys.exit(0)"
        assert count == 10000
    else:
        # Killed by SIGABRT: the rows still buffered are lost, and
        # faulthandler's traceback is on stderr
        assert error.kind == "killed"
        assert error.signal == signal.SIGABRT
        assert "Fatal Python error: Aborted" in error.stderr
        assert "in load" in error.stderr
        assert 0 < count < 10000


# A worker run where the resource module cannot be imported, as on Windows
NO_RESOURCE_WORKER = [
    sys.executable,
    "-P",
    "-c",
    (
        "import runpy, sys; sys.modules['resource'] = None; "
        "sys.argv[0] = 'worker'; "
        "runpy.run_module('datasette.imports.worker', run_name='__main__')"
    ),
]


@pytest.mark.asyncio
async def test_worker_without_the_resource_module(db, tmp_path):
    path = events_csv(tmp_path / "events.csv", rows=100)
    result = await import_file_via_worker(
        db,
        "t",
        path,
        _argv=NO_RESOURCE_WORKER + ["--memory-limit", str(10**9), "--", path],
    )
    assert result.complete
    assert result.rows == 100
    assert result.worker["max_rss"] is None
    assert result.worker["cpu"] > 0
    assert any("Could not limit" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_stopping_the_worker_without_sigkill_or_process_groups(
    db, tmp_path, monkeypatch
):
    # What Windows has: Process.kill() and terminate()
    from datasette.imports import subprocess_import

    monkeypatch.setattr(subprocess_import, "_SIGKILL", None)
    monkeypatch.setattr(subprocess_import, "_PROCESS_GROUPS", False)
    plugins = plugin_dir(tmp_path, BEHAVIOUR_PLUGIN)
    path = behaviour_file(tmp_path, 3000, "sleep")
    started = asyncio.Event()
    pids = []

    def progress(p):
        pids.append(p.pid)
        if p.rows_written >= 1000:
            started.set()

    task = asyncio.ensure_future(
        import_file_via_worker(
            db, "t", path, plugins_dir=plugins, progress=progress, stop_timeout=0.5
        )
    )
    await asyncio.wait_for(started.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gone(pids[0])


# A stand-in worker: the header, a column, and rows 0 to 4
STAND_IN_ROWS = """
    import json, sys, time
    def send(message):
        sys.stdout.write(json.dumps(message) + "\\n")
    send({"type": "header", "protocol": 1, "loader": "fake", "size": 10})
    send({"type": "columns", "add": ["a"]})
    for i in range(5):
        send([i])
"""


@pytest.mark.asyncio
async def test_a_worker_that_floods_stderr_still_imports(db, tmp_path):
    # 1MB on stderr before anything on stdout: far more than a pipe holds,
    # so the worker would block on it, and the import wait for its rows
    # forever, unless stderr is read all the time
    script = write(
        tmp_path / "chatty_worker.py",
        textwrap.dedent("""
            import sys
            sys.stderr.write("x" * (1024 * 1024))
            sys.stderr.flush()
            """)
        + textwrap.dedent(STAND_IN_ROWS)
        + 'send({"type": "end", "rows": 5, "bytes": 10})\n',
    )
    result = await asyncio.wait_for(
        import_file_via_worker(db, "t", "unused", _argv=[sys.executable, script]),
        30,
    )
    assert result.complete
    assert result.returncode == 0
    assert await table_rows(db, "t") == [(i,) for i in range(5)]


@pytest.mark.asyncio
async def test_cancelled_while_the_worker_exits(db, tmp_path, monkeypatch):
    # The worker sends everything, then does not exit: the import waits for
    # it in WorkerProcess.stop(), and is cancelled there. The worker must
    # still be killed and reaped before the cancellation goes on
    from datasette.imports import subprocess_import

    script = write(
        tmp_path / "lingering_worker.py",
        textwrap.dedent(STAND_IN_ROWS) + textwrap.dedent("""
            send({"type": "end", "rows": 5, "bytes": 10})
            sys.stdout.flush()
            sys.stdout.close()
            time.sleep(60)
            """),
    )
    stopping = []
    entered = asyncio.Event()
    real_stop = subprocess_import.WorkerProcess.stop

    async def stop(self, timeout=None):
        stopping.append(self)
        entered.set()
        return await real_stop(self, timeout)

    monkeypatch.setattr(subprocess_import.WorkerProcess, "stop", stop)
    task = asyncio.ensure_future(
        import_file_via_worker(
            db, "t", "unused", _argv=[sys.executable, script], stop_timeout=30
        )
    )
    await asyncio.wait_for(entered.wait(), 30)
    await asyncio.sleep(0.1)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    worker = stopping[0]
    # Killed and reaped by the time the cancellation reached the caller
    assert worker.returncode == -signal.SIGKILL
    assert gone(worker.pid)
    # Every row was in before it was cancelled
    assert await table_rows(db, "t") == [(i,) for i in range(5)]


@pytest.mark.asyncio
async def test_cancelled_while_waiting_for_rows_the_worker_is_killed_at_once(
    db, tmp_path
):
    # The worker has sent five rows and is still running, its output open:
    # the import waits for more and is cancelled there. Nothing else stops
    # the worker - no write failed, no progress callback raised - so the
    # block's exit has to kill it. Waiting for it to exit, as at the end of
    # a block that ended normally, would take stop_timeout and then a
    # SIGTERM: a minute here
    stop_timeout = 60
    script = write(
        tmp_path / "stuck_worker.py",
        textwrap.dedent(STAND_IN_ROWS) + "sys.stdout.flush()\ntime.sleep(60)\n",
    )
    written = asyncio.Event()
    pids = []

    def progress(p):
        pids.append(p.pid)
        if p.rows_written == 5:
            written.set()

    task = asyncio.ensure_future(
        import_file_via_worker(
            db,
            "t",
            "unused",
            _argv=[sys.executable, script],
            # The five rows are the sample: committed before it waits
            sample_size=5,
            progress=progress,
            stop_timeout=stop_timeout,
        )
    )
    await asyncio.wait_for(written.wait(), 30)
    await asyncio.sleep(0.1)
    assert not task.done()
    started = time.monotonic()
    task.cancel()
    # Far less than stop_timeout. If it has not ended by then, wait_for()
    # cancels it again and raises TimeoutError
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert time.monotonic() - started < 5
    # Killed and reaped before the cancellation reached the caller
    assert gone(pids[0])
    assert await table_rows(db, "t") == [(i,) for i in range(5)]


@pytest.mark.asyncio
async def test_cancelled_again_while_the_worker_is_reaped(tmp_path):
    # A cancellation that reaches the block kills the worker and reaps it.
    # Cancelled again during the reap, the block raises at once - but the
    # reap carries on until the worker is gone, its stderr read and its
    # pipes closed, rather than stopping half way
    from datasette.imports.subprocess_import import WorkerProcess

    reaping = asyncio.Event()
    carry_on = asyncio.Event()
    outcome = []

    class SlowToReap(WorkerProcess):
        async def stop(self, timeout=None):
            reaping.set()
            try:
                # A reap that takes a while: long enough to be cancelled
                await carry_on.wait()
                await super().stop(timeout)
            except asyncio.CancelledError:
                outcome.append("interrupted")
                raise
            outcome.append("reaped")

    worker = SlowToReap(
        [sys.executable, "-c", "import time; time.sleep(60)"], stop_timeout=60
    )
    entered = asyncio.Event()

    async def block():
        async with worker:
            entered.set()
            await asyncio.sleep(60)

    task = asyncio.ensure_future(block())
    await asyncio.wait_for(entered.wait(), 30)
    task.cancel()
    await asyncio.wait_for(reaping.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert outcome == []
    carry_on.set()
    deadline = time.monotonic() + 10
    while not outcome and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert outcome == ["reaped"]
    # Killed by the first cancellation, not left to stop_timeout
    assert worker.returncode == -signal.SIGKILL
    assert gone(worker.pid)


GEO_DOC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": 1,
            "properties": {"name": "one"},
            "geometry": {"type": "Point", "coordinates": [1, 2]},
        }
    ],
}


@pytest.mark.asyncio
async def test_plugins_dir_defaults_to_the_datasette_instance(tmp_path):
    plugins = plugin_dir(tmp_path, GEOJSON_PLUGIN)
    path = write(tmp_path / "places.geojson", json.dumps(GEO_DOC))
    # Datasette registers --plugins-dir plugins in this process too
    ds = Datasette(plugins_dir=plugins)
    try:
        db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
        result = await import_file_via_worker(db, "places", path)
        assert result.loader == "testgeo"
        # False: don't load any plugins directory in the worker
        plain = await import_file_via_worker(db, "plain", path, plugins_dir=False)
        assert plain.loader == "json"
        db.close()
    finally:
        pm.unregister(name="test_loader_plugin.py")


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="reads /proc")
@pytest.mark.parametrize("max_workers", (1, None))
async def test_slots_limit_concurrent_workers(tmp_path, max_workers):
    # Background imports take turns for the instance's worker slots
    ds = Datasette()
    db = ds.add_database(ScratchDatabase(ds), name="scratch")
    if max_workers is not None:
        # The first call for an instance says how many slots it has
        worker_slots(ds, max_workers=max_workers)
    registry = import_registry(ds)
    paths = [events_csv(tmp_path / f"e{i}.csv", rows=10000) for i in range(4)]
    pids = set()
    most = []

    def running(pid):
        # Without reaping it, which would confuse asyncio's child watcher
        try:
            with open(f"/proc/{pid}/stat") as fp:
                return fp.read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    def listener(job, event):
        if event == "progress" and job.pid is not None:
            pids.add(job.pid)
            most.append(sum(1 for pid in pids if running(pid)))

    registry.add_listener(listener)
    try:
        jobs = []
        for i, path in enumerate(paths):
            source = Source(path, "file", path)
            source.table = f"t{i}"
            jobs.append(registry.start(db, source))
        await ds.start_background_tasks()
        assert await registry.wait(60)
        assert [job.state for job in jobs] == ["done"] * 4
        for i in range(4):
            count = (await db.execute(f"select count(*) from t{i}")).single_value()
            assert count == 10000
    finally:
        await ds.invoke_shutdown()
    assert len(pids) == 4
    if max_workers == 1:
        assert max(most) == 1
    else:
        # The default: DEFAULT_MAX_WORKERS per Datasette instance
        assert max(most) <= DEFAULT_MAX_WORKERS
    assert registry.slots is worker_slots(ds)


@pytest.mark.asyncio
async def test_in_process_import_matches_the_worker(db, tmp_path):
    path = events_csv(tmp_path / "events.csv")
    progress = []
    in_process = await import_file_in_process(
        db, "in_process", path, batch_size=500, progress=progress.append
    )
    worker = await import_file(db, "worker", path, batch_size=500)
    assert in_process.complete and worker.complete
    assert in_process.loader == worker.loader == "csv"
    assert in_process.rows == worker.rows == 3000
    assert in_process.import_result.types == worker.import_result.types
    assert in_process.worker is None and in_process.returncode is None
    assert await table_shape(db, "in_process") == await table_shape(db, "worker")
    assert progress[-1].rows_written == 3000
    assert progress[-1].fraction == 1.0
    # use_worker=False is the in-process path
    again = await import_file(db, "again", path, use_worker=False)
    assert again.worker is None
    assert again.rows == 3000


@pytest.mark.asyncio
@pytest.mark.parametrize("on_error", ("keep", "drop"))
async def test_in_process_error_keeps_rows_like_the_worker(db, tmp_path, on_error):
    lines = [json.dumps({"id": i}) for i in range(10)] + ["{not json"]
    path = write(tmp_path / "bad.ndjson", "\n".join(lines) + "\n")
    errors = []
    for table, function in (
        ("w", import_file_via_worker),
        ("p", import_file_in_process),
    ):
        with pytest.raises(WorkerImportError) as ex:
            await function(db, table, path, on_error=on_error)
        errors.append(ex.value)
    for error in errors:
        assert error.kind == "error"
        assert error.error_type == "LoaderError"
        assert str(error).startswith("Line 11 is not valid JSON")
        assert error.result.rows_read == 10
        assert error.result.rows == (10 if on_error == "keep" else 0)
        assert error.result.table_exists == (on_error == "keep")
    if on_error == "keep":
        assert await table_rows(db, "w") == await table_rows(db, "p")
    else:
        assert not await db.table_exists("p")


@pytest.mark.asyncio
async def test_in_process_unrecognized_and_missing(db, tmp_path):
    binary = write(tmp_path / "binary.bin", bytes(range(256)) * 10)
    for path, error_type in (
        (binary, "UnrecognizedFormat"),
        (str(tmp_path / "missing.csv"), "FileNotFoundError"),
    ):
        with pytest.raises(WorkerImportError) as ex:
            await import_file_in_process(db, "t", path)
        assert ex.value.error_type == error_type
        assert not ex.value.result.table_exists
    assert not await db.table_exists("t")


def test_end_message_reports_pipe_write_time(tmp_path):
    path = events_csv(tmp_path / "events.csv", rows=100)
    returncode, messages, _ = run_worker(path)
    assert returncode == 0
    assert messages[-1]["write_seconds"] >= 0


async def temp_tables(db):
    return await db.execute_write_fn(
        lambda conn: [
            row[0] for row in conn.execute("select name from temp.sqlite_master")
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("in_process", (False, True))
async def test_cancelling_drops_the_temp_spellings_table(db, tmp_path, in_process):
    # The TEMP table of kept spellings on the write connection goes when
    # the import is cancelled
    plugins = plugin_dir(tmp_path, BEHAVIOUR_PLUGIN)
    started = asyncio.Event()

    def progress(p):
        if p.rows_written >= 1000:
            started.set()

    if in_process:
        # In-process imports use this process's plugins. The loader thread
        # cannot be interrupted, so it is slow rather than stuck
        path = behaviour_file(tmp_path, 5000, "slowspellings")
        plugin = module_from_path(
            os.path.join(plugins, "test_loader_plugin.py"), name="spellings_loader"
        )
        pm.register(plugin, name="spellings_loader")
        coroutine = import_file_in_process(db, "t", path, progress=progress)
    else:
        path = behaviour_file(tmp_path, 3000, "spellings")
        coroutine = import_file_via_worker(
            db, "t", path, plugins_dir=plugins, progress=progress, stop_timeout=0.5
        )
    try:
        task = asyncio.ensure_future(coroutine)
        await asyncio.wait_for(started.wait(), 30)
        assert len(await temp_tables(db)) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if in_process:
            pm.unregister(name="spellings_loader")
    assert await temp_tables(db) == []
    assert (await db.execute("select count(*) from t")).single_value() >= 1000


@pytest.mark.asyncio
async def test_nested_values_arrive_as_the_text_the_inserter_stores(db, tmp_path):
    rows = [
        {"id": 1, "geometry": {"type": "Point", "coordinates": [1.5, -2]}},
        {"id": 2, "geometry": [1, [2, [3, None]]], "tags": ["a", "é", "\ud800"]},
        {"id": 3, "geometry": {"nan": math.nan, "inf": math.inf}},
        {"id": 4, "geometry": "plain text", "tags": []},
        {"id": 5, "geometry": 7, "tags": {}},
        {"id": 6, "geometry": {"$base64": True, "encoded": "AAA="}},
    ]
    path = write(tmp_path / "nested.ndjson", "\n".join(map(json.dumps, rows)) + "\n")
    await import_file_via_worker(db, "worker", path)
    loaded = load_path(path)
    await import_rows(db, "in_process", loaded.rows)
    assert await table_shape(db, "worker") == await table_shape(db, "in_process")
    # The parent gets text, not lists and dicts to serialize again
    process = await asyncio.create_subprocess_exec(
        *WORKER, "--", path, stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    decoder, messages = decode_all(stdout)
    first = rows_of(messages, decoder)[0]
    assert first["geometry"] == '{"type": "Point", "coordinates": [1.5, -2]}'
