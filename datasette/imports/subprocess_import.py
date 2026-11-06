"""
Import a file into a Datasette database through a worker subprocess.

:func:`import_file_via_worker` starts ``python -m datasette.imports.worker``
(see :mod:`datasette.imports.worker` for the protocol), reads the rows it
streams on stdout without blocking the event loop, and inserts them with
:func:`~datasette.imports.inserter.import_rows` - so every chunk goes
through ``db.execute_write_fn()``, where ``write_wrapper`` plugins see it,
and column types are detected here in the parent. A loader that crashes,
hangs or runs out of memory takes down the worker, not the server.

What happens to rows when an import fails part way through
------------------------------------------------------------

Rows are committed a chunk at a time while they arrive, so people can query
the table during the import. If the *worker* fails at row N (a loader
error, a crash, being killed, a malformed or truncated stream), every row
it sent before failing is still inserted and the import is finished
normally (types settled, temporary state dropped), so the table holds
exactly the first rows of the file. Then :class:`WorkerImportError` is
raised. With ``on_error="keep"`` (the default) that table stays, and the
error says how many rows it holds; ``on_error="drop"`` drops it instead.
A failed *insert* in the parent (a ``write_wrapper`` plugin raising, a full
disk) kills the worker and re-raises that exception; cancellation kills
the worker and re-raises ``CancelledError``. In both cases the table keeps
the chunks already committed (or is dropped, with ``on_error="drop"``).
"""

import asyncio
import base64
import dataclasses
import functools
import inspect
import json
import logging
import os
import signal
import sys
import time

from datasette.imports.inserter import ImportResult, import_rows

logger = logging.getLogger(__name__)

WORKER_MODULE = "datasette.imports.worker"
PROTOCOL_VERSION = 1
# Bytes read from the worker's stdout at a time: a bigger read means fewer,
# longer json.loads() calls on the event loop
READ_SIZE = 256 * 1024
# How much of the end of the worker's stderr to keep for error messages
STDERR_TAIL = 8 * 1024
ON_ERROR = ("keep", "drop")

_ROW = 0x5B  # "["
_CONTROL = 0x7B  # "{"


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
    warnings: list
    #: Parent-side timings: decode_seconds (json.loads and building
    #: dicts), write_seconds (inside execute_write_fn), bytes_read
    stats: dict


class ProtocolDecoder:
    """
    Turns the bytes a worker writes into messages, checking the protocol.
    Holds the protocol state: the header, schema and column list.

    :meth:`feed` returns a list of ``(kind, payload)`` pairs, where ``kind``
    is ``"rows"`` (payload: a list of row dictionaries) or a control
    message type (payload: the message). Consecutive row lines are parsed
    with a single ``json.loads()`` call.
    """

    def __init__(self):
        self.header = None
        self.schema = None
        self.columns = []
        self.rows = 0
        self.lines = 0
        self.finished = None
        self._partial = b""

    def feed(self, data):
        if self._partial:
            data = self._partial + data
        lines = data.split(b"\n")
        self._partial = lines.pop()
        out = []
        try:
            self._decode(lines, out)
        except ProtocolError as ex:
            ex.messages = out
            raise
        return out

    def close(self):
        """
        Call at the end of the stream. Returns the incomplete last line, if
        any (the worker died part way through writing it).
        """
        partial, self._partial = self._partial, b""
        return partial

    def _decode(self, lines, out):
        row_lines = []
        first_row_line = 0
        for line in lines:
            self.lines += 1
            if not line:
                continue
            first = line[0]
            if first == _ROW:
                if not row_lines:
                    first_row_line = self.lines
                row_lines.append(line)
                continue
            if row_lines:
                out.append(("rows", self._rows(row_lines, first_row_line)))
                row_lines = []
            if first == _CONTROL:
                message = self._control(line)
                if message is not None:
                    out.append(message)
            elif line.strip():
                raise ProtocolError(
                    f"Line {self.lines} of the worker's output is not a row "
                    f"or a message: {line[:60]!r}"
                )
        if row_lines:
            out.append(("rows", self._rows(row_lines, first_row_line)))

    def _check_can_send_rows(self, line_number):
        if self.header is None:
            raise ProtocolError(f"Line {line_number}: a row before the header")
        if self.finished is not None:
            raise ProtocolError(f"Line {line_number}: a row after the end")

    def _rows(self, lines, first_line):
        self._check_can_send_rows(first_line)
        try:
            batch = json.loads(b"[" + b",".join(lines) + b"]")
        except ValueError:
            batch = None
        # The count check also catches lines that only parse when joined
        if (
            batch is None
            or len(batch) != len(lines)
            or not all(type(values) is list for values in batch)
        ):
            for offset, line in enumerate(lines):
                try:
                    values = json.loads(line)
                except ValueError as ex:
                    raise ProtocolError(
                        f"Line {first_line + offset} of the worker's output is "
                        f"not valid JSON: {ex}"
                    ) from None
                if type(values) is not list:
                    raise ProtocolError(
                        f"Line {first_line + offset}: a row must be one JSON array"
                    )
            raise ProtocolError(
                f"Lines {first_line}-{first_line + len(lines) - 1} of the "
                "worker's output are not one JSON array per line"
            )
        columns = self.columns
        if batch and max(map(len, batch)) > len(columns):
            raise ProtocolError(
                f"Near line {first_line}: a row has more values than the "
                f"{len(columns)} columns announced"
            )
        self.rows += len(batch)
        return [dict(zip(columns, values)) for values in batch]

    def _control(self, line):
        try:
            message = json.loads(line)
        except ValueError as ex:
            raise ProtocolError(
                f"Line {self.lines} of the worker's output is not valid JSON: {ex}"
            ) from None
        if type(message) is not dict or not isinstance(message.get("type"), str):
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
        if kind == "row":
            return ("rows", [self._row_message(message)])
        if kind in ("end", "error"):
            self.finished = message
            return (kind, message)
        if kind == "header":
            raise ProtocolError(f"Line {self.lines}: a second header")
        # progress, log - and types added later, which old parents ignore
        return (kind, message)

    def _row_message(self, message):
        self._check_can_send_rows(self.lines)
        values = message.get("values")
        positions = message.get("base64") or []
        if type(values) is not list or len(values) > len(self.columns):
            raise ProtocolError(f"Line {self.lines}: bad row message")
        try:
            for position in positions:
                values[position] = base64.b64decode(values[position], validate=True)
        except (TypeError, ValueError, IndexError) as ex:
            raise ProtocolError(f"Line {self.lines}: bad base64 value: {ex}") from None
        self.rows += 1
        return dict(zip(self.columns, values))


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
    argv = [sys.executable, "-P", "-m", WORKER_MODULE, os.fspath(path)]
    if format:
        argv += ["--format", format]
    if plugins_dir:
        argv += ["--plugins-dir", os.fspath(plugins_dir)]
    if memory_limit:
        argv += ["--memory-limit", str(int(memory_limit))]
    if progress_interval is not None:
        argv += ["--progress-interval", str(progress_interval)]
    argv += ["--parent-pid", str(os.getpid())]
    return argv + list(extra_args)


class WorkerProcess:
    """
    A running worker subprocess. Reads its stdout through a
    :class:`ProtocolDecoder` and keeps the last ``STDERR_TAIL`` bytes of its
    stderr, read concurrently so a chatty worker can never block on a full
    stderr pipe.
    """

    def __init__(self, argv, *, env=None, read_size=READ_SIZE):
        self.argv = list(argv)
        self.env = env
        self.read_size = read_size
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

    def _signal(self, signum):
        if self.process is None or self.process.returncode is not None:
            return
        try:
            # The whole process group, in case a plugin started processes
            os.killpg(self.process.pid, signum)
        except (AttributeError, ProcessLookupError, PermissionError):
            # No process groups (Windows), or the group is gone
            try:
                self.process.send_signal(signum)
            except ProcessLookupError:
                pass

    def abort(self):
        """
        Stop reading - rows not yet taken are dropped - and kill the worker
        now. Safe to call more than once.
        """
        self.aborted = True
        self._signal(signal.SIGKILL)

    def kill_now(self):
        "Kill the worker now, but let the rows already decoded be used."
        self._signal(signal.SIGKILL)

    async def stop(self, timeout=2.0):
        """
        Make sure the worker is gone and reaped: wait ``timeout`` seconds
        for it to exit, terminate it, wait again, then kill it.
        """
        if self.process is None:
            return
        try:
            if self.process.returncode is None:
                try:
                    await asyncio.wait_for(self.process.wait(), timeout)
                except TimeoutError:
                    self._signal(signal.SIGTERM)
                    try:
                        await asyncio.wait_for(self.process.wait(), timeout)
                    except TimeoutError:
                        self._signal(signal.SIGKILL)
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

    async def kill(self, timeout=2.0):
        "Terminate the worker, then kill it if it is still there after ``timeout``."
        if self.process is not None and self.process.returncode is None:
            self.aborted = True
            self._signal(signal.SIGTERM)
        await self.stop(timeout)


class _WatchedDatabase:
    """
    Passes everything through to a Database, but calls ``on_failure`` when
    a write fails or is cancelled, and times the writes. import_rows() waits
    for its next chunk of rows before re-raising a failed write; killing
    the worker first means that never waits on a worker that has stalled.
    """

    def __init__(self, db, on_failure):
        self._db = db
        self._on_failure = on_failure
        self.writes = 0
        # Writes that inserted rows: TypedInserter.write() returns how many.
        # Once one succeeded, the table is ours - it did not exist when the
        # import started, and the first write created it
        self.row_writes = 0
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
        if type(result) is int and result > 0:
            self.row_writes += 1
        return result


class _Import:
    "The state of one import_file_via_worker() call."

    def __init__(self, worker, table, progress):
        self.worker = worker
        self.table = table
        self.progress = progress
        self.loader = None
        self.schema = None
        self.size = None
        self.bytes = None
        self.rows_read = 0
        self.rows_written = 0
        self.trailer = None
        self.error_message = None
        self.protocol_error = None
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
                self.warnings.append(text)
                logger.warning("Import worker: %s", text)
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

    async def fill(self):
        "Read until rows are ready or the stream is over."
        while not self._ready and not self.done:
            try:
                messages = await self.worker.read_messages()
            except ProtocolError as ex:
                # Rows before the bad line are good: insert those
                self.worker.kill_now()
                self.protocol_error = ex
                self.done = True
                await self._handle(ex.messages)
                return
            if messages is None:
                self.done = True
                return
            await self._handle(messages)

    async def start(self):
        """
        Read up to the first rows, so the header - and the schema, which
        comes before any row - are known.
        """
        while not self.done and not self._ready and self.schema is None:
            try:
                messages = await self.worker.read_messages()
            except ProtocolError as ex:
                # Rows before the bad line are good: insert those
                self.worker.kill_now()
                self.protocol_error = ex
                self.done = True
                await self._handle(ex.messages)
                return
            if messages is None:
                self.done = True
                return
            await self._handle(messages)

    async def rows(self):
        worker = self.worker
        while True:
            if self._ready:
                batch, self._ready = self._ready, []
                for row in batch:
                    if worker.aborted:
                        return
                    yield row
            elif self.done or worker.aborted:
                return
            else:
                await self.fill()

    async def written(self, rows_written):
        self.rows_written = rows_written
        try:
            await self._report()
        except BaseException:
            # import_rows() reads the next chunk before re-raising: don't
            # let it wait for a worker that is still going
            self.worker.abort()
            raise

    def failure(self, returncode, memory_limit):
        """
        ``(kind, message, error_type)`` describing how the worker failed,
        or None if it finished cleanly and sent every row.
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


async def _table_exists(db, table):
    return await db.table_exists(table)


async def _drop_table(db, table):
    def drop(conn):
        conn.execute('DROP TABLE IF EXISTS "{}"'.format(table.replace('"', '""')))

    await db.execute_write_fn(drop)


async def import_file_via_worker(
    db,
    table,
    path,
    *,
    format=None,
    plugins_dir=None,
    sample_size=1000,
    batch_size=1000,
    widen="auto",
    progress=None,
    on_error="keep",
    memory_limit=None,
    progress_interval=None,
    delete_input=False,
    stop_timeout=2.0,
    argv=None,
    env=None,
):
    """
    Import the file at ``path`` into a new ``table`` in ``db`` (a Datasette
    :class:`~datasette.database.Database`), loading it in a worker
    subprocess.

    :param format: use the loader with this name instead of sniffing
    :param plugins_dir: a directory of plugins for the worker to load, as
        ``datasette --plugins-dir`` does (entry point plugins always load)
    :param sample_size: rows used to detect column types
    :param batch_size: rows per ``execute_write_fn()`` call after the sample
    :param widen: passed to :func:`~datasette.imports.inserter.import_rows`
    :param progress: called (and awaited, if it returns an awaitable) with an
        :class:`ImportProgress` after each committed chunk and each
        progress message from the worker
    :param on_error: ``"keep"`` the rows imported before a failure, or
        ``"drop"`` the table
    :param memory_limit: address space limit (``RLIMIT_AS``) for the worker,
        in bytes. It counts reserved address space, not memory used: the
        worker starts at about 65MB (130-200MB with glibc's per-thread
        malloc arena, which this turns off with ``MALLOC_ARENA_MAX=1``), and
        the JSON loader needs about five times the file's size
    :param progress_interval: seconds between the worker's progress messages
    :param delete_input: delete ``path`` afterwards, whatever happens - for
        a temporary copy of stdin or a download
    :param stop_timeout: seconds to wait for the worker to exit before
        terminating it, and then before killing it
    :param argv: run this command instead of the worker (for tests)
    :param env: environment variables for the worker

    Returns a :class:`WorkerImportResult`. Raises :class:`WorkerImportError`
    if the worker fails; see the module documentation for what happens to
    the rows imported before that.
    """
    if on_error not in ON_ERROR:
        raise ValueError(f"on_error must be one of {ON_ERROR}")
    start = time.perf_counter()
    worker = None
    watched = None
    state = None
    try:
        if await _table_exists(db, table):
            raise ValueError(f"Table {table!r} already exists")
        if argv is None:
            argv = worker_command(
                path,
                format=format,
                plugins_dir=plugins_dir,
                memory_limit=memory_limit,
                progress_interval=progress_interval,
            )
        if memory_limit and env is None and "MALLOC_ARENA_MAX" not in os.environ:
            # RLIMIT_AS counts address space glibc reserves but may never
            # use: 64MB per thread for its own malloc arena. One arena is
            # plenty for the worker's two threads
            env = {**os.environ, "MALLOC_ARENA_MAX": "1"}
        worker = WorkerProcess(argv, env=env)
        await worker.start()
        state = _Import(worker, table, progress)
        await state.start()
        watched = _WatchedDatabase(db, on_failure=worker.abort)
        import_result = None
        if worker.decoder.header is not None:
            rows = state.rows()
            try:
                import_result = await import_rows(
                    watched,
                    table,
                    rows,
                    schema=state.schema,
                    sample_size=sample_size,
                    batch_size=batch_size,
                    widen=widen,
                    progress=state.written,
                )
            finally:
                try:
                    await rows.aclose()
                except RuntimeError:
                    pass
        else:
            # An error (or nothing at all) before the header
            while not state.done:
                await state.fill()
        # Wait for the worker to exit (it should, right after its last message)
        await worker.stop(stop_timeout)
        failure = state.failure(worker.returncode, memory_limit)
        table_exists = bool(import_result and import_result.created)
        if failure is not None and on_error == "drop" and table_exists:
            await _drop_table(db, table)
            table_exists = False
        result = WorkerImportResult(
            table=table,
            loader=state.loader,
            rows=import_result.rows if (import_result and table_exists) else 0,
            rows_read=state.rows_read,
            complete=failure is None,
            table_exists=table_exists,
            schema=state.schema,
            import_result=import_result,
            size=state.size,
            seconds=time.perf_counter() - start,
            worker=state.trailer,
            returncode=worker.returncode,
            warnings=state.warnings,
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
    except WorkerImportError:
        raise
    except BaseException:
        # A failed insert, a progress callback that raised, cancellation:
        # kill the worker, and drop what was written if asked to
        if worker is not None:
            worker.abort()
            await asyncio.shield(worker.stop(stop_timeout))
        if on_error == "drop" and watched is not None and watched.row_writes:
            await asyncio.shield(_drop_table(db, table))
        raise
    finally:
        if delete_input:
            try:
                os.unlink(path)
            except OSError:
                pass
