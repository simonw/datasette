"""
    pint.facets.systems.definitions
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ... import errors
from ...compat import Self


@dataclass(frozen=True)
class BaseUnitRule:
    """A rule to define a base unit within a system."""

    #: name of the unit to become base unit
    #: (must exist in the registry)
    new_unit_name: str
    #: name of the unit to be kicked out to make room for the new base uni
    #: If None, the current base unit with the same dimensionality will be used
    old_unit_name: str | None = None

    # Instead of defining __post_init__ here,
    # it will be added to the container class
    # so that the name and a meaningfull class
    # could be used.


@dataclass(frozen=True)
class SystemDefinition(errors.WithDefErr):
    """Definition of a System."""

    #: name of the system
    name: str
    #: unit groups that will be included within the system
    using_group_names: tuple[str, ...]
    #: rules to define new base unit within the system.
    rules: tuple[BaseUnitRule, ...]

    @classmethod
    def from_lines(
        cls: type[Self], lines: Iterable[str], non_int_type: type
    ) -> Self | None:
        # TODO: this is to keep it backwards compatible
        # TODO: check when is None returned.
        from ...delegates import ParserConfig, txt_defparser

        cfg = ParserConfig(non_int_type)
        parser = txt_defparser.DefParser(cfg, None)
        pp = parser.parse_string("\n".join(lines) + "\n@end")
        for definition in parser.iter_parsed_project(pp):
            if isinstance(definition, cls):
                return definition

    @property
    def unit_replacements(self) -> tuple[tuple[str, str | None], ...]:
        # TODO: check if None can be dropped.
        return tuple((el.new_unit_name, el.old_unit_name) for el in self.rules)

    def __post_init__(self):
        if not errors.is_valid_system_name(self.name):
            raise self.def_err(errors.MSG_INVALID_SYSTEM_NAME)

        for k in self.using_group_names:
            if not errors.is_valid_group_name(k):
                raise self.def_err(
                    f"refers to '{k}' that " + errors.MSG_INVALID_GROUP_NAME
                )

        for ndx, rule in enumerate(self.rules, 1):
            if not errors.is_valid_unit_name(rule.new_unit_name):
                raise self.def_err(
                    f"rule #{ndx} refers to '{rule.new_unit_name}' that "
                    + errors.MSG_INVALID_UNIT_NAME
                )
            if rule.old_unit_name and not errors.is_valid_unit_name(rule.old_unit_name):
                raise self.def_err(
                    f"rule #{ndx} refers to '{rule.old_unit_name}' that "
                    + errors.MSG_INVALID_UNIT_NAME
                )
