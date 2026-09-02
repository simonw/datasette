"""
A minimal OTLP/HTTP trace receiver, in about a hundred lines of Python.

Run it, point Datasette's OpenTelemetry agent at it, and get a real end-to-end
export - over the wire, in the real protobuf wire format - without Docker, a
collector, or Jaeger:

    # terminal 1
    uv run --with opentelemetry-proto python demos/otel/otlp_receiver.py

    # terminal 2
    OTEL_TRACES_EXPORTER=otlp \\
    OTEL_METRICS_EXPORTER=none \\
    OTEL_LOGS_EXPORTER=none \\
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \\
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \\
    OTEL_SERVICE_NAME=datasette \\
    OTEL_BSP_SCHEDULE_DELAY=1000 \\
      uv run --with opentelemetry-distro \\
             --with opentelemetry-exporter-otlp-proto-http \\
        opentelemetry-instrument datasette mydb.db

(or `just receiver` and `just serve mydb.db` from this directory.)

Load a page, wait a second for the batch processor to flush, then press
Ctrl-C here for a summary.

This is a debugging aid, not a tracing backend: it does not persist anything,
speaks only OTLP/HTTP (not gRPC), and ignores metrics and logs. For anything
real, export to an actual backend instead.
"""

import gzip
import signal
import sys
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
        ExportTraceServiceResponse,
    )
except ImportError:
    sys.exit(
        "This receiver needs the OpenTelemetry protobuf definitions:\n    uv run --with opentelemetry-proto python demos/otel/otlp_receiver.py"
    )

HOST = "127.0.0.1"
PORT = 4318

received = []


def attribute_value(value):
    for field in ("string_value", "int_value", "double_value", "bool_value"):
        if value.HasField(field):
            return getattr(value, field)
    return None


class OTLPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # the default handler logs every request to stderr

    def do_GET(self):
        # For the person who opens http://localhost:4318 in a browser
        # expecting a UI: there isn't one here, on Jaeger either - 4318 is
        # where exporters POST protobuf. Jaeger's UI lives on :16686.
        body = (
            f"This is an OTLP/HTTP ingestion endpoint ({len(received)} spans "
            "received so far).\n\n"
            "There is no UI on this port - OpenTelemetry exporters POST "
            "protobuf to /v1/traces here.\nThe span summary appears in the "
            "terminal running this receiver when you Ctrl-C it.\n"
            "For a real UI, run Jaeger instead (`just jaeger`) and open "
            "http://localhost:16686\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)

        request = ExportTraceServiceRequest()
        request.ParseFromString(body)
        batch = 0
        for resource_spans in request.resource_spans:
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    batch += 1
                    received.append(
                        {
                            "name": span.name,
                            "parent_id": span.parent_span_id.hex() or None,
                            "duration_ms": (
                                span.end_time_unix_nano - span.start_time_unix_nano
                            )
                            / 1e6,
                            "attributes": {
                                a.key: attribute_value(a.value) for a in span.attributes
                            },
                        }
                    )
        # flush=True so the live feedback survives being piped or redirected
        print(f"received {batch} spans ({len(received)} total)", flush=True)

        payload = ExportTraceServiceResponse().SerializeToString()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def span_detail(span):
    "The one attribute most worth showing next to a span name."
    attributes = span["attributes"]
    query = attributes.get("db.query.text")
    if query:
        return f"  {query[:60]}"
    status = attributes.get("http.response.status_code")
    if status is not None:
        return f"  -> {status}"
    return ""


def summarise():
    if not received:
        print("\nNo spans received.")
        print("Remember: `opentelemetry-instrument` is required - Datasette core")
        print("installs no provider - and the BatchSpanProcessor flushes about")
        print("every 10s unless OTEL_BSP_SCHEDULE_DELAY says otherwise.")
        return

    print(f"\n=== {len(received)} spans ===")
    for name, count in Counter(span["name"] for span in received).most_common():
        total_ms = sum(s["duration_ms"] for s in received if s["name"] == name)
        print(f"  {name:<45}{count:>5}  {total_ms:>9.2f}ms total")

    slowest = sorted(received, key=lambda s: -s["duration_ms"])[:5]
    print("\nslowest spans:")
    for span in slowest:
        print(f"  {span['duration_ms']:>9.2f}ms  {span['name']}{span_detail(span)}")

    # Roots are one span per request (named "GET <route>"), datasette.startup,
    # and any block=False write - those link back to their enqueuer rather than
    # nesting under it, because the write can outlive the request that queued it.
    roots = Counter(span["name"] for span in received if not span["parent_id"])
    print(f"\n{sum(roots.values())} root spans:")
    for name, count in roots.most_common():
        print(f"  {name} x{count}")


if __name__ == "__main__":
    try:
        server = HTTPServer((HOST, PORT), OTLPHandler)
    except OSError as error:
        sys.exit(
            f"Could not listen on {HOST}:{PORT} ({error}).\n"
            "Something else is already using the OTLP port - most likely an "
            "earlier copy of this receiver, or Jaeger, still running."
        )
    print(f"OTLP/HTTP receiver listening on http://{HOST}:{PORT}")
    print("Press Ctrl-C for a summary.")

    # serve_forever() runs on a worker thread and the main thread just waits,
    # so the summary still prints when this is launched through a wrapper such
    # as `uv run`, where relying on KeyboardInterrupt alone is unreliable.
    stop = threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def request_stop(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    server.shutdown()
    summarise()
