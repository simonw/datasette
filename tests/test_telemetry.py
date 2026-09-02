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

# Bounded so a broken time limit fails the test instead of hanging it, but far
# too long to finish inside any of the millisecond budgets used below.
SLOW_SQL = """
with recursive counter(x) as (
    select 1 union all select x + 1 from counter where x < 50000000
)
select max(x) from counter
"""


def _db_query_spans(otel_spans):
    return [span for span in otel_spans.get_finished_spans() if span.name == "db.query"]


def _spans_for_namespace(otel_spans, namespace):
    """
    db.query spans belonging to one database.

    Datasette queries its internal catalog constantly - including while a
    Datasette instance is being constructed - so a test that just grabbed
    every db.query span would be reading someone else's traffic.
    """
    return [
        span
        for span in _db_query_spans(otel_spans)
        if span.attributes["db.namespace"] == namespace
    ]


def _children_named(otel_spans, name, parent_span_context):
    """
    Finished spans called `name` whose parent really is `parent_span_context`.

    Parentage is matched on span id, not on "a span with this name exists" -
    a span can exist and still be an unparented root if a thread boundary
    dropped the otel context, which is the exact failure these tests exist
    to catch.
    """
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
    """
    True if `span` reaches `ancestor_span_context` by walking parent links.

    Walks real span ids rather than trusting a shared trace id: a span can
    carry the right trace id and still hang off the wrong parent.
    """
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
    "Every attribute value across every finished span, for the 'no leaked param values' test."
    values = []
    for span in otel_spans.get_finished_spans():
        values.extend((span.attributes or {}).values())
        for event in span.events:
            values.extend((event.attributes or {}).values())
    return values


def test_datasette_package_never_imports_the_sdk():
    """
    Core depends on opentelemetry-api only. The SDK is a test dependency.

    Checked by importing datasette in a fresh process and inspecting
    sys.modules, rather than by grepping, so a lazy `import
    opentelemetry.sdk` inside a function body cannot slip past.

    conftest.py's pytest_collection_modifyitems() moves this test to the
    front of the run by name - if you rename it, rename it there too.
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
    """
    A result actually cut short by max_returned_rows records truncated=True.

    Every other test asserts the attribute is False, so a regression that
    recorded the flag before the slice (or inverted it) would pass the rest
    of the suite.
    """
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
    assert all(span.attributes["db.query.text"] for span in spans)
    # Rendering the page also queries the internal database, so only some of
    # these spans belong to "fixtures".
    assert any(span.attributes["db.namespace"] == "fixtures" for span in spans)


def test_sql_attribute_truncates_at_2048():
    short_sql = "select 1"
    assert sql_attribute(short_sql) == "select 1"
    # Whitespace is stripped, so the same query logged twice with different
    # surrounding whitespace produces one attribute value, not two.
    assert sql_attribute("  select 1\n") == "select 1"

    long_sql = "select 1 -- " + ("x" * 3000)
    truncated = sql_attribute(long_sql)
    assert len(truncated) == MAX_SQL_LENGTH + len("…[truncated]")
    assert truncated.startswith("select 1 -- ")
    assert truncated.endswith("…[truncated]")


@pytest.mark.asyncio
async def test_db_query_text_is_truncated_in_real_span(ds_client, otel_spans):
    # A long trailing SQL comment keeps the query valid and executable while
    # pushing db.query.text well past the 2048 char cap.
    long_sql = "select 1 -- " + ("x" * 3000)
    response = await ds_client.get("/fixtures/-/query.json", params={"sql": long_sql})
    assert response.status_code == 200

    spans = _db_query_spans(otel_spans)
    assert spans
    assert any(len(span.attributes["db.query.text"]) > 100 for span in spans), (
        "expected the long query to reach a span - otherwise this test would "
        "pass even if truncation were never applied"
    )
    for span in spans:
        recorded = span.attributes["db.query.text"]
        assert len(recorded) <= MAX_SQL_LENGTH + len("…[truncated]")


@pytest.mark.asyncio
async def test_no_span_attribute_ever_contains_a_parameter_value(ds_client, otel_spans):
    response = await ds_client.get(
        "/fixtures/-/query.json",
        params={"sql": "select :secret", "secret": SECRET_PARAM_VALUE},
    )
    assert response.status_code == 200
    # Sanity check the value really did flow through as a bound parameter,
    # not inlined into the SQL text, otherwise this test would be vacuous.
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
    A query that runs out the instance-wide sql_time_limit_ms is an error.

    This used to force the timeout with `?_timelimit=5`, but a caller-supplied
    budget shorter than the instance limit is now the signal that the timeout
    was expected - see test_expected_timeout_is_not_a_span_error - so the
    timeout has to come from the setting for this to still test what it was
    written to test.
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
    """
    Drive the real table_counts() path into a timeout; return its db.query span.

    table_counts() is where the headline instance of this lives: the homepage
    counts every table under a 10ms budget and stores None for any table that
    does not finish in time. Before this was fixed, a two-table database
    produced four ERROR spans - two db.query and two db.query.execute - on
    every single homepage hit.
    """
    db = Datasette(memory=True).add_memory_database(database_name)
    await db.execute_write("create table big (id integer primary key, t text)")
    await db.execute_write_many(
        "insert into big (t) values (?)", [["x" * 50] for _ in range(11000)]
    )
    # count_limit caps the scan at 10001 rows, and below 20ms sqlite_timelimit()
    # runs its progress handler on every VM instruction, so 1ms is not a close
    # call - a scan of that size takes single-digit milliseconds at best.
    counts = await db.table_counts(1)
    assert counts == {
        "big": None
    }, "the count did not actually time out, so the rest of this test is vacuous"

    spans = [
        span
        for span in _spans_for_namespace(otel_spans, database_name)
        if "count(*)" in span.attributes["db.query.text"]
    ]
    assert len(spans) == 1
    return spans[0]


@pytest.mark.asyncio
async def test_expected_timeout_is_not_a_span_error(otel_spans):
    span = await _expected_timeout_count_span(otel_spans, "t09_expected_timeout")
    # The useful signal survives; only the red status goes away.
    assert span.attributes["datasette.interrupted"] is True
    assert span.status.status_code != StatusCode.ERROR
    assert not [event for event in span.events if event.name == "exception"]


@pytest.mark.asyncio
async def test_expected_timeout_does_not_error_the_inner_execute_span(otel_spans):
    """
    The same fix has to reach db.query.execute, which sets its own status.

    Half of the original bug lived here: the inner span passed
    set_status_on_exception=log_sql_errors, and table_counts() leaves
    log_sql_errors at its True default, so it went ERROR too.
    """
    span = await _expected_timeout_count_span(otel_spans, "t09_expected_timeout_inner")
    children = _children_named(otel_spans, "db.query.execute", span.context)
    assert len(children) == 1
    child = children[0]
    assert child.status.status_code != StatusCode.ERROR
    assert not [event for event in child.events if event.name == "exception"]


@pytest.mark.asyncio
async def test_unexpected_timeout_is_still_a_span_error(otel_spans):
    """
    A custom_time_limit *above* sql_time_limit_ms is not a short budget.

    This is the half of the rule that stops the fix collapsing into "never
    report timeouts": the caller asked for 5 seconds, the instance overruled it
    at 20ms, and nobody expected that.
    """
    ds = Datasette(memory=True, settings={"sql_time_limit_ms": 20})
    db = ds.add_memory_database("t09_custom_limit_ignored")
    with pytest.raises(QueryInterrupted):
        await db.execute(SLOW_SQL, custom_time_limit=5000)

    spans = _spans_for_namespace(otel_spans, "t09_custom_limit_ignored")
    assert spans
    span = spans[-1]
    # Proves the caller's larger budget really was discarded - otherwise this
    # would be asserting on a query that ran under a 5s limit.
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
    """
    log_sql_errors=False means the caller is probing and expects failures.

    Facet suggestion runs `json_type(column)` against every column precisely
    to discover which ones raise, so marking those spans as errors would put
    two red spans per text column on every table page - burying real failures
    and tripping any alerting keyed on span status.
    """
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
    # Named in-memory databases are shared-cache, so every test in this file
    # needs its own name or the second `create table` hits an existing table.
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
    # executemany() consumes parameter sets and returns no rows at all, so
    # calling this a row count would be a lie. Asserted explicitly because the
    # attribute really was named datasette.rows_returned at one point.
    assert "datasette.rows_returned" not in span.attributes


# --- Context propagation across thread boundaries --------------------------
#
# Every assertion below checks parentage (child.parent.span_id ==
# expected_parent.span_id, in the same trace), not merely that spans exist.
# Spans can exist and still be wrongly parented - or be unparented roots - if
# a thread boundary drops the otel context, which is exactly the failure mode
# these tests exist to prevent.


@pytest.mark.asyncio
async def test_db_query_execute_parents_to_db_query(ds_client, otel_spans):
    # execute_fn()'s executor.submit() is thread boundary #1. The
    # db.query.execute span is created inside the worker thread; without the
    # copy_context() propagation it comes back as an unparented root span
    # rather than a child of db.query.
    response = await ds_client.get("/fixtures/-/query.json?sql=select+1")
    assert response.status_code == 200

    query_spans = [
        span
        for span in _spans_for_namespace(otel_spans, "fixtures")
        if span.attributes["db.query.text"] == "select 1"
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
    # The execute span is strictly contained by the round-trip span, and the
    # gap between the two is the thread-pool wait.
    assert query_span.start_time <= children[0].start_time
    assert children[0].end_time <= query_span.end_time


@pytest.mark.asyncio
async def test_immutable_database_propagates_context(tmp_path, otel_spans):
    # Thread boundary #3, the easy one to miss: immutable databases route
    # execute_isolated_fn() through loop.run_in_executor() directly rather
    # than through the write thread. A span created inside that worker must
    # still parent to whatever was current when execute_isolated_fn() was
    # awaited, or every immutable-database operation emits orphan roots.
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
    children = _children_named(
        otel_spans, "t04-child-in-isolated-worker", parent_context
    )
    assert len(children) == 1


@pytest.mark.asyncio
async def test_write_spans_parent_to_db_query(otel_spans):
    # Thread boundary #2: WriteTask -> queue.Queue -> the write thread.
    # db.write.queue_wait and db.write.execute are both direct children of
    # the db.query span that was current on the event loop at enqueue time,
    # so they are siblings rather than nested inside one another.
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
    # Siblings, not parent/child: the queue wait is over by the time the
    # write begins.
    assert queue_wait_children[0].end_time <= execute_span.start_time


@pytest.mark.asyncio
async def test_write_queue_wait_duration_reflects_real_wait(otel_spans):
    # db.write.queue_wait is built from explicit start/end timestamps -
    # task.enqueued_at_ns, captured on the event loop, through to the moment
    # the write thread dequeued it. If it were a plain `with` block on the
    # write thread it would instead measure the microseconds spent building
    # the span object, and this assertion would fail.
    ds = Datasette(memory=True)
    db = ds.add_memory_database("t04_queue_wait")
    await db.execute_write("create table docs (id integer primary key)")

    def slow_write(conn):
        time.sleep(0.1)

    # Queue a deliberately slow write without waiting for it, then queue a
    # second write immediately behind it: the second task sits in the queue
    # for roughly the duration of the first.
    _, slow_future = await db._send_to_write_thread(slow_write, block=False)
    await db.execute_write("insert into docs (id) values (1)")
    await slow_future

    query_spans = [
        span
        for span in _spans_for_namespace(otel_spans, "t04_queue_wait")
        if span.attributes["db.query.text"] == "insert into docs (id) values (1)"
    ]
    assert query_spans, "expected a db.query span for the queued-behind insert"
    queue_wait_children = _children_named(
        otel_spans, "db.write.queue_wait", query_spans[-1].context
    )
    assert len(queue_wait_children) == 1
    duration_ns = queue_wait_children[0].end_time - queue_wait_children[0].start_time
    # The slow write sleeps 100ms; anything above 10ms is far beyond the
    # microseconds a mis-timestamped span would report.
    assert duration_ns > 10_000_000, f"queue wait was only {duration_ns}ns"


async def _write_spans_from_one_enqueue(otel_spans, name, block):
    """
    Run exactly one write through the write thread from inside a span of our
    own, and return (enqueueing span context, {span name: span}).

    `_send_to_write_thread` is called directly rather than `execute_write()`
    because `execute_write()` opens its own db.query span, which would then
    be the span current at enqueue time - so the parent/link would point at
    that span rather than at the one this test controls.

    The exporter is cleared immediately before the enqueue so the write spans
    collected here can only have come from this one write.
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
        # The point of block=False is that the write happens after the
        # caller has returned and the enqueueing span above has closed.
        # Awaiting the reply future outside that `with` waits for the write
        # thread deterministically - it is resolved only after both write
        # spans have ended and been exported.
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
    # Regression guard for ticket 07: block=True genuinely has containment -
    # the caller awaits the reply future - so those spans must keep parenting
    # to the enqueueing span, and must not grow links.
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
    # block=False returns before the write runs, so the enqueueing span has
    # already ended (and exported) by the time these spans start. Parenting
    # them to it would draw a child outliving its closed parent, so they are
    # roots in their own traces, linked back to the span that caused them.
    enqueuer_context, spans = await _write_spans_from_one_enqueue(
        otel_spans, "t07_nonblocking_write", block=False
    )
    assert enqueuer_context.is_valid, "test's own enqueueing span was not recorded"
    for name, span in spans.items():
        assert span.parent is None, f"{name} is still parented"
        # A link does not join the linked trace: each of these is its own
        # root trace, which is the correct shape and not a workaround.
        assert span.context.trace_id != enqueuer_context.trace_id, name
        assert len(span.links) == 1, f"{name} has links {span.links}"
        link_context = span.links[0].context
        assert link_context.trace_id == enqueuer_context.trace_id, name
        assert link_context.span_id == enqueuer_context.span_id, name
    # The two write spans are independent roots, not nested in one another.
    assert (
        spans["db.write.queue_wait"].context.trace_id
        != spans["db.write.execute"].context.trace_id
    )


@pytest.mark.asyncio
async def test_nonblocking_write_link_has_no_attributes(otel_spans):
    # There is only one kind of link here, so a relationship-name attribute
    # would be a constant conveying nothing the link's existence does not.
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
    block=False spans pass an explicit empty Context, not merely "no attach".

    Nothing is attached for a block=False task, but "nothing attached" is not
    the same as "no ambient context": the write thread is persistent, and
    anything running on it - a prepare_connection plugin hook, say - can
    attach a context and never detach it. Without the explicit `context=`
    these spans would silently parent to that leftover span instead of being
    roots, and no other test here would notice, because in every other test
    the write thread's ambient context happens to be empty.

    So this test leaks exactly such a context on the write thread, the way a
    careless plugin would, and then checks the write spans are still roots.
    """
    ds = Datasette(memory=True)
    db = ds.add_memory_database("t07_ambient_write_thread")
    write_thread_name = "_execute_writes for database t07_ambient_write_thread"
    real_prepare_connection = ds._prepare_connection
    leaked = {}

    def prepare_connection(conn, database):
        if threading.current_thread().name == write_thread_name:
            # Runs once, on the write thread, before any task is dequeued -
            # and never detaches, which is the whole point.
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
    """
    The inner db.query.execute span must honour log_sql_errors too.

    It is created inside the worker thread, so without record_exception /
    set_status_on_exception being passed through it would mark every facet
    suggestion probe as failed even though the outer db.query span correctly
    reports the failure as suppressed.
    """
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
    """
    invoke_startup() runs with no request, so nothing it does has an ambient
    span to nest under. Without datasette.startup every register_* hook, every
    internal-catalog read and every catalog write becomes its own single-span
    root trace - around twenty of them per fresh instance.
    """
    ds = Datasette(memory=True)
    # Named in-memory databases are shared-cache, so this needs its own name.
    ds.add_memory_database("t05_startup_db")
    # Constructing a Datasette already touches the internal catalog, and that
    # work is genuinely outside startup. Clear so the assertions below describe
    # invoke_startup() alone.
    otel_spans.clear()

    # Deliberately no ambient span: this mirrors the ASGI lifespan path, where
    # startup runs before any request exists. If something did wrap this call
    # the "one root" assertion below would pass for the wrong reason.
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

    # The internal catalog reads are what made up the bulk of the orphans.
    internal_queries = [
        span
        for span in spans
        if span.name == "db.query" and span.attributes["db.namespace"] == "__INTERNAL__"
    ]
    assert internal_queries, "expected internal-catalog db.query spans during startup"
    assert all(
        _descends_from(span, startup.context, by_span_id) for span in internal_queries
    )

    # ...and the catalog writes, which reach the span through the write thread,
    # so they also prove the ticket-04 context capture survives startup.
    write_spans = [span for span in spans if span.name.startswith("db.write.")]
    assert write_spans, "expected db.write.* spans during startup"
    assert all(
        _descends_from(span, startup.context, by_span_id) for span in write_spans
    )


# --- Semantic conventions: span kind, scope, db.operation/collection -------


@pytest.mark.asyncio
async def test_db_query_is_client_kind_and_children_are_internal(otel_spans):
    """
    db.query is a database client span; Datasette's decomposition of it is not.

    Trace UIs key their database rendering off the span kind rather than off
    db.system, so db.query has to be CLIENT. db.query.execute,
    db.write.execute and db.write.queue_wait deliberately stay INTERNAL: they
    are parts of one logical query rather than three separate database calls,
    and queue_wait touches no database at all - marking them CLIENT would
    make one query look like several to anything counting spans by kind.
    """
    # Named in-memory databases are shared-cache, so this needs its own name.
    db = Datasette(memory=True).add_memory_database("t06_span_kind")
    # All four db.query entry points, so a missed `kind=` on any one of them
    # fails here - plus the write path (db.write.queue_wait,
    # db.write.execute) and the read path (db.query.execute) children.
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
    """
    Spans say which Datasette produced them and which semconv version their
    attribute names follow.

    Before get_tracer() was given a version and a schema URL every exported
    scope was name='datasette' version='' schema_url='', so nothing
    downstream could tell which Datasette a span came from, or whether
    `db.system` meant `db.system` or the post-1.30.0 `db.system.name`.
    """
    response = await ds_client.get("/fixtures/-/query.json?sql=select+1")
    assert response.status_code == 200

    spans = _db_query_spans(otel_spans)
    assert spans, "expected at least one db.query span"
    scope = spans[-1].instrumentation_scope

    assert scope.name == "datasette"
    assert scope.version == __version__
    # The literal URL, not the SCHEMA_URL constant: comparing the span
    # against the same constant the instrumentation is built from would only
    # catch a dropped argument, never a wrong value. Bumping this is a claim
    # about the attribute names on the wire - see SCHEMA_URL in telemetry.py.
    assert scope.schema_url == "https://opentelemetry.io/schemas/1.29.0"
    assert SCHEMA_URL == "https://opentelemetry.io/schemas/1.29.0"
    assert __version__, "the scope version must not be empty"


def test_db_operation_name_from_leading_keyword():
    assert sql_operation_name("select 1") == "SELECT"
    assert sql_operation_name("  insert into x (a) values (1)") == "INSERT"
    # A leading CTE reports WITH rather than the operation inside it. That is
    # the documented limitation, not an accident - see sql_operation_name().
    assert sql_operation_name("with foo as (select 1) select * from foo") == "WITH"
    # Unrecognised leading keyword: no attribute rather than a wrong one, and
    # no unbounded value set derived from attacker-supplied SQL.
    assert sql_operation_name("gibberish 1") is None
    # Not a parser: a parenthesised SELECT and a leading comment both yield
    # nothing rather than a guess.
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
        if span.attributes["db.query.text"] == "select 1"
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
    executescript() runs several statements, so naming the operation after
    the first one would be a lie. Semantic conventions say db.operation.name
    should not be extracted from query text that can hold more than one
    operation, so the attribute is absent entirely.

    The script deliberately starts with `create`, which *is* on the
    allowlist - so this fails if the call site ever starts calling
    sql_operation_name().
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


@pytest.mark.asyncio
async def test_db_collection_name_set_from_table_argument(ds_client, otel_spans):
    db = ds_client.ds.get_database("fixtures")
    await db.execute("select pk from facetable limit 1", table="facetable")

    spans = _spans_for_namespace(otel_spans, "fixtures")
    assert spans
    assert spans[-1].attributes["db.collection.name"] == "facetable"


@pytest.mark.asyncio
async def test_db_collection_name_absent_without_table_argument(ds_client, otel_spans):
    """
    db.collection.name comes only from an explicit table= argument and is
    never derived from the SQL.

    Deriving it would be a parse, and on an instance where anybody can create
    a table the value set has no ceiling. Without this test the one above
    would still pass if the table name were being read out of the query text.
    """
    db = ds_client.ds.get_database("fixtures")
    await db.execute("select pk from facetable limit 1")

    spans = _spans_for_namespace(otel_spans, "fixtures")
    assert spans
    span = spans[-1]
    assert span.attributes["db.query.text"] == "select pk from facetable limit 1"
    assert "db.collection.name" not in span.attributes


@pytest.mark.parametrize(
    "path,table",
    (
        ("/fixtures/facetable.json", "facetable"),
        ("/fixtures/simple_primary_key/1.json", "simple_primary_key"),
    ),
)
@pytest.mark.asyncio
async def test_table_and_row_pages_set_db_collection_name(
    ds_client, otel_spans, path, table
):
    "The table and row views know their table, so their queries carry it."
    response = await ds_client.get(path)
    assert response.status_code == 200

    spans = _spans_for_namespace(otel_spans, "fixtures")
    assert spans
    assert any(
        span.attributes.get("db.collection.name") == table for span in spans
    ), f"expected a db.query span from {path} carrying db.collection.name"
