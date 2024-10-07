"""
    pint.facets.context
    ~~~~~~~~~~~~~~~~~~~

    Adds pint the capability to contexts: predefined conversions
    between incompatible dimensions.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from .definitions import ContextDefinition
from .objects import Context
from .registry import ContextRegistry, GenericContextRegistry

__all__ = ["ContextDefinition", "Context", "ContextRegistry", "GenericContextRegistry"]
