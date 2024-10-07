"""
    pint.facets.nonmultiplicative.registry
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from ...compat import TypeAlias
from ...errors import DimensionalityError, UndefinedUnitError
from ...util import UnitsContainer, logger
from ..plain import GenericPlainRegistry, QuantityT, UnitDefinition, UnitT
from . import objects
from .definitions import OffsetConverter, ScaleConverter

T = TypeVar("T")


class GenericNonMultiplicativeRegistry(
    Generic[QuantityT, UnitT], GenericPlainRegistry[QuantityT, UnitT]
):
    """Handle of non multiplicative units (e.g. Temperature).

    Capabilities:
    - Register non-multiplicative units and their relations.
    - Convert between non-multiplicative units.

    Parameters
    ----------
    default_as_delta : bool
        If True, non-multiplicative units are interpreted as
        their *delta* counterparts in multiplications.
    autoconvert_offset_to_baseunit : bool
        If True, non-multiplicative units are
        converted to plain units in multiplications.

    """

    def __init__(
        self,
        default_as_delta: bool = True,
        autoconvert_offset_to_baseunit: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        #: When performing a multiplication of units, interpret
        #: non-multiplicative units as their *delta* counterparts.
        self.default_as_delta = default_as_delta

        # Determines if quantities with offset units are converted to their
        # plain units on multiplication and division.
        self.autoconvert_offset_to_baseunit = autoconvert_offset_to_baseunit

    def parse_units_as_container(
        self,
        input_string: str,
        as_delta: bool | None = None,
        case_sensitive: bool | None = None,
    ) -> UnitsContainer:
        """ """
        if as_delta is None:
            as_delta = self.default_as_delta

        return super().parse_units_as_container(input_string, as_delta, case_sensitive)

    def _add_unit(self, definition: UnitDefinition) -> None:
        super()._add_unit(definition)

        if definition.is_multiplicative:
            return

        if definition.is_logarithmic:
            return

        if not isinstance(definition.converter, OffsetConverter):
            logger.debug(
                "Cannot autogenerate delta version for a unit in "
                "which the converter is not an OffsetConverter"
            )
            return

        delta_name = "delta_" + definition.name
        if definition.symbol:
            delta_symbol = "Δ" + definition.symbol
        else:
            delta_symbol = None

        delta_aliases = tuple("Δ" + alias for alias in definition.aliases) + tuple(
            "delta_" + alias for alias in definition.aliases
        )

        delta_reference = self.UnitsContainer(
            {ref: value for ref, value in definition.reference.items()}
        )

        delta_def = UnitDefinition(
            delta_name,
            delta_symbol,
            delta_aliases,
            ScaleConverter(definition.converter.scale),
            delta_reference,
        )
        super()._add_unit(delta_def)

    def _is_multiplicative(self, unit_name: str) -> bool:
        """True if the unit is multiplicative.

        Parameters
        ----------
        unit_name
            Name of the unit to check.
            Can be prefixed, pluralized or even an alias

        Raises
        ------
        UndefinedUnitError
            If the unit is not in the registry.
        """
        if unit_name in self._units:
            return self._units[unit_name].is_multiplicative

        # If the unit is not in the registry might be because it is not
        # registered with its prefixed version.
        # TODO: Might be better to register them.
        names = self.parse_unit_name(unit_name)
        assert len(names) == 1
        _, base_name, _ = names[0]
        try:
            return self._units[base_name].is_multiplicative
        except KeyError:
            raise UndefinedUnitError(unit_name)

    def _validate_and_extract(self, units: UnitsContainer) -> str | None:
        """Used to check if a given units is suitable for a simple
        conversion.

        Return None if all units are non-multiplicative
        Return the unit name if a single non-multiplicative unit is found
        and is raised to a power equals to 1.

        Otherwise, raise an Exception.

        Parameters
        ----------
        units
            Compound dictionary.

        Raises
        ------
        ValueError
            If the more than a single non-multiplicative unit is present,
            or a single one is present but raised to a power different from 1.

        """

        # TODO: document what happens if autoconvert_offset_to_baseunit
        # TODO: Clarify docs

        # u is for unit, e is for exponent
        nonmult_units = [
            (u, e) for u, e in units.items() if not self._is_multiplicative(u)
        ]

        # Let's validate source offset units
        if len(nonmult_units) > 1:
            # More than one src offset unit is not allowed
            raise ValueError("more than one offset unit.")

        elif len(nonmult_units) == 1:
            # A single src offset unit is present. Extract it
            # But check that:
            # - the exponent is 1
            # - is not used in multiplicative context
            nonmult_unit, exponent = nonmult_units.pop()

            if exponent != 1:
                raise ValueError("offset units in higher order.")

            if len(units) > 1 and not self.autoconvert_offset_to_baseunit:
                raise ValueError("offset unit used in multiplicative context.")

            return nonmult_unit

        return None

    def _add_ref_of_log_or_offset_unit(
        self, offset_unit: str, all_units: UnitsContainer
    ) -> UnitsContainer:
        slct_unit = self._units[offset_unit]
        if slct_unit.is_logarithmic:
            # Extract reference unit
            slct_ref = slct_unit.reference

            # TODO: Check that reference is None

            # If reference unit is not dimensionless
            if slct_ref != UnitsContainer():
                # Extract reference unit
                (u, e) = [(u, e) for u, e in slct_ref.items()].pop()
                # Add it back to the unit list
                return all_units.add(u, e)

        if not slct_unit.is_multiplicative:  # is offset unit
            # Extract reference unit
            return slct_unit.reference

        # Otherwise, return the units unmodified
        return all_units

    def _convert(
        self, value: T, src: UnitsContainer, dst: UnitsContainer, inplace: bool = False
    ) -> T:
        """Convert value from some source to destination units.

        In addition to what is done by the PlainRegistry,
        converts between non-multiplicative units.

        Parameters
        ----------
        value :
            value
        src : UnitsContainer
            source units.
        dst : UnitsContainer
            destination units.
        inplace :
             (Default value = False)

        Returns
        -------
        type
            converted value

        """

        # Conversion needs to consider if non-multiplicative (AKA offset
        # units) are involved. Conversion is only possible if src and dst
        # have at most one offset unit per dimension. Other rules are applied
        # by validate and extract.
        try:
            src_offset_unit = self._validate_and_extract(src)
        except ValueError as ex:
            raise DimensionalityError(src, dst, extra_msg=f" - In source units, {ex}")

        try:
            dst_offset_unit = self._validate_and_extract(dst)
        except ValueError as ex:
            raise DimensionalityError(
                src, dst, extra_msg=f" - In destination units, {ex}"
            )

        # convert if no offset units are present
        if not (src_offset_unit or dst_offset_unit):
            return super()._convert(value, src, dst, inplace)

        src_dim = self._get_dimensionality(src)
        dst_dim = self._get_dimensionality(dst)

        # If the source and destination dimensionality are different,
        # then the conversion cannot be performed.
        if src_dim != dst_dim:
            raise DimensionalityError(src, dst, src_dim, dst_dim)

        # clean src from offset units by converting to reference
        if src_offset_unit:
            if any(u.startswith("delta_") for u in dst):
                raise DimensionalityError(src, dst)
            value = self._units[src_offset_unit].converter.to_reference(value, inplace)
            src = src.remove([src_offset_unit])
            # Add reference unit for multiplicative section
            src = self._add_ref_of_log_or_offset_unit(src_offset_unit, src)

        # clean dst units from offset units
        if dst_offset_unit:
            if any(u.startswith("delta_") for u in src):
                raise DimensionalityError(src, dst)
            dst = dst.remove([dst_offset_unit])
            # Add reference unit for multiplicative section
            dst = self._add_ref_of_log_or_offset_unit(dst_offset_unit, dst)

        # Convert non multiplicative units to the dst.
        value = super()._convert(value, src, dst, inplace, False)

        # Finally convert to offset units specified in destination
        if dst_offset_unit:
            value = self._units[dst_offset_unit].converter.from_reference(
                value, inplace
            )

        return value


class NonMultiplicativeRegistry(
    GenericNonMultiplicativeRegistry[
        objects.NonMultiplicativeQuantity[Any], objects.NonMultiplicativeUnit
    ]
):
    Quantity: TypeAlias = objects.NonMultiplicativeQuantity[Any]
    Unit: TypeAlias = objects.NonMultiplicativeUnit
