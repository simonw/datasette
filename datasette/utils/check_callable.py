import asyncio
import types
from typing import NamedTuple, Any


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
        return CallableStatus(True, asyncio.iscoroutinefunction(obj))

    if hasattr(obj, "__call__"):
        return CallableStatus(True, asyncio.iscoroutinefunction(obj.__call__))

    assert False, "obj {} is somehow callable with no __call__ method".format(repr(obj))
