"""
    pint.delegates.txt_defparser.context
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Definitions for parsing Context and their related objects

    Notices that some of the checks are done within the
    format agnostic parent definition class.

    See each one for a slighly longer description of the
    syntax.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import numbers
import re
import typing as ty
from dataclasses import dataclass
from typing import Union

import flexparser as fp

from ...facets.context import definitions
from ..base_defparser import ParserConfig, PintParsedStatement
from . import block, common, plain

# TODO check syntax
T = ty.TypeVar("T", bound="Union[ForwardRelation, BidirectionalRelation]")


def _from_string_and_context_sep(
    cls: type[T], s: str, config: ParserConfig, separator: str
) -> T | None:
    if separator not in s:
        return None
    if ":" not in s:
        return None

    rel, eq = s.split(":")

    parts = rel.split(separator)

    src, dst = (config.to_dimension_container(s) for s in parts)

    return cls(src, dst, eq.strip())


@dataclass(frozen=True)
class ForwardRelation(PintParsedStatement, definitions.ForwardRelation):
    """A relation connecting a dimension to another via a transformation function.

    <source dimension> -> <target dimension>: <transformation function>
    """

    @classmethod
    def from_string_and_config(
        cls, s: str, config: ParserConfig
    ) -> fp.NullableParsedResult[ForwardRelation]:
        return _from_string_and_context_sep(cls, s, config, "->")


@dataclass(frozen=True)
class BidirectionalRelation(PintParsedStatement, definitions.BidirectionalRelation):
    """A bidirectional relation connecting a dimension to another
    via a simple transformation function.

        <source dimension> <-> <target dimension>: <transformation function>

    """

    @classmethod
    def from_string_and_config(
        cls, s: str, config: ParserConfig
    ) -> fp.NullableParsedResult[BidirectionalRelation]:
        return _from_string_and_context_sep(cls, s, config, "<->")


@dataclass(frozen=True)
class BeginContext(PintParsedStatement):
    """Being of a context directive.

    @context[(defaults)] <canonical name> [= <alias>] [= <alias>]
    """

    _header_re = re.compile(
        r"@context\s*(?P<defaults>\(.*\))?\s+(?P<name>\w+)\s*(=(?P<aliases>.*))*"
    )

    name: str
    aliases: tuple[str, ...]
    defaults: dict[str, numbers.Number]

    @classmethod
    def from_string_and_config(
        cls, s: str, config: ParserConfig
    ) -> fp.NullableParsedResult[BeginContext]:
        try:
            r = cls._header_re.search(s)
            if r is None:
                return None
            name = r.groupdict()["name"].strip()
            aliases = r.groupdict()["aliases"]
            if aliases:
                aliases = tuple(a.strip() for a in r.groupdict()["aliases"].split("="))
            else:
                aliases = ()
            defaults = r.groupdict()["defaults"]
        except Exception as exc:
            return common.DefinitionSyntaxError(
                f"Could not parse the Context header '{s}': {exc}"
            )

        if defaults:
            txt = defaults
            try:
                defaults = (part.split("=") for part in defaults.strip("()").split(","))
                defaults = {str(k).strip(): config.to_number(v) for k, v in defaults}
            except (ValueError, TypeError) as exc:
                return common.DefinitionSyntaxError(
                    f"Could not parse Context definition defaults '{txt}' {exc}"
                )
        else:
            defaults = {}

        return cls(name, tuple(aliases), defaults)


@dataclass(frozen=True)
class ContextDefinition(
    block.DirectiveBlock[
        definitions.ContextDefinition,
        BeginContext,
        ty.Union[
            plain.CommentDefinition,
            BidirectionalRelation,
            ForwardRelation,
            plain.UnitDefinition,
        ],
    ]
):
    """Definition of a Context

        @context[(defaults)] <canonical name> [= <alias>] [= <alias>]
            # units can be redefined within the context
            <redefined unit> = <relation to another unit>

            # can establish unidirectional relationships between dimensions
            <dimension 1> -> <dimension 2>: <transformation function>

            # can establish bidirectionl relationships between dimensions
            <dimension 3> <-> <dimension 4>: <transformation function>
        @end

    See BeginContext, Equality, ForwardRelation, BidirectionalRelation and
    Comment for more parsing related information.

    Example::

        @context(n=1) spectroscopy = sp
            # n index of refraction of the medium.
            [length] <-> [frequency]: speed_of_light / n / value
            [frequency] -> [energy]: planck_constant * value
            [energy] -> [frequency]: value / planck_constant
            # allow wavenumber / kayser
            [wavenumber] <-> [length]: 1 / value
        @end
    """

    def derive_definition(self) -> definitions.ContextDefinition:
        return definitions.ContextDefinition(
            self.name, self.aliases, self.defaults, self.relations, self.redefinitions
        )

    @property
    def name(self) -> str:
        assert isinstance(self.opening, BeginContext)
        return self.opening.name

    @property
    def aliases(self) -> tuple[str, ...]:
        assert isinstance(self.opening, BeginContext)
        return self.opening.aliases

    @property
    def defaults(self) -> dict[str, numbers.Number]:
        assert isinstance(self.opening, BeginContext)
        return self.opening.defaults

    @property
    def relations(self) -> tuple[BidirectionalRelation | ForwardRelation, ...]:
        return tuple(
            r
            for r in self.body
            if isinstance(r, (ForwardRelation, BidirectionalRelation))
        )

    @property
    def redefinitions(self) -> tuple[plain.UnitDefinition, ...]:
        return tuple(r for r in self.body if isinstance(r, plain.UnitDefinition))
