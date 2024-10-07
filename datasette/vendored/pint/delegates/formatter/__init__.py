"""
    pint.delegates.formatter
    ~~~~~~~~~~~~~~~~~~~~~~~~

    Easy to replace and extend string formatting.

    See pint.delegates.formatter.plain.DefaultFormatter for a
    description of a formatter.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from .full import FullFormatter


class Formatter(FullFormatter):
    """Default Pint Formatter"""

    pass


__all__ = [
    "Formatter",
]
