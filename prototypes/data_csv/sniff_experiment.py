"""
Prototype 2 experiment: competing format loaders.

Scores every file with the core loaders plus an example GeoJSON loader
(tests/geojson_loader_plugin.py, registered through the
register_import_loaders() plugin hook), at a range of sniff window sizes, and
runs the messy-data files through the core loaders.

    uv run python prototypes/data_csv/sniff_experiment.py DATA_DIR WORK_DIR

DATA_DIR holds the shared test files (events-100k.csv, places.geojson, ...).
WORK_DIR is where extra edge-case files are written.
"""

import io
import json
import os
import sqlite3
import statistics
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from sqlite_utils.utils import rows_from_file

from datasette.imports import loaders as L
from datasette.plugins import pm
from tests import geojson_loader_plugin

WINDOWS = [
    256,
    1024,
    2048,
    4096,
    16 * 1024,
    64 * 1024,
    256 * 1024,
    320 * 1024,
    1024 * 1024,
]


def make_edge_cases(work):
    os.makedirs(work, exist_ok=True)
    files = {}

    def write(name, content):
        path = os.path.join(work, name)
        with open(path, "wb") as fp:
            fp.write(content)
        files[name] = path

    write("empty.csv", b"")
    write("whitespace-and-bom.csv", b"\xef\xbb\xbf  \n\n")
    write(
        "json-looking-first-line.csv",
        b'{"a": 1},first\n{"a": 2},second\n{"a": 3},third\n',
    )
    write("bracket-headers.csv", b"[id],[name]\n1,Cleo\n2,Pancakes\n")
    write(
        "quoted-json-field.csv",
        b'"{""id"": 1}",note\n"{""id"": 2}",other\n',
    )
    write("one-column.csv", b"name\nCleo\nPancakes\nSirocco\n")
    write("one-column-with-quoted-comma.csv", b'name\n"Smith, John"\nCleo\n')
    write("bom.json", b'\xef\xbb\xbf[{"id": 1, "name": "caf\xc3\xa9"}]')
    write("bom.ndjson", b'\xef\xbb\xbf{"id": 1}\n{"id": 2}\n')
    write("bom.tsv", b"\xef\xbb\xbfid\tname\n1\tcaf\xc3\xa9\n")
    write(
        "readme.txt",
        b"# A README\n\nThis is prose, with commas, and more commas.\n"
        b"It is not a CSV file at all.\nOne more line, really.\n",
    )
    write(
        "pretty.json",
        json.dumps([{"id": i, "name": f"n{i}"} for i in range(50)], indent=2).encode(),
    )
    # GeoJSON with the features first and a first feature bigger than the
    # default window: geometry key first, then type
    big_ring = [[[i * 0.001, i * 0.002] for i in range(20000)]]
    write(
        "geojson-huge-first-feature.json",
        json.dumps(
            {
                "features": [
                    {
                        "geometry": {"type": "Polygon", "coordinates": big_ring},
                        "type": "Feature",
                        "properties": {"name": "big"},
                    }
                ],
                "type": "FeatureCollection",
            }
        ).encode(),
    )
    write(
        "geojson-pretty-features-first.json",
        json.dumps(
            {
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": f"p{i}"},
                        "geometry": {"type": "Point", "coordinates": [i, i]},
                    }
                    for i in range(3000)
                ],
                "type": "FeatureCollection",
            },
            indent=2,
        ).encode(),
    )
    # A 5MB FeatureCollection with "type" last: bigger than MAX_SNIFF_BYTES
    write(
        "geojson-5mb-type-last.json",
        json.dumps(
            {
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [i, i]},
                        "properties": {"name": f"p{i}", "n": i},
                    }
                    for i in range(50000)
                ],
                "type": "FeatureCollection",
            }
        ).encode(),
    )
    # cp1252 bytes that only appear after the first sniff window
    write(
        "latin1-late.csv",
        b"id,name\n"
        + b"".join(b"%d,plain ascii row\n" % i for i in range(5000))
        + b"5000,caf\xe9 \x93quoted\x94 \x80 \x81\n",
    )
    # NDJSON whose first record is longer than the default window
    write(
        "ndjson-long-lines.ndjson",
        b"".join(
            json.dumps({"id": i, "blob": "x" * 100_000}).encode() + b"\n"
            for i in range(3)
        ),
    )
    # A SQLite database file: should be unrecognized
    db_path = os.path.join(work, "binary.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("create table t (id integer primary key, name text)")
    conn.executemany("insert into t (name) values (?)", [("a",), ("b",)])
    conn.commit()
    conn.close()
    files["binary.db"] = db_path
    return files


def read_head(path, size):
    with open(path, "rb") as fp:
        return fp.read(size)


def choose(first, scores):
    try:
        return L._choose(scores, first, L.MIN_CONFIDENCE).name
    except L.EmptyFile:
        return "(empty file)"
    except L.UnrecognizedFormat:
        return "(unrecognized)"


def pick(path, loaders, size):
    "Fixed window: score the first size bytes only"
    first = read_head(path, size)
    scores = L.score_loaders(first, loaders)
    return choose(first, scores), scores


def pick_growing(path, loaders, size=L.SNIFF_BYTES):
    "What load_path() does: sniff_path() grows the window while undecided"
    first, scores = L.sniff_path(path, loaders, sniff_bytes=size)
    return choose(first, scores), scores, len(first)


def main(data_dir, work_dir):
    pm.register(geojson_loader_plugin, name="geojson-example")
    try:
        loaders = L.get_loaders()
    finally:
        pm.unregister(name="geojson-example")
    core = [loader for loader in loaders if loader.name != "geojson"]
    naive = core + [geojson_loader_plugin.NaiveGeoJSONLoader()]
    names = ["csv", "tsv", "json", "ndjson", "geojson"]

    shared = [
        "places.geojson",
        "places-type-last.json",
        "plain-array.json",
        "events-100k.json",
        "events-100k.ndjson",
        "events-100k.csv",
        "events-100k.tsv",
        "messy-bom.csv",
        "messy-latin1.csv",
        "messy-semicolon.csv",
        "messy-wrapped.json",
    ]
    files = {name: os.path.join(data_dir, name) for name in shared}
    files.update(make_edge_cases(work_dir))

    print(
        f"## Scores with sniff_path() (starts at {L.SNIFF_BYTES} bytes, grows x4 "
        f"to {L.MAX_SNIFF_BYTES} while the best score is below "
        f"{L.DECISIVE_CONFIDENCE})\n"
    )
    print(
        "| file | "
        + " | ".join(names)
        + " | bytes sniffed | winner | geojson-naive score (bytes sniffed) "
        "| winner, naive GeoJSON sniff | winner, core loaders only |"
    )
    print("|" + "---|" * (len(names) + 6))
    for name, path in files.items():
        chosen, scores, size = pick_growing(path, loaders)
        by_name = {s.name: s.score for s in scores}
        naive_chosen, naive_scores, naive_size = pick_growing(path, naive)
        naive_score = {s.name: s.score for s in naive_scores}["geojson-naive"]
        core_chosen, _, _ = pick_growing(path, core)
        print(
            f"| {name} | "
            + " | ".join(f"{by_name[n]:.2f}" for n in names)
            + f" | {size} | **{chosen}** | {naive_score:.2f} ({naive_size}) "
            f"| {naive_chosen} | {core_chosen} |"
        )

    print("\n## Winner by starting sniff window size\n")
    sweep = [
        "places.geojson",
        "places-type-last.json",
        "geojson-huge-first-feature.json",
        "geojson-pretty-features-first.json",
        "geojson-5mb-type-last.json",
        "events-100k.json",
        "events-100k.ndjson",
        "ndjson-long-lines.ndjson",
        "events-100k.csv",
    ]
    print(
        "| file | window | fixed window: structural GeoJSON | fixed window: naive "
        "| growing window: structural | growing window: naive |"
    )
    print("|---|---|---|---|---|---|")
    for name in sweep:
        for size in WINDOWS:
            chosen, _ = pick(files[name], loaders, size)
            naive_chosen, _ = pick(files[name], naive, size)
            grow, _, grow_size = pick_growing(files[name], loaders, size)
            grow_naive, _, grow_naive_size = pick_growing(files[name], naive, size)
            print(
                f"| {name} | {size} | {chosen} | {naive_chosen} "
                f"| {grow} ({grow_size}) | {grow_naive} ({grow_naive_size}) |"
            )

    print("\n## sniff() time per loader (median of 20 runs, ms)\n")
    print("| file | window | " + " | ".join(names) + " | all |")
    print("|" + "---|" * (len(names) + 3))
    for name in [
        "places.geojson",
        "places-type-last.json",
        "events-100k.json",
        "events-100k.ndjson",
        "events-100k.csv",
        "events-100k.tsv",
    ]:
        for size in (64 * 1024, 1024 * 1024):
            first = read_head(files[name], size)
            cells = []
            total = 0.0
            for loader in sorted(loaders, key=lambda loader: names.index(loader.name)):
                runs = []
                for _ in range(20):
                    start = time.perf_counter()
                    loader.sniff(first)
                    runs.append(time.perf_counter() - start)
                median = statistics.median(runs) * 1000
                total += median
                cells.append(f"{median:.2f}")
            print(f"| {name} | {size} | " + " | ".join(cells) + f" | {total:.2f} |")

    print("\n## Messy data\n")
    print(
        "| file | sqlite-utils rows_from_file() auto-detect | datasette core loaders |"
    )
    print("|---|---|---|")
    messy = [
        "messy-bom.csv",
        "messy-latin1.csv",
        "messy-semicolon.csv",
        "messy-wrapped.json",
        "bom.json",
        "bom.ndjson",
        "bom.tsv",
        "json-looking-first-line.csv",
        "bracket-headers.csv",
        "quoted-json-field.csv",
        "one-column.csv",
        "one-column-with-quoted-comma.csv",
        "readme.txt",
        "pretty.json",
        "ndjson-long-lines.ndjson",
        "empty.csv",
        "whitespace-and-bom.csv",
        "binary.db",
        "latin1-late.csv",
    ]
    for name in messy:
        print(
            f"| {name} | {describe(sqlite_utils_rows, files[name])} | "
            f"{describe(datasette_rows, files[name], loaders)} |"
        )


def sqlite_utils_rows(path):
    with open(path, "rb") as fp:
        rows, format = rows_from_file(io.BytesIO(fp.read()))
        return format.name, list(rows)


def datasette_rows(path, loaders):
    result = L.load_path(path, loaders)
    return result.loader.name, list(result.rows)


def describe(fn, *args):
    try:
        format, rows = fn(*args)
    except Exception as ex:  # noqa: BLE001 - report every failure
        last = traceback.extract_tb(ex.__traceback__)[-1]
        where = os.path.basename(last.filename)
        message = str(ex).replace("|", "\\|").replace("\n", " ")[:90]
        return f"ERROR {type(ex).__name__}: {message} ({where})"
    sample = json.dumps(rows[:2] if len(rows) < 1000 else rows[-1:], ensure_ascii=False)
    if len(sample) > 110:
        sample = sample[:110] + "..."
    sample = sample.replace("|", "\\|")
    return f"{format}: {len(rows)} rows `{sample}`"


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
