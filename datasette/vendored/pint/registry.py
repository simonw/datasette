"""
    pint.registry
    ~~~~~~~~~~~~~

    Defines the UnitRegistry, a class to contain units and their relations.

    This registry contains all pint capabilities, but you can build your
    customized registry by picking only the features that you actually
    need.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from typing import Generic

from . import facets, registry_helpers
from .compat import TypeAlias
from .util import logger, pi_theorem

# To build the Quantity and Unit classes
# we follow the UnitRegistry bases
# but


class Quantity(
    facets.SystemRegistry.Quantity,
    facets.ContextRegistry.Quantity,
    facets.DaskRegistry.Quantity,
    facets.NumpyRegistry.Quantity,
    facets.MeasurementRegistry.Quantity,
    facets.NonMultiplicativeRegistry.Quantity,
    facets.PlainRegistry.Quantity,
):
    pass


class Unit(
    facets.SystemRegistry.Unit,
    facets.ContextRegistry.Unit,
    facets.DaskRegistry.Unit,
    facets.NumpyRegistry.Unit,
    facets.MeasurementRegistry.Unit,
    facets.NonMultiplicativeRegistry.Unit,
    facets.PlainRegistry.Unit,
):
    pass


class GenericUnitRegistry(
    Generic[facets.QuantityT, facets.UnitT],
    facets.GenericSystemRegistry[facets.QuantityT, facets.UnitT],
    facets.GenericContextRegistry[facets.QuantityT, facets.UnitT],
    facets.GenericDaskRegistry[facets.QuantityT, facets.UnitT],
    facets.GenericNumpyRegistry[facets.QuantityT, facets.UnitT],
    facets.GenericMeasurementRegistry[facets.QuantityT, facets.UnitT],
    facets.GenericNonMultiplicativeRegistry[facets.QuantityT, facets.UnitT],
    facets.GenericPlainRegistry[facets.QuantityT, facets.UnitT],
):
    pass


class UnitRegistry(GenericUnitRegistry[Quantity, Unit]):
    """The unit registry stores the definitions and relationships between units.

    Parameters
    ----------
    filename :
        path of the units definition file to load or line-iterable object.
        Empty string to load the default definition file. (default)
        None to leave the UnitRegistry empty.
    force_ndarray : bool
        convert any input, scalar or not to a numpy.ndarray.
        (Default: False)
    force_ndarray_like : bool
        convert all inputs other than duck arrays to a numpy.ndarray.
        (Default: False)
    default_as_delta :
        In the context of a multiplication of units, interpret
        non-multiplicative units as their *delta* counterparts.
        (Default: False)
    autoconvert_offset_to_baseunit :
        If True converts offset units in quantities are
        converted to their plain units in multiplicative
        context. If False no conversion happens. (Default: False)
    on_redefinition : str
        action to take in case a unit is redefined.
        'warn', 'raise', 'ignore' (Default: 'raise')
    auto_reduce_dimensions :
        If True, reduce dimensionality on appropriate operations.
        (Default: False)
    autoconvert_to_preferred :
        If True, converts preferred units on appropriate operations.
        (Default: False)
    preprocessors :
        list of callables which are iteratively ran on any input expression
        or unit string or None for no preprocessor.
        (Default=None)
    fmt_locale :
        locale identifier string, used in `format_babel` or None.
        (Default=None)
    case_sensitive : bool, optional
        Control default case sensitivity of unit parsing. (Default: True)
    cache_folder : str or pathlib.Path or None, optional
        Specify the folder in which cache files are saved and loaded from.
        If None, the cache is disabled. (default)
    """

    Quantity: TypeAlias = Quantity
    Unit: TypeAlias = Unit

    def __init__(
        self,
        filename="",
        force_ndarray: bool = False,
        force_ndarray_like: bool = False,
        default_as_delta: bool = True,
        autoconvert_offset_to_baseunit: bool = False,
        on_redefinition: str = "warn",
        system=None,
        auto_reduce_dimensions=False,
        autoconvert_to_preferred=False,
        preprocessors=None,
        fmt_locale=None,
        non_int_type=float,
        case_sensitive: bool = True,
        cache_folder=None,
    ):
        super().__init__(
            filename=filename,
            force_ndarray=force_ndarray,
            force_ndarray_like=force_ndarray_like,
            on_redefinition=on_redefinition,
            default_as_delta=default_as_delta,
            autoconvert_offset_to_baseunit=autoconvert_offset_to_baseunit,
            system=system,
            auto_reduce_dimensions=auto_reduce_dimensions,
            autoconvert_to_preferred=autoconvert_to_preferred,
            preprocessors=preprocessors,
            fmt_locale=fmt_locale,
            non_int_type=non_int_type,
            case_sensitive=case_sensitive,
            cache_folder=cache_folder,
        )

    def pi_theorem(self, quantities):
        """Builds dimensionless quantities using the Buckingham π theorem

        Parameters
        ----------
        quantities : dict
            mapping between variable name and units

        Returns
        -------
        list
            a list of dimensionless quantities expressed as dicts

        """
        return pi_theorem(quantities, self)

    def setup_matplotlib(self, enable: bool = True) -> None:
        """Set up handlers for matplotlib's unit support.

        Parameters
        ----------
        enable : bool
            whether support should be enabled or disabled (Default value = True)

        """
        # Delays importing matplotlib until it's actually requested
        from .matplotlib import setup_matplotlib_handlers

        setup_matplotlib_handlers(self, enable)

    wraps = registry_helpers.wraps

    check = registry_helpers.check


class LazyRegistry(Generic[facets.QuantityT, facets.UnitT]):
    def __init__(self, args=None, kwargs=None):
        self.__dict__["params"] = args or (), kwargs or {}

    def __init(self):
        args, kwargs = self.__dict__["params"]
        kwargs["on_redefinition"] = "raise"
        self.__class__ = UnitRegistry
        self.__init__(*args, **kwargs)
        self._after_init()

    def __getattr__(self, item):
        if item == "_on_redefinition":
            return "raise"
        self.__init()
        return getattr(self, item)

    def __setattr__(self, key, value):
        if key == "__class__":
            super().__setattr__(key, value)
        else:
            self.__init()
            setattr(self, key, value)

    def __getitem__(self, item):
        self.__init()
        return self[item]

    def __call__(self, *args, **kwargs):
        self.__init()
        return self(*args, **kwargs)


class ApplicationRegistry:
    """A wrapper class used to distribute changes to the application registry."""

    __slots__ = ["_registry"]

    def __init__(self, registry):
        self._registry = registry

    def get(self):
        """Get the wrapped registry"""
        return self._registry

    def set(self, new_registry):
        """Set the new registry

        Parameters
        ----------
        new_registry : ApplicationRegistry or LazyRegistry or UnitRegistry
            The new registry.

        See Also
        --------
        set_application_registry
        """
        if isinstance(new_registry, type(self)):
            new_registry = new_registry.get()

        if not isinstance(new_registry, (LazyRegistry, UnitRegistry)):
            raise TypeError("Expected UnitRegistry; got %s" % type(new_registry))
        logger.debug(
            "Changing app registry from %r to %r.", self._registry, new_registry
        )
        self._registry = new_registry

    def __getattr__(self, name):
        return getattr(self._registry, name)

    def __setattr__(self, name, value):
        if name in self.__slots__:
            super().__setattr__(name, value)
        else:
            setattr(self._registry, name, value)

    def __dir__(self):
        return dir(self._registry)

    def __getitem__(self, item):
        return self._registry[item]

    def __call__(self, *args, **kwargs):
        return self._registry(*args, **kwargs)

    def __contains__(self, item):
        return self._registry.__contains__(item)

    def __iter__(self):
        return iter(self._registry)
