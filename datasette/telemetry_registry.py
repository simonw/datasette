"""
Every span, metric and attribute that Datasette emits.

These entries are used by the instrumentation code, by `docs/telemetry_doc.py`
to generate the documentation, and by `tests/test_telemetry_registry.py` to
check that the emitted telemetry matches the registry.
"""

from opentelemetry.trace import SpanKind


class Attribute(str):
    """
    A span attribute key, carrying its own documentation.

    Subclasses `str` so it can be handed straight to `set_attribute()`.

    Part of Datasette's public plugin API - plugins declare their own
    telemetry registries with these classes. See the "Telemetry for plugin
    authors" documentation.
    """

    __slots__ = ("description", "optional", "values")

    def __new__(cls, name, description, optional=False, values=None):
        self = super().__new__(cls, name)
        self.description = description
        self.optional = optional
        # The allowed values for this attribute, or None to allow any value
        self.values = frozenset(values) if values is not None else None
        return self

    def __reduce__(self):
        # Copies and pickles become a plain str, since __new__ requires the
        # extra arguments. ConsoleMetricExporter deepcopies attribute keys.
        return (str, (str(self),))

    def __repr__(self):
        return f"Attribute({str(self)!r})"


class SpanName(str):
    """A span name, carrying its documentation and the attributes it may set.

    Part of Datasette's public plugin API, like `Attribute`.
    """

    __slots__ = ("attributes", "description", "dynamic", "kind", "prefix")

    def __new__(
        cls,
        name,
        description,
        attributes=(),
        prefix=False,
        dynamic=False,
        kind=SpanKind.INTERNAL,
    ):
        self = super().__new__(cls, name)
        self.description = description
        self.attributes = tuple(attributes)
        # Match emitted names that start with this prefix, for names with a
        # variable suffix such as SpanName("chat ", ..., prefix=True)
        self.prefix = prefix
        # The emitted name is built at runtime, so `span_for()` matches it by
        # span kind. The entry's string is a template for the documentation.
        self.dynamic = dynamic
        self.kind = kind
        return self

    def __reduce__(self):
        # See Attribute.__reduce__.
        return (str, (str(self),))

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
        # Explicit bucket boundaries, for histograms only
        self.buckets = tuple(buckets) if buckets is not None else None
        return self

    def __reduce__(self):
        # See Attribute.__reduce__.
        return (str, (str(self),))

    def __repr__(self):
        return f"MetricName({str(self)!r})"


COUNTER = "Counter"
UPDOWN_COUNTER = "UpDownCounter"
HISTOGRAM = "Histogram"
GAUGE = "Observable gauge"


# --- Attributes -----------------------------------------------------------

HTTP_REQUEST_METHOD = Attribute(
    "http.request.method",
    "The HTTP request method. Methods outside the nine defined by RFC 9110 "
    "and RFC 5789 are recorded as ``_OTHER``.",
)
HTTP_RESPONSE_STATUS_CODE = Attribute(
    "http.response.status_code",
    "The HTTP response status code. Omitted if no response was started.",
    optional=True,
)
HTTP_ROUTE = Attribute(
    "http.route",
    "The regular expression for the matched route, for example "
    "``/(?P<database>[^\\/\\.]+)/(?P<table>[^\\/\\.]+)(\\.(?P<format>\\w+))?$`` "
    "for a table page. Use this attribute to group requests by route. "
    "Omitted when no route matches.",
    optional=True,
)
URL_PATH = Attribute(
    "url.path",
    "The URL path, excluding the query string.",
)
URL_SCHEME = Attribute("url.scheme", "``http`` or ``https``.")
SERVER_ADDRESS = Attribute(
    "server.address",
    "The ``Host`` header, including any ``:port`` suffix. This value is "
    "supplied by the client.",
    optional=True,
)
USER_AGENT_ORIGINAL = Attribute(
    "user_agent.original",
    "The ``User-Agent`` header, verbatim. Omitted if the client sent none.",
    optional=True,
)
INTERNAL_CLIENT = Attribute(
    "datasette.internal_client",
    "``True`` for requests made through ``datasette.client``. Calls made "
    "inside another request produce a nested ``SERVER`` span. Filter on "
    "this attribute to exclude internal requests from request counts. "
    "Omitted for requests received over the network.",
    optional=True,
)
ERROR_TYPE = Attribute(
    "error.type",
    "The exception class name for a failed operation. On HTTP spans, also "
    "set to the status code as a string for 5xx responses. A 4xx response "
    "alone does not set this attribute or an error status.",
    optional=True,
)

DB_SYSTEM = Attribute("db.system", "Always ``sqlite``.")
DB_NAMESPACE = Attribute("db.namespace", "Name of the database being queried.")
OPERATION = Attribute(
    "datasette.operation",
    "Whether the operation was a read or a write.",
    values={"read", "write"},
)
DB_QUERY_TEXT = Attribute(
    "db.query.text",
    "The SQL, truncated to 2048 characters. Bound parameter values are not "
    "recorded. For callback methods, ``datasette.callback`` is recorded instead.",
    optional=True,
)
CALLBACK = Attribute(
    "datasette.callback",
    "The qualified name of the Python callable passed to ``execute_fn()``, "
    "``execute_write_fn()`` or ``execute_isolated_fn()``, for example "
    "``TableInsertView.post.<locals>.insert_or_upsert_rows``. Set instead of "
    "``db.query.text``. Lambdas appear as ``<lambda>``; use a named function "
    "for a more descriptive span.",
    optional=True,
)
DB_OPERATION_NAME = Attribute(
    "db.operation.name",
    "The statement's leading keyword, such as ``SELECT``, ``INSERT`` or "
    "``CREATE``, if it matches the supported allowlist. Statements beginning "
    "with a common table expression report ``WITH``. Omitted for unrecognized "
    "keywords and ``execute_write_script()``.",
    optional=True,
)
PARAM_COUNT = Attribute(
    "datasette.param_count",
    "Number of bound parameters. Recorded instead of the values themselves.",
    optional=True,
)
PARAM_SETS = Attribute(
    "datasette.param_sets",
    "Number of parameter sets consumed by ``execute_write_many()``. "
    "The parameter values are not recorded.",
    optional=True,
)
TIME_LIMIT_MS = Attribute(
    "datasette.time_limit_ms",
    "Time limit applied to the read query, in milliseconds: "
    ":ref:`setting_sql_time_limit_ms` or a shorter ``custom_time_limit``.",
    optional=True,
)
ROWS_RETURNED = Attribute(
    "datasette.rows_returned",
    "Number of rows returned by a successful read query.",
    optional=True,
)
TRUNCATED = Attribute(
    "datasette.truncated",
    "True if the result was cut short by :ref:`setting_max_returned_rows`.",
    optional=True,
)
INTERRUPTED = Attribute(
    "datasette.interrupted",
    "True if the query exceeded its time limit. The span status is set to "
    "``ERROR`` unless the caller used a ``custom_time_limit`` shorter than "
    ":ref:`setting_sql_time_limit_ms`, in which case the status is left unset.",
    optional=True,
)
SQL_ERROR_SUPPRESSED = Attribute(
    "datasette.sql_error_suppressed",
    "True for a non-timeout SQL error with ``log_sql_errors=False``. The "
    "exception is still raised, but the span status is left unset.",
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
    "One span per HTTP request, containing spans from plugin middleware and "
    "database operations. Named for the HTTP method and matched route, or "
    "just the method if no route matches. Incoming ``traceparent`` headers "
    "are extracted using the global propagator to continue the caller's "
    "trace. Incoming ``baggage`` is not propagated into plugin or downstream "
    "context in this release. Set ``OTEL_PROPAGATORS=none`` to disable "
    "extraction. For public instances, strip trace context headers at your "
    "proxy if callers should not supply trace context.",
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
    "A SQL operation, including time spent queued for a worker thread. For "
    "``block=False`` writes, the span ends after the write is queued. "
    "Callback methods record ``datasette.callback`` in place of ``db.query.text``.",
    (
        DB_SYSTEM,
        DB_NAMESPACE,
        DB_QUERY_TEXT,
        CALLBACK,
        DB_OPERATION_NAME,
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
    "Time a write spent waiting in its database's write queue. For "
    "``block=True``, this is a child of ``db.query``. For ``block=False``, "
    "it is a root span linked to the span that queued the write, since the "
    "write can outlive that request.",
)

DB_WRITE_EXECUTE = SpanName(
    "db.write.execute",
    "The write executing on the write thread. For ``block=True``, this is "
    "a child of ``db.query``. For ``block=False``, it is a root span linked "
    "to the span that queued the write.",
    (ISOLATED_CONNECTION, TRANSACTION),
)

STARTUP = SpanName(
    "datasette.startup",
    "Startup work performed by ``invoke_startup()``, including registration "
    "hooks, schema catalog updates, saved queries, column type configuration "
    "and the ``startup`` hook. Runs during instance startup, either before "
    "serving requests or as part of the first request.",
)

SPANS = (
    HTTP_REQUEST,
    DB_QUERY,
    DB_QUERY_EXECUTE,
    DB_WRITE_QUEUE_WAIT,
    DB_WRITE_EXECUTE,
    STARTUP,
)


def span_for(emitted_name, kind=None, spans=None):
    """
    Resolve an emitted span name to its registry entry, or None.

    Exact matches take precedence over `prefix=True` entries, which take
    precedence over `dynamic=True` entries matched by `kind`.

    `spans` defaults to Datasette's own registry.
    """
    if spans is None:
        spans = SPANS
    for span in spans:
        if span.dynamic:
            continue
        if emitted_name == span:
            return span
    for span in spans:
        if span.prefix and emitted_name.startswith(span):
            return span
    if kind is not None:
        for span in spans:
            if span.dynamic and span.kind == kind:
                return span
    return None


def metric_for(emitted_name, metrics=None):
    """
    Resolve an emitted metric name to its registry entry, or None.

    `metrics` defaults to Datasette's own registry.
    """
    if metrics is None:
        metrics = METRICS
    for metric in metrics:
        if emitted_name == metric:
            return metric
    return None


def attribute_allowed(entry, emitted_key):
    """
    Whether `emitted_key` is a registered attribute of `entry`.

    `entry` is a `SpanName` or a `MetricName` - both carry `.attributes`.
    """
    if entry is None:
        return False
    return emitted_key in entry.attributes


def attribute_value_allowed(entry, emitted_key, value):
    """
    Whether `value` is permitted for `emitted_key` on `entry` (a `SpanName`
    or a `MetricName`).

    Any value is allowed if the attribute does not declare `values=`.
    """
    if entry is None:
        return False
    for attribute in entry.attributes:
        if attribute == emitted_key:
            return attribute.values is None or value in attribute.values
    return False


# --- Metrics --------------------------------------------------------------

# Bucket boundaries in seconds for every duration histogram. OpenTelemetry's
# defaults are designed for milliseconds and would put almost every SQLite
# query in the first bucket. These are the semantic conventions' recommended
# boundaries for db.client.operation.duration, plus 0.0001 and 0.0005 for
# fast in-process SQLite queries.
DURATION_BUCKETS = (0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10)

M_OPERATION_DURATION = MetricName(
    "db.client.operation.duration",
    HISTOGRAM,
    "s",
    "Duration of a SQL operation, including callback-based calls such as "
    "``execute_fn()``. For ``block=False`` writes, measures enqueue time.",
    (DB_SYSTEM, DB_NAMESPACE, OPERATION, ERROR_TYPE),
    buckets=DURATION_BUCKETS,
)

M_WRITE_QUEUE_WAIT = MetricName(
    "datasette.write.queue_wait",
    HISTOGRAM,
    "s",
    "Time each write waited in its database's write queue.",
    (DB_NAMESPACE,),
    buckets=DURATION_BUCKETS,
)

M_QUERIES_INTERRUPTED = MetricName(
    "datasette.sql.queries.interrupted",
    COUNTER,
    "{query}",
    "Queries cancelled for exceeding :ref:`setting_sql_time_limit_ms`. A "
    "rising rate can indicate that queries need optimization or a higher "
    "time limit. Caller-selected timeouts shorter than this limit, such as "
    "those used for facet suggestion, are excluded.",
    (DB_NAMESPACE,),
)

M_THREADS_LIMIT = MetricName(
    "datasette.sql.threads.limit",
    GAUGE,
    "{thread}",
    "Maximum concurrent read queries, configured by "
    ":ref:`setting_num_sql_threads`. Not reported when ``num_sql_threads`` "
    "is ``0``.",
)

M_THREADS_QUEUE_DEPTH = MetricName(
    "datasette.sql.threads.queue_depth",
    GAUGE,
    "{query}",
    "Read queries waiting for a free SQL thread. Sustained values above "
    "zero indicate a saturated read pool.",
)

M_QUERIES_PENDING = MetricName(
    "datasette.sql.queries.pending",
    GAUGE,
    "{query}",
    "Read queries submitted to the pool and not yet complete. Sum across "
    "databases and compare with ``datasette.sql.threads.limit`` to assess "
    "pool usage.",
    (DB_NAMESPACE,),
)

M_WRITE_QUEUE_DEPTH = MetricName(
    "datasette.write.queue_depth",
    GAUGE,
    "{write}",
    "Writes waiting for a database's single write thread. Increasing "
    "``num_sql_threads`` does not increase write concurrency. Not reported for "
    "databases that have never been written to.",
    (DB_NAMESPACE,),
)

M_CONNECTIONS_OPEN = MetricName(
    "datasette.connections.open",
    GAUGE,
    "{connection}",
    "Open SQLite connections managed by Datasette.",
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
