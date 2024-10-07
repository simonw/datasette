"""
    pint.facets.group.objects
    ~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
from typing import TYPE_CHECKING, Any, Generic

from ...util import SharedRegistryObject, getattr_maybe_raise
from ..plain import MagnitudeT, PlainQuantity, PlainUnit
from .definitions import GroupDefinition

if TYPE_CHECKING:
    from ..plain import UnitDefinition

    DefineFunc = Callable[
        [
            Any,
        ],
        None,
    ]
    AddUnitFunc = Callable[
        [
            UnitDefinition,
        ],
        None,
    ]


class GroupQuantity(Generic[MagnitudeT], PlainQuantity[MagnitudeT]):
    pass


class GroupUnit(PlainUnit):
    pass


class Group(SharedRegistryObject):
    """A group is a set of units.

    Units can be added directly or by including other groups.

    Members are computed dynamically, that is if a unit is added to a group X
    all groups that include X are affected.

    The group belongs to one Registry.

    See GroupDefinition for the definition file syntax.

    Parameters
    ----------
    name
        If not given, a root Group will be created.
    """

    def __init__(self, name: str):
        # The name of the group.
        self.name = name

        #: Names of the units in this group.
        #: :type: set[str]
        self._unit_names: set[str] = set()

        #: Names of the groups in this group.
        self._used_groups: set[str] = set()

        #: Names of the groups in which this group is contained.
        self._used_by: set[str] = set()

        # Add this group to the group dictionary
        self._REGISTRY._groups[self.name] = self

        if name != "root":
            # All groups are added to root group
            self._REGISTRY._groups["root"].add_groups(name)

        #: A cache of the included units.
        #: None indicates that the cache has been invalidated.
        self._computed_members: frozenset[str] | None = None

    @property
    def members(self) -> frozenset[str]:
        """Names of the units that are members of the group.

        Calculated to include to all units in all included _used_groups.

        """
        if self._computed_members is None:
            tmp = set(self._unit_names)

            for _, group in self.iter_used_groups():
                tmp |= group.members

            self._computed_members = frozenset(tmp)

        return self._computed_members

    def invalidate_members(self) -> None:
        """Invalidate computed members in this Group and all parent nodes."""
        self._computed_members = None
        d = self._REGISTRY._groups
        for name in self._used_by:
            d[name].invalidate_members()

    def iter_used_groups(self) -> Generator[tuple[str, Group], None, None]:
        pending = set(self._used_groups)
        d = self._REGISTRY._groups
        while pending:
            name = pending.pop()
            group = d[name]
            pending |= group._used_groups
            yield name, d[name]

    def is_used_group(self, group_name: str) -> bool:
        for name, _ in self.iter_used_groups():
            if name == group_name:
                return True
        return False

    def add_units(self, *unit_names: str) -> None:
        """Add units to group."""
        for unit_name in unit_names:
            self._unit_names.add(unit_name)

        self.invalidate_members()

    @property
    def non_inherited_unit_names(self) -> frozenset[str]:
        return frozenset(self._unit_names)

    def remove_units(self, *unit_names: str) -> None:
        """Remove units from group."""
        for unit_name in unit_names:
            self._unit_names.remove(unit_name)

        self.invalidate_members()

    def add_groups(self, *group_names: str) -> None:
        """Add groups to group."""
        d = self._REGISTRY._groups
        for group_name in group_names:
            grp = d[group_name]

            if grp.is_used_group(self.name):
                raise ValueError(
                    "Cyclic relationship found between %s and %s"
                    % (self.name, group_name)
                )

            self._used_groups.add(group_name)
            grp._used_by.add(self.name)

        self.invalidate_members()

    def remove_groups(self, *group_names: str) -> None:
        """Remove groups from group."""
        d = self._REGISTRY._groups
        for group_name in group_names:
            grp = d[group_name]

            self._used_groups.remove(group_name)
            grp._used_by.remove(self.name)

        self.invalidate_members()

    @classmethod
    def from_lines(
        cls, lines: Iterable[str], define_func: DefineFunc, non_int_type: type = float
    ) -> Group:
        """Return a Group object parsing an iterable of lines.

        Parameters
        ----------
        lines : list[str]
            iterable
        define_func : callable
            Function to define a unit in the registry; it must accept a single string as
            a parameter.

        Returns
        -------

        """
        group_definition = GroupDefinition.from_lines(lines, non_int_type)

        if group_definition is None:
            raise ValueError(f"Could not define group from {lines}")

        return cls.from_definition(group_definition, define_func)

    @classmethod
    def from_definition(
        cls,
        group_definition: GroupDefinition,
        add_unit_func: AddUnitFunc | None = None,
    ) -> Group:
        grp = cls(group_definition.name)

        add_unit_func = add_unit_func or grp._REGISTRY._add_unit

        # We first add all units defined within the group
        # to the registry.
        for definition in group_definition.definitions:
            add_unit_func(definition)

        # Then we add all units defined within the group
        # to this group (by name)
        grp.add_units(*group_definition.unit_names)

        # Finally, we add all grou0ps used by this group
        # tho this group (by name)
        if group_definition.using_group_names:
            grp.add_groups(*group_definition.using_group_names)

        return grp

    def __getattr__(self, item: str):
        getattr_maybe_raise(self, item)
        return self._REGISTRY
