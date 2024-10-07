"""
    pint.facets.context.definitions
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import itertools
import numbers
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ... import errors
from ..plain import UnitDefinition

if TYPE_CHECKING:
    from ..._typing import Quantity, UnitsContainer


@dataclass(frozen=True)
class Relation:
    """Base class for a relation between different dimensionalities."""

    _varname_re = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    #: Source dimensionality
    src: UnitsContainer
    #: Destination dimensionality
    dst: UnitsContainer
    #: Equation connecting both dimensionalities from which the tranformation
    #: will be built.
    equation: str

    # Instead of defining __post_init__ here,
    # it will be added to the container class
    # so that the name and a meaningfull class
    # could be used.

    @property
    def variables(self) -> set[str]:
        """Find all variables names in the equation."""
        return set(self._varname_re.findall(self.equation))

    @property
    def transformation(self) -> Callable[..., Quantity]:
        """Return a transformation callable that uses the registry
        to parse the transformation equation.
        """
        return lambda ureg, value, **kwargs: ureg.parse_expression(
            self.equation, value=value, **kwargs
        )

    @property
    def bidirectional(self) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class ForwardRelation(Relation):
    """A relation connecting a dimension to another via a transformation function.

    <source dimension> -> <target dimension>: <transformation function>
    """

    @property
    def bidirectional(self) -> bool:
        return False


@dataclass(frozen=True)
class BidirectionalRelation(Relation):
    """A bidirectional relation connecting a dimension to another
    via a simple transformation function.

        <source dimension> <-> <target dimension>: <transformation function>

    """

    @property
    def bidirectional(self) -> bool:
        return True


@dataclass(frozen=True)
class ContextDefinition(errors.WithDefErr):
    """Definition of a Context"""

    #: name of the context
    name: str
    #: other na
    aliases: tuple[str, ...]
    defaults: dict[str, numbers.Number]
    relations: tuple[Relation, ...]
    redefinitions: tuple[UnitDefinition, ...]

    @property
    def variables(self) -> set[str]:
        """Return all variable names in all transformations."""
        return set().union(*(r.variables for r in self.relations))

    @classmethod
    def from_lines(cls, lines: Iterable[str], non_int_type: type):
        # TODO: this is to keep it backwards compatible
        from ...delegates import ParserConfig, txt_defparser

        cfg = ParserConfig(non_int_type)
        parser = txt_defparser.DefParser(cfg, None)
        pp = parser.parse_string("\n".join(lines) + "\n@end")
        for definition in parser.iter_parsed_project(pp):
            if isinstance(definition, cls):
                return definition

    def __post_init__(self):
        if not errors.is_valid_context_name(self.name):
            raise self.def_err(errors.MSG_INVALID_GROUP_NAME)

        for k in self.aliases:
            if not errors.is_valid_context_name(k):
                raise self.def_err(
                    f"refers to '{k}' that " + errors.MSG_INVALID_CONTEXT_NAME
                )

        for relation in self.relations:
            invalid = tuple(
                itertools.filterfalse(
                    errors.is_valid_dimension_name, relation.src.keys()
                )
            ) + tuple(
                itertools.filterfalse(
                    errors.is_valid_dimension_name, relation.dst.keys()
                )
            )

            if invalid:
                raise self.def_err(
                    f"relation refers to {', '.join(invalid)} that "
                    + errors.MSG_INVALID_DIMENSION_NAME
                )

        for definition in self.redefinitions:
            if definition.symbol != definition.name or definition.aliases:
                raise self.def_err(
                    "can't change a unit's symbol or aliases within a context"
                )
            if definition.is_base:
                raise self.def_err("can't define plain units within a context")

        missing_pars = set(self.defaults.keys()) - self.variables
        if missing_pars:
            raise self.def_err(
                f"Context parameters {missing_pars} not found in any equation"
            )
