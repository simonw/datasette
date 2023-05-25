from datasette.utils.check_callable import check_callable
import pytest


class AsyncClass:
    async def __call__(self):
        pass


class NotAsyncClass:
    def __call__(self):
        pass


class ClassNoCall:
    pass


async def async_func():
    pass


def non_async_func():
    pass


@pytest.mark.parametrize(
    "obj,expected_is_callable,expected_is_async_callable",
    (
        (async_func, True, True),
        (non_async_func, True, False),
        (AsyncClass(), True, True),
        (NotAsyncClass(), True, False),
        (ClassNoCall(), False, False),
        (AsyncClass, True, False),
        (NotAsyncClass, True, False),
        (ClassNoCall, True, False),
        ("", False, False),
        (1, False, False),
        (str, True, False),
    ),
)
def test_check_callable(obj, expected_is_callable, expected_is_async_callable):
    status = check_callable(obj)
    assert status.is_callable == expected_is_callable
    assert status.is_async_callable == expected_is_async_callable
