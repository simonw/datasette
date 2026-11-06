"""
End to end: how much parent CPU do positional rows save in a worker import?

The parent used to turn every row line from the worker into a dictionary
(``dict(zip(columns, values))`` in ProtocolDecoder), which TypedInserter
then turned back into a list. Now the decoder hands over the lists and
import_file_via_worker() calls import_rows(..., columns=decoder.columns).

This runs the real import_file_via_worker() into a ScratchDatabase (WAL,
synchronous=OFF) and measures the parent's CPU (time.process_time(): the
event loop, the write thread and executor threads together) and wall time.
The "dicts" variant patches the parent back to the old behaviour - the old
decoder loop that built dictionaries, and import_rows() without
``columns`` - so the worker, the pipe and everything else are the same.
Variants are interleaved.

    uv run python prototypes/data_csv/bench_worker_positional.py \\
        --repeat 2 events-100k.csv big-1gb.csv
"""

import argparse
import asyncio
import contextlib
import statistics
import time
import uuid

from datasette.app import Datasette
from datasette.imports import subprocess_import
from datasette.imports.scratch import ScratchDatabase, import_writes
from datasette.imports.subprocess_import import (
    _SCAN,
    ProtocolDecoder,
    ProtocolError,
    import_file_via_worker,
)


def _old_decode_lines(self, text, out):
    # ProtocolDecoder._decode_lines() as it was, building a dict per row
    scan = _SCAN
    find = text.find
    columns = self.columns
    rows = None
    pos = 0
    length = len(text)
    while pos < length:
        self.lines += 1
        first = text[pos]
        if first == "\n":
            pos += 1
            continue
        if first != "[" and first != "{":
            raise ProtocolError("not a row or a message")
        start = pos
        value, pos = scan(text, pos)
        if text[pos] != "\n":
            raise ProtocolError("extra data")
        if find("\n", start, pos) != -1:
            raise ProtocolError("not a complete JSON value")
        pos += 1
        if first == "[":
            if rows is None:
                self._check_can_send_rows(self.lines)
                rows = self._run = []
            if len(value) > len(columns):
                raise ProtocolError("too many values")
            rows.append(dict(zip(columns, value)))
            continue
        if value.get("type") == "row":
            if rows is None:
                self._check_can_send_rows(self.lines)
                rows = self._run = []
            row = self._row_message(value)
            if isinstance(row, list):
                row = dict(zip(columns, row))
            rows.append(row)
            continue
        if rows is not None:
            self.rows += len(rows)
            out.append(("rows", rows))
            rows = self._run = None
        message = self._control(value)
        if message is not None:
            out.append(message)


@contextlib.contextmanager
def dict_rows():
    real_import_rows = subprocess_import.import_rows
    real_decode = ProtocolDecoder._decode_lines

    async def import_rows(*args, columns=None, **kwargs):
        return await real_import_rows(*args, **kwargs)

    subprocess_import.import_rows = import_rows
    ProtocolDecoder._decode_lines = _old_decode_lines
    try:
        yield
    finally:
        subprocess_import.import_rows = real_import_rows
        ProtocolDecoder._decode_lines = real_decode


async def run_once(path, variant):
    ds = Datasette()
    db = ds.add_database(ScratchDatabase(ds), name="scratch")
    table = "t_" + uuid.uuid4().hex[:8]
    patch = dict_rows() if variant == "dicts" else contextlib.nullcontext()
    try:
        with patch, import_writes():
            cpu = time.process_time()
            wall = time.perf_counter()
            result = await import_file_via_worker(db, table, path)
            wall = time.perf_counter() - wall
            cpu = time.process_time() - cpu
        return {
            "rows": result.rows,
            "wall": wall,
            "parent_cpu": cpu,
            "worker_cpu": result.worker.get("cpu"),
            "decode": result.stats["decode_seconds"],
            "write": result.stats["write_seconds"],
        }
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--repeat", type=int, default=2)
    args = parser.parse_args()
    for path in args.files:
        results = {"dicts": [], "positional": []}
        for i in range(args.repeat):
            order = ("dicts", "positional") if i % 2 == 0 else ("positional", "dicts")
            for variant in order:
                result = asyncio.run(run_once(path, variant))
                results[variant].append(result)
                print(
                    f"{path} {variant}: {result['rows']:,} rows, "
                    f"wall {result['wall']:.2f}s, parent CPU "
                    f"{result['parent_cpu']:.2f}s (decode {result['decode']:.2f}s, "
                    f"in writes {result['write']:.2f}s), worker CPU "
                    f"{result['worker_cpu']:.2f}s",
                    flush=True,
                )
        for variant, runs in results.items():
            print(
                f"{path} {variant}: median wall "
                f"{statistics.median(r['wall'] for r in runs):.2f}s, parent CPU "
                f"{statistics.median(r['parent_cpu'] for r in runs):.2f}s",
                flush=True,
            )


if __name__ == "__main__":
    main()
