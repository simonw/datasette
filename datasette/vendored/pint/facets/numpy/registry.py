"""
    pint.facets.numpy.registry
    ~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from typing import Any, Generic

from ...compat import TypeAlias
from ..plain import GenericPlainRegistry, QuantityT, UnitT
from .quantity import NumpyQuantity
from .unit import NumpyUnit


class GenericNumpyRegistry(
    Generic[QuantityT, UnitT], GenericPlainRegistry[QuantityT, UnitT]
):
    pass


class NumpyRegistry(GenericPlainRegistry[NumpyQuantity[Any], NumpyUnit]):
    Quantity: TypeAlias = NumpyQuantity[Any]
    Unit: TypeAlias = NumpyUnit
