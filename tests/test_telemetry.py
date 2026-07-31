import json
import sqlite3
import subprocess
import sys

import pytest
from opentelemetry.trace import StatusCode

from datasette.app import Datasette
from datasette.telemetry import MAX_SQL_LENGTH, sql_attribute

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
