import asyncio
import contextlib
import json
import math
import random
import sqlite3
import threading
import time
import uuid
from unittest import mock

import pytest

from datasette.app import Datasette
from datasette.hookspecs import hookimpl
from datasette.imports import inserter as inserter_module
from datasette.imports.inserter import (
    _F,
    _I,
    TypedInserter,
    _canonical,
    _odd_positions,
    _render_real,
    classify,
    import_rows,
    insert_rows,
    iter_chunks,
)
from datasette.plugins import pm
from datasette.utils.sqlite import sqlite3 as datasette_sqlite3

# The ways a column can be widened:
# - "auto": the declared type is edited in place, and stored numbers that
#   have to become text are converted in slices of about 30ms
# - "sliced": the same, converting one row per slice, so the backlog of
#   stored numbers to turn into text lasts as long as possible
# - "fallback": the declared type cannot be edited, so every widening
#   rebuilds the table. The connection refuses writable_schema
#   (SQLITE_DBCONFIG_DEFENSIVE) where Connection.setconfig() exists (Python
#   3.12+); elsewhere TypedInserter._write_schema() is patched to refuse
WIDENINGS = ("auto", "sliced", "fallback")
STRATEGIES = AVAILABLE = WIDENINGS


def can_refuse_writable_schema(module=sqlite3):
    "Whether ``module``'s connections can be made to refuse writable_schema."
    return hasattr(module.Connection, "setconfig")


needs_setconfig = pytest.mark.skipif(
    not can_refuse_writable_schema(), reason="Connection.setconfig() is Python 3.12+"
)


def refuse_writable_schema(conn):
    conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)


def _refused(self, conn):
    return False


def connect(widen="auto", database=":memory:", **kwargs):
    conn = sqlite3.connect(database, **kwargs)
    if widen == "fallback" and can_refuse_writable_schema():
        refuse_writable_schema(conn)
    return conn


def sliced_inserter(table, convert_rows, **kwargs):
    "A TypedInserter that converts ``convert_rows`` rows per slice."
    inserter = TypedInserter(table, **kwargs)
    inserter._convert_rows = convert_rows
    return inserter


def new_inserter(table, widen, **kwargs):
    "An inserter for ``widen`` - use it with a connection from connect(widen)."
    if widen == "sliced":
        return sliced_inserter(table, 1, **kwargs)
    inserter = TypedInserter(table, **kwargs)
    if widen == "fallback" and not can_refuse_writable_schema():
        inserter._write_schema = _refused.__get__(inserter)
    return inserter


@contextlib.contextmanager
def slices_of(convert_rows):
    "Every TypedInserter created in this block converts that many rows a slice."
    with mock.patch.object(TypedInserter, "_convert_rows", convert_rows):
        yield


def widening(widen, module=sqlite3):
    """
    What insert_rows() and import_rows() need for ``widen``, as a context,
    on connections from ``module`` (made by connect(widen) if it is
    sqlite3).
    """
    if widen == "sliced":
        return slices_of(1)
    if widen == "fallback" and not can_refuse_writable_schema(module):
        return mock.patch.object(TypedInserter, "_write_schema", _refused)
    return contextlib.nullcontext()


def declared(conn, table="t"):
    return {row[1]: row[2] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def column(conn, name, table="t"):
    return conn.execute(
        f'select "{name}", typeof("{name}") from "{table}" order by rowid'
    ).fetchall()


def load(rows, widen="auto", sample_size=3, batch_size=2, **kwargs):
    conn = connect(widen)
    with widening(widen):
        result = insert_rows(
            conn, "t", rows, sample_size=sample_size, batch_size=batch_size, **kwargs
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
            # with a superfluous leading zero, or one a float cannot hold
            # (1e999 is infinity, 1e-999 zero) - those are text on purpose
            if r_type != "text":
                number = float(value)
                assert (
                    value.strip().lstrip("+-").startswith("0")
                    or math.isinf(number)
                    or number == 0
                ), value
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
    if widen == "fallback":
        expected = ["rebuild"] * 3
    else:
        expected = ["schema", "incremental", "incremental"]
    assert [w.method for w in result.widenings] == expected
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


@pytest.mark.parametrize("widen", STRATEGIES)
def test_zero_padded_value_after_sample_is_preserved(widen):
    rows = [{"zip": "10001"}, {"zip": "94103"}, {"zip": "60601"}, {"zip": "02134"}]
    conn, _ = load(rows, widen=widen)
    assert declared(conn) == {"zip": "TEXT"}
    assert [v for v, _ in column(conn, "zip")] == ["10001", "94103", "60601", "02134"]


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
    # Earlier rows never had "b" so they stay NULL; the "" arrived while
    # the column was INTEGER (stored as NULL) and is "" again
    assert [v for v, _ in column(conn, "b")] == [None, None, None, "5", "", "x"]


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
            str(2**70),
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


def test_widening_falls_back_to_rebuild_if_someone_adds_an_index():
    conn = sqlite3.connect(":memory:")
    inserter = TypedInserter("t")
    inserter.write(conn, [{"a": "1"}, {"a": "2"}])
    # Someone else adds an index: editing the schema in place is not safe
    conn.execute("create index t_a on t(a)")
    inserter.write(conn, [{"a": "x"}])
    inserter.finish(conn)
    assert [w.method for w in inserter.widenings] == ["rebuild"]
    assert declared(conn) == {"a": "TEXT"}
    assert [v for v, _ in column(conn, "a")] == ["1", "2", "x"]


def test_widening_after_add_column():
    conn = sqlite3.connect(":memory:")
    inserter = TypedInserter("t")
    inserter.write(conn, [{"a": "1"}])
    inserter.write(conn, [{"a": "2", "b": "3"}])
    inserter.write(conn, [{"a": "2.5", "b": "x"}])
    inserter.finish(conn)
    assert [w.method for w in inserter.widenings] == ["schema", "incremental"]
    assert declared(conn) == {"a": "REAL", "b": "TEXT"}
    assert conn.execute("select a, b from t order by rowid").fetchall() == [
        (1.0, None),
        (2.0, "3"),
        (2.5, "x"),
    ]


def test_auto_strategy_edits_in_place_unless_values_must_change():
    conn = sqlite3.connect(":memory:")
    inserter = TypedInserter("t")
    inserter.write(conn, [{"a": "1", "b": "1"}])
    inserter.write(conn, [{"a": "1.5", "b": "2"}])
    inserter.write(conn, [{"a": "2", "b": "x"}])
    inserter.write(conn, [{"a": "y", "b": "3"}])
    inserter.finish(conn)
    assert [(w.column, w.to_type, w.method) for w in inserter.widenings] == [
        ("a", "float", "schema"),
        ("b", "text", "incremental"),
        ("a", "text", "incremental"),
    ]
    assert conn.execute("select a, typeof(a), b from t order by rowid").fetchall() == [
        ("1", "text", "1"),
        ("1.5", "text", "2"),
        ("2", "text", "x"),
        ("y", "text", "3"),
    ]


def test_schema_edit_visible_to_other_shared_cache_connections():
    uri = f"file:{uuid.uuid4().hex}?mode=memory&cache=shared"
    writer = sqlite3.connect(uri, uri=True, isolation_level=None)
    reader = sqlite3.connect(uri, uri=True)
    reader.execute("PRAGMA query_only=1")
    result = insert_rows(
        writer, "t", [{"a": "1"}, {"a": "2.5"}], sample_size=1, batch_size=1
    )
    assert [w.method for w in result.widenings] == ["schema"]
    assert reader.execute("select type from pragma_table_info('t')").fetchall() == [
        ("REAL",)
    ]
    assert reader.execute("select a, typeof(a) from t").fetchall() == [
        (1.0, "real"),
        (2.5, "real"),
    ]


def test_rebuild_keeps_indexes_added_by_others():
    conn = sqlite3.connect(":memory:")
    inserter = TypedInserter("t")
    inserter.write(conn, [{"a": "1", "b": "1"}])
    conn.execute("create index t_b on t(b)")
    inserter.write(conn, [{"a": "x", "b": "2"}])
    assert [w.method for w in inserter.widenings] == ["rebuild"]
    assert [r[1] for r in conn.execute("PRAGMA index_list(t)")] == ["t_b"]
    assert declared(conn) == {"a": "TEXT", "b": "INTEGER"}


@pytest.mark.parametrize("widen", STRATEGIES)
def test_retry_after_rollback(widen):
    conn = connect(widen, isolation_level=None)
    inserter = new_inserter("t", widen)
    conn.execute("BEGIN")
    inserter.write(conn, [{"a": "1"}, {"a": "2"}])
    conn.execute("COMMIT")
    chunk = [{"a": "2.5", "b": "x"}, {"a": "N/A", "b": "y"}]
    # The transaction is rolled back after write() returned
    snapshot = inserter.snapshot()
    conn.execute("BEGIN")
    inserter.write(conn, chunk)
    conn.execute("ROLLBACK")
    inserter.restore(snapshot)
    assert declared(conn) == {"a": "INTEGER"}
    assert inserter.rows_written == 2
    assert inserter.widenings == []
    # Now it can simply be written again
    conn.execute("BEGIN")
    inserter.write(conn, chunk)
    inserter.finish(conn)
    conn.execute("COMMIT")
    assert declared(conn) == {"a": "TEXT", "b": "TEXT"}
    assert conn.execute("select a, b from t order by rowid").fetchall() == [
        ("1", None),
        ("2", None),
        ("2.5", "x"),
        ("N/A", "y"),
    ]
    assert len(inserter.widenings) == 1


def _inserts(conn):
    "Record the INSERT statements run on ``conn`` into table t."
    statements = []

    def trace(sql):
        if sql.startswith('INSERT INTO "t"'):
            statements.append(sql)

    conn.set_trace_callback(trace)
    return statements


def test_a_chunk_is_one_insert_statement():
    # executemany() would step a statement per row, releasing and taking
    # back the GIL each time; one multi-row INSERT does it once
    conn = sqlite3.connect(":memory:", isolation_level=None)
    statements = _inserts(conn)
    rows = [{"a": str(i), "b": f"x{i}", "c": i * 1.5} for i in range(1000)]
    insert_rows(conn, "t", rows, sample_size=1000)
    assert len(statements) == 1
    assert conn.execute("select count(*), sum(a), max(c) from t").fetchone() == (
        1000,
        499500,
        999 * 1.5,
    )


def test_insert_statements_respect_the_parameter_limit():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    # Seven parameters: two rows of three columns a statement
    conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 7)
    statements = _inserts(conn)
    inserter = TypedInserter("t")
    inserter.write(conn, [{"a": str(i), "b": "x", "c": "1.5"} for i in range(9)])
    assert len(statements) == 5
    # A fourth column: one row a statement, as executemany() does
    statements.clear()
    inserter.write(conn, [{"a": "9", "b": "y", "c": "2", "d": "z"}] * 3)
    assert len(statements) == 3
    # Wider than the limit allows for two rows
    statements.clear()
    conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 4)
    inserter.write(conn, [{"a": "10"}, {"a": "11"}])
    assert len(statements) == 2
    inserter.finish(conn)
    assert conn.execute("select rowid, a, b, d from t order by rowid").fetchall() == [
        *((i + 1, i, "x", None) for i in range(9)),
        (10, 9, "y", "z"),
        (11, 9, "y", "z"),
        (12, 9, "y", "z"),
        (13, 10, None, None),
        (14, 11, None, None),
    ]


class WithoutGetlimit:
    "A connection with no getlimit(), like pysqlite3's."

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        if name == "getlimit":
            raise AttributeError(name)
        return getattr(self._conn, name)


def test_connection_without_getlimit():
    # pysqlite3, which Datasette uses in place of sqlite3 when it is
    # installed, has no Connection.getlimit(): the limit SQLite was
    # compiled with applies
    conn = sqlite3.connect(":memory:", isolation_level=None)
    statements = _inserts(conn)
    rows = [{"a": str(i), "b": f"x{i}", "c": i * 1.5} for i in range(1000)]
    result = insert_rows(WithoutGetlimit(conn), "t", rows, sample_size=1000)
    assert result.rows == 1000
    compiled = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    assert len(statements) == -(-3000 // (compiled // 3 * 3))
    assert conn.execute("select count(*), sum(a), max(c) from t").fetchone() == (
        1000,
        499500,
        999 * 1.5,
    )


class FakeConnection:
    def __init__(self, compile_options, version):
        self.compile_options = compile_options
        self.version = version

    def execute(self, sql):
        if sql == "PRAGMA compile_options":
            return iter([(option,) for option in self.compile_options])
        assert sql == "select sqlite_version()"
        return mock.Mock(fetchone=lambda: (self.version,))


@pytest.mark.parametrize(
    "compile_options,version,expected",
    (
        (["THREADSAFE=1", "MAX_VARIABLE_NUMBER=250000"], "3.51.1", 250000),
        (["THREADSAFE=1"], "3.51.1", 32766),
        ([], "3.32.0", 32766),
        ([], "3.31.1", 999),
        ([], "3.7.17", 999),
    ),
)
def test_variable_limit_without_getlimit(compile_options, version, expected):
    conn = FakeConnection(compile_options, version)
    assert inserter_module._variable_limit(conn) == expected


def test_pysqlite3_connection():
    pysqlite3 = pytest.importorskip("pysqlite3")
    conn = pysqlite3.connect(":memory:", isolation_level=None)
    rows = [{"a": str(i), "b": "x" if i == 1500 else str(i)} for i in range(2000)]
    result = insert_rows(conn, "t", rows, sample_size=1000, batch_size=500)
    assert result.types == {"a": "integer", "b": "text"}
    assert conn.execute("select count(*), sum(a), max(b) from t").fetchone() == (
        2000,
        sum(range(2000)),
        "x",
    )
    assert [w.method for w in result.widenings] == ["incremental"]

    # Its exceptions are not sqlite3's: a refused schema edit still means
    # rebuilding the table
    class RefusesSchemaEdits(WithoutGetlimit):
        def execute(self, sql, *args):
            if (
                sql
                == "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?"
            ):
                raise pysqlite3.dbapi2.DatabaseError(
                    "table sqlite_master may not be modified"
                )
            return self._conn.execute(sql, *args)

    conn = pysqlite3.connect(":memory:", isolation_level=None)
    result = insert_rows(
        RefusesSchemaEdits(conn), "t", rows, sample_size=1000, batch_size=500
    )
    assert [w.method for w in result.widenings] == ["rebuild"]
    assert conn.execute("select count(*), sum(a), max(b) from t").fetchone() == (
        2000,
        sum(range(2000)),
        "x",
    )


def test_retry_without_the_column_a_rolled_back_chunk_added():
    # The rolled back chunk added a column; the next one does not have it,
    # so its INSERT must not name it
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t")
    inserter.write(conn, [{"a": "1"}, {"a": "2"}])
    snapshot = inserter.snapshot()
    conn.execute("BEGIN")
    inserter.write(conn, [{"a": "3", "b": "new"}, {"a": "4", "b": "new"}])
    conn.execute("ROLLBACK")
    inserter.restore(snapshot)
    inserter.write(conn, [{"a": "5"}, {"a": "6"}])
    inserter.finish(conn)
    assert conn.execute("select * from t order by rowid").fetchall() == [
        (1,),
        (2,),
        (5,),
        (6,),
    ]


def test_write_that_raises_leaves_state_unchanged():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t2")
    conn.execute("BEGIN")
    inserter.write(conn, [{"a": "1"}])
    conn.execute("COMMIT")
    conn.execute(
        "create trigger no_boom before insert on t2 when new.a = 'boom' "
        "begin select raise(abort, 'no boom'); end"
    )
    conn.execute("BEGIN")
    with pytest.raises(sqlite3.IntegrityError, match="no boom"):
        inserter.write(conn, [{"a": "boom", "c": "1"}])
    conn.execute("ROLLBACK")
    assert inserter.rows_written == 1
    assert inserter.types == {"a": "integer"}
    assert inserter.widenings == []
    conn.execute("BEGIN")
    inserter.write(conn, [{"a": "2", "c": "1.5"}])
    conn.execute("COMMIT")
    assert declared(conn, "t2") == {"a": "INTEGER", "c": "REAL"}
    assert conn.execute("select a, c from t2").fetchall() == [(1, None), (2, 1.5)]


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


# Nothing is lost on widening ---------------------------------------------


@pytest.mark.parametrize("widen", STRATEGIES)
def test_float_text_kept_when_column_widens_to_text(widen):
    # A version-number column that looks like floats for the whole sample:
    # "1.10" must not come back as "1.1", nor "3.0" as "3"
    rows = [
        {"version": "1.10"},
        {"version": "2.50"},
        {"version": "3.0"},
        {"version": "1e3"},
        {"version": "1.5"},
        {"version": "2.0.1"},
    ]
    conn, result = load(rows, widen=widen, sample_size=5, batch_size=1)
    assert declared(conn) == {"version": "TEXT"}
    assert [v for v, _ in column(conn, "version")] == [
        "1.10",
        "2.50",
        "3.0",
        "1e3",
        "1.5",
        "2.0.1",
    ]
    # Only the values a REAL would not give back had to be kept
    assert result.spellings_kept == 4
    # ...and only until the import finished
    assert conn.execute("select count(*) from sqlite_temp_master").fetchone() == (0,)


@pytest.mark.parametrize("widen", STRATEGIES)
def test_odd_integer_text_kept_when_column_widens_to_text(widen):
    rows = [{"a": "-0"}, {"a": "+5"}, {"a": " 12"}, {"a": "7"}, {"a": "x"}]
    conn, result = load(rows, widen=widen, sample_size=4, batch_size=1)
    assert [v for v, _ in column(conn, "a")] == ["-0", "+5", " 12", "7", "x"]
    assert result.spellings_kept == 3


@pytest.mark.parametrize("widen", STRATEGIES)
def test_odd_text_converted_while_column_stays_numeric(widen):
    # The kept text only matters if the column becomes text
    rows = [{"a": " 12"}, {"a": "+5"}, {"a": "1.50"}, {"a": "1e3"}, {"a": "7"}]
    conn, _ = load(rows, widen=widen, sample_size=2, batch_size=1)
    assert declared(conn) == {"a": "REAL"}
    assert column(conn, "a") == [
        (12.0, "real"),
        (5.0, "real"),
        (1.5, "real"),
        (1000.0, "real"),
        (7.0, "real"),
    ]


@pytest.mark.parametrize("widen", STRATEGIES)
def test_big_integers_kept_through_float_to_text(widen):
    # 2**53 + 1 is exact in an INTEGER column but a REAL rounds it
    big = str(2**53 + 1)
    rows = [{"a": big}, {"a": "1"}, {"a": "1.5"}, {"a": big}, {"a": "x"}]
    conn, _ = load(rows, widen=widen, sample_size=2, batch_size=1)
    assert [v for v, _ in column(conn, "a")] == [big, "1", "1.5", big, "x"]


@pytest.mark.parametrize("widen", STRATEGIES)
def test_blank_and_null_told_apart_when_widening_to_text(widen):
    rows = [{"v": "1"}, {"v": ""}, {"v": None}, {"v": "2"}, {"v": None}]
    rows += [{"v": ""}, {}, {"v": "x"}]
    conn, _ = load(rows, widen=widen, sample_size=2, batch_size=2)
    assert [v for v, _ in column(conn, "v")] == [
        "1",
        "",
        None,
        "2",
        None,
        "",
        None,
        "x",
    ]


@pytest.mark.parametrize("widen", STRATEGIES)
def test_json_floats_become_the_same_text_however_chunked(widen):
    rows = [{"f": 3.0}, {"f": 0.1 + 0.2}, {"f": 1e20}, {"f": float("inf")}]
    rows.append({"f": "x"})
    expected = ["3", "0.30000000000000004", "1e+20", "inf", "x"]
    for sample_size in (1, 4, 5):
        conn, _ = load(rows, widen=widen, sample_size=sample_size, batch_size=1)
        assert [v for v, _ in column(conn, "f")] == expected, sample_size


def _tricky_rows(rng, count):
    """
    Rows whose columns mostly hold one kind of value with the occasional
    odd one, so columns widen at random points after the sample.
    """
    profiles = {
        "int": ["1", "-3", "0", "42", "123456789012"],
        "float": ["1.5", "0.25", "-2.75", "3", "1000.125"],
        "json_number": [1, -2, 0, 1.5, 3.0, True, 0.1 + 0.2],
        "text": ["x", "N/A", "hello", "007"],
    }
    rare = [
        "",
        None,
        "-0",
        "+5",
        " 12",
        "12 ",
        "007",
        "1.50",
        "3.0",
        "1e3",
        ".5",
        "1.",
        "2.0.1",
        "N/A",
        str(2**53 + 1),
        "12345678901234567890123",
        "9223372036854775808",
        "3.141592653589793",
        "99999999999999.9",
        "0.00001",
        "1e400",
        "nan",
        2**60,
        2**70,
        1e20,
        float("inf"),
        float("nan"),
        [1, "a"],
        {"k": None},
        b"\x00\x01",
    ]
    names = [(f"c{i}", rng.choice(list(profiles))) for i in range(6)]
    rows = []
    for _ in range(count):
        row = {}
        for name, profile in names:
            roll = rng.random()
            if roll < 0.05:
                continue  # a missing key
            if roll < 0.2:
                row[name] = rng.choice(rare)
            else:
                row[name] = rng.choice(profiles[profile])
        if rng.random() < 0.1:
            # A key that only turns up later
            row[f"late{rng.randint(0, 2)}"] = rng.choice(rare + ["1", "2.5"])
        rows.append(row)
    return rows


def _snapshot(conn):
    names = list(declared(conn))
    select = ", ".join(f'"{n}", typeof("{n}")' for n in names)
    rows = conn.execute(f"select {select} from t order by rowid").fetchall()
    return declared(conn), rows


@pytest.mark.parametrize("seed", range(40))
def test_result_does_not_depend_on_chunk_boundaries(seed, monkeypatch):
    # Whatever the sample size, batch size and widening strategy, the table
    # must be what you get when the whole input is the sample. Odd seeds
    # bind floats instead of float text, as on a SQLite whose float parsing
    # differs from Python's
    monkeypatch.setattr(inserter_module, "_FLOATS_EXACT", seed % 2 == 0)
    rng = random.Random(seed)
    rows = _tricky_rows(rng, rng.randint(1, 40))
    reference_conn, _ = load(rows, sample_size=len(rows))
    reference = _snapshot(reference_conn)
    for widen in AVAILABLE:
        sample_size = rng.randint(1, 6)
        batch_size = rng.randint(1, 5)
        conn, _ = load(
            rows, widen=widen, sample_size=sample_size, batch_size=batch_size
        )
        assert _snapshot(conn) == reference, (widen, sample_size, batch_size, rows)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_fast_patterns_only_match_text_that_round_trips():
    # A canonical value keeps no spelling, so storing it in that column and
    # widening to text must give exactly it back
    rng = random.Random(1)
    conn = sqlite3.connect(":memory:")
    conn.execute("create table t (i INTEGER, r REAL)")
    candidates = {
        "".join(rng.choice("0123456789.-") for _ in range(rng.randint(1, 18)))
        for _ in range(20000)
    }
    candidates |= {
        repr(rng.uniform(-1e6, 1e6) * 10 ** rng.randint(-6, 9)) for _ in range(20000)
    }
    candidates |= {f"{rng.randint(0, 10**6) / 100}" for _ in range(5000)}
    matched = {"int": 0, "real": 0}
    for value in sorted(candidates):
        conn.execute("delete from t")
        if value and _canonical(value, _I):
            conn.execute("insert into t (i) values (?)", [value])
            stored = conn.execute("select i, typeof(i) from t").fetchone()
            assert stored == (int(value), "integer"), value
            assert str(stored[0]) == value
            matched["int"] += 1
        if value and _canonical(value, _F):
            conn.execute("delete from t")
            conn.execute("insert into t (r) values (?)", [float(value)])
            stored = conn.execute("select r from t").fetchone()[0]
            assert _render_real(stored) == value, value
            matched["real"] += 1
    assert matched["int"] > 100 and matched["real"] > 10000, matched
    # The column-at-once version finds the same values
    values = sorted(candidates)
    for rank in (_I, _F):
        odd = _odd_positions(values, rank) or []
        assert odd == [i for i, v in enumerate(values) if not _canonical(v, rank)]
    assert _odd_positions(["1", "", "22"], _I) is None
    assert _odd_positions(["1", "", "007", "1.5", ""], _I) == [2, 3]


def test_sqlite_parses_float_text_like_python(monkeypatch):
    # Canonical float text is bound as text and parsed by SQLite; keeping no
    # spelling for it relies on SQLite getting the same float as float().
    # The inserter checks this at runtime and binds floats if it fails, as
    # it does on SQLite 3.45, which gets about 1 in 12,000 of these wrong
    monkeypatch.setattr(inserter_module, "_FLOATS_EXACT", None)
    rng = random.Random(3)
    texts = [
        repr(rng.uniform(-1e9, 1e9) * 10 ** rng.randint(-12, 12)) for _ in range(5000)
    ]
    texts.extend(inserter_module._HARD_FLOATS)
    conn = sqlite3.connect(":memory:")
    if inserter_module._sqlite_parses_floats_exactly(conn):
        for text in texts:
            stored = conn.execute("select cast(? as real)", [text]).fetchone()[0]
            assert stored == float(text), text
    else:
        assert any(
            conn.execute("select cast(? as real)", [text]).fetchone()[0] != float(text)
            for text in inserter_module._HARD_FLOATS
        )
    # Either way the inserter stores exactly the float each text means, and
    # widening to text gives the text back
    conn, _ = load([{"x": text} for text in texts], sample_size=100, batch_size=1000)
    assert declared(conn) == {"x": "REAL"}
    stored = [value for value, _ in column(conn, "x")]
    assert stored == [float(text) for text in texts]


# Robustness -----------------------------------------------------------------


@pytest.mark.parametrize("widen", STRATEGIES)
@pytest.mark.parametrize(
    "setup",
    (
        "create view v as select * from t",
        "create view v2 as select * from does_not_exist",
    ),
)
def test_rebuild_with_views(widen, setup):
    # Since SQLite 3.26 RENAME checks all views: a view on the table (or any
    # broken view) used to make the rebuild fail
    conn = connect(widen, isolation_level=None)
    inserter = new_inserter("t", widen)
    inserter.write(conn, [{"a": "1"}, {"a": "2"}])
    conn.execute(setup)
    inserter.write(conn, [{"a": "x"}])
    inserter.finish(conn)
    assert declared(conn) == {"a": "TEXT"}
    assert [v for v, _ in column(conn, "a")] == ["1", "2", "x"]
    if "from t" in setup:
        assert conn.execute("select count(*) from v").fetchone() == (3,)
    # The connection setting is restored
    assert conn.execute("PRAGMA legacy_alter_table").fetchone() == (0,)


def test_huge_digit_strings():
    # int() refuses strings of over 4300 digits
    huge = "1" * 5000
    assert classify(huge) == "text"
    assert classify(" " * 5000 + "12") == "integer"
    conn, _ = load([{"a": "x"}, {"a": huge}])
    assert column(conn, "a")[1] == (huge, "text")
    # In a float column it used to be stored as infinity
    conn, result = load([{"a": "1.5"}, {"a": huge}], sample_size=1, batch_size=1)
    assert result.types == {"a": "text"}
    assert column(conn, "a") == [("1.5", "text"), (huge, "text")]


@pytest.mark.parametrize(
    "rows",
    (
        [{"x": "1.5"}, {"x": "12345678901234567890123"}, {"x": "1e5"}],
        [{"x": "1.5"}, {"x": "1e5"}, {"x": "12345678901234567890123"}],
    ),
)
@pytest.mark.parametrize("batch_size", (1, 2))
def test_big_integer_in_float_column_is_text_however_batched(rows, batch_size):
    conn, result = load(rows, sample_size=1, batch_size=batch_size)
    assert result.types == {"x": "text"}
    assert [v for v, _ in column(conn, "x")] == [r["x"] for r in rows]


def test_floats_that_do_not_fit_are_text():
    assert classify("1e400") == "text"
    assert classify("-1e400") == "text"
    assert classify("1e-400") == "text"
    assert classify("0e-400") == "float"
    assert classify("5e-324") == "float"
    conn, _ = load([{"a": "1e400"}, {"a": "1e-400"}, {"a": "1.5"}])
    assert column(conn, "a") == [
        ("1e400", "text"),
        ("1e-400", "text"),
        ("1.5", "text"),
    ]


def test_nan_is_null():
    # SQLite cannot store NaN (sqlite3 binds it as NULL); it is treated as
    # a missing value and is no evidence of a float column
    assert classify(float("nan")) is None
    conn, result = load([{"a": float("nan")}, {"a": 1}, {"a": float("nan")}])
    assert result.types == {"a": "integer"}
    assert column(conn, "a") == [(None, "null"), (1, "integer"), (None, "null")]
    conn, _ = load([{"a": float("nan")}, {"a": "x"}])
    assert column(conn, "a") == [(None, "null"), ("x", "text")]


@pytest.mark.parametrize("widen", STRATEGIES)
def test_sample_of_empty_rows(widen):
    rows = [{}, {}, {"a": "1"}, {"a": "x", "b": "2"}]
    conn, result = load(rows, widen=widen, sample_size=2, batch_size=1)
    assert result.rows == 4
    assert declared(conn) == {"a": "TEXT", "b": "INTEGER"}
    assert conn.execute("select a, b from t order by rowid").fetchall() == [
        (None, None),
        (None, None),
        ("1", None),
        ("x", 2),
    ]


def test_only_empty_rows_create_nothing():
    conn, result = load([{}, {}, {}], sample_size=2, batch_size=1)
    assert not result.created
    assert result.rows == 0
    assert conn.execute("select count(*) from sqlite_master").fetchone() == (0,)


def test_lone_surrogates_are_replaced():
    rows = [
        json.loads('{"a": "ok", "b\\ud800": 1}'),
        json.loads('{"a": "bad \\ud800 value", "b\\ud800": 2}'),
        json.loads('{"a": ["\\udfff"], "b\\ud800": 3}'),
    ]
    conn, result = load(rows, sample_size=1, batch_size=2)
    assert list(declared(conn)) == ["a", "b�"]
    assert [v for v, _ in column(conn, "a")] == [
        "ok",
        "bad � value",
        '["�"]',
    ]
    assert result.rows == 3


def test_discard_drops_kept_spellings():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t")
    inserter.write(conn, [{"a": "1"}])
    inserter.write(conn, [{"a": "1.50"}])
    temp = "select count(*) from sqlite_temp_master where type = 'table'"
    assert conn.execute(temp).fetchone() == (1,)
    inserter.discard(conn)
    assert conn.execute(temp).fetchone() == (0,)


def test_insert_rows_failure_drops_kept_spellings():
    conn = sqlite3.connect(":memory:", isolation_level=None)

    def rows():
        yield {"a": "1"}
        yield {"a": "1.50"}
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError):
        insert_rows(conn, "t", rows(), sample_size=1, batch_size=1)
    assert conn.execute("select count(*) from sqlite_temp_master").fetchone() == (0,)
    assert conn.execute("select count(*) from t").fetchone() == (2,)


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

        @staticmethod
        @hookimpl
        def prepare_connection(conn, database, datasette):
            if widen == "fallback" and can_refuse_writable_schema(datasette_sqlite3):
                refuse_writable_schema(conn)

    progress = []

    async def on_progress(done):
        # Every chunk is committed before the next one starts
        count = (await db.execute("select count(*) from events")).single_value()
        progress.append((done, count))

    pm.register(Plugin(), name="test_import_rows_write_wrapper")
    try:
        with widening(widen, datasette_sqlite3):
            result = await import_rows(
                db, "events", events_rows(2500), batch_size=500, progress=on_progress
            )
    finally:
        pm.unregister(name="test_import_rows_write_wrapper")
    # Sample of 1000, then batches of 500, then finish()
    assert progress[:4] == [(1000, 1000), (1500, 1500), (2000, 2000), (2500, 2500)]
    if widen == "sliced":
        # late_text widened in the last batch, which converted one of the
        # 2,000 rows before it: one convert() call each for the others,
        # each reported as progress
        assert writes == [True] * (5 + 1999)
        assert progress[4:] == [(2500, 2500)] * 1999
    else:
        assert writes == [True] * 5
        assert len(progress) == 4
    assert result.rows == 2500
    assert result.types == {
        "id": "integer",
        "late_float": "float",
        "late_text": "text",
        "maybe_empty": "integer",
    }
    assert [(w.column, w.at_row, w.method) for w in result.widenings] == [
        ("late_float", 1500, "rebuild" if widen == "fallback" else "schema"),
        ("late_text", 2000, "rebuild" if widen == "fallback" else "incremental"),
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
    with pytest.raises(datasette_sqlite3.OperationalError, match="already exists"):
        await import_rows(db, "t", events_rows(50), sample_size=10, batch_size=10)


@pytest.mark.asyncio
async def test_import_rows_drops_kept_spellings():
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    rows = [{"v": "1"}] * 5 + [{"v": "1.50"}] * 5 + [{"v": "x"}]
    result = await import_rows(db, "t", rows, sample_size=5, batch_size=2)
    # The last "1.50" shares its batch with "x", so it is stored as text
    assert result.spellings_kept == 4
    values = await db.execute("select v from t order by rowid")
    assert [r[0] for r in values] == ["1"] * 5 + ["1.50"] * 5 + ["x"]

    def temp_tables(conn):
        return conn.execute("select count(*) from sqlite_temp_master").fetchone()[0]

    assert await db.execute_write_fn(temp_tables) == 0


# Incremental widening -------------------------------------------------------


def _late_text_rows(count, at):
    "Rows whose n turns text at row ``at`` - after odd spellings and blanks."
    rows = []
    for i in range(count):
        if i == at:
            n = "N/A"
        elif i % 7 == 0:
            n = ""
        elif i % 11 == 0:
            n = f"{i}.50"
        else:
            n = str(i)
        rows.append({"id": str(i), "n": n, "f": f"{i}.25"})
    return rows


def test_incremental_widening_converts_a_slice_per_write(tmp_path):
    path = str(tmp_path / "data.db")
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    reader = sqlite3.connect(path, isolation_level=None)
    rows = _late_text_rows(1000, at=900)
    inserter = sliced_inserter("t", 100)
    seen = []

    def commit(fn):
        conn.execute("BEGIN IMMEDIATE")
        fn()
        conn.execute("COMMIT")
        # Another connection always sees a whole, valid table
        assert reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        count = reader.execute("select count(*) from t").fetchone()[0]
        assert count == inserter.rows_written
        types = dict(reader.execute("select name, type from pragma_table_info('t')"))
        numbers = reader.execute(
            "select count(*) from t where typeof(n) in ('integer', 'real')"
        ).fetchone()[0]
        seen.append((inserter.backlog, types["n"], numbers))

    for start in range(0, 1000, 100):
        commit(lambda start=start: inserter.write(conn, rows[start : start + 100]))
    assert [(w.column, w.from_type, w.method) for w in inserter.widenings] == [
        ("n", "float", "incremental")
    ]
    while inserter.backlog:
        commit(lambda: inserter.convert(conn))
    commit(lambda: inserter.finish(conn))
    # n widened in the write of rows 900-999: that write converted rowids
    # 1-100, each convert() 100 more. Declared BLOB (no affinity) until the
    # numbers are gone, so integrity_check never sees a number in a TEXT
    # column; TEXT in the transaction that converts the last of them
    widened = [s for s in seen if s[1] != "INTEGER" and s[1] != "REAL"]
    assert [backlog for backlog, _, _ in widened] == [
        800,
        700,
        600,
        500,
        400,
        300,
        200,
        100,
        0,
        0,
    ]
    assert seen[-1] == (0, "TEXT", 0)
    backlog_types = {sql_type for backlog, sql_type, _ in widened if backlog}
    assert backlog_types == {"BLOB"}
    numbers = [n for backlog, _, n in widened]
    assert numbers == sorted(numbers, reverse=True)
    assert numbers[-1] == 0
    # Exactly the table the whole input as the sample gives
    reference, _ = load(rows, sample_size=len(rows))
    assert _snapshot(conn) == _snapshot(reference)
    assert inserter.convert_seconds > 0
    assert conn.execute("select count(*) from sqlite_temp_master").fetchone() == (0,)


def test_incremental_backlog_and_later_widenings():
    # A second column widens to text while the first one's backlog is still
    # being converted, then a third goes to blob, and new keys add columns
    rows = [{"a": str(i), "b": str(i), "c": str(i)} for i in range(40)]
    rows[10]["a"] = "x"
    rows[20]["b"] = "y"
    rows[25]["c"] = b"\x00"
    rows[30]["d"] = "7"
    rows[35]["a"] = b"\x01"
    for convert_rows in (1, 3, 7):
        with slices_of(convert_rows):
            conn, _ = load(rows, sample_size=5, batch_size=3)
        reference, _ = load(rows, sample_size=len(rows))
        assert _snapshot(conn) == _snapshot(reference), convert_rows
        assert declared(conn) == {"a": "BLOB", "b": "TEXT", "c": "BLOB", "d": "INTEGER"}
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)


class _Recording:
    "A connection that records the statements run on it."

    def __init__(self, conn):
        self.conn = conn
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        return self.conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self.conn, name)


def test_widening_and_slices_never_scan_the_table():
    # A slice must cost as much as its own rows. With the columns' own
    # rowid ranges in the WHERE as well as the slice's, SQLite used one of
    # those for its range scan and visited the whole remaining backlog
    # every slice (70ms for 500 rows of a 1M row table) - which EXPLAIN
    # QUERY PLAN does not show, so the SQL is checked too. And min(rowid)
    # with max(rowid) in one query is a full scan
    conn = sqlite3.connect(":memory:", isolation_level=None)
    recording = _Recording(conn)
    inserter = sliced_inserter("t", 10)
    rows = [
        {"a": str(i), "b": f"{i}.50", "c": "" if i % 3 else str(i)} for i in range(1000)
    ]
    inserter.write(recording, rows)
    inserter.write(recording, [{"a": "x", "b": "1.5", "c": "4"}])
    inserter.write(recording, [{"a": "5", "b": "y", "c": "z"}])
    inserter.convert(recording)
    assert len(inserter._backlog) == 3
    plans = []
    for sql, params in recording.statements:
        if '"t"' not in sql or sql.startswith(("INSERT INTO", "CREATE", "PRAGMA")):
            continue
        plan = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
        plans.append((sql, [row[3] for row in plan]))
    updates = [(sql, plan) for sql, plan in plans if sql.startswith("UPDATE")]
    assert len(updates) == 3
    marker = " WHERE rowid BETWEEN :start AND :end AND ("
    for sql, plan in updates:
        assert plan[0] == "SEARCH t USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)"
        where = sql[sql.index(marker) + len(marker) :]
        assert "rowid BETWEEN" not in where.replace("rid BETWEEN :start AND :end", "")
    for sql, plan in plans:
        assert not any(step.startswith("SCAN t") for step in plan), (sql, plan)
        assert not any("MULTI-INDEX OR" in step for step in plan), (sql, plan)


def test_slices_are_sized_for_rewriting_every_row(monkeypatch):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t")
    inserter.write(conn, [{"a": str(i)} for i in range(20_000)])
    # Until a slice has measured rewriting rows, slices are sized from the
    # cost of inserting them
    insert_cost = inserter._insert_cost
    assert insert_cost > 0 and inserter._rewrite_cost is None
    monkeypatch.setattr(inserter_module, "CONVERT_SECONDS", 2_000 * insert_cost)
    first = inserter._slice_rows()
    assert 1_900 <= first <= 2_100
    inserter.write(conn, [{"a": "x"}])
    assert inserter.backlog == 20_000 - first
    # That slice rewrote every row it covered: it measured the cost
    assert inserter._rewrite_cost > 0
    expected = int(inserter_module.CONVERT_SECONDS / inserter._rewrite_cost)
    expected = min(max(expected, inserter_module._MIN_CONVERT_ROWS), 200_000)
    assert inserter._slice_rows() == expected
    monkeypatch.setattr(inserter_module, "CONVERT_SECONDS", 0.0)
    assert inserter._slice_rows() == inserter_module._MIN_CONVERT_ROWS
    before = inserter.backlog
    assert inserter.convert(conn) == before - inserter_module._MIN_CONVERT_ROWS
    assert inserter.convert(conn, rows=10**9) == 0
    assert inserter.convert(conn) == 0
    assert declared(conn) == {"a": "TEXT"}


def test_rewrite_cost_learning(monkeypatch):
    monkeypatch.setattr(inserter_module, "CONVERT_SECONDS", 0.05)
    inserter = TypedInserter("t")
    inserter._insert_cost = 1e-6
    # 0.05s / 1us
    assert inserter._slice_rows() == 50_000
    # Slices that rewrote few of their rows say nothing about rewriting:
    # skipping rows is cheap, which made the sizing overshoot before
    for _ in range(20):
        inserter._measure_slice(50_000, 10, 0.004)
    assert inserter._rewrite_cost is None
    assert inserter._slice_rows() == 50_000
    # A slice that rewrote most of its rows sets the cost...
    inserter._measure_slice(50_000, 40_000, 0.032)
    assert inserter._rewrite_cost == pytest.approx(0.8e-6)
    # ... a dearer one raises it at once, a cheaper one lowers it by half
    inserter._measure_slice(10_000, 10_000, 0.02)
    assert inserter._rewrite_cost == pytest.approx(2e-6)
    inserter._measure_slice(10_000, 10_000, 0.01)
    assert inserter._rewrite_cost == pytest.approx(1.5e-6)
    # A slice over time with few rows rewritten: size the next one so that
    # this one would have fit
    inserter._measure_slice(20_000, 0, 0.1)
    assert inserter._rewrite_cost == pytest.approx(5e-6)
    assert inserter._slice_rows() == 10_000
    # The limits
    inserter._rewrite_cost = 1e-12
    assert inserter._slice_rows() == inserter_module._MAX_CONVERT_ROWS
    inserter._rewrite_cost = 1.0
    assert inserter._slice_rows() == inserter_module._MIN_CONVERT_ROWS
    assert sliced_inserter("t", 7)._slice_rows() == 7
    # No timing at all yet
    assert TypedInserter("t")._slice_rows() == inserter_module._FIRST_CONVERT_ROWS


def test_slices_do_not_grow_over_rows_with_nothing_to_convert(monkeypatch):
    # A key that turns up late: NULL for most of the backlog, then numbers.
    # Skipping the NULLs is several times quicker than rewriting a row, so
    # sizing slices from the speed of the first ones made them grow until
    # one reached the numbers and rewrote far too many in one transaction
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t")
    rows = [{"id": str(i)} for i in range(30_000)]
    rows += [{"id": str(i), "x": str(i)} for i in range(30_000, 40_000)]
    inserter.write(conn, rows)
    monkeypatch.setattr(inserter_module, "CONVERT_SECONDS", 0.002)
    inserter._insert_cost = 1e-6
    inserter.write(conn, [{"id": "1", "x": "late text"}])
    sizes = []
    rewritten = []
    while inserter.backlog:
        before = inserter.backlog
        numbers = conn.execute(
            "select count(*) from t where typeof(x) = 'integer'"
        ).fetchone()[0]
        inserter.convert(conn)
        sizes.append(before - inserter.backlog)
        rewritten.append(
            numbers
            - conn.execute(
                "select count(*) from t where typeof(x) = 'integer'"
            ).fetchone()[0]
        )
    # Every slice over the NULLs covers what rewriting would allow, 2,000
    # rows (0.002s at 1us a row), however quickly it ran - and so the first
    # slice to reach the numbers rewrites at most that many. Then the
    # measured cost of rewriting decides
    null_slices = [size for size, count in zip(sizes, rewritten) if count == 0]
    assert len(null_slices) >= 13
    # (Smaller only if one ran over time, on a busy machine)
    assert null_slices[0] == 2_000 and max(null_slices) == 2_000
    first_numbers = next(count for count in rewritten if count)
    assert first_numbers <= 2_000
    assert inserter._rewrite_cost is not None
    assert conn.execute(
        "select count(*) from t where typeof(x) not in ('text', 'null')"
    ).fetchone() == (0,)
    assert declared(conn) == {"id": "INTEGER", "x": "TEXT"}


@needs_setconfig
def test_rebuilds_when_writable_schema_is_refused():
    conn = connect("fallback", isolation_level=None)
    rows = [{"a": "1", "b": "1"}, {"a": "1.5", "b": "1.10"}, {"a": "2", "b": "x"}]
    result = insert_rows(conn, "t", rows, sample_size=1, batch_size=1)
    assert [w.method for w in result.widenings] == ["rebuild"] * 3
    assert declared(conn) == {"a": "REAL", "b": "TEXT"}
    assert conn.execute("select a, b from t order by rowid").fetchall() == [
        (1.0, "1"),
        (1.5, "1.10"),
        (2.0, "x"),
    ]


def test_backlog_finishes_by_rebuilding_if_someone_adds_a_trigger():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = sliced_inserter("t", 1)
    inserter.write(conn, [{"a": str(i)} for i in range(5)])
    inserter.write(conn, [{"a": "x"}])
    assert inserter.backlog == 4
    conn.execute("create table log (a)")
    conn.execute(
        "create trigger t_log after update on t begin "
        "insert into log values (new.a); end"
    )
    inserter.convert(conn)
    # The rest was converted by copying the table, which keeps the trigger
    # but did not fire it for the converted rows
    assert inserter.backlog == 0
    assert conn.execute("select count(*) from log").fetchone() == (0,)
    assert [r[1] for r in conn.execute("select * from sqlite_master")].count(
        "t_log"
    ) == 1
    inserter.finish(conn)
    assert [v for v, _ in column(conn, "a")] == ["0", "1", "2", "3", "4", "x"]
    assert {kind for _, kind in column(conn, "a")} == {"text"}


def test_backlog_column_declared_text_by_rebuild_if_schema_edit_fails():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = sliced_inserter("t", 2)
    inserter.write(conn, [{"a": str(i)} for i in range(4)])
    inserter.write(conn, [{"a": "x"}])
    assert declared(conn) == {"a": "BLOB"}
    # Someone adds an index: the last slice can't edit the schema
    conn.execute("create index t_a on t(a)")
    while inserter.backlog:
        inserter.convert(conn)
    assert declared(conn) == {"a": "TEXT"}
    assert [r[1] for r in conn.execute("PRAGMA index_list(t)")] == ["t_a"]
    assert {kind for _, kind in column(conn, "a")} == {"text"}


def test_snapshot_restores_the_backlog():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = sliced_inserter("t", 1)
    conn.execute("BEGIN")
    inserter.write(conn, [{"a": str(i)} for i in range(5)])
    inserter.write(conn, [{"a": "x"}])
    conn.execute("COMMIT")
    snapshot = inserter.snapshot()
    conn.execute("BEGIN")
    inserter.convert(conn)
    inserter.convert(conn)
    conn.execute("ROLLBACK")
    inserter.restore(snapshot)
    assert inserter.backlog == 4
    conn.execute("BEGIN")
    while inserter.backlog:
        inserter.convert(conn)
    inserter.finish(conn)
    conn.execute("COMMIT")
    assert [v for v, _ in column(conn, "a")] == ["0", "1", "2", "3", "4", "x"]


@pytest.mark.parametrize("widen", STRATEGIES)
def test_widening_methods_record(widen):
    rows = [{"a": "1", "b": "1"}, {"a": "1.5", "b": "x"}]
    _, result = load(rows, widen=widen, sample_size=1, batch_size=1)
    methods = [(w.column, w.method) for w in result.widenings]
    expected = {
        "auto": [("a", "schema"), ("b", "incremental")],
        "sliced": [("a", "schema"), ("b", "incremental")],
        "fallback": [("a", "rebuild"), ("b", "rebuild")],
    }[widen]
    assert methods == expected
    assert result.convert_seconds >= 0


# Positional rows -------------------------------------------------------------


def _worker_rows(rows, rng):
    """
    ``rows`` as the worker protocol sends them, as (chunk, columns) pairs
    for TypedInserter.write(): positional rows without trailing nulls -
    except in a row that added keys, which reach the parent once even if
    they hold None - and now and then a sparse row as a dictionary. The
    column list is announced ahead of the rows: the parent decodes a whole
    read of the pipe before it writes a chunk.
    """
    order = []
    for row in rows:
        order.extend(key for key in row if key not in order)
    seen = set()
    position = 0
    while position < len(rows):
        chunk = rows[position : position + rng.randint(1, 6)]
        position += len(chunk)
        ahead = position + rng.choice((0, 0, 1, 3, 10))
        known = {key for row in rows[:ahead] for key in row}
        columns = [key for key in order if key in known]
        values = []
        for row in chunk:
            added = not seen.issuperset(row)
            seen.update(row)
            if row and rng.random() < 0.15:
                values.append(dict(row))
                continue
            # The columns known when the worker sent this row
            row_values = [row.get(key) for key in order if key in seen]
            if not added:
                while row_values and row_values[-1] is None:
                    row_values.pop()
            values.append(tuple(row_values) if rng.random() < 0.5 else row_values)
        yield chunk, values, columns


def _widenings(inserter):
    return [
        (w.column, w.from_type, w.to_type, w.at_row, w.method)
        for w in inserter.widenings
    ]


@pytest.mark.parametrize("seed", range(40))
def test_positional_rows_give_the_same_table_as_dicts(seed):
    # The same chunks as dictionaries and as the worker sends them: the
    # same table, columns in the same order, and the same widenings - a
    # key announced ahead of the rows that have it must not make an
    # all-NULL INTEGER column that a later chunk widens
    rng = random.Random(seed)
    rows = _tricky_rows(rng, rng.randint(1, 40))
    widen = rng.choice(AVAILABLE)
    conns, inserters = [], []
    for _ in range(2):
        conns.append(connect(widen, isolation_level=None))
        inserters.append(new_inserter("t", widen))
    for chunk, values, columns in _worker_rows(rows, rng):
        inserters[0].write(conns[0], chunk)
        inserters[1].write(conns[1], values, columns)
    for conn, inserter in zip(conns, inserters):
        while inserter.backlog:
            inserter.convert(conn)
        inserter.finish(conn)
    by_dicts, positional = conns
    assert list(declared(positional).items()) == list(declared(by_dicts).items())
    assert _snapshot(positional) == _snapshot(by_dicts)
    assert _widenings(inserters[1]) == _widenings(inserters[0])
    reference, _ = load(rows, sample_size=len(rows))
    assert _snapshot(positional) == _snapshot(reference)


def test_positional_rows_add_only_the_columns_they_reach():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t")
    # "late" is announced, but no row in this chunk reaches it
    inserter.write(conn, [["1"], ("2",)], ["id", "late"])
    assert declared(conn) == {"id": "INTEGER"}
    inserter.write(conn, [["3", "x"]], ["id", "late"])
    assert inserter.widenings == []
    assert list(declared(conn).items()) == [("id", "INTEGER"), ("late", "TEXT")]
    # A dictionary adds its keys, in row order with the positional rows
    inserter.write(conn, [{"d": "1"}, ["4", None, "y"]], ["id", "late", "c"])
    assert list(declared(conn)) == ["id", "late", "d", "c"]
    # A row that reaches a key holding None adds it, as {"e": None} would
    inserter.write(conn, [["5", None, None, None]], ["id", "late", "c", "e"])
    inserter.finish(conn)
    assert list(declared(conn)) == ["id", "late", "d", "c", "e"]
    assert inserter.widenings == []
    assert conn.execute("select * from t order by rowid").fetchall() == [
        (1, None, None, None, None),
        (2, None, None, None, None),
        (3, "x", None, None, None),
        (None, None, 1, None, None),
        (4, None, None, "y", None),
        (5, None, None, None, None),
    ]


def test_positional_rows_in_another_order_and_dicts_among_them():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t", schema={"b": None, "a": None})
    inserter.write(conn, [[1, 2], (3,)], ["a", "b"])
    inserter.write(conn, [{"c": "x", "a": 5}, ["6", "7", "y"]], ["a", "b", "c"])
    inserter.write(conn, [[]], ["c"])
    inserter.finish(conn)
    assert list(declared(conn)) == ["b", "a", "c"]
    assert conn.execute("select a, b, c from t order by rowid").fetchall() == [
        (1, 2, None),
        (3, None, None),
        (5, None, "x"),
        (6, 7, "y"),
        (None, None, None),
    ]


def test_positional_rows_are_not_modified():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t")
    rows = [["1", "", None], ["2", "x", [1]]]
    inserter.write(conn, rows, ["a", "b", "c"])
    assert rows == [["1", "", None], ["2", "x", [1]]]


def test_positional_rows_errors():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t")
    with pytest.raises(ValueError, match="duplicate"):
        inserter.write(conn, [[1, 2]], ["a", "a"])
    with pytest.raises(ValueError, match="more values than the 1 columns"):
        inserter.write(conn, [[1, 2]], ["a"])
    # A string is not a row of values (it used to become one per character)
    for row in ("xy", b"xy", 5, {1, 2}, (v for v in "xy")):
        with pytest.raises(TypeError, match="lists, tuples or dicts"):
            inserter.write(conn, [row], ["a", "b"])
        with pytest.raises(TypeError, match="lists, tuples or dicts"):
            inserter.write(conn, [["1", "2"], row], ["a", "b"])
    assert not inserter.created and inserter.columns == {}
    assert inserter.write(conn, [[1]], ["a"]) == 1
    # Other sequences are fine
    assert inserter.write(conn, [range(2), {"a": 5}], ["a", "b"]) == 2
    assert conn.execute("select a, b from t").fetchall() == [
        (1, None),
        (0, 1),
        (5, None),
    ]


def test_positional_rows_with_no_values_yet():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    inserter = TypedInserter("t")
    # No columns: like empty dicts, held until a key turns up
    assert inserter.write(conn, [[], []], []) == 0
    assert inserter.write(conn, [["1"]], ["a"]) == 3
    inserter.finish(conn)
    assert conn.execute("select a from t order by rowid").fetchall() == [
        (None,),
        (None,),
        (1,),
    ]


def test_insert_rows_positional_with_growing_columns():
    conn = sqlite3.connect(":memory:")
    columns = ["a"]

    def rows():
        yield ["1"]
        yield ["2"]
        columns.append("b")
        yield ["3", "x"]
        yield ["4"]

    result = insert_rows(
        conn, "t", rows(), columns=columns, sample_size=2, batch_size=1
    )
    assert result.types == {"a": "integer", "b": "text"}
    assert conn.execute("select a, b from t order by rowid").fetchall() == [
        (1, None),
        (2, None),
        (3, "x"),
        (4, None),
    ]


@pytest.mark.asyncio
async def test_import_rows_positional_from_an_async_source():
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    columns = []

    async def rows():
        columns.extend(["id", "name"])
        for i in range(250):
            if i == 200:
                # A key that turns up later, as a worker announces it
                columns.append("late")
            yield [str(i), f"name {i}"] + (["1.5"] if i >= 200 else [])
            if i % 50 == 0:
                await asyncio.sleep(0)

    result = await import_rows(
        db, "t", rows(), columns=columns, sample_size=100, batch_size=60
    )
    assert result.rows == 250
    assert result.types == {"id": "integer", "name": "text", "late": "float"}
    values = await db.execute("select count(late), sum(id) from t")
    assert tuple(values.first()) == (50, sum(range(250)))


# Clean up after failures and cancellation ----------------------------------


async def _temp_tables(db):
    return await db.execute_write_fn(
        lambda conn: [r[0] for r in conn.execute("select name from sqlite_temp_master")]
    )


class _SlowWrites:
    "A write_wrapper plugin that holds each write for a moment."

    __name__ = "_SlowWrites"

    def __init__(self, seconds):
        self.seconds = seconds
        self.writes = 0

    @hookimpl
    def write_wrapper(self, datasette, database, request, transaction):
        def wrapper(conn):
            self.writes += 1
            time.sleep(self.seconds)
            yield

        return wrapper


@pytest.fixture
def slow_writes():
    plugin = _SlowWrites(0.15)
    pm.register(plugin, name="test_inserter_slow_writes")
    try:
        yield plugin
    finally:
        pm.unregister(name="test_inserter_slow_writes")


@pytest.mark.asyncio
@pytest.mark.parametrize("on_error", ("keep", "drop"))
async def test_cancel_during_the_first_write(slow_writes, on_error):
    # Cancelled while the write that creates the table is running: the
    # write thread finishes it anyway. The clean up waits for it, so the
    # status says the table is this import's - and it is kept, completed,
    # or dropped
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    status = inserter_module.ImportStatus()
    rows = [{"n": "1.50"}, {"n": "2"}] * 5
    task = asyncio.ensure_future(
        import_rows(
            db, "t", rows, sample_size=4, batch_size=2, status=status, on_error=on_error
        )
    )
    await asyncio.sleep(0.05)
    assert slow_writes.writes == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert status.done
    assert status.created
    assert status.rows == 4
    assert isinstance(status.error, asyncio.CancelledError)
    assert await _temp_tables(db) == []
    exists = await db.table_exists("t")
    if on_error == "keep":
        assert exists and status.table_exists
        values = await db.execute("select n, typeof(n) from t order by rowid")
        assert [tuple(r) for r in values] == [(1.5, "real"), (2.0, "real")] * 2
    else:
        assert not exists and status.dropped and not status.table_exists


@pytest.mark.asyncio
async def test_cancel_while_reading_rows():
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    status = inserter_module.ImportStatus()
    reading = []

    def rows():
        yield {"n": "1"}
        yield {"n": "1.50"}
        yield {"n": "x"}
        yield {"n": "4"}
        reading.append("slow")
        time.sleep(0.3)
        yield {"n": "5"}

    task = asyncio.ensure_future(
        import_rows(db, "t", rows(), sample_size=2, batch_size=2, status=status)
    )
    while not reading:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The thread reading rows was waited for, and the table completed
    assert status.done and status.created and status.rows == 4
    assert await _temp_tables(db) == []
    values = await db.execute("select n, typeof(n) from t order by rowid")
    assert [tuple(r) for r in values] == [
        ("1", "text"),
        ("1.50", "text"),
        ("x", "text"),
        ("4", "text"),
    ]
    assert await db.execute_fn(lambda conn: declared(conn)) == {"n": "TEXT"}


def _backlog_rows(count):
    # Numbers with spellings to keep ("1.50", blanks next to real NULLs),
    # then text: the stored numbers of every row before it become text
    rows = []
    for i in range(count):
        if i % 3 == 0:
            rows.append({"n": f"{i}.50", "b": ""})
        elif i % 3 == 1:
            rows.append({"n": str(i), "b": None})
        else:
            rows.append({"n": f"{i}.0", "b": str(i)})
    rows.append({"n": "x", "b": "y"})
    return rows


def _expected_text(rows):
    return [(row["n"], row.get("b")) for row in rows]


@pytest.mark.asyncio
async def test_cancel_during_the_backlog(monkeypatch):
    monkeypatch.setattr(TypedInserter, "_convert_rows", 1)
    # Cancelled while stored numbers are still being converted to text:
    # with on_error="keep" the call raises at once and the conversion
    # finishes in the background
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    status = inserter_module.ImportStatus()
    rows = _backlog_rows(300)
    converting = asyncio.Event()

    async def progress(written):
        if written == 301:
            converting.set()

    task = asyncio.ensure_future(
        import_rows(
            db,
            "t",
            rows,
            sample_size=300,
            batch_size=1,
            progress=progress,
            status=status,
        )
    )
    await converting.wait()
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert status.created and status.rows == 301
    await status.wait()
    assert status.done and status.backlog == 0
    assert await _temp_tables(db) == []
    types = await db.execute("select typeof(n), count(*) from t group by 1")
    assert [tuple(r) for r in types] == [("text", 301)]
    assert await db.execute_fn(lambda conn: declared(conn)) == {
        "n": "TEXT",
        "b": "TEXT",
    }
    values = await db.execute("select n, b from t order by rowid")
    assert [tuple(r) for r in values] == _expected_text(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("on_error", ("keep", "drop"))
async def test_cancel_during_a_long_backlog_returns_at_once(
    slow_writes, on_error, monkeypatch
):
    monkeypatch.setattr(TypedInserter, "_convert_rows", 2)
    # The rest of the backlog takes 150 writes of 20ms here (seconds, at
    # 6M rows of big-1gb.csv): a cancelled call used to convert all of it
    # before raising - and a worker feeding it stayed alive meanwhile
    slow_writes.seconds = 0.02
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    status = inserter_module.ImportStatus()
    rows = _backlog_rows(300)
    cancel = asyncio.Event()

    async def progress(written):
        if written == 301:
            cancel.set()

    task = asyncio.ensure_future(
        import_rows(
            db,
            "t",
            rows,
            sample_size=300,
            batch_size=1,
            progress=progress,
            status=status,
            on_error=on_error,
        )
    )
    await cancel.wait()
    assert status.backlog > 250
    cancelled_at = time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # At most the write that was running, and a drop: not the 2.5s or more
    # of slices left (a generous bound, for busy CI machines)
    assert time.perf_counter() - cancelled_at < 1.5
    if on_error == "drop":
        assert status.done and status.dropped
        assert not await db.table_exists("t")
        assert await _temp_tables(db) == []
        return
    assert not status.done and status.backlog > 200
    # Readers see the table meanwhile, every row there
    count = await db.execute("select count(*) from t")
    assert count.single_value() == 301
    await asyncio.wait_for(status.wait(), 30)
    assert status.done and status.backlog == 0 and not status.dropped
    assert await _temp_tables(db) == []
    values = await db.execute("select n, b from t order by rowid")
    assert [tuple(r) for r in values] == _expected_text(rows)
    assert await db.execute_fn(lambda conn: declared(conn)) == {
        "n": "TEXT",
        "b": "TEXT",
    }


@pytest.mark.asyncio
async def test_background_finish_waits_for_the_cancelled_caller(
    slow_writes, monkeypatch
):
    monkeypatch.setattr(TypedInserter, "_convert_rows", 2)
    # The cancelled task's own clean up goes first - here it drops the
    # table, so nothing is converted at all
    slow_writes.seconds = 0.005
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    status = inserter_module.ImportStatus()
    seen = {}
    cancel = asyncio.Event()

    async def progress(written):
        if written == 301:
            cancel.set()

    async def caller():
        try:
            await import_rows(
                db,
                "t",
                _backlog_rows(300),
                sample_size=300,
                batch_size=1,
                progress=progress,
                status=status,
            )
        except asyncio.CancelledError:
            seen["backlog"] = status.backlog
            await asyncio.sleep(0.05)
            await db.execute_write("drop table t")
            raise

    task = asyncio.ensure_future(caller())
    await cancel.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(status.wait(), 10)
    assert seen["backlog"] > 250
    assert status.backlog == seen["backlog"]
    assert not await db.table_exists("t")
    assert await _temp_tables(db) == []


@pytest.mark.asyncio
async def test_background_finish_leaves_a_replaced_table_alone(
    slow_writes, monkeypatch
):
    monkeypatch.setattr(TypedInserter, "_convert_rows", 2)
    # After a cancelled import raised, its caller may drop the table - and
    # another import may create one by the same name. The conversion that
    # carries on in the background must not touch that one
    slow_writes.seconds = 0.01
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    status = inserter_module.ImportStatus()
    cancel = asyncio.Event()

    async def progress(written):
        if written == 301:
            cancel.set()

    task = asyncio.ensure_future(
        import_rows(
            db,
            "t",
            _backlog_rows(300),
            sample_size=300,
            batch_size=1,
            progress=progress,
            status=status,
        )
    )
    await cancel.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not status.done

    def replace(conn):
        conn.execute("drop table t")
        conn.execute('CREATE TABLE "t" ("n" BLOB, "b" BLOB)')
        conn.executemany("insert into t values (?, ?)", [(i, i) for i in range(400)])

    await db.execute_write_fn(replace)
    await asyncio.wait_for(status.wait(), 30)
    types = await db.execute("select typeof(n), typeof(b), count(*) from t group by 1")
    assert [tuple(r) for r in types] == [("integer", "integer", 400)]
    assert await _temp_tables(db) == []


@pytest.mark.asyncio
async def test_cancel_again_while_cleaning_up(slow_writes):
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    status = inserter_module.ImportStatus()
    rows = [{"n": "1.50"}, {"n": "2"}] * 5
    task = asyncio.ensure_future(
        import_rows(db, "t", rows, sample_size=4, batch_size=2, status=status)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The clean up carries on in the background
    await asyncio.wait_for(status.wait(), 10)
    assert status.done and status.created
    assert await _temp_tables(db) == []


class _HeldFirstWrite:
    "A Database whose first write waits on the write thread until released."

    def __init__(self, db):
        self.db = db
        self.release = threading.Event()
        self.writing = threading.Event()
        self._first = True

    def __getattr__(self, name):
        return getattr(self.db, name)

    async def execute_write_fn(self, fn, *args, **kwargs):
        if self._first:
            self._first = False

            def held(conn):
                self.writing.set()
                self.release.wait(10)
                return fn(conn)

            return await self.db.execute_write_fn(held, *args, **kwargs)
        return await self.db.execute_write_fn(fn, *args, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("on_error", ("keep", "drop"))
async def test_wait_settled_after_cancelling_again(on_error):
    # Cancelled twice while the write that creates the table runs: the
    # second cancellation cuts the clean up short, so the call raises before
    # that write has committed and the status cannot say yet that the table
    # is this call's. wait_settled() waits until it can
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    held = _HeldFirstWrite(db)
    status = inserter_module.ImportStatus()
    rows = [{"n": i} for i in range(1000)]
    task = asyncio.ensure_future(
        import_rows(
            held,
            "t",
            rows,
            sample_size=50,
            batch_size=50,
            on_error=on_error,
            status=status,
        )
    )
    try:
        assert await asyncio.get_running_loop().run_in_executor(
            None, held.writing.wait, 5
        )
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (status.created, status.dropped, status.rows) == (False, False, 0)
        assert not await status.wait_settled(timeout=0.1)
    finally:
        held.release.set()
    assert await status.wait_settled(timeout=5)
    assert status.created and status.rows == 50
    if on_error == "keep":
        assert status.table_exists
        assert (await db.execute("select count(*) from t")).first()[0] == 50
    else:
        assert status.dropped and not await db.table_exists("t")
    await asyncio.wait_for(status.wait(), 10)


@pytest.mark.asyncio
async def test_import_status_wait():
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    status = inserter_module.ImportStatus()
    waiter = asyncio.ensure_future(status.wait())
    await asyncio.sleep(0)
    assert not waiter.done()
    await import_rows(db, "t", [{"a": "1"}], status=status)
    await asyncio.wait_for(waiter, 5)
    assert status.done and status.backlog == 0 and status.rows == 1
    # Done already: returns at once, also for insert_rows() (in a thread)
    await asyncio.wait_for(status.wait(), 1)
    other = inserter_module.ImportStatus()
    waiter = asyncio.ensure_future(other.wait())
    await asyncio.sleep(0)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    await asyncio.get_running_loop().run_in_executor(
        None, lambda: insert_rows(conn, "t", [{"a": "1"}], status=other)
    )
    await asyncio.wait_for(waiter, 5)
    assert other.done


@pytest.mark.asyncio
async def test_progress_callback_that_raises():
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    status = inserter_module.ImportStatus()

    def progress(written):
        if written > 2:
            raise ValueError("stop")

    rows = [{"n": "1"}, {"n": "1.50"}, {"n": "x"}, {"n": "4"}]
    with pytest.raises(ValueError):
        await import_rows(
            db,
            "t",
            rows,
            sample_size=2,
            batch_size=1,
            progress=progress,
            status=status,
            on_error="drop",
        )
    assert status.done and status.created and status.dropped
    assert isinstance(status.error, ValueError)
    assert not await db.table_exists("t")
    assert await _temp_tables(db) == []


@pytest.mark.asyncio
async def test_failure_never_drops_a_table_it_did_not_create():
    ds = Datasette()
    db = ds.add_memory_database(uuid.uuid4().hex, name="scratch")
    await db.execute_write("create table t (a text)")
    status = inserter_module.ImportStatus()
    with pytest.raises(datasette_sqlite3.OperationalError, match="already exists"):
        await import_rows(
            db, "t", events_rows(50), sample_size=10, on_error="drop", status=status
        )
    assert status.done and not status.created and not status.dropped
    assert await db.table_exists("t")


def test_insert_rows_keyboard_interrupt_completes_the_table():
    conn = sqlite3.connect(":memory:")
    status = inserter_module.ImportStatus()

    def rows():
        yield from [{"n": str(i)} for i in range(10)]
        yield {"n": "1.50"}
        yield {"n": "x"}
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt), slices_of(1):
        insert_rows(conn, "t", rows(), sample_size=10, batch_size=2, status=status)
    assert status.done and status.created and status.rows == 12
    assert conn.execute("select count(*) from sqlite_temp_master").fetchone() == (0,)
    assert declared(conn) == {"n": "TEXT"}
    values = [v for v, _ in column(conn, "n")]
    assert values == [str(i) for i in range(10)] + ["1.50", "x"]


def test_insert_rows_on_error_drop():
    conn = sqlite3.connect(":memory:")
    status = inserter_module.ImportStatus()

    def rows():
        yield {"n": "1"}
        yield {"n": "2"}
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError):
        insert_rows(
            conn,
            "t",
            rows(),
            sample_size=1,
            batch_size=1,
            on_error="drop",
            status=status,
        )
    assert status.created and status.dropped
    assert conn.execute("select count(*) from sqlite_master").fetchone() == (0,)
    with pytest.raises(ValueError):
        insert_rows(conn, "t", [], on_error="explode")
