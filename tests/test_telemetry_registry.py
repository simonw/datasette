"""
Tests that the spans, attributes and metrics Datasette emits match
datasette/telemetry_registry.py, in both directions.
"""

import copy
import io
import itertools
import pickle

import pytest
import pytest_asyncio

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.trace import SpanKind

from datasette import hookimpl
from datasette import telemetry_registry as reg
from datasette.app import Datasette
from datasette.database import QueryInterrupted
from datasette.telemetry_testing import assert_metrics_conform, assert_metrics_covered
from datasette.utils.sqlite import sqlite3

# Written out as literals rather than read from the registry, so renaming a
# signal fails these tests.
EXPECTED_ATTRIBUTES = {
    "db.query": {
        "db.system",
        "db.namespace",
        "db.query.text",
        "datasette.callback",
        "db.operation.name",
        "datasette.param_count",
        "datasette.param_sets",
        "datasette.time_limit_ms",
        "datasette.rows_returned",
        "datasette.truncated",
        "datasette.interrupted",
        "datasette.sql_error_suppressed",
        "datasette.executescript",
        "datasette.executemany",
    },
    "db.query.execute": set(),
    "db.write.queue_wait": set(),
    "db.write.execute": {
        "datasette.isolated_connection",
        "datasette.transaction",
    },
    "datasette.startup": set(),
}
EXPECTED_SPANS = set(EXPECTED_ATTRIBUTES)

# The HTTP request span name is composed at runtime as "{method} {route}", so
# it is checked by shape rather than as a literal. The workload only issues GETs.
EXPECTED_HTTP_SPAN_NAME = "{http.request.method} {http.route}"
EXPECTED_HTTP_METHOD_NAMES = {"GET"}
EXPECTED_HTTP_ATTRIBUTES = {
    "http.request.method",
    "http.route",
    "url.path",
    "url.scheme",
    "server.address",
    "user_agent.original",
    "http.response.status_code",
    "error.type",
    "datasette.internal_client",
}

# The registry uses the name template for the request span.
EXPECTED_REGISTRY_ATTRIBUTES = dict(
    EXPECTED_ATTRIBUTES, **{EXPECTED_HTTP_SPAN_NAME: EXPECTED_HTTP_ATTRIBUTES}
)
EXPECTED_REGISTRY_NAMES = set(EXPECTED_REGISTRY_ATTRIBUTES)

# Named in-memory databases are shared between instances, so each workload
# uses a unique name.
_names = itertools.count()


def _unique(prefix):
    return f"{prefix}{next(_names)}"


class _BoomPlugin:
    "A route that raises, producing a 500 and error.type on the request span."

    __name__ = "TelemetryRegistryBoomPlugin"

    @hookimpl
    def register_routes(self):
        return [(r"^/-/telemetry-registry-boom$", lambda: 1 / 0)]


async def exercise():
    """
    Drive enough of Datasette to emit every registered span and attribute,
    including datasette.startup. Returns the instance so the caller can close it.
    """
    name = _unique("registry")
    ds = Datasette(memory=True)
    ds.add_memory_database(name)
    # datasette.startup
    await ds.invoke_startup()
    db = ds.get_database(name)

    # Writes: db.write.queue_wait, db.write.execute, db.query
    await db.execute_write("create table t (id integer primary key, v text)")
    # datasette.executemany, datasette.param_sets
    await db.execute_write_many(
        "insert into t (id, v) values (?, ?)", [[i, f"v{i}"] for i in range(30)]
    )
    # datasette.executescript
    await db.execute_write_script("create table t2 (id integer); drop table t2;")
    # datasette.transaction=False - VACUUM cannot run inside a transaction
    await db.execute_write("vacuum", transaction=False)
    # datasette.isolated_connection=True
    await db.execute_isolated_fn(lambda conn: conn.execute("select 1").fetchone())

    # datasette.callback, using named functions rather than lambdas
    def registry_read_callback(conn):
        return conn.execute("select count(*) from t").fetchone()

    def registry_write_callback(conn):
        conn.execute("insert into t (id, v) values (100, 'callback')")

    await db.execute_fn(registry_read_callback)
    await db.execute_write_fn(registry_write_callback)

    # Reads: db.query.execute, datasette.rows_returned, datasette.truncated,
    # datasette.param_count, datasette.time_limit_ms
    await db.execute("select * from t where id > :n", {"n": 5})
    await db.execute("select * from t", truncate=True)

    # datasette.sql_error_suppressed
    with pytest.raises(sqlite3.OperationalError):
        await db.execute("select nope from t", log_sql_errors=False)

    # datasette.interrupted: an unbounded recursive CTE always exceeds 1ms
    with pytest.raises(QueryInterrupted):
        await db.execute(
            "with recursive c(x) as (select 0 union all select x+1 from c) "
            "select * from c",
            custom_time_limit=1,
        )

    # HTTP request spans and their attributes
    assert (await ds.client.get(f"/{name}/t?_facet=v")).status_code == 200
    assert (await ds.client.get(f"/{name}/t/1.json")).status_code == 200

    # error.type on the request span, set by a 5xx response
    ds.pm.register(_BoomPlugin(), name="telemetry-registry-boom")
    try:
        response = await ds.client.get("/-/telemetry-registry-boom")
        assert response.status_code == 500
    finally:
        ds.pm.unregister(name="telemetry-registry-boom")
    return ds


@pytest_asyncio.fixture
async def emitted(otel_spans):
    """
    Every (span name, span kind, attributes) triple emitted by exercise().
    The kind is needed to resolve the dynamically named request span.
    """
    ds = await exercise()
    spans = otel_spans.get_finished_spans()
    assert spans, "no spans captured - the fixture is not exercising anything"
    # str() so failure messages show plain strings, not registry instances
    collected = tuple(
        (
            str(span.name),
            span.kind,
            {str(key): value for key, value in (span.attributes or {}).items()},
        )
        for span in spans
    )
    ds.close()
    return collected


def _partition(emitted):
    "The statically named spans, and the dynamically named request spans."
    static = [record for record in emitted if record[1] is not SpanKind.SERVER]
    server = [record for record in emitted if record[1] is SpanKind.SERVER]
    return static, server


def _keys_by_span(records):
    by_span = {}
    for name, _kind, attributes in records:
        by_span.setdefault(name, set()).update(attributes)
    return by_span


@pytest.mark.asyncio
async def test_workload_emits_exactly_the_expected_names(emitted):
    "Emitted span and attribute names match the expected literals."
    static, server = _partition(emitted)
    by_span = _keys_by_span(static)
    assert set(by_span) == EXPECTED_SPANS
    assert by_span == EXPECTED_ATTRIBUTES

    assert server, "the workload made HTTP requests but no SERVER span was emitted"
    union = set()
    methods = set()
    for name, _kind, attributes in server:
        union |= set(attributes)
        route = attributes.get("http.route")
        # Every request in the workload matches a route
        assert route, f"the request span {name!r} carries no http.route"
        method, _, name_route = name.partition(" ")
        assert name_route == route, (
            f"the request span is named {name!r}, which is not the "
            f"`{{method}} {{route}}` of {method!r} and {route!r}"
        )
        methods.add(method)
    assert methods == EXPECTED_HTTP_METHOD_NAMES
    assert union == EXPECTED_HTTP_ATTRIBUTES


def test_registry_matches_the_expected_names():
    "Registry names match the expected literals."
    assert {str(span) for span in reg.SPANS} == EXPECTED_REGISTRY_NAMES
    for span in reg.SPANS:
        assert {
            str(attribute) for attribute in span.attributes
        } == EXPECTED_REGISTRY_ATTRIBUTES[str(span)], f"{span} attributes have drifted"


@pytest.mark.asyncio
async def test_every_emitted_span_is_registered(emitted):
    "A span added without a registry entry would be missing from the docs."
    unregistered = sorted(
        {name for name, kind, _ in emitted if reg.span_for(name, kind) is None}
    )
    assert (
        not unregistered
    ), f"these spans are emitted but not in telemetry_registry.SPANS: {unregistered}"


@pytest.mark.asyncio
async def test_every_emitted_attribute_is_registered(emitted):
    "An attribute added without a registry entry would be missing from the docs."
    unregistered = sorted(
        {
            f"{name} -> {key}"
            for name, kind, keys in emitted
            for key in keys
            if not reg.attribute_allowed(reg.span_for(name, kind), key)
        }
    )
    assert (
        not unregistered
    ), "these span attributes are emitted but not registered: " + ", ".join(
        unregistered
    )


@pytest.mark.asyncio
async def test_every_registered_span_is_emitted(emitted):
    "The docs should not describe a span that is no longer emitted."
    # Compare by identity: the request span's registry name never appears on
    # the wire.
    resolved = {id(reg.span_for(name, kind)) for name, kind, _ in emitted}
    missing = sorted(str(span) for span in reg.SPANS if id(span) not in resolved)
    assert not missing, (
        f"these spans are documented but never emitted by the workload: {missing}. "
        "Either the instrumentation was removed, or exercise() no longer reaches it."
    )


@pytest.mark.asyncio
async def test_every_registered_attribute_is_emitted(emitted):
    """
    Every registered attribute, including optional ones, is emitted at least
    once. If a new attribute only appears in rare cases, extend exercise().
    """
    by_entry = {}
    for name, kind, keys in emitted:
        entry = reg.span_for(name, kind)
        if entry is not None:
            by_entry.setdefault(id(entry), set()).update(keys)
    missing = []
    for span in reg.SPANS:
        emitted_keys = by_entry.get(id(span), set())
        for attribute in span.attributes:
            if attribute not in emitted_keys:
                missing.append(f"{span} -> {attribute}")
    assert not missing, (
        "these attributes are documented but never emitted by the workload: "
        + ", ".join(sorted(missing))
    )


def test_registry_has_no_duplicate_names():
    assert len(set(reg.SPANS)) == len(reg.SPANS)
    for span in reg.SPANS:
        assert len(set(span.attributes)) == len(
            span.attributes
        ), f"{span} lists an attribute twice"


def test_registry_entries_are_documented():
    "Every entry has a description, used to generate the docs."
    for span in reg.SPANS:
        assert span.description.strip(), f"{span} has no description"
        for attribute in span.attributes:
            assert attribute.description.strip(), f"{span} -> {attribute} has none"


def test_registry_entries_are_usable_as_plain_strings():
    assert isinstance(reg.DB_QUERY, str)
    assert isinstance(reg.DB_NAMESPACE, str)
    assert reg.DB_QUERY == "db.query"
    assert reg.DB_NAMESPACE == "db.namespace"
    assert f"{reg.DB_QUERY}.execute" == "db.query.execute"


def test_registry_entries_survive_deepcopy_and_pickle():
    """
    A copied or unpickled entry is a plain str. ConsoleMetricExporter
    deepcopies metric attributes, which use registry entries as keys.
    """
    for entry in (reg.DB_NAMESPACE, reg.DB_QUERY, reg.M_OPERATION_DURATION):
        assert copy.deepcopy({entry: 1}) == {str(entry): 1}
        assert type(copy.deepcopy(entry)) is str
        assert pickle.loads(pickle.dumps(entry)) == str(entry)
        # The original entry keeps its metadata
        assert entry.description.strip()


@pytest.mark.asyncio
async def test_console_metric_exporter_renders_core_metric_points(otel_metrics):
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        MetricExportResult,
    )

    name = _unique("registry_console_export")
    ds = Datasette(memory=True)
    ds.add_memory_database(name)
    await ds.invoke_startup()
    # Produces a db.client.operation.duration point keyed by DB_NAMESPACE
    await ds.get_database(name).execute("select 1")

    data = otel_metrics.reader.get_metrics_data()
    assert data is not None, "no metrics captured - nothing to export"
    exporter = ConsoleMetricExporter(out=io.StringIO())
    assert exporter.export(data) is MetricExportResult.SUCCESS
    ds.close()


def test_every_histogram_declares_bucket_boundaries():
    """
    Every histogram declares bucket boundaries, and only histograms do.
    OpenTelemetry's defaults are meant for milliseconds, not seconds.
    """
    for metric in reg.METRICS:
        if metric.kind == reg.HISTOGRAM:
            assert metric.buckets, f"{metric} is a histogram with no boundaries"
            assert list(metric.buckets) == sorted(
                set(metric.buckets)
            ), f"{metric} boundaries must be ascending and unique"
            assert metric.buckets[0] > 0, f"{metric} has a non-positive boundary"
        else:
            assert (
                metric.buckets is None
            ), f"{metric} is a {metric.kind} and cannot have bucket boundaries"


def test_dynamic_span_lookup():
    """
    dynamic=True entries such as the request span match on kind. They never
    match without a kind, and never override a registered name.
    """
    assert reg.span_for("GET", SpanKind.SERVER) is reg.HTTP_REQUEST
    assert reg.span_for("POST /^/(?P<database>[^/]+)$", SpanKind.SERVER) is (
        reg.HTTP_REQUEST
    )
    assert reg.span_for("GET") is None
    assert reg.span_for("anything at all", SpanKind.INTERNAL) is None
    assert reg.span_for("db.query", SpanKind.SERVER) is reg.DB_QUERY


def test_span_and_attribute_lookup():
    assert reg.span_for("db.query") is reg.DB_QUERY
    assert reg.span_for("datasette.startup") is reg.STARTUP
    assert reg.span_for("not.a.datasette.span") is None
    assert reg.attribute_allowed(reg.DB_QUERY, "db.namespace")
    assert not reg.attribute_allowed(reg.DB_QUERY, "db.namespace.extra")
    assert not reg.attribute_allowed(reg.DB_QUERY, "datasette.isolated_connection")
    assert not reg.attribute_allowed(None, "db.namespace")


# --- Metric conformance ----------------------------------------------------


@pytest_asyncio.fixture
async def emitted_metrics(otel_metrics):
    """
    Metric names and (metric name, attribute key) pairs from a broad workload.
    Checks use attribute keys rather than values, since other Datasette
    instances in the session can also report points.
    """
    # Reaches every synchronous metric except datasette.sql.queries.interrupted
    ds = await exercise()

    # datasette.sql.queries.interrupted ignores custom_time_limit timeouts, so
    # this needs an instance with a low sql_time_limit_ms.
    slow_name = _unique("registry_metrics_slow")
    slow = Datasette(memory=True, settings={"sql_time_limit_ms": 5})
    slow.add_memory_database(slow_name)
    await slow.invoke_startup()
    slow_db = slow.get_database(slow_name)
    with pytest.raises(QueryInterrupted):
        await slow_db.execute(
            "with recursive c(x) as (select 0 union all select x+1 from c) "
            "select * from c"
        )

    # Collect before closing the instances so the observable gauges report them
    otel_metrics.collect()
    snapshot = otel_metrics.snapshot
    assert snapshot, "no metrics captured - the fixture is not exercising anything"
    pairs = set()
    for metric_name, points in snapshot.items():
        for point in points:
            for key in point.attributes or {}:
                pairs.add((metric_name, key))
    ds.close()
    slow.close()
    return {"names": set(snapshot), "pairs": pairs, "collector": otel_metrics}


@pytest.mark.asyncio
async def test_metrics_conform_to_the_registry(emitted_metrics):
    """
    Emitted metric names, kinds, units, attribute keys and enum values match
    the registry, using the plugin testing helper.
    """
    assert_metrics_conform(
        reg.METRICS, emitted_metrics["collector"], scope_name="datasette"
    )


@pytest.mark.asyncio
async def test_every_registered_metric_is_emitted(emitted_metrics):
    assert_metrics_covered(
        reg.METRICS, emitted_metrics["collector"], scope_name="datasette"
    )


@pytest.mark.asyncio
async def test_every_registered_metric_attribute_is_emitted(emitted_metrics):
    "Every registered metric attribute, including optional ones, is emitted."
    emitted_keys_by_metric = {}
    for metric_name, key in emitted_metrics["pairs"]:
        emitted_keys_by_metric.setdefault(metric_name, set()).add(key)

    missing = []
    for metric in reg.METRICS:
        if str(metric) not in emitted_metrics["names"]:
            # Reported by test_every_registered_metric_is_emitted
            continue
        emitted_keys = emitted_keys_by_metric.get(str(metric), set())
        for attribute in metric.attributes:
            if attribute not in emitted_keys:
                missing.append(f"{metric} -> {attribute}")
    assert not missing, (
        "these metric attributes are documented but never emitted by the "
        "test workload: " + ", ".join(sorted(missing))
    )


def test_prefix_span_lookup():
    "prefix=True matching, which core does not use but plugin registries can."
    hook = reg.SpanName("myplugin.hook.", "A hypothetical span family", prefix=True)
    spans = reg.SPANS + (hook,)
    assert reg.span_for("myplugin.hook.render_cell", spans=spans) is hook
    assert reg.span_for("myplugin.hook.anything", spans=spans) is hook
    assert reg.span_for("myplugin.hookish", spans=spans) is None
    assert reg.span_for("db.query", spans=spans) is reg.DB_QUERY


def test_exact_match_wins_over_prefix():
    family = reg.SpanName("db.", "Greedy prefix", prefix=True)
    spans = (family,) + reg.SPANS
    assert reg.span_for("db.query", spans=spans) is reg.DB_QUERY
    assert reg.span_for("db.anything-else", spans=spans) is family


def test_attribute_values_enum_enforced():
    outcome = reg.Attribute("myplugin.outcome", "Enum.", values={"ok", "error"})
    open_attr = reg.Attribute("myplugin.note", "Open value set.")
    span = reg.SpanName("myplugin.job", "Test span", (outcome, open_attr))
    assert reg.attribute_value_allowed(span, "myplugin.outcome", "ok")
    assert not reg.attribute_value_allowed(span, "myplugin.outcome", "surprise")
    assert reg.attribute_value_allowed(span, "myplugin.note", "anything at all")
    assert not reg.attribute_value_allowed(span, "not.registered", "x")
    assert not reg.attribute_value_allowed(None, "myplugin.outcome", "ok")
