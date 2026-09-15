"""
`datasette.render_template` spans: one per HTML page render, covering context
building and the Jinja render, named after the template actually selected.
"""

import pytest
import pytest_asyncio

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.trace import SpanKind

from datasette import hookimpl
from datasette.app import Datasette

RENDER = "datasette.render_template"


async def _datasette(**kwargs):
    instance = Datasette(memory=True, **kwargs)
    db = instance.add_memory_database("render")
    await db.execute_write("create table if not exists things (id integer primary key)")
    await instance.invoke_startup()
    return instance


@pytest_asyncio.fixture
async def ds():
    instance = await _datasette()
    try:
        yield instance
    finally:
        instance.close()


def _render_spans(otel_spans):
    return [s for s in otel_spans.get_finished_spans() if s.name == RENDER]


@pytest.mark.asyncio
async def test_html_page_emits_one_render_span_under_request(ds, otel_spans):
    response = await ds.client.get("/render/things")
    assert response.status_code == 200
    (span,) = _render_spans(otel_spans)
    assert dict(span.attributes) == {"datasette.template.name": "table.html"}
    (server,) = [
        s for s in otel_spans.get_finished_spans() if s.kind is SpanKind.SERVER
    ]
    assert span.parent.span_id == server.context.span_id


@pytest.mark.asyncio
async def test_json_endpoint_emits_no_render_span(ds, otel_spans):
    response = await ds.client.get("/render/things.json")
    assert response.status_code == 200
    assert _render_spans(otel_spans) == []


@pytest.mark.asyncio
async def test_custom_template_override_reports_selected_name(tmp_path, otel_spans):
    (tmp_path / "table-render-things.html").write_text("custom table page")
    instance = await _datasette(template_dir=str(tmp_path))
    try:
        otel_spans.clear()
        response = await instance.client.get("/render/things")
        assert response.text == "custom table page"
        (span,) = _render_spans(otel_spans)
        assert span.attributes["datasette.template.name"] == "table-render-things.html"
    finally:
        instance.close()


@pytest.mark.asyncio
async def test_error_page_emits_render_span(ds, otel_spans):
    response = await ds.client.get("/render/does-not-exist")
    assert response.status_code == 404
    (span,) = _render_spans(otel_spans)
    assert span.attributes["datasette.template.name"] == "error.html"


@pytest.mark.asyncio
async def test_template_context_hook_spans_nest_under_render_span(ds, otel_spans):
    class Plugin:
        __name__ = "RenderSpanTestPlugin"

        @hookimpl
        def extra_template_vars(self):
            async def inner():
                return {"from_plugin": 1}

            return inner

        @hookimpl
        def extra_css_urls(self):
            return ["/static/render-span-test.css"]

    ds.pm.register(Plugin(), name="render-span-test")
    try:
        otel_spans.clear()
        assert (await ds.client.get("/render/things")).status_code == 200
    finally:
        ds.pm.unregister(name="render-span-test")
    (render,) = _render_spans(otel_spans)
    hooks = [
        s
        for s in otel_spans.get_finished_spans()
        if s.name == "datasette.hook"
        and s.attributes["datasette.plugin.name"] == "render-span-test"
    ]
    assert {s.attributes["datasette.hook.name"] for s in hooks} == {
        "extra_template_vars",
        "extra_css_urls",
    }
    for span in hooks:
        assert span.parent.span_id == render.context.span_id
