import asyncio
from datasette.utils.asyncdi import AsyncBase, inject
import pytest
from random import random


class Simple(AsyncBase):
    def __init__(self):
        self.log = []

    @inject
    async def two(self):
        self.log.append("two")

    @inject
    async def one(self, two):
        self.log.append("one")
        return self.log

    async def not_inject(self, one, two):
        return one + two


class Complex(AsyncBase):
    inject_all = True

    def __init__(self):
        self.log = []

    async def d(self):
        await asyncio.sleep(random() * 0.1)
        self.log.append("d")

    async def c(self):
        await asyncio.sleep(random() * 0.1)
        self.log.append("c")

    async def b(self, c, d):
        self.log.append("b")

    async def a(self, b, c):
        self.log.append("a")

    async def go(self, a):
        self.log.append("go")
        return self.log


class WithParameters(AsyncBase):
    inject_all = True

    async def go(self, calc1, calc2, param1):
        return param1 + calc1 + calc2

    async def calc1(self):
        return 5

    async def calc2(self):
        return 6


@pytest.mark.asyncio
async def test_simple():
    assert await Simple().one() == ["two", "one"]
    assert await Simple().not_inject(6, 7) == 13


@pytest.mark.asyncio
async def test_complex():
    result = await Complex().go()
    # 'c' should only be called once
    assert tuple(result) in (
        # c and d could happen in either order
        ("c", "d", "b", "a", "go"),
        ("d", "c", "b", "a", "go"),
    )


@pytest.mark.asyncio
async def test_with_parameters():
    result = await WithParameters().go(param1=4)
    assert result == 15

    # Should throw an error if that parameter is missing
    with pytest.raises(AssertionError) as e:
        await WithParameters().go()
        assert e.args[0] == (
            "The following DI parameters could not be "
            "found in the registry: ['param1']"
        )
