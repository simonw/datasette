.. _plugin_telemetry:

Telemetry for plugin authors
============================

Datasette core emits OpenTelemetry spans and metrics for the work it does itself - see :ref:`internals_telemetry` for what those are and how an operator turns them on. This page is about the other half: instrumenting the work **your plugin** does, so that a plugin's queries, background jobs and custom operations show up in the same traces and the same metrics pipeline, using the same conventions.

.. _plugin_telemetry_scope:

Use your own instrumentation scope
----------------------------------

Create a tracer and meter using your plugin's own instrumentation scope:

.. code-block:: python

    from opentelemetry import metrics, trace

    from my_plugin import __version__

    tracer = trace.get_tracer("my_plugin", __version__)
    meter = metrics.get_meter("my_plugin", __version__)

Use these naming rules:

- **Scope**: use your plugin's import package name, such as ``my_plugin``. This lets users filter telemetry by plugin.
- **Signal prefix**: prefix spans, metrics and custom attributes with your package name (``my_plugin.*``) or a product name (``paper.*``). The ``datasette.*`` prefix is reserved for core.

Reuse shared attribute names where they describe the same thing: ``db.namespace`` for a database name, or ``error.type`` for an exception class.

If you pass ``schema_url=`` when creating a tracer or meter, choose the semantic-convention version that matches your attributes. Datasette's version is available as ``datasette.telemetry.SCHEMA_URL``. Omit ``schema_url`` if you are unsure which version applies.

.. _plugin_telemetry_registry:

Declare a registry
------------------

Use ``Attribute``, ``SpanName`` and ``MetricName`` from ``datasette.telemetry_registry`` to describe your plugin's telemetry. Registry entries are strings and can be passed directly to OpenTelemetry:

.. code-block:: python

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

    METRICS = (JOB_DURATION,)

The example uses these optional arguments:

``values`` - iterable
    Allowed values for an ``Attribute``. The :ref:`conformance helpers <plugin_telemetry_testing>` check that emitted values belong to this set. Omit it to allow any value.

``prefix`` - boolean
    For ``SpanName``, match emitted names by prefix. Defaults to ``False``. Exact names take precedence over prefix matches. Avoid overlapping prefixes: the first matching entry in the registry wins.

``buckets`` - iterable
    Histogram boundaries for a ``MetricName``, expressed in the metric's unit. Pass these to ``meter.create_histogram()`` using ``explicit_bucket_boundaries_advisory=JOB_DURATION.buckets``. Choose boundaries suitable for the operations you measure. For SQLite timings, ``datasette.telemetry_registry.DURATION_BUCKETS`` provides boundaries from 0.0001 to 10 seconds.

.. _plugin_telemetry_privacy:

Privacy and cardinality rules
-----------------------------

Core does not explicitly attach bound SQL parameter values, actor identifiers, cookies, authorization headers, client IP addresses or URL query strings as attributes. It does record SQL text, URL paths, host names, User-Agent headers and exception details, which may contain sensitive information. See :ref:`internals_telemetry_privacy`.

- Prefer closed enums, booleans, counts and durations for attribute values. Avoid recording personal information, tokens or other secrets.
- If you record SQL, use ``datasette.telemetry.sql_attribute()`` on spans only. It truncates SQL text but does not redact literal values. Do not add bound parameter values.
- Keep metric dimensions bounded. For user input or other unbounded values, record a count, a byte size, a truncation flag or an enum outcome instead.

Use ``assert_no_forbidden_values()`` in :ref:`plugin_telemetry_testing` to check for specific sensitive values in captured telemetry. This helper does not automatically identify all sensitive information.

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

For background work that can outlive a request, create a root span linked to the span that scheduled it. Call ``linked_root_span_kwargs()`` when scheduling the work, then pass the result when starting its span. If there is no valid span context to capture, the new span has no link:

.. code-block:: python

    from datasette.telemetry import linked_root_span_kwargs

    # Capture the current span when scheduling the work:
    kwargs = linked_root_span_kwargs()

    # Later, wherever the work actually runs:
    with tracer.start_as_current_span(
        "my_plugin.job.run", **kwargs
    ) as span:
        span.set_attribute(OUTCOME, "ok")

For periodic tasks, create a root span and increment a counter on each iteration, including iterations with no work. Record the result in an outcome attribute. A gauge reporting the time since the last iteration can help monitor tasks with long intervals.

``asyncio.create_task()`` inherits the current trace context. Use ``linked_root_span_kwargs()`` to start background work with its own root span and a link to that context.

Tracers and meters can be created at module scope. In embedded deployments, configure the application's providers before the work you want to record begins.

.. _plugin_telemetry_gauges:

Observable gauges
-----------------

Use an observable gauge for current values such as the number of open streams or the length of a queue. The SDK calls its callback when collecting metrics:

- Track live objects using weak references, such as a ``weakref.WeakSet``, and unregister them when they close.
- Callbacks may run on a different thread from request handlers. Protect shared state and avoid waiting on locks held by request handlers.
- Read cached state and yield ``Observation`` values. Keep callbacks synchronous and free of I/O. Refresh cached values outside the callback; use a separate gauge to report their age if needed.

Without a provider, gauge callbacks are not invoked.

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
