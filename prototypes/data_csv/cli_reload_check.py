"""
Exercise ``datasette --reload`` with a CSV file, a URL and stdin.

    uv run python prototypes/data_csv/cli_reload_check.py [URL]

1. Starts ``datasette dogs.csv URL - --reload`` with data piped to stdin.
2. Edits dogs.csv: the worker restarts and loads only dogs again.
3. Touches a template-like file the reloader also watches (the CSV of
   another, unchanged input is not touched): tables are kept.
4. Stops the reloader and checks the session directory is gone.
"""

import glob
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

PORT = 8799


def wait_for(predicate, timeout=60):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            value = predicate()
            if value:
                return value
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError


def tables():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/scratch.json") as r:
        return {t["name"]: t["count"] for t in json.load(r)["tables"]}


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else None
    work = tempfile.mkdtemp()
    csv_path = os.path.join(work, "dogs.csv")
    with open(csv_path, "w") as fp:
        fp.write("id,name\n1,Cleo\n2,Pancakes\n")
    metadata = os.path.join(work, "metadata.json")
    with open(metadata, "w") as fp:
        fp.write("{}")
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "datasette-reload-*")))
    args = [sys.executable, "-m", "datasette", csv_path, "-", "--reload"]
    args += ["-m", metadata, "-p", str(PORT)]
    if url:
        args.insert(4, url)
    log = open(os.path.join(work, "stderr.txt"), "w+")
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stderr=log,
        stdout=log,
        env=dict(os.environ, DATASETTE_SECRET="not_a_secret"),
        start_new_session=True,
    )
    process.stdin.write(b"a,b\n1,2\n3,4\n")
    process.stdin.close()
    start = time.monotonic()
    first = wait_for(tables)
    print(f"first worker served after {time.monotonic() - start:.1f}s: {first}")

    time.sleep(1.5)
    with open(csv_path, "a") as fp:
        fp.write("3,Fido\n")
    start = time.monotonic()
    second = wait_for(lambda: (t := tables()).get("dogs") == 3 and t)
    print(
        f"after editing dogs.csv, served after {time.monotonic() - start:.1f}s: {second}"
    )

    time.sleep(1.5)
    with open(metadata, "w") as fp:
        fp.write('{"title": "Changed"}')
    start = time.monotonic()

    def title_changed():
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/.json") as r:
            return json.load(r).get("metadata", {}).get("title") == "Changed"

    wait_for(title_changed)
    third = tables()
    print(
        f"after editing metadata.json, served after {time.monotonic() - start:.1f}s: {third}"
    )

    os.killpg(process.pid, signal.SIGINT)
    process.wait(timeout=30)
    time.sleep(0.5)
    log.seek(0)
    output = log.read()
    print("loader lines from stderr:")
    for line in output.splitlines():
        if line.startswith(("Loaded", "Kept", "Downloaded", "Loading")):
            print("   ", line.replace(work, "WORK"))
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "datasette-reload-*")))
    print(f"session directories left behind: {sorted(after - before) or 'none'}")


if __name__ == "__main__":
    main()
