"""
How fast ``datasette URL`` downloads, against a local server so the
network is not the limit.

    uv run python prototypes/data_csv/bench_cli_download.py URL [--repeat 2] [--work DIR]

Compares ``datasette.imports.sources.download()`` (httpx2, as the CLI uses
it) with different chunk sizes, httpx2's ``aiter_raw()`` (no decoding
step), and plain ``urllib.request`` with ``shutil.copyfileobj()``. Each
download goes to a temporary file in ``--work`` that is deleted
afterwards. Prints MB/s and the CPU seconds used.
"""

import argparse
import asyncio
import json
import os
import shutil
import tempfile
import time
import urllib.request


async def httpx_raw(url, path, chunk_size):
    import httpx2

    # Same file handling as download(): a plain file opened up front
    fp = open(path, "wb")  # noqa: ASYNC230, SIM115
    client = httpx2.AsyncClient(follow_redirects=True)
    with fp:
        async with client, client.stream("GET", url) as response:
            async for chunk in response.aiter_raw(chunk_size):
                fp.write(chunk)


def run(mode, url, work):
    from datasette.imports.sources import download

    fd, path = tempfile.mkstemp(dir=work)
    os.close(fd)
    start = time.perf_counter()
    cpu = time.process_time()
    if mode.startswith("download-"):
        chunk = int(mode.split("-")[1]) * 1024
        os.unlink(path)
        path = asyncio.run(download(url, work, chunk_size=chunk))
    elif mode.startswith("raw-"):
        chunk = int(mode.split("-")[1]) * 1024
        asyncio.run(httpx_raw(url, path, chunk))
    elif mode == "urllib":
        with urllib.request.urlopen(url) as response, open(path, "wb") as fp:
            shutil.copyfileobj(response, fp, 1024 * 1024)
    elapsed = time.perf_counter() - start
    size = os.path.getsize(path)
    os.unlink(path)
    return {
        "mode": mode,
        "mb": round(size / 1e6, 1),
        "seconds": round(elapsed, 2),
        "mb_per_s": round(size / 1e6 / elapsed),
        "cpu_s": round(time.process_time() - cpu, 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument(
        "--modes", default="download-256,download-1024,download-4096,raw-1024,urllib"
    )
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--work", default=tempfile.gettempdir())
    options = parser.parse_args()
    os.makedirs(options.work, exist_ok=True)
    for i in range(options.repeat):
        for mode in options.modes.split(","):
            result = run(mode, options.url, options.work)
            result["repeat"] = i + 1
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
