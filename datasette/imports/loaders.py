"""
File format loaders for importing files into Datasette.

A loader recognizes one file format from the first bytes of a file
(``sniff()``) and turns a file in that format into rows (``load()``).
Plugins add loaders with the ``register_import_loaders()`` plugin hook. The
CSV, TSV, JSON and newline-delimited JSON loaders in this module are
registered the same way, as a default plugin.

Nothing here needs a Datasette instance, so the same code can run in the
``datasette`` process or in an import worker subprocess.
"""

import codecs
import csv
import dataclasses
import functools
import io
import itertools
import json
import logging
import math
import re
from collections.abc import Iterable, Iterator
from typing import Any, ClassVar

from sqlite_utils.utils import maximize_csv_field_size_limit

from datasette.hookspecs import hookimpl

logger = logging.getLogger(__name__)

# How many leading bytes of a file are passed to sniff() at first
SNIFF_BYTES = 64 * 1024
# A loader must score at least this much to be picked
MIN_CONFIDENCE = 0.1
# A best score below this means "maybe": sniff_path() reads four times as
# many bytes and asks every loader again, up to MAX_SNIFF_BYTES
DECISIVE_CONFIDENCE = 0.7
MAX_SNIFF_BYTES = 1024 * 1024
# Column types a Schema can declare - the same names that
# sqlite_utils.utils.TypeTracker produces
SCHEMA_TYPES = ("text", "integer", "float", "blob")
# CSV and TSV rows with more fields than the header keep the extra values, as
# a list, in this column
EXTRAS_KEY = "_extra"


class LoaderError(Exception):
    """Raised when a file cannot be loaded."""


class UnrecognizedFormat(LoaderError):
    """No loader is confident enough that it can read this file."""

    def __init__(self, message, scores=None):
        super().__init__(message)
        self.scores = scores or []


class EmptyFile(UnrecognizedFormat):
    """The file is empty, or contains only whitespace."""


@dataclasses.dataclass
class Schema:
    """
    Column names and SQLite types, yielded by ``Loader.load()`` before any rows.

    ``columns`` maps each column name to one of ``"text"``, ``"integer"``,
    ``"float"`` or ``"blob"``, in table order. Columns listed here skip type
    detection. Keys that appear in rows but not in the schema are added as
    columns and have their types detected as usual.
    """

    columns: dict[str, str]

    def __post_init__(self):
        columns = {}
        for name, type_ in dict(self.columns).items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"Schema column names must be strings: {name!r}")
            normalized = str(type_).lower()
            if normalized == "real":
                normalized = "float"
            if normalized not in SCHEMA_TYPES:
                raise ValueError(
                    f"Column {name!r} has type {type_!r}, expected one of "
                    + ", ".join(SCHEMA_TYPES)
                )
            columns[name] = normalized
        self.columns = columns

    def to_dict(self) -> dict:
        """A JSON-serializable dictionary, suitable for one line of NDJSON."""
        return {"columns": dict(self.columns)}

    @classmethod
    def from_dict(cls, data: dict) -> "Schema":
        return cls(columns=data["columns"])


@dataclasses.dataclass(frozen=True)
class Note:
    """
    Something the person importing a file should know about how it was
    read, yielded by ``Loader.load()`` anywhere among its rows - for example
    that only part of a JSON document held the rows. Datasette shows notes
    with the import (they end up in :attr:`LoadResult.notes`) and logs each
    one as a warning.
    """

    message: str

    def __post_init__(self):
        if not isinstance(self.message, str) or not self.message:
            raise ValueError(f"A Note needs a message: {self.message!r}")

    def __str__(self):
        return self.message


#: The log record of a :class:`Note` has this attribute, set to the loader's
#: name: the import worker sends those to the parent marked as notes
NOTE_LOG_ATTRIBUTE = "datasette_import_note"


class Loader:
    """
    Base class for file format loaders. Subclass this and implement
    ``sniff()`` and ``load()``.
    """

    #: Short unique identifier for this format, e.g. ``"csv"``
    name: str = ""
    #: Human-readable description, e.g. ``"Comma-separated values"``
    description: str = ""

    def sniff(self, first_bytes: bytes) -> float:
        """
        Return a confidence between 0.0 and 1.0 that ``first_bytes`` is the
        start of a file this loader can read. The bytes may stop part way
        through a record, or even part way through a UTF-8 character.
        """
        return 0.0

    def load(self, path: str) -> Iterable["dict | Schema | Note"]:
        """
        Yield an optional :class:`Schema`, then one dictionary per row - and,
        at any point, a :class:`Note` for something the person importing the
        file should know.
        """
        raise NotImplementedError

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name!r}>"


@dataclasses.dataclass
class LoaderScore:
    """How confident one loader was about one file."""

    loader: Loader
    score: float
    error: str | None = None

    @property
    def name(self) -> str:
        return self.loader.name


@dataclasses.dataclass
class LoadResult:
    """The outcome of :func:`load_path`."""

    loader: Loader
    schema: Schema | None
    rows: Iterator[dict]
    scores: list[LoaderScore]
    #: The messages of the :class:`Note` items the loader has yielded: the
    #: list grows while ``rows`` is consumed, and is complete once it is
    notes: list[str] = dataclasses.field(default_factory=list)


def get_loaders() -> list[Loader]:
    """
    Every loader registered with the ``register_import_loaders()`` hook.

    A plugin loader with the same name as a core loader replaces it. If two
    plugin loaders share a name, the one whose class has the lowest
    ``module.qualname`` wins and a warning is logged. A plugin whose hook
    raises, or whose loader class cannot be instantiated, is skipped with a
    logged warning, so it cannot break imports of other formats.
    """
    by_name: dict[str, Loader] = {}
    for plugin_name, item in _registered_items():
        if isinstance(item, type):
            try:
                loader = item()
            except Exception:
                logger.warning(
                    "Plugin %s: could not create import loader %r",
                    plugin_name,
                    item,
                    exc_info=True,
                )
                continue
        else:
            loader = item
        name = getattr(loader, "name", None)
        if not isinstance(name, str) or not name:
            logger.warning("Ignoring import loader with no name: %r", loader)
            continue
        if not callable(getattr(loader, "sniff", None)) or not callable(
            getattr(loader, "load", None)
        ):
            logger.warning("Ignoring import loader %r: needs sniff() and load()", name)
            continue
        existing = by_name.get(name)
        if existing is None or (_is_core(existing) and not _is_core(loader)):
            by_name[name] = loader
        elif not _is_core(loader):
            winner = min(existing, loader, key=_class_path)
            logger.warning(
                "Two import loaders are called %r: %s and %s - using %s",
                name,
                _class_path(existing),
                _class_path(loader),
                _class_path(winner),
            )
            by_name[name] = winner
    return list(by_name.values())


def _registered_items():
    # Call each register_import_loaders() implementation separately, in the
    # order pluggy would, so one that raises doesn't hide all the others
    from datasette.plugins import pm

    for impl in reversed(pm.hook.register_import_loaders.get_hookimpls()):
        try:
            result = impl.function()
            items = list(result or [])
        except Exception:
            logger.warning(
                "Plugin %s: register_import_loaders() failed",
                impl.plugin_name,
                exc_info=True,
            )
            continue
        for item in items:
            yield impl.plugin_name, item


def get_loader(name: str, loaders: Iterable[Loader] | None = None) -> Loader:
    """The loader called ``name``, for when the format is given explicitly."""
    loaders = get_loaders() if loaders is None else list(loaders)
    for loader in loaders:
        if loader.name == name:
            return loader
    raise LoaderError(
        f"Unknown format {name!r}, available formats: "
        + ", ".join(sorted(loader.name for loader in loaders))
    )


def score_loaders(
    first_bytes: bytes, loaders: Iterable[Loader] | None = None
) -> list[LoaderScore]:
    """
    Ask every loader to sniff ``first_bytes``. Returns scores ranked best
    first: highest score, then plugin loaders ahead of core loaders, then
    loader name.

    A loader whose ``sniff()`` raises, or returns something that is not a
    number, scores 0.0 and the error is recorded. Scores are clamped to the
    range 0.0 to 1.0.
    """
    loaders = get_loaders() if loaders is None else list(loaders)
    scores = []
    for loader in loaders:
        try:
            score = float(loader.sniff(first_bytes))
            if math.isnan(score):
                raise ValueError("sniff() returned NaN")
        except Exception as ex:
            logger.warning(
                "Import loader %r sniff() failed", loader.name, exc_info=True
            )
            scores.append(LoaderScore(loader, 0.0, f"{type(ex).__name__}: {ex}"))
            continue
        scores.append(LoaderScore(loader, min(max(score, 0.0), 1.0)))
    scores.sort(key=lambda s: (-s.score, _is_core(s.loader), s.loader.name))
    return scores


def pick_loader(
    first_bytes: bytes,
    loaders: Iterable[Loader] | None = None,
    min_confidence: float = MIN_CONFIDENCE,
) -> Loader:
    """
    The loader that is most confident it can read a file starting with
    ``first_bytes``. Raises :class:`EmptyFile` for an empty file and
    :class:`UnrecognizedFormat` if no loader scores ``min_confidence`` or more.
    """
    return choose_loader(
        score_loaders(first_bytes, loaders), first_bytes, min_confidence
    )


def sniff_windows(
    fp, sniff_bytes: int = SNIFF_BYTES, max_sniff_bytes: int = MAX_SNIFF_BYTES
) -> Iterator[bytes]:
    """
    Yield ever longer windows from the start of the binary file ``fp``:
    ``sniff_bytes``, then four times as many each time, up to
    ``max_sniff_bytes`` or the end of the file. Stop iterating once a window
    is decisive.

    :func:`sniff_path` uses this. A loader whose ``load()`` needs to repeat a
    decision it made in ``sniff()``, such as which delimiter a CSV file uses,
    can use it too: stopping at the first window where its own score reaches
    ``DECISIVE_CONFIDENCE`` sees the same bytes ``sniff_path()`` saw when it
    picked that loader.
    """
    size = sniff_bytes
    first_bytes = fp.read(size)
    while True:
        yield first_bytes
        if len(first_bytes) < size or size >= max_sniff_bytes:
            return
        size = min(size * 4, max_sniff_bytes)
        first_bytes += fp.read(size - len(first_bytes))


def sniff_path(
    path: str,
    loaders: Iterable[Loader] | None = None,
    sniff_bytes: int = SNIFF_BYTES,
    max_sniff_bytes: int = MAX_SNIFF_BYTES,
) -> tuple[bytes, list[LoaderScore]]:
    """
    Score every loader against the start of the file at ``path``. While no
    loader reaches ``DECISIVE_CONFIDENCE`` and the file is longer than the
    window, read four times as many bytes and score again, up to
    ``max_sniff_bytes``. Returns ``(first_bytes, scores)``.
    """
    loaders = get_loaders() if loaders is None else list(loaders)
    with open(path, "rb") as fp:
        for first_bytes in sniff_windows(fp, sniff_bytes, max_sniff_bytes):
            scores = score_loaders(first_bytes, loaders)
            if scores and scores[0].score >= DECISIVE_CONFIDENCE:
                break
    _peek.cache_clear()
    return first_bytes, scores


def load_path(
    path: str,
    loaders: Iterable[Loader] | None = None,
    format: str | None = None,
) -> LoadResult:
    """
    Pick a loader for the file at ``path`` using :func:`sniff_path` (or use
    the loader called ``format``) and start loading it. The returned ``rows``
    iterator is lazy.
    """
    loaders = get_loaders() if loaders is None else list(loaders)
    if format is not None:
        loader = get_loader(format, loaders)
        scores = []
    else:
        first_bytes, scores = sniff_path(path, loaders)
        loader = choose_loader(scores, first_bytes)
    notes = []
    schema, rows = split_schema(loader.load(path), loader.name, notes=notes)
    return LoadResult(
        loader=loader, schema=schema, rows=rows, scores=scores, notes=notes
    )


def split_schema(
    items: Iterable["dict | Schema | Note"],
    loader_name: str = "loader",
    notes: list | None = None,
) -> tuple[Schema | None, Iterator[dict]]:
    """
    Split what ``Loader.load()`` yields into ``(schema_or_None, rows)``.
    Raises :class:`LoaderError` while iterating if a loader yields a schema
    after the first row, or anything other than a dictionary or a
    :class:`Note`.

    Each :class:`Note` is logged as a warning on this module's logger, with
    the loader's name in the record's ``datasette_import_note`` attribute,
    and if ``notes`` is a list its message is appended to it - as the rows
    are consumed (notes before the schema or the first row straight away).
    """
    iterator = iter(items)

    def note(item):
        if notes is not None:
            notes.append(item.message)
        logger.warning(
            "%s", item.message, extra={NOTE_LOG_ATTRIBUTE: loader_name or "loader"}
        )

    first = _MISSING
    for item in iterator:
        if isinstance(item, Note):
            note(item)
            continue
        first = item
        break
    if first is _MISSING:
        return None, iter(())
    schema = None
    if isinstance(first, Schema):
        schema = first
    else:
        iterator = itertools.chain([first], iterator)

    def rows():
        for row in iterator:
            if not isinstance(row, dict):
                if isinstance(row, Note):
                    note(row)
                    continue
                if isinstance(row, Schema):
                    raise LoaderError(
                        f"{loader_name} yielded a Schema after the first item"
                    )
                raise LoaderError(
                    f"{loader_name} yielded {type(row).__name__}, expected a dict"
                )
            yield row

    return schema, rows()


def choose_loader(
    scores: list[LoaderScore],
    first_bytes: bytes,
    min_confidence: float = MIN_CONFIDENCE,
) -> Loader:
    """
    The loader to use for a file that starts with ``first_bytes``, given the
    ``scores`` that :func:`score_loaders` or :func:`sniff_path` returned for
    it (ranked best first): the best one, if it scored ``min_confidence`` or
    more. Raises :class:`EmptyFile` if ``first_bytes`` holds nothing but
    whitespace (after any UTF-8 byte order mark), otherwise
    :class:`UnrecognizedFormat` if no loader is confident enough. Both
    errors carry the scores in ``.scores``.
    """
    if not first_bytes.removeprefix(codecs.BOM_UTF8).strip():
        raise EmptyFile("File is empty", scores)
    if scores and scores[0].score >= min_confidence:
        return scores[0].loader
    summary = ", ".join(f"{s.name}={s.score:.2f}" for s in scores) or "no loaders"
    raise UnrecognizedFormat(
        f"Could not recognize the file format ({summary}; "
        f"a loader needs at least {min_confidence})",
        scores,
    )


def _is_core(loader) -> bool:
    return type(loader) in _CORE_LOADER_CLASSES


def _class_path(loader) -> str:
    cls = type(loader)
    return f"{cls.__module__}.{cls.__qualname__}"


# Text decoding: UTF-8, falling back to cp1252 / latin-1 for invalid bytes
#
# Spreadsheet exports are often cp1252 (Windows "ANSI"). Rather than guess one
# encoding for the whole file, decode as UTF-8 and decode invalid bytes as
# cp1252, or as latin-1 for the five bytes cp1252 leaves undefined. That works
# no matter how far into the file the first non-UTF-8 byte appears, and in
# files that mix the two. A leading byte order mark is removed.
#
# Python calls the error handler for each invalid byte, which made files that
# really are cp1252 four times slower to decode. So the handler decodes
# everything up to the next valid UTF-8 multi-byte sequence in one go.

_FALLBACK_ERRORS = "datasette-cp1252-fallback"
#: Pass this as an ``encoding=`` to decode text with the fallback described above
TEXT_ENCODING = "datasette-utf8"


def _byte_to_text(byte: int) -> str:
    try:
        return bytes([byte]).decode("cp1252")
    except UnicodeDecodeError:
        return chr(byte)


_FALLBACK_TABLE = "".join(_byte_to_text(byte) for byte in range(256))
# Where a valid UTF-8 multi-byte sequence could start: a lead byte then a
# continuation byte. Deliberately loose (a precise pattern is four times
# slower to search): if a match is not valid UTF-8 after all, the decoder
# calls the error handler again from there
_UTF8_SEQUENCE_RE = re.compile(rb"[\xc2-\xf4][\x80-\xbf]")


def _cp1252_fallback(error):
    if not isinstance(error, UnicodeDecodeError):
        raise error
    data = error.object
    match = _UTF8_SEQUENCE_RE.search(data, error.end)
    end = match.start() if match else len(data)
    # Leave the last three bytes to the UTF-8 decoder: they could be the start
    # of a character that continues in the next chunk of the file
    end = max(error.end, min(end, len(data) - 3))
    text, _ = codecs.charmap_decode(data[error.start : end], "strict", _FALLBACK_TABLE)
    return text, end


_UTF8 = codecs.lookup("utf-8")
_UTF8_SIG = codecs.lookup("utf-8-sig")


class _FallbackIncrementalDecoder(_UTF8_SIG.incrementaldecoder):
    def __init__(self, errors="strict"):
        super().__init__(_FALLBACK_ERRORS)


def _fallback_decode(input, errors="strict"):
    return _UTF8_SIG.decode(input, _FALLBACK_ERRORS)


def _search_codec(name):
    if name.replace("-", "_").lower() != "datasette_utf8":
        return None
    return codecs.CodecInfo(
        name=TEXT_ENCODING,
        # Encodes as plain UTF-8, with no byte order mark
        encode=_UTF8.encode,
        decode=_fallback_decode,
        incrementalencoder=_UTF8.incrementalencoder,
        incrementaldecoder=_FallbackIncrementalDecoder,
        streamwriter=_UTF8.streamwriter,
    )


codecs.register_error(_FALLBACK_ERRORS, _cp1252_fallback)
codecs.register(_search_codec)


# Peeking at JSON that has been cut off by the end of the sniff window


class PartialList(list):
    """A JSON array that was cut off by the end of the sniff window."""


class PartialDict(dict):
    """A JSON object that was cut off by the end of the sniff window."""


@dataclasses.dataclass
class JSONPeek:
    """What :func:`peek_json` could see of a JSON document."""

    #: The parsed prefix. Arrays and objects that were cut off are
    #: ``PartialList`` / ``PartialDict`` holding their complete leading items.
    value: Any
    #: True if the whole top-level value fits inside the window
    complete: bool
    #: True if non-whitespace follows a complete top-level value - so this is
    #: not a single JSON document (newline-delimited JSON, for example)
    trailing: bool


_WS_RE = re.compile(r"[ \t\n\r]*")
_STRING_RE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"', re.DOTALL)
_NUMBER_CHARS = set("0123456789+-.eE")
_MAX_DEPTH = 32
# Nested values are first parsed with the C decoder, but only from this many
# characters, so that a value cut off by the end of the window costs at most
# this much at each level of nesting rather than a scan of the whole window
_FAST_PARSE_CHARS = 16 * 1024
_decoder = json.JSONDecoder(strict=False)


class _Cut(Exception):
    def __init__(self, partial=None):
        self.partial = partial


class _Invalid(Exception):
    pass


def peek_json(first_bytes: bytes, max_items: int = 20) -> JSONPeek | None:
    """
    Parse as much as possible of a JSON array or object that may be cut off
    part way through. Returns ``None`` if this is not the start of a JSON
    array or object. Arrays and objects that were cut off keep at most
    ``max_items`` items (keys, for an object): looking stops at the first
    container with more.

    Use this in ``sniff()`` to look at structure rather than searching for
    strings: the start of a GeoJSON file is a dictionary with a ``features``
    key whether or not its ``"type": "FeatureCollection"`` key comes first.

    Every loader sniffing the same bytes gets the same result object, so
    treat it as read-only.
    """
    return _peek(bytes(first_bytes), max_items)[0]


# json, geojson, csv and tsv all peek at the same window: parse it once.
# Small, and cleared by sniff_path(), so big windows are not kept around.
@functools.lru_cache(maxsize=2)
def _peek(first_bytes: bytes, max_items: int = 20) -> tuple[JSONPeek | None, str, int]:
    """``(peek, text, end)``: ``end`` is where the first value ends in ``text``."""
    text = first_bytes.decode("utf-8-sig", errors="replace")
    start = _WS_RE.match(text).end()
    if start >= len(text) or text[start] not in "[{":
        return None, text, 0
    # A complete document is parsed in one go by the C decoder
    try:
        value, end = _decoder.raw_decode(text, start)
    except (json.JSONDecodeError, RecursionError):
        pass
    else:
        after = _WS_RE.match(text, end).end()
        return JSONPeek(value, complete=True, trailing=after < len(text)), text, end
    try:
        value, end = _peek_value(text, start, 0, max_items)
    except _Cut as cut:
        return JSONPeek(cut.partial, complete=False, trailing=False), text, len(text)
    except (_Invalid, RecursionError):
        return None, text, 0
    # Only reached if the C decoder rejected something _peek_value accepts
    return None, text, 0


def _peek_value(text, i, depth, max_items):
    i = _WS_RE.match(text, i).end()
    length = len(text)
    if i >= length:
        raise _Cut()
    char = text[i]
    if char == '"':
        match = _STRING_RE.match(text, i)
        if match is None:
            raise _Cut()
        try:
            return json.loads(match.group(0), strict=False), match.end()
        except ValueError:
            raise _Invalid()
    if char in "[{":
        if length - i > _FAST_PARSE_CHARS:
            try:
                value, end = _decoder.raw_decode(text[i : i + _FAST_PARSE_CHARS])
                return value, i + end
            except (json.JSONDecodeError, RecursionError):
                pass
        else:
            try:
                return _decoder.raw_decode(text, i)
            except (json.JSONDecodeError, RecursionError):
                pass
        if depth >= _MAX_DEPTH:
            raise _Cut()
        if char == "{":
            return _peek_object(text, i + 1, depth, max_items)
        return _peek_array(text, i + 1, depth, max_items)
    try:
        value, end = _decoder.raw_decode(text, i)
    except (json.JSONDecodeError, RecursionError):
        pass
    else:
        if end >= length and isinstance(value, (int, float)):
            # A number at the very end could have more digits
            raise _Cut()
        return value, end
    rest = text[i : i + 6]
    if any(literal.startswith(rest) for literal in ("true", "false", "null")):
        raise _Cut()
    if char in "-0123456789" and set(text[i:]) <= _NUMBER_CHARS:
        raise _Cut()
    raise _Invalid()


def _peek_object(text, i, depth, max_items):
    result = PartialDict()
    length = len(text)
    while True:
        i = _WS_RE.match(text, i).end()
        if i >= length:
            raise _Cut(result)
        if text[i] == "}" and not result:
            return {}, i + 1
        if len(result) >= max_items:
            raise _Cut(result)
        if text[i] != '"':
            raise _Invalid()
        match = _STRING_RE.match(text, i)
        if match is None:
            raise _Cut(result)
        try:
            key = json.loads(match.group(0), strict=False)
        except ValueError:
            raise _Invalid()
        i = _WS_RE.match(text, match.end()).end()
        if i >= length:
            raise _Cut(result)
        if text[i] != ":":
            raise _Invalid()
        try:
            value, i = _peek_value(text, i + 1, depth + 1, max_items)
        except _Cut as cut:
            if cut.partial is not None:
                result[key] = cut.partial
            raise _Cut(result)
        result[key] = value
        i = _WS_RE.match(text, i).end()
        if i >= length:
            raise _Cut(result)
        if text[i] == "}":
            return dict(result), i + 1
        if text[i] != ",":
            raise _Invalid()
        i += 1


def _peek_array(text, i, depth, max_items):
    result = PartialList()
    length = len(text)
    while True:
        i = _WS_RE.match(text, i).end()
        if i >= length:
            raise _Cut(result)
        if text[i] == "]" and not result:
            return [], i + 1
        if len(result) >= max_items:
            raise _Cut(result)
        try:
            value, i = _peek_value(text, i, depth + 1, max_items)
        except _Cut as cut:
            if cut.partial is not None:
                result.append(cut.partial)
            raise _Cut(result)
        result.append(value)
        i = _WS_RE.match(text, i).end()
        if i >= length:
            raise _Cut(result)
        if text[i] == "]":
            return list(result), i + 1
        if text[i] != ",":
            raise _Invalid()
        i += 1


_LINE_BREAK_RE = re.compile(r"[ \t]*\r?\n")


def _json_shaped(first_bytes: bytes) -> bool:
    """
    True if these bytes are JSON rather than delimited text: a single JSON
    array or object (complete or cut off), or a first line that is entirely
    one JSON value (newline-delimited or concatenated JSON).
    """
    peek, text, end = _peek(first_bytes)
    if peek is None:
        return False
    if not peek.trailing:
        return True
    return _LINE_BREAK_RE.match(text, end) is not None


# Core loaders


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _looks_like_text(text: str) -> bool:
    # A stray NUL or control character is fine; binary files have lots
    return len(_CONTROL_RE.findall(text)) <= len(text) / 100 + 1


def _is_one_column(text: str) -> bool:
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        records = [record for record in itertools.islice(reader, 100) if record]
    except csv.Error:
        return False
    # A header and at least one value
    return len(records) >= 2 and all(len(record) == 1 for record in records)


def _delimiter_score(text: str, delimiter: str, quoting: int) -> tuple[float, int]:
    """
    Score how well ``text`` reads as records separated by ``delimiter``,
    compared with the first record (the header, which needs two or more
    fields):

    - 1.0 if every record has the same number of fields as the header
    - 0.75 if the rows are ragged but plausible: at most 10% have more fields
      than the header (their extra values are kept), and at least half have
      exactly as many or 80% have two or more (spreadsheets often leave out
      trailing empty fields)
    - 0.5 if there is only a header (0.1 if that "header" is a single line
      over 4KB)
    - 0.0 otherwise

    Returns ``(score, number_of_fields)``.
    """
    reader = csv.reader(
        io.StringIO(text, newline=""), delimiter=delimiter, quoting=quoting
    )
    try:
        records = [record for record in itertools.islice(reader, 101) if record]
    except csv.Error:
        return 0.0, 0
    if not records or len(records[0]) < 2:
        return 0.0, 0
    width = len(records[0])
    if (
        len(records) <= 100
        and not text.endswith(("\n", "\r"))
        and len(records) > 1
        and len(records[-1]) < width
    ):
        # The last record was probably cut off by the end of the window
        records.pop()
    body = records[1:101]
    if not body:
        if len(text) > 4096 and "\n" not in text and "\r" not in text:
            # A single line longer than 4KB is unlikely to be a CSV header
            return 0.1, width
        return 0.5, width
    count = len(body)
    exact = sum(1 for record in body if len(record) == width)
    if exact == count:
        return 1.0, width
    longer = sum(1 for record in body if len(record) > width)
    several = sum(1 for record in body if len(record) >= 2)
    if longer <= count * 0.1 and (exact >= count * 0.5 or several >= count * 0.8):
        return 0.75, width
    return 0.0, width


class DelimitedLoader(Loader):
    """Shared logic for CSV and TSV: sniff the dialect, then read with csv."""

    #: Delimiter -> confidence when every record has the same number of fields
    delimiters: ClassVar[dict[str, float]] = {}
    #: Confidence for text with none of the delimiters (a one-column file)
    one_column_score = 0.0

    def sniff_dialect(self, first_bytes: bytes) -> tuple[type[csv.Dialect], float]:
        """``(dialect, confidence)`` for the start of a file."""
        # The sniff must not depend on whether a CSV was loaded earlier in
        # this process: load() raises the same process-wide limit
        maximize_csv_field_size_limit()
        default = self._dialect(next(iter(self.delimiters)), csv.QUOTE_MINIMAL)
        if _json_shaped(first_bytes):
            return default, 0.0
        text = first_bytes.decode(TEXT_ENCODING)
        if not text.strip() or not _looks_like_text(text):
            return default, 0.0
        best = (None, 0.0, 0)
        for delimiter, confidence in self.delimiters.items():
            quoting = csv.QUOTE_MINIMAL
            score, width = _delimiter_score(text, delimiter, quoting)
            if score < 1.0 and '"' in text:
                # A field that starts with a literal quote swallows the rest
                # of the file when quotes are special: try without quoting
                unquoted, unquoted_width = _delimiter_score(
                    text, delimiter, csv.QUOTE_NONE
                )
                if unquoted > score:
                    score, width, quoting = unquoted, unquoted_width, csv.QUOTE_NONE
            score *= confidence
            if score > 0 and (score, width) > best[1:]:
                best = (self._dialect(delimiter, quoting), score, width)
        if best[0] is None:
            if self.one_column_score and _is_one_column(text):
                return default, self.one_column_score
            return default, 0.0
        return best[0], best[1]

    def sniff(self, first_bytes: bytes) -> float:
        return self.sniff_dialect(first_bytes)[1]

    def load(self, path: str) -> Iterator["dict | Schema | Note"]:
        maximize_csv_field_size_limit()
        # Look at the same bytes sniff_path() did, so the dialect matches the
        # one that won
        with open(path, "rb") as fp:
            for first_bytes in sniff_windows(fp):
                dialect, score = self.sniff_dialect(first_bytes)
                if score >= DECISIVE_CONFIDENCE:
                    break
        _peek.cache_clear()
        with open(path, encoding=TEXT_ENCODING, newline="") as fp:
            reader = csv.reader(fp, dialect)
            header = next((record for record in reader if record), None)
            if header is None:
                return
            if dialect.quoting == csv.QUOTE_NONE:
                yield Note(
                    'Reading quotes (") as ordinary characters: a field starts '
                    "with a quote that would otherwise swallow the rest of the file"
                )
            columns = _unique_column_names(header)
            extras_key = _unique_column_names(columns + [EXTRAS_KEY])[-1]
            width = len(columns)
            empty = True
            # Rows with more fields than the header, and the first one's line
            longer = 0
            first_longer = None
            for record in reader:
                if not record:
                    continue
                empty = False
                row = dict(zip(columns, record))
                if len(record) < width:
                    for column in columns[len(record) :]:
                        row[column] = None
                elif len(record) > width:
                    row[extras_key] = record[width:]
                    if not longer:
                        first_longer = reader.line_num
                    longer += 1
                yield row
            if empty:
                # Only a header: keep the column names
                yield Schema({column: "text" for column in columns})
            if longer:
                yield Note(
                    f"{longer} row{' has' if longer == 1 else 's have'} more "
                    f"fields than the header (the first on line {first_longer}):"
                    f" the extra values are in the {extras_key!r} column"
                )

    @staticmethod
    def _dialect(delimiter: str, quoting: int) -> type[csv.Dialect]:
        return type(
            "dialect", (csv.excel,), {"delimiter": delimiter, "quoting": quoting}
        )


def _unique_column_names(names: list[str]) -> list[str]:
    """
    Rename blank and duplicate column names, so no values are lost: blank
    names become ``column_N`` (N is the position, from 1) and repeats get
    ``_2``, ``_3`` and so on. SQLite compares ASCII column names case
    insensitively, so this does too.
    """
    seen = set()
    result = []
    for index, name in enumerate(names):
        base = name if name.strip() else f"column_{index + 1}"
        candidate = base
        suffix = 2
        while _fold(candidate) in seen:
            candidate = f"{base}_{suffix}"
            suffix += 1
        seen.add(_fold(candidate))
        result.append(candidate)
    return result


def _fold(name: str) -> str:
    return "".join(c.lower() if c.isascii() else c for c in name)


class CSVLoader(DelimitedLoader):
    name = "csv"
    description = "Comma-separated values (also semicolon or pipe separated)"
    delimiters: ClassVar[dict[str, float]] = {",": 0.8, ";": 0.7, "|": 0.6}
    one_column_score = 0.2


class TSVLoader(DelimitedLoader):
    name = "tsv"
    description = "Tab-separated values"
    delimiters: ClassVar[dict[str, float]] = {"\t": 0.9}


def _records_inside(value) -> tuple[str, list] | None:
    """If exactly one value of this object is a list of objects, return it."""
    candidates = [
        (key, item)
        for key, item in value.items()
        if isinstance(item, list) and item and all(isinstance(i, dict) for i in item)
    ]
    return candidates[0] if len(candidates) == 1 else None


_MISSING = object()


def _json_values(text: str) -> Iterator[Any]:
    """Each JSON value in ``text``, for one or more values one after another."""
    decoder = json.JSONDecoder()
    i = _WS_RE.match(text).end()
    while i < len(text):
        try:
            value, i = decoder.raw_decode(text, i)
        except json.JSONDecodeError as ex:
            raise LoaderError(f"Invalid JSON: {ex}") from ex
        yield value
        i = _WS_RE.match(text, i).end()


class JSONLoader(Loader):
    name = "json"
    description = "JSON array of objects"

    def sniff(self, first_bytes: bytes) -> float:
        peek, text, end = _peek(first_bytes)
        if peek is None:
            return 0.0
        value = peek.value
        if peek.trailing:
            # Objects one after another (concatenated JSON, as printed by jq)
            # - newline-delimited JSON outscores this when every line parses
            after = _WS_RE.match(text, end).end()
            if (
                isinstance(value, dict)
                and _LINE_BREAK_RE.match(text, end)
                and text.startswith("{", after)
            ):
                return 0.4
            return 0.0
        if isinstance(value, list):
            if not value:
                return 0.5
            if isinstance(value[0], dict):
                return 0.9
            # JSON, but not rows: load() raises an error that says why, which
            # beats this being read as a CSV file with no rows
            return 0.15
        # An object is one row, or a wrapper around a list of rows
        if any(
            isinstance(item, list) and item and isinstance(item[0], dict)
            for item in value.values()
        ):
            return 0.6
        return 0.5

    def load(self, path: str) -> Iterator["dict | Note"]:
        # rows_from_file(format=Format.JSON) wraps a top-level object in a
        # list, which hides whether it was {"rows": [...]} or [{"rows": [...]}]
        with open(path, encoding=TEXT_ENCODING, newline="") as fp:
            values = _json_values(fp.read())
        first = next(values, _MISSING)
        second = next(values, _MISSING)
        if second is _MISSING:
            yield from self._rows(None if first is _MISSING else first)
            return
        # Several values: each one is a row
        for index, value in enumerate(itertools.chain([first, second], values)):
            if not isinstance(value, dict):
                raise LoaderError(
                    f"JSON value {index + 1} is {type(value).__name__}, "
                    "expected an object"
                )
            yield value

    def _rows(self, data):
        if isinstance(data, dict):
            inside = _records_inside(data)
            if inside is None:
                data = [data]
            else:
                key, records = inside
                others = [other for other in data if other != key]
                if others:
                    # An API response: {"total": 2, "next": null, "results": [...]}
                    count = len(records)
                    yield Note(
                        f"Importing the {count} object{'' if count == 1 else 's'}"
                        f" in {key!r}, ignoring "
                        + ", ".join(repr(other) for other in others)
                    )
                data = records
        if not isinstance(data, list):
            raise LoaderError("JSON must be an array of objects or an object")
        for index, row in enumerate(data):
            if not isinstance(row, dict):
                raise LoaderError(
                    f"JSON array item {index} is {type(row).__name__}, expected an object"
                )
            yield row


class NDJSONLoader(Loader):
    name = "ndjson"
    description = "Newline-delimited JSON, one object per line"

    def sniff(self, first_bytes: bytes) -> float:
        text = first_bytes.decode("utf-8-sig", errors="replace")
        pieces = text.split("\n")
        # The last piece is empty if the window ends with a newline, else it
        # may have been cut off by the end of the window
        last = pieces.pop()
        lines = [line for line in pieces if line.strip()][:50]
        if last.strip() and len(lines) < 50:
            lines.append(last)
        else:
            last = ""
        if not lines or not lines[0].lstrip().startswith(("{", "[")):
            return 0.0
        objects = arrays = bad = 0
        cut_off = False
        for index, line in enumerate(lines):
            if last and index == len(lines) - 1 and len(line) > SNIFF_BYTES:
                # Parsing a long last line only tells a whole one-line file
                # (the JSON loader's job) from one cut off by the window
                cut_off = True
                continue
            try:
                value = json.loads(line)
            except ValueError:
                if last and index == len(lines) - 1:
                    cut_off = True
                else:
                    bad += 1
                continue
            if isinstance(value, dict):
                objects += 1
            elif isinstance(value, list):
                arrays += 1
            else:
                bad += 1
        total = objects + arrays + bad
        if total == 0:
            # The first line is longer than the window: can't tell yet
            return 0.25 if lines[0].lstrip().startswith("{") else 0.0
        if objects == 0:
            # JSON arrays, one per line: recognizable, but load() needs objects
            # and raises an error that says so
            return 0.15 if arrays >= 2 and arrays == total else 0.0
        fraction = objects / total
        if objects >= 2:
            if fraction == 1:
                return 0.95
            # Some lines are broken: still newline-delimited JSON, and load()
            # raises an error naming the first bad line
            return 0.9 * fraction if fraction >= 0.5 else 0.0
        if total == 1:
            if cut_off and lines[1].lstrip().startswith("{"):
                # One object, then the start of another
                return 0.9
            # A single object on one line: the JSON loader reads that too
            return 0.3
        return 0.45 if fraction >= 0.5 else 0.0

    def load(self, path: str) -> Iterator[dict]:
        with open(path, encoding=TEXT_ENCODING, newline="\n") as fp:
            for number, line in enumerate(fp, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as ex:
                    raise LoaderError(
                        f"Line {number} is not valid JSON: {_describe_json_error(line)}"
                    ) from ex
                if not isinstance(row, dict):
                    raise LoaderError(
                        f"Line {number} is {type(row).__name__}, expected an object"
                    )
                yield row


def _describe_json_error(line: str) -> str:
    # Without the newline, a cut-off line reports an unterminated string
    # rather than an invalid control character
    try:
        json.loads(line.rstrip("\r\n"))
    except json.JSONDecodeError as ex:
        return f"{ex.msg.removesuffix(' at')} at column {ex.colno}"
    return "invalid JSON"


_CORE_LOADER_CLASSES = (CSVLoader, TSVLoader, JSONLoader, NDJSONLoader)


@hookimpl
def register_import_loaders():
    return [loader_class() for loader_class in _CORE_LOADER_CLASSES]
