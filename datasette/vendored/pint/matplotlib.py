"""
    pint.matplotlib
    ~~~~~~~~~~~~~~~

    Functions and classes related to working with Matplotlib's support
    for plotting with units.

    :copyright: 2017 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import matplotlib.units

from .util import iterable, sized


class PintAxisInfo(matplotlib.units.AxisInfo):
    """Support default axis and tick labeling and default limits."""

    def __init__(self, units):
        """Set the default label to the pretty-print of the unit."""
        formatter = units._REGISTRY.mpl_formatter
        super().__init__(label=formatter.format(units))


class PintConverter(matplotlib.units.ConversionInterface):
    """Implement support for pint within matplotlib's unit conversion framework."""

    def __init__(self, registry):
        super().__init__()
        self._reg = registry

    def convert(self, value, unit, axis):
        """Convert :`Quantity` instances for matplotlib to use."""
        # Short circuit for arrays
        if hasattr(value, "units"):
            return value.to(unit).magnitude
        if iterable(value):
            return [self._convert_value(v, unit, axis) for v in value]

        return self._convert_value(value, unit, axis)

    def _convert_value(self, value, unit, axis):
        """Handle converting using attached unit or falling back to axis units."""
        if hasattr(value, "units"):
            return value.to(unit).magnitude

        return self._reg.Quantity(value, axis.get_units()).to(unit).magnitude

    @staticmethod
    def axisinfo(unit, axis):
        """Return axis information for this particular unit."""

        return PintAxisInfo(unit)

    @staticmethod
    def default_units(x, axis):
        """Get the default unit to use for the given combination of unit and axis."""
        if iterable(x) and sized(x):
            return getattr(x[0], "units", None)
        return getattr(x, "units", None)


def setup_matplotlib_handlers(registry, enable):
    """Set up matplotlib's unit support to handle units from a registry.

    Parameters
    ----------
    registry : pint.UnitRegistry
        The registry that will be used.
    enable : bool
        Whether support should be enabled or disabled.

    Returns
    -------

    """
    if matplotlib.__version__ < "2.0":
        raise RuntimeError("Matplotlib >= 2.0 required to work with pint.")

    if enable:
        matplotlib.units.registry[registry.Quantity] = PintConverter(registry)
    else:
        matplotlib.units.registry.pop(registry.Quantity, None)
