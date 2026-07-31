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

    __slots__ = ("attributes", "description", "dynamic", "kind")

    def __new__(
        cls,
        name,
        description,
        attributes=(),
        dynamic=False,
        kind=SpanKind.INTERNAL,
    ):
        self = super().__new__(cls, name)
        self.description = description
        self.attributes = tuple(attributes)
        # True when the emitted name is composed at runtime and shares no
        # fixed prefix with the registry entry - the HTTP request span, whose
        # name is the request method. There is no substring of the entry that
        # could be matched against the wire, so `span_for()` resolves these by
        # span kind instead, and the entry's own string is a template written
        # for a human reading the generated reference.
        self.dynamic = dynamic
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

HTTP_REQUEST_METHOD = Attribute(
    "http.request.method",
    "The HTTP method, clamped to the nine methods RFC 9110 and RFC 5789 "
    "define. Anything else is reported as ``_OTHER``: the method is a "
    "client-controlled string, so echoing it back unbounded would be a "
    "cardinality hazard.",
)
HTTP_RESPONSE_STATUS_CODE = Attribute(
    "http.response.status_code",
    "The status of the response, read from the ASGI ``http.response.start`` "
    "message rather than from a :ref:`internals_response` object - several "
    "views, including static files, file downloads and streaming CSV, send "
    "that message themselves and never build one. Omitted if the connection "
    "closed before anything was sent.",
    optional=True,
)
URL_PATH = Attribute(
    "url.path",
    "The path portion of the URL. The query string is deliberately **not** "
    "recorded, on this or any other span: Datasette puts user-supplied SQL in "
    "``?sql=`` and canned query parameters in the query string, so exporting "
    "it by default would export exactly the data the rest of this "
    "instrumentation is careful with.",
)
URL_SCHEME = Attribute("url.scheme", "``http`` or ``https``.")
SERVER_ADDRESS = Attribute(
    "server.address",
    "The ``Host`` header. Client-controlled, so treat it as untrusted input "
    "rather than as the identity of the server.",
    optional=True,
)
USER_AGENT_ORIGINAL = Attribute(
    "user_agent.original",
    "The ``User-Agent`` header, verbatim. Omitted if the client sent none. "
    "The client's IP address is deliberately not recorded: core records no "
    "identifier that would tie a span to a person.",
    optional=True,
)
ERROR_TYPE = Attribute(
    "error.type",
    "Set when the request failed: the exception class name if one escaped the "
    "application, otherwise the status code as a string for a 5xx response. "
    "A 4xx does **not** set this and does not set an error status - per "
    "semantic conventions a client error is not a server span's failure.",
    optional=True,
)

DB_SYSTEM = Attribute("db.system", "Always ``sqlite``.")
DB_NAMESPACE = Attribute("db.namespace", "Name of the database being queried.")
DB_QUERY_TEXT = Attribute(
    "db.query.text",
    "The SQL, truncated to 2048 characters. Never the parameter values. "
    "Absent for a callback-style call (``execute_fn()`` and friends), where "
    "there is no SQL string to record - ``datasette.callback`` is set "
    "instead.",
    optional=True,
)
CALLBACK = Attribute(
    "datasette.callback",
    "The qualified name of the Python callable passed to ``execute_fn()``, "
    "``execute_write_fn()`` or ``execute_isolated_fn()`` - for example "
    "``TableInsertView.post.<locals>.insert_or_upsert_rows``. Set instead of "
    "``db.query.text``, which does not exist for a callback: the SQL is "
    "whatever the function chooses to run. A lambda reports ``<lambda>``, "
    "which is why callers wanting a recognisable span should pass a named "
    "function. Bounded cardinality: the set of callables is fixed by the "
    "installed code, not by request input.",
    optional=True,
)
DB_OPERATION_NAME = Attribute(
    "db.operation.name",
    "The statement's leading keyword - ``SELECT``, ``INSERT``, ``CREATE``, and "
    "so on - matched against a small fixed allowlist. Omitted rather than set "
    "to an arbitrary value: the attribute must stay safe to use as a metric "
    "dimension, and echoing an unrecognised first token from user-supplied "
    "SQL would be an unbounded-cardinality hazard. Also omitted for "
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
    "status is also set to ``ERROR``, unless the caller asked for a budget "
    "shorter than :ref:`setting_sql_time_limit_ms` - as table counts, facet "
    "suggestion and autocomplete all do - in which case running out of time "
    "is an expected answer rather than a failure and the status is left "
    "unset.",
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

HTTP_REQUEST = SpanName(
    "{http.request.method}",
    "One span per HTTP request, created by the outermost layer of the ASGI "
    "stack - so plugin ``asgi_wrapper()`` middleware, CSRF protection and "
    "every database span raised while serving the request all nest inside "
    "it. Without it each of those would be its own root trace. The span name "
    "is not a fixed string: it is the value of ``http.request.method``. "
    "W3C ``traceparent`` and ``baggage`` headers are extracted using the "
    "global propagator, so a request arriving from an already-traced caller "
    "continues that trace; set ``OTEL_PROPAGATORS=none`` to turn that off, "
    "and strip those headers at your proxy if your instance is public.",
    (
        HTTP_REQUEST_METHOD,
        URL_PATH,
        URL_SCHEME,
        SERVER_ADDRESS,
        USER_AGENT_ORIGINAL,
        HTTP_RESPONSE_STATUS_CODE,
        ERROR_TYPE,
    ),
    dynamic=True,
    kind=SpanKind.SERVER,
)

DB_QUERY = SpanName(
    "db.query",
    "A SQL operation issued by Datasette, covering the full round trip "
    "including any time spent queued for a thread. Callback-style calls - "
    "``execute_fn()``, ``execute_write_fn()`` and ``execute_isolated_fn()`` - "
    "appear here too, distinguished by ``datasette.callback`` in place of "
    "``db.query.text``.",
    (
        DB_SYSTEM,
        DB_NAMESPACE,
        DB_QUERY_TEXT,
        CALLBACK,
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
    HTTP_REQUEST,
    DB_QUERY,
    DB_QUERY_EXECUTE,
    DB_WRITE_QUEUE_WAIT,
    DB_WRITE_EXECUTE,
    STARTUP,
)


def span_for(emitted_name, kind=None):
    """
    Resolve an emitted span name to its registry entry, or None.

    Handles `dynamic=True` entries, whose emitted names are not knowable in
    advance: the name has no fixed part at all, so it is matched on `kind`
    instead and the caller has to supply one. Exact entries are tried first,
    so a dynamic entry can never shadow a span that does have a registered
    name.
    """
    for span in SPANS:
        if span.dynamic:
            continue
        if emitted_name == span:
            return span
    if kind is not None:
        for span in SPANS:
            if span.dynamic and span.kind == kind:
                return span
    return None


def attribute_allowed(span, emitted_key):
    "Whether `emitted_key` is a registered attribute of `span`."
    if span is None:
        return False
    return emitted_key in span.attributes
