"""
    pint.delegates.txt_defparser.block
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Classes for Pint Blocks, which are defined by:

        @<block name>
            <content>
        @end

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

import flexparser as fp

from ..base_defparser import ParserConfig, PintParsedStatement


@dataclass(frozen=True)
class EndDirectiveBlock(PintParsedStatement):
    """An EndDirectiveBlock is simply an "@end" statement."""

    @classmethod
    def from_string(cls, s: str) -> fp.NullableParsedResult[EndDirectiveBlock]:
        if s == "@end":
            return cls()
        return None


OPST = TypeVar("OPST", bound="PintParsedStatement")
IPST = TypeVar("IPST", bound="PintParsedStatement")

DefT = TypeVar("DefT")


@dataclass(frozen=True)
class DirectiveBlock(
    Generic[DefT, OPST, IPST], fp.Block[OPST, IPST, EndDirectiveBlock, ParserConfig]
):
    """Directive blocks have beginning statement starting with a @ character.
    and ending with a "@end" (captured using a EndDirectiveBlock).

    Subclass this class for convenience.
    """

    def derive_definition(self) -> DefT: ...
