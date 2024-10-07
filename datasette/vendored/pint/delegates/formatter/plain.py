"""
    pint.delegates.formatter.plain
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Implements plain text formatters:
    - Raw: as simple as it gets (no locale aware, no unit formatter.)
    - Default: used when no string spec is given.
    - Compact: like default but with less spaces.
    - Pretty: pretty printed formatter.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import itertools
import re
from typing import TYPE_CHECKING, Any, Iterable

from ..._typing import Magnitude
from ...compat import Unpack, ndarray, np
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
    pretty_fmt_exponent,
)
from ._spec_helpers import (
    remove_custom_flags,
    split_format,
)

if TYPE_CHECKING:
    from ...facets.measurement import Measurement
    from ...facets.plain import MagnitudeT, PlainQuantity, PlainUnit
    from ...registry import UnitRegistry


_EXP_PATTERN = re.compile(r"([0-9]\.?[0-9]*)e(-?)\+?0*([0-9]+)")


class BaseFormatter:
    def __init__(self, registry: UnitRegistry | None = None):
        self._registry = registry


class DefaultFormatter(BaseFormatter):
    """Simple, localizable plain text formatter.

    A formatter is a class with methods to format into string each of the objects
    that appear in pint (magnitude, unit, quantity, uncertainty, measurement)
    """

    def format_magnitude(
        self, magnitude: Magnitude, mspec: str = "", **babel_kwds: Unpack[BabelKwds]
    ) -> str:
        """Format scalar/array into string
        given a string formatting specification and locale related arguments.
        """
        with override_locale(mspec, babel_kwds.get("locale", None)) as format_number:
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
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        """Format a unit (can be compound) into string
        given a string formatting specification and locale related arguments.
        """

        numerator, denominator = prepare_compount_unit(
            unit,
            uspec,
            sort_func=sort_func,
            **babel_kwds,
            registry=self._registry,
        )

        if babel_kwds.get("locale", None):
            length = babel_kwds.get("length") or ("short" if "~" in uspec else "long")
            division_fmt = localize_per(length, babel_kwds.get("locale"), "{} / {}")
        else:
            division_fmt = "{} / {}"

        return formatter(
            numerator,
            denominator,
            as_ratio=True,
            single_denominator=False,
            product_fmt="{} * {}",
            division_fmt=division_fmt,
            power_fmt="{} ** {}",
            parentheses_fmt=r"({})",
        )

    def format_quantity(
        self,
        quantity: PlainQuantity[MagnitudeT],
        qspec: str = "",
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        """Format a quantity (magnitude and unit) into string
        given a string formatting specification and locale related arguments.
        """

        registry = self._registry

        mspec, uspec = split_format(
            qspec, registry.formatter.default_format, registry.separate_format_defaults
        )

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
        """Format an uncertainty magnitude (nominal value and stdev) into string
        given a string formatting specification and locale related arguments.
        """

        return format(uncertainty, unc_spec).replace("+/-", " +/- ")

    def format_measurement(
        self,
        measurement: Measurement,
        meas_spec: str = "",
        sort_func: SortFunc | None = None,
        **babel_kwds: Unpack[BabelKwds],
    ) -> str:
        """Format an measurement (uncertainty and units) into string
        given a string formatting specification and locale related arguments.
        """

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


class CompactFormatter(BaseFormatter):
    """Simple, localizable plain text formatter without extra spaces."""

    def format_magnitude(
        self, magnitude: Magnitude, mspec: str = "", **babel_kwds: Unpack[BabelKwds]
    ) -> str:
        with override_locale(mspec, babel_kwds.get("locale", None)) as format_number:
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

        # Division format in compact formatter is not localized.
        division_fmt = "{}/{}"

        return formatter(
            numerator,
            denominator,
            as_ratio=True,
            single_denominator=False,
            product_fmt="*",  # TODO: Should this just be ''?
            division_fmt=division_fmt,
            power_fmt="{}**{}",
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
        return format(uncertainty, unc_spec).replace("+/-", "+/-")

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


class PrettyFormatter(BaseFormatter):
    """Pretty printed localizable plain text formatter without extra spaces."""

    def format_magnitude(
        self, magnitude: Magnitude, mspec: str = "", **babel_kwds: Unpack[BabelKwds]
    ) -> str:
        with override_locale(mspec, babel_kwds.get("locale", None)) as format_number:
            if isinstance(magnitude, ndarray) and magnitude.ndim > 0:
                # Use custom ndarray text formatting--need to handle scalars differently
                # since they don't respond to printoptions
                with np.printoptions(formatter={"float_kind": format_number}):
                    mstr = format(magnitude).replace("\n", "")
            else:
                mstr = format_number(magnitude)

            m = _EXP_PATTERN.match(mstr)

            if m:
                exp = int(m.group(2) + m.group(3))
                mstr = _EXP_PATTERN.sub(r"\1×10" + pretty_fmt_exponent(exp), mstr)

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
            single_denominator=False,
            product_fmt="·",
            division_fmt=division_fmt,
            power_fmt="{}{}",
            parentheses_fmt="({})",
            exp_call=pretty_fmt_exponent,
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
        return format(uncertainty, unc_spec).replace("±", " ± ")

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

        unc_spec = meas_spec
        joint_fstring = "{} {}"

        return join_unc(
            joint_fstring,
            "(",
            ")",
            self.format_uncertainty(measurement.magnitude, unc_spec, **babel_kwds),
            self.format_unit(measurement.units, uspec, sort_func, **babel_kwds),
        )


class RawFormatter(BaseFormatter):
    """Very simple non-localizable plain text formatter.

    Ignores all pint custom string formatting specification.
    """

    def format_magnitude(
        self, magnitude: Magnitude, mspec: str = "", **babel_kwds: Unpack[BabelKwds]
    ) -> str:
        return str(magnitude)

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

        return " * ".join(
            k if v == 1 else f"{k} ** {v}"
            for k, v in itertools.chain(numerator, denominator)
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
        return format(uncertainty, unc_spec)

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
