"""
    pint.delegates.formatter._spec_helpers
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Convenient functions to deal with format specifications.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import functools
import re
import warnings
from collections.abc import Callable
from typing import Any

FORMATTER = Callable[
    [
        Any,
    ],
    str,
]

# Extract just the type from the specification mini-language: see
# http://docs.python.org/2/library/string.html#format-specification-mini-language
# We also add uS for uncertainties.
_BASIC_TYPES = frozenset("bcdeEfFgGnosxX%uS")

REGISTERED_FORMATTERS: dict[str, Any] = {}


def parse_spec(spec: str) -> str:
    """Parse and return spec.

    If an unknown item is found, raise a ValueError.

    This function still needs work:
    - what happens if two distinct values are found?

    """

    result = ""
    for ch in reversed(spec):
        if ch == "~" or ch in _BASIC_TYPES:
            continue
        elif ch in list(REGISTERED_FORMATTERS.keys()) + ["~"]:
            if result:
                raise ValueError("expected ':' after format specifier")
            else:
                result = ch
        elif ch.isalpha():
            raise ValueError("Unknown conversion specified " + ch)
        else:
            break
    return result


def extract_custom_flags(spec: str) -> str:
    """Return custom flags present in a format specification

    (i.e those not part of Python's formatting mini language)
    """

    if not spec:
        return ""

    # sort by length, with longer items first
    known_flags = sorted(REGISTERED_FORMATTERS.keys(), key=len, reverse=True)

    flag_re = re.compile("(" + "|".join(known_flags + ["~"]) + ")")
    custom_flags = flag_re.findall(spec)

    return "".join(custom_flags)


def remove_custom_flags(spec: str) -> str:
    """Remove custom flags present in a format specification

    (i.e those not part of Python's formatting mini language)
    """

    for flag in sorted(REGISTERED_FORMATTERS.keys(), key=len, reverse=True) + ["~"]:
        if flag:
            spec = spec.replace(flag, "")
    return spec


@functools.lru_cache
def split_format(
    spec: str, default: str, separate_format_defaults: bool = True
) -> tuple[str, str]:
    """Split format specification into magnitude and unit format."""
    mspec = remove_custom_flags(spec)
    uspec = extract_custom_flags(spec)

    default_mspec = remove_custom_flags(default)
    default_uspec = extract_custom_flags(default)

    if separate_format_defaults in (False, None):
        # should we warn always or only if there was no explicit choice?
        # Given that we want to eventually remove the flag again, I'd say yes?
        if spec and separate_format_defaults is None:
            if not uspec and default_uspec:
                warnings.warn(
                    (
                        "The given format spec does not contain a unit formatter."
                        " Falling back to the builtin defaults, but in the future"
                        " the unit formatter specified in the `default_format`"
                        " attribute will be used instead."
                    ),
                    DeprecationWarning,
                )
            if not mspec and default_mspec:
                warnings.warn(
                    (
                        "The given format spec does not contain a magnitude formatter."
                        " Falling back to the builtin defaults, but in the future"
                        " the magnitude formatter specified in the `default_format`"
                        " attribute will be used instead."
                    ),
                    DeprecationWarning,
                )
        elif not spec:
            mspec, uspec = default_mspec, default_uspec
    else:
        mspec = mspec or default_mspec
        uspec = uspec or default_uspec

    return mspec, uspec
