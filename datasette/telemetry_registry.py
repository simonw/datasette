"""
The single source of truth for every span and span attribute that Datasette
core emits.

Three things read this module, which is the point of it existing:

1. **The instrumentation itself.** `Attribute` and `SpanName` subclass `str`,
   so a registry entry *is* the string OpenTelemetry wants. Call sites pass
   `DB_NAMESPACE` where they used to pass `"db.namespace"` - no wrapper API
   over the OTel calls, no parallel structure to keep in step, and a typo is
   now an `ImportError` instead of a silently misnamed attribute.

2. **The documentation.** `docs/telemetry_doc.py` renders the span reference
   in `docs/internals.rst` from these definitions using cog, and
   `cog --check` runs in CI - so the docs cannot drift from the code.

3. **A conformance test.** `tests/test_telemetry_registry.py` makes real
   requests, collects every span and attribute actually emitted, and compares
   both directions: emitted-but-unregistered catches instrumentation added
   without documentation, registered-but-never-emitted catches documentation
   describing something that no longer exists. Neither the type system nor
   the generated docs can catch that second case.
"""

from opentelemetry.trace import SpanKind


class Attribute(str):
    """
    A span attribute key, carrying its own documentation.

    Subclasses `str` so it can be handed straight to `set_attribute()`.
    """

    __slots__ = ("description", "optional")

    def __new__(cls, name, description, optional=False):
        self = super().__new__(cls, name)
        self.description = description
        self.optional = optional
        return self

    def __repr__(self):
        return f"Attribute({str(self)!r})"


class SpanName(str):
    "A span name, carrying its documentation and the attributes it may set."

    __slots__ = ("attributes", "description", "kind", "prefix")

    def __new__(
        cls, name, description, attributes=(), prefix=False, kind=SpanKind.INTERNAL
    ):
        self = super().__new__(cls, name)
        self.description = description
        self.attributes = tuple(attributes)
        # True for a span family whose emitted names carry a variable suffix,
        # so the conformance test matches by prefix rather than equality.
        # Nothing sets it yet.
        self.prefix = prefix
        # SpanKind.INTERNAL by default - every span Datasette emits describes
        # its own internal work. db.query is the one exception: it is a real
        # database call, so semantic conventions (and trace UIs, which key
        # their database styling off this) expect SpanKind.CLIENT.
        self.kind = kind
        return self

    def __repr__(self):
        return f"SpanName({str(self)!r})"


# --- Attributes -----------------------------------------------------------
#
# Shared attributes are defined once and referenced by every span that sets
# them, so "which spans carry db.namespace?" is answerable by grep.

DB_SYSTEM = Attribute("db.system", "Always ``sqlite``.")
DB_NAMESPACE = Attribute("db.namespace", "Name of the database being queried.")
DB_QUERY_TEXT = Attribute(
    "db.query.text",
    "The SQL, truncated to 2048 characters. Never the parameter values.",
)
DB_OPERATION_NAME = Attribute(
    "db.operation.name",
    "The statement's leading keyword - ``SELECT``, ``INSERT``, ``CREATE``, and "
    "so on - matched against a small fixed allowlist. Omitted rather than set "
    "to an arbitrary value: the allowlist exists because this attribute is a "
    "candidate dimension for a query-duration metric in a later phase, and "
    "echoing an unrecognised first token from user-supplied SQL would be an "
    "unbounded-cardinality hazard. Also omitted for "
    "``execute_write_script()``, which runs multiple statements - per "
    "semantic conventions, the operation name should not be extracted from "
    "query text that can contain more than one operation. Note that a "
    "statement beginning with a CTE reports ``WITH``, not the operation "
    "inside it - a substantial share of Datasette's own reads take that "
    "form. Resolving it further would mean parsing.",
    optional=True,
)
DB_COLLECTION_NAME = Attribute(
    "db.collection.name",
    "The primary table, set only where the view already knows it - the table "
    "and row pages. Omitted for arbitrary ``?sql=`` queries, where determining "
    "the table would mean parsing the query.",
    optional=True,
)

PARAM_COUNT = Attribute(
    "datasette.param_count",
    "Number of bound parameters. Recorded instead of the values themselves.",
    optional=True,
)
PARAM_SETS = Attribute(
    "datasette.param_sets",
    "Number of parameter sets consumed by ``execute_write_many()``. Not a row "
    "count - ``executemany()`` returns no rows. The parameter values "
    "themselves are never recorded: that sequence can hold thousands of rows.",
    optional=True,
)
TIME_LIMIT_MS = Attribute(
    "datasette.time_limit_ms",
    "The :ref:`setting_sql_time_limit_ms` value this query ran under. Set on "
    "reads, which are the queries that time limit applies to.",
    optional=True,
)
ROWS_RETURNED = Attribute(
    "datasette.rows_returned",
    "Number of rows a read returned. Set on the read path only, and only when "
    "the read succeeded.",
    optional=True,
)
TRUNCATED = Attribute(
    "datasette.truncated",
    "True if the result was cut short by :ref:`setting_max_returned_rows`.",
    optional=True,
)
INTERRUPTED = Attribute(
    "datasette.interrupted",
    "True if the query was cancelled for exceeding the time limit. The span "
    "status is also set to ``ERROR``.",
    optional=True,
)
SQL_ERROR_SUPPRESSED = Attribute(
    "datasette.sql_error_suppressed",
    "True when the query failed but the caller passed ``log_sql_errors=False``, "
    "meaning it was probing and treats failure as an expected answer. Facet "
    "suggestion does this against every column.",
    optional=True,
)
EXECUTESCRIPT = Attribute(
    "datasette.executescript",
    "True for ``execute_write_script()``, which runs multiple statements.",
    optional=True,
)
EXECUTEMANY = Attribute(
    "datasette.executemany",
    "True for ``execute_write_many()``, which runs one statement against many "
    "parameter sets.",
    optional=True,
)
ISOLATED_CONNECTION = Attribute(
    "datasette.isolated_connection",
    "True if the write ran on its own connection rather than the shared write "
    "connection.",
)
TRANSACTION = Attribute(
    "datasette.transaction",
    "False for statements such as ``VACUUM`` that cannot run inside a transaction.",
)


# --- Spans ----------------------------------------------------------------

DB_QUERY = SpanName(
    "db.query",
    "A SQL operation issued by Datasette, covering the full round trip "
    "including any time spent queued for a thread.",
    (
        DB_SYSTEM,
        DB_NAMESPACE,
        DB_QUERY_TEXT,
        DB_OPERATION_NAME,
        DB_COLLECTION_NAME,
        PARAM_COUNT,
        PARAM_SETS,
        TIME_LIMIT_MS,
        ROWS_RETURNED,
        TRUNCATED,
        INTERRUPTED,
        SQL_ERROR_SUPPRESSED,
        EXECUTESCRIPT,
        EXECUTEMANY,
    ),
    kind=SpanKind.CLIENT,
)

DB_QUERY_EXECUTE = SpanName(
    "db.query.execute",
    "The read executing inside a SQL worker thread. Child of ``db.query``; the "
    "gap between the two is time spent waiting for a thread.",
)

DB_WRITE_QUEUE_WAIT = SpanName(
    "db.write.queue_wait",
    "Time a write spent waiting in its database's write queue before the write "
    "thread picked it up. Child of ``db.query`` for a ``block=True`` write, "
    "where the caller awaits the write and containment is accurate. For a "
    "``block=False`` write the caller does not await it - the enqueueing "
    "request *caused* the write without *containing* it, and the write's "
    "spans can outlive the request's own - so this is a root span instead, "
    "carrying an OpenTelemetry link back to the enqueueing span rather than "
    "a parent. A link records causation without asserting containment, which "
    "is exactly the distinction here.",
)

DB_WRITE_EXECUTE = SpanName(
    "db.write.execute",
    "The write executing on the write thread. Child of ``db.query`` for a "
    "``block=True`` write; for ``block=False`` a root span with a link back "
    "to the enqueueing span instead - see ``db.write.queue_wait`` above.",
    (ISOLATED_CONNECTION, TRANSACTION),
)

STARTUP = SpanName(
    "datasette.startup",
    "``invoke_startup()`` running: ``register_events``, ``register_actions``, "
    "``register_column_types``, ``prepare_jinja2_environment``, internal-database "
    "schema catalog refresh (including the ``prepare_connection`` warm-up this "
    "triggers for each database touched for the first time), saved queries, "
    "column type config and the ``startup`` hook. Runs once per process, before "
    "any request exists, so without this span every child it creates would be "
    "its own orphan root trace. A connection warmed later - lazily, the first "
    "time a *request* touches a new database or thread - nests under that "
    "request's own span instead, not under this one, since this span has "
    "already ended by then.",
)

SPANS = (
    DB_QUERY,
    DB_QUERY_EXECUTE,
    DB_WRITE_QUEUE_WAIT,
    DB_WRITE_EXECUTE,
    STARTUP,
)


def span_for(emitted_name):
    """
    Resolve an emitted span name to its registry entry, or None.

    Handles span families whose emitted names carry a suffix that is not
    knowable in advance - `prefix=True` entries. Phase 1 has none, but the
    lookup is what the conformance test calls, so it lives here rather than
    in the test.
    """
    for span in SPANS:
        if span.prefix:
            if emitted_name.startswith(span):
                return span
        elif emitted_name == span:
            return span
    return None


def attribute_allowed(span, emitted_key):
    "Whether `emitted_key` is a registered attribute of `span`."
    if span is None:
        return False
    return emitted_key in span.attributes
