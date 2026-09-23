"""
`datasette.facet` spans, one per facet type with facets requested, and a single
`datasette.facet.suggest` span covering suggested-facet discovery.
"""

import json

import pytest
import pytest_asyncio

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.trace import SpanKind, StatusCode

from datasette.app import Datasette

FACET = "datasette.facet"
SUGGEST = "datasette.facet.suggest"


@pytest_asyncio.fixture
async def ds():
    instance = Datasette(memory=True)
    db = instance.add_memory_database("facets")
    await instance.invoke_startup()
    await db.execute_write_script("""
        create table if not exists places (
            id integer primary key, state text, city text, tags text, created text
        );
        create view if not exists endless as with recursive c(x) as
            (select 0 union all select x+1 from c) select x from c;
        """)
    await db.execute_write_many(
        "insert or replace into places values (?, ?, ?, ?, ?)",
        [
            [i, ["CA", "MI"][i % 2], f"c{i % 3}", json.dumps(["a", "b"]), "2026-09-15"]
            for i in range(10)
        ],
    )
    try:
        yield instance
    finally:
        instance.close()


def _spans(otel_spans, name):
    return [s for s in otel_spans.get_finished_spans() if s.name == name]


@pytest.mark.asyncio
async def test_column_facet_span(ds, otel_spans):
    response = await ds.client.get("/facets/places?_facet=state&_facet=city")
    assert response.status_code == 200
    finished = otel_spans.get_finished_spans()
    (facet,) = _spans(otel_spans, FACET)
    assert dict(facet.attributes) == {
        "datasette.facet.type": "column",
        "datasette.facet.columns": ("state", "city"),
    }
    assert facet.status.status_code is StatusCode.UNSET
    (server,) = [s for s in finished if s.kind is SpanKind.SERVER]
    assert facet.parent.span_id == server.context.span_id
    facet_queries = [
        s
        for s in finished
        if s.name == "db.query"
        and s.parent
        and s.parent.span_id == facet.context.span_id
    ]
    # One grouping query per column; the rest are facet_results() helpers
    grouping = [
        s
        for s in facet_queries
        if "count(*) as count" in s.attributes.get("db.query.text", "")
    ]
    assert len(grouping) == 2


@pytest.mark.asyncio
async def test_suggest_span_once(ds, otel_spans):
    response = await ds.client.get("/facets/places")
    assert response.status_code == 200
    assert _spans(otel_spans, FACET) == []
    (suggest,) = _spans(otel_spans, SUGGEST)
    count = suggest.attributes["datasette.facet.suggestion_count"]
    assert count > 0
    children = [
        s
        for s in otel_spans.get_finished_spans()
        if s.parent and s.parent.span_id == suggest.context.span_id
    ]
    assert children and all(s.name == "db.query" for s in children)


@pytest.mark.asyncio
async def test_array_and_date_facet_types(ds, otel_spans):
    response = await ds.client.get(
        "/facets/places.json?_facet_array=tags&_facet_date=created"
    )
    assert response.status_code == 200
    types = {
        s.attributes["datasette.facet.type"]: s.attributes["datasette.facet.columns"]
        for s in _spans(otel_spans, FACET)
    }
    assert types == {"array": ("tags",), "date": ("created",)}


@pytest.mark.asyncio
async def test_timed_out_facet_is_not_an_error(ds, otel_spans):
    # Grouping a view that never ends always exceeds facet_time_limit_ms
    response = await ds.client.get(
        "/facets/endless.json?_facet=x&_extra=facets_timed_out"
    )
    assert response.json()["facets_timed_out"] == ["x"]
    (facet,) = _spans(otel_spans, FACET)
    assert facet.attributes["datasette.facet.timed_out_columns"] == ("x",)
    assert facet.status.status_code is StatusCode.UNSET
