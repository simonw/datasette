"""
    pint.delegates.formatter._format_helpers
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Convenient functions to help string formatting operations.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from functools import partial
from locale import LC_NUMERIC, getlocale, setlocale
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
)

from ...compat import ndarray
from ._spec_helpers import FORMATTER

try:
    from numpy import integer as np_integer
except ImportError:
    np_integer = None

if TYPE_CHECKING:
    from ...compat import Locale, Number

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")
W = TypeVar("W")

_PRETTY_EXPONENTS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_JOIN_REG_EXP = re.compile(r"{\d*}")


def format_number(value: Any, spec: str = "") -> str:
    """Format number

    This function might disapear in the future.
    Right now is aiding backwards compatible migration.
    """
    if isinstance(value, float):
        return format(value, spec or ".16n")

    elif isinstance(value, int):
        return format(value, spec or "n")

    elif isinstance(value, ndarray) and value.ndim == 0:
        if issubclass(value.dtype.type, np_integer):
            return format(value, spec or "n")
        else:
            return format(value, spec or ".16n")
    else:
        return str(value)


def builtin_format(value: Any, spec: str = "") -> str:
    """A keyword enabled replacement for builtin format

    format has positional only arguments
    and this cannot be partialized
    and np requires a callable.
    """
    return format(value, spec)


@contextmanager
def override_locale(
    spec: str, locale: str | Locale | None
) -> Generator[Callable[[Any], str], Any, None]:
    """Given a spec a locale, yields a function to format a number.

    IMPORTANT: When the locale is not None, this function uses setlocale
    and therefore is not thread safe.
    """

    if locale is None:
        # If locale is None, just return the builtin format function.
        yield ("{:" + spec + "}").format
    else:
        # If locale is not None, change it and return the backwards compatible
        # format_number.
        prev_locale_string = getlocale(LC_NUMERIC)
        if isinstance(locale, str):
            setlocale(LC_NUMERIC, locale)
        else:
            setlocale(LC_NUMERIC, str(locale))
        yield partial(format_number, spec=spec)
        setlocale(LC_NUMERIC, prev_locale_string)


def pretty_fmt_exponent(num: Number) -> str:
    """Format an number into a pretty printed exponent."""
    # unicode dot operator (U+22C5) looks like a superscript decimal
    ret = f"{num:n}".replace("-", "⁻").replace(".", "\u22C5")
    for n in range(10):
        ret = ret.replace(str(n), _PRETTY_EXPONENTS[n])
    return ret


def join_u(fmt: str, iterable: Iterable[Any]) -> str:
    """Join an iterable with the format specified in fmt.

    The format can be specified in two ways:
    - PEP3101 format with two replacement fields (eg. '{} * {}')
    - The concatenating string (eg. ' * ')
    """
    if not iterable:
        return ""
    if not _JOIN_REG_EXP.search(fmt):
        return fmt.join(iterable)
    miter = iter(iterable)
    first = next(miter)
    for val in miter:
        ret = fmt.format(first, val)
        first = ret
    return first


def join_mu(joint_fstring: str, mstr: str, ustr: str) -> str:
    """Join magnitude and units.

    This avoids that `3 and `1 / m` becomes `3 1 / m`
    """
    if ustr == "":
        return mstr
    if ustr.startswith("1 / "):
        return joint_fstring.format(mstr, ustr[2:])
    return joint_fstring.format(mstr, ustr)


def join_unc(joint_fstring: str, lpar: str, rpar: str, mstr: str, ustr: str) -> str:
    """Join uncertainty magnitude and units.

    Uncertainty magnitudes might require extra parenthesis when joined to units.
    - YES: 3 +/- 1
    - NO : 3(1)
    - NO : (3 +/ 1)e-9

    This avoids that `(3 + 1)` and `meter` becomes ((3 +/- 1) meter)
    """
    if mstr.startswith(lpar) or mstr.endswith(rpar):
        return joint_fstring.format(mstr, ustr)
    return joint_fstring.format(lpar + mstr + rpar, ustr)


def formatter(
    numerator: Iterable[tuple[str, Number]],
    denominator: Iterable[tuple[str, Number]],
    as_ratio: bool = True,
    single_denominator: bool = False,
    product_fmt: str = " * ",
    division_fmt: str = " / ",
    power_fmt: str = "{} ** {}",
    parentheses_fmt: str = "({0})",
    exp_call: FORMATTER = "{:n}".format,
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

    Returns
    -------
    str
        the formula as a string.

    """

    if as_ratio:
        fun = lambda x: exp_call(abs(x))
    else:
        fun = exp_call

    pos_terms: list[str] = []
    for key, value in numerator:
        if value == 1:
            pos_terms.append(key)
        else:
            pos_terms.append(power_fmt.format(key, fun(value)))

    neg_terms: list[str] = []
    for key, value in denominator:
        if value == -1 and as_ratio:
            neg_terms.append(key)
        else:
            neg_terms.append(power_fmt.format(key, fun(value)))

    if not pos_terms and not neg_terms:
        return ""

    if not as_ratio:
        # Show as Product: positive * negative terms ** -1
        return join_u(product_fmt, pos_terms + neg_terms)

    # Show as Ratio: positive terms / negative terms
    pos_ret = join_u(product_fmt, pos_terms) or "1"

    if not neg_terms:
        return pos_ret

    if single_denominator:
        neg_ret = join_u(product_fmt, neg_terms)
        if len(neg_terms) > 1:
            neg_ret = parentheses_fmt.format(neg_ret)
    else:
        neg_ret = join_u(division_fmt, neg_terms)

    return join_u(division_fmt, [pos_ret, neg_ret])
