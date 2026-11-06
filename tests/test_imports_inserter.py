import asyncio
import random
import sqlite3
import time
import uuid

import pytest

from datasette.app import Datasette
from datasette.hookspecs import hookimpl
from datasette.imports.inserter import (
    TypedInserter,
    classify,
    import_rows,
    insert_rows,
    iter_chunks,
)
from datasette.plugins import pm

STRATEGIES = ("schema", "rebuild", "deferred")
IMMEDIATE = ("schema", "rebuild")


def declared(conn, table="t"):
    return {row[1]: row[2] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def column(conn, name, table="t"):
    return conn.execute(
        f'select "{name}", typeof("{name}") from "{table}" order by rowid'
    ).fetchall()


def load(rows, widen="schema", sample_size=3, batch_size=2, **kwargs):
    conn = sqlite3.connect(":memory:")
    result = insert_rows(
        conn,
        "t",
        rows,
        widen=widen,
        sample_size=sample_size,
        batch_size=batch_size,
        **kwargs,
    )
    return conn, result


@pytest.mark.parametrize(
    "value,expected",
    (
        ("1", "integer"),
        ("-42", "integer"),
        ("0", "integer"),
        ("-0", "integer"),
        (" 12", "integer"),
        ("12 ", "integer"),
        ("+5", "integer"),
        ("9223372036854775807", "integer"),
        ("-9223372036854775808", "integer"),
        ("9223372036854775808", "text"),
        ("007", "text"),
        ("00", "text"),
        ("1.5", "float"),
        ("1.50", "float"),
        ("1.0", "float"),
        ("1e3", "float"),
        ("1E-3", "float"),
        (".5", "float"),
        ("1.", "float"),
        ("0001.5", "text"),
        ("nan", "text"),
        ("inf", "text"),
        ("1_000", "text"),
        ("١٢", "text"),
        ("1,000", "text"),
        ("0x10", "text"),
        ("N/A", "text"),
        ("", None),
        (None, None),
        (5, "integer"),
        (True, "integer"),
        (2**63, "text"),
        (1.5, "float"),
        (5000.0, "float"),
        ([1, 2], "text"),
        ({"a": 1}, "text"),
        (b"\x00", "blob"),
    ),
)
def test_classify(value, expected):
    assert classify(value) == expected


def test_classify_agrees_with_sqlite_affinity():
    # Anything classified as a number must be stored as that kind of number
    # by SQLite's own affinity rules, so inserting the raw string is safe
    rng = random.Random(0)
    alphabet = " 0123456789.eE+-_x\t"
    candidates = {
        "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 6)))
        for _ in range(20000)
    }
    conn = sqlite3.connect(":memory:")
    conn.execute("create table t (i INTEGER, r REAL)")
    checked = {"integer": 0, "float": 0, "text": 0}
    for value in sorted(candidates):
        kind = classify(value)
        if kind is None:
            continue
        conn.execute("delete from t")
        conn.execute("insert into t values (?, ?)", [value, value])
        i_type, r_type = conn.execute("select typeof(i), typeof(r) from t").fetchone()
        if kind == "integer":
            assert (i_type, r_type) == ("integer", "real"), value
        elif kind == "float":
            assert r_type == "real", value
            assert i_type in ("integer", "real"), value
        else:
            # Text is only ever converted by SQLite when it is a number
            # with a superfluous leading zero - those are text on purpose
            if r_type != "text":
                assert value.strip().lstrip("+-").startswith("0"), value
        checked[kind] += 1
    assert all(checked.values()), checked


def test_iter_chunks():
    chunks = list(iter_chunks(range(25), sample_size=10, batch_size=4))
    assert [len(c) for c in chunks] == [10, 4, 4, 4, 3]
    assert list(iter_chunks([], 10, 4)) == []


@pytest.mark.parametrize("widen", STRATEGIES)
def test_detects_types_from_sample(widen):
    rows = [
        {"id": "1", "name": "Cleo", "weight": "5.5", "age": "3"},
        {"id": "2", "name": "Pancakes", "weight": "7", "age": ""},
        {"id": "3", "name": "Azi", "weight": "", "age": "11"},
    ]
    conn, result = load(rows, widen=widen)
    assert declared(conn) == {
        "id": "INTEGER",
        "name": "TEXT",
        "weight": "REAL",
        "age": "INTEGER",
    }
    assert result.types == {
        "id": "integer",
        "name": "text",
        "weight": "float",
        "age": "integer",
    }
    assert result.rows == 3
    assert result.widenings == []
    assert conn.execute("select * from t order by rowid").fetchall() == [
        (1, "Cleo", 5.5, 3),
        (2, "Pancakes", 7.0, None),
        (3, "Azi", None, 11),
    ]
    # A rowid table with no primary key
    sql = conn.execute("select sql from sqlite_master where name = 't'").fetchone()[0]
    assert "PRIMARY KEY" not in sql.upper()


@pytest.mark.parametrize("widen", STRATEGIES)
def test_widens_after_sample(widen):
    rows = [{"n": str(i), "f": str(i), "t": str(i)} for i in range(5)]
    rows += [
        {"n": "5", "f": "5.5", "t": "6"},
        {"n": "6", "f": "6", "t": "N/A"},
        {"n": "7", "f": "x", "t": "8"},
    ]
    conn, result = load(rows, widen=widen)
    assert declared(conn) == {"n": "INTEGER", "f": "TEXT", "t": "TEXT"}
    assert [(w.column, w.from_type, w.to_type, w.at_row) for w in result.widenings] == [
        ("f", "integer", "float", 5),
        ("t", "integer", "text", 5),
        ("f", "float", "text", 7),
    ]
    assert {w.method for w in result.widenings} == {widen}
    assert column(conn, "n") == [(i, "integer") for i in range(8)]
    assert column(conn, "t") == [
        ("0", "text"),
        ("1", "text"),
        ("2", "text"),
        ("3", "text"),
        ("4", "text"),
        ("6", "text"),
        ("N/A", "text"),
        ("8", "text"),
    ]
    f = column(conn, "f")
    assert {kind for _, kind in f} == {"text"}
    assert f[5:] == [("5.5", "text"), ("6", "text"), ("x", "text")]
    # Rows keep their rowids through a rebuild
    assert [r[0] for r in conn.execute("select rowid from t order by rowid")] == list(
        range(1, 9)
    )
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("widen", STRATEGIES)
def test_integer_to_float(widen):
    rows = [{"v": str(i)} for i in range(3)] + [{"v": "3.5"}, {"v": "4"}]
    conn, result = load(rows, widen=widen)
    assert declared(conn) == {"v": "REAL"}
    assert column(conn, "v") == [
        (0.0, "real"),
        (1.0, "real"),
        (2.0, "real"),
        (3.5, "real"),
        (4.0, "real"),
    ]
    assert result.types == {"v": "float"}


@pytest.mark.parametrize("widen", STRATEGIES)
def test_never_narrows(widen):
    rows = [{"a": "x", "b": "1.5"}] * 3 + [{"a": "1", "b": "2"}] * 4
    conn, result = load(rows, widen=widen)
    assert declared(conn) == {"a": "TEXT", "b": "REAL"}
    assert result.widenings == []
    assert column(conn, "a")[-1] == ("1", "text")
    assert column(conn, "b")[-1] == (2.0, "real")


@pytest.mark.parametrize("widen", IMMEDIATE)
def test_zero_padded_value_after_sample_is_preserved(widen):
    rows = [{"zip": "10001"}, {"zip": "94103"}, {"zip": "60601"}, {"zip": "02134"}]
    conn, _ = load(rows, widen=widen)
    assert declared(conn) == {"zip": "TEXT"}
    assert [v for v, _ in column(conn, "zip")] == ["10001", "94103", "60601", "02134"]


def test_deferred_cannot_preserve_zero_padded_value():
    # Documents why "deferred" is not the default: until the declared type
    # changes, INTEGER affinity rewrites "02134" as 2134
    rows = [{"zip": "10001"}, {"zip": "94103"}, {"zip": "60601"}, {"zip": "02134"}]
    conn, _ = load(rows, widen="deferred")
    assert declared(conn) == {"zip": "TEXT"}
    assert column(conn, "zip")[-1] == ("2134", "text")


@pytest.mark.parametrize("widen", STRATEGIES)
def test_empty_strings(widen):
    rows = [
        {"num": "1", "txt": "a", "late": "1"},
        {"num": "", "txt": "", "late": ""},
        {"num": "3", "txt": "c", "late": "3"},
        {"num": "", "txt": "", "late": ""},
        {"num": "5", "txt": "e", "late": "five"},
    ]
    conn, _ = load(rows, widen=widen)
    assert declared(conn) == {"num": "INTEGER", "txt": "TEXT", "late": "TEXT"}
    # NULL in numeric columns, "" in text columns - as sqlite-utils does
    assert [v for v, _ in column(conn, "num")] == [1, None, 3, None, 5]
    assert [v for v, _ in column(conn, "txt")] == ["a", "", "c", "", "e"]
    # Widening to text turns the NULLs that were "" back into ""
    assert [v for v, _ in column(conn, "late")] == ["1", "", "3", "", "five"]


@pytest.mark.parametrize("widen", STRATEGIES)
def test_real_nulls_stay_null_when_widening_to_text(widen):
    rows = [{"v": 1}, {"v": None}, {"v": 3}, {"v": ""}, {"v": "x"}]
    conn, _ = load(rows, widen=widen)
    # "" arrived in the same batch as "x", so it was stored as text
    assert [v for v, _ in column(conn, "v")] == ["1", None, "3", "", "x"]


@pytest.mark.parametrize("widen", STRATEGIES)
def test_new_keys_add_columns(widen):
    rows = [{"a": "1"}, {"a": "2", "b": "x"}, {"a": "3"}]
    rows += [{"a": "4", "c": "1.5"}, {"c": "2", "a": "5", "d": "7"}, {"a": "6"}]
    conn, result = load(rows, widen=widen)
    assert declared(conn) == {"a": "INTEGER", "b": "TEXT", "c": "REAL", "d": "INTEGER"}
    assert conn.execute("select a, b, c, d from t order by rowid").fetchall() == [
        (1, None, None, None),
        (2, "x", None, None),
        (3, None, None, None),
        (4, None, 1.5, None),
        (5, None, 2.0, 7),
        (6, None, None, None),
    ]
    assert result.widenings == []


@pytest.mark.parametrize("widen", STRATEGIES)
def test_new_column_later_widens_with_nulls_kept(widen):
    rows = [{"a": "1"}] * 3 + [{"a": "1", "b": "5"}, {"a": "1", "b": ""}]
    rows += [{"a": "1", "b": "x"}]
    conn, _ = load(rows, widen=widen)
    # Earlier rows never had "b" so they stay NULL, "" stays ""... except
    # that "" arrived while the column was INTEGER and is now NULL as well:
    # once a column holds real NULLs the two can't be told apart
    assert [v for v, _ in column(conn, "b")] == [None, None, None, "5", None, "x"]


@pytest.mark.parametrize("widen", STRATEGIES)
def test_json_values(widen):
    rows = [
        {"id": 1, "tags": ["a", "b"], "meta": {"x": 1}, "ok": True, "score": 1.5},
        {"id": 2, "tags": [], "meta": None, "ok": False, "score": 2},
        {"id": 3, "tags": ["é"], "meta": {"y": [1]}, "ok": None, "score": None},
        {"id": 4, "tags": None, "meta": {}, "ok": True, "score": 2**70},
    ]
    conn, _result = load(rows, widen=widen)
    assert declared(conn) == {
        "id": "INTEGER",
        "tags": "TEXT",
        "meta": "TEXT",
        "ok": "INTEGER",
        "score": "TEXT",
    }
    assert conn.execute("select * from t order by rowid").fetchall() == [
        (1, '["a", "b"]', '{"x": 1}', 1, "1.5"),
        (2, "[]", None, 0, "2"),
        (3, '["é"]', '{"y": [1]}', None, None),
        (
            4,
            None,
            "{}",
            1,
            # "deferred" inserts it into the REAL column, which rounds it
            "1.1805916207174113e+21" if widen == "deferred" else str(2**70),
        ),
    ]


def test_json_floats_detected_as_float():
    # sqlite_utils' TypeTracker calls these "integer" because int(1.5) works
    conn, result = load([{"v": 1.5}, {"v": 2.25}, {"v": 3.0}])
    assert result.types == {"v": "float"}
    assert [v for v, _ in column(conn, "v")] == [1.5, 2.25, 3.0]


def test_bytes_make_blob_column():
    conn, _result = load([{"b": b"\x00\x01"}, {"b": None}, {"b": b"x"}])
    assert declared(conn) == {"b": "BLOB"}
    assert column(conn, "b")[0] == (b"\x00\x01", "blob")


@pytest.mark.parametrize("widen", STRATEGIES)
def test_awkward_column_names(widen):
    rows = [
        {
            "": "a",
            "  ": "b",
            "Name": "c",
            "name": "d",
            "NAME_2": "e",
            "rowid": "f",
            'quote"d': "g",
            "[brackets]": "h",
            "with space.dot": "i",
            None: "j",
            7: "k",
        }
    ] * 4
    rows += [{"name": "1", "NAME": "x"}]
    conn, result = load(rows, widen=widen)
    names = list(declared(conn))
    assert names == [
        "untitled",
        "untitled_2",
        "Name",
        "name_2",
        "NAME_2_2",
        "rowid_2",
        'quote"d',
        "[brackets]",
        "with space.dot",
        "untitled_3",
        "7",
        "NAME_3",
    ]
    assert result.renamed == {
        "": "untitled",
        "  ": "untitled_2",
        "name": "name_2",
        "NAME_2": "NAME_2_2",
        "rowid": "rowid_2",
        None: "untitled_3",
        7: "7",
        "NAME": "NAME_3",
    }
    # rowid still means the real rowid
    assert [r[0] for r in conn.execute("select rowid from t order by rowid")] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert conn.execute('select "quote""d", "rowid_2" from t limit 1').fetchone() == (
        "g",
        "f",
    )


def test_zero_rows_with_known_columns():
    conn = sqlite3.connect(":memory:")
    result = insert_rows(conn, "t", [], schema={"id": None, "name": None})
    assert result.created
    assert result.rows == 0
    assert declared(conn) == {"id": "TEXT", "name": "TEXT"}


def test_zero_rows_without_columns_creates_nothing():
    conn = sqlite3.connect(":memory:")
    result = insert_rows(conn, "t", [])
    assert not result.created
    assert result.rows == 0
    assert conn.execute("select count(*) from sqlite_master").fetchone()[0] == 0


@pytest.mark.parametrize("widen", STRATEGIES)
def test_all_empty_column(widen):
    rows = [{"a": "1", "empty": ""} for _ in range(6)]
    conn, result = load(rows, widen=widen)
    # Like sqlite-utils: an all-empty column is INTEGER, all NULL
    assert declared(conn)["empty"] == "INTEGER"
    assert {v for v, _ in column(conn, "empty")} == {None}
    assert result.widenings == []


@pytest.mark.parametrize("widen", STRATEGIES)
def test_empty_sample_column_takes_type_from_later_rows(widen):
    rows = [{"a": ""}] * 3 + [{"a": "2"}, {"a": "2.5"}, {"a": ""}]
    conn, result = load(rows, widen=widen)
    assert declared(conn) == {"a": "REAL"}
    assert [v for v, _ in column(conn, "a")] == [None, None, None, 2.0, 2.5, None]
    assert [(w.from_type, w.to_type) for w in result.widenings] == [
        ("integer", "float")
    ]


@pytest.mark.parametrize("widen", STRATEGIES)
def test_explicit_schema_skips_detection(widen):
    schema = {"id": "integer", "code": str, "score": "REAL", "extra": None}
    rows = [
        {"id": 1, "code": "001", "score": 1, "extra": "1", "new": "x"},
        {"id": 2, "code": "002", "score": 2, "extra": "2", "new": "y"},
        {"id": "three", "code": 3, "score": 3, "extra": "3", "new": "z"},
        {"id": 4, "code": "004", "score": "4", "extra": "3.5", "new": 1},
    ]
    conn, result = load(rows, widen=widen, schema=schema)
    assert declared(conn) == {
        "id": "INTEGER",
        "code": "TEXT",
        "score": "REAL",
        "extra": "REAL",
        "new": "TEXT",
    }
    # Typed columns are trusted, not widened: SQLite keeps "three" as text
    assert [w.column for w in result.widenings] == ["extra"]
    assert column(conn, "id")[2] == ("three", "text")
    assert column(conn, "code")[0] == ("001", "text")


def test_schema_type_validation():
    with pytest.raises(ValueError):
        TypedInserter("t", schema={"a": "geometry"})
    with pytest.raises(ValueError):
        TypedInserter("t", widen="sometimes")


def test_schema_strategy_falls_back_to_rebuild():
    conn = sqlite3.connect(":memory:")
    inserter = TypedInserter("t")
    inserter.write(conn, [{"a": "1"}, {"a": "2"}])
    # Someone else adds an index: editing the schema in place is not safe
    conn.execute("create index t_a on t(a)")
    inserter.write(conn, [{"a": "x"}])
    inserter.finish(conn)
    assert [w.method for w in inserter.widenings] == ["rebuild (fallback)"]
    assert declared(conn) == {"a": "TEXT"}
    assert [v for v, _ in column(conn, "a")] == ["1", "2", "x"]


def test_schema_strategy_after_add_column():
    conn = sqlite3.connect(":memory:")
    inserter = TypedInserter("t")
    inserter.write(conn, [{"a": "1"}])
    inserter.write(conn, [{"a": "2", "b": "3"}])
    inserter.write(conn, [{"a": "2.5", "b": "x"}])
    inserter.finish(conn)
    assert [w.method for w in inserter.widenings] == ["schema", "schema"]
    assert declared(conn) == {"a": "REAL", "b": "TEXT"}
    assert conn.execute("select a, b from t order by rowid").fetchall() == [
        (1.0, None),
        (2.0, "3"),
        (2.5, "x"),
    ]


def test_writer_does_not_commit():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t")
    conn.execute("BEGIN")
    inserter.write(conn, [{"a": "1"}])
    inserter.write(conn, [{"a": "x"}])
    assert conn.in_transaction
    conn.execute("ROLLBACK")
    assert conn.execute("select count(*) from sqlite_master").fetchone()[0] == 0


def test_insert_rows_commits_each_chunk(tmp_path):
    path = str(tmp_path / "data.db")
    conn = sqlite3.connect(path)
    reader = sqlite3.connect(path)
    seen = []

    def progress(done):
        count = reader.execute("select count(*) from t").fetchone()[0]
        seen.append((done, count))

    rows = ({"n": str(i)} for i in range(25))
    result = insert_rows(
        conn, "t", rows, sample_size=10, batch_size=5, progress=progress
    )
    assert seen == [(10, 10), (15, 15), (20, 20), (25, 25)]
    assert result.batches == 4
    assert not conn.in_transaction


def test_insert_rows_autocommit_false(tmp_path):
    if not hasattr(sqlite3.Connection, "autocommit"):
        pytest.skip("Python 3.12+ only")
    path = str(tmp_path / "data.db")
    conn = sqlite3.connect(path, autocommit=False)
    insert_rows(conn, "t", [{"n": "1"}, {"n": "x"}], sample_size=1, batch_size=1)
    assert sqlite3.connect(path).execute("select count(*) from t").fetchone()[0] == 2


# Datasette integration ------------------------------------------------------


def events_rows(count):
    for i in range(count):
        yield {
            "id": str(i),
            "late_float": str(i + 0.5) if i >= 1500 else str(i),
            "late_text": f"N/A-{i}" if i >= 2000 else str(i),
            "maybe_empty": "" if i % 20 == 0 else str(i),
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("widen", STRATEGIES)
async def test_import_rows_into_datasette(widen):
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    writes = []

    class Plugin:
        __name__ = "Plugin"

        @staticmethod
        @hookimpl
        def write_wrapper(datasette, database, request, transaction):
            def wrapper(conn):
                yield
                writes.append(transaction)

            return wrapper

    progress = []

    async def on_progress(done):
        # Every chunk is committed before the next one starts
        count = (await db.execute("select count(*) from events")).single_value()
        progress.append((done, count))

    pm.register(Plugin(), name="test_import_rows_write_wrapper")
    try:
        result = await import_rows(
            db,
            "events",
            events_rows(2500),
            widen=widen,
            batch_size=500,
            progress=on_progress,
        )
    finally:
        pm.unregister(name="test_import_rows_write_wrapper")
    # Sample of 1000, then batches of 500, then finish()
    assert progress == [(1000, 1000), (1500, 1500), (2000, 2000), (2500, 2500)]
    assert writes == [True] * 5
    assert result.rows == 2500
    assert result.types == {
        "id": "integer",
        "late_float": "float",
        "late_text": "text",
        "maybe_empty": "integer",
    }
    assert [(w.column, w.at_row) for w in result.widenings] == [
        ("late_float", 1500),
        ("late_text", 2000),
    ]
    columns = await db.execute("select name, type from pragma_table_info('events')")
    assert [tuple(r) for r in columns] == [
        ("id", "INTEGER"),
        ("late_float", "REAL"),
        ("late_text", "TEXT"),
        ("maybe_empty", "INTEGER"),
    ]
    row = (
        await db.execute(
            "select late_float, late_text, maybe_empty from events where id = 2100"
        )
    ).first()
    assert tuple(row) == (2100.5, "N/A-2100", None)
    types = await db.execute(
        "select typeof(late_text), count(*) from events group by 1"
    )
    assert [tuple(r) for r in types] == [("text", 2500)]


async def _async_rows(count):
    for row in events_rows(count):
        if int(row["id"]) % 100 == 0:
            await asyncio.sleep(0)
        yield row


@pytest.mark.asyncio
async def test_import_rows_async_iterable():
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    result = await import_rows(db, "events", _async_rows(1234), batch_size=100)
    assert result.rows == 1234
    assert result.batches == 4
    count = (await db.execute("select count(*) from events")).single_value()
    assert count == 1234


@pytest.mark.asyncio
async def test_import_rows_does_not_block_event_loop():
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")

    def slow_rows():
        for i in range(30):
            time.sleep(0.01)  # e.g. reading from a slow file
            yield {"n": str(i)}

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    task = asyncio.ensure_future(ticker())
    try:
        result = await import_rows(db, "t", slow_rows(), sample_size=10, batch_size=10)
    finally:
        task.cancel()
    assert result.rows == 30
    # 0.3s of blocking reads; if they ran on the event loop ticks would be ~0
    assert ticks > 20


@pytest.mark.asyncio
async def test_import_rows_error_propagates():
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    await db.execute_write("create table t (a text)")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        await import_rows(db, "t", events_rows(50), sample_size=10, batch_size=10)
