"""
Cog helpers that render the span and metric reference in ``internals.rst``
from ``datasette/telemetry_registry.py``.
"""


def _attribute_lines(cog, attributes):
    if not attributes:
        cog.out("    No attributes.\n\n")
        return
    cog.out("    Attributes:\n\n")
    for attribute in attributes:
        suffix = " *(optional)*" if attribute.optional else ""
        line = f"    - ``{attribute}``{suffix} - {attribute.description}"
        if attribute.values is not None:
            rendered = ", ".join(f"``{value}``" for value in sorted(attribute.values))
            line += f" One of: {rendered}."
        cog.out(line + "\n")
    cog.out("\n")


def spans(cog):
    from opentelemetry.trace import SpanKind

    from datasette.telemetry_registry import SPANS

    cog.out("\n")
    for span in SPANS:
        cog.out(f"``{span}``\n")
        cog.out(f"    {span.description}\n\n")
        # Only show the kind for spans that are not INTERNAL
        if span.kind != SpanKind.INTERNAL:
            cog.out(f"    Kind: ``{span.kind.name}``.\n\n")
        _attribute_lines(cog, span.attributes)


def metrics(cog):
    from datasette.telemetry_registry import METRICS

    cog.out("\n")
    for metric in METRICS:
        cog.out(f"``{metric}``\n")
        cog.out(f"    {metric.kind}, unit ``{metric.unit}``. {metric.description}\n\n")
        if metric.buckets:
            boundaries = ", ".join(f"``{boundary}``" for boundary in metric.buckets)
            cog.out(f"    Bucket boundaries: {boundaries}.\n\n")
        _attribute_lines(cog, metric.attributes)
