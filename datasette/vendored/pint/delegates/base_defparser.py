"""
    pint.delegates.base_defparser
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Common class and function for all parsers.

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import functools
import itertools
import numbers
import pathlib
from dataclasses import dataclass
from typing import Any

import flexcache as fc
import flexparser as fp

from datasette.vendored.pint import errors
from datasette.vendored.pint.facets.plain.definitions import NotNumeric
from datasette.vendored.pint.util import ParserHelper, UnitsContainer


@dataclass(frozen=True)
class ParserConfig:
    """Configuration used by the parser in Pint."""

    #: Indicates the output type of non integer numbers.
    non_int_type: type[numbers.Number] = float

    def to_scaled_units_container(self, s: str):
        return ParserHelper.from_string(s, self.non_int_type)

    def to_units_container(self, s: str):
        v = self.to_scaled_units_container(s)
        if v.scale != 1:
            raise errors.UnexpectedScaleInContainer(str(v.scale))
        return UnitsContainer(v)

    def to_dimension_container(self, s: str):
        v = self.to_units_container(s)
        invalid = tuple(itertools.filterfalse(errors.is_valid_dimension_name, v.keys()))
        if invalid:
            raise errors.DefinitionSyntaxError(
                f"Cannot build a dimension container with {', '.join(invalid)} that "
                + errors.MSG_INVALID_DIMENSION_NAME
            )
        return v

    def to_number(self, s: str) -> numbers.Number:
        """Try parse a string into a number (without using eval).

        The string can contain a number or a simple equation (3 + 4)

        Raises
        ------
        _NotNumeric
            If the string cannot be parsed as a number.
        """
        val = self.to_scaled_units_container(s)
        if len(val):
            raise NotNumeric(s)
        return val.scale


@dataclass(frozen=True)
class PintParsedStatement(fp.ParsedStatement[ParserConfig]):
    """A parsed statement for pint, specialized in the actual config."""


@functools.lru_cache
def build_disk_cache_class(chosen_non_int_type: type):
    """Build disk cache class, taking into account the non_int_type."""

    @dataclass(frozen=True)
    class PintHeader(fc.InvalidateByExist, fc.NameByFields, fc.BasicPythonHeader):
        from .. import __version__

        pint_version: str = __version__
        non_int_type: str = chosen_non_int_type.__qualname__

    @dataclass(frozen=True)
    class PathHeader(fc.NameByFileContent, PintHeader):
        pass

    @dataclass(frozen=True)
    class ParsedProjecHeader(fc.NameByHashIter, PintHeader):
        @classmethod
        def from_parsed_project(
            cls, pp: fp.ParsedProject[Any, ParserConfig], reader_id: str
        ):
            tmp = (
                f"{stmt.content_hash.algorithm_name}:{stmt.content_hash.hexdigest}"
                for stmt in pp.iter_statements()
                if isinstance(stmt, fp.BOS)
            )

            return cls(tuple(tmp), reader_id)

    class PintDiskCache(fc.DiskCache):
        _header_classes = {
            pathlib.Path: PathHeader,
            str: PathHeader.from_string,
            fp.ParsedProject: ParsedProjecHeader.from_parsed_project,
        }

    return PintDiskCache
