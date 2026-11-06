"""
Browse and query Datasette while its command-line inputs import in the
background, and see what breaks.

Starts ``datasette FILES... -p PORT`` (with TMPDIR pointing at a directory of
its own), then runs a load generator against the real server until the
imports finish and for ``--after`` seconds more: several clients, each
requesting the homepage, the scratch database page, the table page, its
JSON, a facet, a sort, a filter, count(*) and a group-by through
/scratch/-/query.json, and /-/imports.json, over and over. Reports, per
endpoint, status counts (the text of every non-200), and latency p50, p95
and max - during the import and after it. Then sends SIGINT (Ctrl+C) and
reports how long the server took to stop, whether any import worker is left
and whether anything is left in TMPDIR.

    uv run python prototypes/data_csv/bench_background_load.py \\
        --table big_1gb DATA/big-1gb.csv DATA/events-100k.csv

    # Cancel an import from the UI's API once it has this many rows
    uv run python prototypes/data_csv/bench_background_load.py \\
        --table big_1gb --cancel-at 2000000 --root DATA/big-1gb.csv

``--interrupt-at ROWS`` sends SIGINT part way through instead.
``--json OUT`` writes the raw numbers. ``--database NAME`` with
``--extra=--save --extra=FILE`` queries a --save file instead of scratch;
``--clients N --think SECONDS`` sets the load.

Results (4 CPUs shared with another agent's tests, load average 1-4):

- First response 0.50-0.53s with a 1GB CSV importing, the same as an empty
  Datasette (0.47-0.55s, bench_background_startup.py).
- big-1gb.csv (6.4M rows): 50.7-56.8s with no load, 59.8s with one client
  making a request about every 0.5s, 199s with four clients requesting
  without pause (243s before the worker's pipe was grown to 1MB). Loading
  before serving (--get) takes 41.2s.
- No request failed because of an import: no "no such table", no schema
  errors, not during a late integer to text rebuild of a 4M row table, not
  while 100 columns were added one at a time. The failures seen are
  Datasette's own: ?_sort_desc on an unindexed column of millions of rows
  answers 500 (QueryInterrupted not caught by the table view) when it hits
  the time limit, during the import and after it alike; a group-by hits the
  1s time limit (400). A table page requested before the first chunk is
  committed is a 404 that says the table is queued or importing.
- Under four clients, latency during the import against after it (p50):
  homepage 117 vs 61ms, table page 399 vs 194ms, table JSON 62 vs 31ms,
  /-/imports.json 29 vs 13ms; max 0.7-1.9s during.
- Cancel through the API: 0.13s, rows kept 2,004,000, no worker left.
  SIGINT part way: 0.5-1.3s to stop, no worker, nothing left in TMPDIR.
"""

# Blocking calls in setup and teardown, outside the measured requests, are fine
# ruff: noqa: ASYNC220, ASYNC230, ASYNC251
import argparse
import asyncio
import collections
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse

import httpx2


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def endpoints(table, city, database="scratch"):
    q = urllib.parse.quote
    count = f"select count(*) from [{table}]"
    group = f"select city, count(*) from [{table}] group by city order by 2 desc"
    return {
        "home": "/",
        "database": f"/{database}",
        "table": f"/{database}/{table}",
        "table.json": f"/{database}/{table}.json?_size=100",
        "facet": f"/{database}/{table}.json?_facet=city&_size=0",
        "sort": f"/{database}/{table}.json?_sort_desc=price&_size=10",
        "filter": f"/{database}/{table}.json?city={q(city)}&_size=10",
        "count": f"/{database}/-/query.json?sql={q(count)}",
        "group-by": f"/{database}/-/query.json?sql={q(group)}",
        "imports.json": "/-/imports.json",
    }


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


class State:
    def __init__(self):
        self.importing = True
        self.table_exists = False
        self.rows = 0
        self.done_at = None
        self.timeline = []
        self.imports = []


def _worker_pids(server_pid):
    # Children of the server running the import worker
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,ppid,args"], check=False, capture_output=True, text=True
        ).stdout
    except OSError:
        return []
    pids = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) == 3 and "datasette.imports.worker" in parts[2]:
            pids.append((int(parts[0]), int(parts[1])))
    return pids


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="+")
    parser.add_argument("--table", required=True, help="Table to query")
    parser.add_argument("--database", default="scratch", help="Its database")
    parser.add_argument("--city", default="Lima", help="Value for the filter")
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument(
        "--think", type=float, default=0, help="Seconds between requests"
    )
    parser.add_argument("--after", type=float, default=15.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--cancel-at", type=int, help="Cancel at this many rows")
    parser.add_argument("--interrupt-at", type=int, help="SIGINT at this many rows")
    parser.add_argument("--root", action="store_true", help="Start with --root")
    parser.add_argument("--extra", action="append", default=[], help="Extra arg")
    parser.add_argument("--json", help="Write the raw results here")
    parser.add_argument("--no-load", action="store_true", help="Only poll imports")
    parser.add_argument("--workdir", help="Where to put TMPDIR and the log")
    parser.add_argument("--switch-interval", type=float, help="sys.setswitchinterval")
    parser.add_argument(
        "--small-pipe",
        dest="pipe_size",
        action="store_false",
        help="Leave the worker's stdout pipe at 64KB (PIPE_SIZE = None)",
    )
    parser.add_argument(
        "--prelude", action="append", default=[], help="Python to run first"
    )
    args = parser.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="bench-background-", dir=args.workdir)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, TMPDIR=tmpdir, DATASETTE_SECRET="not_a_secret")
    log_path = os.path.join(tmpdir, "..", f"bench-background-{port}.log")
    log = open(log_path, "wb")  # noqa: SIM115 - closed at the end
    preludes = list(args.prelude)
    if args.switch_interval:
        # The same server with a different GIL switch interval (default 5ms)
        preludes.append(f"import sys; sys.setswitchinterval({args.switch_interval})")
    if not args.pipe_size:
        # The worker's stdout pipe at the system default (64KB on Linux)
        preludes.append(
            "import datasette.imports.subprocess_import as s; s.PIPE_SIZE = None"
        )
    if preludes:
        prelude = "; ".join([*preludes, "from datasette.cli import cli; cli()"])
        command = [sys.executable, "-c", prelude, *args.files, "-p", str(port)]
    else:
        command = [sys.executable, "-m", "datasette", *args.files, "-p", str(port)]
    command += args.extra
    if args.root:
        command.append("--root")
    started = time.perf_counter()
    # Both streams to the file: uvicorn's access log goes to stdout, and a
    # pipe nobody reads fills up and blocks the server's event loop
    process = subprocess.Popen(
        command, stdout=log, stderr=log, env=env, stdin=subprocess.DEVNULL
    )
    paths = endpoints(args.table, args.city, args.database)
    results = collections.defaultdict(list)  # (phase, name) -> [(status, ms)]
    errors = collections.defaultdict(collections.Counter)
    state = State()
    first_response = None
    token_url = None

    async with httpx2.AsyncClient(base_url=base, timeout=60) as client:
        # Time to first response
        while True:
            try:
                response = await client.get("/", timeout=1)
                first_response = time.perf_counter() - started
                break
            except httpx2.HTTPError:
                if process.poll() is not None:
                    raise SystemExit(f"datasette exited: see {log_path}")
                await asyncio.sleep(0.01)
        print(f"First response {response.status_code} after {first_response:.3f}s")
        print(f"Load average at start: {os.getloadavg()}")
        cookies = {}
        if args.root:
            # --root prints a URL with a token on stdout
            with open(log_path) as fp:
                token_url = re.search(r"http://\S+/-/auth-token\?token=\S+", fp.read())
            token_url = token_url.group(0)
            path = urllib.parse.urlsplit(token_url)
            r = await client.get(f"{path.path}?{path.query}")
            cookies = dict(r.cookies)

        stop = asyncio.Event()
        cancel_result = {}

        async def poll_imports():
            while not stop.is_set():
                try:
                    r = await client.get("/-/imports.json")
                    data = r.json()
                except (httpx2.HTTPError, ValueError):
                    await asyncio.sleep(0.5)
                    continue
                now = time.perf_counter() - started
                jobs = data["imports"]
                state.imports = jobs
                big = [j for j in jobs if j["table"] == args.table]
                if big:
                    state.rows = big[0]["rows"]
                    state.table_exists = big[0]["table_exists"]
                state.timeline.append(
                    (round(now, 2), [(j["table"], j["state"], j["rows"]) for j in jobs])
                )
                if data["active"] == 0 and state.importing:
                    state.importing = False
                    state.done_at = now
                if (
                    args.cancel_at
                    and not cancel_result
                    and state.rows >= args.cancel_at
                ):
                    t0 = time.perf_counter()
                    r = await client.post(
                        f"/{args.database}/{args.table}/-/cancel-import",
                        json={},
                        cookies=cookies,
                    )
                    cancel_result.update(
                        status=r.status_code,
                        seconds=time.perf_counter() - t0,
                        body=r.json(),
                        at_rows=state.rows,
                    )
                    print(
                        f"Cancel: {r.status_code} in {cancel_result['seconds']:.3f}s,"
                        f" rows kept {r.json().get('import', {}).get('rows_kept')}"
                    )
                if args.interrupt_at and state.rows >= args.interrupt_at:
                    stop.set()
                    return
                if state.done_at is not None and now - state.done_at >= args.after:
                    stop.set()
                    return
                if now > args.timeout:
                    stop.set()
                    return
                await asyncio.sleep(0.25)

        async def load(i):
            names = list(paths)
            n = i
            while not stop.is_set():
                name = names[n % len(names)]
                n += 1
                phase = "during" if state.importing else "after"
                if not state.table_exists and name not in (
                    "home",
                    "database",
                    "imports.json",
                ):
                    # Before the table exists, these are a 404 by design
                    phase = "before-table"
                t0 = time.perf_counter()
                try:
                    r = await client.get(paths[name])
                    status = r.status_code
                    if status != 200:
                        text = r.text[:300]
                        match = re.search(r'"error":\s*"([^"]*)"', text)
                        errors[(phase, name)][
                            f"{status}: {match.group(1) if match else text[:120]}"
                        ] += 1
                except httpx2.HTTPError as ex:
                    status = type(ex).__name__
                    errors[(phase, name)][status] += 1
                ms = (time.perf_counter() - t0) * 1000
                results[(phase, name)].append((status, ms))
                if args.think:
                    await asyncio.sleep(args.think)

        tasks = [asyncio.create_task(poll_imports())]
        if not args.no_load:
            tasks += [asyncio.create_task(load(i)) for i in range(args.clients)]
        await asyncio.gather(*tasks)

    # Stop the server the way Ctrl+C does
    workers_before = _worker_pids(process.pid)
    t0 = time.perf_counter()
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    shutdown = time.perf_counter() - t0
    log.close()
    time.sleep(0.2)
    left_workers = [pid for pid, ppid in _worker_pids(process.pid)]
    left_files = sorted(os.listdir(tmpdir))
    with open(log_path) as fp:
        stderr = fp.read()

    print(f"\nServer log ({log_path}):")
    keep = re.compile(
        r"^(INFO|  ?[a-z]|Importing|Loaded|Failed|Cancelled|Stopped|Queued|"
        r"Downloading|Reading|Imported|Aborted)"
    )
    tracebacks = stderr.count("Traceback (most recent call last)")
    for line in stderr.splitlines():
        if "GET /" in line or "POST /" in line or not keep.match(line):
            continue
        print("  " + line)
    print(f"  ({tracebacks} tracebacks in the log)")
    print(
        f"\nImports finished {state.done_at:.1f}s after start"
        if state.done_at
        else "\nImports did not finish"
    )
    print(f"Shutdown after SIGINT: {shutdown:.2f}s, exit code {process.returncode}")
    print(f"Load average at end: {os.getloadavg()}")
    print(f"Import workers running before SIGINT: {len(workers_before)}")
    print(f"Import workers left after: {left_workers}")
    print(f"Left in TMPDIR: {left_files}")
    print()
    header = (
        f"{'phase':<13}{'endpoint':<14}{'n':>6}{'p50':>9}{'p95':>9}{'max':>9}  status"
    )
    print(header)
    summary = {}
    for phase in ("before-table", "during", "after"):
        for name in paths:
            values = results.get((phase, name))
            if not values:
                continue
            ms = [v for _, v in values]
            statuses = collections.Counter(str(s) for s, _ in values)
            row = {
                "n": len(values),
                "p50": percentile(ms, 50),
                "p95": percentile(ms, 95),
                "max": max(ms),
                "statuses": dict(statuses),
                "errors": dict(errors.get((phase, name), {})),
            }
            summary[f"{phase} {name}"] = row
            print(
                f"{phase:<13}{name:<14}{row['n']:>6}{row['p50']:>9.1f}"
                f"{row['p95']:>9.1f}{row['max']:>9.1f}  {dict(statuses)}"
            )
    any_errors = {k: v for k, v in errors.items() if v}
    if any_errors:
        print("\nNon-200 responses:")
        for (phase, name), counter in sorted(any_errors.items()):
            for text, count in counter.most_common():
                print(f"  {phase} {name}: {count} x {text}")
    if args.json:
        with open(args.json, "w") as fp:
            json.dump(
                {
                    "command": command,
                    "first_response": first_response,
                    "done_at": state.done_at,
                    "shutdown": shutdown,
                    "left_workers": left_workers,
                    "left_files": left_files,
                    "summary": summary,
                    "timeline": state.timeline,
                    "imports": state.imports,
                    "cancel": cancel_result if args.cancel_at else None,
                    "stderr": stderr,
                },
                fp,
                indent=1,
                default=str,
            )
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
