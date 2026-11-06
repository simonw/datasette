import codecs
import csv
import io
import json
import logging
import math
import random
import subprocess
import sys

import pytest

from datasette import hookimpl
from datasette.imports import loaders as L
from datasette.imports.loaders import (
    EmptyFile,
    Loader,
    LoaderError,
    PartialDict,
    PartialList,
    Schema,
    UnrecognizedFormat,
    get_loader,
    get_loaders,
    load_path,
    peek_json,
    pick_loader,
    score_loaders,
    sniff_path,
    split_schema,
)
from datasette.plugins import pm

from . import geojson_loader_plugin
from .geojson_loader_plugin import GeoJSONLoader, NaiveGeoJSONLoader

CORE = {"csv", "tsv", "json", "ndjson"}

FEATURE = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [-0.12, 51.5]},
    "properties": {"name": "London", "pop": 8800000},
}
FEATURE_2 = {
    "type": "Feature",
    "id": "p2",
    "geometry": {"type": "Point", "coordinates": [2.35, 48.85]},
    "properties": {"name": "Paris", "pop": 2.1, "capital": True},
}
GEOJSON_TYPE_FIRST = json.dumps(
    {"type": "FeatureCollection", "features": [FEATURE, FEATURE_2]}
).encode()
GEOJSON_TYPE_LAST = json.dumps(
    {"features": [FEATURE, FEATURE_2] * 200, "type": "FeatureCollection"}
).encode()


class FixedLoader(Loader):
    """A loader that always returns the same score."""

    def __init__(self, name, score):
        self.name = name
        self.score = score

    def sniff(self, first_bytes):
        return self.score

    def load(self, path):
        yield {"loader": self.name}


class RaisingLoader(Loader):
    name = "raises"

    def sniff(self, first_bytes):
        raise RuntimeError("sniff exploded")


class _Plugin:
    def __init__(self, *loaders):
        self.loaders = loaders

    @hookimpl
    def register_import_loaders(self):
        return list(self.loaders)


@pytest.fixture
def register():
    registered = []

    def _register(plugin, name):
        pm.register(plugin, name=name)
        registered.append(name)

    yield _register
    for name in registered:
        pm.unregister(name=name)


@pytest.fixture
def with_geojson(register):
    register(geojson_loader_plugin, "test-geojson-loader")


def write(tmp_path, name, content):
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return str(path)


def rows_of(path, **kwargs):
    result = load_path(path, **kwargs)
    return result.loader.name, result.schema, list(result.rows)


# The hook and the registry


def test_core_loaders_are_registered():
    loaders = get_loaders()
    assert {loader.name for loader in loaders} >= CORE
    assert all(isinstance(loader, Loader) for loader in loaders)


def test_hook_can_be_called_without_datasette():
    # The hook takes no arguments so an import worker can call it
    results = pm.hook.register_import_loaders()
    names = {loader.name for result in results for loader in result}
    assert names >= CORE


def test_plugin_can_register_a_loader_class(register):
    class XMLLoader(Loader):
        name = "xml"

        def sniff(self, first_bytes):
            return 1.0 if first_bytes.startswith(b"<?xml") else 0.0

    register(_Plugin(XMLLoader), "test-xml-loader")
    loaders = {loader.name: loader for loader in get_loaders()}
    assert isinstance(loaders["xml"], XMLLoader)
    assert pick_loader(b'<?xml version="1.0"?><a/>').name == "xml"


def test_plugin_loader_replaces_core_loader_with_same_name(register):
    replacement = FixedLoader("csv", 0.5)
    register(_Plugin(replacement), "test-csv-replacement")
    csv_loaders = [loader for loader in get_loaders() if loader.name == "csv"]
    assert csv_loaders == [replacement]


def test_duplicate_plugin_loader_names_warn_and_pick_deterministically(
    register, caplog
):
    class Second(FixedLoader):
        pass

    class First(FixedLoader):
        pass

    register(_Plugin(Second("dupe", 0.5)), "test-dupe-a")
    register(_Plugin(First("dupe", 0.5)), "test-dupe-b")
    with caplog.at_level(logging.WARNING, logger="datasette.imports.loaders"):
        dupes = [loader for loader in get_loaders() if loader.name == "dupe"]
    # Lowest module.qualname wins, whatever the registration order
    assert [type(loader).__name__ for loader in dupes] == ["First"]
    assert "Two import loaders are called 'dupe'" in caplog.text


def test_invalid_loaders_are_skipped(register, caplog):
    class NoName(Loader):
        pass

    register(_Plugin(NoName, object()), "test-invalid-loaders")
    with caplog.at_level(logging.WARNING, logger="datasette.imports.loaders"):
        names = {loader.name for loader in get_loaders()}
    assert names >= CORE
    assert "Ignoring import loader" in caplog.text


def test_get_loader_by_name():
    assert get_loader("tsv").name == "tsv"
    with pytest.raises(LoaderError) as ex:
        get_loader("parquet")
    assert "Unknown format 'parquet'" in str(ex.value)
    assert "csv, json, ndjson, tsv" in str(ex.value)


# Selection


@pytest.mark.parametrize(
    "content,expected",
    (
        (b"id,name\n1,Cleo\n2,Pancakes\n", "csv"),
        (b"id\tname\n1\tCleo\n2\tPancakes\n", "tsv"),
        (b"id;name;price\n1;foo;1,5\n2;bar;2,25\n", "csv"),
        (b"id|name\n1|Cleo\n", "csv"),
        (b'[{"id": 1}, {"id": 2}]', "json"),
        (b'[\n  {\n    "id": 1\n  },\n  {\n    "id": 2\n  }\n]\n', "json"),
        (b'{"id": 1}\n{"id": 2}\n', "ndjson"),
        (b'{"rows": [{"id": 1}]}', "json"),
        (b"\xef\xbb\xbfid,name\n1,caf\xc3\xa9\n", "csv"),
        (b'\xef\xbb\xbf[{"id": 1}]', "json"),
        (b'\xef\xbb\xbf{"id": 1}\n{"id": 2}\n', "ndjson"),
        # A CSV whose first line looks like JSON
        (b'{"a": 1},first\n{"a": 2},second\n', "csv"),
        (b"[id],[name]\n1,Cleo\n", "csv"),
        # One column, no delimiters at all
        (b"name\nCleo\nPancakes\n", "csv"),
        (b'name\n"Smith, John"\nCleo\n', "csv"),
    ),
)
def test_pick_loader(content, expected):
    assert pick_loader(content).name == expected


def test_scores_are_ranked_best_first():
    scores = score_loaders(b"id,name\n1,Cleo\n")
    assert scores[0].name == "csv"
    assert scores[0].score == pytest.approx(0.8)
    assert [s.score for s in scores] == sorted((s.score for s in scores), reverse=True)


@pytest.mark.parametrize("reverse", (False, True))
def test_ties_go_to_plugin_loaders_then_name(reverse):
    core_csv = L.CSVLoader()
    plugin_b = FixedLoader("b-plugin", 0.8)
    plugin_a = FixedLoader("a-plugin", 0.8)
    loaders = [core_csv, plugin_b, plugin_a]
    if reverse:
        loaders.reverse()
    scores = score_loaders(b"id,name\n1,Cleo\n", loaders)
    assert [s.name for s in scores] == ["a-plugin", "b-plugin", "csv"]
    assert pick_loader(b"id,name\n1,Cleo\n", loaders).name == "a-plugin"


def test_ties_between_core_loaders_use_name():
    loaders = [L.TSVLoader(), L.CSVLoader()]
    # Both score zero on binary data: ranked by name
    assert [s.name for s in score_loaders(b"\x00\x01", loaders)] == ["csv", "tsv"]


def test_unrecognized_format():
    with pytest.raises(UnrecognizedFormat) as ex:
        pick_loader(b"SQLite format 3\x00\x10\x00\x01\x01")
    assert "Could not recognize the file format" in str(ex.value)
    assert "a loader needs at least 0.1" in str(ex.value)
    assert {s.name for s in ex.value.scores} >= CORE
    assert all(s.score == 0 for s in ex.value.scores)


def test_prose_is_unrecognized():
    with pytest.raises(UnrecognizedFormat):
        pick_loader(b"# Title\n\nSome prose, with commas, here.\nMore text.\n")


def test_min_confidence_threshold():
    loaders = [FixedLoader("weak", 0.1)]
    assert pick_loader(b"x", loaders).name == "weak"
    with pytest.raises(UnrecognizedFormat):
        pick_loader(b"x", [FixedLoader("weaker", 0.09)])
    with pytest.raises(UnrecognizedFormat):
        pick_loader(b"x", loaders, min_confidence=0.5)


@pytest.mark.parametrize("content", (b"", b"  \n\n", codecs.BOM_UTF8 + b"\n"))
def test_empty_file(content):
    with pytest.raises(EmptyFile) as ex:
        pick_loader(content)
    assert str(ex.value) == "File is empty"
    # EmptyFile is a kind of UnrecognizedFormat
    assert isinstance(ex.value, UnrecognizedFormat)


def test_raising_sniffer_does_not_break_selection(caplog):
    loaders = [RaisingLoader(), L.CSVLoader()]
    with caplog.at_level(logging.WARNING, logger="datasette.imports.loaders"):
        scores = score_loaders(b"id,name\n1,Cleo\n", loaders)
        assert pick_loader(b"id,name\n1,Cleo\n", loaders).name == "csv"
    raised = next(s for s in scores if s.name == "raises")
    assert raised.score == 0.0
    assert raised.error == "RuntimeError: sniff exploded"
    assert "Import loader 'raises' sniff() failed" in caplog.text


def test_raising_sniffer_registered_by_plugin(register):
    register(_Plugin(RaisingLoader), "test-raising-loader")
    assert pick_loader(b'[{"id": 1}]').name == "json"


@pytest.mark.parametrize(
    "returned,expected_score,expected_error",
    (
        (5, 1.0, None),
        (-1, 0.0, None),
        (True, 1.0, None),
        (math.nan, 0.0, "ValueError: sniff() returned NaN"),
        ("high", 0.0, "ValueError: could not convert string to float: 'high'"),
        (None, 0.0, "TypeError"),
    ),
)
def test_sniff_return_values_are_checked(returned, expected_score, expected_error):
    (score,) = score_loaders(b"x", [FixedLoader("odd", returned)])
    assert score.score == expected_score
    if expected_error is None:
        assert score.error is None
    else:
        assert score.error.startswith(expected_error)


# GeoJSON (a plugin) competing with core JSON for the same file


@pytest.mark.parametrize(
    "content,expected",
    (
        (GEOJSON_TYPE_FIRST, "geojson"),
        # "type" comes after a long "features" array
        (GEOJSON_TYPE_LAST, "geojson"),
        (json.dumps(FEATURE).encode(), "geojson"),
        (json.dumps({"features": [FEATURE]}, indent=4).encode(), "geojson"),
        (json.dumps([FEATURE["properties"]]).encode(), "json"),
        (b'{"name": "London"}', "json"),
        (b'{"features": [1, 2]}', "json"),
        (b'{"type": "FeatureCollection"}\n{"type": "Feature"}\n', "ndjson"),
    ),
)
def test_geojson_competes_with_json(with_geojson, content, expected):
    assert pick_loader(content).name == expected


def test_geojson_outscores_json_on_feature_collection(with_geojson):
    scores = {s.name: s.score for s in score_loaders(GEOJSON_TYPE_LAST[:4096])}
    assert scores["geojson"] == pytest.approx(0.9)
    assert scores["json"] == pytest.approx(0.6)
    assert scores["geojson"] > scores["json"] > scores["csv"]


def test_naive_geojson_sniff_needs_type_inside_the_window():
    # Why GeoJSONLoader.sniff() looks at structure: searching the window for
    # "FeatureCollection" fails when the type key comes last
    loaders = [*L._CORE_LOADER_CLASSES, NaiveGeoJSONLoader]
    loaders = [cls() for cls in loaders]
    window = GEOJSON_TYPE_LAST[:4096]
    assert b"FeatureCollection" not in window
    assert pick_loader(window, loaders).name == "json"
    assert pick_loader(GEOJSON_TYPE_LAST, loaders).name == "geojson-naive"


def test_geojson_loader_yields_schema_then_rows(with_geojson, tmp_path):
    path = write(tmp_path, "places.json", GEOJSON_TYPE_FIRST)
    name, schema, rows = rows_of(path)
    assert name == "geojson"
    assert schema.columns == {
        "name": "text",
        "pop": "float",
        "geometry": "text",
        "id": "text",
        "capital": "integer",
    }
    assert rows[0] == {
        "name": "London",
        "pop": 8800000,
        "geometry": '{"type": "Point", "coordinates": [-0.12, 51.5]}',
    }
    assert rows[1]["id"] == "p2"


def test_without_geojson_plugin_json_unwraps_features(tmp_path):
    path = write(tmp_path, "places.json", GEOJSON_TYPE_FIRST)
    name, schema, rows = rows_of(path)
    assert name == "json"
    assert schema is None
    assert len(rows) == 2
    assert rows[0]["properties"] == {"name": "London", "pop": 8800000}


# Growing the sniff window


def test_sniff_path_grows_window_when_undecided(tmp_path):
    # Each NDJSON line is longer than the first 64KB window
    lines = [json.dumps({"id": i, "blob": "x" * 100_000}) for i in range(3)]
    path = write(tmp_path, "long.ndjson", "\n".join(lines) + "\n")
    first_bytes, scores = sniff_path(path)
    assert scores[0].name == "ndjson"
    assert len(first_bytes) == 256 * 1024
    # Without growing, JSON wins (it reads several values one after another,
    # so it still loads the file, but it holds the whole file in memory)
    first_bytes, scores = sniff_path(path, max_sniff_bytes=64 * 1024)
    assert len(first_bytes) == 64 * 1024
    assert scores[0].name == "json"
    name, _, rows = rows_of(path)
    assert name == "ndjson"
    assert [row["id"] for row in rows] == [0, 1, 2]


def test_ndjson_with_lines_longer_than_the_biggest_window(tmp_path):
    # Verifier repro: lines over 1MB went to JSON, which failed with
    # "Extra data". JSON now reads one value after another.
    line = json.dumps({"id": 1, "blob": "x" * 1_200_000})
    path = write(tmp_path, "huge-lines.ndjson", line + "\n" + line + "\n")
    name, _, rows = rows_of(path)
    assert name == "json"
    assert [row["id"] for row in rows] == [1, 1]


def test_sniff_path_stops_at_decisive_score(tmp_path):
    path = write(tmp_path, "big.csv", "id,name\n" + "1,Cleo\n" * 50_000)
    first_bytes, scores = sniff_path(path)
    assert len(first_bytes) == L.SNIFF_BYTES
    assert scores[0].name == "csv"


# Core loaders


def test_csv_loader(tmp_path):
    path = write(tmp_path, "a.csv", 'id,name,notes\n1,Cleo,"a, b"\n2,Pancakes,\n')
    assert rows_of(path) == (
        "csv",
        None,
        [
            {"id": "1", "name": "Cleo", "notes": "a, b"},
            {"id": "2", "name": "Pancakes", "notes": ""},
        ],
    )


def test_csv_loader_semicolons(tmp_path):
    path = write(tmp_path, "a.csv", "id;name;price\n1;foo;1,5\n")
    assert rows_of(path)[2] == [{"id": "1", "name": "foo", "price": "1,5"}]


def test_csv_loader_bom(tmp_path):
    path = write(tmp_path, "a.csv", b"\xef\xbb\xbfid,name\n1,caf\xc3\xa9\n")
    assert rows_of(path)[2] == [{"id": "1", "name": "café"}]


def test_csv_loader_cp1252_fallback(tmp_path):
    # Invalid UTF-8 anywhere in the file decodes as cp1252 (or latin-1)
    content = (
        b"id,name\n"
        + b"".join(b"%d,ascii\n" % i for i in range(5000))
        + b"5000,caf\xe9 \x93quoted\x94 \x80\n"
        + b"5001,valid utf-8 caf\xc3\xa9\n"
    )
    path = write(tmp_path, "a.csv", content)
    rows = rows_of(path)[2]
    assert len(rows) == 5002
    assert rows[-2]["name"] == "café “quoted” €"
    assert rows[-1]["name"] == "valid utf-8 café"


def test_text_encoding_codec():
    assert b"caf\xe9".decode(L.TEXT_ENCODING) == "café"
    assert b"\xef\xbb\xbfcaf\xc3\xa9".decode(L.TEXT_ENCODING) == "café"
    # Bytes cp1252 leaves undefined fall back to latin-1
    assert b"\x81".decode(L.TEXT_ENCODING) == "\x81"


def test_one_column_csv(tmp_path):
    path = write(tmp_path, "a.csv", 'name\n"Smith, John"\nCleo\n')
    assert rows_of(path)[2] == [{"name": "Smith, John"}, {"name": "Cleo"}]


def test_tsv_loader(tmp_path):
    path = write(tmp_path, "a.tsv", "id\tname\n1\tCleo, the dog\n")
    assert rows_of(path) == ("tsv", None, [{"id": "1", "name": "Cleo, the dog"}])


def test_json_loader(tmp_path):
    path = write(tmp_path, "a.json", '[{"id": 1, "tags": ["a"]}, {"id": 2}]')
    assert rows_of(path) == ("json", None, [{"id": 1, "tags": ["a"]}, {"id": 2}])


def test_json_loader_unwraps_single_list_of_objects(tmp_path):
    path = write(
        tmp_path, "a.json", '{"data": [{"id": 1}, {"id": 2}], "meta": {"page": 1}}'
    )
    assert rows_of(path)[2] == [{"id": 1}, {"id": 2}]


def test_json_loader_single_object_is_one_row(tmp_path):
    path = write(tmp_path, "a.json", '{"id": 1, "a": [{"x": 1}], "b": [{"y": 2}]}')
    # Two candidate lists: ambiguous, so the object itself is the row
    assert rows_of(path)[2] == [{"id": 1, "a": [{"x": 1}], "b": [{"y": 2}]}]


def test_json_loader_rejects_array_of_scalars(tmp_path):
    path = write(tmp_path, "a.json", "[1, 2, 3]")
    with pytest.raises(LoaderError) as ex:
        rows_of(path, format="json")
    assert str(ex.value) == "JSON array item 0 is int, expected an object"


def test_ndjson_loader(tmp_path):
    path = write(tmp_path, "a.ndjson", '{"id": 1}\n\n{"id": 2, "x": null}\n')
    assert rows_of(path) == ("ndjson", None, [{"id": 1}, {"id": 2, "x": None}])


def test_ndjson_loader_rejects_non_objects(tmp_path):
    path = write(tmp_path, "a.ndjson", '{"id": 1}\n[1, 2]\n')
    with pytest.raises(LoaderError) as ex:
        rows_of(path, format="ndjson")
    assert str(ex.value) == "Line 2 is list, expected an object"


def test_load_path_with_explicit_format(tmp_path):
    # Without format= this sniffs as TSV
    path = write(tmp_path, "a.txt", "id\tname\n1\tCleo\n")
    assert rows_of(path, format="csv")[2] == [{"id\tname": "1\tCleo"}]


def test_load_path_unrecognized(tmp_path):
    path = write(tmp_path, "a.bin", b"\x00\x01\x02\x03" * 100)
    with pytest.raises(UnrecognizedFormat):
        load_path(path)


# Schema


def test_schema_normalizes_types():
    schema = Schema(
        {"a": "TEXT", "b": "INTEGER", "c": "REAL", "d": "float", "e": "blob"}
    )
    assert schema.columns == {
        "a": "text",
        "b": "integer",
        "c": "float",
        "d": "float",
        "e": "blob",
    }


def test_schema_rejects_unknown_types():
    with pytest.raises(ValueError) as ex:
        Schema({"a": "varchar"})
    assert "expected one of text, integer, float, blob" in str(ex.value)


def test_schema_is_one_line_of_json():
    schema = Schema({"name": "text", "geometry": "text", "pop": "integer"})
    line = json.dumps(schema.to_dict())
    assert "\n" not in line
    assert Schema.from_dict(json.loads(line)) == schema
    # Column order survives the round trip
    assert list(Schema.from_dict(json.loads(line)).columns) == [
        "name",
        "geometry",
        "pop",
    ]


def test_split_schema():
    schema, rows = split_schema(iter([Schema({"a": "text"}), {"a": "1"}]))
    assert schema == Schema({"a": "text"})
    assert list(rows) == [{"a": "1"}]
    schema, rows = split_schema(iter([{"a": "1"}, {"a": "2"}]))
    assert schema is None
    assert list(rows) == [{"a": "1"}, {"a": "2"}]
    schema, rows = split_schema(iter([]))
    assert schema is None
    assert list(rows) == []


def test_split_schema_rejects_late_schema_and_non_dicts():
    _, rows = split_schema(iter([{"a": 1}, Schema({"a": "text"})]), "bad")
    with pytest.raises(LoaderError) as ex:
        list(rows)
    assert str(ex.value) == "bad yielded a Schema after the first item"
    _, rows = split_schema(iter([{"a": 1}, ("a", 1)]), "bad")
    with pytest.raises(LoaderError) as ex:
        list(rows)
    assert str(ex.value) == "bad yielded tuple, expected a dict"


# peek_json()


def test_peek_json_cut_off_document():
    peek = peek_json(b'{"features": [{"type": "Feature", "geometry": {"type": "Po')
    assert not peek.complete
    assert isinstance(peek.value, PartialDict)
    features = peek.value["features"]
    assert isinstance(features, PartialList)
    assert features[0] == {"type": "Feature", "geometry": {}}
    assert isinstance(features[0], PartialDict)


@pytest.mark.parametrize(
    "content,value,complete,trailing",
    (
        (b'[{"a": 1}, {"b": 2}]', [{"a": 1}, {"b": 2}], True, False),
        (b'  {"a": [1, 2]}  \n', {"a": [1, 2]}, True, False),
        (b'{"a": 1}\n{"a": 2}\n', {"a": 1}, True, True),
        (b'[{"a": 1}, {"b": 12', [{"a": 1}, {}], False, False),
        (b'[{"a": "unterminated', [{}], False, False),
        (b'[{"a": tr', [{}], False, False),
        (b'[{"a": 1}, {"b"', [{"a": 1}, {}], False, False),
        (b"[", [], False, False),
        (b"\xef\xbb\xbf[1, 2", [1], False, False),
    ),
)
def test_peek_json(content, value, complete, trailing):
    peek = peek_json(content)
    assert peek.value == value
    assert peek.complete is complete
    assert peek.trailing is trailing


def test_peek_json_limits_items_in_cut_off_arrays():
    content = b"[" + b", ".join(b'{"id": %d}' % i for i in range(100))
    assert len(peek_json(content).value) == 20
    assert len(peek_json(content, max_items=5).value) == 5


@pytest.mark.parametrize(
    "content",
    (b"", b"id,name\n", b"[id],[name]\n1,2", b'"just a string"', b"42", b"{bad}"),
)
def test_peek_json_not_json(content):
    assert peek_json(content) is None


def test_peek_json_deep_nesting_does_not_crash():
    peek = peek_json(b"[" * 10_000)
    assert peek is not None
    assert not peek.complete


# Running a loader in a worker process, with no Datasette instance


def test_loader_in_subprocess_writes_ndjson(tmp_path):
    path = write(tmp_path, "a.csv", "id,name\n1,Cleo\n2,Pancakes\n")
    script = (
        "import json, sys\n"
        "from datasette.imports.loaders import load_path\n"
        "result = load_path(sys.argv[1])\n"
        "print(json.dumps({'loader': result.loader.name,\n"
        "    'schema': result.schema and result.schema.to_dict()}))\n"
        "for row in result.rows:\n"
        "    print(json.dumps(row))\n"
    )
    output = subprocess.run(
        [sys.executable, "-c", script, path],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    lines = [json.loads(line) for line in output.splitlines()]
    assert lines == [
        {"loader": "csv", "schema": None},
        {"id": "1", "name": "Cleo"},
        {"id": "2", "name": "Pancakes"},
    ]


def test_geojson_loader_class_has_example_hook():
    # The example plugin module registers its loader class through the hook
    assert geojson_loader_plugin.register_import_loaders() == [GeoJSONLoader]


# Regressions from the adversarial review of this prototype


def test_csv_sniff_does_not_depend_on_field_size_limit(tmp_path):
    # A field over csv's default 128KB limit was UnrecognizedFormat in a fresh
    # process, but csv once any other CSV had been loaded in that process
    path = write(tmp_path, "big-field.csv", f'id,body,n\n1,"{"x" * 200_000}",3\n')
    previous = csv.field_size_limit(131072)
    try:
        with open(path, "rb") as fp:
            assert pick_loader(fp.read(L.SNIFF_BYTES * 4)).name == "csv"
        _, scores = sniff_path(path)
        assert (scores[0].name, scores[0].score) == ("csv", pytest.approx(0.8))
        assert len(rows_of(path)[2][0]["body"]) == 200_000
    finally:
        csv.field_size_limit(previous)


def ndjson_lines(count):
    return [f'{{"id": {i}, "name": "n{i}", "v": {i * 2}}}' for i in range(count)]


@pytest.mark.parametrize(
    "lines,bad_line",
    (
        # One malformed line in the middle
        (ndjson_lines(10) + ['{"id": 10, "name": "trunc'] + ndjson_lines(9), 11),
        # The last line was cut off by a writer that was killed
        (ndjson_lines(5) + ['{"id": 5, "na'], 6),
        # Trailing junk
        (ndjson_lines(2) + ["not json"], 3),
    ),
)
def test_ndjson_with_a_bad_line_is_not_imported_as_csv(tmp_path, lines, bad_line):
    # These split into a consistent number of comma fields, so CSV used to
    # win and import columns called '{"id": 0' with no error
    path = write(tmp_path, "bad.ndjson", "\n".join(lines) + "\n")
    scores = {s.name: s.score for s in sniff_path(path)[1]}
    assert scores["csv"] == 0
    result = load_path(path)
    assert result.loader.name == "ndjson"
    with pytest.raises(LoaderError) as ex:
        list(result.rows)
    assert str(ex.value).startswith(f"Line {bad_line} is not valid JSON")


def test_ndjson_loader_falls_back_to_cp1252(tmp_path):
    content = '{"name": "café"}\n{"name": "naïve"}\n'.encode("cp1252")
    path = write(tmp_path, "latin1.ndjson", content)
    assert rows_of(path) == ("ndjson", None, [{"name": "café"}, {"name": "naïve"}])


def test_csv_with_short_rows(tmp_path):
    # Spreadsheets often leave out trailing empty fields. This was
    # UnrecognizedFormat, although it loads fine with the format forced.
    lines = ["a,b,c"] + [("1,2,3" if i % 3 else "1,2") for i in range(30)]
    path = write(tmp_path, "ragged.csv", "\n".join(lines) + "\n")
    name, _, rows = rows_of(path)
    assert name == "csv"
    assert len(rows) == 30
    assert rows[0] == {"a": "1", "b": "2", "c": None}
    assert rows[1] == {"a": "1", "b": "2", "c": "3"}


def test_csv_with_long_rows_keeps_extra_values(tmp_path):
    lines = ["a,b"] + ["1,2"] * 19 + ["3,4,5,6"]
    path = write(tmp_path, "long-rows.csv", "\n".join(lines) + "\n")
    name, _, rows = rows_of(path)
    assert name == "csv"
    assert rows[-1] == {"a": "3", "b": "4", "_extra": ["5", "6"]}


def test_extras_key_does_not_overwrite_a_column(tmp_path):
    path = write(tmp_path, "a.csv", "a,_extra\n1,2\n3,4,5\n")
    rows = rows_of(path, format="csv")[2]
    assert rows[1] == {"a": "3", "_extra": "4", "_extra_2": ["5"]}


def test_ragged_tsv_is_still_tsv(tmp_path):
    lines = ["a\tb\tc"] + [("1\t2\t3" if i % 3 else "1\t2") for i in range(30)]
    path = write(tmp_path, "ragged.tsv", "\n".join(lines) + "\n")
    name, _, rows = rows_of(path)
    assert name == "tsv"
    assert rows[0] == {"a": "1", "b": "2", "c": None}


def test_tsv_with_literal_quotes(tmp_path):
    # A field starting with an unclosed quote swallowed the rest of the file,
    # so this became a one-column CSV
    content = 'name\tsize\n"Big" TV\t55\nscreen 5" wide\t7\n"unbalanced\t1\nnext\t2\n'
    path = write(tmp_path, "quotes.tsv", content)
    name, _, rows = rows_of(path)
    assert name == "tsv"
    assert rows == [
        {"name": '"Big" TV', "size": "55"},
        {"name": 'screen 5" wide', "size": "7"},
        {"name": '"unbalanced', "size": "1"},
        {"name": "next", "size": "2"},
    ]


def test_csv_quoted_fields_still_use_quotes(tmp_path):
    content = 'id,notes\n1,"line one\nline two"\n2,"a, ""b"""\n'
    path = write(tmp_path, "quoted.csv", content)
    assert rows_of(path)[2] == [
        {"id": "1", "notes": "line one\nline two"},
        {"id": "2", "notes": 'a, "b"'},
    ]


@pytest.mark.parametrize(
    "content,error",
    (
        (b'[["id","name"],[1,"a"],[2,"b"]]', "JSON array item 0 is list"),
        (b'[\n  ["id", "name"],\n  [1, "a"]\n]\n', "JSON array item 0 is list"),
        (b"[1,2,3]", "JSON array item 0 is int"),
        (b"[1,2]\n[3,4]\n[5,6]\n", "Line 1 is list"),
    ),
)
def test_json_that_is_not_rows_raises_an_error(tmp_path, content, error):
    # These used to import as a header-only CSV: zero rows and no error
    path = write(tmp_path, "a.json", content)
    assert sniff_path(path)[1][0].name in ("json", "ndjson")
    with pytest.raises(LoaderError) as ex:
        list(load_path(path).rows)
    assert str(ex.value).startswith(error)


def test_csv_never_wins_on_a_json_document():
    for content in (b'{"a": 1}', b'[{"a": 1}', b'{"a": [1, 2', b'{"a": 1}\n{"b'):
        assert L.CSVLoader().sniff(content) == 0, content
        assert L.TSVLoader().sniff(content) == 0, content
    # ...but a CSV whose first field looks like JSON is still CSV
    assert L.CSVLoader().sniff(b'{"a": 1},first\n{"a": 2},second\n') == 0.8


def test_concatenated_json_objects(tmp_path):
    # jq prints objects like this
    path = write(tmp_path, "jq.json", '{\n  "a": 1\n}\n{\n  "a": 2\n}\n')
    assert rows_of(path) == ("json", None, [{"a": 1}, {"a": 2}])


def test_concatenated_json_must_be_objects(tmp_path):
    path = write(tmp_path, "a.json", '{"a": 1}\n[1, 2]\n')
    with pytest.raises(LoaderError) as ex:
        rows_of(path, format="json")
    assert str(ex.value) == "JSON value 2 is list, expected an object"


def test_invalid_json_raises_loader_error(tmp_path):
    path = write(tmp_path, "a.json", '[{"a": 1}, {"a": ]')
    with pytest.raises(LoaderError) as ex:
        rows_of(path, format="json")
    assert str(ex.value).startswith("Invalid JSON: Expecting value")


@pytest.mark.parametrize(
    "header,columns",
    (
        ("name,name,age", ["name", "name_2", "age"]),
        ("name,Name,NAME", ["name", "Name_2", "NAME_3"]),
        ("a,a,a_2", ["a", "a_2", "a_2_2"]),
        (",a,", ["column_1", "a", "column_3"]),
    ),
)
def test_duplicate_and_blank_headers_are_renamed(tmp_path, header, columns):
    # DictReader kept only the last of two columns with the same name
    values = [f"v{i}" for i in range(len(columns))]
    path = write(tmp_path, "a.csv", f"{header}\n" + ",".join(values) + "\n")
    rows = rows_of(path, format="csv")[2]
    assert list(rows[0]) == columns
    assert list(rows[0].values()) == values


def test_header_only_csv_yields_schema(tmp_path):
    path = write(tmp_path, "a.csv", "id,name,age\n")
    assert rows_of(path) == (
        "csv",
        Schema({"id": "text", "name": "text", "age": "text"}),
        [],
    )


def test_nul_byte_in_csv(tmp_path):
    path = write(tmp_path, "a.csv", b"a,b\n1,x\x00y\n2,z\n")
    assert rows_of(path) == (
        "csv",
        None,
        [{"a": "1", "b": "x\x00y"}, {"a": "2", "b": "z"}],
    )


def test_load_uses_the_dialect_sniff_path_chose(tmp_path):
    # The header is longer than the first window, and its first column name
    # contains a comma. sniff_path() grows to 256KB and finds ";", but load()
    # used to look at 64KB only and split on ","
    columns = ["a,b"] + [f"column_{i:05d}" for i in range(8000)]
    lines = [";".join(columns)] + [";".join("1" for _ in columns)] * 3
    path = write(tmp_path, "wide.csv", "\n".join(lines) + "\n")
    first_bytes, scores = sniff_path(path)
    assert len(first_bytes) > L.SNIFF_BYTES
    assert scores[0].name == "csv"
    rows = rows_of(path)[2]
    assert len(rows) == 3
    assert list(rows[0])[:2] == ["a,b", "column_00000"]


def test_sniff_windows():
    fp = io.BytesIO(b"x" * 300_000)
    assert [len(w) for w in L.sniff_windows(fp)] == [65536, 262144, 300_000]
    fp = io.BytesIO(b"x" * 100)
    assert [len(w) for w in L.sniff_windows(fp)] == [100]


def test_json_unwrap_notes_the_keys_it_ignores(tmp_path, caplog):
    path = write(
        tmp_path, "a.json", '{"total": 2, "next": null, "results": [{"id": 1}]}'
    )
    with caplog.at_level(logging.WARNING, logger="datasette.imports.loaders"):
        result = load_path(path)
        # The note is there as soon as load_path() returns...
        assert result.notes == [
            "Importing the 1 object in 'results', ignoring 'total', 'next'"
        ]
        assert list(result.rows) == [{"id": 1}]
    # ...and logged once, marked as a note
    records = [r for r in caplog.records if hasattr(r, L.NOTE_LOG_ATTRIBUTE)]
    assert [(r.getMessage(), getattr(r, L.NOTE_LOG_ATTRIBUTE)) for r in records] == [
        ("Importing the 1 object in 'results', ignoring 'total', 'next'", "json")
    ]
    path = write(tmp_path, "b.json", '{"total": 2, "results": [{"id": 1}, {"id": 2}]}')
    assert load_path(path).notes == [
        "Importing the 2 objects in 'results', ignoring 'total'"
    ]
    # Nothing to say about a plain array
    path = write(tmp_path, "c.json", '[{"id": 1}]')
    assert load_path(path).notes == []


def test_csv_notes_rows_with_extra_values(tmp_path):
    lines = ["a,b"] + ["1,2"] * 19 + ["3,4,5,6"] + ["1,2"] * 5 + ["7,8,9"]
    path = write(tmp_path, "long-rows.csv", "\n".join(lines) + "\n")
    result = load_path(path)
    assert result.notes == []
    rows = list(result.rows)
    assert len(rows) == 26
    # Known once every row has been read
    assert result.notes == [
        (
            "2 rows have more fields than the header (the first on line 21): "
            "the extra values are in the '_extra' column"
        )
    ]


def test_tsv_with_literal_quotes_has_a_note(tmp_path):
    content = 'name\tsize\n"unbalanced\t1\nnext\t2\n' + "x\t3\n" * 5
    path = write(tmp_path, "quotes.tsv", content)
    result = load_path(path)
    assert result.loader.name == "tsv"
    assert len(list(result.rows)) == 7
    assert len(result.notes) == 1
    assert result.notes[0].startswith('Reading quotes (") as ordinary characters')


class NoteLoader(Loader):
    name = "notes"

    def load(self, path):
        yield L.Note("first")
        yield {"a": 1}
        yield L.Note("last")


def test_plugin_loader_notes(tmp_path):
    path = write(tmp_path, "a.txt", "anything")
    result = load_path(path, loaders=[NoteLoader()], format="notes")
    assert result.notes == ["first"]
    assert list(result.rows) == [{"a": 1}]
    assert result.notes == ["first", "last"]


def test_split_schema_collects_notes_anywhere(caplog):
    notes = []
    items = [
        L.Note("before the schema"),
        Schema({"a": "text"}),
        L.Note("before the rows"),
        {"a": "1"},
        L.Note("between rows"),
        {"a": "2"},
        L.Note("at the end"),
    ]
    with caplog.at_level(logging.WARNING, logger="datasette.imports.loaders"):
        schema, rows = split_schema(items, "notes", notes=notes)
        assert schema == Schema({"a": "text"})
        assert notes == ["before the schema"]
        assert list(rows) == [{"a": "1"}, {"a": "2"}]
    assert notes == [
        "before the schema",
        "before the rows",
        "between rows",
        "at the end",
    ]
    assert [r.getMessage() for r in caplog.records] == notes
    # Without a list the notes are only logged; a note-only load has no rows
    assert split_schema([L.Note("x")], "notes")[0] is None
    schema, rows = split_schema([L.Note("x")], "notes")
    assert (schema, list(rows)) == (None, [])
    # A schema after a row is still an error, notes or not
    with pytest.raises(LoaderError, match="Schema after the first item"):
        list(split_schema([{"a": 1}, L.Note("x"), Schema({"a": "text"})])[1])


def test_note_needs_a_message():
    with pytest.raises(ValueError):
        L.Note("")
    with pytest.raises(ValueError):
        L.Note(None)
    assert str(L.Note("hello")) == "hello"


def test_choose_loader():
    csv_loader, json_loader = get_loader("csv"), get_loader("json")
    scores = [L.LoaderScore(json_loader, 0.9), L.LoaderScore(csv_loader, 0.2)]
    assert L.choose_loader(scores, b"[{}]") is json_loader
    with pytest.raises(UnrecognizedFormat) as ex:
        L.choose_loader(scores, b"[{}]", min_confidence=0.95)
    assert ex.value.scores is scores
    assert "json=0.90, csv=0.20" in str(ex.value)
    low = [L.LoaderScore(csv_loader, 0.05)]
    with pytest.raises(UnrecognizedFormat):
        L.choose_loader(low, b"abc")
    with pytest.raises(UnrecognizedFormat, match="no loaders"):
        L.choose_loader([], b"abc")
    for empty in (b"", b"  \n", codecs.BOM_UTF8 + b"\n"):
        with pytest.raises(EmptyFile):
            L.choose_loader(scores, empty)


def test_peek_json_limits_keys_in_cut_off_objects():
    content = b"{" + b", ".join(b'"k%d": %d' % (i, i) for i in range(100))
    assert len(peek_json(content).value) == 20
    assert len(peek_json(content, max_items=5).value) == 5
    # A complete object is parsed in full
    assert len(peek_json(content + b"}").value) == 100


def test_peek_json_long_string_inside_cut_off_document():
    content = b'[{"a": "' + b"x" * 100_000 + b'", "b": 1}, {"c": '
    peek = peek_json(content)
    assert peek.value == [{"a": "x" * 100_000, "b": 1}, {}]


@pytest.mark.parametrize("depth", (5, 31))
def test_peek_json_deep_then_long(depth):
    # Used to re-parse the rest of the window at every level of nesting
    inner = "[" + ",".join(str(i) for i in range(50_000))
    content = ('{"a":' * depth + inner).encode()
    value = peek_json(content).value
    for _ in range(depth):
        value = value["a"]
    assert value == list(range(20))


def test_raising_plugin_hook_does_not_break_other_loaders(register, caplog):
    class Raises:
        @hookimpl
        def register_import_loaders(self):
            raise RuntimeError("hook impl raised")

    register(Raises(), "test-raising-hook")
    with caplog.at_level(logging.WARNING, logger="datasette.imports.loaders"):
        assert pick_loader(b"a,b\n1,2\n").name == "csv"
    assert "register_import_loaders() failed" in caplog.text


def test_loader_whose_constructor_raises_is_skipped(register, caplog):
    class Broken(Loader):
        name = "broken"

        def __init__(self):
            raise RuntimeError("boom")

    register(_Plugin(Broken, FixedLoader("fine", 0.95)), "test-broken-loader")
    with caplog.at_level(logging.WARNING, logger="datasette.imports.loaders"):
        names = {loader.name for loader in get_loaders()}
    assert "broken" not in names
    assert names >= CORE | {"fine"}
    assert "could not create import loader" in caplog.text


def _reference_decode(data):
    # The simplest correct fallback: one error handler call per invalid byte
    def per_byte(error):
        invalid = error.object[error.start : error.end]
        return "".join(L._FALLBACK_TABLE[byte] for byte in invalid), error.end

    codecs.register_error("test-per-byte-fallback", per_byte)
    return data.removeprefix(codecs.BOM_UTF8).decode("utf-8", "test-per-byte-fallback")


@pytest.mark.parametrize("chunk_size", (1, 2, 3, 5, 64, 8192))
def test_text_encoding_matches_per_byte_fallback(chunk_size):
    # The fast fallback decodes runs of invalid bytes at once; check it gives
    # the same text as decoding byte by byte, including UTF-8 characters split
    # across chunks and cp1252 bytes that look like the start of UTF-8
    rng = random.Random(chunk_size)
    pieces = [
        "café".encode(),
        "caf\xe9".encode("cp1252"),
        "“quoted” €".encode("cp1252"),
        "“quoted” €".encode(),
        "日本 😀".encode(),
        b"\x81\x8d\x8f\x90\x9d",
        b"\xc3",  # a lone UTF-8 lead byte
        b"\xe9\xa9",  # looks like the start of a 3-byte sequence
        b"\xc3\xa9\xa9",
        b"plain ascii, ",
        b"\n",
    ]
    data = codecs.BOM_UTF8 + b"".join(rng.choice(pieces) for _ in range(2000))
    decoder = codecs.getincrementaldecoder(L.TEXT_ENCODING)()
    text = "".join(
        decoder.decode(data[i : i + chunk_size])
        for i in range(0, len(data), chunk_size)
    ) + decoder.decode(b"", final=True)
    assert text == _reference_decode(data)
    assert data.decode(L.TEXT_ENCODING) == _reference_decode(data)


def test_text_encoding_encodes_without_bom():
    assert "café".encode(L.TEXT_ENCODING) == "café".encode()


def test_geojson_sorted_keys_with_big_first_geometry(with_geojson, tmp_path):
    # With sort_keys every "type" comes last, and a big first polygon fills
    # the window before any "type" appears, so JSON used to win
    ring = [[i * 0.001, i * 0.002] for i in range(60_000)]
    features = [
        {
            "type": "Feature",
            "properties": {"name": "big"},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        },
        FEATURE,
    ]
    doc = {"type": "FeatureCollection", "features": features}
    path = write(tmp_path, "sorted.json", json.dumps(doc, sort_keys=True))
    first_bytes, scores = sniff_path(path)
    assert b'"type"' not in first_bytes
    assert scores[0].name == "geojson"
    name, _, rows = rows_of(path)
    assert name == "geojson"
    assert [row["name"] for row in rows] == ["big", "London"]


def test_geojson_sorted_single_feature(with_geojson):
    feature = json.dumps(
        {"geometry": {"coordinates": [[1.0, 2.0]] * 5000}, "type": "Feature"},
        sort_keys=True,
    )
    assert pick_loader(feature.encode()[:4096]).name == "geojson"


def test_geojson_properties_do_not_overwrite_id_or_geometry(with_geojson, tmp_path):
    doc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": 7,
                "properties": {"id": "prop-id", "geometry": "prop", "name": "x"},
                "geometry": None,
            },
            {"type": "Feature", "properties": {"id": "no-feature-id"}},
        ],
    }
    path = write(tmp_path, "collide.geojson", json.dumps(doc))
    rows = rows_of(path)[2]
    assert rows == [
        {
            "id": 7,
            "geometry": None,
            "properties.id": "prop-id",
            "properties.geometry": "prop",
            "name": "x",
        },
        {"geometry": None, "id": "no-feature-id"},
    ]
