"""
Load several kinds of input at once and join two of the tables.

    uv run python prototypes/data_csv/cli_mixed_inputs.py DATA_DIR URL

Runs ``datasette DATA_DIR/events-100k.csv DATA_DIR/events-100k.ndjson
DATA_DIR/places.geojson URL - --get ...`` with a small CSV of cities piped
to stdin (no GeoJSON plugin is installed, so places.geojson is read by the
JSON loader), then prints the stderr summary, the tables and the result of
a join between the CSV and stdin tables and between the CSV and NDJSON
tables.
"""

import json
import os
import subprocess
import sys
import time
import urllib.parse

STDIN = "city,country\nLondon,UK\nLima,Peru\nParis,France\nTokyo,Japan\n"


def main():
    data_dir, url = sys.argv[1], sys.argv[2]
    inputs = [
        os.path.join(data_dir, "events-100k.csv"),
        os.path.join(data_dir, "events-100k.ndjson"),
        os.path.join(data_dir, "places.geojson"),
        url,
        "-",
    ]
    sql = """
    select stdin.country, count(*) as events, round(sum(e.price), 2) as revenue,
      sum(e.qty = n.qty) as ndjson_rows_agreeing
    from events_100k e
    join stdin on e.city = stdin.city
    join events_100k_2 n on n.id = e.id
    group by stdin.country order by events desc
    """
    paths = {
        "databases": "/-/databases.json",
        "tables": "/scratch.json",
        "join": "/scratch/-/query.json?_shape=array&sql="
        + urllib.parse.quote(" ".join(sql.split())),
    }
    env = dict(os.environ, DATASETTE_SECRET="not_a_secret")
    for label, path in paths.items():
        start = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-m", "datasette", *inputs, "--get", path],
            input=STDIN,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        elapsed = time.perf_counter() - start
        if label == "databases":
            print(f"exit code {result.returncode} in {elapsed:.2f}s; stderr:")
            for line in result.stderr.splitlines():
                print("   ", line.replace(data_dir, "DATA"))
        data = json.loads(result.stdout)
        if label == "databases":
            print("databases:", [db["name"] for db in data["databases"]])
        elif label == "tables":
            print("tables:", {t["name"]: t["count"] for t in data["tables"]})
        else:
            print("join result:")
            for row in data:
                print("   ", row)


if __name__ == "__main__":
    main()
