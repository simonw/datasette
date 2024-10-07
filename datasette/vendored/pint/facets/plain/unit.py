"""
    pint.facets.plain.unit
    ~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2016 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import copy
import locale
import operator
from numbers import Number
from typing import TYPE_CHECKING, Any

from ..._typing import UnitLike
from ...compat import NUMERIC_TYPES, deprecated
from ...errors import DimensionalityError
from ...util import PrettyIPython, SharedRegistryObject, UnitsContainer
from .definitions import UnitDefinition

if TYPE_CHECKING:
    from ..context import Context


class PlainUnit(PrettyIPython, SharedRegistryObject):
    """Implements a class to describe a unit supporting math operations."""

    def __reduce__(self):
        # See notes in Quantity.__reduce__
        from datasette.vendored.pint import _unpickle_unit

        return _unpickle_unit, (PlainUnit, self._units)

    def __init__(self, units: UnitLike) -> None:
        super().__init__()
        if isinstance(units, (UnitsContainer, UnitDefinition)):
            self._units = units
        elif isinstance(units, str):
            self._units = self._REGISTRY.parse_units(units)._units
        elif isinstance(units, PlainUnit):
            self._units = units._units
        else:
            raise TypeError(
                "units must be of type str, Unit or "
                "UnitsContainer; not {}.".format(type(units))
            )

    def __copy__(self) -> PlainUnit:
        ret = self.__class__(self._units)
        return ret

    def __deepcopy__(self, memo) -> PlainUnit:
        ret = self.__class__(copy.deepcopy(self._units, memo))
        return ret

    @deprecated(
        "This function will be removed in future versions of pint.\n"
        "Use ureg.formatter.format_unit_babel"
    )
    def format_babel(self, spec: str = "", **kwspec: Any) -> str:
        return self._REGISTRY.formatter.format_unit_babel(self, spec, **kwspec)

    def __format__(self, spec: str) -> str:
        return self._REGISTRY.formatter.format_unit(self, spec)

    def __str__(self) -> str:
        return self._REGISTRY.formatter.format_unit(self)

    def __bytes__(self) -> bytes:
        return str(self).encode(locale.getpreferredencoding())

    def __repr__(self) -> str:
        return f"<Unit('{self._units}')>"

    @property
    def dimensionless(self) -> bool:
        """Return True if the PlainUnit is dimensionless; False otherwise."""
        return not bool(self.dimensionality)

    @property
    def dimensionality(self) -> UnitsContainer:
        """
        Returns
        -------
        dict
            Dimensionality of the PlainUnit, e.g. ``{length: 1, time: -1}``
        """
        try:
            return self._dimensionality
        except AttributeError:
            dim = self._REGISTRY._get_dimensionality(self._units)
            self._dimensionality = dim

        return self._dimensionality

    def compatible_units(self, *contexts):
        if contexts:
            with self._REGISTRY.context(*contexts):
                return self._REGISTRY.get_compatible_units(self)

        return self._REGISTRY.get_compatible_units(self)

    def is_compatible_with(
        self, other: Any, *contexts: str | Context, **ctx_kwargs: Any
    ) -> bool:
        """check if the other object is compatible

        Parameters
        ----------
        other
            The object to check. Treated as dimensionless if not a
            Quantity, PlainUnit or str.
        *contexts : str or pint.Context
            Contexts to use in the transformation.
        **ctx_kwargs :
            Values for the Context/s

        Returns
        -------
        bool
        """
        from .quantity import PlainQuantity

        if contexts or self._REGISTRY._active_ctx:
            try:
                (1 * self).to(other, *contexts, **ctx_kwargs)
                return True
            except DimensionalityError:
                return False

        if isinstance(other, (PlainQuantity, PlainUnit)):
            return self.dimensionality == other.dimensionality

        if isinstance(other, str):
            return (
                self.dimensionality == self._REGISTRY.parse_units(other).dimensionality
            )

        return self.dimensionless

    def __mul__(self, other):
        if self._check(other):
            if isinstance(other, self.__class__):
                return self.__class__(self._units * other._units)
            else:
                qself = self._REGISTRY.Quantity(1, self._units)
                return qself * other

        if isinstance(other, Number) and other == 1:
            return self._REGISTRY.Quantity(other, self._units)

        return self._REGISTRY.Quantity(1, self._units) * other

    __rmul__ = __mul__

    def __truediv__(self, other):
        if self._check(other):
            if isinstance(other, self.__class__):
                return self.__class__(self._units / other._units)
            else:
                qself = 1 * self
                return qself / other

        return self._REGISTRY.Quantity(1 / other, self._units)

    def __rtruediv__(self, other):
        # As PlainUnit and Quantity both handle truediv with each other rtruediv can
        # only be called for something different.
        if isinstance(other, NUMERIC_TYPES):
            return self._REGISTRY.Quantity(other, 1 / self._units)
        elif isinstance(other, UnitsContainer):
            return self.__class__(other / self._units)

        return NotImplemented

    __div__ = __truediv__
    __rdiv__ = __rtruediv__

    def __pow__(self, other) -> PlainUnit:
        if isinstance(other, NUMERIC_TYPES):
            return self.__class__(self._units**other)

        else:
            mess = f"Cannot power PlainUnit by {type(other)}"
            raise TypeError(mess)

    def __hash__(self) -> int:
        return self._units.__hash__()

    def __eq__(self, other) -> bool:
        # We compare to the plain class of PlainUnit because each PlainUnit class is
        # unique.
        if self._check(other):
            if isinstance(other, self.__class__):
                return self._units == other._units
            else:
                return other == self._REGISTRY.Quantity(1, self._units)

        elif isinstance(other, NUMERIC_TYPES):
            return other == self._REGISTRY.Quantity(1, self._units)

        else:
            return self._units == other

    def __ne__(self, other) -> bool:
        return not (self == other)

    def compare(self, other, op) -> bool:
        self_q = self._REGISTRY.Quantity(1, self)

        if isinstance(other, NUMERIC_TYPES):
            return self_q.compare(other, op)
        elif isinstance(other, (PlainUnit, UnitsContainer, dict)):
            return self_q.compare(self._REGISTRY.Quantity(1, other), op)

        return NotImplemented

    __lt__ = lambda self, other: self.compare(other, op=operator.lt)
    __le__ = lambda self, other: self.compare(other, op=operator.le)
    __ge__ = lambda self, other: self.compare(other, op=operator.ge)
    __gt__ = lambda self, other: self.compare(other, op=operator.gt)

    def __int__(self) -> int:
        return int(self._REGISTRY.Quantity(1, self._units))

    def __float__(self) -> float:
        return float(self._REGISTRY.Quantity(1, self._units))

    def __complex__(self) -> complex:
        return complex(self._REGISTRY.Quantity(1, self._units))

    @property
    def systems(self):
        out = set()
        for uname in self._units.keys():
            for sname, sys in self._REGISTRY._systems.items():
                if uname in sys.members:
                    out.add(sname)
        return frozenset(out)

    def from_(self, value, strict=True, name="value"):
        """Converts a numerical value or quantity to this unit

        Parameters
        ----------
        value :
            a Quantity (or numerical value if strict=False) to convert
        strict :
            boolean to indicate that only quantities are accepted (Default value = True)
        name :
            descriptive name to use if an exception occurs (Default value = "value")

        Returns
        -------
        type
            The converted value as this unit

        """
        if self._check(value):
            if not isinstance(value, self._REGISTRY.Quantity):
                value = self._REGISTRY.Quantity(1, value)
            return value.to(self)
        elif strict:
            raise ValueError("%s must be a Quantity" % value)
        else:
            return value * self

    def m_from(self, value, strict=True, name="value"):
        """Converts a numerical value or quantity to this unit, then returns
        the magnitude of the converted value

        Parameters
        ----------
        value :
            a Quantity (or numerical value if strict=False) to convert
        strict :
            boolean to indicate that only quantities are accepted (Default value = True)
        name :
            descriptive name to use if an exception occurs (Default value = "value")

        Returns
        -------
        type
            The magnitude of the converted value

        """
        return self.from_(value, strict=strict, name=name).magnitude
