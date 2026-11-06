"""
Files for the background import experiments (bench_background_load.py):

- ``widen-late.csv``: the first ROWS rows of big-1gb.csv, then one row with
  text in the integer ``qty`` column, so the import has to change that
  column to text right at the end - which rebuilds the whole table (DROP
  TABLE and ALTER TABLE RENAME) while people are reading it.
- ``late-keys.ndjson``: rows that gain a new key every ``--every`` rows, so
  the import adds a column (ALTER TABLE ADD COLUMN) part way through.

    uv run python prototypes/data_csv/make_background_data.py \\
        DATA/big-1gb.csv OUT_DIR --rows 4000000
"""

import argparse
import json
import os
import random


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("big_csv")
    parser.add_argument("out_dir")
    parser.add_argument("--rows", type=int, default=4_000_000)
    parser.add_argument("--json-rows", type=int, default=600_000)
    parser.add_argument("--medium-rows", type=int, default=1_000_000)
    parser.add_argument("--every", type=int, default=150_000)
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    path = os.path.join(args.out_dir, "widen-late.csv")
    with open(args.big_csv, "rb") as src, open(path, "wb") as out:
        out.write(src.readline())
        for _ in range(args.rows):
            line = src.readline()
            if not line:
                break
            out.write(line)
        out.write(b"999999999,late row,Lima,lots,1.5,2026-01-01,1,1,1,\n")
    print(path, os.path.getsize(path))

    # A smaller copy, for quicker runs
    path = os.path.join(args.out_dir, "medium.csv")
    with open(args.big_csv, "rb") as src, open(path, "wb") as out:
        for _ in range(args.medium_rows + 1):
            out.write(src.readline())
    print(path, os.path.getsize(path))

    path = os.path.join(args.out_dir, "late-keys.ndjson")
    rng = random.Random(1)
    cities = ["Lima", "Oslo", "Perth", "Quito", "Riga"]
    with open(path, "w") as out:
        for i in range(args.json_rows):
            row = {"id": i, "city": rng.choice(cities), "value": rng.random()}
            # One more key every --every rows
            for k in range(i // args.every):
                row[f"extra_{k}"] = i
            out.write(json.dumps(row) + "\n")
    print(path, os.path.getsize(path))


if __name__ == "__main__":
    main()
