import asyncio
import atexit
import contextvars
import inspect
import os
import queue
import sys
import tempfile
import threading
import time
import uuid
from collections import namedtuple
from pathlib import Path

import sqlite_utils
from opentelemetry import context as otel_context_api
from opentelemetry.trace import Status, StatusCode

from .inspect import inspect_hash
from .telemetry import (
    callback_name,
    linked_root_span_kwargs,
    record_operation_duration,
    record_query_interrupted,
    record_write_queue_wait,
    sql_attribute,
    sql_operation_name,
    tracer,
)
from .telemetry_registry import (
    CALLBACK,
    DB_COLLECTION_NAME,
    DB_NAMESPACE,
    DB_OPERATION_NAME,
    DB_QUERY,
    DB_QUERY_EXECUTE,
    DB_QUERY_TEXT,
    DB_SYSTEM,
    DB_WRITE_EXECUTE,
    DB_WRITE_QUEUE_WAIT,
    EXECUTEMANY,
    EXECUTESCRIPT,
    INTERRUPTED,
    ISOLATED_CONNECTION,
    PARAM_COUNT,
    PARAM_SETS,
    ROWS_RETURNED,
    SQL_ERROR_SUPPRESSED,
    TIME_LIMIT_MS,
    TRANSACTION,
    TRUNCATED,
)
from .tracer import trace
from .utils import (
    call_with_supported_arguments,
    detect_fts,
    detect_primary_keys,
    detect_spatialite,
    escape_sqlite,
    get_all_foreign_keys,
    get_outbound_foreign_keys,
    md5_not_usedforsecurity,
    sqlite3,
    sqlite_timelimit,
    table_column_details,
    table_columns,
)
from .utils.sql_analysis import SQLAnalysis, analyze_sql_tables
from .utils.sqlite import sqlite_hidden_table_names

connections = threading.local()

EXECUTE_WRITE_RETURNING_LIMIT = 10

AttachedDatabase = namedtuple("AttachedDatabase", ("seq", "name", "file"))


class DatasetteClosedError(RuntimeError):
    """Raised when using a Datasette or Database instance after close()."""


_SHUTDOWN = object()


class Database:
    # For table counts stop at this many rows:
    count_limit = 10000
    _thread_local_id_counter = 1

    def __init__(
        self,
        ds,
        path=None,
        is_mutable=True,
        is_memory=False,
        memory_name=None,
        mode=None,
        is_temp_disk=False,
    ):
        self.name = None
        self._thread_local_id = f"x{self._thread_local_id_counter}"
        Database._thread_local_id_counter += 1
        self.route = None
        self.ds = ds
        self.path = path
        self.is_mutable = is_mutable
        self.is_memory = is_memory
        self.memory_name = memory_name
        self.is_temp_disk = is_temp_disk
        if memory_name is not None:
            self.is_memory = True
        if is_temp_disk:
            fd, temp_path = tempfile.mkstemp(suffix=".db", prefix="datasette_temp_")
            os.close(fd)
            self.path = temp_path
            self.is_mutable = True
            self.mode = "rwc"
            self._wal_enabled = False
            atexit.register(self._cleanup_temp_file)
        else:
            self._wal_enabled = False
        self.cached_hash = None
        self.cached_size = None
        self._cached_table_counts = None
        self._write_thread = None
        self._write_queue = None
        self._closed = False
        self._pending_execute_futures = set()
        self._pending_execute_futures_lock = threading.Lock()
        # These are used when in non-threaded mode:
        self._read_connection = None
        self._write_connection = None
        # This is used to track all file connections so they can be closed
        self._all_file_connections = []
        if not is_temp_disk:
            self.mode = mode

    def _check_not_closed(self):
        if self._closed:
            raise DatasetteClosedError(f"Database {self.name!r} has been closed")

    def _remove_pending_execute_future(self, future):
        with self._pending_execute_futures_lock:
            self._pending_execute_futures.discard(future)

    @property
    def cached_table_counts(self):
        if self._cached_table_counts is not None:
            return self._cached_table_counts
        # Maybe use self.ds.inspect_data to populate cached_table_counts
        if self.ds.inspect_data and self.ds.inspect_data.get(self.name):
            self._cached_table_counts = {
                key: value["count"]
                for key, value in self.ds.inspect_data[self.name]["tables"].items()
            }
        return self._cached_table_counts

    @property
    def color(self):
        if self.hash:
            return self.hash[:6]
        return md5_not_usedforsecurity(self.name)[:6]

    def suggest_name(self):
        if self.is_temp_disk:
            return "_temp_disk"
        if self.path:
            return Path(self.path).stem
        elif self.memory_name:
            return self.memory_name
        else:
            return "db"

    def connect(self, write=False):
        extra_kwargs = {}
        if write:
            extra_kwargs["isolation_level"] = "IMMEDIATE"
        if self.memory_name:
            uri = f"file:{self.memory_name}?mode=memory&cache=shared"
            conn = sqlite3.connect(
                uri, uri=True, check_same_thread=False, **extra_kwargs
            )
            if not write:
                conn.execute("PRAGMA query_only=1")
            return conn
        if self.is_memory:
            return sqlite3.connect(":memory:", uri=True)

        # mode=ro or immutable=1?
        if self.is_mutable:
            qs = "?mode=ro"
            if self.ds.nolock:
                qs += "&nolock=1"
        else:
            qs = "?immutable=1"
        assert not (write and not self.is_mutable)
        if write:
            qs = ""
        if self.mode is not None:
            qs = f"?mode={self.mode}"
        conn = sqlite3.connect(
            f"file:{self.path}{qs}", uri=True, check_same_thread=False, **extra_kwargs
        )
        self._all_file_connections.append(conn)
        if self.is_temp_disk and not self._wal_enabled:
            conn.execute("PRAGMA journal_mode=WAL")
            self._wal_enabled = True
        return conn

    def close(self):
        """Release all resources held by this database.

        Idempotent. After close() further calls to execute()/execute_fn()/
        execute_write()/execute_write_fn() raise DatasetteClosedError.
        """
        if self._closed:
            return
        with self._pending_execute_futures_lock:
            if self._closed:
                return
            self._closed = True
            pending_execute_futures = tuple(self._pending_execute_futures)
        # Shut down the write thread, if any, via a sentinel. The thread
        # drains any writes already queued before the sentinel and then
        # closes its own write connection and returns.
        write_thread = self._write_thread
        if write_thread is not None and self._write_queue is not None:
            self._write_queue.put(_SHUTDOWN)
            write_thread.join(timeout=10)
            if write_thread.is_alive():
                sys.stderr.write(
                    f"Datasette: write thread for {self.name!r} did not exit within 10s\n"
                )
                sys.stderr.flush()
        for future in pending_execute_futures:
            try:
                future.result()
            except Exception:  # noqa: BLE001, S110
                # Shutdown teardown - a failed pending write must not block close()
                pass
        # Close anything still tracked in _all_file_connections
        for connection in self._all_file_connections:
            try:
                connection.close()
            except Exception:  # noqa: BLE001, S110
                pass
        self._all_file_connections = []
        # Drop per-thread cached read connections we can reach
        try:
            delattr(connections, self._thread_local_id)
        except AttributeError:
            pass
        # Close non-threaded-mode cached connections if still open
        if self._read_connection is not None:
            try:
                self._read_connection.close()
            except Exception:  # noqa: BLE001, S110
                pass
            self._read_connection = None
        if self._write_connection is not None:
            try:
                self._write_connection.close()
            except Exception:  # noqa: BLE001, S110
                pass
            self._write_connection = None
        if self.is_temp_disk:
            self._cleanup_temp_file()

    def _cleanup_temp_file(self):
        if self.is_temp_disk and self.path:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(self.path + suffix)
                except OSError:
                    pass

    async def execute_write(
        self,
        sql,
        params=None,
        block=True,
        request=None,
        return_all=False,
        returning_limit=EXECUTE_WRITE_RETURNING_LIMIT,
        transaction=True,
    ):
        self._check_not_closed()
        if returning_limit < 0:
            raise ValueError("returning_limit must be >= 0")

        def _inner(conn):
            cursor = conn.execute(sql, params or [])
            return ExecuteWriteResult.from_cursor(
                cursor, return_all=return_all, returning_limit=returning_limit
            )

        # SIM117 wants these two context managers merged. They are kept nested
        # deliberately: the hand-rolled tracer's wrapper is on its way out, and
        # nesting makes removing it a single-line deletion.
        with trace(  # noqa: SIM117
            "sql", database=self.name, sql=sql.strip(), params=params
        ):
            with tracer.start_as_current_span(DB_QUERY, kind=DB_QUERY.kind) as span:
                span.set_attribute(DB_SYSTEM, "sqlite")
                span.set_attribute(DB_NAMESPACE, self.name)
                span.set_attribute(DB_QUERY_TEXT, sql_attribute(sql))
                operation_name = sql_operation_name(sql)
                if operation_name:
                    span.set_attribute(DB_OPERATION_NAME, operation_name)
                if params:
                    span.set_attribute(PARAM_COUNT, len(params))
                with record_operation_duration(self.name, "write"):
                    results = await self._execute_write_fn(
                        _inner, block=block, request=request, transaction=transaction
                    )
        return results

    async def execute_write_script(self, sql, block=True, request=None):
        self._check_not_closed()

        def _inner(conn):
            return conn.executescript(sql)

        # Nested on purpose - see the note in execute_write().
        with trace(  # noqa: SIM117
            "sql", database=self.name, sql=sql.strip(), executescript=True
        ):
            # No db.operation.name here, deliberately: executescript() runs
            # several semicolon-separated statements, and semantic conventions
            # say the attribute should not be extracted from query text that
            # can hold more than one operation - see sql_operation_name().
            with tracer.start_as_current_span(DB_QUERY, kind=DB_QUERY.kind) as span:
                span.set_attribute(DB_SYSTEM, "sqlite")
                span.set_attribute(DB_NAMESPACE, self.name)
                span.set_attribute(DB_QUERY_TEXT, sql_attribute(sql))
                span.set_attribute(EXECUTESCRIPT, True)
                with record_operation_duration(self.name, "write"):
                    results = await self._execute_write_fn(
                        _inner, block=block, transaction=False, request=request
                    )
        return results

    async def execute_write_many(self, sql, params_seq, block=True, request=None):
        self._check_not_closed()

        def _inner(conn):
            count = 0

            def count_params(params):
                nonlocal count
                for param in params:
                    count += 1
                    yield param

            return conn.executemany(sql, count_params(params_seq)), count

        # Nested on purpose - see the note in execute_write().
        with trace(
            "sql", database=self.name, sql=sql.strip(), executemany=True
        ) as kwargs:
            with tracer.start_as_current_span(DB_QUERY, kind=DB_QUERY.kind) as span:
                span.set_attribute(DB_SYSTEM, "sqlite")
                span.set_attribute(DB_NAMESPACE, self.name)
                span.set_attribute(DB_QUERY_TEXT, sql_attribute(sql))
                span.set_attribute(EXECUTEMANY, True)
                # A single statement run with many parameter sets, so unlike
                # execute_write_script() there is exactly one operation to name.
                operation_name = sql_operation_name(sql)
                if operation_name:
                    span.set_attribute(DB_OPERATION_NAME, operation_name)
                with record_operation_duration(self.name, "write"):
                    results, count = await self._execute_write_fn(
                        _inner, block=block, request=request
                    )
                # count is the number of parameter *sets* consumed by
                # executemany(), not a row count - executemany returns no rows.
                span.set_attribute(PARAM_SETS, count)
            kwargs["count"] = count
        return results

    async def execute_isolated_fn(self, fn):
        self._check_not_closed()
        # Open a new connection just for the duration of this function,
        # blocking the write queue to avoid any writes occurring during it
        write = self.is_mutable

        def _run():
            isolated_connection = self.connect(write=write)
            try:
                return fn(isolated_connection)
            finally:
                isolated_connection.close()
                try:
                    self._all_file_connections.remove(isolated_connection)
                except ValueError:
                    # Was probably a memory connection
                    pass

        # One db.query span here, like execute_fn() / execute_write_fn().
        # The wrap must NOT move into _send_to_write_thread(): that is the
        # shared tail for every write, and for block=False it is where the
        # link back to this span is captured - a span opened there would be
        # the link target for its own children.
        with tracer.start_as_current_span(DB_QUERY, kind=DB_QUERY.kind) as span:
            span.set_attribute(DB_SYSTEM, "sqlite")
            span.set_attribute(DB_NAMESPACE, self.name)
            span.set_attribute(CALLBACK, callback_name(fn))
            # "write" when mutable because the call blocks the write queue;
            # "read" when immutable, where it runs on the read pool.
            with record_operation_duration(self.name, "write" if write else "read"):
                if self.ds.executor is None:
                    # non-threaded mode
                    return _run()
                if not write:
                    # Immutable database - no writes can ever occur, so there
                    # is no write queue to block; run against a fresh
                    # read-only connection. copy_context() carries the
                    # caller's otel context onto the worker thread - see the
                    # notes in _execute_fn() for why it must be a fresh copy
                    # per submit and why carrying every ContextVar is safe.
                    ctx = contextvars.copy_context()
                    return await asyncio.get_running_loop().run_in_executor(
                        self.ds.executor, ctx.run, _run
                    )
                # Threaded mode - send to write thread
                return await self._send_to_write_thread(fn, isolated_connection=True)

    async def analyze_sql(self, sql, params=None) -> SQLAnalysis:
        self._check_not_closed()

        def _analyze_sql(conn):
            return analyze_sql_tables(conn, sql, params, database_name=self.name)

        return await self.execute_isolated_fn(_analyze_sql)

    async def execute_write_fn(self, fn, block=True, transaction=True, request=None):
        """Run `fn(conn)` on the write connection, traced as one database call.

        The public entry point for callback-style writes. Instrumented like
        `execute_write()`: one `db.query` span (with `datasette.callback` in
        place of `db.query.text`) above the `db.write.queue_wait` and
        `db.write.execute` spans the write thread emits. The SQL-string write
        methods call `_execute_write_fn()` directly, so they never get a
        second span. For `block=False` this span ends at enqueue and the
        write-thread spans become roots carrying a link back to it, exactly
        as for `execute_write(block=False)`.
        """
        self._check_not_closed()
        # The raw fn's name, before _wrap_fn_with_hooks() replaces it with a
        # wrapper - otherwise every write would report the wrapper's name.
        name = callback_name(fn)
        with tracer.start_as_current_span(DB_QUERY, kind=DB_QUERY.kind) as span:
            span.set_attribute(DB_SYSTEM, "sqlite")
            span.set_attribute(DB_NAMESPACE, self.name)
            span.set_attribute(CALLBACK, name)
            with record_operation_duration(self.name, "write"):
                return await self._execute_write_fn(
                    fn, block=block, transaction=transaction, request=request
                )

    async def _execute_write_fn(self, fn, block=True, transaction=True, request=None):
        self._check_not_closed()
        pending_events = []

        def track_event(event):
            pending_events.append(event)

        fn = self._wrap_fn_with_hooks(fn, request, transaction, track_event)
        if self.ds.executor is None:
            # non-threaded mode
            if self._write_connection is None:
                self._write_connection = self.connect(write=True)
                self.ds._prepare_connection(self._write_connection, self.name)
            if transaction:
                with self._write_connection:
                    self._write_connection.execute("BEGIN IMMEDIATE")
                    result = fn(self._write_connection)
            else:
                result = fn(self._write_connection)
        else:
            result = await self._send_to_write_thread(
                fn, block=block, transaction=transaction
            )
        if block:
            for event in pending_events:
                await self.ds.track_event(event)
        else:
            # For non-blocking writes, spawn a background task to
            # dispatch events after the write thread completes
            task_id, reply_future = result

            async def _dispatch_events_after_write():
                try:
                    await reply_future
                except Exception:  # noqa: BLE001
                    # The write failed; skip success events regardless of why
                    # if the write failed, don't emit success events
                    return
                for event in pending_events:
                    await self.ds.track_event(event)

            asyncio.ensure_future(_dispatch_events_after_write())
            result = task_id
        return result

    def _wrap_fn_with_hooks(self, fn, request, transaction, track_event):
        from .plugins import pm

        # Wrap fn so it receives track_event if its signature supports it.
        # Historically fn was called positionally, so any single-parameter
        # name (conn, connection, db, ...) worked. Preserve that by only
        # switching to keyword dependency injection when the callback
        # explicitly opts in by declaring a `track_event` parameter.
        original_fn = fn

        if "track_event" in inspect.signature(original_fn).parameters:

            def fn_with_track_event(conn):
                return call_with_supported_arguments(
                    original_fn, conn=conn, track_event=track_event
                )

            fn = fn_with_track_event

        wrappers = pm.hook.write_wrapper(
            datasette=self.ds,
            database=self.name,
            request=request,
            transaction=transaction,
        )
        wrappers = [w for w in wrappers if w is not None]
        if not wrappers:
            return fn
        # Build the wrapped fn by nesting context manager generators.
        # The first wrapper returned by pluggy is outermost.
        for wrapper_factory in reversed(wrappers):
            fn = _apply_write_wrapper(fn, wrapper_factory, track_event)
        return fn

    async def _send_to_write_thread(
        self, fn, block=True, isolated_connection=False, transaction=True
    ):
        if self._write_queue is None:
            self._write_queue = queue.Queue()
        if self._write_thread is None:
            self._write_thread = threading.Thread(
                target=self._execute_writes, daemon=True
            )
            self._write_thread.name = f"_execute_writes for database {self.name}"
            self._write_thread.start()
        task_id = uuid.uuid5(uuid.NAMESPACE_DNS, "datasette.io")
        loop = asyncio.get_running_loop()
        reply_future = loop.create_future()
        # The otel Context and enqueue timestamp are captured here, on the
        # event loop, for the db.write.queue_wait span built at dequeue time.
        # `block` travels too - it decides parent vs. link; see `_execute_writes`.
        self._write_queue.put(
            WriteTask(
                fn,
                task_id,
                loop,
                reply_future,
                isolated_connection,
                transaction,
                otel_context_api.get_current(),
                time.time_ns(),
                block,
            )
        )
        if block:
            return await reply_future
        else:
            return task_id, reply_future

    def _execute_writes(self):
        # Infinite looping thread that protects the single write connection
        # to this database
        conn_exception = None
        conn = None
        try:
            conn = self.connect(write=True)
            # This warm-up runs before any write has ever been queued, so
            # there is no captured caller context to attach - and a raw
            # threading.Thread does not inherit the context of whoever started
            # it. Spans created by plugin hooks here are therefore roots even
            # when the write thread is started from inside invoke_startup():
            # its datasette.startup span is current on the event loop but does
            # not cross this thread boundary. Read connections differ - they
            # warm up inside executor tasks submitted with copy_context(), so
            # their prepare_connection spans do nest under whoever triggered
            # them.
            self.ds._prepare_connection(conn, self.name)
        except Exception as e:  # noqa: BLE001
            # Stored and re-raised to whoever queues the next write
            conn_exception = e
        while True:
            task = self._write_queue.get()
            if task is _SHUTDOWN:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001, S110
                        # Best-effort close as the write thread exits
                        pass
                return
            # `task.block` decides how this task's spans relate to the
            # context captured at enqueue time:
            #
            # - block=True: the caller genuinely awaits the reply, so
            #   containment is accurate. Restore that context as current
            #   (attach below) so db.write.queue_wait/db.write.execute parent
            #   normally to the request that queued them. The token must be
            #   detached below in `finally` - a leaked token silently
            #   poisons this thread's ambient context for every write
            #   processed after it, and a *wrong*-token detach only logs a
            #   warning rather than raising, so this pairing is load-bearing
            #   and easy to get wrong silently.
            # - block=False: the caller returned already without awaiting,
            #   so the enqueueing span may have closed before this task's
            #   spans even start. The enqueueing request *caused* this write
            #   without *containing* it, so nothing is attached here -
            #   linked_root_span_kwargs() makes each write span a root with
            #   a Link back to the enqueueing span (see its docstring for
            #   the full rationale), built once into `write_span_kwargs`
            #   and spread into every start_span call below.
            token = None
            write_span_kwargs = {}
            if task.block:
                token = otel_context_api.attach(task.otel_context)
            else:
                write_span_kwargs = linked_root_span_kwargs(task.otel_context)
            try:
                exception = None
                result = None
                # Explicit start_time/end_time rather than a `with` block:
                # this span's duration is the time the task actually spent
                # waiting in the queue (enqueue -> dequeue), not the near-
                # zero time spent constructing/ending the span object here.
                dequeued_at_ns = time.time_ns()
                tracer.start_span(
                    DB_WRITE_QUEUE_WAIT,
                    start_time=task.enqueued_at_ns,
                    **write_span_kwargs,
                ).end(end_time=dequeued_at_ns)
                record_write_queue_wait(self.name, dequeued_at_ns - task.enqueued_at_ns)
                if conn_exception is not None:
                    # fn never runs in this branch, so there is nothing to
                    # wrap in a db.write.execute span.
                    exception = conn_exception
                elif task.isolated_connection:
                    try:
                        with tracer.start_as_current_span(
                            DB_WRITE_EXECUTE, **write_span_kwargs
                        ) as span:
                            span.set_attribute(
                                ISOLATED_CONNECTION,
                                task.isolated_connection,
                            )
                            span.set_attribute(TRANSACTION, task.transaction)
                            isolated_connection = self.connect(write=True)
                            try:
                                result = task.fn(isolated_connection)
                            finally:
                                isolated_connection.close()
                                try:
                                    self._all_file_connections.remove(
                                        isolated_connection
                                    )
                                except ValueError:
                                    # Was probably a memory connection
                                    pass
                    except Exception as e:  # noqa: BLE001
                        # Write thread must survive any task failure or the database wedges
                        sys.stderr.write(f"{e}\n")
                        sys.stderr.flush()
                        exception = e
                else:
                    try:
                        with tracer.start_as_current_span(
                            DB_WRITE_EXECUTE, **write_span_kwargs
                        ) as span:
                            span.set_attribute(
                                ISOLATED_CONNECTION,
                                task.isolated_connection,
                            )
                            span.set_attribute(TRANSACTION, task.transaction)
                            if task.transaction:
                                with conn:
                                    conn.execute("BEGIN IMMEDIATE")
                                    result = task.fn(conn)
                            else:
                                result = task.fn(conn)
                    except Exception as e:  # noqa: BLE001
                        sys.stderr.write(f"{e}\n")
                        sys.stderr.flush()
                        exception = e
                _deliver_write_result(task, result, exception)
            finally:
                if token is not None:
                    otel_context_api.detach(token)

    async def execute_fn(self, fn):
        """Run `fn(conn)` on a read connection, traced as one database call.

        The public entry point for callback-style reads - plugins and core
        both use it to run arbitrary Python against a connection. It is
        instrumented exactly like `execute()`: one `db.query` span (with
        `datasette.callback` in place of `db.query.text`, since there is no
        SQL string to record) and a `db.query.execute` child covering the
        time actually spent on the worker thread. `execute()` itself calls
        `_execute_fn()` directly, so a SQL read never gets a second span.
        """
        self._check_not_closed()

        def fn_in_execute_span(conn):
            # Created on the worker thread; parents to the db.query span via
            # the copy_context() propagation in _execute_fn(). The gap
            # between the two spans is time spent waiting for a free thread.
            with tracer.start_as_current_span(DB_QUERY_EXECUTE):
                return fn(conn)

        with tracer.start_as_current_span(DB_QUERY, kind=DB_QUERY.kind) as span:
            span.set_attribute(DB_SYSTEM, "sqlite")
            span.set_attribute(DB_NAMESPACE, self.name)
            span.set_attribute(CALLBACK, callback_name(fn))
            # Default exception handling applies, unlike execute(): there is
            # no log_sql_errors=False probing caller and no expected-timeout
            # budget on this path, so a raised exception is an error.
            with record_operation_duration(self.name, "read"):
                return await self._execute_fn(fn_in_execute_span)

    async def _execute_fn(self, fn):
        self._check_not_closed()
        if self.ds.executor is None:
            # non-threaded mode
            if self._read_connection is None:
                self._read_connection = self.connect()
                self.ds._prepare_connection(self._read_connection, self.name)
            return fn(self._read_connection)

        # threaded mode
        def in_thread():
            conn = getattr(connections, self._thread_local_id, None)
            if not conn:
                conn = self.connect()
                self.ds._prepare_connection(conn, self.name)
                setattr(connections, self._thread_local_id, conn)
            return fn(conn)

        with self._pending_execute_futures_lock:
            self._check_not_closed()
            # A fresh copy_context() is required per submit (not one shared
            # copy reused across calls): concurrent execution of the same
            # Context raises "RuntimeError: cannot enter context ...
            # already entered". This propagates the caller's otel context
            # (e.g. the enclosing db.query span) onto the worker thread.
            #
            # copy_context() is not selective: it also carries Datasette's own
            # ContextVars - _skip_permission_checks and _permission_check_cache
            # (datasette/permissions.py), _in_datasette_client (app.py) and,
            # until the hand-rolled tracer goes, trace_task_id (tracer.py) -
            # into worker threads, where they previously took their defaults.
            # That is safe, for two reasons. Nothing reads them on a worker
            # thread: the permission code that reads the first two is async and
            # only ever runs on the event loop. And Context.run() restores the
            # thread's previous context when the callable returns, so a value
            # cannot outlive the submit that carried it and reach the next task
            # on this shared pool - "skip permission checks" in particular can
            # never bleed from one request into another's query. Where a value
            # would be read - a plugin calling datasette.in_client() or trace()
            # from inside an execute_fn callable - seeing the submitting
            # request's value is the more accurate answer, not a leak.
            ctx = contextvars.copy_context()
            future = self.ds.executor.submit(ctx.run, in_thread)
            self._pending_execute_futures.add(future)
        future.add_done_callback(self._remove_pending_execute_future)
        return await asyncio.wrap_future(future)

    async def execute(
        self,
        sql,
        params=None,
        truncate=False,
        custom_time_limit=None,
        page_size=None,
        log_sql_errors=True,
        table=None,
    ):
        """Executes sql against db_name in a thread

        `table`, if passed, is recorded as the `db.collection.name` span
        attribute. It exists for callers that already know which table the
        query targets - the table and row views - and is never derived from
        `sql` itself: deriving it would be a parse, and on an instance where
        anyone can create a table the resulting value set has no ceiling.
        """
        self._check_not_closed()
        page_size = page_size or self.ds.page_size
        time_limit_ms = self.ds.sql_time_limit_ms
        # A caller that hands in a budget shorter than the instance-wide
        # sql_time_limit_ms is saying "this may not finish, and that is an
        # answer I can use" - and every such caller in core does treat the
        # timeout as normal: table_counts() stores None per table, facet
        # suggestion moves on to the next column, autocomplete falls back to a
        # prefix query. Those timeouts are therefore not span errors. Without
        # this, the homepage alone emits one red span per table (it counts
        # every table under a 10ms budget) on every single hit.
        #
        # A query that runs out the instance-wide limit is a different event -
        # nobody asked for a short budget, so it stays an error.
        timeout_expected = bool(custom_time_limit) and custom_time_limit < time_limit_ms
        if timeout_expected:
            time_limit_ms = custom_time_limit

        def sql_operation_in_thread(conn):
            # This span is created inside the worker thread. Its parent is
            # resolved from the ambient otel context, which was propagated
            # onto this thread via copy_context() at the executor.submit()
            # boundary in _execute_fn() (or run_in_executor() for immutable
            # databases) - so it parents correctly to the enclosing
            # db.query span despite running on a different thread.
            #
            # Exception handling is explicit rather than left to the context
            # manager's flags, which apply to every exception type alike. This
            # span needs to tell two apart: an expected timeout is never an
            # error, while a genuine SQL failure is one unless the caller
            # passed log_sql_errors=False, meaning it was probing and treats
            # failure as an expected answer. Without the latter, facet
            # suggestion marks two spans per text column as failed on every
            # table page; without the former, so does every homepage hit.
            with tracer.start_as_current_span(
                DB_QUERY_EXECUTE,
                record_exception=False,
                set_status_on_exception=False,
            ) as execute_span:
                try:
                    with sqlite_timelimit(conn, time_limit_ms):
                        try:
                            cursor = conn.cursor()
                            cursor.execute(sql, params if params is not None else {})
                            max_returned_rows = self.ds.max_returned_rows
                            if max_returned_rows == page_size:
                                max_returned_rows += 1
                            if max_returned_rows and truncate:
                                rows = cursor.fetchmany(max_returned_rows + 1)
                                truncated = len(rows) > max_returned_rows
                                rows = rows[:max_returned_rows]
                            else:
                                rows = cursor.fetchall()
                                truncated = False
                        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                            if e.args == ("interrupted",):
                                raise QueryInterrupted(e, sql, params)
                            if log_sql_errors:
                                sys.stderr.write(
                                    f"ERROR: conn={conn}, sql = {sql!r}, params = {params}: {e}\n"
                                )
                                sys.stderr.flush()
                            raise
                except QueryInterrupted as e:
                    if not timeout_expected:
                        execute_span.record_exception(e)
                        execute_span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
                except Exception as e:
                    if log_sql_errors:
                        execute_span.record_exception(e)
                        execute_span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

                if truncate:
                    return Results(rows, truncated, cursor.description)

                else:
                    return Results(rows, False, cursor.description)

        # SIM117 wants these two context managers merged. They are kept nested
        # deliberately: the hand-rolled tracer's wrapper is on its way out, and
        # nesting makes removing it a single-line deletion.
        with trace(  # noqa: SIM117
            "sql", database=self.name, sql=sql.strip(), params=params
        ):
            # Exception handling is explicit rather than left to the context
            # manager's defaults, so that callers passing log_sql_errors=False
            # can be honoured - see the comment on the generic handler below.
            with tracer.start_as_current_span(
                DB_QUERY,
                kind=DB_QUERY.kind,
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                span.set_attribute(DB_SYSTEM, "sqlite")
                span.set_attribute(DB_NAMESPACE, self.name)
                span.set_attribute(DB_QUERY_TEXT, sql_attribute(sql))
                span.set_attribute(TIME_LIMIT_MS, time_limit_ms)
                operation_name = sql_operation_name(sql)
                if operation_name:
                    span.set_attribute(DB_OPERATION_NAME, operation_name)
                if table:
                    span.set_attribute(DB_COLLECTION_NAME, table)
                if params:
                    span.set_attribute(PARAM_COUNT, len(params))
                try:
                    with record_operation_duration(self.name, "read"):
                        results = await self._execute_fn(sql_operation_in_thread)
                except QueryInterrupted as e:
                    # datasette.interrupted is set either way - it is the
                    # signal worth having. Only the ERROR status is
                    # conditional; see the timeout_expected comment above.
                    span.set_attribute(INTERRUPTED, True)
                    if not timeout_expected:
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        span.record_exception(e)
                        # Expected timeouts (a caller that opted into a shorter
                        # budget, like facet suggestion) are not counted - see
                        # the M_QUERIES_INTERRUPTED registry entry for why.
                        record_query_interrupted(self.name)
                    raise
                except Exception as e:
                    # log_sql_errors=False means the caller is probing and
                    # treats failure as an expected answer, not an error.
                    # Facet suggestion is the big one: it runs json_type()
                    # against every column precisely to find out which ones
                    # raise, so a table with N text columns would otherwise
                    # mark N queries per page as failed - burying real errors
                    # and setting off any alerting based on span status.
                    if log_sql_errors:
                        span.record_exception(e)
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                    else:
                        span.set_attribute(SQL_ERROR_SUPPRESSED, True)
                    raise
                span.set_attribute(TRUNCATED, results.truncated)
                span.set_attribute(ROWS_RETURNED, len(results.rows))
        return results

    @property
    def hash(self):
        if self.cached_hash is not None:
            return self.cached_hash
        elif self.is_mutable or self.is_memory or self.is_temp_disk:
            return None
        elif self.ds.inspect_data and self.ds.inspect_data.get(self.name):
            self.cached_hash = self.ds.inspect_data[self.name]["hash"]
            return self.cached_hash
        else:
            p = Path(self.path)
            self.cached_hash = inspect_hash(p)
            return self.cached_hash

    @property
    def size(self):
        if self.cached_size is not None:
            return self.cached_size
        elif self.is_memory:
            return 0
        elif self.is_mutable:
            return Path(self.path).stat().st_size
        elif self.ds.inspect_data and self.ds.inspect_data.get(self.name):
            self.cached_size = self.ds.inspect_data[self.name]["size"]
            return self.cached_size
        else:
            self.cached_size = Path(self.path).stat().st_size
            return self.cached_size

    async def table_counts(self, limit=10):
        if not self.is_mutable and self.cached_table_counts is not None:
            return self.cached_table_counts
        # Try to get counts for each table, $limit timeout for each count
        counts = {}
        for table in await self.table_names():
            try:
                table_count = (
                    await self.execute(
                        f"select count(*) from (select * from {escape_sqlite(table)} limit {self.count_limit + 1})",
                        custom_time_limit=limit,
                    )
                ).rows[0][0]
                counts[table] = table_count
            # In some cases I saw "SQL Logic Error" here in addition to
            # QueryInterrupted - so we catch that too:
            except (QueryInterrupted, sqlite3.OperationalError, sqlite3.DatabaseError):
                counts[table] = None
        if not self.is_mutable:
            self._cached_table_counts = counts
        return counts

    @property
    def mtime_ns(self):
        if self.is_memory:
            return None
        return Path(self.path).stat().st_mtime_ns

    async def attached_databases(self):
        # This used to be:
        #   select seq, name, file from pragma_database_list() where seq > 0
        # But SQLite prior to 3.16.0 doesn't support pragma functions
        results = await self.execute("PRAGMA database_list;")
        # {'seq': 0, 'name': 'main', 'file': ''}
        return [
            AttachedDatabase(*row)
            for row in results.rows
            # Filter out the SQLite internal "temp" database, refs #2557
            if row["seq"] > 0 and row["name"] != "temp"
        ]

    async def table_exists(self, table):
        results = await self.execute(
            "select 1 from sqlite_master where type='table' and name=?", params=(table,)
        )
        return bool(results.rows)

    async def view_exists(self, table):
        results = await self.execute(
            "select 1 from sqlite_master where type='view' and name=?", params=(table,)
        )
        return bool(results.rows)

    async def table_names(self):
        results = await self.execute(
            "select name from sqlite_master where type='table' order by name"
        )
        return [r[0] for r in results.rows]

    # These callbacks are named functions rather than lambdas so that their
    # db.query spans carry a greppable datasette.callback - exactly the
    # guidance the plugin telemetry docs give, applied to core's own
    # highest-frequency introspection calls.

    async def table_columns(self, table):
        def _table_columns(conn):
            return table_columns(conn, table)

        return await self.execute_fn(_table_columns)

    async def table_column_details(self, table):
        def _table_column_details(conn):
            return table_column_details(conn, table)

        return await self.execute_fn(_table_column_details)

    async def primary_keys(self, table):
        def _primary_keys(conn):
            return detect_primary_keys(conn, table)

        return await self.execute_fn(_primary_keys)

    async def fts_table(self, table):
        def _fts_table(conn):
            return detect_fts(conn, table)

        return await self.execute_fn(_fts_table)

    async def label_column_for_table(self, table):
        explicit_label_column = (await self.ds.table_config(self.name, table)).get(
            "label_column"
        )
        if explicit_label_column:
            return explicit_label_column

        def column_details(conn):
            # Returns {column_name: (type, is_unique)}
            db = sqlite_utils.Database(conn)
            columns = db[table].columns_dict
            indexes = db[table].indexes
            details = {}
            for name in columns:
                is_unique = any(
                    index
                    for index in indexes
                    if index.columns == [name] and index.unique
                )
                details[name] = (columns[name], is_unique)
            return details

        column_details = await self.execute_fn(column_details)
        # Is there just one unique column that's text?
        unique_text_columns = [
            name
            for name, (type_, is_unique) in column_details.items()
            if is_unique and type_ is str
        ]
        if len(unique_text_columns) == 1:
            return unique_text_columns[0]

        column_names = list(column_details.keys())
        # Is there a name or title column?
        name_or_title = [c for c in column_names if c.lower() in ("name", "title")]
        if name_or_title:
            return name_or_title[0]
        # If a table has two columns, one of which is ID, then label_column is the other one
        if (
            column_names
            and len(column_names) == 2
            and ("id" in column_names or "pk" in column_names)
            and set(column_names) != {"id", "pk"}
        ):
            return next(c for c in column_names if c not in ("id", "pk"))
        # Couldn't find a label:
        return None

    async def foreign_keys_for_table(self, table):
        return await self.execute_fn(
            lambda conn: get_outbound_foreign_keys(conn, table)
        )

    async def hidden_table_names(self):
        hidden_tables = []
        # Add any tables marked as hidden in config
        db_config = self.ds.config.get("databases", {}).get(self.name, {})
        if "tables" in db_config:
            hidden_tables += [
                t for t in db_config["tables"] if db_config["tables"][t].get("hidden")
            ]

        hidden_tables += await self.execute_fn(sqlite_hidden_table_names)

        has_spatialite = await self.execute_fn(detect_spatialite)
        if has_spatialite:
            # Also hide Spatialite internal tables
            hidden_tables += [
                "ElementaryGeometries",
                "SpatialIndex",
                "geometry_columns",
                "spatial_ref_sys",
                "spatialite_history",
                "sql_statements_log",
                "sqlite_sequence",
                "views_geometry_columns",
                "virts_geometry_columns",
                "data_licenses",
                "KNN",
                "KNN2",
            ] + [
                r[0] for r in (await self.execute("""
                        select name from sqlite_master
                        where name like "idx_%"
                        and type = "table"
                    """)).rows
            ]

        return hidden_tables

    async def view_names(self):
        results = await self.execute("select name from sqlite_master where type='view'")
        return [r[0] for r in results.rows]

    async def get_all_foreign_keys(self):
        return await self.execute_fn(get_all_foreign_keys)

    async def get_table_definition(self, table, type_="table"):
        table_definition_rows = list(
            await self.execute(
                "select sql from sqlite_master where name = :n and type=:t",
                {"n": table, "t": type_},
            )
        )
        if not table_definition_rows:
            return None
        bits = [table_definition_rows[0][0] + ";"]
        # Add on any indexes
        index_rows = list(
            await self.execute(
                "select sql from sqlite_master where tbl_name = :n and type='index' and sql is not null",
                {"n": table},
            )
        )
        for index_row in index_rows:
            bits.append(index_row[0] + ";")
        return "\n".join(bits)

    async def get_view_definition(self, view):
        return await self.get_table_definition(view, "view")

    def __repr__(self):
        tags = []
        if self.is_mutable:
            tags.append("mutable")
        if self.is_memory:
            tags.append("memory")
        if self.is_temp_disk:
            tags.append("temp_disk")
        if self.hash:
            tags.append(f"hash={self.hash}")
        if self.size is not None:
            tags.append(f"size={self.size}")
        tags_str = ""
        if tags:
            tags_str = f" ({', '.join(tags)})"
        return f"<Database: {self.name}{tags_str}>"


def _apply_write_wrapper(fn, wrapper_factory, track_event):
    """Apply a single write_wrapper context manager around fn.

    ``wrapper_factory`` is a callable that takes ``(conn)`` and optionally
    ``track_event``, and returns a generator that yields exactly once.
    Code before the yield runs before ``fn(conn)``, code after the yield
    runs after.  The result of ``fn(conn)`` is sent into the generator
    via ``.send()``, and any exception raised by ``fn(conn)`` is thrown
    via ``.throw()``.
    """

    def wrapped(conn):
        gen = call_with_supported_arguments(
            wrapper_factory, conn=conn, track_event=track_event
        )
        # Advance to the yield point (run "before" code)
        try:
            next(gen)
        except StopIteration:
            # Generator didn't yield — just run fn unchanged
            return fn(conn)

        # Execute the actual write
        try:
            result = fn(conn)
        except Exception as e:
            # Throw exception into generator so it can handle it
            try:
                gen.throw(e)
            except StopIteration:
                pass
            # Re-raise the original exception
            raise
        else:
            # Send the result back through the yield
            try:
                gen.send(result)
            except StopIteration:
                pass
            return result

    return wrapped


class WriteTask:
    __slots__ = (
        "block",
        "enqueued_at_ns",
        "fn",
        "isolated_connection",
        "loop",
        "otel_context",
        "reply_future",
        "task_id",
        "transaction",
    )

    def __init__(
        self,
        fn,
        task_id,
        loop,
        reply_future,
        isolated_connection,
        transaction,
        otel_context,
        enqueued_at_ns,
        block,
    ):
        self.fn = fn
        self.task_id = task_id
        self.loop = loop
        self.reply_future = reply_future
        self.isolated_connection = isolated_connection
        self.transaction = transaction
        self.otel_context = otel_context
        self.enqueued_at_ns = enqueued_at_ns
        # Whether the enqueueing caller awaits the reply future. Decides how
        # `_execute_writes` relates this task's spans to `otel_context`:
        # parent (block=True) or span-link target (block=False). See the
        # comment at the WriteTask construction site in
        # `_send_to_write_thread`.
        self.block = block


def _deliver_write_result(task, result, exception):
    # Called from the write thread. Delivers the result back to the
    # awaiting coroutine on its event loop via call_soon_threadsafe.
    def _set():
        if task.reply_future.done():
            # Awaiter was cancelled; nothing to do.
            return
        if exception is not None:
            task.reply_future.set_exception(exception)
        else:
            task.reply_future.set_result(result)

    try:
        task.loop.call_soon_threadsafe(_set)
    except RuntimeError:
        # Event loop has been closed; the awaiter is gone.
        pass


class QueryInterrupted(Exception):
    def __init__(self, e, sql, params):
        self.e = e
        self.sql = sql
        self.params = params

    def __str__(self):
        return f"QueryInterrupted: {self.e}"


class MultipleValues(Exception):
    pass


class ExecuteWriteResult:
    def __init__(self, rowcount, lastrowid, description, rows, truncated):
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.description = description
        self.truncated = truncated
        self._rows = rows

    @classmethod
    def from_cursor(
        cls, cursor, return_all=False, returning_limit=EXECUTE_WRITE_RETURNING_LIMIT
    ):
        rows = []
        truncated = False
        description = cursor.description
        lastrowid = cursor.lastrowid
        try:
            if description is not None:
                if return_all:
                    rows = cursor.fetchall()
                else:
                    rows = cursor.fetchmany(returning_limit + 1)
                    if len(rows) > returning_limit:
                        rows = rows[:returning_limit]
                        truncated = True
            rowcount = cursor.rowcount
        finally:
            cursor.close()
        if description is not None and not return_all and truncated:
            rowcount = -1
        return cls(rowcount, lastrowid, description, rows, truncated)

    def fetchall(self):
        rows = self._rows
        self._rows = []
        return rows


class Results:
    def __init__(self, rows, truncated, description):
        self.rows = rows
        self.truncated = truncated
        self.description = description

    @property
    def columns(self):
        return [d[0] for d in self.description]

    def first(self):
        if self.rows:
            return self.rows[0]
        else:
            return None

    def single_value(self):
        if self.rows and 1 == len(self.rows) and 1 == len(self.rows[0]):
            return self.rows[0][0]
        else:
            raise MultipleValues

    def dicts(self):
        return [dict(row) for row in self.rows]

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)
