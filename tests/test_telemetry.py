import json
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
import sqlite_utils
from opentelemetry import context as otel_context_api
from opentelemetry import trace as otel_trace
from opentelemetry.trace import SpanKind, StatusCode

from datasette.app import Datasette
from datasette.database import Database, QueryInterrupted
from datasette.telemetry import (
    MAX_SQL_LENGTH,
    SCHEMA_URL,
    sql_attribute,
    sql_operation_name,
    tracer,
)
from datasette.version import __version__

SECRET_PARAM_VALUE = "SUPER_SECRET_PARAM_VALUE_XYZ_123"

INVALID_SQL = "select this_is_not_valid_sql from nowhere"

# Bounded so a broken time limit fails rather than hangs, but too slow to
# finish within the millisecond time limits used below.
SLOW_SQL = """
with recursive counter(x) as (
    select 1 union all select x + 1 from counter where x < 50000000
)
select max(x) from counter
"""


def _db_query_spans(otel_spans):
    return [span for span in otel_spans.get_finished_spans() if span.name == "db.query"]


def _spans_for_namespace(otel_spans, namespace):
    "db.query spans for one database, excluding queries against the internal database."
    return [
        span
        for span in _db_query_spans(otel_spans)
        if span.attributes["db.namespace"] == namespace
    ]


def _children_named(otel_spans, name, parent_span_context):
    "Finished spans called `name` that are direct children of `parent_span_context`."
    return [
        span
        for span in otel_spans.get_finished_spans()
        if span.name == name
        and span.parent is not None
        and span.parent.span_id == parent_span_context.span_id
        and span.parent.trace_id == parent_span_context.trace_id
        and span.context.trace_id == parent_span_context.trace_id
    ]


def _descends_from(span, ancestor_span_context, by_span_id):
    "True if `span` reaches `ancestor_span_context` by walking parent links."
    seen = set()
    current = span
    while current.parent is not None:
        if current.parent.span_id == ancestor_span_context.span_id:
            return current.parent.trace_id == ancestor_span_context.trace_id
        if current.parent.span_id in seen:
            return False
        seen.add(current.parent.span_id)
        current = by_span_id.get(current.parent.span_id)
        if current is None:
            return False
    return False


def _all_attribute_values(otel_spans):
    "Every attribute value on every finished span and span event."
    values = []
    for span in otel_spans.get_finished_spans():
        values.extend((span.attributes or {}).values())
        for event in span.events:
            values.extend((event.attributes or {}).values())
    return values


def test_datasette_package_never_imports_the_sdk():
    """
    Importing datasette does not load the OpenTelemetry SDK.

    conftest.py moves this test to the front of the run by name.
    """
    code = (
        "import datasette.app, datasette.database, datasette.telemetry, sys; "
        "print([m for m in sys.modules if m.startswith('opentelemetry.sdk')])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert (
        result.stdout.strip() == "[]"
    ), f"datasette imported the OpenTelemetry SDK: {result.stdout.strip()}"


@pytest.mark.asyncio
async def test_db_query_span_basic_attributes(ds_client, otel_spans):
    response = await ds_client.get("/fixtures/-/query.json?sql=select+1")
    assert response.status_code == 200

    spans = _db_query_spans(otel_spans)
    assert spans, "expected at least one db.query span"
    span = spans[-1]

    assert span.attributes["db.system"] == "sqlite"
    assert span.attributes["db.namespace"] == "fixtures"
    assert span.attributes["db.query.text"] == "select 1"
    assert span.attributes["datasette.rows_returned"] == 1
    assert span.attributes["datasette.truncated"] is False
    assert isinstance(span.attributes["datasette.time_limit_ms"], int)
    assert span.status.status_code == StatusCode.UNSET


@pytest.mark.asyncio
async def test_truncated_result_sets_truncated_attribute(otel_spans):
    "A result cut short by max_returned_rows records truncated=True."
    ds = Datasette(memory=True, settings={"max_returned_rows": 5})
    db = ds.add_memory_database("t04_truncated")
    results = await db.execute(
        "select value from json_each('[1,2,3,4,5,6,7,8,9,10]')", truncate=True
    )
    assert results.truncated

    spans = _spans_for_namespace(otel_spans, "t04_truncated")
    assert spans
    span = spans[-1]
    assert span.attributes["datasette.truncated"] is True
    assert span.attributes["datasette.rows_returned"] == 5


@pytest.mark.asyncio
async def test_facetable_request_produces_db_query_spans(ds_client, otel_spans):
    response = await ds_client.get("/fixtures/facetable.json")
    assert response.status_code == 200

    spans = _db_query_spans(otel_spans)
    assert spans, "expected at least one db.query span"
    assert all(span.attributes["db.system"] == "sqlite" for span in spans)
    # Each span records the SQL or, for callback methods, the callback name:
    assert all(
        span.attributes.get("db.query.text")
        or span.attributes.get("datasette.callback")
        for span in spans
    )
    assert any(span.attributes.get("db.query.text") for span in spans)
    # Rendering the page also queries the internal database, so only some of
    # these spans belong to "fixtures".
    assert any(span.attributes["db.namespace"] == "fixtures" for span in spans)


def test_sql_attribute_truncates_at_2048():
    short_sql = "select 1"
    assert sql_attribute(short_sql) == "select 1"
    # Surrounding whitespace is stripped:
    assert sql_attribute("  select 1\n") == "select 1"

    long_sql = "select 1 -- " + ("x" * 3000)
    truncated = sql_attribute(long_sql)
    assert len(truncated) == MAX_SQL_LENGTH + len("…[truncated]")
    assert truncated.startswith("select 1 -- ")
    assert truncated.endswith("…[truncated]")


@pytest.mark.asyncio
async def test_db_query_text_is_truncated_in_real_span(ds_client, otel_spans):
    # A long trailing comment keeps the SQL valid but over the 2048 character limit
    long_sql = "select 1 -- " + ("x" * 3000)
    response = await ds_client.get("/fixtures/-/query.json", params={"sql": long_sql})
    assert response.status_code == 200

    spans = _db_query_spans(otel_spans)
    assert spans
    assert any(len(span.attributes.get("db.query.text", "")) > 100 for span in spans), (
        "expected the long query to reach a span - otherwise this test would "
        "pass even if truncation were never applied"
    )
    for span in spans:
        recorded = span.attributes.get("db.query.text", "")
        assert len(recorded) <= MAX_SQL_LENGTH + len("…[truncated]")


@pytest.mark.asyncio
async def test_no_span_attribute_ever_contains_a_parameter_value(ds_client, otel_spans):
    response = await ds_client.get(
        "/fixtures/-/query.json",
        params={"sql": "select :secret", "secret": SECRET_PARAM_VALUE},
    )
    assert response.status_code == 200
    # Confirm the bound parameter value was used by the query:
    assert SECRET_PARAM_VALUE in json.dumps(response.json())

    for value in _all_attribute_values(otel_spans):
        if isinstance(value, str):
            assert SECRET_PARAM_VALUE not in value
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    assert SECRET_PARAM_VALUE not in item

    spans = _db_query_spans(otel_spans)
    assert spans
    span = spans[-1]
    assert "select :secret" in span.attributes["db.query.text"]
    assert span.attributes.get("datasette.param_count") == 1


@pytest.mark.asyncio
async def test_query_interrupted_sets_error_status(otel_spans):
    """
    A query that exceeds the sql_time_limit_ms setting is a span error.

    The limit comes from the setting because a shorter custom_time_limit
    marks the timeout as expected.
    """
    ds = Datasette(memory=True, settings={"sql_time_limit_ms": 20})
    db = ds.add_memory_database("t09_instance_limit_timeout")
    with pytest.raises(QueryInterrupted):
        await db.execute(SLOW_SQL)

    spans = _spans_for_namespace(otel_spans, "t09_instance_limit_timeout")
    assert spans
    span = spans[-1]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["datasette.interrupted"] is True
    assert span.events
    assert all(event.name == "exception" for event in span.events)


async def _expected_timeout_count_span(otel_spans, database_name):
    "Make table_counts() time out and return its db.query span."
    db = Datasette(memory=True).add_memory_database(database_name)
    await db.execute_write("create table big (id integer primary key, t text)")
    await db.execute_write_many(
        "insert into big (t) values (?)", [["x" * 50] for _ in range(11000)]
    )
    # count_limit caps the scan at 10001 rows. Below 20ms sqlite_timelimit()
    # checks the limit on every VM instruction, so this reliably exceeds 1ms.
    counts = await db.table_counts(1)
    assert counts == {
        "big": None
    }, "the count did not actually time out, so the rest of this test is vacuous"

    spans = [
        span
        for span in _spans_for_namespace(otel_spans, database_name)
        if "count(*)" in span.attributes.get("db.query.text", "")
    ]
    assert len(spans) == 1
    return spans[0]


@pytest.mark.asyncio
async def test_expected_timeout_is_not_a_span_error(otel_spans):
    span = await _expected_timeout_count_span(otel_spans, "t09_expected_timeout")
    # Recorded as interrupted, but not as an error:
    assert span.attributes["datasette.interrupted"] is True
    assert span.status.status_code != StatusCode.ERROR
    assert not [event for event in span.events if event.name == "exception"]


@pytest.mark.asyncio
async def test_expected_timeout_does_not_error_the_inner_execute_span(otel_spans):
    "The db.query.execute child span is not marked as an error either."
    span = await _expected_timeout_count_span(otel_spans, "t09_expected_timeout_inner")
    children = _children_named(otel_spans, "db.query.execute", span.context)
    assert len(children) == 1
    child = children[0]
    assert child.status.status_code != StatusCode.ERROR
    assert not [event for event in child.events if event.name == "exception"]


@pytest.mark.asyncio
async def test_unexpected_timeout_is_still_a_span_error(otel_spans):
    "A timeout is an error if custom_time_limit is above sql_time_limit_ms."
    ds = Datasette(memory=True, settings={"sql_time_limit_ms": 20})
    db = ds.add_memory_database("t09_custom_limit_ignored")
    with pytest.raises(QueryInterrupted):
        await db.execute(SLOW_SQL, custom_time_limit=5000)

    spans = _spans_for_namespace(otel_spans, "t09_custom_limit_ignored")
    assert spans
    span = spans[-1]
    # The setting overrides the larger custom_time_limit:
    assert span.attributes["datasette.time_limit_ms"] == 20
    assert span.attributes["datasette.interrupted"] is True
    assert span.status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in span.events)

    children = _children_named(otel_spans, "db.query.execute", span.context)
    assert len(children) == 1
    assert children[0].status.status_code == StatusCode.ERROR


@pytest.mark.asyncio
async def test_unsuppressed_sql_error_is_a_span_error(ds_client, otel_spans):
    db = ds_client.ds.get_database("fixtures")
    with pytest.raises(sqlite3.OperationalError):
        await db.execute(INVALID_SQL)

    spans = _db_query_spans(otel_spans)
    assert spans
    span = spans[-1]
    assert span.status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in span.events)
    assert "datasette.sql_error_suppressed" not in span.attributes


@pytest.mark.asyncio
async def test_suppressed_sql_error_is_not_a_span_error(ds_client, otel_spans):
    "With log_sql_errors=False the error is recorded as suppressed, not a span error."
    db = ds_client.ds.get_database("fixtures")
    with pytest.raises(sqlite3.OperationalError):
        await db.execute(INVALID_SQL, log_sql_errors=False)

    spans = _db_query_spans(otel_spans)
    assert spans
    span = spans[-1]
    assert span.status.status_code == StatusCode.UNSET
    assert span.attributes["datasette.sql_error_suppressed"] is True
    assert not [event for event in span.events if event.name == "exception"]


@pytest.mark.asyncio
async def test_execute_write_produces_db_query_span(otel_spans):
    # Named in-memory databases are shared, so each test uses a unique name.
    db = Datasette(memory=True).add_memory_database("t03_write_span")
    await db.execute_write("create table docs (id integer primary key, name text)")
    await db.execute_write("insert into docs (id, name) values (?, ?)", [1, "one"])

    spans = _spans_for_namespace(otel_spans, "t03_write_span")
    assert spans, "expected db.query spans from execute_write()"
    span = spans[-1]

    assert span.attributes["db.system"] == "sqlite"
    assert span.attributes["db.namespace"] == "t03_write_span"
    assert span.attributes["db.query.text"] == (
        "insert into docs (id, name) values (?, ?)"
    )
    assert span.attributes["datasette.param_count"] == 2


@pytest.mark.asyncio
async def test_execute_write_script_sets_executescript_attribute(otel_spans):
    db = Datasette(memory=True).add_memory_database("t03_write_script_span")
    await db.execute_write_script(
        "create table docs (id integer primary key);\n"
        "insert into docs (id) values (1);"
    )

    spans = _spans_for_namespace(otel_spans, "t03_write_script_span")
    assert spans, "expected a db.query span from execute_write_script()"
    span = spans[-1]

    assert span.attributes["db.system"] == "sqlite"
    assert span.attributes["datasette.executescript"] is True
    assert "insert into docs" in span.attributes["db.query.text"]


@pytest.mark.asyncio
async def test_execute_write_many_records_param_sets_not_rows_returned(otel_spans):
    db = Datasette(memory=True).add_memory_database("t03_write_many_span")
    await db.execute_write("create table docs (id integer primary key)")
    await db.execute_write_many(
        "insert into docs (id) values (?)", [[i] for i in range(1, 6)]
    )

    spans = _spans_for_namespace(otel_spans, "t03_write_many_span")
    many_spans = [
        span for span in spans if span.attributes.get("datasette.executemany") is True
    ]
    assert len(many_spans) == 1
    span = many_spans[0]

    assert span.attributes["datasette.param_sets"] == 5
    assert "datasette.rows_returned" not in span.attributes


# --- Context propagation across thread boundaries --------------------------
#
# These tests check span parentage, not just that the spans exist.


@pytest.mark.asyncio
async def test_db_query_execute_parents_to_db_query(ds_client, otel_spans):
    # execute_fn() submits to the executor, so db.query.execute is created on
    # another thread.
    response = await ds_client.get("/fixtures/-/query.json?sql=select+1")
    assert response.status_code == 200

    query_spans = [
        span
        for span in _spans_for_namespace(otel_spans, "fixtures")
        if span.attributes.get("db.query.text") == "select 1"
    ]
    assert query_spans, "expected a db.query span for 'select 1'"
    query_span = query_spans[-1]

    assert [
        span
        for span in otel_spans.get_finished_spans()
        if span.name == "db.query.execute"
    ], "expected at least one db.query.execute span"
    children = _children_named(otel_spans, "db.query.execute", query_span.context)
    assert len(children) == 1, "expected exactly one db.query.execute child of db.query"
    # db.query.execute runs within db.query; the gap is the thread pool wait.
    assert query_span.start_time <= children[0].start_time
    assert children[0].end_time <= query_span.end_time


@pytest.mark.asyncio
async def test_immutable_database_propagates_context(tmp_path, otel_spans):
    # Immutable databases run execute_isolated_fn() on another thread using
    # loop.run_in_executor(), not the write thread.
    db_path = tmp_path / "t04_immutable.db"
    sqlite_utils.Database(str(db_path))["t"].insert({"id": 1}, pk="id")

    ds = Datasette()
    db = Database(ds, path=str(db_path), is_mutable=False)
    ds.add_database(db, name="t04_immutable")

    def fn(conn):
        with tracer.start_as_current_span("t04-child-in-isolated-worker"):
            pass

    try:
        with tracer.start_as_current_span("t04-parent-on-event-loop") as parent:
            parent_context = parent.get_span_context()
            await db.execute_isolated_fn(fn)
    finally:
        ds.remove_database("t04_immutable")

    assert [
        span
        for span in otel_spans.get_finished_spans()
        if span.name == "t04-child-in-isolated-worker"
    ], "expected a span created inside execute_isolated_fn's worker thread"
    # Expected chain: event loop parent -> db.query -> worker thread child
    query_spans = _children_named(otel_spans, "db.query", parent_context)
    assert len(query_spans) == 1
    children = _children_named(
        otel_spans, "t04-child-in-isolated-worker", query_spans[0].context
    )
    assert len(children) == 1


@pytest.mark.asyncio
async def test_write_spans_parent_to_db_query(otel_spans):
    # execute_write() queues a WriteTask for the write thread.
    # db.write.queue_wait and db.write.execute are both children of db.query.
    db = Datasette(memory=True).add_memory_database("t04_write_spans")
    await db.execute_write("create table docs (id integer primary key)")

    query_spans = _spans_for_namespace(otel_spans, "t04_write_spans")
    assert query_spans, "expected a db.query span from execute_write()"
    query_span = query_spans[-1]

    queue_wait_children = _children_named(
        otel_spans, "db.write.queue_wait", query_span.context
    )
    execute_children = _children_named(
        otel_spans, "db.write.execute", query_span.context
    )
    assert len(queue_wait_children) == 1
    assert len(execute_children) == 1

    execute_span = execute_children[0]
    assert execute_span.attributes["datasette.isolated_connection"] is False
    assert execute_span.attributes["datasette.transaction"] is True
    # The queue wait ends before the write begins.
    assert queue_wait_children[0].end_time <= execute_span.start_time


@pytest.mark.asyncio
async def test_write_queue_wait_duration_reflects_real_wait(otel_spans):
    # db.write.queue_wait runs from task.enqueued_at_ns, captured on the event
    # loop, to when the write thread dequeues the task.
    ds = Datasette(memory=True)
    db = ds.add_memory_database("t04_queue_wait")
    await db.execute_write("create table docs (id integer primary key)")

    def slow_write(conn):
        time.sleep(0.1)

    # Queue a slow write without waiting for it, then a second write behind it:
    _, slow_future = await db._send_to_write_thread(slow_write, block=False)
    await db.execute_write("insert into docs (id) values (1)")
    await slow_future

    query_spans = [
        span
        for span in _spans_for_namespace(otel_spans, "t04_queue_wait")
        if span.attributes.get("db.query.text") == "insert into docs (id) values (1)"
    ]
    assert query_spans, "expected a db.query span for the queued-behind insert"
    queue_wait_children = _children_named(
        otel_spans, "db.write.queue_wait", query_spans[-1].context
    )
    assert len(queue_wait_children) == 1
    duration_ns = queue_wait_children[0].end_time - queue_wait_children[0].start_time
    # The slow write sleeps for 100ms
    assert duration_ns > 10_000_000, f"queue wait was only {duration_ns}ns"


async def _write_spans_from_one_enqueue(otel_spans, name, block):
    """
    Run one write through the write thread inside a span, returning
    (enqueueing span context, {span name: span}).

    Uses _send_to_write_thread() because execute_write() would add its own
    db.query span between the enqueueing span and the write spans.
    """
    db = Datasette(memory=True).add_memory_database(name)
    await db.execute_write("create table docs (id integer primary key)")

    def insert(conn):
        conn.execute("insert into docs (id) values (1)")

    otel_spans.clear()
    with tracer.start_as_current_span("enqueueing-span") as enqueuer:
        enqueuer_context = enqueuer.get_span_context()
        queued = await db._send_to_write_thread(insert, block=block)
    if not block:
        # Wait for the write after the enqueueing span has ended. The reply
        # future resolves once both write spans have been exported.
        _, reply_future = queued
        await reply_future

    spans = {}
    for span in otel_spans.get_finished_spans():
        if span.name in ("db.write.queue_wait", "db.write.execute"):
            assert span.name not in spans, f"more than one {span.name} span"
            spans[span.name] = span
    assert set(spans) == {"db.write.queue_wait", "db.write.execute"}
    return enqueuer_context, spans


@pytest.mark.asyncio
async def test_blocking_write_spans_still_parent_normally(otel_spans):
    # block=True waits for the write, so its spans are children of the
    # enqueueing span, with no links.
    enqueuer_context, spans = await _write_spans_from_one_enqueue(
        otel_spans, "t07_blocking_write", block=True
    )
    for name, span in spans.items():
        assert span.parent is not None, f"{name} lost its parent"
        assert span.parent.span_id == enqueuer_context.span_id, name
        assert span.parent.trace_id == enqueuer_context.trace_id, name
        assert span.context.trace_id == enqueuer_context.trace_id, name
        assert span.links == (), f"{name} should be parented, not linked"


@pytest.mark.asyncio
async def test_nonblocking_write_spans_are_roots_with_a_link(otel_spans):
    # block=False returns before the write runs, so the write spans are roots
    # linked to the enqueueing span.
    enqueuer_context, spans = await _write_spans_from_one_enqueue(
        otel_spans, "t07_nonblocking_write", block=False
    )
    assert enqueuer_context.is_valid, "test's own enqueueing span was not recorded"
    for name, span in spans.items():
        assert span.parent is None, f"{name} is still parented"
        # Each write span starts its own trace
        assert span.context.trace_id != enqueuer_context.trace_id, name
        assert len(span.links) == 1, f"{name} has links {span.links}"
        link_context = span.links[0].context
        assert link_context.trace_id == enqueuer_context.trace_id, name
        assert link_context.span_id == enqueuer_context.span_id, name
    # The two write spans are separate roots
    assert (
        spans["db.write.queue_wait"].context.trace_id
        != spans["db.write.execute"].context.trace_id
    )


@pytest.mark.asyncio
async def test_nonblocking_write_link_has_no_attributes(otel_spans):
    _, spans = await _write_spans_from_one_enqueue(
        otel_spans, "t07_nonblocking_link_attrs", block=False
    )
    for name, span in spans.items():
        assert len(span.links) == 1, name
        assert dict(span.links[0].attributes or {}) == {}, name


@pytest.mark.asyncio
async def test_nonblocking_write_spans_ignore_the_write_threads_ambient_context(
    otel_spans,
):
    """
    block=False spans ignore any context left attached on the write thread.

    A prepare_connection hook could attach a context and never detach it.
    This test does that, then checks the write spans are still roots.
    """
    ds = Datasette(memory=True)
    db = ds.add_memory_database("t07_ambient_write_thread")
    write_thread_name = "_execute_writes for database t07_ambient_write_thread"
    real_prepare_connection = ds._prepare_connection
    leaked = {}

    def prepare_connection(conn, database):
        if threading.current_thread().name == write_thread_name:
            # Runs on the write thread before any task is dequeued, and never
            # detaches.
            span = tracer.start_span("leaked-write-thread-ambient-span")
            leaked["span_id"] = span.get_span_context().span_id
            otel_context_api.attach(otel_trace.set_span_in_context(span))
        return real_prepare_connection(conn, database)

    ds._prepare_connection = prepare_connection
    try:
        await db.execute_write("create table docs (id integer primary key)")

        def insert(conn):
            conn.execute("insert into docs (id) values (1)")

        otel_spans.clear()
        with tracer.start_as_current_span("enqueueing-span") as enqueuer:
            enqueuer_context = enqueuer.get_span_context()
            _, reply_future = await db._send_to_write_thread(insert, block=False)
        await reply_future
    finally:
        ds._prepare_connection = real_prepare_connection
        db.close()

    assert "span_id" in leaked, "the ambient context was never leaked - test is vacuous"
    write_spans = [
        span
        for span in otel_spans.get_finished_spans()
        if span.name in ("db.write.queue_wait", "db.write.execute")
    ]
    assert len(write_spans) == 2
    for span in write_spans:
        assert span.parent is None, (
            f"{span.name} parented to the write thread's leftover ambient "
            "context instead of being a root"
        )
        assert span.links[0].context.span_id == enqueuer_context.span_id


@pytest.mark.asyncio
async def test_suppressed_error_does_not_mark_execute_span(ds_client, otel_spans):
    "The inner db.query.execute span also respects log_sql_errors=False."
    db = ds_client.ds.get_database("fixtures")
    with pytest.raises(sqlite3.OperationalError):
        await db.execute(INVALID_SQL, log_sql_errors=False)

    execute_spans = [
        span
        for span in otel_spans.get_finished_spans()
        if span.name == "db.query.execute"
    ]
    assert execute_spans
    span = execute_spans[-1]
    assert span.status.status_code == StatusCode.UNSET
    assert not [event for event in span.events if event.name == "exception"]


@pytest.mark.asyncio
async def test_invoke_startup_produces_one_trace_not_dozens_of_orphans(otel_spans):
    "Spans emitted by invoke_startup() share a single datasette.startup root span."
    ds = Datasette(memory=True)
    ds.add_memory_database("t05_startup_db")
    # Ignore spans from constructing Datasette, which happens before startup
    otel_spans.clear()

    # No ambient span, as in the ASGI lifespan path where startup runs before
    # any request.
    assert (
        not otel_trace.get_current_span().get_span_context().is_valid
    ), "this test must run with no ambient span"

    await ds.invoke_startup()

    spans = otel_spans.get_finished_spans()
    assert len(spans) > 10, f"expected startup to emit many spans, got {len(spans)}"

    startup_spans = [span for span in spans if span.name == "datasette.startup"]
    assert len(startup_spans) == 1
    startup = startup_spans[0]
    assert startup.parent is None, "datasette.startup should be a root span"

    trace_ids = {span.context.trace_id for span in spans}
    assert trace_ids == {startup.context.trace_id}, (
        f"startup produced {len(trace_ids)} distinct traces; every span it "
        "causes should share the datasette.startup trace"
    )

    roots = [span for span in spans if span.parent is None]
    assert [span.name for span in roots] == ["datasette.startup"]

    by_span_id = {span.context.span_id: span for span in spans}

    # Internal database reads:
    internal_queries = [
        span
        for span in spans
        if span.name == "db.query" and span.attributes["db.namespace"] == "__INTERNAL__"
    ]
    assert internal_queries, "expected internal-catalog db.query spans during startup"
    assert all(
        _descends_from(span, startup.context, by_span_id) for span in internal_queries
    )

    # Internal database writes, which run on the write thread:
    write_spans = [span for span in spans if span.name.startswith("db.write.")]
    assert write_spans, "expected db.write.* spans during startup"
    assert all(
        _descends_from(span, startup.context, by_span_id) for span in write_spans
    )


# --- Semantic conventions: span kind, scope, db.operation.name -------------


@pytest.mark.asyncio
async def test_db_query_is_client_kind_and_children_are_internal(otel_spans):
    """
    db.query spans are CLIENT. Their child spans are INTERNAL because they are
    parts of one query rather than separate database calls.
    """
    db = Datasette(memory=True).add_memory_database("t06_span_kind")
    # Call each of the four SQL string methods:
    await db.execute_write("create table docs (id integer primary key)")
    await db.execute_write_many(
        "insert into docs (id) values (?)", [[i] for i in range(1, 4)]
    )
    await db.execute_write_script("insert into docs (id) values (99);")
    await db.execute("select id from docs")

    query_spans = _spans_for_namespace(otel_spans, "t06_span_kind")
    assert len(query_spans) == 4, "expected a db.query span per entry point"
    for span in query_spans:
        text = span.attributes["db.query.text"]
        assert span.kind == SpanKind.CLIENT, f"db.query for {text!r} should be CLIENT"

    for name in ("db.query.execute", "db.write.execute", "db.write.queue_wait"):
        children = [
            span for span in otel_spans.get_finished_spans() if span.name == name
        ]
        assert children, f"expected at least one {name} span"
        for span in children:
            assert span.kind == SpanKind.INTERNAL, f"{name} should be INTERNAL"


@pytest.mark.asyncio
async def test_instrumentation_scope_declares_version_and_schema_url(
    ds_client, otel_spans
):
    "The instrumentation scope includes the Datasette version and schema URL."
    response = await ds_client.get("/fixtures/-/query.json?sql=select+1")
    assert response.status_code == 200

    spans = _db_query_spans(otel_spans)
    assert spans, "expected at least one db.query span"
    scope = spans[-1].instrumentation_scope

    assert scope.name == "datasette"
    assert scope.version == __version__
    # Uses the literal URL so changing SCHEMA_URL requires updating this test
    assert scope.schema_url == "https://opentelemetry.io/schemas/1.29.0"
    assert SCHEMA_URL == "https://opentelemetry.io/schemas/1.29.0"
    assert __version__, "the scope version must not be empty"


def test_db_operation_name_from_leading_keyword():
    assert sql_operation_name("select 1") == "SELECT"
    assert sql_operation_name("  insert into x (a) values (1)") == "INSERT"
    # A leading CTE reports WITH, not the operation inside it
    assert sql_operation_name("with foo as (select 1) select * from foo") == "WITH"
    # Unrecognized leading keyword
    assert sql_operation_name("gibberish 1") is None
    # A parenthesized SELECT or a leading comment also returns None
    assert sql_operation_name("(select 1) union select 2") is None
    assert sql_operation_name("-- a comment\nselect 1") is None
    assert sql_operation_name("") is None


@pytest.mark.asyncio
async def test_db_operation_name_on_real_span(ds_client, otel_spans):
    response = await ds_client.get("/fixtures/-/query.json?sql=select+1")
    assert response.status_code == 200

    spans = [
        span
        for span in _spans_for_namespace(otel_spans, "fixtures")
        if span.attributes.get("db.query.text") == "select 1"
    ]
    assert spans, "expected a db.query span for 'select 1'"
    assert spans[-1].attributes["db.operation.name"] == "SELECT"


@pytest.mark.asyncio
async def test_execute_write_sets_db_operation_name(otel_spans):
    db = Datasette(memory=True).add_memory_database("t06_write_operation")
    await db.execute_write("create table docs (id integer primary key)")
    await db.execute_write_many(
        "insert into docs (id) values (?)", [[i] for i in range(1, 4)]
    )

    spans = _spans_for_namespace(otel_spans, "t06_write_operation")
    by_operation = {
        span.attributes["db.query.text"]: span.attributes.get("db.operation.name")
        for span in spans
    }
    assert by_operation["create table docs (id integer primary key)"] == "CREATE"
    assert by_operation["insert into docs (id) values (?)"] == "INSERT"


@pytest.mark.asyncio
async def test_execute_write_script_has_no_operation_name(otel_spans):
    """
    Scripts can contain several statements, so db.operation.name is omitted.

    The script starts with `create`, which is on the allowlist, so this fails
    if the operation name is extracted anyway.
    """
    db = Datasette(memory=True).add_memory_database("t06_script_operation")
    await db.execute_write_script(
        "create table docs (id integer primary key);\n"
        "insert into docs (id) values (1);"
    )

    spans = _spans_for_namespace(otel_spans, "t06_script_operation")
    script_spans = [
        span for span in spans if span.attributes.get("datasette.executescript") is True
    ]
    assert len(script_spans) == 1
    assert "db.operation.name" not in script_spans[0].attributes


# --- Callback-style calls: execute_fn / execute_write_fn / execute_isolated_fn


@pytest.mark.asyncio
async def test_execute_fn_produces_db_query_span(otel_spans):
    db = Datasette(memory=True).add_memory_database("t16_execute_fn")
    await db.execute_write("create table t (id integer primary key)")

    def count_rows(conn):
        return conn.execute("select count(*) from t").fetchone()[0]

    otel_spans.clear()
    assert await db.execute_fn(count_rows) == 0

    spans = _spans_for_namespace(otel_spans, "t16_execute_fn")
    assert len(spans) == 1
    span = spans[0]
    assert span.kind == SpanKind.CLIENT
    assert span.attributes["db.system"] == "sqlite"
    assert (
        span.attributes["datasette.callback"]
        == "test_execute_fn_produces_db_query_span.<locals>.count_rows"
    )
    # Callbacks have no SQL text to record or take an operation name from
    assert "db.query.text" not in span.attributes
    assert "db.operation.name" not in span.attributes
    children = _children_named(otel_spans, "db.query.execute", span.context)
    assert len(children) == 1


@pytest.mark.asyncio
async def test_execute_fn_lambda_reports_lambda(otel_spans):
    db = Datasette(memory=True).add_memory_database("t16_lambda")
    otel_spans.clear()
    await db.execute_fn(lambda conn: conn.execute("select 1").fetchone())
    spans = _spans_for_namespace(otel_spans, "t16_lambda")
    assert len(spans) == 1
    assert spans[0].attributes["datasette.callback"].endswith("<lambda>")


@pytest.mark.asyncio
async def test_execute_write_fn_produces_db_query_span(otel_spans):
    db = Datasette(memory=True).add_memory_database("t16_write_fn")

    def create_table(conn):
        conn.execute("create table t (id integer primary key)")

    otel_spans.clear()
    await db.execute_write_fn(create_table)

    spans = _spans_for_namespace(otel_spans, "t16_write_fn")
    assert len(spans) == 1
    span = spans[0]
    assert span.kind == SpanKind.CLIENT
    assert (
        span.attributes["datasette.callback"]
        == "test_execute_write_fn_produces_db_query_span.<locals>.create_table"
    )
    assert "db.query.text" not in span.attributes
    # The write-thread spans are this span's children, same as execute_write()
    for name in ("db.write.queue_wait", "db.write.execute"):
        assert len(_children_named(otel_spans, name, span.context)) == 1, name


@pytest.mark.asyncio
async def test_execute_write_fn_callback_name_is_not_the_hook_wrapper(otel_spans):
    # _wrap_fn_with_hooks() wraps callbacks that accept track_event
    db = Datasette(memory=True).add_memory_database("t16_wrapper_name")

    def create_with_events(conn, track_event):
        conn.execute("create table t (id integer primary key)")

    otel_spans.clear()
    await db.execute_write_fn(create_with_events)
    spans = _spans_for_namespace(otel_spans, "t16_wrapper_name")
    assert len(spans) == 1
    assert spans[0].attributes["datasette.callback"] == (
        "test_execute_write_fn_callback_name_is_not_the_hook_wrapper"
        ".<locals>.create_with_events"
    )


@pytest.mark.asyncio
async def test_execute_write_fn_nonblocking_spans_link_to_the_new_span(otel_spans):
    # With block=False the write thread spans link to the db.query span from
    # execute_write_fn(), not to the span that was current when it was called.
    db = Datasette(memory=True).add_memory_database("t16_nonblocking")
    await db.execute_write("create table docs (id integer primary key)")

    def insert(conn):
        conn.execute("insert into docs (id) values (1)")

    otel_spans.clear()
    with tracer.start_as_current_span("t16-enqueueing-span") as enqueuer:
        enqueuer_context = enqueuer.get_span_context()
        await db.execute_write_fn(insert, block=False)
    # Writes run in order, so this waits for the non-blocking write to finish
    await db.execute_write("insert into docs (id) values (2)")

    query_spans = [
        span
        for span in _spans_for_namespace(otel_spans, "t16_nonblocking")
        if span.attributes.get("datasette.callback")
    ]
    assert len(query_spans) == 1
    fn_span_context = query_spans[0].context
    linked = [
        span
        for span in otel_spans.get_finished_spans()
        if span.name in ("db.write.queue_wait", "db.write.execute") and span.links
    ]
    assert len(linked) == 2
    for span in linked:
        assert span.parent is None, f"{span.name} is still parented"
        assert span.links[0].context.span_id == fn_span_context.span_id, span.name
        assert span.links[0].context.span_id != enqueuer_context.span_id, span.name


@pytest.mark.asyncio
async def test_execute_does_not_double_wrap(otel_spans):
    # execute() and the SQL string write methods call the private
    # _execute_fn() and _execute_write_fn(), so they create one db.query span.
    db = Datasette(memory=True).add_memory_database("t16_no_double_wrap")
    otel_spans.clear()
    await db.execute_write("create table t (id integer primary key)")
    assert len(_spans_for_namespace(otel_spans, "t16_no_double_wrap")) == 1
    otel_spans.clear()
    await db.execute("select * from t")
    spans = _spans_for_namespace(otel_spans, "t16_no_double_wrap")
    assert len(spans) == 1
    assert len(_children_named(otel_spans, "db.query.execute", spans[0].context)) == 1


@pytest.mark.asyncio
async def test_execute_isolated_fn_span_on_mutable_and_immutable(tmp_path, otel_spans):
    def read_one(conn):
        return conn.execute("select 1").fetchone()[0]

    mutable = Datasette(memory=True).add_memory_database("t16_isolated_mutable")
    otel_spans.clear()
    assert await mutable.execute_isolated_fn(read_one) == 1
    spans = _spans_for_namespace(otel_spans, "t16_isolated_mutable")
    assert len(spans) == 1
    assert spans[0].attributes["datasette.callback"].endswith("read_one")
    # Mutable databases route through the write thread, so the write spans
    # appear as children; immutable ones run on the pool and get none.
    assert _children_named(otel_spans, "db.write.execute", spans[0].context)

    db_path = tmp_path / "t16_isolated_immutable.db"
    sqlite_utils.Database(str(db_path))["t"].insert({"id": 1})
    ds = Datasette()
    immutable = Database(ds, path=str(db_path), is_mutable=False)
    ds.add_database(immutable, name="t16_isolated_immutable")
    try:
        otel_spans.clear()
        assert await immutable.execute_isolated_fn(read_one) == 1
    finally:
        ds.remove_database("t16_isolated_immutable")
    spans = _spans_for_namespace(otel_spans, "t16_isolated_immutable")
    assert len(spans) == 1
    assert spans[0].attributes["datasette.callback"].endswith("read_one")
    assert not _children_named(otel_spans, "db.write.execute", spans[0].context)


@pytest.mark.asyncio
async def test_execute_fn_exception_marks_span_error(otel_spans):
    # execute_fn() has no log_sql_errors option, so exceptions are span errors
    db = Datasette(memory=True).add_memory_database("t16_fn_error")

    def boom(conn):
        raise ValueError("callback failed")

    otel_spans.clear()
    with pytest.raises(ValueError):
        await db.execute_fn(boom)
    spans = _spans_for_namespace(otel_spans, "t16_fn_error")
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in spans[0].events)
