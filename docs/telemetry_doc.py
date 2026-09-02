"""
Render the span reference in ``internals.rst`` from
``datasette/telemetry_registry.py``.

Driven by cog, and ``cog --check docs/*.rst`` runs in CI - so adding a span
without documenting it, or documenting one that no longer exists, is a build
failure rather than something a reader discovers later.
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
        # INTERNAL is the default and the overwhelming majority of spans -
        # printing it on every one would be noise. Only the exceptional case,
        # a real database call, is worth calling out.
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
