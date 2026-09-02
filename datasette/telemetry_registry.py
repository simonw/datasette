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
        # name is the request method followed by the matched route. There is
        # no substring of the entry that could be matched against the wire, so
        # `span_for()` resolves these by span kind instead, and the entry's own
        # string is a template written for a human reading the generated
        # reference.
        self.dynamic = dynamic
        # SpanKind.INTERNAL by default - every span Datasette emits describes
        # its own internal work. db.query is the one exception: it is a real
        # database call, so semantic conventions (and trace UIs, which key
        # their database styling off this) expect SpanKind.CLIENT.
        self.kind = kind
        return self

    def __repr__(self):
        return f"SpanName({str(self)!r})"


class MetricName(str):
    "A metric name, carrying its instrument kind, unit and attributes."

    __slots__ = ("attributes", "buckets", "description", "kind", "unit")

    def __new__(cls, name, kind, unit, description, attributes=(), buckets=None):
        self = super().__new__(cls, name)
        self.kind = kind
        self.unit = unit
        self.description = description
        self.attributes = tuple(attributes)
        # Explicit histogram bucket boundaries, for histograms only. Passed to
        # create_histogram() as explicit_bucket_boundaries_advisory and
        # published in the generated docs, since an operator writing a
        # histogram_quantile() query needs to know them.
        self.buckets = tuple(buckets) if buckets is not None else None
        return self

    def __repr__(self):
        return f"MetricName({str(self)!r})"


COUNTER = "Counter"
HISTOGRAM = "Histogram"
GAUGE = "Observable gauge"


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
HTTP_ROUTE = Attribute(
    "http.route",
    "The route the request matched, as the compiled regular expression "
    "pattern Datasette routes with - for example "
    "``/(?P<database>[^\\/\\.]+)/(?P<table>[^\\/\\.]+)(\\.(?P<format>\\w+))?$`` "
    "for a table page. It is deliberately the pattern rather than a prettified "
    "``/{database}/{table}`` template: the route table is fixed when the app "
    "is built, so the pattern is exact, bounded and needs no parsing, whereas "
    "the transform into something prettier accretes edge cases. Unlike "
    "``url.path`` this is low cardinality, so it is the attribute to group by. "
    "Omitted when no route matched - a 404 - which is also when the span name "
    "falls back to the bare method.",
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
    "The ``Host`` header, verbatim - including any ``:port`` suffix, a "
    "deliberate deviation from semantic conventions' ``server.address`` / "
    "``server.port`` split. Client-controlled, so treat it as untrusted input "
    "rather than as the identity of the server.",
    optional=True,
)
USER_AGENT_ORIGINAL = Attribute(
    "user_agent.original",
    "The ``User-Agent`` header, verbatim. Omitted if the client sent none.",
    optional=True,
)
INTERNAL_CLIENT = Attribute(
    "datasette.internal_client",
    "``True`` when the request was made in-process through "
    "``datasette.client`` rather than arriving over the network. Such a "
    "sub-request runs the full ASGI stack, so it emits its own nested "
    "``SERVER`` span inside the outer request's - filter on this attribute "
    "to keep kind-based dashboards from double-counting requests. Omitted "
    "for real inbound requests.",
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
OPERATION = Attribute("datasette.operation", "``read`` or ``write``.")
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
    "{http.request.method} {http.route}",
    "One span per HTTP request, created by the outermost layer of the ASGI "
    "stack - so plugin ``asgi_wrapper()`` middleware, CSRF protection and "
    "every database span raised while serving the request all nest inside "
    "it. Without it each of those would be its own root trace. The span name "
    "is not a fixed string: it is the method followed by the matched route, "
    "and just the method for a request that matched no route. The span starts "
    "at the ASGI edge, before routing has happened, so it is named for the "
    "method there and renamed once the route is known. "
    "W3C ``traceparent`` and ``baggage`` headers are extracted using the "
    "global propagator, so a request arriving from an already-traced caller "
    "continues that trace; set ``OTEL_PROPAGATORS=none`` to turn that off, "
    "and strip those headers at your proxy if your instance is public.",
    (
        HTTP_REQUEST_METHOD,
        HTTP_ROUTE,
        URL_PATH,
        URL_SCHEME,
        SERVER_ADDRESS,
        USER_AGENT_ORIGINAL,
        HTTP_RESPONSE_STATUS_CODE,
        ERROR_TYPE,
        INTERNAL_CLIENT,
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


# --- Metrics --------------------------------------------------------------

# Every duration histogram here is in seconds, and OpenTelemetry's default
# bucket boundaries are tuned for milliseconds - their first non-zero boundary
# is 5, so without explicit boundaries every SQLite query lands in the single
# (0, 5] second bucket and every quantile query returns noise.
#
# These are the OpenTelemetry semantic conventions' recommended boundaries for
# db.client.operation.duration, in seconds, plus 0.0001 and 0.0005 at the
# bottom. The deviation is deliberate: those boundaries assume a network
# database client, whereas SQLite is in-process and a large fraction of real
# queries run in 30-80us, which would otherwise all pile into the first
# bucket and be indistinguishable from each other.
#
# One shared list is used for every duration histogram rather than a tailored
# list each, so that dashboards stay comparable and a queue wait can be read
# against the query duration it delays. It already spans 100us to 10s, which
# covers both a fast in-process read and a write queued behind contention.
DURATION_BUCKETS = (0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10)

M_OPERATION_DURATION = MetricName(
    "db.client.operation.duration",
    HISTOGRAM,
    "s",
    "Duration of a SQL operation. The standard OpenTelemetry semantic "
    "convention metric, and the one that survives trace sampling. "
    "Callback-style calls (``execute_fn()`` and friends) are counted "
    "alongside the SQL-string methods.",
    (DB_SYSTEM, DB_NAMESPACE, OPERATION, ERROR_TYPE),
    buckets=DURATION_BUCKETS,
)

M_WRITE_QUEUE_WAIT = MetricName(
    "datasette.write.queue_wait",
    HISTOGRAM,
    "s",
    "Time each write waited in its database's write queue. The metric "
    "counterpart of the ``db.write.queue_wait`` span.",
    (DB_NAMESPACE,),
    buckets=DURATION_BUCKETS,
)

M_QUERIES_INTERRUPTED = MetricName(
    "datasette.sql.queries.interrupted",
    COUNTER,
    "{query}",
    "Queries cancelled for exceeding :ref:`setting_sql_time_limit_ms`. Worth "
    "alerting on: a rising rate means the limit is too tight or a table has "
    "outgrown its queries. A caller that opted into a deliberately shorter "
    "budget - facet suggestion, for example - is not counted, for the same "
    "reason its timeout is not a span error.",
    (DB_NAMESPACE,),
)

M_THREADS_LIMIT = MetricName(
    "datasette.sql.threads.limit",
    GAUGE,
    "{thread}",
    "Maximum concurrent read queries - the :ref:`setting_num_sql_threads` "
    "value. Not reported when ``num_sql_threads`` is ``0``, since then queries "
    "run on the event loop and there is no pool.",
)

M_THREADS_QUEUE_DEPTH = MetricName(
    "datasette.sql.threads.queue_depth",
    GAUGE,
    "{query}",
    "Read queries waiting for a free thread. **This is the saturation "
    "signal** - sustained above zero means requests are queueing on "
    "``num_sql_threads``.",
)

M_QUERIES_PENDING = MetricName(
    "datasette.sql.queries.pending",
    GAUGE,
    "{query}",
    "Read queries submitted to the pool and not yet complete. Summed across "
    "databases and compared against the thread limit, this is pool "
    "utilisation.",
    (DB_NAMESPACE,),
)

M_WRITE_QUEUE_DEPTH = MetricName(
    "datasette.write.queue_depth",
    GAUGE,
    "{write}",
    "Writes queued behind a database's single write thread. Backpressure that "
    "raising ``num_sql_threads`` cannot relieve. Not reported for a database "
    "that has never been written to.",
    (DB_NAMESPACE,),
)

M_CONNECTIONS_OPEN = MetricName(
    "datasette.connections.open",
    GAUGE,
    "{connection}",
    "Open SQLite file connections currently tracked for closing.",
    (DB_NAMESPACE,),
)

METRICS = (
    M_OPERATION_DURATION,
    M_WRITE_QUEUE_WAIT,
    M_QUERIES_INTERRUPTED,
    M_THREADS_LIMIT,
    M_THREADS_QUEUE_DEPTH,
    M_QUERIES_PENDING,
    M_WRITE_QUEUE_DEPTH,
    M_CONNECTIONS_OPEN,
)
