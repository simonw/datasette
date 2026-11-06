"""
Files with shapes the dense 100k-row benchmarks miss, for bench_worker.py:

- hugecell.csv: one 64MB cell
- hugeobj.ndjson: one NDJSON line with a 64MB string
- biglines-4mb.ndjson: 40 GeoJSON-like features of about 4MB each (nested
  lists of coordinates), 160MB
- sparse-200.ndjson / sparse-1000.ndjson: 100k rows, each with id, event
  and 8 of 200 / 1000 optional keys

    uv run python prototypes/data_csv/make_worker_shapes.py OUT_DIR
"""

import json
import os
import random
import sys


def main(out):
    os.makedirs(out, exist_ok=True)
    big = 64 * 1024 * 1024
    with open(os.path.join(out, "hugecell.csv"), "w") as fp:
        fp.write("a,b\n1," + "x" * big + "\n2,y\n")
    with open(os.path.join(out, "hugeobj.ndjson"), "w") as fp:
        fp.write(json.dumps({"a": 1, "big": "y" * big}) + "\n")
        fp.write(json.dumps({"a": 2}) + "\n")
    rng = random.Random(0)
    with open(os.path.join(out, "biglines-4mb.ndjson"), "w") as fp:
        for i in range(40):
            coords = []
            length = 0
            while length < 4 * 1024 * 1024:
                coords.append(
                    [round(rng.uniform(-180, 180), 6), round(rng.uniform(-90, 90), 6)]
                )
                length += 26
            feature = {
                "id": i,
                "name": f"feature {i}",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            }
            fp.write(json.dumps(feature) + "\n")
    for keys in (200, 1000):
        rng = random.Random(0)
        names = [f"prop_{i}" for i in range(keys)]
        with open(os.path.join(out, f"sparse-{keys}.ndjson"), "w") as fp:
            for i in range(100000):
                row = {"id": i, "event": "click"}
                for name in rng.sample(names, 8):
                    row[name] = rng.choice([rng.randint(0, 1000), "some text", 1.5])
                fp.write(json.dumps(row) + "\n")
    for name in sorted(os.listdir(out)):
        print(f"{name}: {os.path.getsize(os.path.join(out, name)):,} bytes")


if __name__ == "__main__":
    main(sys.argv[1])
