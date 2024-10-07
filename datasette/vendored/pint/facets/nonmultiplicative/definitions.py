"""
    pint.facets.nonmultiplicative.definitions
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..._typing import Magnitude
from ...compat import HAS_NUMPY, exp, log
from ..plain import ScaleConverter


@dataclass(frozen=True)
class OffsetConverter(ScaleConverter):
    """An affine transformation."""

    offset: float

    @property
    def is_multiplicative(self):
        return self.offset == 0

    def to_reference(self, value: Magnitude, inplace: bool = False) -> Magnitude:
        if inplace:
            value *= self.scale
            value += self.offset
        else:
            value = value * self.scale + self.offset

        return value

    def from_reference(self, value: Magnitude, inplace: bool = False) -> Magnitude:
        if inplace:
            value -= self.offset
            value /= self.scale
        else:
            value = (value - self.offset) / self.scale

        return value

    @classmethod
    def preprocess_kwargs(cls, **kwargs):
        if "offset" in kwargs and kwargs["offset"] == 0:
            return {"scale": kwargs["scale"]}
        return None


@dataclass(frozen=True)
class LogarithmicConverter(ScaleConverter):
    """Converts between linear units and logarithmic units, such as dB, octave, neper or pH.
    Q_log = logfactor * log( Q_lin / scale ) / log(log_base)

    Parameters
    ----------
    scale : float
        unit of reference at denominator for logarithmic unit conversion
    logbase : float
        plain of logarithm used in the logarithmic unit conversion
    logfactor : float
        factor multiplied to logarithm for unit conversion
    inplace : bool
        controls if computation is done in place
    """

    # TODO: Can I use PintScalar here?
    logbase: float
    logfactor: float

    @property
    def is_multiplicative(self):
        return False

    @property
    def is_logarithmic(self):
        return True

    def from_reference(self, value: Magnitude, inplace: bool = False) -> Magnitude:
        """Converts value from the reference unit to the logarithmic unit

        dBm   <------   mW
        y dBm = 10 log10( x / 1mW )
        """
        if inplace:
            value /= self.scale
            if HAS_NUMPY:
                log(value, value)
            else:
                value = log(value)
            value *= self.logfactor / log(self.logbase)
        else:
            value = self.logfactor * log(value / self.scale) / log(self.logbase)

        return value

    def to_reference(self, value: Magnitude, inplace: bool = False) -> Magnitude:
        """Converts value to the reference unit from the logarithmic unit

        dBm   ------>   mW
        y dBm = 10 log10( x / 1mW )
        """
        if inplace:
            value /= self.logfactor
            value *= log(self.logbase)
            if HAS_NUMPY:
                exp(value, value)
            else:
                value = exp(value)
            value *= self.scale
        else:
            value = self.scale * exp(log(self.logbase) * (value / self.logfactor))

        return value
