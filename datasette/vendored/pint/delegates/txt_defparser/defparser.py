from __future__ import annotations

import pathlib
import typing as ty

import flexcache as fc
import flexparser as fp

from ..base_defparser import ParserConfig
from . import block, common, context, defaults, group, plain, system


class PintRootBlock(
    fp.RootBlock[
        ty.Union[
            plain.CommentDefinition,
            common.ImportDefinition,
            context.ContextDefinition,
            defaults.DefaultsDefinition,
            system.SystemDefinition,
            group.GroupDefinition,
            plain.AliasDefinition,
            plain.DerivedDimensionDefinition,
            plain.DimensionDefinition,
            plain.PrefixDefinition,
            plain.UnitDefinition,
        ],
        ParserConfig,
    ]
):
    pass


class _PintParser(fp.Parser[PintRootBlock, ParserConfig]):
    """Parser for the original Pint definition file, with cache."""

    _delimiters = {
        "#": (
            fp.DelimiterInclude.SPLIT_BEFORE,
            fp.DelimiterAction.CAPTURE_NEXT_TIL_EOL,
        ),
        **fp.SPLIT_EOL,
    }
    _root_block_class = PintRootBlock
    _strip_spaces = True

    _diskcache: fc.DiskCache | None

    def __init__(self, config: ParserConfig, *args: ty.Any, **kwargs: ty.Any):
        self._diskcache = kwargs.pop("diskcache", None)
        super().__init__(config, *args, **kwargs)

    def parse_file(
        self, path: pathlib.Path
    ) -> fp.ParsedSource[PintRootBlock, ParserConfig]:
        if self._diskcache is None:
            return super().parse_file(path)
        content, _basename = self._diskcache.load(path, super().parse_file)
        return content


class DefParser:
    skip_classes: tuple[type, ...] = (
        fp.BOF,
        fp.BOR,
        fp.BOS,
        fp.EOS,
        plain.CommentDefinition,
    )

    def __init__(self, default_config: ParserConfig, diskcache: fc.DiskCache):
        self._default_config = default_config
        self._diskcache = diskcache

    def iter_parsed_project(
        self, parsed_project: fp.ParsedProject[PintRootBlock, ParserConfig]
    ) -> ty.Generator[fp.ParsedStatement[ParserConfig], None, None]:
        last_location = None
        for stmt in parsed_project.iter_blocks():
            if isinstance(stmt, fp.BOS):
                if isinstance(stmt, fp.BOF):
                    last_location = str(stmt.path)
                    continue
                elif isinstance(stmt, fp.BOR):
                    last_location = (
                        f"[package: {stmt.package}, resource: {stmt.resource_name}]"
                    )
                    continue
                else:
                    last_location = "orphan string"
                    continue

            if isinstance(stmt, self.skip_classes):
                continue

            assert isinstance(last_location, str)
            if isinstance(stmt, common.DefinitionSyntaxError):
                stmt.set_location(last_location)
                raise stmt
            elif isinstance(stmt, block.DirectiveBlock):
                for exc in stmt.errors:
                    exc = common.DefinitionSyntaxError(str(exc))
                    exc.set_position(*stmt.get_position())
                    exc.set_raw(
                        (stmt.opening.raw or "") + " [...] " + (stmt.closing.raw or "")
                    )
                    exc.set_location(last_location)
                    raise exc

                try:
                    yield stmt.derive_definition()
                except Exception as exc:
                    exc = common.DefinitionSyntaxError(str(exc))
                    exc.set_position(*stmt.get_position())
                    exc.set_raw(stmt.opening.raw + " [...] " + stmt.closing.raw)
                    exc.set_location(last_location)
                    raise exc
            else:
                yield stmt

    def parse_file(
        self, filename: pathlib.Path | str, cfg: ParserConfig | None = None
    ) -> fp.ParsedProject[PintRootBlock, ParserConfig]:
        return fp.parse(
            filename,
            _PintParser,
            cfg or self._default_config,
            diskcache=self._diskcache,
            strip_spaces=True,
            delimiters=_PintParser._delimiters,
        )

    def parse_string(
        self, content: str, cfg: ParserConfig | None = None
    ) -> fp.ParsedProject[PintRootBlock, ParserConfig]:
        return fp.parse_bytes(
            content.encode("utf-8"),
            _PintParser,
            cfg or self._default_config,
            diskcache=self._diskcache,
            strip_spaces=True,
            delimiters=_PintParser._delimiters,
        )
