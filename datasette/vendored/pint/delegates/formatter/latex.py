"""
    pint.delegates.formatter.latex
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Implements:
    - Latex: uses vainilla latex.
    - SIunitx: uses latex siunitx package format.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ..._typing import Magnitude
from ...compat import Number, Unpack, ndarray
from ._compound_unit_helpers import (
    BabelKwds,
    SortFunc,
    prepare_compount_unit,
)
from ._format_helpers import (
    FORMATTER,
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
    from ...registry import UnitRegistry
    from ...util import ItMatrix


def vector_to_latex(
    vec: Iterable[Any], fmtfun: FORMATTER | str = "{:.2n}".format
) -> str:
    """Format a vector into a latex string."""
    return matrix_to_latex([vec], fmtfun)


def matrix_to_latex(matrix: ItMatrix, fmtfun: FORMATTER | str = "{:.2n}".format) -> str:
    """Format a matrix into a latex string."""

    ret: list[str] = []

    for row in matrix:
        ret += [" & ".join(fmtfun(f) for f in row)]

    return r"\begin{pmatrix}%s\end{pmatrix}" % "\\\\ \n".join(ret)


def ndarray_to_latex_parts(
    ndarr: ndarray, fmtfun: FORMATTER = "{:.2n}".format, dim: tuple[int, ...] = tuple()
) -> list[str]:
    """Convert an numpy array into an iterable of elements to be print.

    e.g.
    - if the array is 2d, it will return an iterable of rows.
    - if the array is 3d, it will return an iterable of matrices.
    """

    if isinstance(fmtfun, str):
        fmtfun = fmtfun.format

    if ndarr.ndim == 0:
        _ndarr = ndarr.reshape(1)
        return [vector_to_latex(_ndarr, fmtfun)]
    if ndarr.ndim == 1:
        return [vector_to_latex(ndarr, fmtfun)]
    if ndarr.ndim == 2:
        return [matrix_to_latex(ndarr, fmtfun)]
    else:
        ret = []
        if ndarr.ndim == 3:
            header = ("arr[%s," % ",".join("%d" % d for d in dim)) + "%d,:,:]"
            for elno, el in enumerate(ndarr):
                ret += [header % elno + " = " + matrix_to_latex(el, fmtfun)]
        else:
            for elno, el in enumerate(ndarr):
                ret += ndarray_to_latex_parts(el, fmtfun, dim + (elno,))

        return ret


def ndarray_to_latex(
    ndarr: ndarray,
    fmtfun: FORMATTER | str = "{:.2n}".format,
    dim: tuple[int, ...] = tuple(),
) -> str:
    """Format a numpy array into string."""
    return "\n".join(ndarray_to_latex_parts(ndarr, fmtfun, dim))


def latex_escape(string: str) -> str:
    """Prepend characters that have a special meaning in LaTeX with a backslash."""
    return functools.reduce(
        lambda s, m: re.sub(m[0], m[1], s),
        (
            (r"[\\]", r"\\textbackslash "),
            (r"[~]", r"\\textasciitilde "),
            (r"[\^]", r"\\textasciicircum "),
            (r"([&%$#_{}])", r"\\\1"),
        ),
        str(string),
    )


def siunitx_format_unit(
    units: Iterable[tuple[str, Number]], registry: UnitRegistry
) -> str:
    """Returns LaTeX code for the unit that can be put into an siunitx command."""

    def _tothe(power) -> str:
        if power == int(power):
            if power == 1:
                return ""
            elif power == 2:
                return r"\squared"
            elif power == 3:
                return r"\cubed"
            else:
                return rf"\tothe{{{int(power):d}}}"
        else:
            # limit float powers to 3 decimal places
            return rf"\tothe{{{power:.3f}}}".rstrip("0")

    lpos = []
    lneg = []
    # loop through all units in the container
    for unit, power in sorted(units):
        # remove unit prefix if it exists
        # siunitx supports \prefix commands

        lpick = lpos if power >= 0 else lneg
        prefix = None
        # TODO: fix this to be fore efficient and detect also aliases.
        for p in registry._prefixes.values():
            p = str(p.name)
            if len(p) > 0 and unit.find(p) == 0:
                prefix = p
                unit = unit.replace(prefix, "", 1)

        if power < 0:
            lpick.append(r"\per")
        if prefix is not None:
            lpick.append(rf"\{prefix}")
        lpick.append(rf"\{unit}")
        lpick.append(rf"{_tothe(abs(power))}")

    return "".join(lpos) + "".join(lneg)


_EXP_PATTERN = re.compile(r"([0-9]\.?[0-9]*)e(-?)\+?0*([0-9]+)")


class LatexFormatter(BaseFormatter):
    """Latex localizable text formatter."""

    def format_magnitude(
        self, magnitude: Magnitude, mspec: str = "", **babel_kwds: Unpack[BabelKwds]
    ) -> str:
        with override_locale(mspec, babel_kwds.get("locale", None)) as format_number:
            if isinstance(magnitude, ndarray):
                mstr = ndarray_to_latex(magnitude, mspec)
            else:
                mstr = format_number(magnitude)

            mstr = _EXP_PATTERN.sub(r"\1\\times 10^{\2\3}", mstr)

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

        numerator = ((rf"\mathrm{{{latex_escape(u)}}}", p) for u, p in numerator)
        denominator = ((rf"\mathrm{{{latex_escape(u)}}}", p) for u, p in denominator)

        # Localized latex
        # if babel_kwds.get("locale", None):
        #     length = babel_kwds.get("length") or ("short" if "~" in uspec else "long")
        #     division_fmt = localize_per(length, babel_kwds.get("locale"), "{}/{}")
        # else:
        #     division_fmt = "{}/{}"

        # division_fmt = r"\frac" + division_fmt.format("[{}]", "[{}]")

        formatted = formatter(
            numerator,
            denominator,
            as_ratio=True,
            single_denominator=True,
            product_fmt=r" \cdot ",
            division_fmt=r"\frac[{}][{}]",
            power_fmt="{}^[{}]",
            parentheses_fmt=r"\left({}\right)",
        )

        return formatted.replace("[", "{").replace("]", "}")

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

        joint_fstring = r"{}\ {}"

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
        # uncertainties handles everythin related to latex.
        unc_str = format(uncertainty, unc_spec)

        if unc_str.startswith(r"\left"):
            return unc_str

        return unc_str.replace("(", r"\left(").replace(")", r"\right)")

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

        # TODO: ugly. uncertainties recognizes L
        if "L" not in unc_spec:
            unc_spec += "L"

        joint_fstring = r"{}\ {}"

        return join_unc(
            joint_fstring,
            r"\left(",
            r"\right)",
            self.format_uncertainty(measurement.magnitude, unc_spec, **babel_kwds),
            self.format_unit(measurement.units, uspec, sort_func, **babel_kwds),
        )


class SIunitxFormatter(BaseFormatter):
    """Latex localizable text formatter with siunitx format.

    See: https://ctan.org/pkg/siunitx
    """

    def format_magnitude(
        self,
        magnitude: Magnitude,
        mspec: str = "",
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        with override_locale(mspec, babel_kwds.get("locale", None)) as format_number:
            if isinstance(magnitude, ndarray):
                mstr = ndarray_to_latex(magnitude, mspec)
            else:
                mstr = format_number(magnitude)

            # TODO: Why this is not needed in siunitx?
            # mstr = _EXP_PATTERN.sub(r"\1\\times 10^{\2\3}", mstr)

        return mstr

    def format_unit(
        self,
        unit: PlainUnit | Iterable[tuple[str, Any]],
        uspec: str = "",
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        registry = self._registry
        if registry is None:
            raise ValueError(
                "Can't format as siunitx without a registry."
                " This is usually triggered when formatting a instance"
                ' of the internal `UnitsContainer` with a spec of `"Lx"`'
                " and might indicate a bug in `pint`."
            )

        # TODO: not sure if I should call format_compound_unit here.
        # siunitx_format_unit requires certain specific names?
        # should unit names be translated?
        # should unit names be shortened?
        # units = format_compound_unit(unit, uspec, **babel_kwds)

        try:
            units = unit._units.items()
        except Exception:
            units = unit

        formatted = siunitx_format_unit(units, registry)

        if "~" in uspec:
            formatted = formatted.replace(r"\percent", r"\%")

        # TODO: is this the right behaviour? Should we return the \si[] when only
        # the units are returned?
        return rf"\si[]{{{formatted}}}"

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

        joint_fstring = "{}{}"

        mstr = self.format_magnitude(quantity.magnitude, mspec, **babel_kwds)
        ustr = self.format_unit(quantity.unit_items(), uspec, sort_func, **babel_kwds)[
            len(r"\si[]") :
        ]
        return r"\SI[]" + join_mu(joint_fstring, "{%s}" % mstr, ustr)

    def format_uncertainty(
        self,
        uncertainty,
        unc_spec: str = "",
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        # SIunitx requires space between "+-" (or "\pm") and the nominal value
        # and uncertainty, and doesn't accept "+/-"
        # SIunitx doesn't accept parentheses, which uncs uses with
        # scientific notation ('e' or 'E' and sometimes 'g' or 'G').
        return (
            format(uncertainty, unc_spec)
            .replace("+/-", r" +- ")
            .replace("(", "")
            .replace(")", " ")
        )

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

        joint_fstring = "{}{}"

        return r"\SI" + join_unc(
            joint_fstring,
            r"",
            r"",
            "{%s}"
            % self.format_uncertainty(measurement.magnitude, unc_spec, **babel_kwds),
            self.format_unit(measurement.units, uspec, sort_func, **babel_kwds)[
                len(r"\si[]") :
            ],
        )
