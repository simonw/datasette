"""
`datasette.permission.check` and `datasette.permission.resources` spans: one
per public permission API call, never nested, never carrying the actor.
"""

import pytest
import pytest_asyncio

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.trace import SpanKind

from datasette.app import Datasette
from datasette.resources import TableResource
from datasette.telemetry_testing import assert_no_forbidden_values
from datasette.utils.asgi import Forbidden

ACTOR_ID = "secret-actor-id"
PERMISSION_SPANS = {"datasette.permission.check", "datasette.permission.resources"}


@pytest_asyncio.fixture
async def ds():
    instance = Datasette(
        memory=True,
        config={
            "databases": {"perms": {"tables": {"private": {"allow": {"id": ACTOR_ID}}}}}
        },
    )
    db = instance.add_memory_database("perms")
    await db.execute_write("create table if not exists t (id integer primary key)")
    await db.execute_write(
        "create table if not exists private (id integer primary key)"
    )
    await instance.invoke_startup()
    try:
        yield instance
    finally:
        instance.close()


def _permission_spans(otel_spans):
    return [s for s in otel_spans.get_finished_spans() if s.name in PERMISSION_SPANS]


@pytest.mark.asyncio
async def test_allowed_emits_span(ds, otel_spans):
    assert await ds.allowed(
        action="view-table", resource=TableResource("perms", "t"), actor=None
    )
    (span,) = _permission_spans(otel_spans)
    assert span.name == "datasette.permission.check"
    assert dict(span.attributes) == {
        "datasette.permission.action": "view-table",
        "datasette.resource.parent": "perms",
        "datasette.resource.child": "t",
        "datasette.permission.allowed": True,
    }


@pytest.mark.asyncio
async def test_denied_check_records_false(ds, otel_spans):
    with pytest.raises(Forbidden):
        await ds.ensure_permission(
            action="view-table",
            resource=TableResource("perms", "private"),
            actor={"id": "someone-else"},
        )
    (span,) = _permission_spans(otel_spans)
    assert span.attributes["datasette.permission.allowed"] is False


@pytest.mark.asyncio
async def test_delegating_apis_emit_one_span(ds, otel_spans):
    resource = TableResource("perms", "private")
    # check_visibility() calls allowed() twice - once for the actor, once
    # for an anonymous user - but is one check
    assert await ds.check_visibility({"id": ACTOR_ID}, "view-table", resource) == (
        True,
        True,
    )
    (span,) = _permission_spans(otel_spans)
    assert span.name == "datasette.permission.check"
    assert span.attributes["datasette.permission.allowed"] is True

    # allowed_resources() calls allowed_resources_sql()
    otel_spans.clear()
    page = await ds.allowed_resources("view-table", None, parent="perms")
    assert [r.child for r in page.resources] == ["t"]
    (span,) = _permission_spans(otel_spans)
    assert span.name == "datasette.permission.resources"
    assert dict(span.attributes) == {
        "datasette.permission.action": "view-table",
        "datasette.resource.parent": "perms",
    }


@pytest.mark.asyncio
async def test_page_spans_parented_to_request_and_never_carry_actor(ds, otel_spans):
    cookies = {"ds_actor": ds.client.actor_cookie({"id": ACTOR_ID})}
    for path in ("/", "/perms", "/perms/t", "/perms/private"):
        otel_spans.clear()
        response = await ds.client.get(path, cookies=cookies)
        assert response.status_code == 200
        spans = otel_spans.get_finished_spans()
        (server,) = [s for s in spans if s.kind is SpanKind.SERVER]
        permission_spans = _permission_spans(otel_spans)
        assert permission_spans, path
        by_id = {s.context.span_id: s for s in spans}
        for span in permission_spans:
            # Parented to the request, directly or through other spans, and
            # never inside another permission span
            parent = span
            while parent.parent is not None and parent.parent.span_id in by_id:
                parent = by_id[parent.parent.span_id]
                assert parent.name not in PERMISSION_SPANS, path
            assert parent is server, path
        assert_no_forbidden_values({ACTOR_ID}, finished_spans=spans)
