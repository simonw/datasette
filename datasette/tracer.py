import asyncio
from contextlib import contextmanager
import time
import json
import traceback

tracers = {}

TRACE_RESERVED_KEYS = {"type", "start", "end", "duration_ms", "traceback"}


# asyncio.current_task was introduced in Python 3.7:
for obj in (asyncio, asyncio.Task):
    current_task = getattr(obj, "current_task", None)
    if current_task is not None:
        break


def get_task_id():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return None
    return id(current_task(loop=loop))


@contextmanager
def trace(type, **kwargs):
    assert not TRACE_RESERVED_KEYS.intersection(
        kwargs.keys()
    ), ".trace() keyword parameters cannot include {}".format(TRACE_RESERVED_KEYS)
    task_id = get_task_id()
    if task_id is None:
        yield
        return
    tracer = tracers.get(task_id)
    if tracer is None:
        yield
        return
    start = time.time()
    yield
    end = time.time()
    trace_info = {
        "type": type,
        "start": start,
        "end": end,
        "duration_ms": (end - start) * 1000,
        "traceback": traceback.format_list(traceback.extract_stack(limit=6)[:-3]),
    }
    trace_info.update(kwargs)
    tracer.append(trace_info)


@contextmanager
def capture_traces(tracer):
    # tracer is a list
    task_id = get_task_id()
    if task_id is None:
        yield
        return
    tracers[task_id] = tracer
    yield
    del tracers[task_id]


class AsgiTracer:
    # If the body is larger than this we don't attempt to append the trace
    max_body_bytes = 1024 * 256  # 256 KB

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if b"_trace=1" not in scope.get("query_string", b"").split(b"&"):
            await self.app(scope, receive, send)
            return
        trace_start = time.time()
        traces = []

        accumulated_body = b""
        size_limit_exceeded = False
        response_headers = []

        async def wrapped_send(message):
            nonlocal accumulated_body, size_limit_exceeded, response_headers
            if message["type"] == "http.response.start":
                response_headers = message["headers"]
                await send(message)
                return

            if message["type"] != "http.response.body" or size_limit_exceeded:
                await send(message)
                return

            # Accumulate body until the end or until size is exceeded
            accumulated_body += message["body"]
            if len(accumulated_body) > self.max_body_bytes:
                await send(
                    {
                        "type": "http.response.body",
                        "body": accumulated_body,
                        "more_body": True,
                    }
                )
                size_limit_exceeded = True
                return

            if not message.get("more_body"):
                # We have all the body - modify it and send the result
                # TODO: What to do about Content-Type or other cases?
                trace_info = {
                    "request_duration_ms": 1000 * (time.time() - trace_start),
                    "sum_trace_duration_ms": sum(t["duration_ms"] for t in traces),
                    "num_traces": len(traces),
                    "traces": traces,
                }
                try:
                    content_type = [
                        v.decode("utf8")
                        for k, v in response_headers
                        if k.lower() == b"content-type"
                    ][0]
                except IndexError:
                    content_type = ""
                if "text/html" in content_type and b"</body>" in accumulated_body:
                    extra = json.dumps(trace_info, indent=2)
                    extra_html = "<pre>{}</pre></body>".format(extra).encode("utf8")
                    accumulated_body = accumulated_body.replace(b"</body>", extra_html)
                elif "json" in content_type and accumulated_body.startswith(b"{"):
                    data = json.loads(accumulated_body.decode("utf8"))
                    if "_trace" not in data:
                        data["_trace"] = trace_info
                    accumulated_body = json.dumps(data).encode("utf8")
                await send({"type": "http.response.body", "body": accumulated_body})

        with capture_traces(traces):
            await self.app(scope, receive, wrapped_send)
