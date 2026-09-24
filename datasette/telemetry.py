"""
OpenTelemetry integration for Datasette.

This uses `opentelemetry-api` only. Providers, exporters and sampling are
configured by whoever runs Datasette, for example `opentelemetry-instrument`.
"""

import contextvars
import re
import threading
import time
import weakref
from contextlib import contextmanager

from opentelemetry import context as otel_context_api
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.propagate import extract
from opentelemetry.propagators.textmap import Getter
from opentelemetry.trace import Link, SpanKind, Status, StatusCode, get_current_span

from .telemetry_registry import (
    DB_NAMESPACE,
    DB_SYSTEM,
    ERROR_TYPE,
    HTTP_REQUEST_METHOD,
    HTTP_RESPONSE_STATUS_CODE,
    INTERNAL_CLIENT,
    M_CONNECTIONS_OPEN,
    M_OPERATION_DURATION,
    M_QUERIES_INTERRUPTED,
    M_QUERIES_PENDING,
    M_THREADS_LIMIT,
    M_THREADS_QUEUE_DEPTH,
    M_WRITE_QUEUE_DEPTH,
    M_WRITE_QUEUE_WAIT,
    OPERATION,
    SERVER_ADDRESS,
    URL_PATH,
    URL_SCHEME,
    USER_AGENT_ORIGINAL,
)
from .version import __version__

# True while code is executing within a datasette.client request. Defined
# here rather than in app.py to avoid a circular import.
_in_datasette_client = contextvars.ContextVar("in_datasette_client", default=False)

# The semantic conventions version matching the attribute names used here.
# 1.30.0 renamed `db.system` to `db.system.name`, so update this when
# renaming attributes to match a newer version.
SCHEMA_URL = "https://opentelemetry.io/schemas/1.29.0"

tracer = otel_trace.get_tracer("datasette", __version__, schema_url=SCHEMA_URL)
meter = otel_metrics.get_meter("datasette", __version__, schema_url=SCHEMA_URL)

MAX_SQL_LENGTH = 2048


def sql_attribute(sql: str) -> str:
    "Truncate SQL text so it is safe to attach to a span as an attribute."
    sql = sql.strip()
    if len(sql) <= MAX_SQL_LENGTH:
        return sql
    return sql[:MAX_SQL_LENGTH] + "…[truncated]"


def callback_name(fn) -> str:
    """
    The name recorded as `datasette.callback` for a callback-style call.

    Falls back to the type name for callables such as `functools.partial`
    that have no `__qualname__`.
    """
    return getattr(fn, "__qualname__", type(fn).__name__)


def linked_root_span_kwargs(context=None):
    """
    Keyword arguments that start a new root span with a ``Link`` back to
    the current span.

    Use this for work that can outlive the span that caused it, such as a
    background task or a ``block=False`` write.

    Pass ``context`` to link to the span in a previously captured context
    instead of the current one. If there is no valid span, no link is added.

    Works with any tracer::

        with my_tracer.start_as_current_span(
            "myplugin.job", **linked_root_span_kwargs()
        ):
            ...
    """
    cause = get_current_span(context).get_span_context()
    links = [Link(cause)] if cause.is_valid else []
    return {"context": otel_context_api.Context(), "links": links}


# Keywords that can be recorded as db.operation.name. SQL can be supplied by
# users, so an allowlist keeps the number of distinct values small.
DB_OPERATION_ALLOWLIST = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "DROP",
        "ALTER",
        "PRAGMA",
        "EXPLAIN",
        "REPLACE",
        "VACUUM",
        "ANALYZE",
        "WITH",
    }
)

_LEADING_KEYWORD = re.compile(r"^\s*([A-Za-z]+)")


def sql_operation_name(sql: str) -> str | None:
    """
    The statement's leading keyword if it is in the allowlist, else None.

    Statements that start with a comment or "(" return None. Statements
    starting with a CTE return `WITH`. Only call this for a single statement.
    """
    match = _LEADING_KEYWORD.match(sql)
    if not match:
        return None
    keyword = match.group(1).upper()
    if keyword in DB_OPERATION_ALLOWLIST:
        return keyword
    return None


# --- The HTTP request span ------------------------------------------------


class _ScopeHeadersGetter(Getter):
    "Read W3C trace context from an ASGI scope's headers."

    def get(self, carrier, key):
        wanted = key.lower().encode("latin-1")
        values = [v.decode("latin-1") for k, v in carrier if k.lower() == wanted]
        return values or None

    def keys(self, carrier):
        return [k.decode("latin-1") for k, _ in carrier]


_HEADERS_GETTER = _ScopeHeadersGetter()


# Methods defined by RFC 9110 plus PATCH (RFC 5789). Anything else is
# recorded as `_OTHER`, as recommended by semantic conventions.
_KNOWN_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"}
)


def clamp_http_method(method):
    "The request method if it is one we recognise, else ``_OTHER``."
    method = (method or "").upper()
    return method if method in _KNOWN_METHODS else "_OTHER"


def _first_header(headers, name):
    "The first value of a header, decoded, or None."
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _url_path(scope):
    """
    The request path, with any query string removed.

    Prefers `raw_path`, which preserves encoded slashes in database and
    table names. Some clients include the query string in `raw_path`, so
    that is stripped as well.
    """
    raw_path = scope.get("raw_path")
    if raw_path:
        if isinstance(raw_path, bytes):
            raw_path = raw_path.decode("latin-1")
        return raw_path.split("?", 1)[0]
    return scope.get("path", "")


# The request span is passed to the router in the ASGI scope, because a
# plugin's asgi_wrapper() middleware may have made its own span current.
# Absent if the span is not recording.
REQUEST_SPAN_SCOPE_KEY = "datasette.telemetry.request_span"


def request_span(scope):
    """
    The recording request span for an ASGI scope, or None.

    Falls back to the current span, for when Datasette is running under
    other instrumentation.
    """
    span = scope.get(REQUEST_SPAN_SCOPE_KEY)
    if span is None:
        span = otel_trace.get_current_span()
    return span if span.is_recording() else None


class TelemetryMiddleware:
    """
    One `SpanKind.SERVER` span per HTTP request.

    The span ends after the full response, including any streamed body,
    has been sent.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Pass lifespan and websocket scopes straight through
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or []
        # Uses the global propagator, configured with OTEL_PROPAGATORS
        context = extract(headers, getter=_HEADERS_GETTER)
        method = clamp_http_method(scope.get("method", ""))
        # Renamed to include the route once routing has happened
        with tracer.start_as_current_span(
            method, context=context, kind=SpanKind.SERVER
        ) as span:
            if not span.is_recording():
                # No provider installed, or the trace was not sampled
                await self.app(scope, receive, send)
                return
            span.set_attribute(HTTP_REQUEST_METHOD, method)
            span.set_attribute(URL_PATH, _url_path(scope))
            scheme = scope.get("scheme")
            if scheme:
                span.set_attribute(URL_SCHEME, scheme)
            host = _first_header(headers, b"host")
            if host:
                span.set_attribute(SERVER_ADDRESS, host)
            user_agent = _first_header(headers, b"user-agent")
            if user_agent:
                span.set_attribute(USER_AGENT_ORIGINAL, user_agent)
            if _in_datasette_client.get():
                span.set_attribute(INTERNAL_CLIENT, True)

            scope = dict(scope, **{REQUEST_SPAN_SCOPE_KEY: span})

            # Some responses are sent without a Response object, so the
            # status is captured by wrapping send()
            status_holder = {}

            async def wrapped_send(message):
                if (
                    message["type"] == "http.response.start"
                    and "status" not in status_holder
                ):
                    status_holder["status"] = message["status"]
                await send(message)

            escaped = False
            try:
                await self.app(scope, receive, wrapped_send)
            except BaseException as exception:
                # Includes asyncio.CancelledError when a client disconnects
                escaped = True
                span.set_attribute(ERROR_TYPE, type(exception).__name__)
                span.set_status(Status(StatusCode.ERROR, str(exception)))
                raise
            finally:
                status = status_holder.get("status")
                if status is not None:
                    span.set_attribute(HTTP_RESPONSE_STATUS_CODE, status)
                    # 4xx responses are not errors for a server span. If an
                    # exception escaped, keep its class name as error.type.
                    if status >= 500 and not escaped:
                        span.set_status(Status(StatusCode.ERROR))
                        span.set_attribute(ERROR_TYPE, str(status))


# --- Metrics --------------------------------------------------------------


def _duration_attributes(database_name, operation):
    return {
        DB_SYSTEM: "sqlite",
        DB_NAMESPACE: database_name,
        OPERATION: operation,
    }


# Instruments use plain text descriptions. The registry entries have longer
# reStructuredText descriptions for the documentation.

sql_operation_duration = meter.create_histogram(
    M_OPERATION_DURATION,
    unit=M_OPERATION_DURATION.unit,
    description="Duration of a SQL operation issued by Datasette",
    explicit_bucket_boundaries_advisory=M_OPERATION_DURATION.buckets,
)

write_queue_wait = meter.create_histogram(
    M_WRITE_QUEUE_WAIT,
    unit=M_WRITE_QUEUE_WAIT.unit,
    description=(
        "Time a write spent queued behind the single write thread for its database"
    ),
    explicit_bucket_boundaries_advisory=M_WRITE_QUEUE_WAIT.buckets,
)

queries_interrupted = meter.create_counter(
    M_QUERIES_INTERRUPTED,
    unit=M_QUERIES_INTERRUPTED.unit,
    description="Queries cancelled for exceeding sql_time_limit_ms",
)


@contextmanager
def record_operation_duration(database_name, operation):
    """
    Record `db.client.operation.duration` for one SQL operation.

    Sets `error.type` to the exception class on failure. For a `block=False`
    write this measures the time taken to enqueue the write.
    """
    attributes = _duration_attributes(database_name, operation)
    started = time.perf_counter()
    try:
        yield
    except BaseException as exception:
        attributes[ERROR_TYPE] = type(exception).__qualname__
        raise
    finally:
        sql_operation_duration.record(time.perf_counter() - started, attributes)


def record_write_queue_wait(database_name, waited_ns):
    write_queue_wait.record(waited_ns / 1e9, {DB_NAMESPACE: database_name})


def record_query_interrupted(database_name):
    queries_interrupted.add(1, {DB_NAMESPACE: database_name})


# Live Datasette instances reported by the gauges below. The lock is needed
# because gauge callbacks run on the SDK's collection thread.
#
# The pool gauges do not identify which instance they came from, so they
# are only meaningful for a process running a single Datasette instance.
_live_datasettes = weakref.WeakSet()
_live_datasettes_lock = threading.Lock()


def register_datasette(ds):
    "Start reporting pool/queue gauges for this Datasette instance."
    with _live_datasettes_lock:
        _live_datasettes.add(ds)


def unregister_datasette(ds):
    "Stop reporting gauges for an instance that has been closed."
    with _live_datasettes_lock:
        _live_datasettes.discard(ds)


def _live_instances():
    with _live_datasettes_lock:
        return list(_live_datasettes)


def _databases_of(ds):
    "Every Database attached to an instance, including the internal database."
    databases = list(ds.databases.values())
    internal = getattr(ds, "_internal_database", None)
    if internal is not None:
        databases.append(internal)
    return databases


def observe_sql_thread_limit(options=None):
    "Size of the shared read-query thread pool (the num_sql_threads setting)."
    for ds in _live_instances():
        if ds.executor is None:
            # num_sql_threads=0 - queries run on the event loop, no pool.
            continue
        yield otel_metrics.Observation(ds.setting("num_sql_threads"), {})


def observe_sql_thread_queue_depth(options=None):
    """
    Read queries waiting for a free thread in the shared pool.

    `_work_queue` is a private attribute of ThreadPoolExecutor, so this
    reports nothing if it is missing.
    """
    for ds in _live_instances():
        if ds.executor is None:
            continue
        work_queue = getattr(ds.executor, "_work_queue", None)
        if work_queue is None:
            continue
        yield otel_metrics.Observation(work_queue.qsize(), {})


def observe_pending_queries(options=None):
    """
    Read queries submitted to the pool and not yet finished, per database.

    Reads `len()` without `_pending_execute_futures_lock` to avoid blocking
    queries.
    """
    for ds in _live_instances():
        for db in _databases_of(ds):
            yield otel_metrics.Observation(
                len(db._pending_execute_futures), {DB_NAMESPACE: db.name}
            )


def observe_write_queue_depth(options=None):
    "Writes queued behind the single write thread, per database."
    for ds in _live_instances():
        for db in _databases_of(ds):
            write_queue = db._write_queue
            if write_queue is None:
                # No write has ever been queued for this database.
                continue
            yield otel_metrics.Observation(write_queue.qsize(), {DB_NAMESPACE: db.name})


def observe_open_connections(options=None):
    "Open SQLite connections tracked for closing, per database."
    for ds in _live_instances():
        for db in _databases_of(ds):
            yield otel_metrics.Observation(
                len(db._all_connections), {DB_NAMESPACE: db.name}
            )


sql_thread_limit_gauge = meter.create_observable_gauge(
    M_THREADS_LIMIT,
    callbacks=[observe_sql_thread_limit],
    unit=M_THREADS_LIMIT.unit,
    description="Maximum concurrent read queries (the num_sql_threads setting)",
)

sql_thread_queue_depth_gauge = meter.create_observable_gauge(
    M_THREADS_QUEUE_DEPTH,
    callbacks=[observe_sql_thread_queue_depth],
    unit=M_THREADS_QUEUE_DEPTH.unit,
    description="Read queries waiting for a free thread in the shared SQL pool",
)

pending_queries_gauge = meter.create_observable_gauge(
    M_QUERIES_PENDING,
    callbacks=[observe_pending_queries],
    unit=M_QUERIES_PENDING.unit,
    description="Read queries submitted to the pool and not yet complete",
)

write_queue_depth_gauge = meter.create_observable_gauge(
    M_WRITE_QUEUE_DEPTH,
    callbacks=[observe_write_queue_depth],
    unit=M_WRITE_QUEUE_DEPTH.unit,
    description="Writes queued behind a database's single write thread",
)

open_connections_gauge = meter.create_observable_gauge(
    M_CONNECTIONS_OPEN,
    callbacks=[observe_open_connections],
    unit=M_CONNECTIONS_OPEN.unit,
    description="Open SQLite connections tracked for closing",
)
