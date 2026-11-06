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
import io
import itertools
import json
import logging
import math
import re
from collections.abc import Iterable, Iterator
from typing import Any, ClassVar

from sqlite_utils.utils import Format, maximize_csv_field_size_limit, rows_from_file

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

    def load(self, path: str) -> Iterable["dict | Schema"]:
        """
        Yield an optional :class:`Schema`, then one dictionary per row.
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


def get_loaders() -> list[Loader]:
    """
    Every loader registered with the ``register_import_loaders()`` hook.

    A plugin loader with the same name as a core loader replaces it. If two
    plugin loaders share a name, the one whose class has the lowest
    ``module.qualname`` wins and a warning is logged.
    """
    from datasette.plugins import pm

    by_name: dict[str, Loader] = {}
    for item in itertools.chain.from_iterable(
        result for result in pm.hook.register_import_loaders() if result
    ):
        loader = item() if isinstance(item, type) else item
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
    return _choose(score_loaders(first_bytes, loaders), first_bytes, min_confidence)


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
    size = sniff_bytes
    with open(path, "rb") as fp:
        first_bytes = fp.read(size)
        while True:
            scores = score_loaders(first_bytes, loaders)
            decisive = scores and scores[0].score >= DECISIVE_CONFIDENCE
            if decisive or len(first_bytes) < size or size >= max_sniff_bytes:
                return first_bytes, scores
            size = min(size * 4, max_sniff_bytes)
            first_bytes += fp.read(size - len(first_bytes))


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
        loader = _choose(scores, first_bytes, MIN_CONFIDENCE)
    schema, rows = split_schema(loader.load(path), loader.name)
    return LoadResult(loader=loader, schema=schema, rows=rows, scores=scores)


def split_schema(
    items: Iterable["dict | Schema"], loader_name: str = "loader"
) -> tuple[Schema | None, Iterator[dict]]:
    """
    Split what ``Loader.load()`` yields into ``(schema_or_None, rows)``.
    Raises :class:`LoaderError` while iterating if a loader yields a schema
    after the first item, or anything other than a dictionary.
    """
    iterator = iter(items)
    schema = None
    try:
        first = next(iterator)
    except StopIteration:
        return None, iter(())
    if isinstance(first, Schema):
        schema = first
    else:
        iterator = itertools.chain([first], iterator)

    def rows():
        for row in iterator:
            if not isinstance(row, dict):
                if isinstance(row, Schema):
                    raise LoaderError(
                        f"{loader_name} yielded a Schema after the first item"
                    )
                raise LoaderError(
                    f"{loader_name} yielded {type(row).__name__}, expected a dict"
                )
            yield row

    return schema, rows()


def _choose(scores, first_bytes, min_confidence):
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
# encoding for the whole file, decode as UTF-8 and decode each invalid byte as
# cp1252, or as latin-1 for the five bytes cp1252 leaves undefined. That works
# no matter how far into the file the first non-UTF-8 byte appears, and costs
# nothing for files that are valid UTF-8. A leading byte order mark is removed.

_FALLBACK_ERRORS = "datasette-cp1252-fallback"
#: Pass this as an ``encoding=`` to decode text with the fallback described above
TEXT_ENCODING = "datasette-utf8"


def _byte_to_text(byte: int) -> str:
    try:
        return bytes([byte]).decode("cp1252")
    except UnicodeDecodeError:
        return chr(byte)


_FALLBACK_TABLE = [_byte_to_text(byte) for byte in range(256)]


def _cp1252_fallback(error):
    if not isinstance(error, UnicodeDecodeError):
        raise error
    invalid = error.object[error.start : error.end]
    return "".join(_FALLBACK_TABLE[byte] for byte in invalid), error.end


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
        encode=_UTF8_SIG.encode,
        decode=_fallback_decode,
        incrementalencoder=_UTF8_SIG.incrementalencoder,
        incrementaldecoder=_FallbackIncrementalDecoder,
        streamreader=_UTF8_SIG.streamreader,
        streamwriter=_UTF8_SIG.streamwriter,
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
_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_NUMBER_CHARS = set("0123456789+-.eE")
_MAX_DEPTH = 32
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
    array or object. Arrays that were cut off keep at most ``max_items`` items.

    Use this in ``sniff()`` to look at structure rather than searching for
    strings: the start of a GeoJSON file is a dictionary with a ``features``
    key whether or not its ``"type": "FeatureCollection"`` key comes first.
    """
    text = first_bytes.decode("utf-8-sig", errors="replace")
    start = _WS_RE.match(text).end()
    if start >= len(text) or text[start] not in "[{":
        return None
    try:
        value, end = _peek_value(text, start, 0, max_items)
    except _Cut as cut:
        return JSONPeek(cut.partial, complete=False, trailing=False)
    except (_Invalid, RecursionError):
        return None
    end = _WS_RE.match(text, end).end()
    return JSONPeek(value, complete=True, trailing=end < len(text))


def _peek_value(text, i, depth, max_items):
    i = _WS_RE.match(text, i).end()
    if i >= len(text):
        raise _Cut()
    char = text[i]
    try:
        value, end = _decoder.raw_decode(text, i)
    except (json.JSONDecodeError, RecursionError):
        pass
    else:
        if end >= len(text) and isinstance(value, (int, float)):
            # A number at the very end could have more digits
            raise _Cut()
        return value, end
    if char in "[{":
        if depth >= _MAX_DEPTH:
            raise _Cut()
        if char == "{":
            return _peek_object(text, i + 1, depth, max_items)
        return _peek_array(text, i + 1, depth, max_items)
    rest = text[i:]
    if char == '"' and not _STRING_RE.match(text, i):
        raise _Cut()
    if any(literal.startswith(rest) for literal in ("true", "false", "null")):
        raise _Cut()
    if char in "-0123456789" and set(rest) <= _NUMBER_CHARS:
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


# Core loaders


_CONTROL_RE = re.compile(r"[\x01-\x08\x0b\x0c\x0e-\x1f]")


def _looks_like_text(text: str) -> bool:
    if "\x00" in text:
        return False
    return len(_CONTROL_RE.findall(text)) <= len(text) / 100


def _is_one_column(text: str) -> bool:
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        return all(len(record) <= 1 for record in itertools.islice(reader, 100))
    except csv.Error:
        return False


def _delimiter_score(text: str, delimiter: str) -> tuple[float, int]:
    """
    Score how well ``text`` reads as records separated by ``delimiter``:
    1.0 if every record has the same number of fields (at least two) as the
    first, 0.75 if at least 90% do, 0.5 if there is only a header (0.1 if
    that "header" is a single line over 4KB), else 0.
    Returns ``(score, number_of_fields)``.
    """
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
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
    consistent = sum(1 for record in body if len(record) == width) / len(body)
    if consistent == 1:
        return 1.0, width
    if consistent >= 0.9:
        return 0.75, width
    return 0.0, width


class DelimitedLoader(Loader):
    """Shared logic for CSV and TSV: sniff the delimiter, then use sqlite-utils."""

    #: Delimiter -> confidence when every record has the same number of fields
    delimiters: ClassVar[dict[str, float]] = {}
    #: Confidence for text with none of the delimiters (a one-column file)
    one_column_score = 0.0
    format = Format.CSV

    def best_delimiter(self, first_bytes: bytes) -> tuple[str | None, float]:
        text = first_bytes.decode(TEXT_ENCODING)
        if not text.strip() or not _looks_like_text(text):
            return None, 0.0
        best = (None, 0.0, 0)
        for delimiter, confidence in self.delimiters.items():
            score, width = _delimiter_score(text, delimiter)
            score *= confidence
            if score > 0 and (score, width) > best[1:]:
                best = (delimiter, score, width)
        if best[0] is None:
            if self.one_column_score and _is_one_column(text):
                return None, self.one_column_score
            return None, 0.0
        return best[0], best[1]

    def sniff(self, first_bytes: bytes) -> float:
        return self.best_delimiter(first_bytes)[1]

    def load(self, path: str) -> Iterator[dict]:
        maximize_csv_field_size_limit()
        with open(path, "rb") as fp:
            delimiter, _ = self.best_delimiter(fp.read(SNIFF_BYTES))
        delimiter = delimiter or next(iter(self.delimiters))
        with open(path, "rb") as fp:
            if self.format == Format.TSV:
                rows, _ = rows_from_file(fp, format=Format.TSV, encoding=TEXT_ENCODING)
            else:
                dialect = type("dialect", (csv.excel,), {"delimiter": delimiter})
                rows, _ = rows_from_file(
                    fp, format=Format.CSV, dialect=dialect, encoding=TEXT_ENCODING
                )
            yield from rows


class CSVLoader(DelimitedLoader):
    name = "csv"
    description = "Comma-separated values (also semicolon or pipe separated)"
    delimiters: ClassVar[dict[str, float]] = {",": 0.8, ";": 0.7, "|": 0.6}
    one_column_score = 0.2


class TSVLoader(DelimitedLoader):
    name = "tsv"
    description = "Tab-separated values"
    delimiters: ClassVar[dict[str, float]] = {"\t": 0.9}
    format = Format.TSV


def _records_inside(value) -> list | None:
    """If exactly one value of this object is a list of objects, return it."""
    candidates = [
        item
        for item in value.values()
        if isinstance(item, list) and item and all(isinstance(i, dict) for i in item)
    ]
    return candidates[0] if len(candidates) == 1 else None


class JSONLoader(Loader):
    name = "json"
    description = "JSON array of objects"

    def sniff(self, first_bytes: bytes) -> float:
        peek = peek_json(first_bytes)
        if peek is None or peek.trailing:
            return 0.0
        value = peek.value
        if isinstance(value, list):
            if not value:
                return 0.5
            return 0.9 if isinstance(value[0], dict) else 0.0
        # An object is one row, or a wrapper around a list of rows
        if any(
            isinstance(item, list) and item and isinstance(item[0], dict)
            for item in value.values()
        ):
            return 0.6
        return 0.5

    def load(self, path: str) -> Iterator[dict]:
        # rows_from_file(format=Format.JSON) wraps a top-level object in a
        # list, which hides whether it was {"rows": [...]} or [{"rows": [...]}]
        with open(path, "rb") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            data = _records_inside(data) or [data]
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
        lines = [line for line in text.split("\n") if line.strip()][:50]
        if not lines or not lines[0].lstrip().startswith("{"):
            return 0.0
        parsed = 0
        cut_off = False
        for index, line in enumerate(lines):
            try:
                value = json.loads(line)
            except ValueError:
                if index == len(lines) - 1 and not text.endswith("\n"):
                    # Cut off by the end of the window
                    cut_off = True
                    break
                return 0.0
            if not isinstance(value, dict):
                return 0.0
            parsed += 1
        if parsed >= 2:
            return 0.95
        if parsed == 1:
            if cut_off and lines[1].lstrip().startswith("{"):
                # One object, then the start of another
                return 0.9
            # A single object on one line: the JSON loader reads that too
            return 0.3
        # The first line is longer than the window: can't tell yet
        return 0.25

    def load(self, path: str) -> Iterator[dict]:
        with open(path, "rb") as fp:
            rows, _ = rows_from_file(fp, format=Format.NL)
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise LoaderError(
                        f"Row {index + 1} is {type(row).__name__}, expected an object"
                    )
                yield row


_CORE_LOADER_CLASSES = (CSVLoader, TSVLoader, JSONLoader, NDJSONLoader)


@hookimpl
def register_import_loaders():
    return [loader_class() for loader_class in _CORE_LOADER_CLASSES]
