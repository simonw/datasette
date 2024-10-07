"""
    pint
    ~~~~

    Pint is Python module/package to define, operate and manipulate
    **physical quantities**: the product of a numerical value and a
    unit of measurement. It allows arithmetic operations between them
    and conversions from and to different units.

    :copyright: 2016 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from importlib.metadata import version

from .delegates.formatter._format_helpers import formatter
from .errors import (  # noqa: F401
    DefinitionSyntaxError,
    DimensionalityError,
    LogarithmicUnitCalculusError,
    OffsetUnitCalculusError,
    PintError,
    RedefinitionError,
    UndefinedUnitError,
    UnitStrippedWarning,
)
from .formatting import register_unit_format
from .registry import ApplicationRegistry, LazyRegistry, UnitRegistry
from .util import logger, pi_theorem  # noqa: F401

# Default Quantity, Unit and Measurement are the ones
# build in the default registry.
Quantity = UnitRegistry.Quantity
Unit = UnitRegistry.Unit
Measurement = UnitRegistry.Measurement
Context = UnitRegistry.Context
Group = UnitRegistry.Group

try:  # pragma: no cover
    __version__ = version("pint")
except Exception:  # pragma: no cover
    # we seem to have a local copy not installed without setuptools
    # so the reported version will be unknown
    __version__ = "unknown"


#: A Registry with the default units and constants.
_DEFAULT_REGISTRY = LazyRegistry()

#: Registry used for unpickling operations.
application_registry = ApplicationRegistry(_DEFAULT_REGISTRY)


def _unpickle(cls, *args):
    """Rebuild object upon unpickling.
    All units must exist in the application registry.

    Parameters
    ----------
    cls : Quantity, Magnitude, or Unit
    *args

    Returns
    -------
    object of type cls

    """
    from datasette.vendored.pint.util import UnitsContainer

    for arg in args:
        # Prefixed units are defined within the registry
        # on parsing (which does not happen here).
        # We need to make sure that this happens before using.
        if isinstance(arg, UnitsContainer):
            for name in arg:
                application_registry.parse_units(name)

    return cls(*args)


def _unpickle_quantity(cls, *args):
    """Rebuild quantity upon unpickling using the application registry."""
    return _unpickle(application_registry.Quantity, *args)


def _unpickle_unit(cls, *args):
    """Rebuild unit upon unpickling using the application registry."""
    return _unpickle(application_registry.Unit, *args)


def _unpickle_measurement(cls, *args):
    """Rebuild measurement upon unpickling using the application registry."""
    return _unpickle(application_registry.Measurement, *args)


def set_application_registry(registry):
    """Set the application registry, which is used for unpickling operations
    and when invoking pint.Quantity or pint.Unit directly.

    Parameters
    ----------
    registry : pint.UnitRegistry
    """
    application_registry.set(registry)


def get_application_registry():
    """Return the application registry. If :func:`set_application_registry` was never
    invoked, return a registry built using :file:`defaults_en.txt` embedded in the pint
    package.

    Returns
    -------
    pint.UnitRegistry
    """
    return application_registry


# Enumerate all user-facing objects
# Hint to intersphinx that, when building objects.inv, these objects must be registered
# under the top-level module and not in their original submodules
__all__ = (
    "Measurement",
    "Quantity",
    "Unit",
    "UnitRegistry",
    "PintError",
    "DefinitionSyntaxError",
    "LogarithmicUnitCalculusError",
    "DimensionalityError",
    "OffsetUnitCalculusError",
    "RedefinitionError",
    "UndefinedUnitError",
    "UnitStrippedWarning",
    "formatter",
    "get_application_registry",
    "set_application_registry",
    "register_unit_format",
    "pi_theorem",
    "__version__",
    "Context",
)
