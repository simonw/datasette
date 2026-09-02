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

Importing the fixture names into a conftest registers them; ``otel_provider``
and ``otel_meter_provider`` are session-scoped and autouse, so a real SDK
provider (when the SDK is installed) is in place before any test emits a
signal. Tests then take ``otel_spans`` / ``otel_metrics``. Everything here
imports the OpenTelemetry SDK lazily: with no SDK installed the fixtures
skip rather than fail, and importing this module costs nothing.

The conformance helpers (`assert_spans_conform`, `assert_registry_covered`)
check a registry of `SpanName` entries against actually-finished spans in
both directions - emitted-but-unregistered and registered-but-never-emitted,
the two drift modes documented in `tests/test_telemetry_registry.py`.
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

    `set_tracer_provider()` is effectively once-per-process (a second call
    logs a warning and is ignored), so this must run before anything asserts
    on spans. A `SimpleSpanProcessor` exports synchronously on span end - no
    background batching thread, so assertions immediately after a request
    never race.
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
    _span_exporter = exporter
    return exporter


def install_metric_reader():
    """
    Install a MeterProvider + InMemoryMetricReader once per process and
    return the reader, or None when the SDK is not installed.

    DELTA temporality for counters and histograms, so each collection
    reports only what happened since the previous one - with the SDK default
    of CUMULATIVE, every metrics test would see every measurement from every
    earlier test in the session.
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
    otel_metrics_api.set_meter_provider(MeterProvider(metric_readers=[reader]))
    _metric_reader = reader
    return reader


@pytest.fixture(scope="session", autouse=True)
def otel_provider():
    """
    Session-scoped, autouse: install the span exporter exactly once, before
    any span is created.

    `datasette.telemetry.tracer` (and a plugin's own tracer) is a
    module-level `ProxyTracer`: once a provider exists, the first span it
    starts resolves a concrete tracer and caches it permanently. It does
    *not* cache the no-op tracer, so a span started before this fixture runs
    is merely lost rather than poisoning the tracer for the process. With no
    SDK installed this does nothing and spans stay no-op.
    """
    install_span_exporter()


@pytest.fixture(scope="session", autouse=True)
def otel_meter_provider():
    """
    Session-scoped, autouse: install the metric reader once per process.

    Unlike the tracer, ordering is not load-bearing - `_ProxyMeter` and its
    instruments forward to a provider installed after they were created.
    Still autouse for symmetry, and so a single reader collects all run.
    """
    install_metric_reader()


@pytest.fixture
def otel_spans():
    """
    Function-scoped access to the finished-spans exporter: clears spans left
    over from previous tests, then yields the exporter so a test can call
    `.get_finished_spans()`. Skips if the OTel SDK is not installed.
    """
    pytest.importorskip("opentelemetry.sdk")
    exporter = install_span_exporter()
    if exporter is None:
        pytest.skip("OpenTelemetry SDK provider was not installed")
    exporter.clear()
    yield exporter


class MetricsCollector:
    """
    Thin reader over an `InMemoryMetricReader`.

    `collect()` runs a collection cycle - which is what invokes observable
    gauge callbacks - and snapshots the result. Queries then run against
    that snapshot rather than re-collecting, so a test that inspects
    several metrics sees one consistent moment and does not drain delta
    state twice.
    """

    def __init__(self, reader):
        self.reader = reader
        self.snapshot = {}
        # (instrumentation scope name, sdk Metric) pairs from the last
        # collect() - the metric conformance helpers read this, because the
        # name-keyed snapshot deliberately flattens the scope away.
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
    """
    Function-scoped metrics collector. Drains delta state accumulated by
    earlier tests before yielding, so counts start from zero.
    """
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
    Every finished span (optionally: only those from `scope_name`, which is
    what a plugin should pass - its own tracer's name) resolves to an entry
    in `registry_spans`, sets only registered attributes, and respects any
    declared `values=` enums. This is the emitted-but-unregistered direction:
    instrumentation added without documentation fails here.
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


def assert_registry_covered(registry_spans, finished_spans, scope_name=None):
    """
    Every entry in `registry_spans` was emitted at least once, and every one
    of its registered non-`optional` attributes appeared on it at least
    once. This is the registered-but-never-emitted direction - documentation
    describing a signal that no longer exists, which is worse than omitting
    it because a reader will build a dashboard on it. Run it against a
    workload broad enough to exercise everything the registry claims;
    `optional=True` attributes are exempt so a workload is not forced to
    manufacture every error path (pin those with targeted tests instead).
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
# A registry kind outside this table (a plugin's own vocabulary) is not
# kind-checked. "Counter" maps to Sum; monotonicity is not asserted, so
# UpDownCounters registered as "Counter" pass too.
_KIND_TO_DATA_TYPE = {
    "Counter": "Sum",
    "Histogram": "Histogram",
    "Observable gauge": "Gauge",
}


def _scoped_metrics(collector, scope_name):
    for scope, metric in collector.collected:
        if scope_name is None or scope == scope_name:
            yield metric


def assert_metrics_conform(registry_metrics, collector, scope_name=None):
    """
    Every metric in the collector's last `collect()` (optionally: only those
    from `scope_name`, which is what a plugin should pass - its own meter's
    name) is registered in `registry_metrics`, was created as the instrument
    kind and unit the registry declares, sets only registered attributes,
    and respects any declared `values=` enums.

    The kind and unit checks catch a drift nothing else does: the registry
    entry and the `meter.create_*()` call are separate statements, and a
    dashboard built on the registry's word breaks silently if they disagree.
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
    Every entry in `registry_metrics` was collected at least once, and every
    registered non-`optional` attribute appeared on it at least once - the
    registered-but-never-emitted direction for metrics.

    Run one broad workload, then a single `collect()`, then this: the reader
    uses delta temporality, so measurements drained by an earlier collect()
    are gone. `optional=True` attributes (e.g. an `error.type` only present
    on failures) are exempt, same as the span-side helper.
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


def assert_package_never_imports_sdk(*module_names):
    """
    Import the named modules in a fresh interpreter and assert none of them
    dragged in `opentelemetry.sdk`. Checked via sys.modules in a subprocess
    rather than by grepping, so a lazy `import opentelemetry.sdk` inside a
    function body cannot slip past. A plugin should depend on
    `opentelemetry-api` only, exactly as Datasette core does.
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
