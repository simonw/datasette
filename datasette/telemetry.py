"""
OpenTelemetry integration for Datasette core.

Core depends on `opentelemetry-api` only. It never creates a
`TracerProvider`, never configures an exporter, and never touches
sampling - that is the responsibility of whoever is running Datasette
(an `opentelemetry-instrument` agent, a future plugin, or a test
harness). With no provider installed every span produced here is a
`NonRecordingSpan` and costs approximately nothing.
"""

from opentelemetry import trace as otel_trace

tracer = otel_trace.get_tracer("datasette")

MAX_SQL_LENGTH = 2048


def sql_attribute(sql: str) -> str:
    "Truncate SQL text so it is safe to attach to a span as an attribute."
    sql = sql.strip()
    if len(sql) <= MAX_SQL_LENGTH:
        return sql
    return sql[:MAX_SQL_LENGTH] + "…[truncated]"
