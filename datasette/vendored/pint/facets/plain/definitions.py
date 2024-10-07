"""
    pint.facets.plain.definitions
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import itertools
import numbers
import typing as ty
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from ... import errors
from ..._typing import Magnitude
from ...converters import Converter
from ...util import UnitsContainer


class NotNumeric(Exception):
    """Internal exception. Do not expose outside Pint"""

    def __init__(self, value: Any):
        self.value = value


########################
# Convenience functions
########################


@dataclass(frozen=True)
class Equality:
    """An equality statement contains a left and right hand separated
    by and equal (=) sign.

        lhs = rhs

    lhs and rhs are space stripped.
    """

    lhs: str
    rhs: str


@dataclass(frozen=True)
class CommentDefinition:
    """A comment"""

    comment: str


@dataclass(frozen=True)
class DefaultsDefinition:
    """Directive to store default values."""

    group: ty.Optional[str]
    system: ty.Optional[str]

    def items(self):
        if self.group is not None:
            yield "group", self.group
        if self.system is not None:
            yield "system", self.system


@dataclass(frozen=True)
class NamedDefinition:
    #: name of the prefix
    name: str


@dataclass(frozen=True)
class PrefixDefinition(NamedDefinition, errors.WithDefErr):
    """Definition of a prefix."""

    #: scaling value for this prefix
    value: numbers.Number
    #: canonical symbol
    defined_symbol: str | None = ""
    #: additional names for the same prefix
    aliases: ty.Tuple[str, ...] = ()

    @property
    def symbol(self) -> str:
        return self.defined_symbol or self.name

    @property
    def has_symbol(self) -> bool:
        return bool(self.defined_symbol)

    @cached_property
    def converter(self) -> ScaleConverter:
        return ScaleConverter(self.value)

    def __post_init__(self):
        if not errors.is_valid_prefix_name(self.name):
            raise self.def_err(errors.MSG_INVALID_PREFIX_NAME)

        if self.defined_symbol and not errors.is_valid_prefix_symbol(self.name):
            raise self.def_err(
                f"the symbol {self.defined_symbol} " + errors.MSG_INVALID_PREFIX_SYMBOL
            )

        for alias in self.aliases:
            if not errors.is_valid_prefix_alias(alias):
                raise self.def_err(
                    f"the alias {alias} " + errors.MSG_INVALID_PREFIX_ALIAS
                )


@dataclass(frozen=True)
class UnitDefinition(NamedDefinition, errors.WithDefErr):
    """Definition of a unit."""

    #: canonical symbol
    defined_symbol: str | None
    #: additional names for the same unit
    aliases: tuple[str, ...]
    #: A functiont that converts a value in these units into the reference units
    # TODO: this has changed as converter is now annotated as converter.
    # Briefly, in several places converter attributes like as_multiplicative were
    # accesed. So having a generic function is a no go.
    # I guess this was never used as errors where not raised.
    converter: Converter | None
    #: Reference units.
    reference: UnitsContainer | None

    def __post_init__(self):
        if not errors.is_valid_unit_name(self.name):
            raise self.def_err(errors.MSG_INVALID_UNIT_NAME)

        # TODO: check why  reference: Optional[UnitsContainer]
        assert isinstance(self.reference, UnitsContainer)

        if not any(map(errors.is_dim, self.reference.keys())):
            invalid = tuple(
                itertools.filterfalse(errors.is_valid_unit_name, self.reference.keys())
            )
            if invalid:
                raise self.def_err(
                    f"refers to {', '.join(invalid)} that "
                    + errors.MSG_INVALID_UNIT_NAME
                )
            is_base = False

        elif all(map(errors.is_dim, self.reference.keys())):
            invalid = tuple(
                itertools.filterfalse(
                    errors.is_valid_dimension_name, self.reference.keys()
                )
            )
            if invalid:
                raise self.def_err(
                    f"refers to {', '.join(invalid)} that "
                    + errors.MSG_INVALID_DIMENSION_NAME
                )

            is_base = True
            scale = getattr(self.converter, "scale", 1)
            if scale != 1:
                return self.def_err(
                    "Base unit definitions cannot have a scale different to 1. "
                    f"(`{scale}` found)"
                )
        else:
            raise self.def_err(
                "Cannot mix dimensions and units in the same definition. "
                "Base units must be referenced only to dimensions. "
                "Derived units must be referenced only to units."
            )

        super.__setattr__(self, "_is_base", is_base)

        if self.defined_symbol and not errors.is_valid_unit_symbol(self.name):
            raise self.def_err(
                f"the symbol {self.defined_symbol} " + errors.MSG_INVALID_UNIT_SYMBOL
            )

        for alias in self.aliases:
            if not errors.is_valid_unit_alias(alias):
                raise self.def_err(
                    f"the alias {alias} " + errors.MSG_INVALID_UNIT_ALIAS
                )

    @property
    def is_base(self) -> bool:
        """Indicates if it is a base unit."""

        # TODO: This is set in __post_init__
        return self._is_base

    @property
    def is_multiplicative(self) -> bool:
        # TODO: Check how to avoid this check
        assert isinstance(self.converter, Converter)
        return self.converter.is_multiplicative

    @property
    def is_logarithmic(self) -> bool:
        # TODO: Check how to avoid this check
        assert isinstance(self.converter, Converter)
        return self.converter.is_logarithmic

    @property
    def symbol(self) -> str:
        return self.defined_symbol or self.name

    @property
    def has_symbol(self) -> bool:
        return bool(self.defined_symbol)


@dataclass(frozen=True)
class DimensionDefinition(NamedDefinition, errors.WithDefErr):
    """Definition of a root dimension"""

    @property
    def is_base(self) -> bool:
        return True

    def __post_init__(self) -> None:
        if not errors.is_valid_dimension_name(self.name):
            raise self.def_err(errors.MSG_INVALID_DIMENSION_NAME)


@dataclass(frozen=True)
class DerivedDimensionDefinition(DimensionDefinition):
    """Definition of a derived dimension."""

    #: reference dimensions.
    reference: UnitsContainer

    @property
    def is_base(self) -> bool:
        return False

    def __post_init__(self):
        if not errors.is_valid_dimension_name(self.name):
            raise self.def_err(errors.MSG_INVALID_DIMENSION_NAME)

        if not all(map(errors.is_dim, self.reference.keys())):
            return self.def_err(
                "derived dimensions must only reference other dimensions"
            )

        invalid = tuple(
            itertools.filterfalse(errors.is_valid_dimension_name, self.reference.keys())
        )

        if invalid:
            raise self.def_err(
                f"refers to {', '.join(invalid)} that "
                + errors.MSG_INVALID_DIMENSION_NAME
            )


@dataclass(frozen=True)
class AliasDefinition(errors.WithDefErr):
    """Additional alias(es) for an already existing unit."""

    #: name of the already existing unit
    name: str
    #: aditional names for the same unit
    aliases: ty.Tuple[str, ...]

    def __post_init__(self):
        if not errors.is_valid_unit_name(self.name):
            raise self.def_err(errors.MSG_INVALID_UNIT_NAME)

        for alias in self.aliases:
            if not errors.is_valid_unit_alias(alias):
                raise self.def_err(
                    f"the alias {alias} " + errors.MSG_INVALID_UNIT_ALIAS
                )


@dataclass(frozen=True)
class ScaleConverter(Converter):
    """A linear transformation without offset."""

    scale: float

    def to_reference(self, value: Magnitude, inplace: bool = False) -> Magnitude:
        if inplace:
            value *= self.scale
        else:
            value = value * self.scale

        return value

    def from_reference(self, value: Magnitude, inplace: bool = False) -> Magnitude:
        if inplace:
            value /= self.scale
        else:
            value = value / self.scale

        return value
