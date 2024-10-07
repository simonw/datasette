"""
    pint.facets.measurement.registry
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from typing import Any, Generic

from ...compat import TypeAlias, ufloat
from ...util import create_class_with_registry
from ..plain import GenericPlainRegistry, QuantityT, UnitT
from . import objects


class GenericMeasurementRegistry(
    Generic[QuantityT, UnitT], GenericPlainRegistry[QuantityT, UnitT]
):
    Measurement = objects.Measurement

    def _init_dynamic_classes(self) -> None:
        """Generate subclasses on the fly and attach them to self"""
        super()._init_dynamic_classes()

        if ufloat is not None:
            self.Measurement = create_class_with_registry(self, self.Measurement)
        else:

            def no_uncertainties(*args, **kwargs):
                raise RuntimeError(
                    "Pint requires the 'uncertainties' package to create a Measurement object."
                )

            self.Measurement = no_uncertainties


class MeasurementRegistry(
    GenericMeasurementRegistry[
        objects.MeasurementQuantity[Any], objects.MeasurementUnit
    ]
):
    Quantity: TypeAlias = objects.MeasurementQuantity[Any]
    Unit: TypeAlias = objects.MeasurementUnit
