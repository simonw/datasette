"""
    pint.delegates.txt_defparser.system
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import re
import typing as ty
from dataclasses import dataclass

import flexparser as fp

from ...facets.system import definitions
from ..base_defparser import PintParsedStatement
from . import block, common, plain


@dataclass(frozen=True)
class BaseUnitRule(PintParsedStatement, definitions.BaseUnitRule):
    @classmethod
    def from_string(cls, s: str) -> fp.NullableParsedResult[BaseUnitRule]:
        if ":" not in s:
            return cls(s.strip())
        parts = [p.strip() for p in s.split(":")]
        if len(parts) != 2:
            return common.DefinitionSyntaxError(
                f"Exactly two terms expected for rule, not {len(parts)} (`{s}`)"
            )
        return cls(*parts)


@dataclass(frozen=True)
class BeginSystem(PintParsedStatement):
    """Being of a system directive.

    @system <name> [using <group 1>, ..., <group N>]
    """

    #: Regex to match the header parts of a context.
    _header_re = re.compile(r"@system\s+(?P<name>\w+)\s*(using\s(?P<used_groups>.*))*")

    name: str
    using_group_names: ty.Tuple[str, ...]

    @classmethod
    def from_string(cls, s: str) -> fp.NullableParsedResult[BeginSystem]:
        if not s.startswith("@system"):
            return None

        r = cls._header_re.search(s)

        if r is None:
            raise ValueError("Invalid System header syntax '%s'" % s)

        name = r.groupdict()["name"].strip()
        groups = r.groupdict()["used_groups"]

        # If the systems has no group, it automatically uses the root group.
        if groups:
            group_names = tuple(a.strip() for a in groups.split(","))
        else:
            group_names = ("root",)

        return cls(name, group_names)


@dataclass(frozen=True)
class SystemDefinition(
    block.DirectiveBlock[
        definitions.SystemDefinition,
        BeginSystem,
        ty.Union[plain.CommentDefinition, BaseUnitRule],
    ]
):
    """Definition of a System:

        @system <name> [using <group 1>, ..., <group N>]
            <rule 1>
            ...
            <rule N>
        @end

    See Rule and Comment for more parsing related information.

    The syntax for the rule is:

        new_unit_name : old_unit_name

    where:
        - old_unit_name: a root unit part which is going to be removed from the system.
        - new_unit_name: a non root unit which is going to replace the old_unit.

    If the new_unit_name and the old_unit_name, the later and the colon can be omitted.
    """

    def derive_definition(self) -> definitions.SystemDefinition:
        return definitions.SystemDefinition(
            self.name, self.using_group_names, self.rules
        )

    @property
    def name(self) -> str:
        assert isinstance(self.opening, BeginSystem)
        return self.opening.name

    @property
    def using_group_names(self) -> tuple[str, ...]:
        assert isinstance(self.opening, BeginSystem)
        return self.opening.using_group_names

    @property
    def rules(self) -> tuple[BaseUnitRule, ...]:
        return tuple(el for el in self.body if isinstance(el, BaseUnitRule))
