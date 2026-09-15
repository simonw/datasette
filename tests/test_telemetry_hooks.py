"""
`datasette.hook` spans: one per plugin hookimpl call, covering the await for
an async implementation.
"""

import asyncio
import itertools

import pytest
import pytest_asyncio

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.trace import SpanKind

from datasette import hookimpl
from datasette.app import Datasette
from datasette.utils import await_me_maybe

_names = itertools.count()


@pytest_asyncio.fixture
async def ds():
    instance = Datasette(memory=True)
    await instance.invoke_startup()
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def register(ds):
    "Register a plugin object for the duration of one test."
    names = []

    def _register(plugin):
        name = f"hook-spans-test-{next(_names)}"
        ds.pm.register(plugin, name=name)
        names.append(name)
        return name

    yield _register
    for name in names:
        ds.pm.unregister(name=name)


def _hook_spans(otel_spans, plugin_name):
    return [
        span
        for span in otel_spans.get_finished_spans()
        if span.name == "datasette.hook"
        and span.attributes.get("datasette.plugin.name") == plugin_name
    ]


@pytest.mark.asyncio
async def test_sync_hookimpl_emits_span(ds, register, otel_spans):
    class Plugin:
        @hookimpl
        def menu_links(self, datasette, actor, request):
            return [{"href": "/sync", "label": "Sync"}]

    name = register(Plugin())
    response = await ds.client.get("/")
    assert response.status_code == 200
    (span,) = _hook_spans(otel_spans, name)
    assert dict(span.attributes) == {
        "datasette.hook.name": "menu_links",
        "datasette.plugin.name": name,
    }


@pytest.mark.asyncio
async def test_async_hookimpl_span_covers_the_await(ds, register, otel_spans):
    class Plugin:
        @hookimpl
        async def menu_links(self, datasette, actor, request):
            await asyncio.sleep(0.05)
            return []

    name = register(Plugin())
    await ds.client.get("/")
    (span,) = _hook_spans(otel_spans, name)
    assert span.attributes["datasette.hook.name"] == "menu_links"
    assert span.end_time - span.start_time >= 50_000_000


@pytest.mark.asyncio
async def test_hook_span_is_parented_to_request_span(ds, register, otel_spans):
    class Plugin:
        @hookimpl
        async def menu_links(self, datasette, actor, request):
            return []

    name = register(Plugin())
    await ds.client.get("/")
    (span,) = _hook_spans(otel_spans, name)
    (server,) = [
        s for s in otel_spans.get_finished_spans() if s.kind is SpanKind.SERVER
    ]
    assert span.context.trace_id == server.context.trace_id
    # Parented to the request, directly or through other internal spans
    by_id = {s.context.span_id: s for s in otel_spans.get_finished_spans()}
    parent = span
    while parent.parent is not None and parent.parent.span_id in by_id:
        parent = by_id[parent.parent.span_id]
    assert parent is server


@pytest.mark.asyncio
async def test_skipped_hook_emits_nothing(ds, register, otel_spans):
    class Plugin:
        @hookimpl
        def render_cell(self, value):
            return None

        @hookimpl
        def permission_resources_sql(self, datasette, actor, action):
            return None

        @hookimpl
        def register_routes(self, datasette):
            return []

        @hookimpl
        def asgi_wrapper(self, datasette):
            return lambda app: app

    name = register(Plugin())
    await ds.client.get("/_memory")
    await ds.client.get("/_memory/sqlite_master")
    assert _hook_spans(otel_spans, name) == []


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:coroutine .* was never awaited")
async def test_short_circuit_leaves_no_started_spans(
    ds, register, otel_spans, monkeypatch
):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import SpanProcessor

    started = []

    class Recorder(SpanProcessor):
        def on_start(self, span, parent_context=None):
            started.append(span)

    # An unended span never reaches the exporter, so watch span starts instead
    multi = trace.get_tracer_provider()._active_span_processor
    monkeypatch.setattr(
        multi, "_span_processors", multi._span_processors + (Recorder(),)
    )

    class First:
        @hookimpl
        async def menu_links(self, datasette, actor, request):
            return [{"href": "/first", "label": "First"}]

    class Second:
        @hookimpl
        async def menu_links(self, datasette, actor, request):
            raise AssertionError("never awaited")

    # pluggy calls the most recently registered plugin first
    second = register(Second())
    first = register(First())
    # Stop after the first non-None result, as render_cell's callers do
    results = ds.pm.hook.menu_links(datasette=ds, actor=None, request=None)
    for result in results:
        if await await_me_maybe(result) is not None:
            break
    for result in results:
        if asyncio.iscoroutine(result):
            result.close()
    ours = [
        span
        for span in started
        if span.name == "datasette.hook"
        and span.attributes.get("datasette.plugin.name") in (first, second)
    ]
    assert len(ours) == 1
    assert ours[0].end_time is not None
    assert _hook_spans(otel_spans, second) == []
