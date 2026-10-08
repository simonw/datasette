"""
Pytest helpers for testing OpenTelemetry instrumentation - Datasette's own
and any plugin's. Part of Datasette's public plugin API; see the "Telemetry
for plugin authors" documentation.

Usage from a plugin's ``conftest.py``::

    from datasette.telemetry_testing import (  # noqa: F401
        MetricsCollector,
        otel_metrics,
        otel_meter_provider,
        otel_provider,
        otel_spans,
    )

Tests can then use the ``otel_spans`` and ``otel_metrics`` fixtures. The
OpenTelemetry SDK is imported lazily, and the fixtures skip if it is not
installed.
"""

import subprocess
import sys

import pytest

from .telemetry_registry import (
    attribute_allowed,
    attribute_value_allowed,
    metric_for,
    span_for,
)

_span_exporter = None
_metric_reader = None


def install_span_exporter():
    """
    Install a TracerProvider + InMemorySpanExporter once per process and
    return the exporter, or None when the SDK is not installed.

    Uses `SimpleSpanProcessor` so spans are exported as soon as they end.
    """
    global _span_exporter
    if _span_exporter is not None:
        return _span_exporter
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )
    except ImportError:
        return None
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)
    # set_tracer_provider() is ignored if a provider was already installed,
    # in which case the fixtures skip
    if otel_trace.get_tracer_provider() is not provider:
        return None
    _span_exporter = exporter
    return exporter


def install_metric_reader():
    """
    Install a MeterProvider + InMemoryMetricReader once per process and
    return the reader, or None when the SDK is not installed.

    Uses delta temporality for counters and histograms, so each collection
    only reports measurements since the previous one.
    """
    global _metric_reader
    if _metric_reader is not None:
        return _metric_reader
    try:
        from opentelemetry import metrics as otel_metrics_api
        from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
        from opentelemetry.sdk.metrics.export import (
            AggregationTemporality,
            InMemoryMetricReader,
        )
    except ImportError:
        return None
    reader = InMemoryMetricReader(
        preferred_temporality={
            Counter: AggregationTemporality.DELTA,
            Histogram: AggregationTemporality.DELTA,
        }
    )
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics_api.set_meter_provider(provider)
    if otel_metrics_api.get_meter_provider() is not provider:
        return None
    _metric_reader = reader
    return reader


@pytest.fixture(scope="session", autouse=True)
def otel_provider():
    "Install the span exporter once per test session, before any spans are created."
    install_span_exporter()


@pytest.fixture(scope="session", autouse=True)
def otel_meter_provider():
    "Install the metric reader once per test session."
    install_metric_reader()


@pytest.fixture(autouse=True)
def otel_reset():
    "Clear recorded spans and drain collected metrics after every test."
    yield
    if _span_exporter is not None:
        _span_exporter.clear()
    if _metric_reader is not None:
        _metric_reader.get_metrics_data()


@pytest.fixture
def otel_spans():
    """
    The in-memory span exporter, cleared before the test. Call
    `.get_finished_spans()` to retrieve spans.
    """
    pytest.importorskip("opentelemetry.sdk")
    exporter = install_span_exporter()
    if exporter is None:
        pytest.skip("OpenTelemetry SDK provider was not installed")
    exporter.clear()
    yield exporter


class MetricsCollector:
    """
    Wraps an `InMemoryMetricReader`.

    `collect()` runs a collection cycle and stores a snapshot, which
    `points()` and `point()` then query.
    """

    def __init__(self, reader):
        self.reader = reader
        self.snapshot = {}
        # (instrumentation scope name, sdk Metric) pairs from the last collect()
        self.collected = []

    def collect(self):
        self.snapshot = {}
        self.collected = []
        data = self.reader.get_metrics_data()
        if data is None:
            return self.snapshot
        for resource_metrics in data.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                scope_name = scope_metrics.scope.name if scope_metrics.scope else None
                for metric in scope_metrics.metrics:
                    self.snapshot.setdefault(metric.name, []).extend(
                        metric.data.data_points
                    )
                    self.collected.append((scope_name, metric))
        return self.snapshot

    def points(self, name, attributes=None):
        "Data points for `name` whose attributes are a superset of `attributes`."
        found = []
        for point in self.snapshot.get(name, []):
            point_attributes = dict(point.attributes or {})
            if all(point_attributes.get(k) == v for k, v in (attributes or {}).items()):
                found.append(point)
        return found

    def point(self, name, attributes=None):
        "The single matching data point, asserting there is exactly one."
        found = self.points(name, attributes)
        assert len(found) == 1, (
            f"expected exactly one {name} point matching {attributes}, "
            f"got {len(found)}: {found}"
        )
        return found[0]


@pytest.fixture
def otel_metrics():
    "A `MetricsCollector`, drained before the test so counts start from zero."
    pytest.importorskip("opentelemetry.sdk")
    reader = install_metric_reader()
    if reader is None:
        pytest.skip("OpenTelemetry SDK meter provider was not installed")
    reader.get_metrics_data()
    yield MetricsCollector(reader)


def _scoped(finished_spans, scope_name):
    if scope_name is None:
        return list(finished_spans)
    return [
        span
        for span in finished_spans
        if span.instrumentation_scope and span.instrumentation_scope.name == scope_name
    ]


def assert_spans_conform(registry_spans, finished_spans, scope_name=None):
    """
    Assert every finished span is registered in `registry_spans`, sets only
    registered attributes and uses allowed attribute values.

    Pass `scope_name` to only check spans from that instrumentation scope.
    """
    problems = []
    for span in _scoped(finished_spans, scope_name):
        entry = span_for(str(span.name), kind=span.kind, spans=registry_spans)
        if entry is None:
            problems.append(f"unregistered span: {span.name!r}")
            continue
        for key, value in (span.attributes or {}).items():
            if not attribute_allowed(entry, str(key)):
                problems.append(f"{span.name}: unregistered attribute {key!r}")
            elif not attribute_value_allowed(entry, str(key), value):
                problems.append(
                    f"{span.name}: {key}={value!r} not in the declared enum"
                )
    assert not problems, "\n".join(problems)


def assert_spans_covered(registry_spans, finished_spans, scope_name=None):
    """
    Assert every entry in `registry_spans` was emitted at least once, with
    each of its attributes that is not `optional=True`.
    """
    spans = _scoped(finished_spans, scope_name)
    seen_attributes = {}
    for span in spans:
        entry = span_for(str(span.name), kind=span.kind, spans=registry_spans)
        if entry is not None:
            seen = seen_attributes.setdefault(str(entry), set())
            seen.update(str(key) for key in (span.attributes or {}))
    problems = []
    for entry in registry_spans:
        if str(entry) not in seen_attributes:
            problems.append(f"registered span never emitted: {entry!r}")
            continue
        required = {
            str(attribute) for attribute in entry.attributes if not attribute.optional
        }
        missing = required - seen_attributes[str(entry)]
        if missing:
            problems.append(
                f"{entry}: registered attributes never emitted: {sorted(missing)}"
            )
    assert not problems, "\n".join(problems)


# Registry instrument kinds mapped to the SDK data type collected for them.
# Both counter kinds collect as Sum, distinguished by is_monotonic.
_KIND_TO_DATA_TYPE = {
    "Counter": "Sum",
    "UpDownCounter": "Sum",
    "Histogram": "Histogram",
    "Observable gauge": "Gauge",
}
_KIND_IS_MONOTONIC = {"Counter": True, "UpDownCounter": False}


def _scoped_metrics(collector, scope_name):
    for scope, metric in collector.collected:
        if scope_name is None or scope == scope_name:
            yield metric


def assert_metrics_conform(registry_metrics, collector, scope_name=None):
    """
    Assert every metric in the collector's last `collect()` is registered in
    `registry_metrics` with a matching instrument kind and unit, sets only
    registered attributes and uses allowed attribute values.

    Pass `scope_name` to only check metrics from that instrumentation scope.
    """
    problems = set()
    for metric in _scoped_metrics(collector, scope_name):
        entry = metric_for(metric.name, metrics=registry_metrics)
        if entry is None:
            problems.add(f"unregistered metric: {metric.name!r}")
            continue
        expected_data_type = _KIND_TO_DATA_TYPE.get(entry.kind)
        actual_data_type = type(metric.data).__name__
        if expected_data_type is not None and actual_data_type != expected_data_type:
            problems.add(
                f"{metric.name}: registry declares {entry.kind}, "
                f"SDK collected {actual_data_type}"
            )
        expected_monotonic = _KIND_IS_MONOTONIC.get(entry.kind)
        actual_monotonic = getattr(metric.data, "is_monotonic", None)
        if (
            expected_monotonic is not None
            and actual_monotonic is not None
            and actual_monotonic != expected_monotonic
        ):
            problems.add(
                f"{metric.name}: registry declares {entry.kind}, but the "
                f"collected Sum is_monotonic={actual_monotonic}"
            )
        if (metric.unit or "") != (entry.unit or ""):
            problems.add(
                f"{metric.name}: instrument unit {metric.unit!r} != "
                f"registry unit {entry.unit!r}"
            )
        for point in metric.data.data_points:
            for key, value in dict(point.attributes or {}).items():
                if not attribute_allowed(entry, str(key)):
                    problems.add(f"{metric.name}: unregistered attribute {key!r}")
                elif not attribute_value_allowed(entry, str(key), value):
                    problems.add(
                        f"{metric.name}: {key}={value!r} not in the declared enum"
                    )
    assert not problems, "\n".join(sorted(problems))


def assert_metrics_covered(registry_metrics, collector, scope_name=None):
    """
    Assert every entry in `registry_metrics` was collected at least once,
    with each of its attributes that is not `optional=True`.

    Call `collect()` once after the workload and before this check.
    """
    seen_attributes = {}
    for metric in _scoped_metrics(collector, scope_name):
        entry = metric_for(metric.name, metrics=registry_metrics)
        if entry is None:
            continue
        seen = seen_attributes.setdefault(str(entry), set())
        for point in metric.data.data_points:
            seen.update(str(key) for key in dict(point.attributes or {}))
    problems = []
    for entry in registry_metrics:
        if str(entry) not in seen_attributes:
            problems.append(f"registered metric never collected: {entry!r}")
            continue
        required = {
            str(attribute) for attribute in entry.attributes if not attribute.optional
        }
        missing = required - seen_attributes[str(entry)]
        if missing:
            problems.append(
                f"{entry}: registered attributes never collected: {sorted(missing)}"
            )
    assert not problems, "\n".join(problems)


def assert_no_forbidden_values(
    forbidden, finished_spans=None, collector=None, scope_name=None
):
    """
    Assert that none of the `forbidden` strings appear anywhere in the
    emitted telemetry: span names, span attribute values, span event names
    and attributes, span status descriptions, or metric point attributes.

    Use fake private values such as tokens or email addresses in your test
    workload, then check that they were not recorded:

        FORBIDDEN = {"secret-token-123", "alice@example.com"}
        run_workload_using_those_values()
        assert_no_forbidden_values(
            FORBIDDEN,
            finished_spans=otel_spans.get_finished_spans(),
            collector=otel_metrics,
        )

    Matches substrings of each value's string form. Empty strings in
    `forbidden` are ignored. Leave `scope_name` unset to also check
    Datasette's own telemetry.
    """
    needles = [needle for needle in forbidden if needle]
    leaks = set()

    def check(value, where):
        text = str(value)
        for needle in needles:
            if needle in text:
                leaks.add(f"{where} contains {needle!r}")

    if finished_spans is not None:
        for span in _scoped(finished_spans, scope_name):
            check(span.name, f"span name {str(span.name)!r}")
            for key, value in (span.attributes or {}).items():
                check(value, f"{span.name} attribute {key}")
            for event in span.events or ():
                check(event.name, f"{span.name} event name")
                for key, value in (event.attributes or {}).items():
                    check(value, f"{span.name} event {event.name} attribute {key}")
            if span.status is not None and span.status.description:
                check(span.status.description, f"{span.name} status description")
    if collector is not None:
        for metric in _scoped_metrics(collector, scope_name):
            for point in metric.data.data_points:
                for key, value in dict(point.attributes or {}).items():
                    check(value, f"metric {metric.name} attribute {key}")
    assert not leaks, "forbidden values leaked into telemetry:\n" + "\n".join(
        sorted(leaks)
    )


def assert_package_never_imports_sdk(*module_names):
    """
    Import the named modules in a fresh interpreter and assert none of them
    imported `opentelemetry.sdk`.

    Run the test that calls this early in your suite: on macOS with CPython
    3.13, starting a subprocess from a process with many threads can crash.
    """
    imports = "; ".join(f"import {name}" for name in module_names)
    code = (
        f"import sys; {imports}; "
        "print([m for m in sys.modules if m.startswith('opentelemetry.sdk')])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]", (
        f"importing {module_names} pulled in the OpenTelemetry SDK: "
        f"{result.stdout.strip()}"
    )
