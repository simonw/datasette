"""
    pint.delegates.formatter.html
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Implements:
    - HTML: suitable for web/jupyter notebook outputs.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable

from ..._typing import Magnitude
from ...compat import Unpack, ndarray, np
from ...util import iterable
from ._compound_unit_helpers import (
    BabelKwds,
    SortFunc,
    localize_per,
    prepare_compount_unit,
)
from ._format_helpers import (
    formatter,
    join_mu,
    join_unc,
    override_locale,
)
from ._spec_helpers import (
    remove_custom_flags,
    split_format,
)
from .plain import BaseFormatter

if TYPE_CHECKING:
    from ...facets.measurement import Measurement
    from ...facets.plain import MagnitudeT, PlainQuantity, PlainUnit

_EXP_PATTERN = re.compile(r"([0-9]\.?[0-9]*)e(-?)\+?0*([0-9]+)")


class HTMLFormatter(BaseFormatter):
    """HTML localizable text formatter."""

    def format_magnitude(
        self, magnitude: Magnitude, mspec: str = "", **babel_kwds: Unpack[BabelKwds]
    ) -> str:
        with override_locale(mspec, babel_kwds.get("locale", None)) as format_number:
            if hasattr(magnitude, "_repr_html_"):
                # If magnitude has an HTML repr, nest it within Pint's
                mstr = magnitude._repr_html_()  # type: ignore
                assert isinstance(mstr, str)
            else:
                if isinstance(magnitude, ndarray):
                    # Need to override for scalars, which are detected as iterable,
                    # and don't respond to printoptions.
                    if magnitude.ndim == 0:
                        mstr = format_number(magnitude)
                    else:
                        with np.printoptions(formatter={"float_kind": format_number}):
                            mstr = (
                                "<pre>" + format(magnitude).replace("\n", "") + "</pre>"
                            )
                elif not iterable(magnitude):
                    # Use plain text for scalars
                    mstr = format_number(magnitude)
                else:
                    # Use monospace font for other array-likes
                    mstr = (
                        "<pre>"
                        + format_number(magnitude).replace("\n", "<br>")
                        + "</pre>"
                    )

        m = _EXP_PATTERN.match(mstr)
        _exp_formatter = lambda s: f"<sup>{s}</sup>"

        if m:
            exp = int(m.group(2) + m.group(3))
            mstr = _EXP_PATTERN.sub(r"\1×10" + _exp_formatter(exp), mstr)

        return mstr

    def format_unit(
        self,
        unit: PlainUnit | Iterable[tuple[str, Any]],
        uspec: str = "",
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        numerator, denominator = prepare_compount_unit(
            unit,
            uspec,
            sort_func=sort_func,
            **babel_kwds,
            registry=self._registry,
        )

        if babel_kwds.get("locale", None):
            length = babel_kwds.get("length") or ("short" if "~" in uspec else "long")
            division_fmt = localize_per(length, babel_kwds.get("locale"), "{}/{}")
        else:
            division_fmt = "{}/{}"

        return formatter(
            numerator,
            denominator,
            as_ratio=True,
            single_denominator=True,
            product_fmt=r" ",
            division_fmt=division_fmt,
            power_fmt=r"{}<sup>{}</sup>",
            parentheses_fmt=r"({})",
        )

    def format_quantity(
        self,
        quantity: PlainQuantity[MagnitudeT],
        qspec: str = "",
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        registry = self._registry

        mspec, uspec = split_format(
            qspec, registry.formatter.default_format, registry.separate_format_defaults
        )

        if iterable(quantity.magnitude):
            # Use HTML table instead of plain text template for array-likes
            joint_fstring = (
                "<table><tbody>"
                "<tr><th>Magnitude</th>"
                "<td style='text-align:left;'>{}</td></tr>"
                "<tr><th>Units</th><td style='text-align:left;'>{}</td></tr>"
                "</tbody></table>"
            )
        else:
            joint_fstring = "{} {}"

        return join_mu(
            joint_fstring,
            self.format_magnitude(quantity.magnitude, mspec, **babel_kwds),
            self.format_unit(quantity.unit_items(), uspec, sort_func, **babel_kwds),
        )

    def format_uncertainty(
        self,
        uncertainty,
        unc_spec: str = "",
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        unc_str = format(uncertainty, unc_spec).replace("+/-", " &plusmn; ")

        unc_str = re.sub(r"\)e\+0?(\d+)", r")×10<sup>\1</sup>", unc_str)
        unc_str = re.sub(r"\)e-0?(\d+)", r")×10<sup>-\1</sup>", unc_str)
        return unc_str

    def format_measurement(
        self,
        measurement: Measurement,
        meas_spec: str = "",
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        registry = self._registry

        mspec, uspec = split_format(
            meas_spec,
            registry.formatter.default_format,
            registry.separate_format_defaults,
        )

        unc_spec = remove_custom_flags(meas_spec)

        joint_fstring = "{} {}"

        return join_unc(
            joint_fstring,
            "(",
            ")",
            self.format_uncertainty(measurement.magnitude, unc_spec, **babel_kwds),
            self.format_unit(measurement.units, uspec, sort_func, **babel_kwds),
        )
