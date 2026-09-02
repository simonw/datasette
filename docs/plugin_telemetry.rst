.. _plugin_telemetry:

Telemetry for plugin authors
============================

Datasette core emits OpenTelemetry spans and metrics for the work it does itself - see :ref:`internals_telemetry` for what those are and how an operator turns them on. This page is about the other half: instrumenting the work **your plugin** does, so that a plugin's queries, background jobs and custom operations show up in the same traces and the same metrics pipeline, using the same conventions.

Everything here follows one rule inherited from core: **depend on** ``opentelemetry-api`` **only, and never install a provider**. With no SDK installed every span and instrument your plugin creates is a free no-op; whoever runs Datasette decides whether telemetry is collected, sampled or exported. A plugin that installs a ``TracerProvider`` or configures an exporter is making an operator's decision for them.

.. _plugin_telemetry_scope:

Use your own instrumentation scope
----------------------------------

Never emit through core's tracer or meter. Your plugin's scope name is the machine-readable claim about *who emitted a signal*, and consumers filter on it:

.. code-block:: python

    from opentelemetry import metrics, trace

    from my_plugin import __version__

    tracer = trace.get_tracer("my-plugin", __version__)
    meter = metrics.get_meter("my-plugin", __version__)

If every attribute you emit follows current semantic conventions you can also pass ``schema_url=``; ``datasette.telemetry.SCHEMA_URL`` is the version core's own spellings track, with a comment explaining how to choose one. When in doubt, omit it - a wrong schema URL is worse than none.

Name your own signals under a prefix you own (``my_plugin.*``). Reuse core's shared attribute spellings where they mean the same thing - ``db.namespace`` for a database name, ``error.type`` for a failure class - rather than minting parallel ones.

.. _plugin_telemetry_registry:

Declare a registry
------------------

Core keeps a single source of truth for every signal it emits in ``datasette/telemetry_registry.py``, and the classes it uses are public API. They subclass ``str``, so a registry entry *is* the name you pass to OpenTelemetry - no parallel constants to keep in step:

.. code-block:: python

    from opentelemetry.trace import SpanKind

    from datasette.telemetry_registry import Attribute, MetricName, SpanName

    OUTCOME = Attribute(
        "my_plugin.outcome",
        "How the job ended.",
        values={"ok", "error", "skipped"},
    )
    JOB_NAME = Attribute("my_plugin.job", "The registered job name.")

    JOB_RUN = SpanName(
        "my_plugin.job.run",
        "One execution of a scheduled job.",
        (OUTCOME, JOB_NAME),
    )

    # A span family with a variable suffix - emitted as "my_plugin.chat gpt-5"
    CHAT = SpanName(
        "my_plugin.chat ",
        "One model call, named ``my_plugin.chat {model}``.",
        prefix=True,
    )

    SPANS = (JOB_RUN, CHAT)

    JOB_DURATION = MetricName(
        "my_plugin.job.duration",
        "Histogram",
        "s",
        "How long each job took.",
        (JOB_NAME, OUTCOME),
        buckets=(0.01, 0.1, 1, 10, 60, 600, 3600),
    )

Three details that matter:

- ``values=`` declares a **closed enum**. The conformance helpers (below) assert every emitted value is a member, which is what makes an attribute safe to use as a metric dimension - a metric series is keyed by its attribute values, so an open value set on a metric is an unbounded-cardinality hazard.
- ``prefix=True`` registers a span *family* whose emitted names share a fixed prefix; ``datasette.telemetry_registry.span_for()`` matches them by prefix, exact names first.
- Declare explicit histogram ``buckets=`` scaled to *your* domain. Core's SQLite-scale boundaries are importable as ``datasette.telemetry_registry.DURATION_BUCKETS`` (0.0001s to 10s) - use them if you are timing SQLite work so dashboards align, and define your own otherwise (a job scheduler wants buckets out to an hour; the SDK's defaults will put all your measurements in one bucket either way).

.. _plugin_telemetry_privacy:

Privacy and cardinality rules
-----------------------------

Core's instrumentation records **no data users put into Datasette and no identifier that ties a signal to a person** - no parameter values, no query strings, no actor identifiers, no IP addresses. Hold your plugin to the same bar:

- Attribute values should be closed enums, booleans, counts and durations. Anything echoed from user input - a name, a URL, a token, free text - does not belong on a span, and *especially* not on a metric.
- If you time user-influenced SQL, follow core: record the SQL via ``datasette.telemetry.sql_attribute()`` (truncated, never parameters) on spans only.
- When a value is interesting but unbounded, record a bounded proxy instead: a count, a byte size, a truncation flag, or the enum outcome.

.. _plugin_telemetry_callbacks:

Your database work is already traced
------------------------------------

Every call your plugin makes through :ref:`db.execute() <database_execute>`, :ref:`db.execute_fn() <database_execute_fn>`, :ref:`db.execute_write() <database_execute_write>` and :ref:`db.execute_write_fn() <database_execute_write_fn>` already emits core's ``db.query`` spans and is counted in the ``db.client.operation.duration`` histogram. Two consequences:

- **Pass named callables**, not lambdas: the span for a callback-style call is identified by ``datasette.callback``, the callable's qualified name, and a lambda reports ``<lambda>``.
- If you also wrap those calls in your own span or histogram, you are creating a *second* series in *your* scope - that is fine and sometimes right (yours can carry plugin-level attributes core cannot know), but it is a deliberate two-series design, not a substitute for core's.

.. _plugin_telemetry_request_span:

Enriching the request span
--------------------------

Inside a view or ASGI middleware, ``datasette.telemetry.request_span(scope)`` returns the recording ``SERVER`` span for the current request, or ``None`` when nothing is recording - which is also your signal to skip any work done only to compute attributes:

.. code-block:: python

    from datasette.telemetry import request_span

    async def my_view(request):
        span = request_span(request.scope)
        if span is not None:
            span.set_attribute("my_plugin.cache", "hit")
        ...

.. _plugin_telemetry_background:

Background work: roots with links
---------------------------------

A background job, a scheduled task or a queue consumer must **not** parent its spans to the request that caused it - by the time the work runs, that request span has usually ended, and a child outliving its closed parent renders badly in every major trace UI. The correct shape, the one core itself uses for ``execute_write(block=False)``, is a **root span carrying a link** to the causing span:

.. code-block:: python

    from datasette.telemetry import linked_root_span_kwargs

    # Capture at scheduling time, while the causing span is current:
    kwargs = linked_root_span_kwargs()

    # Later, wherever the work actually runs:
    with tracer.start_as_current_span("my_plugin.job.run", **kwargs) as span:
        span.set_attribute(OUTCOME, "ok")

For a periodic loop (a health check, a scheduler tick), the convention is one root span **per tick**, always emitted - including no-op ticks, with an outcome attribute saying so - plus a tick counter metric. Suppressing quiet ticks seems tidy but destroys the signal operators actually want: "is the loop still running?". Pair the spans with a gauge for the loop's staleness if the interval is long.

Two propagation facts worth knowing (details in ``datasette/telemetry.py``):

- Core's ``tracer`` and yours are proxies. A ``ProxyTracer`` permanently caches the first concrete tracer it resolves *after* a provider exists, so in embedded deployments the provider must be installed before the first span - importing the module is fine, starting spans is not. Meters forward retroactively; tracers do not.
- ``asyncio.create_task`` copies the ambient context, so a long-running task created during a request will silently parent to that request's span - exactly the bug ``linked_root_span_kwargs()`` exists to avoid.

.. _plugin_telemetry_testing:

Testing your instrumentation
----------------------------

``datasette.telemetry_testing`` ships the same fixtures and checks core's own suite uses. In your ``conftest.py``:

.. code-block:: python

    from datasette.telemetry_testing import (  # noqa: F401
        otel_metrics,
        otel_meter_provider,
        otel_provider,
        otel_spans,
    )

``otel_provider`` and ``otel_meter_provider`` are session-scoped and autouse - they install a real SDK provider (in-memory, synchronous export) once per process, and do nothing when the SDK is not installed, so add ``opentelemetry-sdk`` to your test dependencies only. Tests then take ``otel_spans`` (an ``InMemorySpanExporter``) or ``otel_metrics`` (a collector with ``collect()`` / ``point()`` helpers).

Wire your registry to reality with the conformance helpers - the two directions catch instrumentation added without documentation and documentation describing signals that no longer exist:

.. code-block:: python

    from datasette.telemetry_testing import (
        assert_package_never_imports_sdk,
        assert_registry_covered,
        assert_spans_conform,
    )

    from my_plugin.telemetry import SPANS


    def test_conformance(otel_spans):
        run_a_workload_that_exercises_everything()
        finished = otel_spans.get_finished_spans()
        # Everything emitted is registered (and enum values are legal):
        assert_spans_conform(SPANS, finished, scope_name="my-plugin")
        # Everything registered was emitted:
        assert_registry_covered(SPANS, finished, scope_name="my-plugin")


    def test_api_only_dependency():
        assert_package_never_imports_sdk("my_plugin")

Always pass ``scope_name`` - the exporter also holds core's spans, and your registry should only be judged against your own.

.. _plugin_telemetry_caveats:

Known caveats
-------------

- **Streaming responses hold the request span open.** Core's request span ends when the response body finishes, so for an SSE or long-streaming route its duration is the connection lifetime. If you need per-message timing on a stream, emit your own child spans or span events per message, and use gauges for concurrent-stream counts.
- **A plugin timing core's work double-measures by design.** See :ref:`plugin_telemetry_callbacks` above.
- ``datasette.client`` requests made from inside a request currently produce a nested ``SERVER`` span, which can double-count requests in kind-based dashboards.
