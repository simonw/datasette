import inspect
from typing import Any, NamedTuple


class CallableStatus(NamedTuple):
    is_callable: bool
    is_async_callable: bool


def check_callable(obj: Any) -> CallableStatus:
    if not callable(obj):
        return CallableStatus(False, False)

    if isinstance(obj, type):
        # It's a class
        return CallableStatus(True, False)

    if inspect.iscoroutinefunction(obj):
        # Inspect the object itself, so bound methods and functools.partial objects
        # wrapping an async function are recognised - they are not types.FunctionType,
        # and their __call__ is a method wrapper rather than a coroutine function.
        return CallableStatus(True, True)

    if callable(obj):
        return CallableStatus(True, inspect.iscoroutinefunction(obj.__call__))

    assert False, f"obj {obj!r} is somehow callable with no __call__ method"
