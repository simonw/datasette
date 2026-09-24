.. _plugin_telemetry:

Telemetry for plugin authors
============================

Datasette core emits OpenTelemetry spans and metrics for the work it does itself - see :ref:`internals_telemetry` for what those are and how an operator turns them on. This page is about the other half: instrumenting the work **your plugin** does, so that a plugin's queries, background jobs and custom operations show up in the same traces and the same metrics pipeline, using the same conventions.

Depend on ``opentelemetry-api`` only. Providers and exporters are configured by whoever runs Datasette. Without a provider, no telemetry is recorded.

.. _plugin_telemetry_scope:

Use your own instrumentation scope
----------------------------------

Never emit through core's tracer or meter. Your plugin's scope name is the machine-readable claim about *who emitted a signal*, and consumers filter on it:

.. code-block:: python

    from opentelemetry import metrics, trace

    from my_plugin import __version__

    tracer = trace.get_tracer("my_plugin", __version__)
    meter = metrics.get_meter("my_plugin", __version__)

If every attribute you emit follows current semantic conventions you can also pass ``schema_url=``; ``datasette.telemetry.SCHEMA_URL`` is the version core's own spellings track, with a comment explaining how to choose one. When in doubt, omit it - a wrong schema URL is worse than none.

Two naming rules keep the ecosystem's signals tellable-apart:

- **Scope**: use your plugin's *import package* name - ``my_plugin``, underscores and all. Consumers filter on the scope, and one spelling convention means they can guess it.
- **Signal prefix**: name spans, metrics and custom attributes under a prefix you own - your package name (``my_plugin.*``) or a short product name (``paper.*``). **Never a bare** ``datasette.*`` **prefix**: that namespace belongs to core, an operator could no longer tell core signals from plugin signals, and a future core signal could collide with yours.

Reuse core's shared attribute spellings where they mean the same thing - ``db.namespace`` for a database name, ``error.type`` for a failure class - rather than minting parallel ones. If a span family in your registry shares a prefix with another entry, exact names always win over prefix matches, but two overlapping ``prefix=True`` entries resolve to whichever is listed first - avoid overlapping families rather than relying on order.

.. _plugin_telemetry_registry:

Declare a registry
------------------

Core keeps a single source of truth for every signal it emits in ``datasette/telemetry_registry.py``, and the classes it uses are public API. They subclass ``str``, so a registry entry *is* the name you pass to OpenTelemetry - no parallel constants to keep in step:

.. code-block:: python

    from opentelemetry.trace import SpanKind

    from datasette.telemetry_registry import (
        Attribute,
        MetricName,
        SpanName,
    )

    OUTCOME = Attribute(
        "my_plugin.outcome",
        "How the job ended.",
        values={"ok", "error", "skipped"},
    )
    JOB_NAME = Attribute(
        "my_plugin.job", "The registered job name."
    )

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

These rules are enforceable: see ``assert_no_forbidden_values()`` in :ref:`plugin_telemetry_testing`.

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
    with tracer.start_as_current_span(
        "my_plugin.job.run", **kwargs
    ) as span:
        span.set_attribute(OUTCOME, "ok")

For a periodic loop (a health check, a scheduler tick), the convention is one root span **per tick**, always emitted - including no-op ticks, with an outcome attribute saying so - plus a tick counter metric. Suppressing quiet ticks seems tidy but destroys the signal operators actually want: "is the loop still running?". Pair the spans with a gauge for the loop's staleness if the interval is long.

Two propagation facts worth knowing (details in ``datasette/telemetry.py``):

- Core's ``tracer`` and yours are proxies. A ``ProxyTracer`` permanently caches the first concrete tracer it resolves *after* a provider exists, so in embedded deployments the provider must be installed before the first span - importing the module is fine, starting spans is not. Meters forward retroactively; tracers do not.
- ``asyncio.create_task`` copies the ambient context, so a long-running task created during a request will silently parent to that request's span - exactly the bug ``linked_root_span_kwargs()`` exists to avoid.

.. _plugin_telemetry_gauges:

Observable gauges
-----------------

For a *level* - how many streams are open, how deep is a queue - register an observable gauge whose callback the SDK invokes on its own collection cycle. Three disciplines, all inherited from how core implements its pool gauges in ``datasette/telemetry.py``:

- Hold live objects **weakly** (a ``weakref.WeakSet`` guarded by a lock), so instrumenting an object never keeps it alive, and unregister on close.
- The callback runs on the SDK's **collection thread**: never take a lock the request path holds, never await, never do I/O. Read cached state and yield ``Observation`` values; if freshness matters, refresh the cache from your own code and expose its staleness as another gauge.
- With no provider installed the callback is **never invoked at all**, so gauges are free by default.

.. _plugin_telemetry_testing:

Testing your instrumentation
----------------------------

Use ``datasette.telemetry_testing`` to capture telemetry in your tests and check it against your registry. Add `opentelemetry-sdk <https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-sdk>`__ to your test dependencies, then import these fixtures in ``conftest.py``:

.. code-block:: python

    from datasette.telemetry_testing import (  # noqa: F401
        otel_metrics,
        otel_meter_provider,
        otel_provider,
        otel_reset,
        otel_spans,
    )

``otel_provider`` and ``otel_meter_provider``
    Automatically configure in-memory recording for spans and metrics once per test session.

``otel_reset``
    Automatically clears recorded spans and drains collected metrics after every test.

``otel_spans``
    Provides an ``InMemorySpanExporter``. Call ``get_finished_spans()`` to retrieve spans recorded during the test.

``otel_metrics``
    Provides a metrics collector. Call ``collect()`` to capture a snapshot, then use ``point()`` or ``points()`` to inspect it.

Tests requesting ``otel_spans`` or ``otel_metrics`` skip if the SDK is unavailable or another provider has already been installed.

The assertion helpers check the recorded telemetry against your registry:

``assert_spans_conform()``
    Checks that emitted spans and attributes are registered, and attribute values match any declared ``values=`` enums.

``assert_metrics_conform()``
    Checks that emitted metrics and attributes are registered, attribute values match any declared enums, and instrument kinds and units match the registry.

``assert_spans_covered()`` and ``assert_metrics_covered()``
    Check that every registered span or metric and its required attributes appeared during the test. Attributes marked ``optional=True`` are excluded from this check; test those separately.

Pass your plugin's instrumentation scope as ``scope_name`` to these helpers, since the fixtures also record Datasette's own telemetry.

Run a workload that exercises your instrumentation, then call ``otel_metrics.collect()`` once before checking the metrics. Counters and histograms report measurements since the previous collection. Keep the Datasette instance open until collection so observable gauges can report its state:

.. code-block:: python

    from datasette.telemetry_testing import (
        assert_metrics_conform,
        assert_metrics_covered,
        assert_package_never_imports_sdk,
        assert_spans_covered,
        assert_spans_conform,
    )

    from my_plugin.telemetry import METRICS, SPANS


    def test_api_only_dependency():
        assert_package_never_imports_sdk("my_plugin")


    def test_conformance(otel_spans, otel_metrics):
        run_a_workload_that_exercises_everything()
        finished = otel_spans.get_finished_spans()
        # Everything emitted is registered (and enum values are legal):
        assert_spans_conform(
            SPANS, finished, scope_name="my_plugin"
        )
        # Everything registered was emitted:
        assert_spans_covered(
            SPANS, finished, scope_name="my_plugin"
        )
        # Collect once, then check the metrics:
        otel_metrics.collect()
        assert_metrics_conform(
            METRICS, otel_metrics, scope_name="my_plugin"
        )
        assert_metrics_covered(
            METRICS, otel_metrics, scope_name="my_plugin"
        )

``assert_package_never_imports_sdk()`` checks that importing your plugin does not import the OpenTelemetry SDK. Run this test early in your suite; see the helper's docstring for a macOS threading limitation.

Use ``assert_no_forbidden_values()`` to check for private data in telemetry. Include fake email addresses, tokens or usernames in your test workload, then pass those values, the finished spans and the collected metrics to the helper. It checks span names, attributes, events, status descriptions and metric attributes.

Leave ``scope_name`` unset for privacy checks so they include both your plugin's telemetry and Datasette's own.

.. _plugin_telemetry_caveats:

Known caveats
-------------

- **Streaming responses hold the request span open.** Core's request span ends when the response body finishes, so for an SSE or long-streaming route its duration is the connection lifetime. If you need per-message timing on a stream, emit your own child spans or span events per message, and use gauges for concurrent-stream counts.
- **A plugin timing core's work double-measures by design.** See :ref:`plugin_telemetry_callbacks` above.
- ``datasette.client`` requests made from inside a request produce a nested ``SERVER`` span. Those spans carry ``datasette.internal_client: true`` - filter on it to keep kind-based dashboards from double-counting requests.
