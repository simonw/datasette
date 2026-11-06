"""
ProtocolDecoder cost: long lines and ordinary rows, current decoder against
an older copy of the module (for example the first version, which re-joined
the partial line on every read and parsed runs of row lines with one
batched json.loads()).

    git show 8117747:datasette/imports/subprocess_import.py > /tmp/old.py
    uv run python -P -m datasette.imports.worker events-100k.csv > /tmp/csv.stream
    uv run python prototypes/data_csv/bench_worker_decoder.py \\
        --old /tmp/old.py --stream /tmp/csv.stream

Long lines are fed in 64KB pieces, which is what a Linux pipe hands over per
read; the stream files are fed in 64KB and 256KB pieces.
"""

import argparse
import importlib.util
import time


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def long_line(module, megabytes, piece):
    value = b"x" * (megabytes * 1024 * 1024)
    data = (
        b'{"type":"header","protocol":1}\n{"type":"columns","add":["a"]}\n["'
        + value
        + b'"]\n'
    )
    decoder = module.ProtocolDecoder()
    start = time.perf_counter()
    longest = 0
    for i in range(0, len(data), piece):
        t = time.perf_counter()
        decoder.feed(data[i : i + piece])
        longest = max(longest, time.perf_counter() - t)
    return time.perf_counter() - start, longest


def stream(module, data, piece):
    best = None
    for _ in range(3):
        decoder = module.ProtocolDecoder()
        rows = 0
        start = time.perf_counter()
        for i in range(0, len(data), piece):
            for kind, payload in decoder.feed(data[i : i + piece]):
                if kind == "rows":
                    rows += len(payload)
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    return best, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", help="An older subprocess_import.py to compare")
    parser.add_argument("--stream", nargs="*", default=[], help="Worker output")
    parser.add_argument("--megabytes", type=int, nargs="+", default=[4, 16, 32])
    args = parser.parse_args()
    from datasette.imports import subprocess_import as new

    modules = [("new", new)]
    if args.old:
        modules.insert(0, ("old", load(args.old, "old_subprocess_import")))
    for megabytes in args.megabytes:
        for name, module in modules:
            total, longest = long_line(module, megabytes, 65536)
            print(
                f"{megabytes:3d}MB line, 64KB reads, {name}: {total:7.3f}s total, "
                f"longest feed() {longest * 1000:7.1f}ms"
            )
    for path in args.stream:
        data = open(path, "rb").read()
        for piece in (65536, 262144):
            for name, module in modules:
                seconds, rows = stream(module, data, piece)
                print(
                    f"{path.rsplit('/', 1)[-1]} {piece // 1024}KB reads, {name}: "
                    f"{seconds * 1e6 / rows:.2f} us/row ({rows} rows, best of 3)"
                )


if __name__ == "__main__":
    main()
