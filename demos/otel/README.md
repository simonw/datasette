# OpenTelemetry demo

Datasette core depends on `opentelemetry-api` only. It emits spans and nothing else — it never
creates a `TracerProvider`, never configures an exporter, and never sets a sampler. With no SDK
installed every span is a no-op and costs approximately nothing.

That means "turning tracing on" is entirely the job of whoever runs Datasette. This directory
shows two ways to do it, **neither of which needs Docker**:

| | |
|---|---|
| `otlp_receiver.py` | A ~150 line pure-Python OTLP/HTTP receiver — a real protobuf export, summarized in your terminal |
| `just jaeger` | The same export into Jaeger's own binary, for a real trace UI |

Both listen for OTLP/HTTP on port 4318, so the Datasette side is identical — run one or the
other, not both. The `Justfile` in this directory wraps every command below; bare `just` lists
the recipes.

## 1. A real OTLP export, pure Python

Terminal 1 — the receiver (`just receiver`):

```bash
uv run --with opentelemetry-proto python demos/otel/otlp_receiver.py
```

Terminal 2 — Datasette under the OpenTelemetry agent (`just serve`, which also generates a
200-row `demo.db` on first run):

```bash
OTEL_TRACES_EXPORTER=otlp \
OTEL_METRICS_EXPORTER=none \
OTEL_LOGS_EXPORTER=none \
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
OTEL_SERVICE_NAME=datasette \
OTEL_BSP_SCHEDULE_DELAY=1000 \
  uv run --with opentelemetry-distro \
         --with opentelemetry-exporter-otlp-proto-http \
    opentelemetry-instrument datasette demo.db -p 8001
```

Load a page (`just request`), wait a second for the batch flush, then Ctrl-C the receiver.
Real output from startup plus one request against the 200-row demo table:

```
received 93 spans (93 total)

=== 93 spans ===
  db.query                                        43      18.55ms total
  db.query.execute                                42       7.60ms total
  db.write.queue_wait                              3       0.40ms total
  db.write.execute                                 3       4.99ms total
  datasette.startup                                1       7.17ms total
  GET /(?P<database>[^\/\.]+)/(?P<table>[^\/\.]+)(\.(?P<format>\w+))?$    1      59.46ms total

slowest spans:
      59.46ms  GET /(?P<database>[^\/\.]+)/(?P<table>[^\/\.]+)(\.(?P<format>\w+))?$  -> 200
       7.17ms  datasette.startup
       4.58ms  db.write.execute
       1.81ms  db.query  with limited as (select * from (select id, name, height_cm f

2 root spans:
  datasette.startup x1
  GET /(?P<database>[^\/\.]+)/(?P<table>[^\/\.]+)(\.(?P<format>\w+))?$ x1
```

Things worth noticing:

- **Exactly two root spans.** Every span belongs to either the request that caused it or to
  `datasette.startup` — there are no orphans. A `block=False` write would be a third kind of
  root, carrying a *link* back to the request that enqueued it rather than a parent, because
  the write can outlive that request.
- **Request spans are named after the matched route**, which in Datasette is a regex — that
  string is ugly but it is the honest low-cardinality name. The pretty path is in the
  `url.path` attribute.
- **`db.query` vs `db.query.execute`.** The gap between the two is time spent waiting for a
  thread. Likewise `db.write.queue_wait` vs `db.write.execute` is how you tell "the write was
  slow" apart from "the write waited behind another writer".

The receiver is a debugging aid, not a backend: nothing is persisted, it speaks OTLP/HTTP only
(not gRPC), and it ignores metrics and logs.

## 2. The same thing with a real UI: Jaeger, no Docker

Jaeger ingests OTLP directly on the same port 4318, so the Datasette command does not change.
With the `jaeger` binary on your PATH (<https://www.jaegertracing.io/download/>):

```bash
just jaeger    # terminal 1 — UI on http://localhost:16686
just serve     # terminal 2 — identical to above
just request   # terminal 3
```

Then open <http://localhost:16686>, pick service `datasette`, and Find Traces. Verified: one
request produces exactly two traces — the request trace (~67 spans, rooted at the `GET ...`
span, with every `db.query` nested inside it across thread boundaries) and the
`datasette.startup` trace (~26 spans of catalog queries and connection warm-up).

## Notes on the environment variables

- **`opentelemetry-instrument` is required.** Setting `OTEL_TRACES_EXPORTER` and running plain
  `datasette` produces nothing at all: that variable is read by the SDK's auto-configuration,
  which only runs under the agent — core never installs a provider itself.
- **`OTEL_SERVICE_NAME=datasette`** is what you pick from Jaeger's Service dropdown. Leave it
  out and the SDK defaults to `unknown_service:<executable>`.
- **`OTEL_BSP_SCHEDULE_DELAY=1000`** drops the batch flush from its ~10 second default to ~1
  second. For a demo this is the difference between "it works" and "it looks broken". Do not
  use it in production — it trades export efficiency for latency.
- **`OTEL_METRICS_EXPORTER=none OTEL_LOGS_EXPORTER=none`** because `opentelemetry-distro`
  defaults every signal to OTLP, and a traces-only backend like Jaeger answers the metrics
  and logs exports with a stream of `StatusCode.UNIMPLEMENTED` noise.

## Privacy

`db.query.text` **is** recorded, truncated. SQL **parameter values are never recorded** — only
a parameter count. On a public Datasette instance the SQL text is user-supplied; if you export
to a third-party vendor, that text leaves your infrastructure.

See the telemetry section of the Datasette documentation for the full span and attribute
reference.
