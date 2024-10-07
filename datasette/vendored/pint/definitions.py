"""
    pint.definitions
    ~~~~~~~~~~~~~~~~

    Kept for backwards compatibility

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import flexparser as fp

from . import errors
from .delegates import ParserConfig, txt_defparser


class Definition:
    """This is kept for backwards compatibility"""

    @classmethod
    def from_string(cls, input_string: str, non_int_type: type = float) -> Definition:
        """Parse a string into a definition object.

        Parameters
        ----------
        input_string
            Single line string.
        non_int_type
            Numerical type used for non integer values.

        Raises
        ------
        DefinitionSyntaxError
            If a syntax error was found.
        """
        cfg = ParserConfig(non_int_type)
        parser = txt_defparser.DefParser(cfg, None)
        pp = parser.parse_string(input_string)
        for definition in parser.iter_parsed_project(pp):
            if isinstance(definition, Exception):
                raise errors.DefinitionSyntaxError(str(definition))
            if not isinstance(definition, (fp.BOS, fp.BOF, fp.BOS)):
                return definition

        # TODO: What shall we do in this return path.
