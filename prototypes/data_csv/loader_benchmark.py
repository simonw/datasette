"""
Prototype 2 benchmark: how fast the core loaders produce rows, how much
memory they use, and what the cp1252-fallback text codec costs.

    uv run python prototypes/data_csv/loader_benchmark.py DATA_DIR WORK_DIR [--big]

Each measurement runs in a fresh subprocess, so peak RSS is per measurement
and nothing (csv field size limit, codec caches) carries over. Peak RSS is
VmHWM from /proc/self/status: getrusage()'s ru_maxrss survives exec on Linux,
so a child would report the parent's peak if that was higher. The modes
being compared for one file run interleaved - one round runs every mode once,
in a rotated order - so drift in machine load affects them all alike. Rows
are only counted, not inserted: this measures parsing, not SQLite.

WORK_DIR gets two generated 500,000 row files with accented text, one UTF-8
and one really cp1252, for the codec cost. --big also reads big-1gb.csv
(about 15 seconds per run).
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

    from datasette.imports.loaders import TEXT_ENCODING, load_path, sniff_path
    from datasette.plugins import pm

    start = time.perf_counter()
    count = 0
    if mode in ("load_path", "load_path+geojson"):
        # Everything a CLI import pays: get_loaders(), sniff_path(), load()
        if mode == "load_path+geojson":
            from tests import geojson_loader_plugin

            pm.register(geojson_loader_plugin, name="geojson-example")
        result = load_path(path)
        picked = result.loader.name
        for _ in result.rows:
            count += 1
    elif mode == "sqlite-utils auto":
        # What `sqlite-utils memory` does: rows_from_file() detects the format
        with open(path, "rb") as fp:
            rows, format = rows_from_file(fp)
            picked = format.name
            for _ in rows:
                count += 1
    elif mode.startswith("rows_from_file "):
        # sqlite-utils parsing with the format forced: what the first version
        # of the core loaders wrapped
        _, format_name, encoding = mode.split()
        encoding = TEXT_ENCODING if encoding == "datasette-utf8" else encoding
        with open(path, "rb") as fp:
            rows, _ = rows_from_file(fp, format=Format[format_name], encoding=encoding)
            for _ in rows:
                count += 1
        picked = format_name
    elif mode == "sniff_path":
        first_bytes, scores = sniff_path(path)
        picked = f"{scores[0].name} ({len(first_bytes)} bytes)"
    else:
        raise ValueError(mode)
    seconds = time.perf_counter() - start
    maxrss_kb = peak_rss_kb()
    print(
        json.dumps(
            {
                "rows": count,
                "seconds": seconds,
                "maxrss_mb": maxrss_kb / 1024,
                "loader": picked,
            }
        )
    )


def peak_rss_kb():
    try:
        with open("/proc/self/status") as fp:
            for line in fp:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


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


def compare(path, modes, rounds):
    "Run every mode once per round, rotating the order, and summarize"
    results = {mode: [] for mode in modes}
    for round_number in range(rounds):
        shift = round_number % len(modes)
        for mode in modes[shift:] + modes[:shift]:
            results[mode].append(run(mode, path))
    name = os.path.basename(path)
    for mode in modes:
        runs = results[mode]
        if "error" in runs[0]:
            row([name, mode, "ERROR " + runs[0]["error"], "", "", "", "", ""])
            continue
        seconds = sorted(r["seconds"] for r in runs)
        median = statistics.median(seconds)
        rate = runs[0]["rows"] / median if median and runs[0]["rows"] else 0
        row(
            [
                name,
                mode,
                runs[0]["loader"],
                runs[0]["rows"],
                f"{median:.3f}",
                f"{seconds[0]:.3f}-{seconds[-1]:.3f}",
                f"{rate:,.0f}",
                f"{max(r['maxrss_mb'] for r in runs):.0f}",
            ]
        )


def make_codec_files(work_dir, count=500_000):
    os.makedirs(work_dir, exist_ok=True)
    paths = []
    for encoding in ("utf-8", "cp1252"):
        path = os.path.join(work_dir, f"accents-{encoding.replace('-', '')}.csv")
        with open(path, "w", encoding=encoding, newline="") as fp:
            fp.write("id,name,city\n")
            fp.writelines(
                f"{i},José Müller née Ångström,Kraków São Paulo\n" for i in range(count)
            )
        paths.append(path)
    return paths


def main(data_dir, work_dir, big):
    print("## Worker start-up cost (median of 7 runs)\n")
    row(["python -c ...", "seconds"])
    row(["---", "---"])
    for code in (
        "pass",
        "import sqlite_utils.utils",
        "import datasette.imports.loaders as L; L.get_loaders()",
    ):
        row([f"`{code}`", f"{startup_seconds(code):.3f}"])

    print(
        "\n## Rows from each loader (fresh process per run, rows counted only, "
        "modes interleaved)\n"
    )
    row(
        [
            "file",
            "mode",
            "picked",
            "rows",
            "median s",
            "min-max s",
            "rows/s",
            "peak RSS MB",
        ]
    )
    row(["---"] * 8)
    data = lambda name: os.path.join(data_dir, name)
    compare(
        data("events-100k.csv"),
        ["load_path", "rows_from_file CSV utf-8-sig", "sqlite-utils auto"],
        5,
    )
    compare(
        data("events-100k.tsv"),
        ["load_path", "rows_from_file TSV utf-8-sig", "sqlite-utils auto"],
        5,
    )
    compare(
        data("events-100k.json"),
        ["load_path", "rows_from_file JSON utf-8-sig", "sqlite-utils auto"],
        5,
    )
    compare(
        data("events-100k.ndjson"),
        ["load_path", "rows_from_file NL utf-8-sig", "sqlite-utils auto"],
        5,
    )
    compare(data("places.geojson"), ["load_path+geojson"], 3)
    compare(data("places-type-last.json"), ["load_path+geojson"], 3)
    compare(data("big-1gb.csv"), ["sniff_path"], 3)

    print("\n## Text codec cost: CSV parsing with each encoding\n")
    row(
        [
            "file",
            "mode",
            "picked",
            "rows",
            "median s",
            "min-max s",
            "rows/s",
            "peak RSS MB",
        ]
    )
    row(["---"] * 8)
    utf8, cp1252 = make_codec_files(work_dir)
    compare(
        utf8,
        [
            "rows_from_file CSV utf-8-sig",
            "rows_from_file CSV datasette-utf8",
            "load_path",
        ],
        5,
    )
    compare(
        cp1252,
        [
            "rows_from_file CSV cp1252",
            "rows_from_file CSV datasette-utf8",
            "load_path",
        ],
        5,
    )
    if big:
        compare(
            data("big-1gb.csv"),
            [
                "rows_from_file CSV utf-8-sig",
                "rows_from_file CSV datasette-utf8",
                "load_path",
            ],
            3,
        )


if __name__ == "__main__":
    if sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3])
    else:
        main(sys.argv[1], sys.argv[2], "--big" in sys.argv)
