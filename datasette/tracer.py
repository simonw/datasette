import asyncio
from contextlib import contextmanager
import time
import traceback

tracers = {}

TRACE_RESERVED_KEYS = {"type", "start", "end", "duration_ms", "traceback"}


def get_task_id():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return None
    return id(asyncio.Task.current_task(loop=loop))


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
    trace = {
        "type": type,
        "start": start,
        "end": end,
        "duration_ms": (end - start) * 1000,
        "traceback": traceback.format_list(traceback.extract_stack(limit=6)[:-3]),
    }
    trace.update(kwargs)
    tracer.append(trace)


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
