"""
How long does the import worker take to start, and where does that go?

1. Wall time from spawning ``python -P -m datasette.imports.worker`` on a
   tiny CSV file to its header line and to its exit, against a bare
   ``python -c pass``, and for the stand-ins in worker_startup_floor.py
   (what a lazy ``datasette/__init__.py`` and a loaders-only plugin manager
   would save).
2. ``python -X importtime``: the biggest cumulative imports.

    uv run python prototypes/data_csv/bench_worker_startup.py --work /tmp/work
"""

import argparse
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def time_command(argv, repeat):
    to_header, to_exit = [], []
    for _ in range(repeat):
        start = time.perf_counter()
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        first = process.stdout.readline()
        header_at = time.perf_counter()
        process.stdout.read()
        process.wait()
        end = time.perf_counter()
        assert process.returncode == 0, argv
        if first:
            to_header.append(header_at - start)
        to_exit.append(end - start)
    return to_header, to_exit


def fmt(values):
    if not values:
        return "-"
    return f"min {min(values) * 1000:5.0f} ms, median {statistics.median(values) * 1000:5.0f} ms"


def importtime(argv):
    output = subprocess.run(
        [argv[0], "-X", "importtime", *argv[1:]],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    rows = []
    for line in output.splitlines():
        match = re.match(r"import time:\s+(\d+) \|\s+(\d+) \| (\s*)(\S+)", line)
        if match:
            rows.append((int(match.group(2)), len(match.group(3)) // 2, match.group(4)))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True)
    parser.add_argument("--repeat", type=int, default=10)
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    tiny = os.path.join(args.work, "tiny.csv")
    with open(tiny, "w") as fp:
        fp.write("id,name\n1,a\n2,b\n")
    python = sys.executable
    floor = os.path.join(HERE, "worker_startup_floor.py")
    commands = {
        "python -c pass": [python, "-c", "pass"],
        "worker (as shipped)": [python, "-P", "-m", "datasette.imports.worker", tiny],
        "stub datasette/__init__ + loaders-only pm": [python, floor, tiny],
        "... and no sqlite_utils import": [python, floor, "--no-sqlite-utils", tiny],
        "... and no entry point scan": [
            python,
            floor,
            "--no-sqlite-utils",
            "--no-entrypoints",
            tiny,
        ],
    }
    print(f"## Spawn to header / to exit, tiny CSV, {args.repeat} runs each")
    for name, argv in commands.items():
        to_header, to_exit = time_command(argv, args.repeat)
        print(f"  {name:45s} header: {fmt(to_header):32s} exit: {fmt(to_exit)}")
    print("\n## python -X importtime -m datasette.imports.worker tiny.csv")
    rows = importtime([python, "-P", "-m", "datasette.imports.worker", tiny])
    total = sum(us for us, depth, name in rows if depth == 0)
    print(f"  total of top-level imports: {total / 1000:.0f} ms")
    interesting = [
        "datasette",
        "datasette.imports.loaders",
        "datasette.plugins",
        "sqlite_utils",
        "sqlite_utils.utils",
        "datasette.utils",
        "datasette.utils.asgi",
        "datasette.database",
        "datasette.views",
        "datasette.app",
        "pluggy",
        "importlib.metadata",
        "asyncio",
        "datasette.events",
        "datasette.permissions",
        "datasette.background_tasks",
        "datasette.facets",
        "datasette.filters",
        "datasette.publish.heroku",
        "datasette.default_permissions",
        "datasette.telemetry",
        "opentelemetry.trace",
        "click",
        "yaml",
    ]
    by_name = {}
    for us, depth, name in rows:
        by_name.setdefault(name, us)
    for name in interesting:
        if name in by_name:
            print(f"  {name:32s} {by_name[name] / 1000:6.1f} ms (cumulative)")
        else:
            print(f"  {name:32s}      - (not imported)")


if __name__ == "__main__":
    main()
