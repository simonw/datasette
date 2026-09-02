"""
The HTTP request span.

`tests/test_telemetry_registry.py` already pins the span's name shape, kind
and attribute keys against literals, so this file deliberately does not
repeat that. What it covers is the properties of the middleware and of the
router's `http.route` enrichment that the registry conformance test
structurally cannot see:

- **where the middleware sits.** Outermost is the entire point - moving it
  inside the plugin `asgi_wrapper()` loop leaves plugin middleware creating
  orphan root traces, which is the problem this span exists to fix, and every
  attribute assertion still passes.
- **which span the route lands on**, which only diverges once something else
  has made a span current.
- **method clamping**, which a workload of ordinary GETs can never exercise.
- **the query string never being recorded**, which only fails if a request
  actually carries one.
- **the span outliving a streamed response body**, which only a paging export
  can distinguish from ending far too early.
"""

import asyncio
import itertools
import json
import subprocess
import sys
import textwrap
import time

import pytest
import pytest_asyncio

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    SpanKind,
    StatusCode,
    TraceFlags,
)

from datasette import hookimpl
from datasette.app import Datasette
from datasette.telemetry import (
    REQUEST_SPAN_SCOPE_KEY,
    TelemetryMiddleware,
    request_span,
    tracer,
)
from datasette.utils import resolve_routes

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


@pytest_asyncio.fixture
async def ds_paging():
    """
    An instance whose table is bigger than `max_returned_rows`.

    That is what makes `?_stream=1` genuinely page: `stream_csv` loops calling
    `fetch_data` for each page *inside* the response body send, so the trace
    contains `db.query` spans that start after the response has begun. On a
    table that fits in one page every query finishes before the body starts
    and the span-covers-the-body assertion cannot fail.
    """
    name = f"httpspanpaging{next(_names)}"
    # Both settings matter. `?_stream=1` forces `_size=max`, which is
    # `max_returned_rows` - so lowering only that gives one page of five rows
    # and no `next` token, and the export never loops.
    instance = Datasette(
        memory=True, settings={"max_returned_rows": 5, "default_page_size": 3}
    )
    instance.add_memory_database(name)
    await instance.invoke_startup()
    db = instance.get_database(name)
    await db.execute_write("create table t (id integer primary key, v text)")
    await db.execute_write_many(
        "insert into t (id, v) values (?, ?)", [[i, f"v{i}"] for i in range(40)]
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


def _route_for(ds, path):
    "The compiled pattern Datasette's own router resolves `path` to."
    match, _view = resolve_routes(ds._routes(), path)
    assert match is not None, f"{path} matches no route"
    return match.re.pattern


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
    server_span = server[0]
    assert server_span.parent is None, "the request span should be the trace root"

    plugin_spans = [span for span in spans if span.name == PLUGIN_MIDDLEWARE_SPAN]
    assert len(plugin_spans) == 1
    assert plugin_spans[0].parent is not None
    assert plugin_spans[0].parent.span_id == server_span.context.span_id
    assert plugin_spans[0].context.trace_id == server_span.context.trace_id

    # And the database work is in the same trace, not off on its own.
    queries = [span for span in spans if span.name == "db.query"]
    assert queries, "a table page should have issued at least one query"
    for query in queries:
        assert query.context.trace_id == server_span.context.trace_id


@pytest.mark.asyncio
async def test_unrecognised_method_is_clamped(ds, otel_spans):
    """
    Anyone can send `FROB / HTTP/1.1`. An unclamped method is an unbounded
    dimension a client controls, so semantic conventions map anything off the
    known list to `_OTHER`.

    The span name is checked too, and it is the reason the router clamps the
    method a second time when it renames the span: the middleware's clamping
    protects the attribute, but the name is rebuilt from `request.method` in
    `route_path`, which is the raw client string. An unclamped rename would
    put attacker-supplied text straight back into the span name.
    """
    otel_spans.clear()
    await ds.client.request("FROB", f"/{ds.db_name}/t")
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.request.method"] == "_OTHER"
    assert server[0].name == f"_OTHER {server[0].attributes['http.route']}"


@pytest.mark.asyncio
async def test_known_method_is_not_clamped(ds, otel_spans):
    "The other half of clamping: a real method must survive it verbatim."
    otel_spans.clear()
    await ds.client.get(f"/{ds.db_name}/t")
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.request.method"] == "GET"
    assert server[0].name == f"GET {server[0].attributes['http.route']}"


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

    Note this 404 *does* match a route: `/no-such-database-at-all` matches the
    database pattern and the view then raises `NotFound`. Most Datasette 404s
    are that shape rather than the unrouted one below.
    """
    otel_spans.clear()
    response = await ds.client.get("/no-such-database-at-all")
    assert response.status_code == 404
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.response.status_code"] == 404
    assert "error.type" not in server[0].attributes
    assert server[0].status.status_code is StatusCode.UNSET
    # Route enrichment must not be gated on a successful response.
    assert "http.route" in server[0].attributes
    assert server[0].name != "GET"


@pytest.mark.asyncio
async def test_an_unrouted_404_has_no_route_and_a_bare_method_name(ds, otel_spans):
    """
    When no route matches there is nothing to set `http.route` to, so the span
    keeps the bare method name it was given at the edge - which is exactly the
    fallback semantic conventions specify for an unknown route.

    `/a/b/c/d/e` is used rather than a plausible-looking missing name because
    Datasette's route table is greedy: `/no-such-database-at-all` matches the
    database pattern, and `/-/nope/deeper` matches the row pattern. Only a
    path deeper than any route matches nothing at all.
    """
    otel_spans.clear()
    response = await ds.client.get("/a/b/c/d/e")
    assert response.status_code == 404
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].name == "GET"
    assert "http.route" not in server[0].attributes
    assert server[0].attributes["http.response.status_code"] == 404
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


@pytest.mark.asyncio
async def test_http_route_is_the_compiled_pattern(ds, otel_spans):
    """
    `http.route` is the route's compiled regex, not a prettified template.

    Asserted against what Datasette's own router resolves rather than against
    a copied literal, so this pins the *relationship* - the attribute is the
    matched route - and does not break when a core pattern is edited.
    """
    path = f"/{ds.db_name}/t"
    expected = _route_for(ds, path)
    otel_spans.clear()
    assert (await ds.client.get(path)).status_code == 200
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.route"] == expected
    assert server[0].name == f"GET {expected}"
    # The pattern really is the ugly one, and that is deliberate - if someone
    # adds a prettifier this is the assertion that should make them argue for
    # it rather than slip it in.
    assert "(?P<database>" in expected


@pytest.mark.asyncio
async def test_the_route_lands_on_the_request_span_not_a_plugins_current_span(
    ds, otel_spans
):
    """
    The route is set on the span the middleware started, found through the
    ASGI scope - not on whatever span happens to be current when routing
    resolves.

    Those are the same span only until a plugin `asgi_wrapper()` starts one of
    its own. A plugin wrapper runs *inside* this middleware, so an instrumented
    plugin makes its span current for the whole request: reading the current
    span in `route_path` renames that plugin's INTERNAL span to
    `GET <route>` and hangs `http.route` off it, while the actual request span
    keeps a bare method name and never gets the one attribute a trace UI
    groups requests by. Verified by reproducing it, not by reasoning about it.
    """
    ds.pm.register(_MiddlewarePlugin(), name="httpspan-middleware")
    try:
        otel_spans.clear()
        path = f"/{ds.db_name}/t"
        expected = _route_for(ds, path)
        assert (await ds.client.get(path)).status_code == 200
    finally:
        ds.pm.unregister(name="httpspan-middleware")

    spans = otel_spans.get_finished_spans()
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.route"] == expected
    assert server[0].name == f"GET {expected}"
    # And the plugin's span is untouched: same name, no route attribute.
    plugin_spans = [span for span in spans if span.name == PLUGIN_MIDDLEWARE_SPAN]
    assert len(plugin_spans) == 1
    assert "http.route" not in (plugin_spans[0].attributes or {})


@pytest.mark.asyncio
async def test_request_span_attributes(ds, otel_spans):
    "The whole attribute set on one ordinary request."
    path = f"/{ds.db_name}/t"
    otel_spans.clear()
    assert (await ds.client.get(path)).status_code == 200
    server = _server_spans(otel_spans)
    assert len(server) == 1
    attributes = server[0].attributes
    assert attributes["http.request.method"] == "GET"
    assert attributes["url.path"] == path
    assert attributes["url.scheme"] == "http"
    assert attributes["http.response.status_code"] == 200
    assert attributes["http.route"] == _route_for(ds, path)
    assert server[0].status.status_code is StatusCode.UNSET
    # Never, on any span: an IP is borderline PII and the query string carries
    # user-supplied SQL.
    assert "client.address" not in attributes
    assert "url.query" not in attributes


@pytest.mark.asyncio
async def test_db_query_spans_are_children_of_the_request_span(ds, otel_spans):
    """
    The point of the whole PR.

    Not just "same trace ID" - every `db.query` span must reach the request
    span by walking parents, and the request span must be the only root. A
    stray root would show up in a trace UI as its own single-span trace, which
    is the state this replaces.
    """
    otel_spans.clear()
    assert (await ds.client.get(f"/{ds.db_name}/t?_facet=v")).status_code == 200
    spans = otel_spans.get_finished_spans()
    server = _server_spans(otel_spans)
    assert len(server) == 1
    server_span = server[0]
    assert server_span.parent is None

    by_span_id = {span.context.span_id: span for span in spans}
    roots = [span for span in spans if span.parent is None]
    assert [span.name for span in roots] == [server_span.name], (
        "every span from a request should hang off the request span, but these "
        f"are roots: {sorted(span.name for span in roots)}"
    )

    queries = [span for span in spans if span.name == "db.query"]
    assert queries, "a faceted table page should have issued queries"
    for query in queries:
        assert query.context.trace_id == server_span.context.trace_id
        # Walk up to the root, which must be the request span.
        current = query
        seen = 0
        while current.parent is not None:
            current = by_span_id[current.parent.span_id]
            seen += 1
            assert seen < 20, "parent chain did not terminate"
        assert current is server_span


@pytest.mark.asyncio
async def test_500_sets_error_status_and_error_type(ds, otel_spans):
    """
    A plain 500 - no exception escaping the app, because `route_path` converts
    it into a response itself. The status is the only signal the middleware
    gets, so `error.type` is the status as a string.
    """
    ds.pm.register(_BoomPlugin(), name="httpspan-boom")
    try:
        otel_spans.clear()
        response = await ds.client.get("/-/http-span-boom")
        assert response.status_code == 500
    finally:
        ds.pm.unregister(name="httpspan-boom")
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.response.status_code"] == 500
    assert server[0].attributes["error.type"] == "500"
    assert server[0].status.status_code is StatusCode.ERROR


@pytest.mark.asyncio
async def test_csv_stream_span_covers_the_body_send(ds_paging, otel_spans):
    """
    The span must not end when the handler returns - it has to cover the
    response body.

    `stream_csv` runs its generator inline inside `AsgiStream.asgi_send`, and
    that call happens inside the single `await self.app(...)` the middleware
    makes, so a plain `finally` is enough and no deferred-end machinery is
    needed. This is the assertion that holds that claim up: a `db.query` that
    starts during the body send must still finish before the request span
    does.

    Only meaningful on an export that actually pages, hence `ds_paging` - on a
    single-page table every query is over before the body begins and this
    passes however early the span ends. The middle assertion below, that some
    query *started* after `http.response.start` went out, is what keeps the
    test honest about that; it is why the app is driven as raw ASGI rather
    than through `ds.client`, which cannot timestamp the response start.

    `time.time_ns()` is the same clock the SDK stamps spans with, so the two
    are directly comparable.
    """
    app = ds_paging.app()
    body = []
    response_started_at = None

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal response_started_at
        if message["type"] == "http.response.start":
            assert message["status"] == 200
            response_started_at = time.time_ns()
        else:
            body.append(message.get("body") or b"")

    otel_spans.clear()
    await app(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": f"/{ds_paging.db_name}/t.csv",
            "raw_path": f"/{ds_paging.db_name}/t.csv".encode("latin-1"),
            "query_string": b"_stream=1",
            "scheme": "http",
            "headers": [(b"host", b"localhost")],
        },
        receive,
        send,
    )
    # 40 rows plus a header - the export really did read past one page
    assert len(b"".join(body).decode("utf-8").strip().splitlines()) == 41
    assert response_started_at is not None

    spans = otel_spans.get_finished_spans()
    server = _server_spans(otel_spans)
    assert len(server) == 1
    server_span = server[0]
    queries = [span for span in spans if span.name == "db.query"]
    assert len(queries) > 1
    during_body = [span for span in queries if span.start_time > response_started_at]
    assert during_body, (
        "no query ran after the response started, so this workload cannot "
        "distinguish a span that covers the body send from one that ends when "
        "the handler returns - the export is not paging"
    )
    last_query_end = max(span.end_time for span in queries)
    assert server_span.end_time > last_query_end, (
        "the request span ended before the last query of a streaming export - "
        "it is not covering the response body"
    )
    for query in queries:
        assert query.context.trace_id == server_span.context.trace_id


@pytest.mark.asyncio
async def test_inbound_traceparent_becomes_the_parent(ds, otel_spans):
    """
    W3C trace context is extracted with the global propagator, so a request
    from an already-traced caller continues that trace.

    The sampled flag has to be set: the SDK's default sampler is
    parentbased_always_on, so a `-00` flag would drop the span and the test
    would fail for a reason that has nothing to do with propagation.
    """
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    parent_span_id = "00f067aa0ba902b7"
    otel_spans.clear()
    response = await ds.client.get(
        f"/{ds.db_name}/t",
        headers={"traceparent": f"00-{trace_id}-{parent_span_id}-01"},
    )
    assert response.status_code == 200
    server = _server_spans(otel_spans)
    assert len(server) == 1
    server_span = server[0]
    assert f"{server_span.context.trace_id:032x}" == trace_id
    assert server_span.parent is not None
    assert f"{server_span.parent.span_id:016x}" == parent_span_id
    assert server_span.parent.is_remote
    # And the database spans joined the caller's trace too, not a new one.
    queries = [
        span for span in otel_spans.get_finished_spans() if span.name == "db.query"
    ]
    assert queries
    for query in queries:
        assert f"{query.context.trace_id:032x}" == trace_id


@pytest.mark.asyncio
async def test_user_supplied_sql_in_the_query_string_is_never_recorded(ds, otel_spans):
    """
    The `?sql=` case specifically, which is the one that matters: this is the
    request where the query string *is* user-supplied SQL, and it reaches a
    view that runs it. The marker is searched for across every attribute of
    every span in the trace, not just for a `url.query` key, so recording it
    under some other name fails too.

    `db.query.text` legitimately contains the SQL - that is documented and
    deliberate - so the marker is checked against the request span's own
    attributes, and against `url.*` and `http.*` keys everywhere.
    """
    marker = "secret_marker_5b1f"
    otel_spans.clear()
    # `/{db}?sql=` 302s to the query view, so go straight there - a redirect
    # would leave the SQL only on a span for a request that never ran it.
    response = await ds.client.get(f"/{ds.db_name}/-/query?sql=select+'{marker}'")
    assert response.status_code == 200
    spans = otel_spans.get_finished_spans()
    server = _server_spans(otel_spans)
    assert len(server) == 1
    leaked = [
        f"{span.name} -> {key}={value!r}"
        for span in spans
        for key, value in (span.attributes or {}).items()
        if (span is server[0] or str(key).startswith(("url.", "http.")))
        and (marker in str(value) or str(key) == "url.query")
    ]
    assert not leaked, "the query string reached a span attribute: " + ", ".join(leaked)
    # The request really did carry the marker, so the search above had
    # something to find.
    assert marker in response.text


def test_request_span_skips_a_valid_but_non_recording_span():
    """
    `request_span()` is guarded on `is_recording()`, not on
    `get_span_context().is_valid`, and this is the case that separates them.

    With no provider installed but an inbound `traceparent`, the API's
    NoOpTracer hands back a `NonRecordingSpan` carrying the *remote* span
    context - valid, sampled, and recording nothing. An `is_valid` guard would
    wave that through and the router would build the name string and call
    `set_attribute`/`update_name` on a span that discards both.

    Tested at this level deliberately: through a real request the two guards
    are indistinguishable, because every call the router makes on a
    NonRecordingSpan is already a no-op. The only difference is the work done
    to get there, so the guard itself is what has to be asserted on.
    """
    remote = SpanContext(
        trace_id=0x4BF92F3577B34DA6A3CE929D0E0E4736,
        span_id=0x00F067AA0BA902B7,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    assert remote.is_valid
    non_recording = NonRecordingSpan(remote)
    assert non_recording.is_recording() is False
    assert request_span({REQUEST_SPAN_SCOPE_KEY: non_recording}) is None
    # Nothing current, nothing in the scope: the INVALID_SPAN fallback.
    assert request_span({}) is None
    # And the case it must not skip.
    with tracer.start_as_current_span("test.request_span.recording") as span:
        assert request_span({REQUEST_SPAN_SCOPE_KEY: span}) is span
        # Falling back to the current span is how an externally installed
        # SERVER span still gets enriched.
        assert request_span({}) is span


NO_PROVIDER_PROGRAM = textwrap.dedent("""
    import asyncio, json, sys

    from datasette.telemetry import TelemetryMiddleware

    seen = {}


    async def inner(scope, receive, send):
        seen.setdefault("sends", []).append(send)
        seen.setdefault("scopes", []).append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


    async def real_send(message):
        pass


    async def main():
        middleware = TelemetryMiddleware(inner)
        for headers in ([], [(b"traceparent", b"00-" + b"a" * 32 + b"-" + b"b" * 16 + b"-01")]):
            await middleware(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/",
                    "raw_path": b"/",
                    "scheme": "http",
                    "headers": headers,
                },
                None,
                real_send,
            )
        print(
            json.dumps(
                {
                    "unwrapped": [send is real_send for send in seen["sends"]],
                    "scope_keys": [
                        "datasette.telemetry.request_span" in scope
                        for scope in seen["scopes"]
                    ],
                    "sdk_imported": any(
                        name.startswith("opentelemetry.sdk") for name in sys.modules
                    ),
                }
            )
        )


    asyncio.run(main())
    """)


def test_no_provider_takes_the_fast_path():
    """
    With no `TracerProvider` installed the middleware must hand the
    application the *original* `send`, not a wrapper - a default Datasette
    install should pay essentially nothing for instrumentation it is not
    using.

    This has to run in a subprocess. The suite's `_otel_provider` fixture is
    session-scoped and autouse, and `set_tracer_provider()` is effectively
    once-per-process, so in-process every span is recording and the fast path
    is unreachable.

    The second case, with an inbound `traceparent`, is the one that pins the
    check itself. With no provider the API's NoOpTracer returns a
    NonRecordingSpan carrying the *remote* span context: its
    `get_span_context().is_valid` is True while `is_recording()` is False. A
    fast path guarded on `is_valid` would therefore silently stop working for
    exactly the requests that arrive from an already-traced caller - which on
    a real deployment behind an instrumented proxy is all of them.

    conftest.py's pytest_collection_modifyitems() moves this test to the front
    of the run by name - if you rename it, rename it there too.
    """
    result = subprocess.run(
        [sys.executable, "-c", NO_PROVIDER_PROGRAM],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["sdk_imported"] is False, "the SDK loaded in a fresh interpreter"
    assert report["unwrapped"] == [True, True], (
        "the middleware wrapped `send` with no provider installed; the second "
        "entry is the inbound-traceparent case, which fails if the fast path "
        "is guarded on is_valid instead of is_recording()"
    )
    # Same fast path, other observable: nothing is stashed in the scope either.
    assert report["scope_keys"] == [False, False]


@pytest.mark.asyncio
async def test_internal_client_requests_are_marked(ds, otel_spans):
    """
    An in-process `datasette.client` request runs the full ASGI stack, so it
    emits its own SERVER span - `datasette.internal_client` marks those so
    kind-based dashboards can filter the double-count out. A request that
    arrives through the raw ASGI app (the shape of a real inbound request,
    without the DatasetteClient wrapper setting the ContextVar) must not
    carry the attribute.
    """
    otel_spans.clear()
    assert (await ds.client.get("/")).status_code == 200
    server = _server_spans(otel_spans)
    assert server
    assert all(
        span.attributes.get("datasette.internal_client") is True for span in server
    )

    import httpx

    transport = httpx.ASGITransport(app=ds.app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost"
    ) as client:
        otel_spans.clear()
        assert (await client.get("/")).status_code == 200
    server = _server_spans(otel_spans)
    assert server
    assert all("datasette.internal_client" not in span.attributes for span in server)
