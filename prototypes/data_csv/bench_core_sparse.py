"""
Conversion slices on a backlog that starts with rows holding nothing to
convert: column x is missing (NULL) for the first SPARSE rows - a key that
turns up late in JSON - then holds integers, and a text value at 97% widens
it to text. Every row before that has to be looked at, but only the ones
holding a number rewritten.

Slices sized from the measured speed (the reviewed version of proto/core)
grew while they were skipping NULLs, until one reached the integers and
rewrote far too many rows in one transaction. Slices sized for rewriting
every row (now) stay small over the NULLs.

Drives TypedInserter on a WAL file, one BEGIN/COMMIT per write() and per
convert(), timing each transaction including its COMMIT. Variants are run
interleaved; each is an inserter.py file.

    git show 8aed739:datasette/imports/inserter.py > /tmp/inserter_reviewed.py
    uv run python prototypes/data_csv/bench_core_sparse.py \\
        --variant reviewed=/tmp/inserter_reviewed.py \\
        --variant now=datasette/imports/inserter.py \\
        --rows 6000000 --sparse 4500000 --work /tmp/work --repeat 2
"""

import argparse
import importlib.util
import json
import os
import sqlite3
import time


def load(name, path):
    spec = importlib.util.spec_from_file_location(f"inserter_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def remove(path):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


def run(name, module, args):
    path = os.path.join(args.work, f"sparse-{name}-{os.getpid()}.db")
    remove(path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    inserter = module.TypedInserter("t")
    text_at = int(args.rows * 0.97)
    pad = "x" * args.pad
    transactions = []
    slices = []

    def transaction(kind, fn):
        began = time.perf_counter()
        backlog = getattr(inserter, "backlog", 0)
        conn.execute("BEGIN IMMEDIATE")
        fn()
        committing = time.perf_counter()
        conn.execute("COMMIT")
        ended = time.perf_counter()
        transactions.append(
            (ended - began, committing - began, kind, len(transactions), backlog)
        )
        if backlog:
            slices.append(backlog - inserter.backlog)

    start = time.perf_counter()
    position = 0
    while position < args.rows:
        end = min(args.rows, position + 1000)
        chunk = []
        for i in range(position, end):
            row = {"id": str(i), "name": f"name {i}", "pad": pad}
            if i >= args.sparse:
                row["x"] = "late text" if i == text_at else str(i * 7)
            chunk.append(row)
        transaction("write", lambda chunk=chunk: inserter.write(conn, chunk))
        position = end
    while inserter.backlog:
        transaction("convert", lambda: inserter.convert(conn))
    transaction("finish", lambda: inserter.finish(conn))
    total = time.perf_counter() - start
    types = conn.execute("select typeof(x), count(*) from t group by 1").fetchall()
    check = conn.execute("PRAGMA quick_check").fetchone()[0]
    conn.close()
    remove(path)
    times = sorted((t[0] for t in transactions), reverse=True)
    top = sorted(transactions, reverse=True)[:4]
    return {
        "variant": name,
        "rows": args.rows,
        "sparse": args.sparse,
        "pad": args.pad,
        "total_seconds": round(total, 2),
        "transactions": len(transactions),
        "longest_transactions_ms": [round(t * 1000, 1) for t in times[:4]],
        # (total ms, of which before COMMIT, kind, number, backlog before)
        "longest_transactions": [
            (round(t * 1000, 1), round(f * 1000, 1), kind, n, b)
            for t, f, kind, n, b in top
        ],
        "over_100ms": sum(1 for t in times if t > 0.1),
        "over_200ms": sum(1 for t in times if t > 0.2),
        "convert_seconds": round(inserter.convert_seconds, 2),
        "slices": len(slices),
        "largest_slice_rows": max(slices) if slices else 0,
        "types": sorted(types, key=str),
        "quick_check": check,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", action="append", required=True)
    parser.add_argument("--rows", type=int, default=6_000_000)
    parser.add_argument("--sparse", type=int, default=4_500_000)
    parser.add_argument("--pad", type=int, default=40)
    parser.add_argument("--work", required=True)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    variants = []
    for spec in args.variant:
        name, path = spec.split("=", 1)
        variants.append((name, load(name, path)))
    for repeat in range(args.repeat):
        for name, module in variants:
            result = run(name, module, args)
            result["repeat"] = repeat
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
