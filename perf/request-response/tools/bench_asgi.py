"""
Drive Datasette's ASGI app in-process (no HTTP server, no httpx) so that
profiles show only Datasette's request/response machinery.

Run it with the source tree you want to measure first on PYTHONPATH, e.g.
    PYTHONPATH=/path/to/datasette python bench_asgi.py time --n 1000
fixtures.db is generated next to this script on first run. Set IMMUTABLE=1
to open it as an immutable database, NUM_SQL_THREADS to change the pool.

Usage:
    python bench_asgi.py time [--endpoints a,b] [--n 2000] [--rounds 5]
    python bench_asgi.py loop ENDPOINT --seconds 10      # for profiling.sampling
    python bench_asgi.py client --n 300                   # datasette.client path
"""

import argparse
import asyncio
import json
import os
import pathlib
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures.db")

from datasette.app import Datasette

if not os.path.exists(FIXTURES):
    from datasette.fixtures import write_fixture_database

    write_fixture_database(FIXTURES)

ENDPOINTS = {
    # name: (method, path, query_string, extra headers, needs actor cookie)
    "versions_json": ("GET", "/-/versions.json", "", [], False),
    "versions_html": ("GET", "/-/versions", "", [], False),
    "actor_json_cookie": ("GET", "/-/actor.json", "", [], True),
    "static_css": ("GET", "/-/static/app.css", "", [], False),
    "static_css_304": ("GET", "/-/static/app.css", "", "ETAG", False),
    "favicon": ("GET", "/favicon.ico", "", [], False),
    "redirect_dash": ("GET", "/-", "", [], False),
    "trailing_slash": ("GET", "/fixtures/", "", [], False),
    "not_found": ("GET", "/fixtures/nope/nope/nope/nope", "", [], False),
    "index_html": ("GET", "/", "", [], False),
    "index_json": ("GET", "/.json", "", [], False),
    "database_html": ("GET", "/fixtures", "", [], False),
    "table_json": (
        "GET",
        "/fixtures/facetable.json",
        "_size=5&_nofacet=1&_nocount=1",
        [],
        False,
    ),
    "table_html": ("GET", "/fixtures/facetable", "_size=5", [], False),
    "row_json": ("GET", "/fixtures/facetable/1.json", "", [], False),
    "post_csrf_json": ("POST", "/-/actor.json", "", "POSTHDRS", False),
    "static_css_hash": ("GET", "/-/static/app.css", "HASH", [], False),
    "table_html_100": (
        "GET",
        "/fixtures/compound_three_primary_keys",
        "_size=100",
        [],
        False,
    ),
    "table_csv_1000": (
        "GET",
        "/fixtures/compound_three_primary_keys.csv",
        "_size=1000",
        [],
        False,
    ),
}

BROWSER_HEADERS = [
    (b"host", b"localhost:8001"),
    (b"user-agent", b"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130"),
    (b"accept", b"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    (b"accept-encoding", b"gzip, deflate, br"),
    (b"accept-language", b"en-US,en;q=0.9"),
    (b"connection", b"keep-alive"),
    (b"sec-fetch-site", b"none"),
    (b"sec-fetch-mode", b"navigate"),
]


def make_ds():
    return Datasette(
        [] if os.environ.get("IMMUTABLE") else [FIXTURES],
        immutables=[FIXTURES] if os.environ.get("IMMUTABLE") else [],
        settings={"num_sql_threads": int(os.environ.get("NUM_SQL_THREADS", "3"))},
    )


class Driver:
    def __init__(self, ds):
        self.ds = ds
        self.app = ds.app()
        self.cookie = None
        self.etag = None

    async def setup(self):
        await self.ds.invoke_startup()
        await self.ds.refresh_schemas()
        self.cookie = (
            b"ds_actor=" + self.ds.sign({"a": {"id": "root"}}, "actor").encode()
        )
        # Discover static ETag
        _, headers, _ = await self.request("static_css")
        self.etag = {bytes(k).lower(): v for k, v in headers}.get(b"etag")
        import hashlib

        import datasette as _d

        css = os.path.join(os.path.dirname(_d.__file__), "static", "app.css")
        self.css_hash = hashlib.sha256(pathlib.Path(css).read_bytes()).hexdigest()[:12]

    def scope_for(self, name):
        method, path, qs, extra, cookie = ENDPOINTS[name]
        headers = list(BROWSER_HEADERS)
        if extra == "ETAG":
            headers.append((b"if-none-match", self.etag or b""))
        elif extra == "POSTHDRS":
            headers.append((b"content-type", b"application/json"))
            headers.append((b"content-length", b"0"))
        if cookie:
            headers.append((b"cookie", self.cookie))
        if qs == "HASH":
            qs = "_hash=" + self.css_hash
        raw_path = path.encode()
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "server": ("127.0.0.1", 8001),
            "client": ("127.0.0.1", 50000),
            "scheme": "http",
            "method": method,
            "root_path": "",
            "path": path,
            "raw_path": raw_path,
            "query_string": qs.encode(),
            "headers": headers,
            "state": {},
        }

    async def request(self, name):
        scope = self.scope_for(name)
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await self.app(scope, receive, send)
        start = sent[0]
        body = b"".join(m.get("body", b"") for m in sent[1:])
        return start["status"], start["headers"], body


async def run_time(endpoints, n, rounds):
    ds = make_ds()
    d = Driver(ds)
    await d.setup()
    results = {}
    for name in endpoints:
        status, _, body = await d.request(name)
        # warmup
        for _ in range(min(200, n)):
            await d.request(name)
        per_round = []
        for _ in range(rounds):
            t0 = time.perf_counter()
            for _ in range(n):
                await d.request(name)
            per_round.append((time.perf_counter() - t0) / n * 1e6)
        results[name] = {
            "status": status,
            "bytes": len(body),
            "median_us": round(statistics.median(per_round), 1),
            "min_us": round(min(per_round), 1),
        }
        print(
            f"{name:22s} {status} {len(body):7d}B  median {results[name]['median_us']:8.1f}us  min {results[name]['min_us']:8.1f}us",
            flush=True,
        )
    return results


async def run_loop(name, seconds):
    ds = make_ds()
    d = Driver(ds)
    await d.setup()
    for _ in range(200):
        await d.request(name)
    deadline = time.perf_counter() + seconds
    count = 0
    while time.perf_counter() < deadline:
        for _ in range(50):
            await d.request(name)
        count += 50
    print(
        f"{name}: {count} requests in {seconds}s = {count / seconds:.0f} req/s",
        file=sys.stderr,
    )


async def run_client(n, path="/-/versions.json"):
    ds = make_ds()
    await ds.invoke_startup()
    await ds.client.get(path)
    t0 = time.perf_counter()
    for _ in range(n):
        await ds.client.get(path)
    us = (time.perf_counter() - t0) / n * 1e6
    print(f"datasette.client.get({path!r}) {us:.1f}us/request")
    t0 = time.perf_counter()
    for _ in range(n):
        ds.app()
    print(f"ds.app() rebuild alone: {(time.perf_counter() - t0) / n * 1e6:.1f}us")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("time")
    t.add_argument("--endpoints", default=",".join(ENDPOINTS))
    t.add_argument("--n", type=int, default=1000)
    t.add_argument("--rounds", type=int, default=5)
    t.add_argument("--json-out")
    lp = sub.add_parser("loop")
    lp.add_argument("endpoint")
    lp.add_argument("--seconds", type=float, default=10)
    c = sub.add_parser("client")
    c.add_argument("--n", type=int, default=300)
    c.add_argument("--path", default="/-/versions.json")
    args = p.parse_args()
    if args.cmd == "time":
        res = asyncio.run(run_time(args.endpoints.split(","), args.n, args.rounds))
        if args.json_out:
            with open(args.json_out, "w") as fp:
                json.dump(res, fp, indent=2)
    elif args.cmd == "loop":
        asyncio.run(run_loop(args.endpoint, args.seconds))
    elif args.cmd == "client":
        asyncio.run(run_client(args.n, args.path))


if __name__ == "__main__":
    main()
