"""
The plugin telemetry kit (`datasette.telemetry_testing` plus the public
registry classes), exercised the way a third-party plugin would use it: a
toy plugin registry, a toy tracer scope, and the kit's own fixtures and
conformance helpers.
"""

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace as otel_trace

from datasette import telemetry_registry as reg
from datasette.telemetry import linked_root_span_kwargs
from datasette.telemetry_testing import (
    assert_package_never_imports_sdk,
    assert_registry_covered,
    assert_spans_conform,
)

SCOPE = "toyplugin"

OUTCOME = reg.Attribute(
    "toyplugin.outcome", "How the job ended.", values={"ok", "error"}
)
JOB_NAME = reg.Attribute("toyplugin.job", "The job's registered name.")
JOB = reg.SpanName("toyplugin.job.run", "One job execution.", (OUTCOME, JOB_NAME))
CHAT = reg.SpanName(
    "toyplugin.chat ", "One model call, named `toyplugin.chat {model}`.", prefix=True
)
TOY_SPANS = (JOB, CHAT)

toy_tracer = otel_trace.get_tracer(SCOPE, "0.1")


def _toy_spans(otel_spans):
    return [
        span
        for span in otel_spans.get_finished_spans()
        if span.instrumentation_scope and span.instrumentation_scope.name == SCOPE
    ]


def _run_workload():
    with toy_tracer.start_as_current_span(JOB) as span:
        span.set_attribute(OUTCOME, "ok")
        span.set_attribute(JOB_NAME, "nightly")
    with toy_tracer.start_as_current_span("toyplugin.chat gpt-5"):
        pass


def test_conformance_passes_for_a_conforming_workload(otel_spans):
    _run_workload()
    finished = otel_spans.get_finished_spans()
    assert_spans_conform(TOY_SPANS, finished, scope_name=SCOPE)
    # Coverage direction needs prefix families seen too - the chat span
    # resolves to the CHAT entry despite its variable suffix.
    assert_registry_covered(TOY_SPANS, finished, scope_name=SCOPE)


def test_conformance_catches_an_unregistered_span(otel_spans):
    with toy_tracer.start_as_current_span("toyplugin.surprise"):
        pass
    with pytest.raises(AssertionError, match="unregistered span"):
        assert_spans_conform(
            TOY_SPANS, otel_spans.get_finished_spans(), scope_name=SCOPE
        )


def test_conformance_catches_an_unregistered_attribute(otel_spans):
    with toy_tracer.start_as_current_span(JOB) as span:
        span.set_attribute("toyplugin.stealth", 1)
    with pytest.raises(AssertionError, match="unregistered attribute"):
        assert_spans_conform(
            TOY_SPANS, otel_spans.get_finished_spans(), scope_name=SCOPE
        )


def test_conformance_enforces_declared_enums(otel_spans):
    with toy_tracer.start_as_current_span(JOB) as span:
        span.set_attribute(OUTCOME, "surprise")
    with pytest.raises(AssertionError, match="not in the declared enum"):
        assert_spans_conform(
            TOY_SPANS, otel_spans.get_finished_spans(), scope_name=SCOPE
        )


def test_coverage_catches_a_never_emitted_span(otel_spans):
    with toy_tracer.start_as_current_span(JOB) as span:
        span.set_attribute(OUTCOME, "ok")
        span.set_attribute(JOB_NAME, "nightly")
    # CHAT never emitted
    with pytest.raises(AssertionError, match="never emitted"):
        assert_registry_covered(
            TOY_SPANS, otel_spans.get_finished_spans(), scope_name=SCOPE
        )


def test_scope_filter_ignores_other_scopes(otel_spans):
    # Core's own spans are in the exporter too; a plugin's conformance run
    # must not fail because of them.
    other = otel_trace.get_tracer("someone-else", "1.0")
    with other.start_as_current_span("not.in.the.toy.registry"):
        pass
    _run_workload()
    assert_spans_conform(TOY_SPANS, otel_spans.get_finished_spans(), scope_name=SCOPE)


def test_linked_root_span_kwargs_links_without_parenting(otel_spans):
    with toy_tracer.start_as_current_span("toyplugin.cause") as cause:
        cause_context = cause.get_span_context()
        kwargs = linked_root_span_kwargs()
    with toy_tracer.start_as_current_span("toyplugin.effect", **kwargs):
        pass
    effect = [
        span for span in _toy_spans(otel_spans) if span.name == "toyplugin.effect"
    ][0]
    assert effect.parent is None, "must be a root, not a child"
    assert effect.context.trace_id != cause_context.trace_id
    assert len(effect.links) == 1
    assert effect.links[0].context.span_id == cause_context.span_id


def test_linked_root_span_kwargs_with_no_current_span(otel_spans):
    kwargs = linked_root_span_kwargs()
    assert kwargs["links"] == []
    with toy_tracer.start_as_current_span("toyplugin.orphanless", **kwargs):
        pass
    span = _toy_spans(otel_spans)[0]
    assert span.parent is None
    assert span.links == ()


def test_kit_module_itself_never_imports_the_sdk():
    # The kit imports the SDK lazily, so a plugin importing it at module
    # level does not violate the api-only dependency rule.
    assert_package_never_imports_sdk("datasette.telemetry_testing")
