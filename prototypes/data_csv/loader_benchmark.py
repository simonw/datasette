"""
Prototype 2 benchmark: how fast the core loaders produce rows, how much
memory they use, and what the cp1252-fallback text codec costs.

    uv run python prototypes/data_csv/loader_benchmark.py DATA_DIR [--big]

Each measurement runs in a fresh subprocess so peak RSS is per measurement.
Rows are only counted, not inserted: this measures parsing, not SQLite.
--big also reads big-1gb.csv (about a minute per mode).
"""

import json
import os
import resource
import statistics
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def child(mode, path):
    sys.path.insert(0, ROOT)
    # Import first so the timings below measure the work, not the imports
    from sqlite_utils.utils import Format, rows_from_file

    if mode != "sqlite-utils":
        from datasette.imports.loaders import (
            TEXT_ENCODING,
            get_loaders,
            load_path,
            sniff_path,
        )
        from datasette.plugins import pm
    start = time.perf_counter()
    count = 0
    loader_name = None
    if mode == "datasette":
        result = load_path(path)
        loader_name = result.loader.name
        for _ in result.rows:
            count += 1
    elif mode == "datasette+geojson":
        from tests import geojson_loader_plugin

        pm.register(geojson_loader_plugin, name="geojson-example")
        result = load_path(path, get_loaders())
        loader_name = result.loader.name
        for _ in result.rows:
            count += 1
    elif mode == "sqlite-utils":
        # What `sqlite-utils memory` does: rows_from_file() detects the format
        with open(path, "rb") as fp:
            rows, format = rows_from_file(fp)
            loader_name = format.name
            for _ in rows:
                count += 1
    elif mode in ("csv-utf-8-sig", "csv-datasette-utf8"):
        encoding = "utf-8-sig" if mode == "csv-utf-8-sig" else TEXT_ENCODING
        with open(path, "rb") as fp:
            rows, _ = rows_from_file(fp, format=Format.CSV, encoding=encoding)
            for _ in rows:
                count += 1
        loader_name = encoding
    elif mode == "sniff":
        first_bytes, scores = sniff_path(path)
        loader_name = f"{scores[0].name} ({len(first_bytes)} bytes)"
    seconds = time.perf_counter() - start
    maxrss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(
        json.dumps(
            {
                "rows": count,
                "seconds": seconds,
                "maxrss_mb": maxrss_kb / 1024,
                "loader": loader_name,
            }
        )
    )


def run(mode, path):
    output = subprocess.run(
        [sys.executable, __file__, "--child", mode, path],
        check=False,
        capture_output=True,
        text=True,
    )
    if output.returncode:
        last = (output.stderr.strip().splitlines() or ["?"])[-1]
        return {"error": last[:100]}
    return json.loads(output.stdout)


def startup_seconds(code, runs=7):
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        subprocess.run([sys.executable, "-c", code], check=True, cwd=ROOT)
        times.append(time.perf_counter() - start)
    return statistics.median(times)


def row(cells):
    print("| " + " | ".join(str(cell) for cell in cells) + " |")


def main(data_dir, big):
    print("## Worker start-up cost (median of 7 runs)\n")
    row(["python -c ...", "seconds"])
    row(["---", "---"])
    for code in (
        "pass",
        "import sqlite_utils.utils",
        "import datasette.imports.loaders as L; L.get_loaders()",
    ):
        row([f"`{code}`", f"{startup_seconds(code):.3f}"])

    print("\n## Rows from each loader (fresh process each, rows counted only)\n")
    row(["file", "mode", "picked", "rows", "seconds", "rows/s", "peak RSS MB"])
    row(["---"] * 7)
    cases = [
        ("events-100k.csv", "datasette"),
        ("events-100k.csv", "sqlite-utils"),
        ("events-100k.tsv", "datasette"),
        ("events-100k.tsv", "sqlite-utils"),
        ("events-100k.json", "datasette"),
        ("events-100k.json", "sqlite-utils"),
        ("events-100k.ndjson", "datasette"),
        ("events-100k.ndjson", "sqlite-utils"),
        ("places.geojson", "datasette+geojson"),
        ("places-type-last.json", "datasette+geojson"),
        ("events-100k.csv", "csv-utf-8-sig"),
        ("events-100k.csv", "csv-datasette-utf8"),
        ("big-1gb.csv", "sniff"),
    ]
    if big:
        cases += [
            ("big-1gb.csv", "csv-utf-8-sig"),
            ("big-1gb.csv", "csv-datasette-utf8"),
            ("big-1gb.csv", "datasette"),
        ]
    for name, mode in cases:
        repeats = 2 if name.startswith("big") and mode != "sniff" else 3
        results = [run(mode, os.path.join(data_dir, name)) for _ in range(repeats)]
        if "error" in results[0]:
            row([name, mode, "ERROR " + results[0]["error"], "", "", "", ""])
            continue
        seconds = statistics.median(r["seconds"] for r in results)
        rate = results[0]["rows"] / seconds if seconds and results[0]["rows"] else 0
        row(
            [
                name,
                mode,
                results[0]["loader"],
                results[0]["rows"],
                f"{seconds:.3f}",
                f"{rate:,.0f}",
                f"{max(r['maxrss_mb'] for r in results):.0f}",
            ]
        )


if __name__ == "__main__":
    if sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3])
    else:
        main(sys.argv[1], "--big" in sys.argv)
