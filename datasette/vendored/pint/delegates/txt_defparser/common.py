"""
    pint.delegates.txt_defparser.common
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Definitions for parsing an Import Statement

    Also DefinitionSyntaxError

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import flexparser as fp

from ... import errors
from ..base_defparser import ParserConfig


@dataclass(frozen=True)
class DefinitionSyntaxError(errors.DefinitionSyntaxError, fp.ParsingError):
    """A syntax error was found in a definition. Combines:

    DefinitionSyntaxError: which provides a message placeholder.
    fp.ParsingError: which provides raw text, and start and end column and row

    and an extra location attribute in which the filename or reseource is stored.
    """

    location: str = field(init=False, default="")

    def __str__(self) -> str:
        msg = (
            self.msg + "\n    " + (self.format_position or "") + " " + (self.raw or "")
        )
        if self.location:
            msg += "\n    " + self.location
        return msg

    def set_location(self, value: str) -> None:
        super().__setattr__("location", value)


@dataclass(frozen=True)
class ImportDefinition(fp.IncludeStatement[ParserConfig]):
    value: str

    @property
    def target(self) -> str:
        return self.value

    @classmethod
    def from_string(cls, s: str) -> fp.NullableParsedResult[ImportDefinition]:
        if s.startswith("@import"):
            return ImportDefinition(s[len("@import") :].strip())
        return None
