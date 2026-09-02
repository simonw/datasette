"""
OpenTelemetry integration for Datasette core.

Core depends on `opentelemetry-api` only. It never creates a
`TracerProvider`, never configures an exporter, and never touches
sampling - that is the responsibility of whoever is running Datasette
(an `opentelemetry-instrument` agent, a future plugin, or a test
harness).

With no provider installed every span produced here is a
`NonRecordingSpan`. That is not free - a table page emits ~100 spans -
but end-to-end page benchmarks put the overhead below their own
run-to-run variation. Installing an SDK provider is what costs
something measurable.
"""

import re

from opentelemetry import trace as otel_trace

from .version import __version__

# The semantic-convention version whose spellings this instrumentation
# actually emits. Deliberately NOT the latest release.
#
# A schema URL is a machine-readable claim: a consumer doing schema
# translation replays the renames between the declared version and the one
# it wants, so the claim has to name the version whose spellings are on the
# wire. A wrong one makes translation wrong rather than merely uninformative.
#
# Datasette emits `db.system`, which was renamed to `db.system.name` in
# semconv 1.30.0. Everything else it emits (`db.namespace`, `db.query.text`,
# `db.operation.name`, `db.collection.name`) has been current since 1.26.0.
# So 1.29.0 is the highest version at which every name emitted here is the
# current spelling. Everything under `datasette.*` is Datasette's own and
# outside semconv, so it is unaffected either way.
#
# Declaring 1.43.0 would be false about `db.system`, and would actively STOP
# a consumer translating it forward, because it asserts the rename already
# happened. Bump this deliberately, in the same commit as the attribute
# renames it implies - it is a claim about the names, not decoration.
SCHEMA_URL = "https://opentelemetry.io/schemas/1.29.0"

tracer = otel_trace.get_tracer("datasette", __version__, schema_url=SCHEMA_URL)

MAX_SQL_LENGTH = 2048


def sql_attribute(sql: str) -> str:
    "Truncate SQL text so it is safe to attach to a span as an attribute."
    sql = sql.strip()
    if len(sql) <= MAX_SQL_LENGTH:
        return sql
    return sql[:MAX_SQL_LENGTH] + "…[truncated]"


def callback_name(fn) -> str:
    """
    The name recorded as `datasette.callback` for a callback-style call.

    `functools.partial` objects (and other callables) have no `__qualname__`,
    so fall back to the type's name rather than fail the query over telemetry.
    """
    return getattr(fn, "__qualname__", type(fn).__name__)


# db.operation.name is the leading keyword of a statement matched against a
# fixed allowlist - deliberately not a parse.
#
# This runs against arbitrary user-supplied SQL (the `?sql=` query string,
# canned queries, anything typed into the query editor), and the attribute is
# a candidate dimension on a query-duration metric in a later phase. A metric
# series is keyed by its attribute values, so echoing back an arbitrary first
# token would let one visitor's typo mint a new, permanent series. The
# allowlist bounds that at a fixed, small set regardless of what anyone sends.
DB_OPERATION_ALLOWLIST = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "DROP",
        "ALTER",
        "PRAGMA",
        "EXPLAIN",
        "REPLACE",
        "VACUUM",
        "ANALYZE",
        "WITH",
    }
)

_LEADING_KEYWORD = re.compile(r"^\s*([A-Za-z]+)")


def sql_operation_name(sql: str) -> str | None:
    """
    The statement's leading keyword, if it is one we recognise.

    Returns None - never a guess - for anything not on the allowlist,
    including a statement that opens with a comment or with punctuation such
    as the "(" of a parenthesised SELECT.

    Known limitation: a statement beginning with a CTE reports `WITH` rather
    than the operation inside it, and a substantial share of Datasette's own
    reads take that form. Extracting more than the leading keyword means
    handling comment stripping, parenthesised `(SELECT ...) UNION` and
    compound names like `CREATE TABLE` - each a special case a hand-rolled
    matcher would accrete and eventually get wrong. Omitting a name beats
    guessing at one.

    Only safe to call with a single statement: `execute_write_script()` runs
    several separated by semicolons, and semantic conventions say
    `db.operation.name` "SHOULD NOT be extracted from db.query.text, when the
    database system supports query text with multiple operations in non-batch
    operations" - so that call site does not use this at all rather than
    reporting only the first statement's operation.
    """
    match = _LEADING_KEYWORD.match(sql)
    if not match:
        return None
    keyword = match.group(1).upper()
    if keyword in DB_OPERATION_ALLOWLIST:
        return keyword
    return None
