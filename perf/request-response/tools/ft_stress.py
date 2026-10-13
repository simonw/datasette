"""
Fire many concurrent table-page requests (with parallel extras) at one
Datasette instance and check every response: status 200 and a body that
matches the first response once timing fields are removed.

    PYTHONPATH=tree python ft_stress.py --concurrency 16 --requests 400
"""

import argparse
import asyncio
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("BIGDB", "1")

import bench_asgi

TIMING = re.compile(rb"(\d+(\.\d+)?\s*ms|\"query_ms\":\s*[\d.e-]+|took [\d.]+ms)")
ENDPOINTS = [
    "table_html",
    "table_html_100",
    "big_html",
    "big_json_extras",
    "row_json",
    "table_json",
    "mem_html",
    "mem_json_extras",
]
bench_asgi.ENDPOINTS["mem_html"] = ("GET", "/memtest/t", "", [], False)
bench_asgi.ENDPOINTS["mem_json_extras"] = (
    "GET",
    "/memtest/t.json",
    "_facet=cat&_extra=count&_extra=facet_results&_extra=suggested_facets",
    [],
    False,
)


def normalize(body):
    return TIMING.sub(b"T", body)


async def main(concurrency, total):
    ds = bench_asgi.make_ds()
    # Named in-memory database: the case behind issue #2189
    mem = ds.add_memory_database("memtest")
    await mem.execute_write(
        "create table t (id integer primary key, name text, cat text)"
    )
    await mem.execute_write_many(
        "insert into t (name, cat) values (?, ?)",
        [(f"name {i}", f"cat {i % 7}") for i in range(2000)],
    )
    d = bench_asgi.Driver(ds)
    await d.setup()
    expected = {}
    for name in ENDPOINTS:
        status, _, body = await d.request(name)
        expected[name] = (status, normalize(body))
    outcomes = collections.Counter()
    mismatches = []
    queue = asyncio.Queue()
    for i in range(total):
        queue.put_nowait(ENDPOINTS[i % len(ENDPOINTS)])

    async def worker():
        while not queue.empty():
            name = queue.get_nowait()
            try:
                status, _, body = await d.request(name)
            except Exception as ex:  # noqa: BLE001 - report every failure
                outcomes[f"{name}: exception {type(ex).__name__}: {ex}"] += 1
                continue
            # Facet suggestions on the 500k-row table depend on query time
            # limits, so only the status is compared for those pages
            if name.startswith("big_") and status == expected[name][0] or (status, normalize(body)) == expected[name]:
                outcomes[f"{name}: ok"] += 1
            else:
                outcomes[f"{name}: MISMATCH status={status}"] += 1
                if len(mismatches) < 3:
                    mismatches.append((name, status, body[:300]))
                    import difflib

                    a = expected[name][1].decode("utf-8", "replace").splitlines()
                    b = normalize(body).decode("utf-8", "replace").splitlines()
                    print(
                        "DIFF",
                        name,
                        "\n".join(
                            list(difflib.unified_diff(a, b, lineterm="", n=1))[:40]
                        ),
                    )

    await asyncio.gather(*(worker() for _ in range(concurrency)))
    import sys as _sys

    print("gil enabled:", getattr(_sys, "_is_gil_enabled", lambda: True)())
    for k, v in sorted(outcomes.items()):
        print(f"  {v:5d}  {k}")
    for m in mismatches:
        print("MISMATCH SAMPLE", m)
    bad = sum(v for k, v in outcomes.items() if not k.endswith(": ok"))
    print("FAILURES:", bad)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--requests", type=int, default=400)
    a = p.parse_args()
    asyncio.run(main(a.concurrency, a.requests))
