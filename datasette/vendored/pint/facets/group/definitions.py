"""
    pint.facets.group.definitions
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ... import errors
from ...compat import Self
from .. import plain


@dataclass(frozen=True)
class GroupDefinition(errors.WithDefErr):
    """Definition of a group."""

    #: name of the group
    name: str
    #: unit groups that will be included within the group
    using_group_names: tuple[str, ...]
    #: definitions for the units existing within the group
    definitions: tuple[plain.UnitDefinition, ...]

    @classmethod
    def from_lines(
        cls: type[Self], lines: Iterable[str], non_int_type: type
    ) -> Self | None:
        # TODO: this is to keep it backwards compatible
        from ...delegates import ParserConfig, txt_defparser

        cfg = ParserConfig(non_int_type)
        parser = txt_defparser.DefParser(cfg, None)
        pp = parser.parse_string("\n".join(lines) + "\n@end")
        for definition in parser.iter_parsed_project(pp):
            if isinstance(definition, cls):
                return definition

    @property
    def unit_names(self) -> tuple[str, ...]:
        return tuple(el.name for el in self.definitions)

    def __post_init__(self) -> None:
        if not errors.is_valid_group_name(self.name):
            raise self.def_err(errors.MSG_INVALID_GROUP_NAME)

        for k in self.using_group_names:
            if not errors.is_valid_group_name(k):
                raise self.def_err(
                    f"refers to '{k}' that " + errors.MSG_INVALID_GROUP_NAME
                )
