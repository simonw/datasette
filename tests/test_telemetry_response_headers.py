"""
Trace context in HTTP response headers: `traceresponse` and
`Server-Timing: traceparent`, emitted by `TelemetryMiddleware` only while the
request span is recording.
"""

import itertools

import pytest
import pytest_asyncio

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.trace import NoOpTracer, SpanKind

from datasette import hookimpl
from datasette.app import Datasette
import datasette.telemetry

_names = itertools.count()


@pytest_asyncio.fixture
async def ds():
    name = f"responseheaders{next(_names)}"
    instance = Datasette(memory=True)
    instance.add_memory_database(name)
    await instance.invoke_startup()
    instance.db_name = name
    try:
        yield instance
    finally:
        instance.close()


def _server_span(otel_spans):
    server = [
        span for span in otel_spans.get_finished_spans() if span.kind is SpanKind.SERVER
    ]
    assert len(server) == 1
    return server[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/-/does-not-exist"])
async def test_headers_point_at_the_server_span(ds, otel_spans, path):
    response = await ds.client.get(path)
    span = _server_span(otel_spans)
    expected = "00-{:032x}-{:016x}-{:02x}".format(
        span.context.trace_id, span.context.span_id, span.context.trace_flags
    )
    assert response.headers["traceresponse"] == expected
    assert response.headers["server-timing"] == f'traceparent;desc="{expected}"'
    # No CORS configured, so no expose-headers entry is invented.
    assert "access-control-expose-headers" not in response.headers


@pytest.mark.asyncio
async def test_inbound_traceparent_keeps_trace_id_but_not_span_id(ds, otel_spans):
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    client_span_id = "00f067aa0ba902b7"
    response = await ds.client.get(
        "/", headers={"traceparent": f"00-{trace_id}-{client_span_id}-01"}
    )
    _, got_trace_id, got_span_id, _ = response.headers["traceresponse"].split("-")
    assert got_trace_id == trace_id
    assert got_span_id != client_span_id
    assert got_span_id == f"{_server_span(otel_spans).context.span_id:016x}"


@pytest.mark.asyncio
async def test_absent_when_the_request_span_is_not_recording(ds, monkeypatch):
    # NoOpTracer is what the API hands out with no provider installed. The
    # inbound traceparent makes its span context valid, which must still not
    # be enough to emit headers.
    monkeypatch.setattr(datasette.telemetry, "tracer", NoOpTracer())
    response = await ds.client.get(
        "/",
        headers={
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        },
    )
    assert response.status_code == 200
    assert "traceresponse" not in response.headers
    assert "server-timing" not in response.headers
    assert "access-control-expose-headers" not in response.headers


class _ExposeHeadersPlugin:
    "Stands in for a CORS plugin that already set Access-Control-Expose-Headers."

    __name__ = "ExposeHeadersPlugin"

    def __init__(self, value):
        self.value = value

    @hookimpl
    def asgi_wrapper(self, datasette):
        value = self.value

        def wrap(app):
            async def wrapped(scope, receive, send):
                async def add_header(message):
                    if message["type"] == "http.response.start":
                        message = dict(
                            message,
                            headers=list(message.get("headers") or [])
                            + [(b"Access-Control-Expose-Headers", value)],
                        )
                    await send(message)

                await app(scope, receive, add_header)

            return wrapped

        return wrap


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing,expected", [(b"Link", "Link, traceresponse"), (b"*", "*")]
)
async def test_expose_headers_is_appended_not_clobbered(
    ds, otel_spans, existing, expected
):
    plugin = _ExposeHeadersPlugin(existing)
    ds.pm.register(plugin, name="expose-headers-plugin")
    try:
        response = await ds.client.get("/")
    finally:
        ds.pm.unregister(name="expose-headers-plugin")
    assert response.headers.get_list("access-control-expose-headers") == [expected]
    assert "traceresponse" in response.headers
