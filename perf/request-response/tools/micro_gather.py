"""db.execute() run one at a time vs. 10 at a time with asyncio.gather."""

import asyncio
import os
import statistics
import sys
import time

from datasette.app import Datasette

HERE = os.path.dirname(os.path.abspath(__file__))  # run bench_asgi.py once first
QUERIES = {
    "1 row": "select 1",
    "100 text rows": "select pk1, pk2, pk3, content from compound_three_primary_keys limit 100",
    "introspection": "select name, type, sql from sqlite_master",
}


async def main():
    ds = Datasette(
        [os.path.join(HERE, "fixtures.db")],
        settings={"num_sql_threads": int(os.environ.get("SQL_THREADS", "3"))},
    )
    await ds.invoke_startup()
    db = ds.get_database("fixtures")
    gil = getattr(sys, "_is_gil_enabled", lambda: True)()
    print(
        f"GIL {'on' if gil else 'off'}, num_sql_threads={os.environ.get('SQL_THREADS', '3')}"
    )
    for label, sql in QUERIES.items():
        for _ in range(50):
            await db.execute(sql)
        res = {}
        for mode in ("sequential", "gather10"):
            walls, cpus = [], []
            for _ in range(5):
                c0, t0 = time.process_time(), time.perf_counter()
                if mode == "sequential":
                    for _ in range(300):
                        await db.execute(sql)
                else:
                    for _ in range(30):
                        await asyncio.gather(*(db.execute(sql) for _ in range(10)))
                walls.append((time.perf_counter() - t0) / 300 * 1e6)
                cpus.append((time.process_time() - c0) / 300 * 1e6)
            res[mode] = (statistics.median(walls), statistics.median(cpus))
        s, g = res["sequential"], res["gather10"]
        print(
            f"  {label:14s} sequential wall {s[0]:6.0f}us cpu {s[1]:6.0f}us | gather10 wall {g[0]:6.0f}us cpu {g[1]:6.0f}us  (cpu x{g[1] / s[1]:.2f})"
        )


asyncio.run(main())
