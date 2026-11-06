"""
Generate the deterministic synthetic test data used by the data.csv prototypes.

Usage:

    python prototypes/data_csv/generate_test_data.py OUTPUT_DIR

Writes about 1.1 GB: events-100k.{csv,tsv,ndjson,json} (100,000 rows each),
big-1gb.csv (6.4 million rows, about 1 GB), places.geojson,
places-type-last.json (a FeatureCollection with "type" after "features"),
plain-array.json and four messy-*.{csv,json} files. Takes about a minute.

Columns are chosen to exercise type detection: late_float is an integer until
row 5,000 and then has some floats, late_text is an integer until row 20,000
and then has occasional "N/A-..." text, and maybe_empty has about 5% empty
strings. Both widenings happen after the 1,000-row sample.
"""

import csv
import json
import os
import random
import sys

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
rnd = random.Random(42)
WORDS = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "omicron",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "chi",
    "psi",
    "omega",
]
CITIES = [
    "London",
    "Paris",
    "Tokyo",
    "Lagos",
    "Lima",
    "Oslo",
    "Delhi",
    "Cairo",
    "Quito",
    "Perth",
]


def row(i):
    # id: int always. qty: int. price: float. late_float: int until row 5000 then floats (forces int->float widening)
    # late_text: int until row 20000 then occasional text (forces ->text widening). maybe_empty: int with ~5% empty strings
    late_float = i if i < 5000 else (i + 0.5 if i % 7 == 0 else i)
    late_text = i if i < 20000 or i % 1000 else f"N/A-{i}"
    return {
        "id": i,
        "name": " ".join(rnd.choice(WORDS) for _ in range(3)),
        "city": rnd.choice(CITIES),
        "qty": rnd.randint(0, 10000),
        "price": round(rnd.uniform(0, 1000), 2),
        "created": f"2026-{rnd.randint(1,12):02d}-{rnd.randint(1,28):02d}",
        "late_float": late_float,
        "late_text": late_text,
        "maybe_empty": "" if rnd.random() < 0.05 else rnd.randint(0, 99),
        "notes": "lorem ipsum dolor sit amet, consectetur adipiscing elit "
        * rnd.randint(0, 3),
    }


def write_csv(path, n, delimiter=","):
    with open(path, "w", newline="") as fp:
        w = None
        for i in range(n):
            r = row(i)
            if w is None:
                w = csv.DictWriter(fp, fieldnames=list(r), delimiter=delimiter)
                w.writeheader()
            w.writerow(r)


def write_csv_to_size(path, target_bytes):
    with open(path, "w", newline="") as fp:
        w = None
        i = 0
        while True:
            r = row(i)
            if w is None:
                w = csv.DictWriter(fp, fieldnames=list(r))
                w.writeheader()
            w.writerow(r)
            i += 1
            if i % 100000 == 0 and fp.tell() >= target_bytes:
                break
    return i


small = 100_000
write_csv(f"{OUT}/events-100k.csv", small)
write_csv(f"{OUT}/events-100k.tsv", small, delimiter="\t")
with open(f"{OUT}/events-100k.ndjson", "w") as fp:
    fp.writelines(json.dumps(row(i)) + "\n" for i in range(small))
with open(f"{OUT}/events-100k.json", "w") as fp:
    json.dump([row(i) for i in range(small)], fp)
# GeoJSON FeatureCollection: "type" first, and a variant where "features" come before "type" and "crs" is large
feats = [
    {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [rnd.uniform(-180, 180), rnd.uniform(-90, 90)],
        },
        "properties": {"name": rnd.choice(WORDS), "pop": rnd.randint(1, 10**6)},
    }
    for _ in range(2000)
]
with open(f"{OUT}/places.geojson", "w") as fp:
    json.dump({"type": "FeatureCollection", "features": feats}, fp)
with open(f"{OUT}/places-type-last.json", "w") as fp:
    json.dump({"features": feats, "type": "FeatureCollection"}, fp)
with open(f"{OUT}/plain-array.json", "w") as fp:
    json.dump([f["properties"] for f in feats], fp)
# Messy files
with open(f"{OUT}/messy-bom.csv", "w", encoding="utf-8-sig") as fp:
    fp.write("id,name\n1,café\n2,naïve\n")
with open(f"{OUT}/messy-latin1.csv", "wb") as fp:
    fp.write("id,name\n1,café\n2,naïve\n".encode("latin-1"))
with open(f"{OUT}/messy-semicolon.csv", "w") as fp:
    fp.write("id;name;price\n1;foo;1,5\n2;bar;2,25\n")
with open(f"{OUT}/messy-wrapped.json", "w") as fp:
    json.dump(
        {
            "data": [
                {"id": 1, "tags": ["a", "b"], "meta": {"x": 1}},
                {"id": 2, "tags": [], "meta": None},
            ]
        },
        fp,
    )
n = write_csv_to_size(f"{OUT}/big-1gb.csv", 1_000_000_000)
print("big rows", n)
