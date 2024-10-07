"""
    pint.facets.plain.quantity
    ~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import copy
import datetime
import locale
import numbers
import operator
from collections.abc import Callable, Iterator, Sequence
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Iterable,
    TypeVar,
    overload,
)

from ..._typing import Magnitude, QuantityOrUnitLike, Scalar, UnitLike
from ...compat import (
    HAS_NUMPY,
    _to_magnitude,
    deprecated,
    eq,
    is_duck_array_type,
    is_upcast_type,
    np,
    zero_or_nan,
)
from ...errors import DimensionalityError, OffsetUnitCalculusError, PintTypeError
from ...util import (
    PrettyIPython,
    SharedRegistryObject,
    UnitsContainer,
    logger,
    to_units_container,
)
from . import qto
from .definitions import UnitDefinition

if TYPE_CHECKING:
    from ..context import Context
    from .unit import PlainUnit as Unit
    from .unit import UnitsContainer as UnitsContainerT

    if HAS_NUMPY:
        import numpy as np  # noqa

try:
    import uncertainties.unumpy as unp
    from uncertainties import UFloat, ufloat

    HAS_UNCERTAINTIES = True
except ImportError:
    unp = np
    ufloat = Ufloat = None
    HAS_UNCERTAINTIES = False


MagnitudeT = TypeVar("MagnitudeT", bound=Magnitude)
ScalarT = TypeVar("ScalarT", bound=Scalar)

T = TypeVar("T", bound=Magnitude)


def ireduce_dimensions(f):
    def wrapped(self, *args, **kwargs):
        result = f(self, *args, **kwargs)
        try:
            if result._REGISTRY.autoconvert_to_preferred:
                result.ito_preferred()
        except AttributeError:
            pass

        try:
            if result._REGISTRY.auto_reduce_dimensions:
                result.ito_reduced_units()
        except AttributeError:
            pass
        return result

    return wrapped


def check_implemented(f):
    def wrapped(self, *args, **kwargs):
        other = args[0]
        if is_upcast_type(type(other)):
            return NotImplemented
        # pandas often gets to arrays of quantities [ Q_(1,"m"), Q_(2,"m")]
        # and expects PlainQuantity * array[PlainQuantity] should return NotImplemented
        elif isinstance(other, list) and other and isinstance(other[0], type(self)):
            return NotImplemented
        return f(self, *args, **kwargs)

    return wrapped


def method_wraps(numpy_func):
    if isinstance(numpy_func, str):
        numpy_func = getattr(np, numpy_func, None)

    def wrapper(func):
        func.__wrapped__ = numpy_func

        return func

    return wrapper


# TODO: remove all nonmultiplicative remnants


class PlainQuantity(Generic[MagnitudeT], PrettyIPython, SharedRegistryObject):
    """Implements a class to describe a physical quantity:
    the product of a numerical value and a unit of measurement.

    Parameters
    ----------
    value : str, pint.PlainQuantity or any numeric type
        Value of the physical quantity to be created.
    units : UnitsContainer, str or pint.PlainQuantity
        Units of the physical quantity to be created.

    Returns
    -------

    """

    _magnitude: MagnitudeT

    @property
    def ndim(self) -> int:
        if isinstance(self.magnitude, numbers.Number):
            return 0
        if str(type(self.magnitude)) == "NAType":
            return 0
        return self.magnitude.ndim

    @property
    def force_ndarray(self) -> bool:
        return self._REGISTRY.force_ndarray

    @property
    def force_ndarray_like(self) -> bool:
        return self._REGISTRY.force_ndarray_like

    def __reduce__(self) -> tuple[type, Magnitude, UnitsContainer]:
        """Allow pickling quantities. Since UnitRegistries are not pickled, upon
        unpickling the new object is always attached to the application registry.
        """
        from datasette.vendored.pint import _unpickle_quantity

        # Note: type(self) would be a mistake as subclasses built by
        # dinamically can't be pickled
        # TODO: Check if this is still the case.
        return _unpickle_quantity, (PlainQuantity, self.magnitude, self._units)

    @overload
    def __new__(
        cls, value: MagnitudeT, units: UnitLike | None = None
    ) -> PlainQuantity[MagnitudeT]: ...

    @overload
    def __new__(
        cls, value: str, units: UnitLike | None = None
    ) -> PlainQuantity[Any]: ...

    @overload
    def __new__(  # type: ignore[misc]
        cls, value: Sequence[ScalarT], units: UnitLike | None = None
    ) -> PlainQuantity[Any]: ...

    @overload
    def __new__(
        cls, value: PlainQuantity[Any], units: UnitLike | None = None
    ) -> PlainQuantity[Any]: ...

    def __new__(cls, value, units=None):
        if is_upcast_type(type(value)):
            raise TypeError(f"PlainQuantity cannot wrap upcast type {type(value)}")

        if units is None and isinstance(value, str) and value == "":
            raise ValueError(
                "Expression to parse as PlainQuantity cannot be an empty string."
            )

        if units is None and isinstance(value, str):
            ureg = SharedRegistryObject.__new__(cls)._REGISTRY
            inst = ureg.parse_expression(value)
            return cls.__new__(cls, inst)

        if units is None and isinstance(value, cls):
            return copy.copy(value)

        inst = SharedRegistryObject().__new__(cls)
        if units is None:
            units = inst.UnitsContainer()
        else:
            if isinstance(units, (UnitsContainer, UnitDefinition)):
                units = units
            elif isinstance(units, str):
                units = inst._REGISTRY.parse_units(units)._units
            elif isinstance(units, SharedRegistryObject):
                if isinstance(units, PlainQuantity) and units.magnitude != 1:
                    units = copy.copy(units)._units
                    logger.warning(
                        "Creating new PlainQuantity using a non unity PlainQuantity as units."
                    )
                else:
                    units = units._units
            else:
                raise TypeError(
                    "units must be of type str, PlainQuantity or "
                    "UnitsContainer; not {}.".format(type(units))
                )
        if isinstance(value, cls):
            magnitude = value.to(units)._magnitude
        else:
            magnitude = _to_magnitude(
                value, inst.force_ndarray, inst.force_ndarray_like
            )
        inst._magnitude = magnitude
        inst._units = units

        return inst

    def __iter__(self: PlainQuantity[MagnitudeT]) -> Iterator[Any]:
        # Make sure that, if self.magnitude is not iterable, we raise TypeError as soon
        # as one calls iter(self) without waiting for the first element to be drawn from
        # the iterator
        it_magnitude = iter(self.magnitude)

        def it_outer():
            for element in it_magnitude:
                yield self.__class__(element, self._units)

        return it_outer()

    def __copy__(self) -> PlainQuantity[MagnitudeT]:
        ret = self.__class__(copy.copy(self._magnitude), self._units)
        return ret

    def __deepcopy__(self, memo) -> PlainQuantity[MagnitudeT]:
        ret = self.__class__(
            copy.deepcopy(self._magnitude, memo), copy.deepcopy(self._units, memo)
        )
        return ret

    @deprecated(
        "This function will be removed in future versions of pint.\n"
        "Use ureg.formatter.format_quantity_babel"
    )
    def format_babel(self, spec: str = "", **kwspec: Any) -> str:
        return self._REGISTRY.formatter.format_quantity_babel(self, spec, **kwspec)

    def __format__(self, spec: str) -> str:
        return self._REGISTRY.formatter.format_quantity(self, spec)

    def __str__(self) -> str:
        return self._REGISTRY.formatter.format_quantity(self)

    def __bytes__(self) -> bytes:
        return str(self).encode(locale.getpreferredencoding())

    def __repr__(self) -> str:
        if HAS_UNCERTAINTIES:
            if isinstance(self._magnitude, UFloat):
                return f"<Quantity({self._magnitude:.6}, '{self._units}')>"
            else:
                return f"<Quantity({self._magnitude}, '{self._units}')>"
        elif isinstance(self._magnitude, float):
            return f"<Quantity({self._magnitude:.9}, '{self._units}')>"

        return f"<Quantity({self._magnitude}, '{self._units}')>"

    def __hash__(self) -> int:
        self_base = self.to_base_units()
        if self_base.dimensionless:
            return hash(self_base.magnitude)

        return hash((self_base.__class__, self_base.magnitude, self_base.units))

    @property
    def magnitude(self) -> MagnitudeT:
        """PlainQuantity's magnitude. Long form for `m`"""
        return self._magnitude

    @property
    def m(self) -> MagnitudeT:
        """PlainQuantity's magnitude. Short form for `magnitude`"""
        return self._magnitude

    def m_as(self, units) -> MagnitudeT:
        """PlainQuantity's magnitude expressed in particular units.

        Parameters
        ----------
        units : pint.PlainQuantity, str or dict
            destination units

        Returns
        -------

        """
        return self.to(units).magnitude

    @property
    def units(self) -> Unit:
        """PlainQuantity's units. Long form for `u`"""
        return self._REGISTRY.Unit(self._units)

    @property
    def u(self) -> Unit:
        """PlainQuantity's units. Short form for `units`"""
        return self._REGISTRY.Unit(self._units)

    @property
    def unitless(self) -> bool:
        """ """
        return not bool(self.to_root_units()._units)

    def unit_items(self) -> Iterable[tuple[str, Scalar]]:
        """A view of the unit items."""
        return self._units.unit_items()

    @property
    def dimensionless(self) -> bool:
        """ """
        tmp = self.to_root_units()

        return not bool(tmp.dimensionality)

    _dimensionality: UnitsContainerT | None = None

    @property
    def dimensionality(self) -> UnitsContainerT:
        """
        Returns
        -------
        dict
            Dimensionality of the PlainQuantity, e.g. ``{length: 1, time: -1}``
        """
        if self._dimensionality is None:
            self._dimensionality = self._REGISTRY._get_dimensionality(self._units)

        return self._dimensionality

    def check(self, dimension: UnitLike) -> bool:
        """Return true if the quantity's dimension matches passed dimension."""
        return self.dimensionality == self._REGISTRY.get_dimensionality(dimension)

    @classmethod
    def from_list(
        cls, quant_list: list[PlainQuantity[MagnitudeT]], units=None
    ) -> PlainQuantity[MagnitudeT]:
        """Transforms a list of Quantities into an numpy.array quantity.
        If no units are specified, the unit of the first element will be used.
        Same as from_sequence.

        If units is not specified and list is empty, the unit cannot be determined
        and a ValueError is raised.

        Parameters
        ----------
        quant_list : list of pint.PlainQuantity
            list of pint.PlainQuantity
        units : UnitsContainer, str or pint.PlainQuantity
            units of the physical quantity to be created (Default value = None)

        Returns
        -------
        pint.PlainQuantity
        """
        return cls.from_sequence(quant_list, units=units)

    @classmethod
    def from_sequence(
        cls, seq: Sequence[PlainQuantity[MagnitudeT]], units=None
    ) -> PlainQuantity[MagnitudeT]:
        """Transforms a sequence of Quantities into an numpy.array quantity.
        If no units are specified, the unit of the first element will be used.

        If units is not specified and sequence is empty, the unit cannot be determined
        and a ValueError is raised.

        Parameters
        ----------
        seq : sequence of pint.PlainQuantity
            sequence of pint.PlainQuantity
        units : UnitsContainer, str or pint.PlainQuantity
            units of the physical quantity to be created (Default value = None)

        Returns
        -------
        pint.PlainQuantity
        """

        len_seq = len(seq)
        if units is None:
            if len_seq:
                units = seq[0].u
            else:
                raise ValueError("Cannot determine units from empty sequence!")

        a = np.empty(len_seq)

        for i, seq_i in enumerate(seq):
            a[i] = seq_i.m_as(units)
            # raises DimensionalityError if incompatible units are used in the sequence

        return cls(a, units)

    @classmethod
    def from_tuple(cls, tup):
        return cls(tup[0], cls._REGISTRY.UnitsContainer(tup[1]))

    def to_tuple(self) -> tuple[MagnitudeT, tuple[tuple[str, ...]]]:
        return self.m, tuple(self._units.items())

    def compatible_units(self, *contexts):
        if contexts:
            with self._REGISTRY.context(*contexts):
                return self._REGISTRY.get_compatible_units(self._units)

        return self._REGISTRY.get_compatible_units(self._units)

    def is_compatible_with(
        self, other: Any, *contexts: str | Context, **ctx_kwargs: Any
    ) -> bool:
        """check if the other object is compatible

        Parameters
        ----------
        other
            The object to check. Treated as dimensionless if not a
            PlainQuantity, Unit or str.
        *contexts : str or pint.Context
            Contexts to use in the transformation.
        **ctx_kwargs :
            Values for the Context/s

        Returns
        -------
        bool
        """
        from .unit import PlainUnit

        if contexts or self._REGISTRY._active_ctx:
            try:
                self.to(other, *contexts, **ctx_kwargs)
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

    def _convert_magnitude_not_inplace(self, other, *contexts, **ctx_kwargs):
        if contexts:
            with self._REGISTRY.context(*contexts, **ctx_kwargs):
                return self._REGISTRY.convert(self._magnitude, self._units, other)

        return self._REGISTRY.convert(self._magnitude, self._units, other)

    def _convert_magnitude(self, other, *contexts, **ctx_kwargs):
        if contexts:
            with self._REGISTRY.context(*contexts, **ctx_kwargs):
                return self._REGISTRY.convert(self._magnitude, self._units, other)

        return self._REGISTRY.convert(
            self._magnitude,
            self._units,
            other,
            inplace=is_duck_array_type(type(self._magnitude)),
        )

    def ito(
        self, other: QuantityOrUnitLike | None = None, *contexts, **ctx_kwargs
    ) -> None:
        """Inplace rescale to different units.

        Parameters
        ----------
        other : pint.PlainQuantity, str or dict
            Destination units. (Default value = None)
        *contexts : str or pint.Context
            Contexts to use in the transformation.
        **ctx_kwargs :
            Values for the Context/s
        """

        other = to_units_container(other, self._REGISTRY)

        self._magnitude = self._convert_magnitude(other, *contexts, **ctx_kwargs)
        self._units = other

        return None

    def to(
        self, other: QuantityOrUnitLike | None = None, *contexts, **ctx_kwargs
    ) -> PlainQuantity:
        """Return PlainQuantity rescaled to different units.

        Parameters
        ----------
        other : pint.PlainQuantity, str or dict
            destination units. (Default value = None)
        *contexts : str or pint.Context
            Contexts to use in the transformation.
        **ctx_kwargs :
            Values for the Context/s

        Returns
        -------
        pint.PlainQuantity
        """
        other = to_units_container(other, self._REGISTRY)

        magnitude = self._convert_magnitude_not_inplace(other, *contexts, **ctx_kwargs)

        return self.__class__(magnitude, other)

    def ito_root_units(self) -> None:
        """Return PlainQuantity rescaled to root units."""

        _, other = self._REGISTRY._get_root_units(self._units)

        self._magnitude = self._convert_magnitude(other)
        self._units = other

        return None

    def to_root_units(self) -> PlainQuantity[MagnitudeT]:
        """Return PlainQuantity rescaled to root units."""

        _, other = self._REGISTRY._get_root_units(self._units)

        magnitude = self._convert_magnitude_not_inplace(other)

        return self.__class__(magnitude, other)

    def ito_base_units(self) -> None:
        """Return PlainQuantity rescaled to plain units."""

        _, other = self._REGISTRY._get_base_units(self._units)

        self._magnitude = self._convert_magnitude(other)
        self._units = other

        return None

    def to_base_units(self) -> PlainQuantity[MagnitudeT]:
        """Return PlainQuantity rescaled to plain units."""

        _, other = self._REGISTRY._get_base_units(self._units)

        magnitude = self._convert_magnitude_not_inplace(other)

        return self.__class__(magnitude, other)

    # Functions not essential to a Quantity but it is
    # convenient that they live in PlainQuantity.
    # They are implemented elsewhere to keep Quantity class clean.
    to_compact = qto.to_compact
    to_preferred = qto.to_preferred
    ito_preferred = qto.ito_preferred
    to_reduced_units = qto.to_reduced_units
    ito_reduced_units = qto.ito_reduced_units

    # Mathematical operations
    def __int__(self) -> int:
        if self.dimensionless:
            return int(self._convert_magnitude_not_inplace(UnitsContainer()))
        raise DimensionalityError(self._units, "dimensionless")

    def __float__(self) -> float:
        if self.dimensionless:
            return float(self._convert_magnitude_not_inplace(UnitsContainer()))
        raise DimensionalityError(self._units, "dimensionless")

    def __complex__(self) -> complex:
        if self.dimensionless:
            return complex(self._convert_magnitude_not_inplace(UnitsContainer()))
        raise DimensionalityError(self._units, "dimensionless")

    @check_implemented
    def _iadd_sub(self, other, op):
        """Perform addition or subtraction operation in-place and return the result.

        Parameters
        ----------
        other : pint.PlainQuantity or any type accepted by :func:`_to_magnitude`
            object to be added to / subtracted from self
        op : function
            operator function (e.g. operator.add, operator.isub)

        """
        if not self._check(other):
            # other not from same Registry or not a PlainQuantity
            try:
                other_magnitude = _to_magnitude(
                    other, self.force_ndarray, self.force_ndarray_like
                )
            except PintTypeError:
                raise
            except TypeError:
                return NotImplemented
            if zero_or_nan(other, True):
                # If the other value is 0 (but not PlainQuantity 0)
                # do the operation without checking units.
                # We do the calculation instead of just returning the same
                # value to enforce any shape checking and type casting due to
                # the operation.
                self._magnitude = op(self._magnitude, other_magnitude)
            elif self.dimensionless:
                self.ito(self.UnitsContainer())
                self._magnitude = op(self._magnitude, other_magnitude)
            else:
                raise DimensionalityError(self._units, "dimensionless")
            return self

        if not self.dimensionality == other.dimensionality:
            raise DimensionalityError(
                self._units, other._units, self.dimensionality, other.dimensionality
            )

        # Next we define some variables to make if-clauses more readable.
        self_non_mul_units = self._get_non_multiplicative_units()
        is_self_multiplicative = len(self_non_mul_units) == 0
        if len(self_non_mul_units) == 1:
            self_non_mul_unit = self_non_mul_units[0]
        other_non_mul_units = other._get_non_multiplicative_units()
        is_other_multiplicative = len(other_non_mul_units) == 0
        if len(other_non_mul_units) == 1:
            other_non_mul_unit = other_non_mul_units[0]

        # Presence of non-multiplicative units gives rise to several cases.
        if is_self_multiplicative and is_other_multiplicative:
            if self._units == other._units:
                self._magnitude = op(self._magnitude, other._magnitude)
            # If only self has a delta unit, other determines unit of result.
            elif self._get_delta_units() and not other._get_delta_units():
                self._magnitude = op(
                    self._convert_magnitude(other._units), other._magnitude
                )
                self._units = other._units
            else:
                self._magnitude = op(self._magnitude, other.to(self._units)._magnitude)

        elif (
            op == operator.isub
            and len(self_non_mul_units) == 1
            and self._units[self_non_mul_unit] == 1
            and not other._has_compatible_delta(self_non_mul_unit)
        ):
            if self._units == other._units:
                self._magnitude = op(self._magnitude, other._magnitude)
            else:
                self._magnitude = op(self._magnitude, other.to(self._units)._magnitude)
            self._units = self._units.rename(
                self_non_mul_unit, "delta_" + self_non_mul_unit
            )

        elif (
            op == operator.isub
            and len(other_non_mul_units) == 1
            and other._units[other_non_mul_unit] == 1
            and not self._has_compatible_delta(other_non_mul_unit)
        ):
            # we convert to self directly since it is multiplicative
            self._magnitude = op(self._magnitude, other.to(self._units)._magnitude)

        elif (
            len(self_non_mul_units) == 1
            # order of the dimension of offset unit == 1 ?
            and self._units[self_non_mul_unit] == 1
            and other._has_compatible_delta(self_non_mul_unit)
        ):
            # Replace offset unit in self by the corresponding delta unit.
            # This is done to prevent a shift by offset in the to()-call.
            tu = self._units.rename(self_non_mul_unit, "delta_" + self_non_mul_unit)
            self._magnitude = op(self._magnitude, other.to(tu)._magnitude)
        elif (
            len(other_non_mul_units) == 1
            # order of the dimension of offset unit == 1 ?
            and other._units[other_non_mul_unit] == 1
            and self._has_compatible_delta(other_non_mul_unit)
        ):
            # Replace offset unit in other by the corresponding delta unit.
            # This is done to prevent a shift by offset in the to()-call.
            tu = other._units.rename(other_non_mul_unit, "delta_" + other_non_mul_unit)
            self._magnitude = op(self._convert_magnitude(tu), other._magnitude)
            self._units = other._units
        else:
            raise OffsetUnitCalculusError(self._units, other._units)

        return self

    @check_implemented
    def _add_sub(self, other, op):
        """Perform addition or subtraction operation and return the result.

        Parameters
        ----------
        other : pint.PlainQuantity or any type accepted by :func:`_to_magnitude`
            object to be added to / subtracted from self
        op : function
            operator function (e.g. operator.add, operator.isub)
        """
        if not self._check(other):
            # other not from same Registry or not a PlainQuantity
            if zero_or_nan(other, True):
                # If the other value is 0 or NaN (but not a PlainQuantity)
                # do the operation without checking units.
                # We do the calculation instead of just returning the same
                # value to enforce any shape checking and type casting due to
                # the operation.
                units = self._units
                magnitude = op(
                    self._magnitude,
                    _to_magnitude(other, self.force_ndarray, self.force_ndarray_like),
                )
            elif self.dimensionless:
                units = self.UnitsContainer()
                magnitude = op(
                    self.to(units)._magnitude,
                    _to_magnitude(other, self.force_ndarray, self.force_ndarray_like),
                )
            else:
                raise DimensionalityError(self._units, "dimensionless")
            return self.__class__(magnitude, units)

        if not self.dimensionality == other.dimensionality:
            raise DimensionalityError(
                self._units, other._units, self.dimensionality, other.dimensionality
            )

        # Next we define some variables to make if-clauses more readable.
        self_non_mul_units = self._get_non_multiplicative_units()
        is_self_multiplicative = len(self_non_mul_units) == 0
        if len(self_non_mul_units) == 1:
            self_non_mul_unit = self_non_mul_units[0]
        other_non_mul_units = other._get_non_multiplicative_units()
        is_other_multiplicative = len(other_non_mul_units) == 0
        if len(other_non_mul_units) == 1:
            other_non_mul_unit = other_non_mul_units[0]

        # Presence of non-multiplicative units gives rise to several cases.
        if is_self_multiplicative and is_other_multiplicative:
            if self._units == other._units:
                magnitude = op(self._magnitude, other._magnitude)
                units = self._units
            # If only self has a delta unit, other determines unit of result.
            elif self._get_delta_units() and not other._get_delta_units():
                magnitude = op(
                    self._convert_magnitude_not_inplace(other._units), other._magnitude
                )
                units = other._units
            else:
                units = self._units
                magnitude = op(self._magnitude, other.to(self._units).magnitude)

        elif (
            op == operator.sub
            and len(self_non_mul_units) == 1
            and self._units[self_non_mul_unit] == 1
            and not other._has_compatible_delta(self_non_mul_unit)
        ):
            if self._units == other._units:
                magnitude = op(self._magnitude, other._magnitude)
            else:
                magnitude = op(self._magnitude, other.to(self._units)._magnitude)
            units = self._units.rename(self_non_mul_unit, "delta_" + self_non_mul_unit)

        elif (
            op == operator.sub
            and len(other_non_mul_units) == 1
            and other._units[other_non_mul_unit] == 1
            and not self._has_compatible_delta(other_non_mul_unit)
        ):
            # we convert to self directly since it is multiplicative
            magnitude = op(self._magnitude, other.to(self._units)._magnitude)
            units = self._units

        elif (
            len(self_non_mul_units) == 1
            # order of the dimension of offset unit == 1 ?
            and self._units[self_non_mul_unit] == 1
            and other._has_compatible_delta(self_non_mul_unit)
        ):
            # Replace offset unit in self by the corresponding delta unit.
            # This is done to prevent a shift by offset in the to()-call.
            tu = self._units.rename(self_non_mul_unit, "delta_" + self_non_mul_unit)
            magnitude = op(self._magnitude, other.to(tu).magnitude)
            units = self._units
        elif (
            len(other_non_mul_units) == 1
            # order of the dimension of offset unit == 1 ?
            and other._units[other_non_mul_unit] == 1
            and self._has_compatible_delta(other_non_mul_unit)
        ):
            # Replace offset unit in other by the corresponding delta unit.
            # This is done to prevent a shift by offset in the to()-call.
            tu = other._units.rename(other_non_mul_unit, "delta_" + other_non_mul_unit)
            magnitude = op(self._convert_magnitude_not_inplace(tu), other._magnitude)
            units = other._units
        else:
            raise OffsetUnitCalculusError(self._units, other._units)

        return self.__class__(magnitude, units)

    @overload
    def __iadd__(self, other: datetime.datetime) -> datetime.timedelta:  # type: ignore[misc]
        ...

    @overload
    def __iadd__(self, other) -> PlainQuantity[MagnitudeT]: ...

    def __iadd__(self, other):
        if isinstance(other, datetime.datetime):
            return self.to_timedelta() + other
        elif is_duck_array_type(type(self._magnitude)):
            return self._iadd_sub(other, operator.iadd)

        return self._add_sub(other, operator.add)

    def __add__(self, other):
        if isinstance(other, datetime.datetime):
            return self.to_timedelta() + other

        return self._add_sub(other, operator.add)

    __radd__ = __add__

    def __isub__(self, other):
        if is_duck_array_type(type(self._magnitude)):
            return self._iadd_sub(other, operator.isub)

        return self._add_sub(other, operator.sub)

    def __sub__(self, other):
        return self._add_sub(other, operator.sub)

    def __rsub__(self, other):
        if isinstance(other, datetime.datetime):
            return other - self.to_timedelta()

        return -self._add_sub(other, operator.sub)

    @check_implemented
    @ireduce_dimensions
    def _imul_div(self, other, magnitude_op, units_op=None):
        """Perform multiplication or division operation in-place and return the
        result.

        Parameters
        ----------
        other : pint.PlainQuantity or any type accepted by :func:`_to_magnitude`
            object to be multiplied/divided with self
        magnitude_op : function
            operator function to perform on the magnitudes
            (e.g. operator.mul)
        units_op : function or None
            operator function to perform on the units; if None,
            *magnitude_op* is used (Default value = None)

        Returns
        -------

        """
        if units_op is None:
            units_op = magnitude_op

        offset_units_self = self._get_non_multiplicative_units()
        no_offset_units_self = len(offset_units_self)

        if not self._check(other):
            if not self._ok_for_muldiv(no_offset_units_self):
                raise OffsetUnitCalculusError(self._units, getattr(other, "units", ""))
            if len(offset_units_self) == 1:
                if self._units[offset_units_self[0]] != 1 or magnitude_op not in (
                    operator.mul,
                    operator.imul,
                ):
                    raise OffsetUnitCalculusError(
                        self._units, getattr(other, "units", "")
                    )
            try:
                other_magnitude = _to_magnitude(
                    other, self.force_ndarray, self.force_ndarray_like
                )
            except PintTypeError:
                raise
            except TypeError:
                return NotImplemented
            self._magnitude = magnitude_op(self._magnitude, other_magnitude)
            self._units = units_op(self._units, self.UnitsContainer())
            return self

        if isinstance(other, self._REGISTRY.Unit):
            other = 1 * other

        if not self._ok_for_muldiv(no_offset_units_self):
            raise OffsetUnitCalculusError(self._units, other._units)
        elif no_offset_units_self == len(self._units) == 1:
            self.ito_root_units()

        no_offset_units_other = len(other._get_non_multiplicative_units())

        if not other._ok_for_muldiv(no_offset_units_other):
            raise OffsetUnitCalculusError(self._units, other._units)
        elif no_offset_units_other == len(other._units) == 1:
            other.ito_root_units()

        self._magnitude = magnitude_op(self._magnitude, other._magnitude)
        self._units = units_op(self._units, other._units)

        return self

    @check_implemented
    @ireduce_dimensions
    def _mul_div(self, other, magnitude_op, units_op=None):
        """Perform multiplication or division operation and return the result.

        Parameters
        ----------
        other : pint.PlainQuantity or any type accepted by :func:`_to_magnitude`
            object to be multiplied/divided with self
        magnitude_op : function
            operator function to perform on the magnitudes
            (e.g. operator.mul)
        units_op : function or None
            operator function to perform on the units; if None,
            *magnitude_op* is used (Default value = None)

        Returns
        -------

        """
        if units_op is None:
            units_op = magnitude_op

        offset_units_self = self._get_non_multiplicative_units()
        no_offset_units_self = len(offset_units_self)

        if not self._check(other):
            if not self._ok_for_muldiv(no_offset_units_self):
                raise OffsetUnitCalculusError(self._units, getattr(other, "units", ""))
            if len(offset_units_self) == 1:
                if self._units[offset_units_self[0]] != 1 or magnitude_op not in (
                    operator.mul,
                    operator.imul,
                ):
                    raise OffsetUnitCalculusError(
                        self._units, getattr(other, "units", "")
                    )
            try:
                other_magnitude = _to_magnitude(
                    other, self.force_ndarray, self.force_ndarray_like
                )
            except PintTypeError:
                raise
            except TypeError:
                return NotImplemented

            magnitude = magnitude_op(self._magnitude, other_magnitude)
            units = units_op(self._units, self.UnitsContainer())

            return self.__class__(magnitude, units)

        if isinstance(other, self._REGISTRY.Unit):
            other = 1 * other

        new_self = self

        if not self._ok_for_muldiv(no_offset_units_self):
            raise OffsetUnitCalculusError(self._units, other._units)
        elif no_offset_units_self == len(self._units) == 1:
            new_self = self.to_root_units()

        no_offset_units_other = len(other._get_non_multiplicative_units())

        if not other._ok_for_muldiv(no_offset_units_other):
            raise OffsetUnitCalculusError(self._units, other._units)
        elif no_offset_units_other == len(other._units) == 1:
            other = other.to_root_units()

        magnitude = magnitude_op(new_self._magnitude, other._magnitude)
        units = units_op(new_self._units, other._units)

        return self.__class__(magnitude, units)

    def __imul__(self, other):
        if is_duck_array_type(type(self._magnitude)):
            return self._imul_div(other, operator.imul)

        return self._mul_div(other, operator.mul)

    def __mul__(self, other):
        return self._mul_div(other, operator.mul)

    __rmul__ = __mul__

    def __matmul__(self, other):
        return np.matmul(self, other)

    __rmatmul__ = __matmul__

    def _truedivide_cast_int(self, a, b):
        t = self._REGISTRY.non_int_type
        if isinstance(a, int):
            a = t(a)
        if isinstance(b, int):
            b = t(b)
        return operator.truediv(a, b)

    def __itruediv__(self, other):
        if is_duck_array_type(type(self._magnitude)):
            return self._imul_div(other, operator.itruediv)

        return self._mul_div(other, operator.truediv)

    def __truediv__(self, other):
        if isinstance(self.m, int) or isinstance(getattr(other, "m", None), int):
            return self._mul_div(other, self._truedivide_cast_int, operator.truediv)
        return self._mul_div(other, operator.truediv)

    def __rtruediv__(self, other):
        try:
            other_magnitude = _to_magnitude(
                other, self.force_ndarray, self.force_ndarray_like
            )
        except PintTypeError:
            raise
        except TypeError:
            return NotImplemented

        no_offset_units_self = len(self._get_non_multiplicative_units())
        if not self._ok_for_muldiv(no_offset_units_self):
            raise OffsetUnitCalculusError(self._units, "")
        elif no_offset_units_self == len(self._units) == 1:
            self = self.to_root_units()

        return self.__class__(other_magnitude / self._magnitude, 1 / self._units)

    __div__ = __truediv__
    __rdiv__ = __rtruediv__
    __idiv__ = __itruediv__

    def __ifloordiv__(self, other):
        if self._check(other):
            self._magnitude //= other.to(self._units)._magnitude
        elif self.dimensionless:
            self._magnitude = self.to("")._magnitude // other
        else:
            raise DimensionalityError(self._units, "dimensionless")
        self._units = self.UnitsContainer({})
        return self

    @check_implemented
    def __floordiv__(self, other):
        if self._check(other):
            magnitude = self._magnitude // other.to(self._units)._magnitude
        elif self.dimensionless:
            magnitude = self.to("")._magnitude // other
        else:
            raise DimensionalityError(self._units, "dimensionless")
        return self.__class__(magnitude, self.UnitsContainer({}))

    @check_implemented
    def __rfloordiv__(self, other):
        if self._check(other):
            magnitude = other._magnitude // self.to(other._units)._magnitude
        elif self.dimensionless:
            magnitude = other // self.to("")._magnitude
        else:
            raise DimensionalityError(self._units, "dimensionless")
        return self.__class__(magnitude, self.UnitsContainer({}))

    @check_implemented
    def __imod__(self, other):
        if not self._check(other):
            other = self.__class__(other, self.UnitsContainer({}))
        self._magnitude %= other.to(self._units)._magnitude
        return self

    @check_implemented
    def __mod__(self, other):
        if not self._check(other):
            other = self.__class__(other, self.UnitsContainer({}))
        magnitude = self._magnitude % other.to(self._units)._magnitude
        return self.__class__(magnitude, self._units)

    @check_implemented
    def __rmod__(self, other):
        if self._check(other):
            magnitude = other._magnitude % self.to(other._units)._magnitude
            return self.__class__(magnitude, other._units)
        elif self.dimensionless:
            magnitude = other % self.to("")._magnitude
            return self.__class__(magnitude, self.UnitsContainer({}))
        else:
            raise DimensionalityError(self._units, "dimensionless")

    @check_implemented
    def __divmod__(self, other):
        if not self._check(other):
            other = self.__class__(other, self.UnitsContainer({}))
        q, r = divmod(self._magnitude, other.to(self._units)._magnitude)
        return (
            self.__class__(q, self.UnitsContainer({})),
            self.__class__(r, self._units),
        )

    @check_implemented
    def __rdivmod__(self, other):
        if self._check(other):
            q, r = divmod(other._magnitude, self.to(other._units)._magnitude)
            unit = other._units
        elif self.dimensionless:
            q, r = divmod(other, self.to("")._magnitude)
            unit = self.UnitsContainer({})
        else:
            raise DimensionalityError(self._units, "dimensionless")
        return (self.__class__(q, self.UnitsContainer({})), self.__class__(r, unit))

    @check_implemented
    def __ipow__(self, other):
        if not is_duck_array_type(type(self._magnitude)):
            return self.__pow__(other)

        try:
            _to_magnitude(other, self.force_ndarray, self.force_ndarray_like)
        except PintTypeError:
            raise
        except TypeError:
            return NotImplemented
        else:
            if not self._ok_for_muldiv:
                raise OffsetUnitCalculusError(self._units)

            if is_duck_array_type(type(getattr(other, "_magnitude", other))):
                # arrays are refused as exponent, because they would create
                # len(array) quantities of len(set(array)) different units
                # unless the plain is dimensionless. Ensure dimensionless
                # units are reduced to "dimensionless".
                # Note: this will strip Units of degrees or radians from PlainQuantity
                if self.dimensionless:
                    if getattr(other, "dimensionless", False):
                        self._magnitude = self.m_as("") ** other.m_as("")
                        self._units = self.UnitsContainer()
                        return self
                    elif not getattr(other, "dimensionless", True):
                        raise DimensionalityError(other._units, "dimensionless")
                    else:
                        self._magnitude = self.m_as("") ** other
                        self._units = self.UnitsContainer()
                        return self
                elif np.size(other) > 1:
                    raise DimensionalityError(
                        self._units,
                        "dimensionless",
                        extra_msg=". PlainQuantity array exponents are only allowed if the "
                        "plain is dimensionless",
                    )

            if other == 1:
                return self
            elif other == 0:
                self._units = self.UnitsContainer()
            else:
                if not self._is_multiplicative:
                    if self._REGISTRY.autoconvert_offset_to_baseunit:
                        self.ito_base_units()
                    else:
                        raise OffsetUnitCalculusError(self._units)

                if getattr(other, "dimensionless", False):
                    other = other.to_base_units().magnitude
                    self._units **= other
                elif not getattr(other, "dimensionless", True):
                    raise DimensionalityError(self._units, "dimensionless")
                else:
                    self._units **= other

            self._magnitude **= _to_magnitude(
                other, self.force_ndarray, self.force_ndarray_like
            )
            return self

    @check_implemented
    def __pow__(self, other) -> PlainQuantity[MagnitudeT]:
        try:
            _to_magnitude(other, self.force_ndarray, self.force_ndarray_like)
        except PintTypeError:
            raise
        except TypeError:
            return NotImplemented
        else:
            if not self._ok_for_muldiv:
                raise OffsetUnitCalculusError(self._units)

            if is_duck_array_type(type(getattr(other, "_magnitude", other))):
                # arrays are refused as exponent, because they would create
                # len(array) quantities of len(set(array)) different units
                # unless the plain is dimensionless.
                # Note: this will strip Units of degrees or radians from PlainQuantity
                if self.dimensionless:
                    if getattr(other, "dimensionless", False):
                        return self.__class__(
                            self._convert_magnitude_not_inplace(self.UnitsContainer())
                            ** other.m_as("")
                        )
                    elif not getattr(other, "dimensionless", True):
                        raise DimensionalityError(other._units, "dimensionless")
                    else:
                        return self.__class__(
                            self._convert_magnitude_not_inplace(self.UnitsContainer())
                            ** other
                        )
                elif np.size(other) > 1:
                    raise DimensionalityError(
                        self._units,
                        "dimensionless",
                        extra_msg=". PlainQuantity array exponents are only allowed if the "
                        "plain is dimensionless",
                    )

            new_self = self
            if other == 1:
                return self
            elif other == 0:
                exponent = 0
                units = self.UnitsContainer()
            else:
                if not self._is_multiplicative:
                    if self._REGISTRY.autoconvert_offset_to_baseunit:
                        new_self = self.to_root_units()
                    else:
                        raise OffsetUnitCalculusError(self._units)

                if getattr(other, "dimensionless", False):
                    exponent = other.to_root_units().magnitude
                    units = new_self._units**exponent
                elif not getattr(other, "dimensionless", True):
                    raise DimensionalityError(other._units, "dimensionless")
                else:
                    exponent = _to_magnitude(
                        other, force_ndarray=False, force_ndarray_like=False
                    )
                    units = new_self._units**exponent

            magnitude = new_self._magnitude**exponent
            return self.__class__(magnitude, units)

    @check_implemented
    def __rpow__(self, other) -> PlainQuantity[MagnitudeT]:
        try:
            _to_magnitude(other, self.force_ndarray, self.force_ndarray_like)
        except PintTypeError:
            raise
        except TypeError:
            return NotImplemented
        else:
            if not self.dimensionless:
                raise DimensionalityError(self._units, "dimensionless")
            new_self = self.to_root_units()
            return other**new_self._magnitude

    def __abs__(self) -> PlainQuantity[MagnitudeT]:
        return self.__class__(abs(self._magnitude), self._units)

    def __round__(self, ndigits: int | None = 0) -> PlainQuantity[MagnitudeT]:
        return self.__class__(round(self._magnitude, ndigits=ndigits), self._units)

    def __pos__(self) -> PlainQuantity[MagnitudeT]:
        return self.__class__(operator.pos(self._magnitude), self._units)

    def __neg__(self) -> PlainQuantity[MagnitudeT]:
        return self.__class__(operator.neg(self._magnitude), self._units)

    @check_implemented
    def __eq__(self, other):
        def bool_result(value):
            nonlocal other

            if not is_duck_array_type(type(self._magnitude)):
                return value

            if isinstance(other, PlainQuantity):
                other = other._magnitude

            template, _ = np.broadcast_arrays(self._magnitude, other)
            return np.full_like(template, fill_value=value, dtype=np.bool_)

        # We compare to the plain class of PlainQuantity because
        # each PlainQuantity class is unique.
        if not isinstance(other, PlainQuantity):
            if other is None:
                # A loop in pandas-dev/pandas/core/common.py(86)consensus_name_attr() can result in OTHER being None
                return bool_result(False)
            if zero_or_nan(other, True):
                # Handle the special case in which we compare to zero or NaN
                # (or an array of zeros or NaNs)
                if self._is_multiplicative:
                    # compare magnitude
                    return eq(self._magnitude, other, False)
                else:
                    # compare the magnitude after converting the
                    # non-multiplicative quantity to plain units
                    if self._REGISTRY.autoconvert_offset_to_baseunit:
                        return eq(self.to_base_units()._magnitude, other, False)
                    else:
                        raise OffsetUnitCalculusError(self._units)

            if self.dimensionless:
                return eq(
                    self._convert_magnitude_not_inplace(self.UnitsContainer()),
                    other,
                    False,
                )

            return bool_result(False)

        # TODO: this might be expensive. Do we even need it?
        if eq(self._magnitude, 0, True) and eq(other._magnitude, 0, True):
            return bool_result(self.dimensionality == other.dimensionality)

        if self._units == other._units:
            return eq(self._magnitude, other._magnitude, False)

        try:
            return eq(
                self._convert_magnitude_not_inplace(other._units),
                other._magnitude,
                False,
            )
        except DimensionalityError:
            return bool_result(False)

    @check_implemented
    def __ne__(self, other):
        out = self.__eq__(other)
        if is_duck_array_type(type(out)):
            return np.logical_not(out)
        return not out

    @check_implemented
    def compare(self, other, op):
        if not isinstance(other, PlainQuantity):
            if self.dimensionless:
                return op(
                    self._convert_magnitude_not_inplace(self.UnitsContainer()), other
                )
            elif zero_or_nan(other, True):
                # Handle the special case in which we compare to zero or NaN
                # (or an array of zeros or NaNs)
                if self._is_multiplicative:
                    # compare magnitude
                    return op(self._magnitude, other)
                else:
                    # compare the magnitude after converting the
                    # non-multiplicative quantity to plain units
                    if self._REGISTRY.autoconvert_offset_to_baseunit:
                        return op(self.to_base_units()._magnitude, other)
                    else:
                        raise OffsetUnitCalculusError(self._units)
            else:
                raise ValueError(f"Cannot compare PlainQuantity and {type(other)}")

        # Registry equality check based on util.SharedRegistryObject
        if self._REGISTRY is not other._REGISTRY:
            mess = "Cannot operate with {} and {} of different registries."
            raise ValueError(
                mess.format(self.__class__.__name__, other.__class__.__name__)
            )

        if self._units == other._units:
            return op(self._magnitude, other._magnitude)
        if self.dimensionality != other.dimensionality:
            raise DimensionalityError(
                self._units, other._units, self.dimensionality, other.dimensionality
            )
        return op(self.to_root_units().magnitude, other.to_root_units().magnitude)

    __lt__ = lambda self, other: self.compare(other, op=operator.lt)
    __le__ = lambda self, other: self.compare(other, op=operator.le)
    __ge__ = lambda self, other: self.compare(other, op=operator.ge)
    __gt__ = lambda self, other: self.compare(other, op=operator.gt)

    def __bool__(self) -> bool:
        # Only cast when non-ambiguous (when multiplicative unit)
        if self._is_multiplicative:
            return bool(self._magnitude)
        else:
            raise ValueError(
                "Boolean value of PlainQuantity with offset unit is ambiguous."
            )

    __nonzero__ = __bool__

    def tolist(self):
        units = self._units

        try:
            values = self._magnitude.tolist()
            if not isinstance(values, list):
                return self.__class__(values, units)

            return [
                (
                    self.__class__(value, units).tolist()
                    if isinstance(value, list)
                    else self.__class__(value, units)
                )
                for value in self._magnitude.tolist()
            ]
        except AttributeError:
            raise AttributeError(
                f"Magnitude '{type(self._magnitude).__name__}' does not support tolist."
            )

    def _get_unit_definition(self, unit: str) -> UnitDefinition:
        try:
            return self._REGISTRY._units[unit]
        except KeyError:
            # pint#1062: The __init__ method of this object added the unit to
            # UnitRegistry._units (e.g. units with prefix are added on the fly the
            # first time they're used) but the key was later removed, e.g. because
            # a Context with unit redefinitions was deactivated.
            self._REGISTRY.parse_units(unit)
            return self._REGISTRY._units[unit]

    # methods/properties that help for math operations with offset units
    @property
    def _is_multiplicative(self) -> bool:
        """Check if the PlainQuantity object has only multiplicative units."""
        return True

    def _get_non_multiplicative_units(self) -> list[str]:
        """Return a list of the of non-multiplicative units of the PlainQuantity object."""
        return []

    def _get_delta_units(self) -> list[str]:
        """Return list of delta units ot the PlainQuantity object."""
        return [u for u in self._units if u.startswith("delta_")]

    def _has_compatible_delta(self, unit: str) -> bool:
        """ "Check if PlainQuantity object has a delta_unit that is compatible with unit"""
        return False

    def _ok_for_muldiv(self, no_offset_units=None) -> bool:
        return True

    def to_timedelta(self: PlainQuantity[MagnitudeT]) -> datetime.timedelta:
        return datetime.timedelta(microseconds=self.to("microseconds").magnitude)

    # We put this last to avoid overriding UnitsContainer
    # and I do not want to rename it.
    # TODO: Maybe in the future we need to change it to a more meaningful
    # non-colliding name.

    @property
    def UnitsContainer(self) -> Callable[..., UnitsContainerT]:
        return self._REGISTRY.UnitsContainer
