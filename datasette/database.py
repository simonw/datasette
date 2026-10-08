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
from .utils.sqlite import sqlite_derived_table_dependencies, sqlite_hidden_table_names

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
        self._cached_derived_table_dependencies = None
        self._write_thread = None
        self._write_queue = None
        self._closed = False
        self._pending_execute_futures = set()
        self._pending_execute_futures_lock = threading.Lock()
        # These are used when in non-threaded mode:
        self._read_connection = None
        self._write_connection = None
        # Track file and memory connections, including reads on worker threads,
        # so close() can release all of them from the calling thread.
        self._all_connections = []
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
            self._all_connections.append(conn)
            return conn
        if self.is_memory:
            conn = sqlite3.connect(":memory:", uri=True, check_same_thread=False)
            self._all_connections.append(conn)
            return conn

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
        self._all_connections.append(conn)
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
        # Close anything still tracked in _all_connections
        for connection in self._all_connections:
            try:
                connection.close()
            except Exception:  # noqa: BLE001, S110
                pass
        self._all_connections = []
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
        time_limit_ms=2000,
    ):
        self._check_not_closed()
        if returning_limit < 0:
            raise ValueError("returning_limit must be >= 0")

        def execute_sql(conn):
            cursor = conn.execute(sql, params or [])
            return ExecuteWriteResult.from_cursor(
                cursor, return_all=return_all, returning_limit=returning_limit
            )

        def _inner(conn):
            try:
                if time_limit_ms is None:
                    return execute_sql(conn)
                with sqlite_timelimit(conn, time_limit_ms):
                    return execute_sql(conn)
            except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                if e.args == ("interrupted",):
                    raise QueryInterrupted(e, sql, params)
                raise

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

        with trace(  # noqa: SIM117
            "sql", database=self.name, sql=sql.strip(), executescript=True
        ):
            # No db.operation.name, since the script can contain multiple statements
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

        with trace(
            "sql", database=self.name, sql=sql.strip(), executemany=True
        ) as kwargs:
            with tracer.start_as_current_span(DB_QUERY, kind=DB_QUERY.kind) as span:
                span.set_attribute(DB_SYSTEM, "sqlite")
                span.set_attribute(DB_NAMESPACE, self.name)
                span.set_attribute(DB_QUERY_TEXT, sql_attribute(sql))
                span.set_attribute(EXECUTEMANY, True)
                operation_name = sql_operation_name(sql)
                if operation_name:
                    span.set_attribute(DB_OPERATION_NAME, operation_name)
                with record_operation_duration(self.name, "write"):
                    results, count = await self._execute_write_fn(
                        _inner, block=block, request=request
                    )
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
                    self._all_connections.remove(isolated_connection)
                except ValueError:
                    # May already have been cleared by close().
                    pass

        with tracer.start_as_current_span(DB_QUERY, kind=DB_QUERY.kind) as span:
            span.set_attribute(DB_SYSTEM, "sqlite")
            span.set_attribute(DB_NAMESPACE, self.name)
            span.set_attribute(CALLBACK, callback_name(fn))
            # Immutable databases run this on the read pool, not the write queue
            with record_operation_duration(self.name, "write" if write else "read"):
                if self.ds.executor is None:
                    # non-threaded mode
                    return _run()
                if not write:
                    # Immutable database - no writes can ever occur, so there
                    # is no write queue to block; run against a fresh
                    # read-only connection
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
        """Run `fn(conn)` on the write connection, traced as a `db.query` span.

        The SQL-string write methods call `_execute_write_fn()` directly to
        avoid creating a second span.
        """
        self._check_not_closed()
        # Record the name before _wrap_fn_with_hooks() wraps fn
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
            if not block:
                # There is no write thread here, so the write has already
                # finished. Hand back the same (task_id, reply_future) shape
                # _send_to_write_thread() returns, with the future already
                # resolved, so the block=False path below is identical in
                # both modes.
                reply_future = asyncio.get_running_loop().create_future()
                reply_future.set_result(result)
                result = (uuid.uuid4(), reply_future)
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
        task_id = uuid.uuid4()
        loop = asyncio.get_running_loop()
        reply_future = loop.create_future()
        # Capture the OpenTelemetry context and enqueue time for the write thread
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
            # Threads do not inherit the caller's context, so any spans
            # created by prepare_connection hooks here are root spans
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
            # block=True: the caller awaits the result, so the write spans
            # are children of the caller's span. The token must be detached
            # in the finally block or the context leaks into later writes.
            # block=False: the caller may finish first, so the write spans
            # are root spans with a link back to the caller's span.
            token = None
            write_span_kwargs = {}
            if task.block:
                token = otel_context_api.attach(task.otel_context)
            else:
                write_span_kwargs = linked_root_span_kwargs(task.otel_context)
            try:
                exception = None
                result = None
                # Span covers the time from enqueue to dequeue
                dequeued_at_ns = time.time_ns()
                tracer.start_span(
                    DB_WRITE_QUEUE_WAIT,
                    start_time=task.enqueued_at_ns,
                    **write_span_kwargs,
                ).end(end_time=dequeued_at_ns)
                record_write_queue_wait(self.name, dequeued_at_ns - task.enqueued_at_ns)
                if conn_exception is not None:
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
                                    self._all_connections.remove(isolated_connection)
                                except ValueError:
                                    # May already have been cleared by close().
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
        """Run `fn(conn)` on a read connection, traced as a `db.query` span.

        `execute()` calls `_execute_fn()` directly to avoid creating a second
        span.
        """
        self._check_not_closed()

        def fn_in_execute_span(conn):
            # Runs on the worker thread
            with tracer.start_as_current_span(DB_QUERY_EXECUTE):
                return fn(conn)

        with tracer.start_as_current_span(DB_QUERY, kind=DB_QUERY.kind) as span:
            span.set_attribute(DB_SYSTEM, "sqlite")
            span.set_attribute(DB_NAMESPACE, self.name)
            span.set_attribute(CALLBACK, callback_name(fn))
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
            # Run in a copy of the caller's context so spans created in the
            # thread have the correct parent. This needs a fresh copy for
            # each submit, since a Context cannot be entered concurrently.
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
    ):
        """Executes sql against db_name in a thread"""
        self._check_not_closed()
        page_size = page_size or self.ds.page_size
        time_limit_ms = self.ds.sql_time_limit_ms
        # Callers that pass a shorter custom_time_limit, such as table counts
        # and facet suggestions, expect timeouts, so they are not span errors
        timeout_expected = bool(custom_time_limit) and custom_time_limit < time_limit_ms
        if timeout_expected:
            time_limit_ms = custom_time_limit

        def sql_operation_in_thread(conn):
            # Expected timeouts and errors with log_sql_errors=False are not
            # recorded as span errors, so exceptions are handled explicitly
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

        with trace(  # noqa: SIM117
            "sql", database=self.name, sql=sql.strip(), params=params
        ):
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
                if params:
                    span.set_attribute(PARAM_COUNT, len(params))
                try:
                    with record_operation_duration(self.name, "read"):
                        results = await self._execute_fn(sql_operation_in_thread)
                except QueryInterrupted as e:
                    span.set_attribute(INTERRUPTED, True)
                    if not timeout_expected:
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        span.record_exception(e)
                        record_query_interrupted(self.name)
                    raise
                except Exception as e:
                    # log_sql_errors=False callers, such as facet suggestion,
                    # expect some queries to fail
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

    # Named functions rather than lambdas give more useful datasette.callback
    # span attributes

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

    async def derived_table_dependencies(self):
        """Return implementation tables and the tables they derive from."""
        schema_version = (await self.execute("PRAGMA schema_version")).first()[0]
        if (
            self._cached_derived_table_dependencies is None
            or self._cached_derived_table_dependencies[0] != schema_version
        ):
            dependencies = await self.execute_fn(sqlite_derived_table_dependencies)
            self._cached_derived_table_dependencies = (schema_version, dependencies)
        return self._cached_derived_table_dependencies[1]

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
