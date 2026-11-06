"""
Fuzz the claim that an import does not depend on where the sample and batch
boundaries fall: for random rows full of awkward values, every widening
strategy with a random sample size and batch size must produce exactly the
table you get when the whole input is the sample. A bigger run of
test_result_does_not_depend_on_chunk_boundaries.

    uv run python prototypes/data_csv/fuzz_inserter_boundaries.py \\
        --seeds 3000 --max-rows 80 --floats exact
    # as on a SQLite whose float parsing differs from Python's:
    uv run python prototypes/data_csv/fuzz_inserter_boundaries.py --floats python
"""

import argparse
import os
import random
import sys
import time

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "tests")
)
from test_imports_inserter import _snapshot, _tricky_rows, load

from datasette.imports import inserter as inserter_module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1000)
    parser.add_argument("--seeds", type=int, default=3000)
    parser.add_argument("--max-rows", type=int, default=80)
    parser.add_argument("--floats", choices=("exact", "python"), default="exact")
    args = parser.parse_args()
    # "python": bind floats converted in Python instead of float text
    inserter_module._FLOATS_EXACT = args.floats == "exact"
    failures = 0
    start = time.perf_counter()
    for seed in range(args.start, args.start + args.seeds):
        rng = random.Random(seed)
        rows = _tricky_rows(rng, rng.randint(1, args.max_rows))
        reference_conn, _ = load(rows, widen="auto", sample_size=len(rows))
        reference = _snapshot(reference_conn)
        for widen in inserter_module.WIDEN_STRATEGIES:
            sample_size = rng.randint(1, 12)
            batch_size = rng.randint(1, 9)
            try:
                conn, _ = load(
                    rows, widen=widen, sample_size=sample_size, batch_size=batch_size
                )
                same = _snapshot(conn) == reference
            except Exception as e:  # noqa: BLE001 - report and carry on
                same = False
                print(f"seed {seed} {widen}: {e!r}")
            if not same:
                failures += 1
                print(
                    f"MISMATCH seed {seed} {widen} sample={sample_size}"
                    f" batch={batch_size}"
                )
    print(
        f"{args.seeds} seeds x {len(inserter_module.WIDEN_STRATEGIES)} strategies"
        f" (floats: {args.floats}): {failures} mismatches,"
        f" {time.perf_counter() - start:.1f}s"
    )


if __name__ == "__main__":
    main()
