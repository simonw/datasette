"""
    pint.facets.measurement.objects
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import copy
import re
from typing import Generic

from ...compat import ufloat
from ..plain import MagnitudeT, PlainQuantity, PlainUnit

MISSING = object()


class MeasurementQuantity(Generic[MagnitudeT], PlainQuantity[MagnitudeT]):
    # Measurement support
    def plus_minus(self, error, relative=False):
        if isinstance(error, self.__class__):
            if relative:
                raise ValueError(f"{error} is not a valid relative error.")
            error = error.to(self._units).magnitude
        else:
            if relative:
                error = error * abs(self.magnitude)

        return self._REGISTRY.Measurement(copy.copy(self.magnitude), error, self._units)


class MeasurementUnit(PlainUnit):
    pass


class Measurement(PlainQuantity):
    """Implements a class to describe a quantity with uncertainty.

    Parameters
    ----------
    value : pint.Quantity or any numeric type
        The expected value of the measurement
    error : pint.Quantity or any numeric type
        The error or uncertainty of the measurement

    Returns
    -------

    """

    def __new__(cls, value, error=MISSING, units=MISSING):
        if units is MISSING:
            try:
                value, units = value.magnitude, value.units
            except AttributeError:
                # if called with two arguments and the first looks like a ufloat
                # then assume the second argument is the units, keep value intact
                if hasattr(value, "nominal_value"):
                    units = error
                    error = MISSING  # used for check below
                else:
                    units = ""
        if error is MISSING:
            # We've already extracted the units from the Quantity above
            mag = value
        else:
            try:
                error = error.to(units).magnitude
            except AttributeError:
                pass
            if error < 0:
                raise ValueError("The magnitude of the error cannot be negative")
            else:
                mag = ufloat(value, error)

        inst = super().__new__(cls, mag, units)
        return inst

    @property
    def value(self):
        return self._REGISTRY.Quantity(self.magnitude.nominal_value, self.units)

    @property
    def error(self):
        return self._REGISTRY.Quantity(self.magnitude.std_dev, self.units)

    @property
    def rel(self):
        return abs(self.magnitude.std_dev / self.magnitude.nominal_value)

    def __reduce__(self):
        # See notes in Quantity.__reduce__
        from datasette.vendored.pint import _unpickle_measurement

        return _unpickle_measurement, (Measurement, self.magnitude, self._units)

    def __repr__(self):
        return "<Measurement({}, {}, {})>".format(
            self.magnitude.nominal_value, self.magnitude.std_dev, self.units
        )

    def __str__(self):
        return f"{self}"

    def __format__(self, spec):
        spec = spec or self._REGISTRY.default_format
        return self._REGISTRY.formatter.format_measurement(self, spec)

    def old_format(self, spec):
        # TODO: provisional
        from ...formatting import _FORMATS, extract_custom_flags, siunitx_format_unit

        # special cases
        if "Lx" in spec:  # the LaTeX siunitx code
            # the uncertainties module supports formatting
            # numbers in value(unc) notation (i.e. 1.23(45) instead of 1.23 +/- 0.45),
            # using type code 'S', which siunitx actually accepts as input.
            # However, the implementation is incompatible with siunitx.
            # Uncertainties will do 9.1(1.1), which is invalid, should be 9.1(11).
            # TODO: add support for extracting options
            #
            # Get rid of this code, we'll deal with it here
            spec = spec.replace("Lx", "")
            # The most compatible format from uncertainties is the default format,
            # but even this requires fixups.
            # For one, SIUnitx does not except some formats that unc does, like 'P',
            # and 'S' is broken as stated, so...
            spec = spec.replace("S", "").replace("P", "")
            # get SIunitx options
            # TODO: allow user to set this value, somehow
            opts = _FORMATS["Lx"]["siopts"]
            if opts != "":
                opts = r"[" + opts + r"]"
            # SI requires space between "+-" (or "\pm") and the nominal value
            # and uncertainty, and doesn't accept "+/-", so this setting
            # selects the desired replacement.
            pm_fmt = _FORMATS["Lx"]["pm_fmt"]
            mstr = format(self.magnitude, spec).replace(r"+/-", pm_fmt)
            # Also, SIunitx doesn't accept parentheses, which uncs uses with
            # scientific notation ('e' or 'E' and sometimes 'g' or 'G').
            mstr = mstr.replace("(", "").replace(")", " ")
            ustr = siunitx_format_unit(self.units._units.items(), self._REGISTRY)
            return rf"\SI{opts}{{{mstr}}}{{{ustr}}}"

        # standard cases
        if "L" in spec:
            newpm = pm = r"  \pm  "
            pars = _FORMATS["L"]["parentheses_fmt"]
        elif "P" in spec:
            newpm = pm = "±"
            pars = _FORMATS["P"]["parentheses_fmt"]
        else:
            newpm = pm = "+/-"
            pars = _FORMATS[""]["parentheses_fmt"]

        if "C" in spec:
            sp = ""
            newspec = spec.replace("C", "")
            pars = _FORMATS["C"]["parentheses_fmt"]
        else:
            sp = " "
            newspec = spec

        if "H" in spec:
            newpm = "&plusmn;"
            newspec = spec.replace("H", "")
            pars = _FORMATS["H"]["parentheses_fmt"]

        mag = format(self.magnitude, newspec).replace(pm, sp + newpm + sp)
        if "(" in mag:
            # Exponential format has its own parentheses
            pars = "{}"

        if "L" in newspec and "S" in newspec:
            mag = mag.replace("(", r"\left(").replace(")", r"\right)")

        if "L" in newspec:
            space = r"\ "
        else:
            space = " "

        uspec = extract_custom_flags(spec)
        ustr = format(self.units, uspec)
        if not ("uS" in newspec or "ue" in newspec or "u%" in newspec):
            mag = pars.format(mag)

        if "H" in spec:
            # Fix exponential format
            mag = re.sub(r"\)e\+0?(\d+)", r")×10<sup>\1</sup>", mag)
            mag = re.sub(r"\)e-0?(\d+)", r")×10<sup>-\1</sup>", mag)

        return mag + space + ustr
