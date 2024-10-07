"""
    pint.delegates.formatter.full
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Implements:
    - Full: dispatch to other formats, accept defaults.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import locale
from typing import TYPE_CHECKING, Any, Iterable, Literal

from ..._typing import Magnitude
from ...compat import Unpack, babel_parse
from ...util import iterable
from ._compound_unit_helpers import BabelKwds, SortFunc, sort_by_unit_name
from ._to_register import REGISTERED_FORMATTERS
from .html import HTMLFormatter
from .latex import LatexFormatter, SIunitxFormatter
from .plain import (
    BaseFormatter,
    CompactFormatter,
    DefaultFormatter,
    PrettyFormatter,
    RawFormatter,
)

if TYPE_CHECKING:
    from ...compat import Locale
    from ...facets.measurement import Measurement
    from ...facets.plain import (
        MagnitudeT,
        PlainQuantity,
        PlainUnit,
    )
    from ...registry import UnitRegistry


class FullFormatter(BaseFormatter):
    """A formatter that dispatch to other formatters.

    Has a default format, locale and babel_length
    """

    _formatters: dict[str, Any] = {}

    default_format: str = ""

    # TODO: This can be over-riden by the registry definitions file
    dim_order: tuple[str, ...] = (
        "[substance]",
        "[mass]",
        "[current]",
        "[luminosity]",
        "[length]",
        "[]",
        "[time]",
        "[temperature]",
    )

    default_sort_func: SortFunc | None = staticmethod(sort_by_unit_name)

    locale: Locale | None = None

    def __init__(self, registry: UnitRegistry | None = None):
        super().__init__(registry)

        self._formatters = {}
        self._formatters["raw"] = RawFormatter(registry)
        self._formatters["D"] = DefaultFormatter(registry)
        self._formatters["H"] = HTMLFormatter(registry)
        self._formatters["P"] = PrettyFormatter(registry)
        self._formatters["Lx"] = SIunitxFormatter(registry)
        self._formatters["L"] = LatexFormatter(registry)
        self._formatters["C"] = CompactFormatter(registry)

    def set_locale(self, loc: str | None) -> None:
        """Change the locale used by default by `format_babel`.

        Parameters
        ----------
        loc : str or None
            None (do not translate), 'sys' (detect the system locale) or a locale id string.
        """
        if isinstance(loc, str):
            if loc == "sys":
                loc = locale.getdefaultlocale()[0]

            # We call babel parse to fail here and not in the formatting operation
            babel_parse(loc)

        self.locale = loc

    def get_formatter(self, spec: str):
        if spec == "":
            return self._formatters["D"]
        for k, v in self._formatters.items():
            if k in spec:
                return v

        for k, v in REGISTERED_FORMATTERS.items():
            if k in spec:
                orphan_fmt = REGISTERED_FORMATTERS[k]
                break
        else:
            return self._formatters["D"]

        try:
            fmt = orphan_fmt.__class__(self._registry)
            spec = getattr(fmt, "spec", spec)
            self._formatters[spec] = fmt
            return fmt
        except Exception:
            return orphan_fmt

    def format_magnitude(
        self, magnitude: Magnitude, mspec: str = "", **babel_kwds: Unpack[BabelKwds]
    ) -> str:
        mspec = mspec or self.default_format
        return self.get_formatter(mspec).format_magnitude(
            magnitude, mspec, **babel_kwds
        )

    def format_unit(
        self,
        unit: PlainUnit | Iterable[tuple[str, Any]],
        uspec: str = "",
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        uspec = uspec or self.default_format
        sort_func = sort_func or self.default_sort_func
        return self.get_formatter(uspec).format_unit(
            unit, uspec, sort_func=sort_func, **babel_kwds
        )

    def format_quantity(
        self,
        quantity: PlainQuantity[MagnitudeT],
        spec: str = "",
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        spec = spec or self.default_format
        # If Compact is selected, do it at the beginning
        if "#" in spec:
            spec = spec.replace("#", "")
            obj = quantity.to_compact()
        else:
            obj = quantity

        del quantity

        locale = babel_kwds.get("locale", self.locale)

        if locale:
            if "use_plural" in babel_kwds:
                use_plural = babel_kwds["use_plural"]
            else:
                use_plural = obj.magnitude > 1
                if iterable(use_plural):
                    use_plural = True
        else:
            use_plural = False

        return self.get_formatter(spec).format_quantity(
            obj,
            spec,
            sort_func=self.default_sort_func,
            use_plural=use_plural,
            length=babel_kwds.get("length", None),
            locale=locale,
        )

    def format_measurement(
        self,
        measurement: Measurement,
        meas_spec: str = "",
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        meas_spec = meas_spec or self.default_format
        # If Compact is selected, do it at the beginning
        if "#" in meas_spec:
            meas_spec = meas_spec.replace("#", "")
            obj = measurement.to_compact()
        else:
            obj = measurement

        del measurement

        use_plural = obj.magnitude.nominal_value > 1
        if iterable(use_plural):
            use_plural = True

        return self.get_formatter(meas_spec).format_measurement(
            obj,
            meas_spec,
            sort_func=self.default_sort_func,
            use_plural=babel_kwds.get("use_plural", use_plural),
            length=babel_kwds.get("length", None),
            locale=babel_kwds.get("locale", self.locale),
        )

    #######################################
    # This is for backwards compatibility
    #######################################

    def format_unit_babel(
        self,
        unit: PlainUnit | Iterable[tuple[str, Any]],
        spec: str = "",
        length: Literal["short", "long", "narrow"] | None = None,
        locale: Locale | None = None,
    ) -> str:
        if self.locale is None and locale is None:
            raise ValueError(
                "format_babel requires a locale argumente if the Formatter locale is not set."
            )

        return self.format_unit(
            unit,
            spec or self.default_format,
            sort_func=self.default_sort_func,
            use_plural=False,
            length=length,
            locale=locale or self.locale,
        )

    def format_quantity_babel(
        self,
        quantity: PlainQuantity[MagnitudeT],
        spec: str = "",
        length: Literal["short", "long", "narrow"] | None = None,
        locale: Locale | None = None,
    ) -> str:
        if self.locale is None and locale is None:
            raise ValueError(
                "format_babel requires a locale argumente if the Formatter locale is not set."
            )

        use_plural = quantity.magnitude > 1
        if iterable(use_plural):
            use_plural = True

        return self.format_quantity(
            quantity,
            spec or self.default_format,
            sort_func=self.default_sort_func,
            use_plural=use_plural,
            length=length,
            locale=locale or self.locale,
        )


################################################################
# This allows to format units independently of the registry
#
REGISTERED_FORMATTERS["raw"] = RawFormatter()
REGISTERED_FORMATTERS["D"] = DefaultFormatter()
REGISTERED_FORMATTERS["H"] = HTMLFormatter()
REGISTERED_FORMATTERS["P"] = PrettyFormatter()
REGISTERED_FORMATTERS["Lx"] = SIunitxFormatter()
REGISTERED_FORMATTERS["L"] = LatexFormatter()
REGISTERED_FORMATTERS["C"] = CompactFormatter()
