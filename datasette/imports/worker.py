"""
The import worker: load one file with an import loader in a separate process
and stream its rows to the parent as newline-delimited JSON.

Run it as::

    python -m datasette.imports.worker PATH [--format NAME]
        [--plugins-dir DIR] [--progress-interval SECONDS]
        [--memory-limit BYTES] [--parent-pid PID]

The parent (:mod:`datasette.imports.subprocess_import`) reads the stream,
detects column types and inserts the rows through Datasette's write queue.
A strange file or a memory-hungry loader plugin that crashes takes down
this process, not the server.

Protocol, version 1
-------------------

stdout carries the protocol and nothing else (``print()`` from a plugin and
anything else written to file descriptor 1 is sent to stderr). Every line
is one JSON value, encoded as UTF-8, ending in ``\\n``. **A line that starts
with ``[`` is a row; a line that starts with ``{`` is a control message**
with a ``"type"`` key. Rows can never be mistaken for control messages, and
the parent can tell them apart from the first byte without parsing.

In order:

``header`` (mandatory, first)
    ``{"type": "header", "protocol": 1, "loader": "csv", "path": ...,
    "size": 15185971, "scores": {"csv": 1.0, "tsv": 0.0, ...}}`` - sent as
    soon as the loader is chosen, before ``load()`` starts. ``size`` is the
    input file's size in bytes. ``scores`` is empty when ``--format`` chose
    the loader. A parent must refuse a ``protocol`` it does not know.

``schema`` (optional, at most once, before any row)
    ``{"type": "schema", "columns": {"id": "integer", "name": "text"}}`` -
    the :class:`~datasette.imports.loaders.Schema` the loader yielded,
    exactly ``Schema.to_dict()`` plus ``"type"``.

``columns`` (before the first row that has the new columns)
    ``{"type": "columns", "add": ["id", "name", ...]}`` - append these keys
    to the column list. Rows are positional, so the first ``columns``
    message comes before the first row, and another one follows whenever a
    later row has a key no earlier row had (JSON files whose objects differ).
    Keys are the loader's dictionary keys, normally strings.

row
    ``[1, "Cleo", null, ...]`` - the row's values in column-list order, one
    per column known when it was sent. ``null`` is a value that is ``None``
    or a key the row did not have (the inserter treats both the same way).
    Values are whatever the loader yielded, encoded with ``json.dumps``:
    strings, numbers (integers of any size up to Python's 4,300 digit
    limit), booleans, nested lists and objects. ``NaN``, ``Infinity`` and
    ``-Infinity`` are written the way Python's ``json`` module reads and
    writes them by default (not strict JSON). Lone surrogates, which
    ``json.loads`` accepts in ``\\ud800`` escapes, are written with the
    ``surrogatepass`` error handler, which ``json.loads(bytes)`` reverses.
    Values of other types (``datetime``, ``Decimal``) are sent as ``str()``.

``row`` message (a row holding ``bytes``)
    ``{"type": "row", "values": [...], "base64": [2]}`` - the same as a row
    line, except that the values at the listed positions are base64
    strings to decode to ``bytes``. Out of band, so nothing inside a row
    line is ever a tagged value: ``{"$base64": true}`` in a row line is just
    a nested object.

``progress`` (every ``--progress-interval`` seconds while rows flow)
    ``{"type": "progress", "rows": 52000, "bytes": 7864320, "size":
    15185971}`` - rows sent so far and how far into the input file the
    loader has read (``null`` if that cannot be found out). The position is
    read from the operating system's offset for the loader's open file, so
    it works for any loader without its help; it is ahead of the rows by
    the loader's read buffer.

``log`` (any time after the header)
    ``{"type": "log", "level": "WARNING", "logger": "...", "message":
    "..."}`` - a warning (or worse) the loader or a plugin logged, such as
    the JSON loader's "importing the 2 objects in 'results' and ignoring
    'total'".

``end`` (mandatory last message of a successful import)
    ``{"type": "end", "rows": 100000, "bytes": 15185971, "seconds": 1.9,
    "cpu": 1.8, "max_rss": 41000000}`` - the number of rows sent, so the
    parent can tell a clean finish from a stream that was cut short. The
    worker then exits with status 0.

``error`` (instead of ``end``)
    ``{"type": "error", "error": "LoaderError", "message": "Line 11 is not
    valid JSON: ...", "rows": 10}`` - every row the loader produced before
    the error has been sent before this message (``rows`` says how many).
    The traceback goes to stderr and the worker exits with status 1. An
    ``error`` can also be the first message, before any ``header``, if the
    file cannot be read or no loader recognizes it.

Anything else - no ``end`` or ``error``, a row count that does not match,
a line that is not JSON, a non-zero exit after ``end`` - is a crash or a
protocol error, and the parent treats it as one.

``--memory-limit`` sets ``RLIMIT_AS`` (soft and hard) for this process
before any plugin is loaded, so a loader that tries to use more fails with
``MemoryError`` (reported as an ``error`` message if there is still enough
memory to do that) or is killed. On Linux the worker also makes itself the
out-of-memory killer's first choice (``oom_score_adj`` 1000) and, given
``--parent-pid``, asks to be killed when the parent dies
(``PR_SET_PDEATHSIG``) - otherwise it notices when its next write to the
closed pipe fails.

``--direct-db`` is an experiment, not part of the protocol: the worker
inserts the rows into that SQLite file itself with
:func:`~datasette.imports.inserter.insert_rows` and sends only ``header``,
``schema``, ``progress`` and ``end``/``error``.
"""

import argparse
import base64
import collections
import json
import logging
import os
import sys
import threading
import time

PROTOCOL_VERSION = 1
# Rows are written to stdout once this many bytes of them are waiting
FLUSH_BYTES = 64 * 1024
DEFAULT_PROGRESS_INTERVAL = 0.2
# Exit statuses
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

_BYTES_TYPES = (bytes, bytearray, memoryview)


class _BytesValue(Exception):
    "Raised while encoding a row that holds bytes: it takes the slow path."


def _default(value):
    if isinstance(value, _BYTES_TYPES):
        raise _BytesValue()
    # The inserter would store str(value) for these too
    return str(value)


def _nested_default(value):
    # Inside a list or dict: the inserter's json.dumps(default=str) does this
    return str(value)


_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), default=_default)
_NESTED_ENCODER = json.JSONEncoder(
    ensure_ascii=False, separators=(",", ":"), default=_nested_default
)
_CONTROL_ENCODER = json.JSONEncoder(
    ensure_ascii=False, separators=(",", ":"), default=str
)


def encode_row(values: list) -> str:
    """
    One row as a protocol line (without the newline): a JSON array, or a
    ``row`` control message if any value is ``bytes``.
    """
    try:
        return _ENCODER.encode(values)
    except (_BytesValue, ValueError):
        return _encode_row_slowly(values)


def _encode_row_slowly(values):
    values = list(values)
    positions = []
    for i, value in enumerate(values):
        if isinstance(value, _BYTES_TYPES):
            values[i] = base64.b64encode(bytes(value)).decode("ascii")
            positions.append(i)
        elif isinstance(value, int) and value.bit_length() > 14000:
            # More digits than int() and str() allow by default: send the
            # text the inserter would store for it
            values[i] = _int_to_text(value)
    message = {"type": "row", "values": values}
    if positions:
        message["base64"] = positions
    return _NESTED_ENCODER.encode(message)


def _int_to_text(value):
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(0)
    try:
        return str(value)
    finally:
        sys.set_int_max_str_digits(limit)


def encode_message(message: dict) -> str:
    "One control message as a protocol line (without the newline)."
    return _CONTROL_ENCODER.encode(message)


class _Writer:
    """
    Buffers protocol lines and writes them to the protocol file.

    Lines go into a deque, which the loading thread appends to without a
    lock; :meth:`flush` takes the lines that are there under a lock, so the
    :class:`_Flusher` thread can flush too without lines being lost,
    repeated or reordered.
    """

    def __init__(self, out):
        self.out = out
        self.pending = collections.deque()
        self.pending_bytes = 0
        self.last_flush = time.monotonic()
        self._lock = threading.Lock()

    def line(self, line):
        self.pending.append(line)
        self.pending_bytes += len(line)
        if self.pending_bytes >= FLUSH_BYTES:
            self.flush()

    def message(self, message):
        self.line(encode_message(message))

    def flush(self):
        with self._lock:
            pending = self.pending
            lines = [pending.popleft() for _ in range(len(pending))]
            self.pending_bytes = 0
            self.last_flush = time.monotonic()
            if lines:
                data = ("\n".join(lines) + "\n").encode("utf-8", "surrogatepass")
                self.out.write(data)
            self.out.flush()


class _Flusher(threading.Thread):
    """
    While a loader is slow to produce its next row (or stuck), send the rows
    it has produced so far, and progress: nothing waits in the buffer for
    longer than ``interval`` seconds.
    """

    def __init__(self, writer, streamer, interval):
        super().__init__(daemon=True, name="import-worker-flusher")
        self.writer = writer
        self.streamer = streamer
        self.interval = max(interval, 0.05)
        self.stopped = threading.Event()

    def run(self):
        try:
            while not self.stopped.wait(self.interval):
                if time.monotonic() - self.writer.last_flush >= self.interval:
                    if self.streamer is not None:
                        self.streamer.progress()
                    self.writer.flush()
        except BrokenPipeError:
            os._exit(EXIT_ERROR)

    def stop(self):
        self.stopped.set()
        self.join()


class _LogHandler(logging.Handler):
    "Sends warnings logged in the worker to the parent as ``log`` messages."

    def __init__(self, writer):
        super().__init__(level=logging.WARNING)
        self.writer = writer

    def emit(self, record):
        try:
            self.writer.message(
                {
                    "type": "log",
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
            )
        except Exception:  # noqa: BLE001
            self.handleError(record)


class InputPosition:
    """
    How far into a file this process has read, from the offset of whichever
    open file descriptor refers to it - so it works for any loader, which
    opens the file itself. Returns ``None`` where open descriptors cannot
    be listed (``/proc/self/fd`` on Linux, ``/dev/fd`` on macOS).
    """

    def __init__(self, path):
        stat = os.stat(path)
        self.key = (stat.st_dev, stat.st_ino)
        self.size = stat.st_size
        self.fd = None
        self.last = 0
        self.fd_dir = next(
            (d for d in ("/proc/self/fd", "/dev/fd") if os.path.isdir(d)), None
        )

    def _offset(self, fd):
        try:
            stat = os.fstat(fd)
            if (stat.st_dev, stat.st_ino) != self.key:
                return None
            return os.lseek(fd, 0, os.SEEK_CUR)
        except OSError:
            return None

    def get(self):
        if self.fd is not None:
            offset = self._offset(self.fd)
            if offset is not None:
                self.last = max(self.last, offset)
                return self.last
            self.fd = None
        if self.fd_dir is None:
            return None
        try:
            names = os.listdir(self.fd_dir)
        except OSError:
            return None
        for name in names:
            if not name.isdigit() or int(name) < 3:
                continue
            offset = self._offset(int(name))
            if offset is not None:
                self.fd = int(name)
                self.last = max(self.last, offset)
                return self.last
        # The loader has closed the file (or not opened it yet)
        return self.last


def _protect_stdout():
    """
    Keep the protocol on a private copy of stdout, and send anything else
    written to file descriptor 1 (``print()`` in a plugin, a C library) to
    stderr instead.
    """
    sys.stdout.flush()
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(protocol_fd, "wb", buffering=0)


def _limit_resources(memory_limit, parent_pid):
    if memory_limit:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    if sys.platform.startswith("linux"):
        # Out of memory: kill this process before the server
        try:
            with open("/proc/self/oom_score_adj", "w") as fp:
                fp.write("1000")
        except OSError:
            pass
        if parent_pid:
            try:
                import ctypes
                import signal

                libc = ctypes.CDLL(None, use_errno=True)
                PR_SET_PDEATHSIG = 1
                libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
            except Exception:  # noqa: BLE001, S110
                # Best effort: a failed write to the pipe stops us anyway
                pass
            if os.getppid() != parent_pid:
                # The parent died before prctl() took effect
                os._exit(EXIT_ERROR)


def load_plugins_dir(plugins_dir):
    """
    Register every ``*.py`` file in ``plugins_dir`` as a plugin, the way
    ``Datasette(plugins_dir=...)`` does, without creating a Datasette.
    """
    import glob

    from datasette.plugins import pm
    from datasette.utils import module_from_path

    for filepath in sorted(glob.glob(os.path.join(plugins_dir, "*.py"))):
        if not os.path.isfile(filepath):
            continue
        mod = module_from_path(filepath, name=os.path.basename(filepath))
        try:
            pm.register(mod)
        except ValueError:
            # Already registered
            pass


class RowStreamer:
    """
    Sends rows (dictionaries) as ``columns`` messages and row lines, with
    ``progress`` messages. ``count`` is the number of rows sent, kept up to
    date even if the loader raises part way through.
    """

    def __init__(self, writer, position, progress_interval):
        self.writer = writer
        self.position = position
        self.progress_interval = progress_interval
        self.columns = []
        self.count = 0

    def progress(self):
        self.writer.message(
            {
                "type": "progress",
                "rows": self.count,
                "bytes": self.position.get(),
                "size": self.position.size,
            }
        )

    def send(self, rows):
        writer = self.writer
        columns = self.columns
        known = set(columns)
        keys = tuple(columns)
        count = self.count
        encode = _ENCODER.encode
        pending = writer.pending
        interval = self.progress_interval
        last_progress = time.monotonic()
        try:
            for row in rows:
                if tuple(row) == keys:
                    values = list(row.values())
                else:
                    new = [key for key in row if key not in known]
                    if new:
                        columns.extend(new)
                        known.update(new)
                        writer.message({"type": "columns", "add": new})
                    keys = tuple(columns)
                    if tuple(row) == keys:
                        values = list(row.values())
                    else:
                        values = [row.get(key) for key in columns]
                try:
                    line = encode(values)
                except (_BytesValue, ValueError):
                    line = _encode_row_slowly(values)
                pending.append(line)
                writer.pending_bytes += len(line)
                count += 1
                if writer.pending_bytes >= FLUSH_BYTES or not count & 1023:
                    now = time.monotonic()
                    if now - last_progress >= interval:
                        last_progress = now
                        self.count = count
                        self.progress()
                    if (
                        writer.pending_bytes >= FLUSH_BYTES
                        or now - writer.last_flush >= interval
                    ):
                        writer.flush()
        finally:
            self.count = count
        return count


def _usage():
    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF)
    max_rss = usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    return usage.ru_utime + usage.ru_stime, max_rss


def run(args, writer):
    "Load the file and stream it. Returns the exit status."
    from datasette.imports import loaders as loaders_module

    start = time.monotonic()
    handler = _LogHandler(writer)
    logging.getLogger().addHandler(handler)
    count = 0
    try:
        if args.plugins_dir:
            load_plugins_dir(args.plugins_dir)
        path = args.path
        position = InputPosition(path)
        loaders = loaders_module.get_loaders()
        if args.format:
            loader = loaders_module.get_loader(args.format, loaders)
            scores = []
        else:
            first_bytes, scores = loaders_module.sniff_path(path, loaders)
            loader = loaders_module._choose(
                scores, first_bytes, loaders_module.MIN_CONFIDENCE
            )
        writer.message(
            {
                "type": "header",
                "protocol": PROTOCOL_VERSION,
                "loader": loader.name,
                "path": path,
                "size": position.size,
                "scores": {score.name: score.score for score in scores},
            }
        )
        writer.flush()
        schema, rows = loaders_module.split_schema(loader.load(path), loader.name)
        if schema is not None:
            writer.message({"type": "schema", **schema.to_dict()})
        if args.direct_db:
            count = _insert_directly(args, writer, schema, rows, position)
        else:
            streamer = RowStreamer(writer, position, args.progress_interval)
            flusher = _Flusher(writer, streamer, args.progress_interval)
            flusher.start()
            try:
                count = streamer.send(rows)
            finally:
                count = streamer.count
                flusher.stop()
        cpu, max_rss = _usage()
        writer.message(
            {
                "type": "end",
                "rows": count,
                "bytes": position.size,
                "seconds": round(time.monotonic() - start, 6),
                "cpu": round(cpu, 6),
                "max_rss": max_rss,
            }
        )
        writer.flush()
        return EXIT_OK
    except BrokenPipeError:
        # The parent has gone away: nobody is listening
        raise
    except Exception as ex:  # noqa: BLE001
        import traceback

        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        message = {
            "type": "error",
            "error": type(ex).__name__,
            "message": str(ex),
            "rows": count,
        }
        if isinstance(ex, loaders_module.LoaderError):
            message["loader_error"] = True
        # Rows already sent stay valid: send the ones still buffered first
        writer.message(message)
        writer.flush()
        return EXIT_ERROR
    finally:
        logging.getLogger().removeHandler(handler)


def _insert_directly(args, writer, schema, rows, position):
    "EXPERIMENT: insert into ``args.direct_db`` from this process."
    import sqlite3

    from datasette.imports.inserter import insert_rows

    conn = sqlite3.connect(args.direct_db, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    last = [time.monotonic()]

    def progress(written):
        now = time.monotonic()
        if now - last[0] >= args.progress_interval:
            last[0] = now
            writer.message(
                {
                    "type": "progress",
                    "rows": written,
                    "bytes": position.get(),
                    "size": position.size,
                }
            )
            writer.flush()

    try:
        result = insert_rows(
            conn,
            args.table,
            rows,
            schema=schema.columns if schema else None,
            batch_size=args.batch_size,
            progress=progress,
        )
    finally:
        conn.close()
    return result.rows


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m datasette.imports.worker",
        description="Load a file with a Datasette import loader and write its "
        "rows to stdout as newline-delimited JSON.",
    )
    parser.add_argument("path", help="File to load")
    parser.add_argument("--format", help="Use this loader instead of sniffing")
    parser.add_argument(
        "--plugins-dir", help="Directory of plugins, as for datasette --plugins-dir"
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=DEFAULT_PROGRESS_INTERVAL,
        help="Seconds between progress messages",
    )
    parser.add_argument(
        "--memory-limit", type=int, help="Address space limit in bytes (RLIMIT_AS)"
    )
    parser.add_argument(
        "--parent-pid", type=int, help="Exit if this process (the parent) dies"
    )
    parser.add_argument(
        "--direct-db", help="EXPERIMENT: insert into this SQLite file directly"
    )
    parser.add_argument("--table", help="Table for --direct-db")
    parser.add_argument(
        "--batch-size", type=int, default=1000, help="Batch size for --direct-db"
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.direct_db and not args.table:
        print("--direct-db needs --table", file=sys.stderr)
        return EXIT_USAGE
    out = _protect_stdout()
    _limit_resources(args.memory_limit, args.parent_pid)
    writer = _Writer(out)
    try:
        return run(args, writer)
    except BrokenPipeError:
        # Don't try to flush anything on the way out
        os._exit(EXIT_ERROR)


if __name__ == "__main__":
    sys.exit(main())
