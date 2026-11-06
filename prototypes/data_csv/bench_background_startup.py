"""
How long until ``datasette ...`` answers its first request: an empty
Datasette against the same command with data files to import in the
background. Runs each command ``--repeat`` times, interleaved, and reports
the time from starting the process to the first 200 from ``/``.

    uv run python prototypes/data_csv/bench_background_startup.py \\
        --repeat 5 -- "" DATA/big-1gb.csv "DATA/big-1gb.csv DATA/events-100k.csv"

Each argument after ``--`` is one command line (split on spaces).
"""

import argparse
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def first_response(args, workdir):
    tmpdir = tempfile.mkdtemp(prefix="startup-", dir=workdir)
    port = free_port()
    env = dict(os.environ, TMPDIR=tmpdir, DATASETTE_SECRET="not_a_secret")
    start = time.perf_counter()
    process = subprocess.Popen(
        [sys.executable, "-m", "datasette", *args, "-p", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        while True:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/", timeout=2
                ) as r:
                    if r.status == 200:
                        return time.perf_counter() - start
            except OSError:
                if process.poll() is not None:
                    raise SystemExit(f"exited: {args}") from None
                time.sleep(0.005)
    finally:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--workdir")
    parser.add_argument("commands", nargs="+")
    args = parser.parse_args()
    times = {command: [] for command in args.commands}
    for _ in range(args.repeat):
        for command in args.commands:
            times[command].append(first_response(command.split(), args.workdir))
    for command, values in times.items():
        label = command or "(empty)"
        print(
            f"{label}: median {statistics.median(values):.3f}s, "
            f"min {min(values):.3f}s, max {max(values):.3f}s "
            f"({', '.join(f'{v:.3f}' for v in values)})"
        )


if __name__ == "__main__":
    main()
