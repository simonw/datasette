"""
Import a file into a Datasette database through a worker subprocess.

:func:`import_file_via_worker` starts ``python -m datasette.imports.worker``
(see :mod:`datasette.imports.worker` for the protocol), reads the rows it
streams on stdout, and inserts them with
:func:`~datasette.imports.inserter.import_rows` - so every chunk goes
through ``db.execute_write_fn()``, where ``write_wrapper`` plugins see it,
and column types are detected here in the parent. A loader that crashes,
hangs or runs out of memory takes down the worker, not the server.

The worker's output is decoded on the event loop, a read (at most 256KB)
at a time, in time proportional to its size. A single huge value is the
exception: a 64MB string is parsed in one go, which holds the loop for
about 0.2s (an in-process import parses it in a thread, but holding the
GIL, for about 0.1s).

:func:`import_file_in_process` does the same import in a thread of this
process, with the same result and errors, for Pyodide (no subprocesses);
:func:`import_file` picks one.

The Engine boundary
-------------------

A background import (:class:`~datasette.imports.background.ImportRun`)
does not know how the rows reach its table. Once it holds a worker slot
and has checked that no table of that name exists, it calls ``load()`` on
the :class:`Engine` in its options::

    result = await engine.load(
        db, table, path, loader="csv", status=job.import_status,
        progress=on_progress, on_error="keep", sample_size=1000,
        batch_size=1000,
    )

What crosses the boundary:

- In: the database, the new table's name, a local file and the name of
  the loader that sniffing picked (or ``None``: the engine sniffs). Also
  ``status``, the import's own
  :class:`~datasette.imports.inserter.ImportStatus`, which the engine
  passes to :func:`~datasette.imports.inserter.import_rows` so that it
  stays the one record of what this import created, committed and
  dropped, even if ``load()`` raises or is cancelled. And ``progress``,
  called (and awaited, if it returns an awaitable) with an
  :class:`ImportProgress`.
- Out: a :class:`WorkerImportResult` (loader, notes, widenings, size,
  timings). If the loader or the worker failed, ``load()`` raises
  :class:`WorkerImportError` instead, with the same result attached. Any
  other exception - a failed write, a progress callback that raised - and
  a cancellation go on as they are. Whichever way ``load()`` ends, the
  write that was running has settled and ``on_error`` has been applied to
  the table first, with one exception: cancelled a second time while
  ``import_rows()`` cleans up after the first (a cancel request, then
  Datasette shutting down), it raises at once, and the clean up carries on
  in the background - a write still running may yet commit, and with
  ``on_error="drop"`` the table be dropped.
  :meth:`~datasette.imports.inserter.ImportStatus.wait_settled` on
  ``status`` waits until it says what the clean up did, as a background
  import does before it looks at its table.

There are two engines:

- :class:`WorkerEngine`, the default (:func:`default_engine`), starts
  ``python -m datasette.imports.worker`` as its :class:`WorkerOptions` say
  (plugins directory, memory limit, progress interval, how long it may
  take to stop, environment). It runs the worker inside ``async with
  WorkerProcess(...)``, which never lets the worker outlive the block: any
  exception kills the worker's process group and reaps it.
  ``_WorkerStream`` turns what the worker writes into rows for
  ``import_rows()``, and says how the worker ended: an error it reported,
  killed by a signal, crashed, truncated output or bad output.
- :class:`InProcessEngine` loads the file in a thread of this process,
  with the same results and errors, for Pyodide and anywhere else a
  subprocess cannot be started.

Neither engine waits for a worker slot: the background import holds one of
:func:`~datasette.imports.background.worker_slots` around ``load()``. So
:func:`import_file_via_worker`, :func:`import_file_in_process` and
:func:`import_file`, thin wrappers over the two engines for direct callers,
start their worker at once.

What happens to rows when an import fails part way through
------------------------------------------------------------

Rows are committed a chunk at a time while they arrive, so people can query
the table during the import. If the *worker* fails (a loader error, a
crash, being killed, a malformed or truncated stream), every row the parent
*received* is still inserted and the import is finished normally (types
settled, temporary state dropped), so the table holds exactly the first N
rows of the file, and the error says N. On a loader error, ``SystemExit``
or ``MemoryError`` the worker sends every row it produced before saying so;
a worker that dies without a word (a segfault, ``os._exit()``, SIGKILL)
takes the rows still in its buffer with it - at most 64KB, or the progress
interval's worth - so N can be lower than the row the loader reached.
Then :class:`WorkerImportError` is raised. With ``on_error="keep"`` (the
default) that table stays; ``on_error="drop"`` drops it instead. A failed
*insert* in the parent (a ``write_wrapper`` plugin raising, a full disk)
kills the worker and re-raises that exception; cancellation kills the
worker and re-raises ``CancelledError``. In both cases
:func:`~datasette.imports.inserter.import_rows` keeps the chunks already
committed, completing the table (or drops it, with ``on_error="drop"``).

Why the parent does the writes
------------------------------

The alternative, the worker inserting into the database file itself, was
1.1-1.4 times faster on a 1GB CSV across three sets of runs (120,000 rows/s
against 97,000-99,000) and left the server's event loop alone, but it
bypasses ``write_wrapper`` plugins, events and the write queue, and makes
the worker a second process writing to the file: a widening that rebuilt a
6M-row table held SQLite's write lock for 12s, and the server's own writes
to that file failed with "database is locked" after their 5s busy timeout
(through the parent the same writes queued instead). It would also be a
second write path next to the in-process one that Pyodide (no subprocesses)
needs anyway. Writing in the parent is still faster than loading
in-process (82,000-84,000 rows/s), and keeps loaders that hold the GIL for
a long time out of the server: ``json.load()`` of a whole 27MB file stalled
the event loop for 150-200ms in-process.
"""

import asyncio
import base64
import contextlib
import dataclasses
import functools
import inspect
import json.decoder
import json.scanner
import logging
import os
import signal
import sys
import time
from typing import Protocol

from datasette.imports.inserter import (
    ON_ERROR,
    ImportResult,
    ImportStatus,
    import_rows,
)
from datasette.imports.loaders import NOTE_LOG_ATTRIBUTE

logger = logging.getLogger(__name__)

WORKER_MODULE = "datasette.imports.worker"
PROTOCOL_VERSION = 1
# Bytes read from the worker's stdout at a time: a bigger read means fewer,
# longer json.loads() calls on the event loop
READ_SIZE = 256 * 1024
# How much of the end of the worker's stderr to keep for error messages
STDERR_TAIL = 8 * 1024
# Size of the pipe the worker writes rows to, where the system allows it
# (Linux: up to /proc/sys/fs/pipe-max-size, 1MB by default). See _grow_pipe()
PIPE_SIZE = 1024 * 1024

# The C scanner json.loads() uses, called directly: it parses one value at
# a position in a string and says where the value ended
_SCAN = json.scanner.make_scanner(json.decoder.JSONDecoder())
# Windows has neither: WorkerProcess falls back to Process.kill()/terminate()
_SIGKILL = getattr(signal, "SIGKILL", None)
_PROCESS_GROUPS = hasattr(os, "killpg") and os.name == "posix"


class ProtocolError(Exception):
    """The worker's output does not follow the protocol."""

    def __init__(self, message, messages=None):
        super().__init__(message)
        # Messages decoded successfully before the bad line
        self.messages = messages or []


class WorkerImportError(Exception):
    """
    The worker failed. ``kind`` is one of:

    - ``"error"``: the worker reported an error (``error_type`` is the
      exception's class name, for example ``LoaderError``, ``EmptyFile``,
      ``MemoryError``)
    - ``"killed"``: the worker was killed by a signal (``signal``)
    - ``"crashed"``: the worker exited with a non-zero status and no error
    - ``"truncated"``: the output stopped without the ``end`` message
    - ``"protocol"``: the output was not valid protocol (the worker was
      killed)

    ``result`` is the :class:`WorkerImportResult` for the rows that were
    imported before the failure, ``stderr`` the end of the worker's stderr.
    """

    def __init__(
        self,
        message,
        *,
        kind,
        result=None,
        error_type=None,
        returncode=None,
        stderr="",
    ):
        super().__init__(message)
        self.kind = kind
        self.result = result
        self.error_type = error_type
        self.returncode = returncode
        self.stderr = stderr

    @property
    def signal(self):
        if self.returncode is not None and self.returncode < 0:
            return -self.returncode
        return None


@dataclasses.dataclass
class ImportProgress:
    "Passed to the ``progress`` callback of :func:`import_file_via_worker`."

    table: str
    loader: str | None
    #: Rows received from the worker
    rows_read: int
    #: Rows committed to the table
    rows_written: int
    #: How far the worker's loader has read into the file, and its size
    bytes: int | None
    size: int | None
    #: The worker's process ID
    pid: int | None = None
    #: Rows (by rowid range) whose stored numbers still have to become text
    #: after a column widened to text, see
    #: :attr:`~datasette.imports.inserter.ImportStatus.backlog`
    backlog: int = 0
    #: True once every row is in the table: what is left is converting the
    #: :attr:`backlog` and finishing the table
    finishing: bool = False
    #: The messages of the :class:`~datasette.imports.loaders.Note` items
    #: the loader has yielded so far: things the person importing the file
    #: should know about how it was read
    notes: list = dataclasses.field(default_factory=list)

    @property
    def fraction(self):
        "How much of the file has been read, from 0.0 to 1.0, if known."
        if self.bytes is None or not self.size:
            return None
        return min(self.bytes / self.size, 1.0)


@dataclasses.dataclass
class WorkerImportResult:
    "What :func:`import_file_via_worker` returns (or attaches to its error)."

    table: str
    loader: str | None
    #: Rows in the table
    rows: int
    #: Rows received from the worker
    rows_read: int
    #: Rows committed to the table - the same as ``rows`` unless the table
    #: was dropped after a failure
    rows_written: int
    #: True if the worker finished cleanly and every row was inserted
    complete: bool
    #: False if no table was created, or it was dropped after an error
    table_exists: bool
    schema: dict | None
    import_result: ImportResult | None
    size: int | None
    seconds: float
    #: The worker's ``end`` message: its rows, seconds, cpu, max_rss
    worker: dict | None
    returncode: int | None
    #: The loader's notes, see :attr:`ImportProgress.notes`
    notes: list
    #: Other warnings that the loader, a plugin or the worker logged
    warnings: list
    #: Parent-side timings: decode_seconds (json.loads and building
    #: dicts), write_seconds (inside execute_write_fn), bytes_read
    stats: dict

    @classmethod
    def from_status(cls, status: ImportStatus, **fields) -> "WorkerImportResult":
        """
        The result of an import whose
        :func:`~datasette.imports.inserter.import_rows` call kept ``status``
        up to date: the rows in the table and committed, and whether the
        table is there, are what it says. ``fields`` are the others.
        """
        return cls(
            rows=status.rows if status.table_exists else 0,
            rows_written=status.rows,
            table_exists=status.table_exists,
            **fields,
        )


class ProtocolDecoder:
    """
    Turns the bytes a worker writes into messages, checking the protocol.
    Holds the protocol state: the header, schema and column list.

    :meth:`feed` returns a list of ``(kind, payload)`` pairs, where ``kind``
    is ``"rows"`` or a control message type (payload: the message).
    Consecutive rows come as one ``"rows"`` pair, whose payload is a list of
    rows: each a list of values in the order of :attr:`columns` - shorter
    than :attr:`columns` if its last values are missing, or if columns were
    added after it - or, for a sparse row, a dictionary. That is what
    :func:`~datasette.imports.inserter.import_rows` takes with
    ``columns=decoder.columns``, without a dictionary built per row.

    Each line is parsed on its own with the C JSON scanner, which must end
    exactly at the line's newline: a line is one JSON value or the stream
    is rejected, so line boundaries can never be read differently. The
    incomplete last line of a read is kept as a list of pieces and joined
    once its newline arrives, so a long line costs time in proportion to
    its length, however many reads it takes.
    """

    def __init__(self):
        self.header = None
        self.schema = None
        self.columns = []
        self.rows = 0
        self.lines = 0
        self.finished = None
        self._pieces = []

    def feed(self, data):
        end = data.rfind(b"\n") + 1
        if not end:
            if data:
                self._pieces.append(data)
            return []
        if self._pieces:
            self._pieces.append(data[:end])
            complete = b"".join(self._pieces)
            self._pieces = []
        else:
            complete = data[:end] if end < len(data) else data
        if end < len(data):
            self._pieces.append(data[end:])
        out = []
        bad_encoding = None
        try:
            text = complete.decode("utf-8", "surrogatepass")
        except UnicodeDecodeError as ex:
            # Decode the lines before the bad one, then reject it
            good = complete.rfind(b"\n", 0, ex.start) + 1
            text = complete[:good].decode("utf-8", "surrogatepass")
            bad_encoding = ex
        try:
            self._decode(text, out)
            if bad_encoding is not None:
                raise ProtocolError(
                    f"Line {self.lines + 1} of the worker's output is not "
                    f"valid UTF-8: {bad_encoding}"
                )
        except ProtocolError as ex:
            ex.messages = out
            raise
        return out

    def close(self):
        """
        Call at the end of the stream. Returns the incomplete last line, if
        any (the worker died part way through writing it).
        """
        partial = b"".join(self._pieces)
        self._pieces = []
        return partial

    def _decode(self, text, out):
        # ``text`` is a run of complete lines, each ending with "\n"
        self._run = None
        try:
            self._decode_lines(text, out)
        finally:
            # Rows before a bad line are good: hand them over too
            if self._run:
                self.rows += len(self._run)
                out.append(("rows", self._run))
            self._run = None

    def _decode_lines(self, text, out):
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
                raise ProtocolError(
                    f"Line {self.lines} of the worker's output is not a row "
                    f"or a message: {text[pos : pos + 60]!r}"
                )
            start = pos
            try:
                value, pos = scan(text, pos)
            except StopIteration:
                raise ProtocolError(
                    f"Line {self.lines} of the worker's output is not valid JSON"
                ) from None
            except ValueError as ex:
                raise ProtocolError(
                    f"Line {self.lines} of the worker's output is not valid JSON: {ex}"
                ) from None
            # Exactly one JSON value per line: it must end at the newline,
            # and must not have used a newline as whitespace inside it
            if text[pos] != "\n":
                if text[pos] == "\r" and text[pos + 1] == "\n":
                    # Tolerate \r\n, should Windows translate newlines
                    pos += 1
                else:
                    raise ProtocolError(
                        f"Line {self.lines} of the worker's output has extra "
                        f"data after its JSON value: {text[pos : pos + 60]!r}"
                    )
            if find("\n", start, pos) != -1:
                raise ProtocolError(
                    f"Line {self.lines} of the worker's output is not a "
                    "complete JSON value"
                )
            pos += 1
            if first == "[":
                # A JSON value that starts with "[" is a list
                if rows is None:
                    self._check_can_send_rows(self.lines)
                    rows = self._run = []
                if len(value) > len(columns):
                    raise ProtocolError(
                        f"Line {self.lines}: a row has more values than the "
                        f"{len(columns)} columns announced"
                    )
                rows.append(value)
                continue
            if value.get("type") == "row":
                # A row with bytes, or a sparse row: part of the same run
                if rows is None:
                    self._check_can_send_rows(self.lines)
                    rows = self._run = []
                rows.append(self._row_message(value))
                continue
            if rows is not None:
                self.rows += len(rows)
                out.append(("rows", rows))
                rows = self._run = None
            message = self._control(value)
            if message is not None:
                out.append(message)
        # _decode() hands over the last run of rows

    def _check_can_send_rows(self, line_number):
        if self.header is None:
            raise ProtocolError(f"Line {line_number}: a row before the header")
        if self.finished is not None:
            raise ProtocolError(f"Line {line_number}: a row after the end")

    def _control(self, message):
        if not isinstance(message.get("type"), str):
            raise ProtocolError(f"Line {self.lines}: a message needs a type")
        kind = message["type"]
        if self.header is None:
            if kind == "header":
                if message.get("protocol") != PROTOCOL_VERSION:
                    raise ProtocolError(
                        f"Unsupported worker protocol {message.get('protocol')!r}, "
                        f"expected {PROTOCOL_VERSION}"
                    )
                self.header = message
                return ("header", message)
            if kind == "error":
                self.finished = message
                return ("error", message)
            if kind == "log":
                # Logged while choosing the loader, such as a plugin whose
                # register_import_loaders() failed
                return ("log", message)
            raise ProtocolError(f"Line {self.lines}: expected the header, got {kind!r}")
        if self.finished is not None:
            raise ProtocolError(f"Line {self.lines}: {kind!r} after the end")
        if kind == "schema":
            if self.schema is not None or self.rows:
                raise ProtocolError(
                    f"Line {self.lines}: a schema must come once, before any row"
                )
            if not isinstance(message.get("columns"), dict):
                raise ProtocolError(f"Line {self.lines}: schema needs columns")
            self.schema = message
            return ("schema", message)
        if kind == "columns":
            add = message.get("add")
            if not isinstance(add, list):
                raise ProtocolError(f"Line {self.lines}: columns needs a list")
            self.columns.extend(add)
            return None
        if kind in ("end", "error"):
            self.finished = message
            return (kind, message)
        if kind == "header":
            raise ProtocolError(f"Line {self.lines}: a second header")
        # progress, log - and types added later, which old parents ignore
        return (kind, message)

    def _row_message(self, message):
        values = message.get("values")
        at = message.get("at")
        positions = message.get("base64") or []
        columns = self.columns
        if type(values) is not list or len(values) > len(columns):
            raise ProtocolError(f"Line {self.lines}: bad row message")
        try:
            for position in positions:
                values[position] = base64.b64decode(values[position], validate=True)
        except (TypeError, ValueError, IndexError) as ex:
            raise ProtocolError(f"Line {self.lines}: bad base64 value: {ex}") from None
        if at is None:
            return values
        # Sparse: values[i] is the value of column at[i]
        try:
            if (
                type(at) is not list
                or len(at) != len(values)
                or len(set(at)) != len(at)
                or (at and min(at) < 0)
            ):
                raise ValueError
            return {columns[p]: value for p, value in zip(at, values)}
        except (TypeError, ValueError, IndexError):
            raise ProtocolError(
                f"Line {self.lines}: bad column positions in a row message"
            ) from None


def worker_command(
    path,
    *,
    format=None,
    plugins_dir=None,
    memory_limit=None,
    progress_interval=None,
    extra_args=(),
):
    """The command line that runs the worker for ``path``."""
    # -P: don't put the current directory on sys.path, where a json.py
    # would shadow the standard library
    argv = [sys.executable, "-P", "-m", WORKER_MODULE]
    if format:
        argv += ["--format", format]
    if plugins_dir:
        argv += ["--plugins-dir", os.fspath(plugins_dir)]
    if memory_limit:
        argv += ["--memory-limit", str(int(memory_limit))]
    if progress_interval is not None:
        argv += ["--progress-interval", str(progress_interval)]
    argv += ["--parent-pid", str(os.getpid())]
    # The path goes after "--", so "-data.csv" is not read as an option
    return argv + list(extra_args) + ["--", os.fspath(path)]


def _grow_pipe(process, size):
    """
    Make the worker's stdout pipe hold ``size`` bytes (Linux only). asyncio
    reads a pipe once per event loop iteration, and a Linux pipe holds 64KB
    by default: about 400 rows of a typical CSV. While the server is busy
    answering requests an iteration can take tens of milliseconds, so the
    import could take only 64KB per iteration. With 1MB it takes up to
    asyncio's 256KB. With the event loop blocked for 10ms per iteration
    (GIL free), 100,000 rows went from 16,500 to 37,000 rows/s. On a real
    server under four clients requesting without pause it made no
    measurable difference: a 1M row import took 40.3, 41.6 and 44.4s with
    64KB against 42.1, 43.5 and 45.6s with 1MB, runs interleaved. There the
    import is held up by the GIL - the write thread and the pages being
    rendered take turns - not by the pipe. It costs 1MB of kernel memory
    per worker.
    """
    if not size:
        return
    try:
        import fcntl

        transport = process._transport.get_pipe_transport(1)
        pipe = transport.get_extra_info("pipe")
        fcntl.fcntl(pipe.fileno(), fcntl.F_SETPIPE_SZ, size)
    except (ImportError, AttributeError, OSError, ValueError):
        # Not Linux, or more than /proc/sys/fs/pipe-max-size allows
        pass


class WorkerProcess:
    """
    A running worker subprocess. Reads its stdout through a
    :class:`ProtocolDecoder` and keeps the last ``STDERR_TAIL`` bytes of its
    stderr, read concurrently so a chatty worker can never block on a full
    stderr pipe.

    Use it with ``async with``: the worker never outlives the block. A block
    that ends normally waits for the worker to exit, as it does after its
    last message, and reaps it (:meth:`stop`). Any exception - a failed
    insert, a progress callback that raised, a cancellation, including one
    that lands while it waits for the worker to exit - kills the worker's
    process group and reaps it, shielded from being cancelled again, before
    the exception goes on.
    """

    def __init__(self, argv, *, env=None, read_size=READ_SIZE, stop_timeout=2.0):
        self.argv = list(argv)
        self.env = env
        self.read_size = read_size
        #: Seconds :meth:`stop` waits for the worker to exit before
        #: terminating it, and then before killing it
        self.stop_timeout = stop_timeout
        self.process = None
        self.decoder = ProtocolDecoder()
        self.aborted = False
        self.eof = False
        self.bytes_read = 0
        self.decode_seconds = 0.0
        self.partial_line = b""
        self._stderr = bytearray()
        self._stderr_task = None

    @property
    def pid(self):
        return self.process.pid if self.process else None

    @property
    def returncode(self):
        return self.process.returncode if self.process else None

    @property
    def stderr(self):
        return self._stderr.decode("utf-8", "replace")

    async def __aenter__(self):
        try:
            await self.start()
        except BaseException:
            await self._kill_and_reap()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                # It has sent its last message: wait for it to exit
                await self.stop()
            except BaseException:
                # Cancelled while it exited
                await self._kill_and_reap()
                raise
            return False
        # A failed insert, a progress callback that raised, a cancellation:
        # import_rows() has cleaned up already, keeping or dropping the table
        await self._kill_and_reap()
        return False

    async def _kill_and_reap(self):
        self.abort()
        await asyncio.shield(self.stop())

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            # Its own process group: Ctrl+C in a terminal reaches the server,
            # which stops the worker, and stop() can kill anything it started
            start_new_session=True,
            limit=self.read_size,
        )
        _grow_pipe(self.process, PIPE_SIZE)
        self._stderr_task = asyncio.ensure_future(self._drain_stderr())
        return self

    async def _drain_stderr(self):
        stream = self.process.stderr
        while True:
            data = await stream.read(65536)
            if not data:
                return
            self._stderr += data
            if len(self._stderr) > STDERR_TAIL:
                del self._stderr[: len(self._stderr) - STDERR_TAIL]

    async def read_messages(self):
        """
        Read the next chunk of stdout and return the messages in it: an
        empty list if it held no complete line, ``None`` at the end of the
        stream. Raises :class:`ProtocolError` for bad output.
        """
        if self.eof:
            return None
        data = await self.process.stdout.read(self.read_size)
        if not data or self.aborted:
            self.eof = True
            self.partial_line = self.decoder.close()
            return None
        self.bytes_read += len(data)
        start = time.perf_counter()
        try:
            return self.decoder.feed(data)
        finally:
            self.decode_seconds += time.perf_counter() - start

    def _signal(self, kill):
        """
        Send SIGKILL (``kill=True``) or SIGTERM to the worker's process group,
        in case a plugin started processes of its own. Where there are no
        process groups or no SIGKILL (Windows), ``Process.kill()`` and
        ``terminate()`` both call TerminateProcess.
        """
        process = self.process
        if process is None or process.returncode is not None:
            return
        signum = _SIGKILL if kill else signal.SIGTERM
        if _PROCESS_GROUPS and signum is not None:
            try:
                os.killpg(process.pid, signum)
                return
            except ProcessLookupError:
                # Nothing left in the group: the worker has been reaped
                return
            except PermissionError:
                pass
        try:
            if kill:
                process.kill()
            else:
                process.terminate()
        except ProcessLookupError:
            pass

    def abort(self):
        """
        Stop reading - rows not yet taken are dropped - and kill the worker
        now. Safe to call more than once.
        """
        self.aborted = True
        self._signal(kill=True)

    def kill(self):
        "Kill the worker now, but let the rows already decoded be used."
        self._signal(kill=True)

    async def stop(self, timeout=None):
        """
        Make sure the worker is gone and reaped: wait ``timeout`` seconds
        (by default :attr:`stop_timeout`) for it to exit, terminate it, wait
        again, then kill it.
        """
        if self.process is None:
            return
        if timeout is None:
            timeout = self.stop_timeout
        try:
            if self.process.returncode is None:
                try:
                    await asyncio.wait_for(self.process.wait(), timeout)
                except TimeoutError:
                    self._signal(kill=False)
                    try:
                        await asyncio.wait_for(self.process.wait(), timeout)
                    except TimeoutError:
                        self._signal(kill=True)
                        await self.process.wait()
            if self._stderr_task is not None:
                try:
                    await asyncio.wait_for(self._stderr_task, timeout)
                except Exception:  # noqa: BLE001
                    self._stderr_task.cancel()
        finally:
            transport = getattr(self.process, "_transport", None)
            if transport is not None:
                transport.close()


class _WatchedDatabase:
    """
    Passes everything through to a Database, but calls ``on_failure`` when
    a write fails or is cancelled - so the worker is stopped at once, not
    after import_rows() has cleaned up - and times the writes.
    """

    def __init__(self, db, on_failure):
        self._db = db
        self._on_failure = on_failure
        self.writes = 0
        self.write_seconds = 0.0

    def __getattr__(self, name):
        return getattr(self._db, name)

    async def execute_write_fn(self, fn, *args, **kwargs):
        @functools.wraps(fn)
        def timed(conn):
            start = time.perf_counter()
            try:
                return fn(conn)
            finally:
                self.write_seconds += time.perf_counter() - start

        try:
            result = await self._db.execute_write_fn(timed, *args, **kwargs)
        except BaseException:
            self._on_failure()
            raise
        self.writes += 1
        return result


class _WorkerFailed(Exception):
    """
    Raised by :meth:`_WorkerStream.rows` after the last row of a worker
    that failed, so that import_rows() drops the table (``on_error="drop"``).
    """


class _WorkerStream:
    """
    One worker's output, read into
    :func:`~datasette.imports.inserter.import_rows` by :meth:`import_into`,
    and how the worker ended (:meth:`failure`).
    """

    def __init__(self, worker, table, progress, status, raise_on_failure):
        self.worker = worker
        self.table = table
        self.progress = progress
        #: The ImportStatus of the import_rows() call
        self.status = status
        self.raise_on_failure = raise_on_failure
        self.loader = None
        self.schema = None
        self.size = None
        self.bytes = None
        self.rows_read = 0
        self.rows_written = 0
        self.trailer = None
        self.error_message = None
        self.protocol_error = None
        self.notes = []
        self.warnings = []
        self.done = False
        self._ready = []

    async def _report(self):
        if self.progress is None:
            return
        outcome = self.progress(
            ImportProgress(
                table=self.table,
                loader=self.loader,
                rows_read=self.rows_read,
                rows_written=self.rows_written,
                bytes=self.bytes,
                size=self.size,
                pid=self.worker.pid,
                backlog=self.status.backlog,
                finishing=self.status.finishing,
                notes=list(self.notes),
            )
        )
        if inspect.isawaitable(outcome):
            await outcome

    async def _handle(self, messages):
        report = False
        for kind, payload in messages:
            if kind == "rows":
                if self._ready:
                    self._ready.extend(payload)
                else:
                    self._ready = payload
                self.rows_read += len(payload)
            elif kind == "progress":
                self.bytes = payload.get("bytes")
                report = True
            elif kind == "header":
                self.loader = payload.get("loader")
                self.size = payload.get("size")
                self.bytes = 0
            elif kind == "schema":
                self.schema = payload["columns"]
            elif kind == "log":
                text = payload.get("message", "")
                extra = None
                if payload.get("note"):
                    self.notes.append(text)
                    report = True
                    # Marked as the loader marks it, see split_schema()
                    extra = {NOTE_LOG_ATTRIBUTE: self.loader or "loader"}
                else:
                    self.warnings.append(text)
                logger.warning("Import worker: %s", text, extra=extra)
            elif kind == "end":
                self.trailer = payload
                self.bytes = payload.get("bytes", self.bytes)
                self.done = True
                report = True
            elif kind == "error":
                self.error_message = payload
                self.done = True
        if report:
            await self._report()

    async def _read(self):
        "Read the next messages; False at the end of the stream."
        try:
            messages = await self.worker.read_messages()
        except ProtocolError as ex:
            # Rows before the bad line are good: insert those
            self.worker.kill()
            self.protocol_error = ex
            self.done = True
            await self._handle(ex.messages)
            return False
        if messages is None:
            self.done = True
            return False
        await self._handle(messages)
        return True

    async def fill(self):
        "Read until rows are ready or the stream is over."
        while not self._ready and not self.done:
            if not await self._read():
                return

    async def start(self):
        """
        Read up to the first rows, so the header - and the schema, which
        comes before any row - are known.
        """
        while not self.done and not self._ready and self.schema is None:
            if not await self._read():
                return

    async def import_into(self, db, *, sample_size, batch_size, on_error):
        """
        Insert the rows into a new table with import_rows() as they arrive,
        returning its :class:`~datasette.imports.inserter.ImportResult`:
        ``None`` if the worker failed before its header, or failed with
        ``raise_on_failure`` set (import_rows() then dropped the table).
        """
        await self.start()
        if self.worker.decoder.header is None:
            # An error (or nothing at all) before the header
            while not self.done:
                await self.fill()
            return None
        rows = self.rows()
        try:
            return await import_rows(
                db,
                self.table,
                rows,
                # The decoder's own list, which grows as columns are
                # announced: rows are lists of values in this order
                columns=self.worker.decoder.columns,
                schema=self.schema,
                sample_size=sample_size,
                batch_size=batch_size,
                progress=self.written,
                on_error=on_error,
                status=self.status,
            )
        except _WorkerFailed:
            # import_rows() dropped the table, if there was one
            return None
        finally:
            with contextlib.suppress(RuntimeError):
                await rows.aclose()

    async def rows(self):
        """
        The rows as they arrive, positional (see :class:`ProtocolDecoder`).
        With ``raise_on_failure``, raises :class:`_WorkerFailed` after the
        last one if the worker failed.
        """
        worker = self.worker
        while True:
            if self._ready:
                batch, self._ready = self._ready, []
                for row in batch:
                    if worker.aborted:
                        return
                    yield row
            elif worker.aborted:
                return
            elif self.done:
                # Whether it failed is known before its exit status is
                if self.raise_on_failure and self.failure() is not None:
                    raise _WorkerFailed()
                return
            else:
                await self.fill()

    async def written(self, rows_written):
        self.rows_written = rows_written
        try:
            await self._report()
        except BaseException:
            # Stop the worker now rather than once import_rows() has
            # finished cleaning up
            self.worker.abort()
            raise

    def failure(self, returncode=None, memory_limit=None):
        """
        ``(kind, message, error_type)`` describing how the worker failed,
        or None if it finished cleanly and sent every row. Whether it failed
        is known once its output has ended; the kind of failure of a worker
        that stopped without a word depends on its exit status,
        ``returncode``.
        """
        worker = self.worker
        if self.protocol_error is not None:
            return "protocol", str(self.protocol_error), None
        if self.error_message is not None:
            message = self.error_message
            text = message.get("message") or message.get("error", "error")
            return "error", text, message.get("error")
        if self.trailer is not None:
            if self.trailer.get("rows") != self.rows_read:
                message = (
                    f"The worker sent {self.rows_read} rows but its end "
                    f"message says {self.trailer.get('rows')}"
                )
                return "protocol", message, None
            if returncode not in (0, None):
                self.warnings.append(
                    f"The worker exited with status {returncode} after finishing"
                )
            return None
        partial = " (its last line was cut off)" if worker.partial_line else ""
        hint = ""
        if memory_limit:
            hint = f" - it may have run out of memory (limit {memory_limit:,} bytes)"
        sent = f"{self.rows_read} rows{partial}"
        if returncode is not None and returncode < 0:
            try:
                name = signal.Signals(-returncode).name
            except ValueError:
                name = f"signal {-returncode}"
            message = f"The import worker was killed by {name} after sending {sent}"
            return "killed", message + hint, None
        if returncode:
            message = (
                f"The import worker exited with status {returncode} after "
                f"sending {sent}"
            )
            return "crashed", message + hint, None
        message = (
            f"The import worker's output ended without an end message after {sent}"
        )
        return "truncated", message, None


def subprocesses_available():
    "False where a worker cannot be started: Pyodide (emscripten), WASI."
    return sys.platform not in ("emscripten", "wasi")


# Engines ----------------------------------------------------------------------


class Engine(Protocol):
    """
    How the rows of a file get into a new table: :class:`WorkerEngine`, in
    a worker subprocess, or :class:`InProcessEngine`, in a thread of this
    process. :func:`default_engine` picks one for this platform.
    """

    async def load(
        self,
        db,
        table: str,
        path: str,
        *,
        loader: str | None,
        status: ImportStatus,
        progress,
        on_error: str,
        sample_size: int,
        batch_size: int,
    ) -> WorkerImportResult:
        """
        Create ``table`` in ``db`` (``ValueError`` if it exists) and insert
        the rows of the file at ``path``, read by the loader named
        ``loader`` (or the one sniffing picks), keeping ``status`` up to
        date. Returns a :class:`WorkerImportResult`; raises
        :class:`WorkerImportError` if the loader or the worker fails. Once
        it raises, for that or any other failure or a cancellation, the
        write that was running has settled and ``on_error`` has been
        applied to the table - unless it was cancelled again while
        :func:`~datasette.imports.inserter.import_rows` cleaned up after
        the first cancellation. That clean up then carries on in the
        background, and
        :meth:`~datasette.imports.inserter.ImportStatus.wait_settled` on
        ``status`` waits for it. The other parameters are those of
        :func:`import_file_via_worker`.
        """
        ...


@dataclasses.dataclass(frozen=True)
class WorkerOptions:
    """
    How a :class:`WorkerEngine` starts its worker, all in one place: see
    :func:`import_file_via_worker` for what each option does.
    """

    #: A directory of plugins for the worker to load: ``None`` for the
    #: Datasette instance's own ``plugins_dir``, ``False`` for none
    plugins_dir: str | bool | None = None
    #: Address space limit (``RLIMIT_AS``) for the worker, in bytes
    memory_limit: int | None = None
    #: Seconds between the worker's progress messages
    progress_interval: float | None = None
    #: Seconds to wait for the worker to exit before terminating it, and
    #: then before killing it
    stop_timeout: float = 2.0
    #: Environment variables for the worker
    env: dict | None = None
    #: Run this command instead of the worker - a test hook, for stand-ins
    #: that misbehave in ways a real worker cannot be made to
    argv: list | None = None

    def command(self, path, *, format=None, ds=None) -> list:
        "The command line that runs the worker for ``path``, for ``ds``."
        if self.argv is not None:
            return list(self.argv)
        plugins_dir = self.plugins_dir
        if plugins_dir is None:
            plugins_dir = getattr(ds, "plugins_dir", None)
        return worker_command(
            path,
            format=format,
            plugins_dir=plugins_dir or None,
            memory_limit=self.memory_limit,
            progress_interval=self.progress_interval,
        )

    def environment(self) -> dict | None:
        "The worker's environment variables (``None``: this process's)."
        if (
            self.memory_limit
            and self.env is None
            and "MALLOC_ARENA_MAX" not in os.environ
        ):
            # RLIMIT_AS counts address space glibc reserves but may never
            # use: 64MB per thread for its own malloc arena. One arena is
            # plenty for the worker's two threads
            return {**os.environ, "MALLOC_ARENA_MAX": "1"}
        return self.env


class WorkerEngine:
    """
    Loads the file in a worker subprocess, started as ``options`` (a
    :class:`WorkerOptions`) say: what :func:`import_file_via_worker` does.
    The default where subprocesses can be started.
    """

    def __init__(self, options: WorkerOptions | None = None):
        self.options = options if options is not None else WorkerOptions()

    def __repr__(self):
        return f"WorkerEngine({self.options!r})"

    async def load(
        self,
        db,
        table,
        path,
        *,
        loader,
        status,
        progress,
        on_error,
        sample_size,
        batch_size,
    ):
        "See :meth:`Engine.load`."
        if on_error not in ON_ERROR:
            raise ValueError(f"on_error must be one of {ON_ERROR}")
        if await db.table_exists(table):
            raise ValueError(f"Table {table!r} already exists")
        options = self.options
        return await _import_via_worker(
            db,
            table,
            options.command(path, format=loader, ds=getattr(db, "ds", None)),
            env=options.environment(),
            status=status,
            sample_size=sample_size,
            batch_size=batch_size,
            progress=progress,
            on_error=on_error,
            memory_limit=options.memory_limit,
            stop_timeout=options.stop_timeout,
        )


def default_engine() -> Engine:
    """
    A :class:`WorkerEngine`, or where a worker cannot be started (Pyodide)
    an :class:`InProcessEngine`.
    """
    return WorkerEngine() if subprocesses_available() else InProcessEngine()


async def import_file_via_worker(
    db,
    table,
    path,
    *,
    format=None,
    plugins_dir=None,
    sample_size=1000,
    batch_size=1000,
    progress=None,
    on_error="keep",
    memory_limit=None,
    progress_interval=None,
    stop_timeout=2.0,
    _argv=None,
    env=None,
    status=None,
):
    """
    Import the file at ``path`` into a new ``table`` in ``db`` (a Datasette
    :class:`~datasette.database.Database`), loading it in a worker
    subprocess.

    :param format: use the loader with this name instead of sniffing
    :param plugins_dir: a directory of plugins for the worker to load, as
        ``datasette --plugins-dir`` does (entry point plugins always load).
        By default the Datasette instance's own ``plugins_dir`` (from
        ``--plugins-dir`` or a configuration directory), so the worker sees
        the same loaders as the server; ``False`` for none
    :param sample_size: rows used to detect column types
    :param batch_size: rows per ``execute_write_fn()`` call after the sample
    :param progress: called (and awaited, if it returns an awaitable) with an
        :class:`ImportProgress` after each committed chunk, each slice of
        converting stored numbers to text once every row is in (see
        :func:`~datasette.imports.inserter.import_rows`), each progress
        message from the worker and each note from the loader
    :param on_error: ``"keep"`` the rows imported before a failure, or
        ``"drop"`` the table
    :param memory_limit: address space limit (``RLIMIT_AS``) for the worker,
        in bytes. It counts reserved address space, not memory used: the
        JSON loader, which parses a whole file at once, needs several times
        the file's size. With a limit, the worker runs with
        ``MALLOC_ARENA_MAX=1`` (unless ``env`` is given), because glibc's
        per-thread malloc arena reserves 64MB that the limit counts
    :param progress_interval: seconds between the worker's progress messages
    :param stop_timeout: seconds to wait for the worker to exit before
        terminating it, and then before killing it
    :param _argv: run this command instead of the worker - a test hook, for
        stand-ins that misbehave in ways a real worker cannot be made to
    :param env: environment variables for the worker
    :param status: an :class:`~datasette.imports.inserter.ImportStatus` for
        :func:`~datasette.imports.inserter.import_rows` to keep up to date,
        to find out what the import created, committed and dropped even if
        it raises or is cancelled

    Returns a :class:`WorkerImportResult`. Raises :class:`WorkerImportError`
    if the worker fails; see the module documentation for what happens to
    the rows imported before that.
    """
    engine = WorkerEngine(
        WorkerOptions(
            plugins_dir=plugins_dir,
            memory_limit=memory_limit,
            progress_interval=progress_interval,
            stop_timeout=stop_timeout,
            env=env,
            argv=_argv,
        )
    )
    return await engine.load(
        db,
        table,
        path,
        loader=format,
        status=status if status is not None else ImportStatus(),
        progress=progress,
        on_error=on_error,
        sample_size=sample_size,
        batch_size=batch_size,
    )


async def _import_via_worker(
    db,
    table,
    argv,
    *,
    env,
    status,
    sample_size,
    batch_size,
    progress,
    on_error,
    memory_limit,
    stop_timeout,
):
    start = time.perf_counter()
    async with WorkerProcess(argv, env=env, stop_timeout=stop_timeout) as worker:
        # With on_error="drop", rows() raises after the last row if the
        # worker failed, so that import_rows() drops the table. With "keep"
        # the import finishes normally: the table has every row received
        stream = _WorkerStream(
            worker, table, progress, status, raise_on_failure=on_error == "drop"
        )
        # A write that fails or is cancelled kills the worker at once
        watched = _WatchedDatabase(db, on_failure=worker.abort)
        import_result = await stream.import_into(
            watched, sample_size=sample_size, batch_size=batch_size, on_error=on_error
        )
    # The worker has exited and been reaped: its exit status is known
    failure = stream.failure(worker.returncode, memory_limit)
    result = WorkerImportResult.from_status(
        status,
        table=table,
        loader=stream.loader,
        rows_read=stream.rows_read,
        complete=failure is None,
        schema=stream.schema,
        import_result=import_result,
        size=stream.size,
        seconds=time.perf_counter() - start,
        worker=stream.trailer,
        returncode=worker.returncode,
        notes=stream.notes,
        warnings=stream.warnings,
        stats={
            "decode_seconds": worker.decode_seconds,
            "write_seconds": watched.write_seconds,
            "writes": watched.writes,
            "bytes_read": worker.bytes_read,
        },
    )
    if failure is not None:
        kind, message, error_type = failure
        raise WorkerImportError(
            message,
            kind=kind,
            result=result,
            error_type=error_type,
            returncode=worker.returncode,
            stderr=worker.stderr,
        )
    return result


class _GuardedRows:
    """
    Iterates over a loader's rows, remembering the exception if the loader
    raises part way through. With ``stop=True`` the rows then end cleanly,
    so import_rows() still inserts every row before it (as the worker path
    does); otherwise the exception is raised on, and import_rows() cleans
    up as for any other failure.
    """

    def __init__(self, rows, stop):
        self._rows = iter(rows)
        self.stop = stop
        self.error = None
        self.count = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.error is not None:
            raise StopIteration
        try:
            row = next(self._rows)
        except StopIteration:
            raise
        except Exception as ex:
            self.error = ex
            if not self.stop:
                raise
            raise StopIteration from None
        self.count += 1
        return row


class _CollectWarnings(logging.Handler):
    "Collects warnings, but not the loader notes logged as warnings."

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        if not getattr(record, NOTE_LOG_ATTRIBUTE, None):
            self.messages.append(record.getMessage())


class InProcessEngine:
    """
    Loads the file in a thread of this process, with the same result and
    errors as :class:`WorkerEngine`: what :func:`import_file_in_process`
    does, for Pyodide and anywhere else a subprocess cannot be started.
    The loader shares the server's memory and GIL (a JSON file is parsed
    whole while holding it), and a loader that crashes takes the server
    with it.

    A loader error part way through keeps the rows before it, exactly as
    the worker does, and raises :class:`WorkerImportError` with
    ``kind="error"``; ``returncode``, ``worker`` and ``pid`` are ``None``.
    Loader warnings in ``result.warnings`` come from the
    ``datasette.imports`` loggers, so an in-process import running at the
    same time may contribute some.
    """

    def __repr__(self):
        return "InProcessEngine()"

    async def load(
        self,
        db,
        table,
        path,
        *,
        loader,
        status,
        progress,
        on_error,
        sample_size,
        batch_size,
    ):
        "See :meth:`Engine.load`."
        return await _import_in_process(
            db,
            table,
            path,
            loader=loader,
            status=status,
            sample_size=sample_size,
            batch_size=batch_size,
            progress=progress,
            on_error=on_error,
        )


async def _import_in_process(
    db,
    table,
    path,
    *,
    loader,
    status,
    sample_size,
    batch_size,
    progress,
    on_error,
):
    from datasette.imports import loaders as loaders_module
    from datasette.imports.worker import InputPosition

    if on_error not in ON_ERROR:
        raise ValueError(f"on_error must be one of {ON_ERROR}")
    start = time.perf_counter()
    loader_logger = logging.getLogger("datasette.imports")
    warnings = _CollectWarnings()
    loader_logger.addHandler(warnings)
    watched = None
    loaded = None
    size = None
    try:
        if await db.table_exists(table):
            raise ValueError(f"Table {table!r} already exists")

        def result(import_result, rows_read, complete):
            return WorkerImportResult.from_status(
                status,
                table=table,
                loader=loaded.loader.name if loaded else None,
                rows_read=rows_read,
                complete=complete,
                schema=loaded.schema.columns if (loaded and loaded.schema) else None,
                import_result=import_result,
                size=size,
                seconds=time.perf_counter() - start,
                worker=None,
                returncode=None,
                notes=list(loaded.notes) if loaded else [],
                warnings=warnings.messages,
                stats={
                    "write_seconds": watched.write_seconds if watched else 0.0,
                    "writes": watched.writes if watched else 0,
                },
            )

        loop = asyncio.get_running_loop()
        try:
            position = InputPosition(path)
            size = position.size
            loaded = await loop.run_in_executor(
                None, functools.partial(loaders_module.load_path, path, format=loader)
            )
        except Exception as ex:
            # Cannot read it, no loader recognizes it, or it failed at once
            raise WorkerImportError(
                str(ex),
                kind="error",
                result=result(None, 0, False),
                error_type=type(ex).__name__,
            ) from ex
        # With on_error="drop" a loader error goes through import_rows(),
        # which drops the table
        rows = _GuardedRows(loaded.rows, stop=on_error == "keep")
        watched = _WatchedDatabase(db, on_failure=lambda: None)

        async def written(rows_written):
            if progress is None:
                return
            outcome = progress(
                ImportProgress(
                    table=table,
                    loader=loaded.loader.name,
                    rows_read=rows.count,
                    rows_written=rows_written,
                    bytes=position.get(),
                    size=size,
                    backlog=status.backlog,
                    finishing=status.finishing,
                    notes=list(loaded.notes),
                )
            )
            if inspect.isawaitable(outcome):
                await outcome

        import_result = None
        try:
            import_result = await import_rows(
                watched,
                table,
                rows,
                schema=loaded.schema.columns if loaded.schema else None,
                sample_size=sample_size,
                batch_size=batch_size,
                progress=written,
                on_error=on_error,
                status=status,
            )
        except Exception as ex:
            if ex is not rows.error:
                raise
        error = rows.error
        outcome = result(import_result, rows.count, error is None)
        if error is not None:
            raise WorkerImportError(
                str(error),
                kind="error",
                result=outcome,
                error_type=type(error).__name__,
            ) from error
        return outcome
    finally:
        loader_logger.removeHandler(warnings)


async def import_file_in_process(
    db,
    table,
    path,
    *,
    format=None,
    sample_size=1000,
    batch_size=1000,
    progress=None,
    on_error="keep",
    status=None,
):
    """
    The same import as :func:`import_file_via_worker`, with the same result
    and errors, but loading the file in a thread of this process: see
    :class:`InProcessEngine`.
    """
    return await InProcessEngine().load(
        db,
        table,
        path,
        loader=format,
        status=status if status is not None else ImportStatus(),
        progress=progress,
        on_error=on_error,
        sample_size=sample_size,
        batch_size=batch_size,
    )


async def import_file(
    db,
    table,
    path,
    *,
    use_worker=None,
    format=None,
    sample_size=1000,
    batch_size=1000,
    progress=None,
    on_error="keep",
    status=None,
    **worker_options,
):
    """
    Import ``path`` with :func:`import_file_via_worker` where subprocesses
    are available, otherwise (``use_worker=False``, Pyodide) with
    :func:`import_file_in_process`. Same result and errors either way.
    ``worker_options`` (``plugins_dir``, ``memory_limit``...) only apply to
    the worker.
    """
    if use_worker is None:
        use_worker = subprocesses_available()
    options = {
        "format": format,
        "sample_size": sample_size,
        "batch_size": batch_size,
        "progress": progress,
        "on_error": on_error,
        "status": status,
    }
    if use_worker:
        return await import_file_via_worker(
            db, table, path, **options, **worker_options
        )
    return await import_file_in_process(db, table, path, **options)
