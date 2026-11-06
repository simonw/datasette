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
from datasette.imports.inserter import import_rows
from datasette.imports.loaders import load_path
from datasette.imports.subprocess_import import (
    ProtocolDecoder,
    ProtocolError,
    WorkerImportError,
    import_file_via_worker,
)
from datasette.plugins import pm

WORKER = [sys.executable, "-P", "-m", "datasette.imports.worker"]


@pytest.fixture
def db():
    ds = Datasette()
    db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
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
                yield {"id": i, "name": f"row {i}", "data": b"\\x00\\xff" if action == "bytes" else None}
            if action == "raise":
                raise LoaderError(f"Broken after {count} rows")
            if action == "exit3":
                os._exit(3)
            if action == "exit0":
                os._exit(0)
            if action == "sleep":
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


def rows_of(messages):
    return [row for kind, payload in messages if kind == "rows" for row in payload]


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
        rows = rows_of(messages)
        assert len(rows) == len(ROUND_TRIP_ROWS)
        for sent, received in zip(ROUND_TRIP_ROWS, rows):
            # Missing keys come back as None, which the inserter treats the same
            assert {k: v for k, v in received.items() if v is not None} == {
                k: v for k, v in sent.items() if v is not None
            }
        assert type(rows[5]["blob"]) is bytes
        # A nested object with a "$base64" key is just data
        assert rows[3]["nested"] == {"$base64": True, "a": [1]}
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
            "Extra data",
        ),
        (
            b'{"type":"header","protocol":1}\n{"type":"columns","add":["a"]}\n[1,\n[2]]\n',
            "not valid JSON",
        ),
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
    assert rows_of(ex.value.messages) == [{"a": 1}, {"a": 2}]


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
    assert any("ignoring 'total'" in warning for warning in result.warnings)


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
async def test_unrecognized_format(db, tmp_path):

    path = write(tmp_path / "binary.bin", bytes(range(256)) * 10)
    with pytest.raises(WorkerImportError) as ex:
        await import_file_via_worker(db, "t", path)
    assert ex.value.kind == "error"
    assert ex.value.error_type == "UnrecognizedFormat"
    assert ex.value.result.loader is None
    assert not ex.value.result.table_exists
    assert not await db.table_exists("t")


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
            argv=[sys.executable, script],
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
            delete_input=True,
            stop_timeout=0.5,
        )
    )
    await asyncio.wait_for(started.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gone(pids[0])
    # The input copy was deleted
    assert not os.path.exists(path)
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
