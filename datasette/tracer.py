import asyncio
from contextlib import contextmanager
import time

tracers = {}


def get_task_id():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return None
    return id(asyncio.Task.current_task(loop=loop))


@contextmanager
def trace(type, action):
    task_id = get_task_id()
    if task_id is None:
        yield
        return
    tracer = tracers.get(task_id)
    if tracer is None:
        yield
        return
    begin = time.time()
    yield
    end = time.time()
    tracer.append((type, action, begin, end, 1000 * (end - begin)))


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
