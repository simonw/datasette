"""
    pint.formatter
    ~~~~~~~~~~~~~~

    Format units for pint.

    :copyright: 2016 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from numbers import Number
from typing import Iterable

from .delegates.formatter._format_helpers import (
    _PRETTY_EXPONENTS,  # noqa: F401
)
from .delegates.formatter._format_helpers import (
    join_u as _join,  # noqa: F401
)
from .delegates.formatter._format_helpers import (
    pretty_fmt_exponent as _pretty_fmt_exponent,  # noqa: F401
)
from .delegates.formatter._spec_helpers import (
    _BASIC_TYPES,  # noqa: F401
    FORMATTER,  # noqa: F401
    REGISTERED_FORMATTERS,
    extract_custom_flags,  # noqa: F401
    remove_custom_flags,  # noqa: F401
)
from .delegates.formatter._spec_helpers import (
    parse_spec as _parse_spec,  # noqa: F401
)
from .delegates.formatter._spec_helpers import (
    split_format as split_format,  # noqa: F401
)

# noqa
from .delegates.formatter._to_register import register_unit_format  # noqa: F401

# Backwards compatiblity stuff
from .delegates.formatter.latex import (
    _EXP_PATTERN,  # noqa: F401
    latex_escape,  # noqa: F401
    matrix_to_latex,  # noqa: F401
    ndarray_to_latex,  # noqa: F401
    ndarray_to_latex_parts,  # noqa: F401
    siunitx_format_unit,  # noqa: F401
    vector_to_latex,  # noqa: F401
)


def formatter(
    items: Iterable[tuple[str, Number]],
    as_ratio: bool = True,
    single_denominator: bool = False,
    product_fmt: str = " * ",
    division_fmt: str = " / ",
    power_fmt: str = "{} ** {}",
    parentheses_fmt: str = "({0})",
    exp_call: FORMATTER = "{:n}".format,
    sort: bool = True,
) -> str:
    """Format a list of (name, exponent) pairs.

    Parameters
    ----------
    items : list
        a list of (name, exponent) pairs.
    as_ratio : bool, optional
        True to display as ratio, False as negative powers. (Default value = True)
    single_denominator : bool, optional
        all with terms with negative exponents are
        collected together. (Default value = False)
    product_fmt : str
        the format used for multiplication. (Default value = " * ")
    division_fmt : str
        the format used for division. (Default value = " / ")
    power_fmt : str
        the format used for exponentiation. (Default value = "{} ** {}")
    parentheses_fmt : str
        the format used for parenthesis. (Default value = "({0})")
    exp_call : callable
         (Default value = lambda x: f"{x:n}")
    sort : bool, optional
        True to sort the formatted units alphabetically (Default value = True)

    Returns
    -------
    str
        the formula as a string.

    """

    join_u = _join

    if sort is False:
        items = tuple(items)
    else:
        items = sorted(items)

    if not items:
        return ""

    if as_ratio:
        fun = lambda x: exp_call(abs(x))
    else:
        fun = exp_call

    pos_terms, neg_terms = [], []

    for key, value in items:
        if value == 1:
            pos_terms.append(key)
        elif value > 0:
            pos_terms.append(power_fmt.format(key, fun(value)))
        elif value == -1 and as_ratio:
            neg_terms.append(key)
        else:
            neg_terms.append(power_fmt.format(key, fun(value)))

    if not as_ratio:
        # Show as Product: positive * negative terms ** -1
        return _join(product_fmt, pos_terms + neg_terms)

    # Show as Ratio: positive terms / negative terms
    pos_ret = _join(product_fmt, pos_terms) or "1"

    if not neg_terms:
        return pos_ret

    if single_denominator:
        neg_ret = join_u(product_fmt, neg_terms)
        if len(neg_terms) > 1:
            neg_ret = parentheses_fmt.format(neg_ret)
    else:
        neg_ret = join_u(division_fmt, neg_terms)

    # TODO: first or last pos_ret should be pluralized

    return _join(division_fmt, [pos_ret, neg_ret])


def format_unit(unit, spec: str, registry=None, **options):
    # registry may be None to allow formatting `UnitsContainer` objects
    # in that case, the spec may not be "Lx"

    if not unit:
        if spec.endswith("%"):
            return ""
        else:
            return "dimensionless"

    if not spec:
        spec = "D"

    if registry is None:
        _formatter = REGISTERED_FORMATTERS.get(spec, None)
    else:
        try:
            _formatter = registry.formatter._formatters[spec]
        except Exception:
            _formatter = registry.formatter._formatters.get(spec, None)

    if _formatter is None:
        raise ValueError(f"Unknown conversion specified: {spec}")

    return _formatter.format_unit(unit)
