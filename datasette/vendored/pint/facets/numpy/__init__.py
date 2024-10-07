"""
    pint.facets.numpy
    ~~~~~~~~~~~~~~~~~

    Adds pint the capability to interoperate with NumPy

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from .registry import GenericNumpyRegistry, NumpyRegistry

__all__ = ["NumpyRegistry", "GenericNumpyRegistry"]
