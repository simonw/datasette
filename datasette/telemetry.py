"""
OpenTelemetry integration for Datasette core.

Core depends on `opentelemetry-api` only. It never creates a
`TracerProvider`, never configures an exporter, and never touches
sampling - that is the responsibility of whoever is running Datasette
(an `opentelemetry-instrument` agent, a future plugin, or a test
harness).

With no provider installed every span produced here is a
`NonRecordingSpan`. That is not free - a table page emits ~100 spans -
but end-to-end page benchmarks put the overhead below their own
run-to-run variation. Installing an SDK provider is what costs
something measurable.
"""

import contextvars
import re

from opentelemetry import trace as otel_trace
from opentelemetry.propagate import extract
from opentelemetry.propagators.textmap import Getter
from opentelemetry.trace import SpanKind, Status, StatusCode

from .telemetry_registry import (
    ERROR_TYPE,
    HTTP_REQUEST_METHOD,
    HTTP_RESPONSE_STATUS_CODE,
    INTERNAL_CLIENT,
    SERVER_ADDRESS,
    URL_PATH,
    URL_SCHEME,
    USER_AGENT_ORIGINAL,
)
from .version import __version__

# True while code is executing within a datasette.client request. Defined
# here rather than in app.py (which owns its writers and the in_client()
# accessor) so TelemetryMiddleware can read it without a circular import:
# an in-process sub-request runs the full ASGI stack, so it emits a second,
# nested SERVER span - datasette.internal_client marks those so kind-based
# dashboards can filter the double-count out.
_in_datasette_client = contextvars.ContextVar("in_datasette_client", default=False)

# The semantic-convention version whose spellings this instrumentation
# actually emits. Deliberately NOT the latest release.
#
# A schema URL is a machine-readable claim: a consumer doing schema
# translation replays the renames between the declared version and the one
# it wants, so the claim has to name the version whose spellings are on the
# wire. A wrong one makes translation wrong rather than merely uninformative.
#
# Datasette emits `db.system`, which was renamed to `db.system.name` in
# semconv 1.30.0. Everything else it emits (`db.namespace`, `db.query.text`,
# `db.operation.name`, `db.collection.name`) has been current since 1.26.0.
# So 1.29.0 is the highest version at which every name emitted here is the
# current spelling. Everything under `datasette.*` is Datasette's own and
# outside semconv, so it is unaffected either way.
#
# Declaring 1.43.0 would be false about `db.system`, and would actively STOP
# a consumer translating it forward, because it asserts the rename already
# happened. Bump this deliberately, in the same commit as the attribute
# renames it implies - it is a claim about the names, not decoration.
SCHEMA_URL = "https://opentelemetry.io/schemas/1.29.0"

tracer = otel_trace.get_tracer("datasette", __version__, schema_url=SCHEMA_URL)

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

    `functools.partial` objects (and other callables) have no `__qualname__`,
    so fall back to the type's name rather than fail the query over telemetry.
    """
    return getattr(fn, "__qualname__", type(fn).__name__)


# db.operation.name is the leading keyword of a statement matched against a
# fixed allowlist - deliberately not a parse.
#
# This runs against arbitrary user-supplied SQL (the `?sql=` query string,
# canned queries, anything typed into the query editor), and the attribute is
# a candidate dimension on a query-duration metric in a later phase. A metric
# series is keyed by its attribute values, so echoing back an arbitrary first
# token would let one visitor's typo mint a new, permanent series. The
# allowlist bounds that at a fixed, small set regardless of what anyone sends.
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
    The statement's leading keyword, if it is one we recognise.

    Returns None - never a guess - for anything not on the allowlist,
    including a statement that opens with a comment or with punctuation such
    as the "(" of a parenthesised SELECT.

    Known limitation: a statement beginning with a CTE reports `WITH` rather
    than the operation inside it, and a substantial share of Datasette's own
    reads take that form. Extracting more than the leading keyword means
    handling comment stripping, parenthesised `(SELECT ...) UNION` and
    compound names like `CREATE TABLE` - each a special case a hand-rolled
    matcher would accrete and eventually get wrong. Omitting a name beats
    guessing at one.

    Only safe to call with a single statement: `execute_write_script()` runs
    several separated by semicolons, and semantic conventions say
    `db.operation.name` "SHOULD NOT be extracted from db.query.text, when the
    database system supports query text with multiple operations in non-batch
    operations" - so that call site does not use this at all rather than
    reporting only the first statement's operation.
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
    """
    Read W3C trace context out of an ASGI scope's headers.

    `scope["headers"]` is a list of `(bytes, bytes)` pairs, lowercased by the
    server per the ASGI spec - but `.lower()` is applied again here because
    that is a spec promise about servers, not something this process
    controls. Header bytes are latin-1 by RFC 9110.
    """

    def get(self, carrier, key):
        wanted = key.lower().encode("latin-1")
        values = [v.decode("latin-1") for k, v in carrier if k.lower() == wanted]
        return values or None

    def keys(self, carrier):
        return [k.decode("latin-1") for k, _ in carrier]


_HEADERS_GETTER = _ScopeHeadersGetter()


# An unclamped method is an unbounded dimension a client controls: anyone can
# send `FOO / HTTP/1.1`. Semantic conventions say map anything unrecognised to
# `_OTHER`. These nine are the methods of RFC 9110 plus PATCH (RFC 5789).
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

    `raw_path` is preferred because it is the bytes the client sent, before
    percent-decoding - Datasette routes on database and table names that can
    contain encoded slashes, which `scope["path"]` has already collapsed.

    The split on "?" is not decoration. The ASGI spec's `raw_path` excludes
    the query string, but the name is read both ways in the wild - httpx's
    own `raw_path` includes the query - and Datasette's query strings carry
    user-supplied SQL, which core never records. A literal "?" cannot appear
    unencoded in a path, so the defensive split costs nothing when the server
    is well behaved.
    """
    raw_path = scope.get("raw_path")
    if raw_path:
        if isinstance(raw_path, bytes):
            raw_path = raw_path.decode("latin-1")
        return raw_path.split("?", 1)[0]
    return scope.get("path", "")


# The request span is handed to `DatasetteRouter.route_path` through the ASGI
# scope rather than through `get_current_span()`, because by the time routing
# happens the current span may well be something else: a plugin
# `asgi_wrapper()` runs *inside* this middleware, and an instrumented one makes
# its own span current for the whole request. Reading the current span there
# would set `http.route` on that plugin's span - and rename it - while leaving
# the actual request span without the one attribute a trace UI groups by. Not
# hypothetical: an ordinary tracing plugin triggers it.
#
# Namespaced per the ASGI spec's rules for extension keys. Absent when the span
# is not recording, which is exactly when the router should skip the work too.
REQUEST_SPAN_SCOPE_KEY = "datasette.telemetry.request_span"


def request_span(scope):
    """
    The recording request span for an ASGI scope, or None.

    Falls back to the current span so that a `DatasetteRouter` running under
    some other instrumentation - one that started a SERVER span but of course
    knows nothing about this scope key - still gets enriched.
    """
    span = scope.get(REQUEST_SPAN_SCOPE_KEY)
    if span is None:
        span = otel_trace.get_current_span()
    # is_recording(), not `get_span_context().is_valid` - see the fast-path
    # comment in TelemetryMiddleware for why valid is not the same as recording.
    return span if span.is_recording() else None


class TelemetryMiddleware:
    """
    One `SpanKind.SERVER` span per HTTP request.

    Mounted outermost in `Datasette.app()`, so every other span raised while
    serving a request - database queries, plugin middleware, startup work on
    a cold ASGI-hosted deployment - has somewhere to belong instead of
    becoming its own root trace.

    Deliberately much smaller than `opentelemetry-instrumentation-asgi`,
    which needs several hundred lines of deferred-end machinery for
    applications that return before their body is sent. Datasette does not:
    `DatasetteRouter.route_path` awaits `response.asgi_send(send)`, and for a
    streaming CSV export `AsgiStream.asgi_send` runs the generator inline.
    All of it happens inside the single `await self.app(...)` below, so
    ending the span in a `finally` covers the response body too.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # First, before anything else: `AsgiLifespan` is *inside* this
        # middleware, so lifespan startup and shutdown have to pass through
        # untouched or the server never starts. Same for websockets.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or []
        # The *global* propagator, deliberately: it leaves the operator in
        # control with no Datasette-specific setting - OTEL_PROPAGATORS=none
        # disables extraction entirely, OTEL_PROPAGATORS=tracecontext drops
        # baggage - and core configuring propagation itself would be the same
        # mistake as core configuring sampling.
        context = extract(headers, getter=_HEADERS_GETTER)
        method = clamp_http_method(scope.get("method", ""))
        # The method, not the URL: a span name has to be low cardinality, and
        # the method is what is known out here at the edge, before any routing
        # has happened.
        with tracer.start_as_current_span(
            method, context=context, kind=SpanKind.SERVER
        ) as span:
            if not span.is_recording():
                # No provider installed, or a sampler dropped this trace.
                # Everything below would be discarded, so skip building the
                # `send` wrapper and let a default install pay almost
                # nothing. Note this cannot be `get_span_context().is_valid`:
                # with no provider but an inbound `traceparent`, the API's
                # NoOpTracer returns a NonRecordingSpan carrying the *remote*
                # context, which is perfectly valid and still records nothing.
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

            # A copy, not a mutation: the scope belongs to the server, and
            # every other layer in Datasette extends it the same way.
            scope = dict(scope, **{REQUEST_SPAN_SCOPE_KEY: span})

            # The status cannot be read off a Response object: `asgi_static`,
            # the favicon route, `AsgiStream` and `AsgiFileDownload` all call
            # `send` directly and never build one. Wrapping `send` is the only
            # thing that sees every response, including the 404 and 500
            # handlers.
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
                # BaseException, not Exception: `route_path` turns almost
                # everything into a 500 itself, but `asyncio.CancelledError`
                # on client disconnect is a BaseException its `except
                # Exception` deliberately does not catch.
                escaped = True
                span.set_attribute(ERROR_TYPE, type(exception).__name__)
                span.set_status(Status(StatusCode.ERROR, str(exception)))
                raise
            finally:
                status = status_holder.get("status")
                if status is not None:
                    span.set_attribute(HTTP_RESPONSE_STATUS_CODE, status)
                    # 4xx is NOT an error for a SERVER span per semantic
                    # conventions - the client made the mistake, not us.
                    #
                    # `not escaped` because this block still runs when an
                    # exception is on its way out, and a response can have
                    # started before it: the exception's class name is more
                    # use than the string "500", so it wins.
                    if status >= 500 and not escaped:
                        span.set_status(Status(StatusCode.ERROR))
                        span.set_attribute(ERROR_TYPE, str(status))
