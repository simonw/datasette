"""
How should the import worker encode rows on its stdout?

Compares, on rows produced by the real loaders, per row:

- ``objects``: one JSON object per line (``{"id": "1", "name": ...}``)
- ``arrays``: column names once in a header, then one JSON array per line
  (``["1", ...]``), turned back into dicts with ``dict(zip(columns, row))``
  in the parent

for the worker's side (``json.dumps`` per row, ``ensure_ascii`` on and off)
and the parent's side (``json.loads`` per line, or one ``json.loads`` of a
whole batch of lines joined with commas), plus the bytes per row. Also times
the slow path for rows holding bytes (base64) and checks how NaN, infinity,
lone surrogates and very large integers come through.

    uv run python prototypes/data_csv/bench_worker_encoding.py \\
        --data /path/to/data --rows 100000 --repeat 3
"""

import argparse
import base64
import itertools
import json
import math
import statistics
import time

from datasette.imports.loaders import load_path


def rows_from(path, limit):
    result = load_path(path)
    return result.loader.name, list(itertools.islice(result.rows, limit))


def best(fn, repeat):
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - start)
    return min(times), statistics.median(times), out


def bench(name, rows, repeat):
    columns = list(rows[0])
    n = len(rows)
    dumps = json.dumps
    loads = json.loads
    out = {}

    def enc_objects(ascii_):
        def run():
            return [
                dumps(r, ensure_ascii=ascii_, separators=(",", ":")).encode(
                    "utf-8", "surrogatepass"
                )
                for r in rows
            ]

        return run

    def enc_arrays(ascii_):
        def run():
            return [
                dumps(
                    list(r.values()), ensure_ascii=ascii_, separators=(",", ":")
                ).encode("utf-8", "surrogatepass")
                for r in rows
            ]

        return run

    # Batched encoding: one dumps() call for 1000 rows, then split? Not
    # possible for lines, but encoding a joined str once is cheaper
    def enc_arrays_joined():
        lines = [dumps(list(r.values()), separators=(",", ":")) for r in rows]
        return "\n".join(lines).encode("utf-8", "surrogatepass")

    encoded = {}
    for label, fn in (
        ("objects ascii", enc_objects(True)),
        ("objects utf8", enc_objects(False)),
        ("arrays ascii", enc_arrays(True)),
        ("arrays utf8", enc_arrays(False)),
    ):
        low, _, lines = best(fn, repeat)
        encoded[label] = lines
        size = sum(len(line) + 1 for line in lines)
        out[f"encode {label}"] = (low / n * 1e6, size / n)
    low, _, _ = best(enc_arrays_joined, repeat)
    out["encode arrays ascii, one encode() per batch"] = (low / n * 1e6, None)

    def dec_lines(lines, as_arrays):
        def run():
            if as_arrays:
                return [dict(zip(columns, loads(line))) for line in lines]
            return [loads(line) for line in lines]

        return run

    def dec_batches(lines, as_arrays, size=1000):
        def run():
            result = []
            for start in range(0, len(lines), size):
                batch = loads(b"[" + b",".join(lines[start : start + size]) + b"]")
                if as_arrays:
                    result.extend([dict(zip(columns, values)) for values in batch])
                else:
                    result.extend(batch)
            return result

        return run

    def dec_batches_no_dict(lines, size=1000):
        def run():
            result = []
            for start in range(0, len(lines), size):
                result.extend(
                    loads(b"[" + b",".join(lines[start : start + size]) + b"]")
                )
            return result

        return run

    for label, lines, as_arrays in (
        ("objects ascii", encoded["objects ascii"], False),
        ("arrays ascii", encoded["arrays ascii"], True),
        ("objects utf8", encoded["objects utf8"], False),
        ("arrays utf8", encoded["arrays utf8"], True),
    ):
        low, _, decoded = best(dec_lines(lines, as_arrays), repeat)
        assert decoded == rows, label
        out[f"decode {label}: loads() per line"] = (low / n * 1e6, None)
        low, _, decoded = best(dec_batches(lines, as_arrays), repeat)
        assert decoded == rows, label
        out[f"decode {label}: one loads() per 1000 lines"] = (low / n * 1e6, None)
    low, _, _ = best(dec_batches_no_dict(encoded["arrays ascii"]), repeat)
    out["decode arrays ascii: one loads() per 1000 lines, no dict()"] = (
        low / n * 1e6,
        None,
    )
    print(f"\n## {name}: {n} rows, {len(columns)} columns")
    for label, (us, size) in out.items():
        extra = f", {size:.1f} bytes/row" if size else ""
        print(f"  {label:60s} {us:6.2f} us/row{extra}")


def special_values(repeat):
    print("\n## Special values")
    # bytes: the default= hook sees them; the row then takes the slow path
    blob = bytes(range(256)) * 4
    rows = [{"id": i, "data": blob, "name": "x"} for i in range(20000)]

    def with_default():
        out = []
        for row in rows:
            values = list(row.values())
            positions = [i for i, v in enumerate(values) if type(v) is bytes]
            for i in positions:
                values[i] = base64.b64encode(values[i]).decode("ascii")
            out.append(
                json.dumps(
                    {"type": "row", "values": values, "base64": positions},
                    separators=(",", ":"),
                )
            )
        return out

    low, _, lines = best(with_default, repeat)
    print(
        f"  1KB bytes value, slow-path row message: {low / len(rows) * 1e6:.2f} us/row, "
        f"{sum(map(len, lines)) / len(rows):.0f} bytes/row (raw bytes: {len(blob)})"
    )
    checks = {
        "nan": math.nan,
        "inf": math.inf,
        "-inf": -math.inf,
        "2**63": 2**63,
        "-2**63-1": -(2**63) - 1,
        "2**200": 2**200,
        "lone surrogate": "\ud800x",
        "emoji": "\U0001f600",
        "nested": {"a": [1, {"b": None}], "$base64": True},
    }
    for label, value in checks.items():
        line = json.dumps([value], separators=(",", ":"))
        back = json.loads(line.encode("utf-8", "surrogatepass"))[0]
        same = back == value or (
            isinstance(value, float) and math.isnan(value) and math.isnan(back)
        )
        print(f"  {label:16s} -> {line[:60]!s:62s} round trip: {same}")
    try:
        json.dumps([10**5000])
    except ValueError as ex:
        print(f"  10**5000: json.dumps raises ValueError: {str(ex)[:70]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    for filename in ("events-100k.csv", "events-100k.ndjson"):
        loader, rows = rows_from(f"{args.data}/{filename}", args.rows)
        bench(f"{filename} ({loader} loader)", rows, args.repeat)
    special_values(args.repeat)


if __name__ == "__main__":
    main()
