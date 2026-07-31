"""
The HTTP request span.

`tests/test_telemetry_registry.py` already pins the span's name shape, kind
and attribute keys against literals, so this file deliberately does not
repeat that. What it covers is the three properties of the middleware that
the registry conformance test structurally cannot see:

- **where the middleware sits.** Outermost is the entire point - moving it
  inside the plugin `asgi_wrapper()` loop leaves plugin middleware creating
  orphan root traces, which is the problem this span exists to fix, and every
  attribute assertion still passes.
- **method clamping**, which a workload of ordinary GETs can never exercise.
- **the query string never being recorded**, which only fails if a request
  actually carries one.
"""

import asyncio
import itertools

import pytest
import pytest_asyncio

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.trace import SpanKind, StatusCode

from datasette import hookimpl
from datasette.app import Datasette
from datasette.telemetry import TelemetryMiddleware, tracer

# Named in-memory databases are shared-cache: two Datasette instances given
# the same name share one SQLite database and the second `create table`
# fails.
_names = itertools.count()


PLUGIN_MIDDLEWARE_SPAN = "test.plugin.middleware"


class _MiddlewarePlugin:
    "A plugin asgi_wrapper() that creates a span, standing in for a real one."

    __name__ = "HttpSpanMiddlewarePlugin"

    @hookimpl
    def asgi_wrapper(self, datasette):
        def wrap(app):
            async def wrapped(scope, receive, send):
                with tracer.start_as_current_span(PLUGIN_MIDDLEWARE_SPAN):
                    await app(scope, receive, send)

            return wrapped

        return wrap


class _RaisingMiddlewarePlugin:
    """
    A plugin asgi_wrapper() that raises.

    `route_path` converts almost every exception into a 500 itself, so an
    exception escaping into the request span is only reachable from *outside*
    the router - a plugin wrapper, or a failure inside the 500 handler.
    """

    __name__ = "HttpSpanRaisingMiddlewarePlugin"

    def __init__(self, call_app_first):
        self.call_app_first = call_app_first

    @hookimpl
    def asgi_wrapper(self, datasette):
        call_app_first = self.call_app_first

        def wrap(app):
            async def wrapped(scope, receive, send):
                if call_app_first:
                    await app(scope, receive, send)
                raise RuntimeError("wrapper exploded")

            return wrapped

        return wrap


class _BoomPlugin:
    "A route that raises, which route_path turns into a 500."

    __name__ = "HttpSpanBoomPlugin"

    @hookimpl
    def register_routes(self):
        return [(r"^/-/http-span-boom$", lambda: 1 / 0)]


@pytest_asyncio.fixture
async def ds():
    name = f"httpspan{next(_names)}"
    instance = Datasette(memory=True)
    instance.add_memory_database(name)
    await instance.invoke_startup()
    await instance.get_database(name).execute_write(
        "create table t (id integer primary key, v text)"
    )
    instance.db_name = name
    try:
        yield instance
    finally:
        instance.close()


def _server_spans(otel_spans):
    return [
        span for span in otel_spans.get_finished_spans() if span.kind is SpanKind.SERVER
    ]


@pytest.mark.asyncio
async def test_plugin_asgi_wrapper_middleware_runs_inside_the_request_span(
    ds, otel_spans
):
    """
    The placement check.

    A span created by a plugin `asgi_wrapper()` must be a *child* of the
    request span. If the middleware is mounted anywhere inside the plugin
    loop the two swap places - the plugin's span becomes the root and the
    request span its child - which is exactly the orphaning this is meant to
    prevent, and which no attribute assertion notices.
    """
    ds.pm.register(_MiddlewarePlugin(), name="httpspan-middleware")
    try:
        otel_spans.clear()
        response = await ds.client.get(f"/{ds.db_name}/t")
        assert response.status_code == 200
    finally:
        ds.pm.unregister(name="httpspan-middleware")

    spans = otel_spans.get_finished_spans()
    server = [span for span in spans if span.kind is SpanKind.SERVER]
    assert len(server) == 1, "expected exactly one SERVER span per request"
    request_span = server[0]
    assert request_span.parent is None, "the request span should be the trace root"

    plugin_spans = [span for span in spans if span.name == PLUGIN_MIDDLEWARE_SPAN]
    assert len(plugin_spans) == 1
    assert plugin_spans[0].parent is not None
    assert plugin_spans[0].parent.span_id == request_span.context.span_id
    assert plugin_spans[0].context.trace_id == request_span.context.trace_id

    # And the database work is in the same trace, not off on its own.
    queries = [span for span in spans if span.name == "db.query"]
    assert queries, "a table page should have issued at least one query"
    for query in queries:
        assert query.context.trace_id == request_span.context.trace_id


@pytest.mark.asyncio
async def test_unrecognised_method_is_clamped(ds, otel_spans):
    """
    Anyone can send `FROB / HTTP/1.1`. An unclamped method is an unbounded
    dimension a client controls, so semantic conventions map anything off the
    known list to `_OTHER` - and the span name is the method, so an unclamped
    one would put attacker-supplied text in the span name too.
    """
    otel_spans.clear()
    await ds.client.request("FROB", f"/{ds.db_name}/t")
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].name == "_OTHER"
    assert server[0].attributes["http.request.method"] == "_OTHER"


@pytest.mark.asyncio
async def test_known_method_is_not_clamped(ds, otel_spans):
    "The other half of clamping: a real method must survive it verbatim."
    otel_spans.clear()
    await ds.client.get(f"/{ds.db_name}/t")
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].name == "GET"
    assert server[0].attributes["http.request.method"] == "GET"


@pytest.mark.asyncio
async def test_the_query_string_is_never_recorded(ds, otel_spans):
    """
    Datasette puts user-supplied SQL in `?sql=` and canned query parameters in
    the query string, so no span may carry it. Asserting on the absence of a
    `url.query` key alone would not catch it arriving under some other name,
    so this searches every attribute value of every span for the marker.
    """
    marker = "canary-9f2b1c"
    otel_spans.clear()
    await ds.client.get(f"/{ds.db_name}/t?_facet=v&_nosuch={marker}")
    spans = otel_spans.get_finished_spans()
    assert _server_spans(otel_spans), "no request span was emitted"
    leaked = [
        f"{span.name} -> {key}={value!r}"
        for span in spans
        for key, value in (span.attributes or {}).items()
        if marker in str(value) or key == "url.query"
    ]
    assert not leaked, "the query string reached a span attribute: " + ", ".join(leaked)


@pytest.mark.asyncio
async def test_url_path_is_recorded_without_the_query_string(ds, otel_spans):
    otel_spans.clear()
    await ds.client.get(f"/{ds.db_name}/t?_facet=v")
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["url.path"] == f"/{ds.db_name}/t"


@pytest.mark.asyncio
async def test_escaping_exception_sets_error_type_and_reraises(ds, otel_spans):
    """
    An exception that gets past `route_path` must be recorded, not swallowed.

    No response ever started, so there is no status code to record either.
    """
    ds.pm.register(
        _RaisingMiddlewarePlugin(call_app_first=False), name="httpspan-raiser"
    )
    try:
        otel_spans.clear()
        with pytest.raises(RuntimeError):
            await ds.client.get(f"/{ds.db_name}/t")
    finally:
        ds.pm.unregister(name="httpspan-raiser")
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["error.type"] == "RuntimeError"
    assert "http.response.status_code" not in server[0].attributes
    assert server[0].status.status_code is StatusCode.ERROR


@pytest.mark.asyncio
async def test_an_escaping_exception_beats_the_status_code_for_error_type(
    ds, otel_spans
):
    """
    Both paths can fire on one request: a 500 response is sent and *then*
    something raises on the way out. The `finally` block runs while the
    exception is propagating, so without the guard it would overwrite the
    exception's class name with the string "500" - strictly less information
    about what actually went wrong.
    """
    ds.pm.register(_BoomPlugin(), name="httpspan-boom")
    ds.pm.register(
        _RaisingMiddlewarePlugin(call_app_first=True), name="httpspan-raiser"
    )
    try:
        otel_spans.clear()
        with pytest.raises(RuntimeError):
            await ds.client.get("/-/http-span-boom")
    finally:
        ds.pm.unregister(name="httpspan-raiser")
        ds.pm.unregister(name="httpspan-boom")
    server = _server_spans(otel_spans)
    assert len(server) == 1
    # The 500 really was sent, so the status is still recorded ...
    assert server[0].attributes["http.response.status_code"] == 500
    # ... but error.type names the exception, not the status.
    assert server[0].attributes["error.type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_a_404_is_not_an_error(ds, otel_spans):
    """
    Per semantic conventions a 4xx is the client's mistake, not the server's,
    so a SERVER span must record the status and leave both its own status and
    `error.type` alone. Datasette 404s are routine - every missing table, and
    every bot probing for /wp-login.php - so treating them as errors would
    drown a real 500 in noise.
    """
    otel_spans.clear()
    response = await ds.client.get("/no-such-database-at-all")
    assert response.status_code == 404
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.response.status_code"] == 404
    assert "error.type" not in server[0].attributes
    assert server[0].status.status_code is StatusCode.UNSET


@pytest.mark.asyncio
async def test_only_the_first_http_response_start_is_recorded(otel_spans):
    """
    The `send` wrapper keeps the first status it sees.

    Nothing in Datasette sends two `http.response.start` messages, so this
    drives the middleware directly rather than pretending a request could
    reach it. Without the guard a misbehaving plugin's second start message
    would silently replace the status the client actually received.
    """

    async def two_starts(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = TelemetryMiddleware(two_starts)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/twice",
        "raw_path": b"/twice",
        "scheme": "http",
        "headers": [],
    }
    otel_spans.clear()
    await middleware(scope, None, lambda message: asyncio.sleep(0))
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.response.status_code"] == 200
    assert "error.type" not in server[0].attributes


@pytest.mark.asyncio
async def test_lifespan_scope_passes_through_unspanned(otel_spans):
    """
    `AsgiLifespan` sits *inside* this middleware, so the scope-type check has
    to come first or startup and shutdown events never reach it. A SERVER
    span for a lifespan scope is the symptom of that check being missing or
    late.
    """
    instance = Datasette(memory=True)
    app = instance.app()
    events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent = []

    async def receive():
        return next(events)

    async def send(message):
        sent.append(message["type"])

    otel_spans.clear()
    await app({"type": "lifespan"}, receive, send)
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert not _server_spans(otel_spans)
