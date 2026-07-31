import json
import sqlite3
import subprocess
import sys
import time

import pytest
import sqlite_utils
from opentelemetry import trace as otel_trace
from opentelemetry.trace import StatusCode

from datasette.app import Datasette
from datasette.database import Database
from datasette.telemetry import MAX_SQL_LENGTH, sql_attribute, tracer

SECRET_PARAM_VALUE = "SUPER_SECRET_PARAM_VALUE_XYZ_123"

INVALID_SQL = "select this_is_not_valid_sql from nowhere"


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
async def test_query_interrupted_sets_error_status(ds_client, otel_spans):
    response = await ds_client.get(
        "/fixtures/-/query.json",
        params={"sql": "select sleep(0.05)", "_timelimit": 5},
    )
    assert response.status_code == 400

    spans = _db_query_spans(otel_spans)
    assert spans
    span = spans[-1]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["datasette.interrupted"] is True
    assert span.events
    assert all(event.name == "exception" for event in span.events)


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
