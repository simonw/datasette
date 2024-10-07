"""
    pint.delegates.formatter.base_formatter
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Common class and function for all formatters.
    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Iterable

from ..._typing import Magnitude
from ...compat import Unpack, ndarray, np
from ...util import UnitsContainer
from ._compound_unit_helpers import BabelKwds, prepare_compount_unit
from ._format_helpers import join_mu, override_locale
from ._spec_helpers import REGISTERED_FORMATTERS, split_format
from .plain import BaseFormatter

if TYPE_CHECKING:
    from ...facets.plain import MagnitudeT, PlainQuantity, PlainUnit
    from ...registry import UnitRegistry


def register_unit_format(name: str):
    """register a function as a new format for units

    The registered function must have a signature of:

    .. code:: python

        def new_format(unit, registry, **options):
            pass

    Parameters
    ----------
    name : str
        The name of the new format (to be used in the format mini-language). A error is
        raised if the new format would overwrite a existing format.

    Examples
    --------
    .. code:: python

        @pint.register_unit_format("custom")
        def format_custom(unit, registry, **options):
            result = "<formatted unit>"  # do the formatting
            return result


        ureg = pint.UnitRegistry()
        u = ureg.m / ureg.s ** 2
        f"{u:custom}"
    """

    # TODO: kwargs missing in typing
    def wrapper(func: Callable[[PlainUnit, UnitRegistry], str]):
        if name in REGISTERED_FORMATTERS:
            raise ValueError(f"format {name!r} already exists")  # or warn instead

        class NewFormatter(BaseFormatter):
            spec = name

            def format_magnitude(
                self,
                magnitude: Magnitude,
                mspec: str = "",
                **babel_kwds: Unpack[BabelKwds],
            ) -> str:
                with override_locale(
                    mspec, babel_kwds.get("locale", None)
                ) as format_number:
                    if isinstance(magnitude, ndarray) and magnitude.ndim > 0:
                        # Use custom ndarray text formatting--need to handle scalars differently
                        # since they don't respond to printoptions
                        with np.printoptions(formatter={"float_kind": format_number}):
                            mstr = format(magnitude).replace("\n", "")
                    else:
                        mstr = format_number(magnitude)

                return mstr

            def format_unit(
                self,
                unit: PlainUnit | Iterable[tuple[str, Any]],
                uspec: str = "",
                **babel_kwds: Unpack[BabelKwds],
            ) -> str:
                numerator, _denominator = prepare_compount_unit(
                    unit,
                    uspec,
                    **babel_kwds,
                    as_ratio=False,
                    registry=self._registry,
                )

                if self._registry is None:
                    units = UnitsContainer(numerator)
                else:
                    units = self._registry.UnitsContainer(numerator)

                return func(units, registry=self._registry)

            def format_quantity(
                self,
                quantity: PlainQuantity[MagnitudeT],
                qspec: str = "",
                **babel_kwds: Unpack[BabelKwds],
            ) -> str:
                registry = self._registry

                if registry is None:
                    mspec, uspec = split_format(qspec, "", True)
                else:
                    mspec, uspec = split_format(
                        qspec,
                        registry.formatter.default_format,
                        registry.separate_format_defaults,
                    )

                joint_fstring = "{} {}"
                return join_mu(
                    joint_fstring,
                    self.format_magnitude(quantity.magnitude, mspec, **babel_kwds),
                    self.format_unit(quantity.unit_items(), uspec, **babel_kwds),
                )

        REGISTERED_FORMATTERS[name] = NewFormatter()

    return wrapper
