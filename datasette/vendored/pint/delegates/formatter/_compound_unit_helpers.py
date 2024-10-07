"""
    pint.delegates.formatter._compound_unit_helpers
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Convenient functions to help organize compount units.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import functools
import locale
from collections.abc import Callable, Iterable
from functools import partial
from itertools import filterfalse, tee
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypedDict,
    TypeVar,
)

from ...compat import TypeAlias, babel_parse
from ...util import UnitsContainer

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")
W = TypeVar("W")

if TYPE_CHECKING:
    from ...compat import Locale, Number
    from ...facets.plain import PlainUnit
    from ...registry import UnitRegistry


class SortKwds(TypedDict):
    registry: UnitRegistry


SortFunc: TypeAlias = Callable[
    [Iterable[tuple[str, Any, str]], Any], Iterable[tuple[str, Any, str]]
]


class BabelKwds(TypedDict):
    """Babel related keywords used in formatters."""

    use_plural: bool
    length: Literal["short", "long", "narrow"] | None
    locale: Locale | str | None


def partition(
    predicate: Callable[[T], bool], iterable: Iterable[T]
) -> tuple[filterfalse[T], filter[T]]:
    """Partition entries into false entries and true entries.

    If *predicate* is slow, consider wrapping it with functools.lru_cache().
    """
    # partition(is_odd, range(10)) --> 0 2 4 6 8   and  1 3 5 7 9
    t1, t2 = tee(iterable)
    return filterfalse(predicate, t1), filter(predicate, t2)


def localize_per(
    length: Literal["short", "long", "narrow"] = "long",
    locale: Locale | str | None = locale.LC_NUMERIC,
    default: str | None = None,
) -> str:
    """Localized singular and plural form of a unit.

    THIS IS TAKEN FROM BABEL format_unit. But
    - No magnitude is returned in the string.
    - If the unit is not found, the default is given.
    - If the default is None, then the same value is given.
    """
    locale = babel_parse(locale)

    patterns = locale._data["compound_unit_patterns"].get("per", None)
    if patterns is None:
        return default or "{}/{}"

    patterns = patterns.get(length, None)
    if patterns is None:
        return default or "{}/{}"

    # babel 2.8
    if isinstance(patterns, str):
        return patterns

    # babe; 2.15
    return patterns.get("compound", default or "{}/{}")


@functools.lru_cache
def localize_unit_name(
    measurement_unit: str,
    use_plural: bool,
    length: Literal["short", "long", "narrow"] = "long",
    locale: Locale | str | None = locale.LC_NUMERIC,
    default: str | None = None,
) -> str:
    """Localized singular and plural form of a unit.

    THIS IS TAKEN FROM BABEL format_unit. But
    - No magnitude is returned in the string.
    - If the unit is not found, the default is given.
    - If the default is None, then the same value is given.
    """
    locale = babel_parse(locale)
    from babel.units import _find_unit_pattern, get_unit_name

    q_unit = _find_unit_pattern(measurement_unit, locale=locale)
    if not q_unit:
        return measurement_unit

    unit_patterns = locale._data["unit_patterns"][q_unit].get(length, {})

    if use_plural:
        grammatical_number = "other"
    else:
        grammatical_number = "one"

    if grammatical_number in unit_patterns:
        return unit_patterns[grammatical_number].format("").replace("\xa0", "").strip()

    if default is not None:
        return default

    # Fall back to a somewhat bad representation.
    # nb: This is marked as no-cover, as the current CLDR seemingly has no way for this to happen.
    fallback_name = get_unit_name(
        measurement_unit, length=length, locale=locale
    )  # pragma: no cover
    return f"{fallback_name or measurement_unit}"  # pragma: no cover


def extract2(element: tuple[str, T, str]) -> tuple[str, T]:
    """Extract display name and exponent from a tuple containing display name, exponent and unit name."""

    return element[:2]


def to_name_exponent_name(element: tuple[str, T]) -> tuple[str, T, str]:
    """Convert unit name and exponent to unit name as display name, exponent and unit name."""

    # TODO: write a generic typing

    return element + (element[0],)


def to_symbol_exponent_name(
    el: tuple[str, T], registry: UnitRegistry
) -> tuple[str, T, str]:
    """Convert unit name and exponent to unit symbol as display name, exponent and unit name."""
    return registry._get_symbol(el[0]), el[1], el[0]


def localize_display_exponent_name(
    element: tuple[str, T, str],
    use_plural: bool,
    length: Literal["short", "long", "narrow"] = "long",
    locale: Locale | str | None = locale.LC_NUMERIC,
    default: str | None = None,
) -> tuple[str, T, str]:
    """Localize display name in a triplet display name, exponent and unit name."""

    return (
        localize_unit_name(
            element[2], use_plural, length, locale, default or element[0]
        ),
        element[1],
        element[2],
    )


#####################
# Sorting functions
#####################


def sort_by_unit_name(
    items: Iterable[tuple[str, Number, str]], _registry: UnitRegistry | None
) -> Iterable[tuple[str, Number, str]]:
    return sorted(items, key=lambda el: el[2])


def sort_by_display_name(
    items: Iterable[tuple[str, Number, str]], _registry: UnitRegistry | None
) -> Iterable[tuple[str, Number, str]]:
    return sorted(items)


def sort_by_dimensionality(
    items: Iterable[tuple[str, Number, str]], registry: UnitRegistry | None
) -> Iterable[tuple[str, Number, str]]:
    """Sort a list of units by dimensional order (from `registry.formatter.dim_order`).

    Parameters
    ----------
    items : tuple
        a list of tuples containing (unit names, exponent values).
    registry : UnitRegistry | None
        the registry to use for looking up the dimensions of each unit.

    Returns
    -------
    list
        the list of units sorted by most significant dimension first.

    Raises
    ------
    KeyError
        If unit cannot be found in the registry.
    """

    if registry is None:
        return items

    dim_order = registry.formatter.dim_order

    def sort_key(item: tuple[str, Number, str]):
        _display_name, _unit_exponent, unit_name = item
        cname = registry.get_name(unit_name)
        cname_dims = registry.get_dimensionality(cname) or {"[]": None}
        for cname_dim in cname_dims:
            if cname_dim in dim_order:
                return dim_order.index(cname_dim), cname

        raise KeyError(f"Unit {unit_name} (aka {cname}) has no recognized dimensions")

    return sorted(items, key=sort_key)


def prepare_compount_unit(
    unit: PlainUnit | UnitsContainer | Iterable[tuple[str, T]],
    spec: str = "",
    sort_func: SortFunc | None = None,
    use_plural: bool = True,
    length: Literal["short", "long", "narrow"] | None = None,
    locale: Locale | str | None = None,
    as_ratio: bool = True,
    registry: UnitRegistry | None = None,
) -> tuple[Iterable[tuple[str, T]], Iterable[tuple[str, T]]]:
    """Format compound unit into unit container given
    an spec and locale.

    Returns
    -------
    iterable of display name, exponent, canonical name
    """

    if isinstance(unit, UnitsContainer):
        out = unit.items()
    elif hasattr(unit, "_units"):
        out = unit._units.items()
    else:
        out = unit

    # out: unit_name, unit_exponent

    if len(out) == 0:
        if "~" in spec:
            return ([], [])
        else:
            return ([("dimensionless", 1)], [])

    if "~" in spec:
        if registry is None:
            raise ValueError(
                f"Can't short format a {type(unit)} without a registry."
                " This is usually triggered when formatting a instance"
                " of the internal `UnitsContainer`."
            )
        _to_symbol_exponent_name = partial(to_symbol_exponent_name, registry=registry)
        out = map(_to_symbol_exponent_name, out)
    else:
        out = map(to_name_exponent_name, out)

    # We keep unit_name because the sort or localizing functions might needed.
    # out: display_unit_name, unit_exponent, unit_name

    if as_ratio:
        numerator, denominator = partition(lambda el: el[1] < 0, out)
    else:
        numerator, denominator = out, ()

    # numerator: display_unit_name, unit_name, unit_exponent
    # denominator: display_unit_name, unit_name, unit_exponent

    if locale is None:
        if sort_func is not None:
            numerator = sort_func(numerator, registry)
            denominator = sort_func(denominator, registry)

        return map(extract2, numerator), map(extract2, denominator)

    if length is None:
        length = "short" if "~" in spec else "long"

    mapper = partial(
        localize_display_exponent_name, use_plural=False, length=length, locale=locale
    )

    numerator = map(mapper, numerator)
    denominator = map(mapper, denominator)

    if sort_func is not None:
        numerator = sort_func(numerator, registry)
        denominator = sort_func(denominator, registry)

    if use_plural:
        if not isinstance(numerator, list):
            numerator = list(numerator)
        numerator[-1] = localize_display_exponent_name(
            numerator[-1],
            use_plural,
            length=length,
            locale=locale,
            default=numerator[-1][0],
        )

    return map(extract2, numerator), map(extract2, denominator)
