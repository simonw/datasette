"""
The `datasette.csv` span: one per CSV response body, covering every page of a
`?_stream=on` export.
"""

import pytest
import pytest_asyncio

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.trace import SpanKind, StatusCode

from datasette.app import Datasette

CSV = "datasette.csv"


def _span_tree(otel_spans):
    finished = otel_spans.get_finished_spans()
    (csv_span,) = [s for s in finished if s.name == CSV]
    (server,) = [s for s in finished if s.kind is SpanKind.SERVER]
    children = [
        s for s in finished if s.parent and s.parent.span_id == csv_span.context.span_id
    ]
    return csv_span, server, children


@pytest.mark.asyncio
async def test_csv_span(ds_client, otel_spans):
    response = await ds_client.get("/fixtures/facetable.csv")
    assert response.status_code == 200
    csv_span, server, children = _span_tree(otel_spans)
    assert csv_span.parent.span_id == server.context.span_id
    rows = len(response.text.strip().split("\r\n")) - 1
    assert rows == 15
    assert dict(csv_span.attributes) == {
        "datasette.csv.stream": False,
        "datasette.csv.rows_written": rows,
        "datasette.csv.truncated": False,
    }
    assert csv_span.status.status_code is StatusCode.UNSET
    # The first page is fetched by the view, before the body is written
    assert children == []


@pytest.mark.asyncio
async def test_csv_stream_span_on_sql_view(ds_client, otel_spans):
    # paginated_view has no primary keys; streaming it must still terminate
    response = await ds_client.get("/fixtures/paginated_view.csv?_stream=on")
    assert response.status_code == 200
    lines = response.text.strip().split("\r\n")
    assert len(lines) == 203
    csv_span, _, children = _span_tree(otel_spans)
    assert csv_span.attributes["datasette.csv.stream"] is True
    assert csv_span.attributes["datasette.csv.rows_written"] == 202
    assert csv_span.attributes["datasette.csv.truncated"] is False
    # Later pages' queries nest under the CSV span
    page_queries = [
        s
        for s in children
        if s.name == "db.query"
        and "paginated_view" in s.attributes.get("db.query.text", "")
    ]
    assert len(page_queries) >= 2


@pytest_asyncio.fixture
async def ds_small_csv_limit():
    instance = Datasette(memory=True, settings={"max_csv_mb": 1})
    await instance.invoke_startup()
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.asyncio
async def test_csv_truncated_by_max_csv_mb(ds_small_csv_limit, otel_spans):
    # Three rows of ~600KB each: the second one pushes past the 1MB limit
    sql = (
        "select replace(hex(zeroblob(300000)), '0', 'x') as big "
        "from (select 1 union all select 2 union all select 3)"
    )
    response = await ds_small_csv_limit.client.get(
        "/_memory/-/query.csv", params={"sql": sql}
    )
    assert response.status_code == 200
    assert response.text.endswith("CSV contains more than 1048576 bytes")
    (csv_span,) = [s for s in otel_spans.get_finished_spans() if s.name == CSV]
    assert csv_span.attributes["datasette.csv.truncated"] is True
    assert csv_span.attributes["datasette.csv.rows_written"] == 1
    assert csv_span.status.status_code is StatusCode.UNSET
