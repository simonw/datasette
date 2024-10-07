"""
    pint.facets.nonmultiplicative.objects
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from typing import Generic

from ..plain import MagnitudeT, PlainQuantity, PlainUnit


class NonMultiplicativeQuantity(Generic[MagnitudeT], PlainQuantity[MagnitudeT]):
    @property
    def _is_multiplicative(self) -> bool:
        """Check if the PlainQuantity object has only multiplicative units."""
        return not self._get_non_multiplicative_units()

    def _get_non_multiplicative_units(self) -> list[str]:
        """Return a list of the of non-multiplicative units of the PlainQuantity object."""
        return [
            unit
            for unit in self._units
            if not self._get_unit_definition(unit).is_multiplicative
        ]

    def _get_delta_units(self) -> list[str]:
        """Return list of delta units ot the PlainQuantity object."""
        return [u for u in self._units if u.startswith("delta_")]

    def _has_compatible_delta(self, unit: str) -> bool:
        """ "Check if PlainQuantity object has a delta_unit that is compatible with unit"""
        deltas = self._get_delta_units()
        if "delta_" + unit in deltas:
            return True
        # Look for delta units with same dimension as the offset unit
        offset_unit_dim = self._get_unit_definition(unit).reference
        return any(
            self._get_unit_definition(d).reference == offset_unit_dim for d in deltas
        )

    def _ok_for_muldiv(self, no_offset_units: int | None = None) -> bool:
        """Checks if PlainQuantity object can be multiplied or divided"""

        is_ok = True
        if no_offset_units is None:
            no_offset_units = len(self._get_non_multiplicative_units())
        if no_offset_units > 1:
            is_ok = False
        if no_offset_units == 1:
            if len(self._units) > 1:
                is_ok = False
            if (
                len(self._units) == 1
                and not self._REGISTRY.autoconvert_offset_to_baseunit
            ):
                is_ok = False
            if next(iter(self._units.values())) != 1:
                is_ok = False
        return is_ok


class NonMultiplicativeUnit(PlainUnit):
    pass
