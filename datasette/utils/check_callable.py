import inspect
import types
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

    if isinstance(obj, types.FunctionType):
        return CallableStatus(True, inspect.iscoroutinefunction(obj))

    if callable(obj):
        return CallableStatus(True, inspect.iscoroutinefunction(obj.__call__))

    assert False, f"obj {obj!r} is somehow callable with no __call__ method"
