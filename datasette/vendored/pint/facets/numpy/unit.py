"""
    pint.facets.numpy.unit
    ~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from ...compat import is_upcast_type
from ..plain import PlainUnit


class NumpyUnit(PlainUnit):
    __array_priority__ = 17

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        if method != "__call__":
            # Only handle ufuncs as callables
            return NotImplemented

        # Check types and return NotImplemented when upcast type encountered
        types = {
            type(arg)
            for arg in list(inputs) + list(kwargs.values())
            if hasattr(arg, "__array_ufunc__")
        }
        if any(is_upcast_type(other) for other in types):
            return NotImplemented

        # Act on limited implementations by conversion to multiplicative identity
        # Quantity
        if ufunc.__name__ in ("true_divide", "divide", "floor_divide", "multiply"):
            return ufunc(
                *tuple(
                    self._REGISTRY.Quantity(1, self._units) if arg is self else arg
                    for arg in inputs
                ),
                **kwargs,
            )

        return NotImplemented
