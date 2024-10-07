"""
    pint.facets
    ~~~~~~~~~~~

    Facets are way to add a specific set of funcionalities to Pint. It is more
    an organization logic than anything else. It aims to enable growth while
    keeping each part small enough to be hackable.

    Each facet contains one or more of the following modules:
    - definitions: classes describing specific unit-related definitons.
      These objects must be immutable, pickable and not reference the registry (e.g. ContextDefinition)
    - objects: classes and functions that encapsulate behavior (e.g. Context)
    - registry: implements a subclass of PlainRegistry or class that can be
      mixed with it (e.g. ContextRegistry)

    In certain cases, some of these modules might be collapsed into a single one
    as the code is very short (like in dask) or expanded as the code is too long
    (like in plain, where quantity and unit object are in their own module).
    Additionally, certain facets might not have one of them.

    An important part of this scheme is that each facet should export only a few
    classes in the __init__.py and everything else should not be accessed by any
    other module (except for testing). This is Python, so accessing it cannot be
    really limited. So is more an agreement than a rule.

    It is worth noticing that a Pint Quantity or Unit is always connected to a
    *specific* registry. Therefore we need to provide a way in which functionality
    can be added to a Quantity class in an easy way. This is achieved beautifully
    using specific class attributes. For example, the NumpyRegistry looks like this:

    class NumpyRegistry:

        Quantity = NumpyQuantity
        Unit = NumpyUnit

    This tells pint that it should use NumpyQuantity as base class for a quantity
    class that belongs to a registry that has NumpyRegistry as one of its bases.

    Currently the folowing facets are implemented:

    - plain: basic manipulation and calculation with multiplicative
      dimensions, units and quantities (e.g. length, time, mass, etc).

    - nonmultiplicative: manipulation and calculation with offset and
      log units and quantities (e.g. temperature and decibel).

    - measurement: manipulation and calculation of a quantity with
      an uncertainty.

    - numpy: using numpy array as magnitude and properly handling
      numpy functions operating on quantities.

    - dask: allows pint to interoperate with dask by implementing
      dask magic methods.

    - group: allow to make collections of units that can be then
      addressed together.

    - system: redefine base units for dimensions for a particular
      collection of units (e.g. imperial)

    - context: provides the means to interconvert between incompatible
      units through well defined relations (e.g. spectroscopy allows
      converting between spatial wavelength and temporal frequency)

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from .context import ContextRegistry, GenericContextRegistry
from .dask import DaskRegistry, GenericDaskRegistry
from .group import GenericGroupRegistry, GroupRegistry
from .measurement import GenericMeasurementRegistry, MeasurementRegistry
from .nonmultiplicative import (
    GenericNonMultiplicativeRegistry,
    NonMultiplicativeRegistry,
)
from .numpy import GenericNumpyRegistry, NumpyRegistry
from .plain import GenericPlainRegistry, MagnitudeT, PlainRegistry, QuantityT, UnitT
from .system import GenericSystemRegistry, SystemRegistry

__all__ = [
    "ContextRegistry",
    "DaskRegistry",
    "FormattingRegistry",
    "GroupRegistry",
    "MeasurementRegistry",
    "NonMultiplicativeRegistry",
    "NumpyRegistry",
    "PlainRegistry",
    "SystemRegistry",
    "GenericContextRegistry",
    "GenericDaskRegistry",
    "GenericFormattingRegistry",
    "GenericGroupRegistry",
    "GenericMeasurementRegistry",
    "GenericNonMultiplicativeRegistry",
    "GenericNumpyRegistry",
    "GenericPlainRegistry",
    "GenericSystemRegistry",
    "QuantityT",
    "UnitT",
    "MagnitudeT",
]
