"""
    pint.facets.system
    ~~~~~~~~~~~~~~~~~~

    Adds pint the capability to system of units.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from .definitions import SystemDefinition
from .objects import System
from .registry import GenericSystemRegistry, SystemRegistry

__all__ = ["SystemDefinition", "System", "SystemRegistry", "GenericSystemRegistry"]
