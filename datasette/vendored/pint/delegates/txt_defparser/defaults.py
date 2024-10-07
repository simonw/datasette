"""
    pint.delegates.txt_defparser.defaults
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Definitions for parsing Default sections.

    See each one for a slighly longer description of the
    syntax.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import typing as ty
from dataclasses import dataclass, fields

import flexparser as fp

from ...facets.plain import definitions
from ..base_defparser import PintParsedStatement
from . import block, plain


@dataclass(frozen=True)
class BeginDefaults(PintParsedStatement):
    """Being of a defaults directive.

    @defaults
    """

    @classmethod
    def from_string(cls, s: str) -> fp.NullableParsedResult[BeginDefaults]:
        if s.strip() == "@defaults":
            return cls()
        return None


@dataclass(frozen=True)
class DefaultsDefinition(
    block.DirectiveBlock[
        definitions.DefaultsDefinition,
        BeginDefaults,
        ty.Union[
            plain.CommentDefinition,
            plain.Equality,
        ],
    ]
):
    """Directive to store values.

        @defaults
            system = mks
        @end

    See Equality and Comment for more parsing related information.
    """

    @property
    def _valid_fields(self) -> tuple[str, ...]:
        return tuple(f.name for f in fields(definitions.DefaultsDefinition))

    def derive_definition(self) -> definitions.DefaultsDefinition:
        for definition in self.filter_by(plain.Equality):
            if definition.lhs not in self._valid_fields:
                raise ValueError(
                    f"`{definition.lhs}` is not a valid key "
                    f"for the default section. {self._valid_fields}"
                )

        return definitions.DefaultsDefinition(
            *tuple(self.get_key(key) for key in self._valid_fields)
        )

    def get_key(self, key: str) -> str:
        for stmt in self.body:
            if isinstance(stmt, plain.Equality) and stmt.lhs == key:
                return stmt.rhs
        raise KeyError(key)
