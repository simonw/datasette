"""
Tests for the HTTP request span created by TelemetryMiddleware and the
`http.route` enrichment added by the router.
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

# Named in-memory databases are shared between instances, so each fixture
# needs a unique name.
_names = itertools.count()


PLUGIN_MIDDLEWARE_SPAN = "test.plugin.middleware"


class _MiddlewarePlugin:
    "A plugin asgi_wrapper() that creates a span."

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
    A plugin asgi_wrapper() that raises. `route_path` turns most exceptions
    into a 500, so this is how an exception reaches the request span.
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
    An instance whose table is bigger than `max_returned_rows`, so a
    `?_stream=1` export runs queries for later pages during the body send.
    """
    name = f"httpspanpaging{next(_names)}"
    # Both settings are needed: lowering only max_returned_rows gives a
    # single page with no `next` token.
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
    Spans created by plugin asgi_wrapper() middleware are children of the
    request span.
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

    # Database spans are in the same trace.
    queries = [span for span in spans if span.name == "db.query"]
    assert queries, "a table page should have issued at least one query"
    for query in queries:
        assert query.context.trace_id == server_span.context.trace_id


@pytest.mark.asyncio
async def test_unrecognised_method_is_clamped(ds, otel_spans):
    """
    Unknown methods are recorded as `_OTHER` in both the attribute and the
    span name, which the router rebuilds from the raw `request.method`.
    """
    otel_spans.clear()
    await ds.client.request("FROB", f"/{ds.db_name}/t")
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.request.method"] == "_OTHER"
    assert server[0].name == f"_OTHER {server[0].attributes['http.route']}"


@pytest.mark.asyncio
async def test_known_method_is_not_clamped(ds, otel_spans):
    "Known methods are recorded unchanged."
    otel_spans.clear()
    await ds.client.get(f"/{ds.db_name}/t")
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.request.method"] == "GET"
    assert server[0].name == f"GET {server[0].attributes['http.route']}"


@pytest.mark.asyncio
async def test_the_query_string_is_never_recorded(ds, otel_spans):
    "No attribute on any span contains the query string."
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
    An exception that escapes `route_path` is recorded and re-raised. No
    response started, so no status code is recorded.
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
    A 500 response followed by an exception records the exception class as
    `error.type`, not "500".
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
    assert server[0].attributes["http.response.status_code"] == 500
    assert server[0].attributes["error.type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_a_404_is_not_an_error(ds, otel_spans):
    """
    A 4xx records the status code but no `error.type` or error status.
    `/no-such-database-at-all` matches the database route, so `http.route`
    is still set.
    """
    otel_spans.clear()
    response = await ds.client.get("/no-such-database-at-all")
    assert response.status_code == 404
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.response.status_code"] == 404
    assert "error.type" not in server[0].attributes
    assert server[0].status.status_code is StatusCode.UNSET
    assert "http.route" in server[0].attributes
    assert server[0].name != "GET"


@pytest.mark.asyncio
async def test_an_unrouted_404_has_no_route_and_a_bare_method_name(ds, otel_spans):
    """
    With no matching route the span keeps the bare method name. Most missing
    paths still match a route, so this uses a path deeper than any route.
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
    "The `send` wrapper records the status from the first `http.response.start`."

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
    Lifespan scopes reach `AsgiLifespan`, which sits inside this middleware,
    without creating a SERVER span.
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
    "`http.route` is the compiled regex of the route Datasette's router resolves."
    path = f"/{ds.db_name}/t"
    expected = _route_for(ds, path)
    otel_spans.clear()
    assert (await ds.client.get(path)).status_code == 200
    server = _server_spans(otel_spans)
    assert len(server) == 1
    assert server[0].attributes["http.route"] == expected
    assert server[0].name == f"GET {expected}"
    # The raw pattern, not a prettified template:
    assert "(?P<database>" in expected


@pytest.mark.asyncio
async def test_the_route_lands_on_the_request_span_not_a_plugins_current_span(
    ds, otel_spans
):
    """
    The route is set on the span the middleware started, found through the
    ASGI scope, not on a plugin `asgi_wrapper()` span that is current during
    routing.
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
    # The plugin's span keeps its name and has no route attribute.
    plugin_spans = [span for span in spans if span.name == PLUGIN_MIDDLEWARE_SPAN]
    assert len(plugin_spans) == 1
    assert "http.route" not in (plugin_spans[0].attributes or {})


@pytest.mark.asyncio
async def test_request_span_attributes(ds, otel_spans):
    "The attributes recorded for an ordinary request."
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
    # The client IP address and query string are not recorded.
    assert "client.address" not in attributes
    assert "url.query" not in attributes


@pytest.mark.asyncio
async def test_db_query_spans_are_children_of_the_request_span(ds, otel_spans):
    """
    Every `db.query` span descends from the request span, which is the only
    root span.
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
        # Walk up to the root, which should be the request span.
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
    `route_path` turns the exception into a 500 response, so `error.type` is
    the status code as a string.
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
    The request span covers a streamed CSV body, including queries for later
    pages that run after the response has started.

    Driven as raw ASGI to timestamp `http.response.start` with `time.time_ns()`,
    the clock the SDK uses for spans.
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
    # 40 rows plus a header, so the export read past the first page
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
    An inbound `traceparent` header continues the caller's trace. It uses the
    sampled flag (`-01`) because the SDK's default sampler is parent-based.
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
    # Database spans are in the caller's trace too.
    queries = [
        span for span in otel_spans.get_finished_spans() if span.name == "db.query"
    ]
    assert queries
    for query in queries:
        assert f"{query.context.trace_id:032x}" == trace_id


@pytest.mark.asyncio
async def test_user_supplied_sql_in_the_query_string_is_never_recorded(ds, otel_spans):
    """
    SQL from `?sql=` is not recorded on the request span or in any `url.*`
    or `http.*` attribute. `db.query.text` is expected to contain it.
    """
    marker = "secret_marker_5b1f"
    otel_spans.clear()
    # `/{db}?sql=` redirects to the query view, so request that directly.
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
    # Confirm the query ran with the marker.
    assert marker in response.text


def test_request_span_skips_a_valid_but_non_recording_span():
    """
    `request_span()` returns None for a `NonRecordingSpan` with a valid remote
    span context, which is what an inbound `traceparent` produces with no
    provider installed.
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
    # No span in the scope and no current span:
    assert request_span({}) is None
    # A recording span is returned:
    with tracer.start_as_current_span("test.request_span.recording") as span:
        assert request_span({REQUEST_SPAN_SCOPE_KEY: span}) is span
        # Falls back to the current span, such as one created by another
        # SERVER instrumentation:
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
    With no `TracerProvider` installed the middleware passes the original
    `send` to the application, including for requests with a `traceparent`.

    Runs in a subprocess because the suite installs a provider for the whole
    process. conftest.py moves this test to the front of the run by name.
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
    # Nothing is stored in the scope either.
    assert report["scope_keys"] == [False, False]


@pytest.mark.asyncio
async def test_internal_client_requests_are_marked(ds, otel_spans):
    """
    `datasette.internal_client` is set on SERVER spans for `datasette.client`
    requests, but not for requests made directly to the ASGI app.
    """
    otel_spans.clear()
    assert (await ds.client.get("/")).status_code == 200
    server = _server_spans(otel_spans)
    assert server
    assert all(
        span.attributes.get("datasette.internal_client") is True for span in server
    )

    import httpx2

    transport = httpx2.ASGITransport(app=ds.app())
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://localhost"
    ) as client:
        otel_spans.clear()
        assert (await client.get("/")).status_code == 200
    server = _server_spans(otel_spans)
    assert server
    assert all("datasette.internal_client" not in span.attributes for span in server)
