"""
The import worker: load one file with an import loader in a separate process
and stream its rows to the parent as newline-delimited JSON.

Run it as::

    python -m datasette.imports.worker [--format NAME]
        [--plugins-dir DIR] [--progress-interval SECONDS]
        [--memory-limit BYTES] [--parent-pid PID] [--] PATH

(the parent always puts ``--`` before the path, so a file called
``-data.csv`` is not taken for an option).

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

``header`` (mandatory, first but for ``log`` messages)
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
    ``[1, "Cleo", null, ...]`` - the row's values in column-list order, at
    most one per column known when it was sent. A row may stop early: a
    row whose last keys are missing is sent without the trailing nulls.
    ``null`` is a value that is ``None`` or a key the row did not have (the
    inserter treats both the same way).
    Values are whatever the loader yielded, encoded with ``json.dumps``:
    strings, numbers (integers of any size up to Python's 4,300 digit
    limit) and booleans. A nested list or object (or tuple) is sent as its
    JSON text, ``json.dumps(value, default=str, ensure_ascii=False)``,
    which is exactly what the inserter stores for one: the parent never
    rebuilds and re-serializes a big nested value such as a GeoJSON
    geometry. ``NaN``, ``Infinity`` and
    ``-Infinity`` are written the way Python's ``json`` module reads and
    writes them by default (not strict JSON). Lone surrogates, which
    ``json.loads`` accepts in ``\\ud800`` escapes, are written with the
    ``surrogatepass`` error handler, which ``json.loads(bytes)`` reverses.
    Values of other types (``datetime``, ``Decimal``) are sent as ``str()``.

``row`` message (a row holding ``bytes``, or a sparse row)
    ``{"type": "row", "values": [...], "base64": [2]}`` - the same as a row
    line, except that the values at the listed indexes of ``values`` are
    base64 strings to decode to ``bytes``. Out of band, so nothing inside a
    row line is ever a tagged value: ``{"$base64": true}`` in a row line is
    just a nested object. With ``"at": [0, 517, 9]``, ``values[i]`` belongs
    to column ``at[i]`` and every other column is missing. The worker sends
    a row this way when more than 32 of the known columns, and more than
    two thirds of them, are missing from it (JSON objects that each have a
    few of many optional keys), instead of a row line made mostly of
    nulls. The keys keep the row's own order.

``progress`` (every ``--progress-interval`` seconds, any time after the header)
    ``{"type": "progress", "rows": 52000, "bytes": 7864320, "size":
    15185971}`` - rows sent so far and how far into the input file the
    loader has read (``null`` if that cannot be found out). The position is
    read from the operating system's offset for the loader's open file, so
    it works for any loader without its help; it is ahead of the rows by
    the loader's read buffer. A thread sends these while the loader is busy
    before its first row, or between rows, too - unless the loader holds
    the GIL throughout (``json.load()`` of a whole file does), so the
    parent should not treat a quiet worker as a dead one. Rows never wait
    in the worker's buffer for longer than the interval.

``log`` (any time, even before the header)
    ``{"type": "log", "level": "WARNING", "logger": "...", "message":
    "..."}`` - a warning (or worse) the loader or a plugin logged, for
    example while the loader is chosen, before the header, when a plugin's
    ``register_import_loaders()`` fails. With
    ``"note": true`` it is a :class:`~datasette.imports.loaders.Note` the
    loader yielded, something to show the person importing the file, such
    as the JSON loader's "Importing the 2 objects in 'results', ignoring
    'total'" (``LoadResult.notes`` in an in-process load). It arrives in
    order with the rows: a note about the whole file can come after the
    last row. Parents that don't know ``"note"`` see an ordinary warning.

``end`` (mandatory last message of a successful import)
    ``{"type": "end", "rows": 100000, "bytes": 15185971, "seconds": 1.9,
    "cpu": 1.8, "max_rss": 41000000, "write_seconds": 0.4}`` - the number of
    rows sent, so the parent can tell a clean finish from a stream that was
    cut short. ``write_seconds`` is the time spent writing to stdout, which
    is mostly time blocked on a full pipe while the parent catches up.
    ``max_rss`` is ``null`` where there is no ``resource`` module
    (Windows). The worker then exits with status 0.

``error`` (instead of ``end``)
    ``{"type": "error", "error": "LoaderError", "message": "Line 11 is not
    valid JSON: ...", "rows": 10}`` - every row the loader produced before
    the error has been sent before this message (``rows`` says how many).
    That includes ``SystemExit`` and ``KeyboardInterrupt`` raised by a
    plugin. The traceback goes to stderr and the worker exits with status
    1. An ``error`` can also be the first message, before any ``header``, if
    the file cannot be read or no loader recognizes it.

A worker that dies without an ``error`` message (a segfault, ``os._exit()``
in a plugin, a signal) loses the rows still in its buffer: at most 64KB of
them, or ``--progress-interval`` seconds' worth. A segfault's Python
traceback goes to stderr (``faulthandler``).

Anything else - no ``end`` or ``error``, a row count that does not match,
a line that is not JSON, a non-zero exit after ``end`` - is a crash or a
protocol error, and the parent treats it as one.

``--memory-limit`` sets ``RLIMIT_AS`` (soft and hard) for this process
before any plugin is loaded, so a loader that tries to use more fails with
``MemoryError`` (reported as an ``error`` message if there is still enough
memory to do that) or is killed. Where no limit can be set (Windows has no
``resource`` module) the worker sends a ``log`` warning and carries on
without one. On Linux the worker also makes itself the
out-of-memory killer's first choice (``oom_score_adj`` 1000) and, given
``--parent-pid``, asks to be killed when the parent dies
(``PR_SET_PDEATHSIG``) - otherwise it notices when its next write to the
closed pipe fails.
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
# A row is sent as a sparse "row" message ("at" positions) instead of a row
# line when more than this many known columns are missing from it...
SPARSE_MIN_MISSING = 32
# ...and it has fewer than 1 / SPARSE_RATIO of them
SPARSE_RATIO = 3
# Exit statuses
EXIT_OK = 0
EXIT_ERROR = 1

_BYTES_TYPES = (bytes, bytearray, memoryview)
_NESTED_TYPES = (list, dict, tuple)
# loaders.NOTE_LOG_ATTRIBUTE: this module avoids importing the loaders (and
# their plugin machinery) until it has protected stdout
_NOTE_LOG_ATTRIBUTE = "datasette_import_note"


def _nested_text(value):
    # Exactly what the inserter stores for a list or dict (_storable() and
    # _text_form() in inserter.py)
    return json.dumps(value, default=str, ensure_ascii=False)


def _plain(values):
    """
    ``values`` with nested lists and dicts replaced by their JSON text (a
    new list), or ``values`` itself if there are none.
    """
    if any(isinstance(value, _NESTED_TYPES) for value in values):
        return [
            _nested_text(value) if isinstance(value, _NESTED_TYPES) else value
            for value in values
        ]
    return values


def _maybe_nested(line):
    # Without a "[" (after the row's own) or "{" in the encoded row, no value
    # can be a list or dict: keeps the check off the path of plain rows
    return line.find("[", 1) != -1 or line.find("{") != -1


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
        line = _ENCODER.encode(values)
    except (_BytesValue, ValueError):
        return _encode_row_slowly(_plain(values))
    if _maybe_nested(line):
        flat = _plain(values)
        if flat is not values:
            return encode_row(flat)
    return line


def _encode_row_slowly(values, at=None):
    """
    A ``row`` message for a row that cannot be a plain row line: it holds
    bytes, or an integer too long for ``int()``/``str()``. With ``at``, a
    sparse row (``values[i]`` is the value of column ``at[i]``).
    """
    values = list(values)
    positions = []
    for i, value in enumerate(values):
        if isinstance(value, _BYTES_TYPES):
            values[i] = base64.b64encode(bytes(value)).decode("ascii")
            positions.append(i)
        elif isinstance(value, int) and not isinstance(value, bool):
            try:
                str(value)
            except ValueError:
                # More digits than int() and str() allow (4,300 by default,
                # PYTHONINTMAXSTRDIGITS): send the text the inserter would
                # store for it - exactly where a row line would fail
                values[i] = _int_to_text(value)
    message = {"type": "row"}
    if at is not None:
        message["at"] = at
    message["values"] = values
    if positions:
        message["base64"] = positions
    return _NESTED_ENCODER.encode(message)


def _encode_sparse_row(at, values):
    values = _plain(values)
    try:
        return _ENCODER.encode({"type": "row", "at": at, "values": values})
    except (_BytesValue, ValueError):
        return _encode_row_slowly(values, at)


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
        # Time spent in write(): mostly blocked on a full pipe
        self.write_seconds = 0.0
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
                view = memoryview(data)
                start = time.perf_counter()
                # A raw write can be partial (interrupted by a signal)
                while view:
                    view = view[self.out.write(view) :]
                self.write_seconds += time.perf_counter() - start
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
            message = {
                "type": "log",
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            # A Note the loader yielded (split_schema() logs it): something
            # to show the person importing the file, not just a log line
            if getattr(record, _NOTE_LOG_ATTRIBUTE, None):
                message["note"] = True
            self.writer.message(message)
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
    "Returns a list of warnings to send once the header is out."
    warnings = []
    if memory_limit:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
        except (ImportError, ValueError, OSError) as ex:
            # No resource module on Windows; macOS may refuse RLIMIT_AS
            warnings.append(
                f"Could not limit the import worker's memory to "
                f"{memory_limit:,} bytes ({type(ex).__name__}: {ex}), "
                "running without a limit"
            )
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
    return warnings


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
        # key -> position in columns
        self.index = {}
        self.count = 0
        # True once a row had a list or dict value
        self.nested = False

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
        index = self.index
        keys = tuple(columns)
        count = self.count
        encode = _ENCODER.encode
        pending = writer.pending
        interval = self.progress_interval
        nested = self.nested
        last_progress = time.monotonic()
        try:
            for row in rows:
                line = None
                if tuple(row) == keys:
                    values = list(row.values())
                else:
                    new = [key for key in row if key not in index]
                    if new:
                        for key in new:
                            index[key] = len(columns)
                            columns.append(key)
                        writer.message({"type": "columns", "add": new})
                        keys = tuple(columns)
                    if tuple(row) == keys:
                        values = list(row.values())
                    else:
                        present = len(row)
                        missing = len(columns) - present
                        if (
                            missing > SPARSE_MIN_MISSING
                            and present * SPARSE_RATIO < len(columns)
                        ):
                            # Mostly missing: positions and values only
                            line = _encode_sparse_row(
                                [index[key] for key in row], list(row.values())
                            )
                        else:
                            values = [row.get(key) for key in columns]
                            # Trailing nulls are implied - unless this row
                            # added columns, which must reach the parent
                            # once even if they hold None, as they do in
                            # an in-process import
                            if not new:
                                while values and values[-1] is None:
                                    values.pop()
                if line is None:
                    if nested:
                        # This input has nested values: flatten first rather
                        # than encode a big geometry twice
                        values = _plain(values)
                    try:
                        line = encode(values)
                    except (_BytesValue, ValueError):
                        line = _encode_row_slowly(_plain(values))
                    else:
                        if not nested and _maybe_nested(line):
                            flat = _plain(values)
                            if flat is not values:
                                nested = self.nested = True
                                line = encode_row(flat)
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
    "``(cpu seconds, peak RSS in bytes or None)``."
    try:
        import resource
    except ImportError:
        # Windows
        return time.process_time(), None
    usage = resource.getrusage(resource.RUSAGE_SELF)
    max_rss = usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    return usage.ru_utime + usage.ru_stime, max_rss


def run(args, writer, warnings=()):
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
            loader = loaders_module.choose_loader(scores, first_bytes)
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
        for warning in warnings:
            writer.message(
                {
                    "type": "log",
                    "level": "WARNING",
                    "logger": __name__,
                    "message": warning,
                }
            )
        writer.flush()
        streamer = RowStreamer(writer, position, args.progress_interval)
        # Started before the loader produces anything, so progress (how far
        # it has read) keeps coming while a loader reads a whole file first
        flusher = _Flusher(writer, streamer, args.progress_interval)
        flusher.start()
        try:
            schema, rows = loaders_module.split_schema(loader.load(path), loader.name)
            if schema is not None:
                writer.message({"type": "schema", **schema.to_dict()})
            streamer.send(rows)
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
                "write_seconds": round(writer.write_seconds, 6),
            }
        )
        writer.flush()
        return EXIT_OK
    except BrokenPipeError:
        # The parent has gone away: nobody is listening
        raise
    except BaseException as ex:  # noqa: BLE001
        # Exception, but also SystemExit or KeyboardInterrupt from a plugin:
        # the rows it produced before that are still worth sending
        import traceback

        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        text = str(ex)
        if isinstance(ex, SystemExit):
            text = f"The loader called sys.exit({ex.code!r})"
        elif isinstance(ex, KeyboardInterrupt):
            text = "The worker was interrupted"
        message = {
            "type": "error",
            "error": type(ex).__name__,
            "message": text,
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
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    out = _protect_stdout()
    # A loader that segfaults leaves a Python traceback on stderr, which
    # the parent keeps for its error message
    import faulthandler

    faulthandler.enable()
    warnings = _limit_resources(args.memory_limit, args.parent_pid)
    writer = _Writer(out)
    try:
        return run(args, writer, warnings)
    except BrokenPipeError:
        # Don't try to flush anything on the way out
        os._exit(EXIT_ERROR)


if __name__ == "__main__":
    sys.exit(main())
