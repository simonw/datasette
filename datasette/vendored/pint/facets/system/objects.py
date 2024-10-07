"""
    pint.facets.systems.objects
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import numbers
from collections.abc import Callable, Iterable
from numbers import Number
from typing import Any, Generic

from ..._typing import UnitLike
from ...babel_names import _babel_systems
from ...compat import babel_parse
from ...util import (
    SharedRegistryObject,
    getattr_maybe_raise,
    logger,
    to_units_container,
)
from .. import group
from ..plain import MagnitudeT
from .definitions import SystemDefinition

GetRootUnits = Callable[[UnitLike, bool], tuple[Number, UnitLike]]


class SystemQuantity(Generic[MagnitudeT], group.GroupQuantity[MagnitudeT]):
    pass


class SystemUnit(group.GroupUnit):
    pass


class System(SharedRegistryObject):
    """A system is a Group plus a set of plain units.

    Members are computed dynamically, that is if a unit is added to a group X
    all groups that include X are affected.

    The System belongs to one Registry.

    See SystemDefinition for the definition file syntax.

    Parameters
    ----------
    name
        Name of the group.
    """

    def __init__(self, name: str):
        #: Name of the system
        #: :type: str
        self.name = name

        #: Maps root unit names to a dict indicating the new unit and its exponent.
        self.base_units: dict[str, dict[str, numbers.Number]] = {}

        #: Derived unit names.
        self.derived_units: set[str] = set()

        #: Names of the _used_groups in used by this system.
        self._used_groups: set[str] = set()

        self._computed_members: frozenset[str] | None = None

        # Add this system to the system dictionary
        self._REGISTRY._systems[self.name] = self

    def __dir__(self):
        return list(self.members)

    def __getattr__(self, item: str) -> Any:
        getattr_maybe_raise(self, item)
        u = getattr(self._REGISTRY, self.name + "_" + item, None)
        if u is not None:
            return u
        return getattr(self._REGISTRY, item)

    @property
    def members(self):
        d = self._REGISTRY._groups
        if self._computed_members is None:
            tmp: set[str] = set()

            for group_name in self._used_groups:
                try:
                    tmp |= d[group_name].members
                except KeyError:
                    logger.warning(
                        "Could not resolve {} in System {}".format(
                            group_name, self.name
                        )
                    )

            self._computed_members = frozenset(tmp)

        return self._computed_members

    def invalidate_members(self):
        """Invalidate computed members in this Group and all parent nodes."""
        self._computed_members = None

    def add_groups(self, *group_names: str) -> None:
        """Add groups to group."""
        self._used_groups |= set(group_names)

        self.invalidate_members()

    def remove_groups(self, *group_names: str) -> None:
        """Remove groups from group."""
        self._used_groups -= set(group_names)

        self.invalidate_members()

    def format_babel(self, locale: str) -> str:
        """translate the name of the system."""
        if locale and self.name in _babel_systems:
            name = _babel_systems[self.name]
            locale = babel_parse(locale)
            return locale.measurement_systems[name]
        return self.name

    # TODO: When 3.11 is minimal version, use Self

    @classmethod
    def from_lines(
        cls: type[System],
        lines: Iterable[str],
        get_root_func: GetRootUnits,
        non_int_type: type = float,
    ) -> System:
        # TODO: we changed something here it used to be
        # system_definition = SystemDefinition.from_lines(lines, get_root_func)
        system_definition = SystemDefinition.from_lines(lines, non_int_type)

        if system_definition is None:
            raise ValueError(f"Could not define System from from {lines}")

        return cls.from_definition(system_definition, get_root_func)

    @classmethod
    def from_definition(
        cls: type[System],
        system_definition: SystemDefinition,
        get_root_func: GetRootUnits | None = None,
    ) -> System:
        if get_root_func is None:
            # TODO: kept for backwards compatibility
            get_root_func = cls._REGISTRY.get_root_units
        base_unit_names = {}
        derived_unit_names = []
        for new_unit, old_unit in system_definition.unit_replacements:
            if old_unit is None:
                old_unit_dict = to_units_container(get_root_func(new_unit)[1])

                if len(old_unit_dict) != 1:
                    raise ValueError(
                        "The new unit must be a root dimension if not discarded unit is specified."
                    )

                old_unit, value = dict(old_unit_dict).popitem()

                base_unit_names[old_unit] = {new_unit: 1 / value}
            else:
                # The old unit MUST be a root unit, if not raise an error.
                if old_unit != str(get_root_func(old_unit)[1]):
                    raise ValueError(
                        f"The old unit {old_unit} must be a root unit "
                        f"in order to be replaced by new unit {new_unit}"
                    )

                # Here we find new_unit expanded in terms of root_units
                new_unit_expanded = to_units_container(
                    get_root_func(new_unit)[1], cls._REGISTRY
                )

                # We require that the old unit is present in the new_unit expanded
                if old_unit not in new_unit_expanded:
                    raise ValueError("Old unit must be a component of new unit")

                # Here we invert the equation, in other words
                # we write old units in terms new unit and expansion
                new_unit_dict = {
                    new_unit: -1 / value
                    for new_unit, value in new_unit_expanded.items()
                    if new_unit != old_unit
                }
                new_unit_dict[new_unit] = 1 / new_unit_expanded[old_unit]

                base_unit_names[old_unit] = new_unit_dict

        system = cls(system_definition.name)
        system.add_groups(*system_definition.using_group_names)
        system.base_units.update(**base_unit_names)
        system.derived_units |= set(derived_unit_names)

        return system


class Lister:
    def __init__(self, d: dict[str, Any]):
        self.d = d

    def __dir__(self) -> list[str]:
        return list(self.d.keys())

    def __getattr__(self, item: str) -> Any:
        getattr_maybe_raise(self, item)
        return self.d[item]
