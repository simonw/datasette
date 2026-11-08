"""
Turn a stream of rows (dictionaries, or lists of values in the order of a
list of keys) into a typed SQLite table.

The algorithm:

1. The first chunk of rows (the *sample*, 1,000 rows by default) is used to
   detect a type for every column: ``integer``, ``float`` or ``text`` (or
   ``blob`` for ``bytes`` values). A rowid table with no primary key is
   created with those types and the sample is inserted.
2. Every later chunk (a *batch*, 100 rows by default) is checked against the
   current column types before it is inserted. If a value does not fit, the
   column is *widened* first: ``integer -> float -> text -> blob``. Columns
   never narrow. New keys add new columns.
3. Each chunk is meant to be written in its own transaction so readers see
   progress - :func:`import_rows` sends one ``db.execute_write_fn()`` call
   per chunk, :func:`insert_rows` commits after every chunk - and is
   inserted with as few multi-row ``INSERT`` statements as SQLite's limit
   on bound parameters allows (see :meth:`TypedInserter._insert`).

Widening changes the declared type in place, which takes well under a
millisecond at any table size (if the schema cannot be edited, the table
is copied instead). Widening to text also means the numbers
already stored have to become text: that is done a slice of rows at a time,
by each following write (about 30ms of it each) and then by
:meth:`TypedInserter.convert` calls of their own, so no single transaction
holds the write lock for long however big the table is. Until it is done,
readers can see numbers in some rows of that column, which is declared
``BLOB`` meanwhile (see :class:`TypedInserter`).

:class:`TypedInserter` is the synchronous core. It works on a plain
``sqlite3.Connection`` supplied by the caller and never commits itself, so
it can run inside ``Database.execute_write_fn()`` or from a script.

Value rules (see :func:`classify`):

- ``None`` and ``""`` carry no type information. ``""`` is stored as NULL in
  integer and float columns and kept as ``""`` in text columns, matching
  what ``sqlite-utils insert --csv`` (which detects types) produces.
- An integer string must be canonical: ``"007"`` is text, so zero-padded
  codes keep their zeros. Surrounding whitespace and a leading ``+`` are
  accepted (SQLite's affinity accepts them too).
- A float string is anything SQLite's REAL affinity converts, except for
  superfluous leading zeros and values a float cannot hold: ``"1.50"``,
  ``"1e3"`` and ``".5"`` are floats, ``"1e400"`` (infinity) and
  ``"1e-400"`` (zero) are text.
- ``nan``, ``inf``, ``1_000`` and non-ASCII digits are text, because SQLite
  stores them as text. A Python ``float('nan')`` is treated like ``None``:
  SQLite cannot store NaN.
- A Python ``int`` outside the signed 64-bit range is stored as text.
- ``list``, ``dict`` and ``tuple`` values are stored as JSON text.
- A column with no values at all stays ``integer`` (all NULL), as
  ``sqlite-utils`` does: the narrowest type, so later rows can still widen
  it. A table imported from zero rows gets ``text`` columns.
- Lone surrogates (which ``json.loads()`` accepts) cannot be stored as
  UTF-8 and are replaced with U+FFFD.

Nothing is lost when a column widens to text after the sample: the result
is the same as if the whole input had been the sample. Widening happens
before the batch that needs it is inserted, and a number turns back into
the text it came from. That works without remembering anything for nearly
every value, because the stored number reproduces it (``"6"``, ``"5005.5"``).
For the rest - ``"1.50"``, ``"3.0"``, ``"1e3"``, ``" 12"``, ``"+5"``, ``""``
next to real NULLs - the original text is kept in a ``TEMP`` table on the
writing connection until the import finishes. A float with no original text
(from JSON) becomes the shortest text that reads back as it, with no
``.0`` for whole numbers.
"""

import asyncio
import contextlib
import dataclasses
import inspect
import itertools
import json
import logging
import math
import re
import sqlite3
import time
import uuid
from collections.abc import AsyncIterable, Iterable, Sequence

from datasette.utils.sqlite import sqlite3 as _datasette_sqlite3

logger = logging.getLogger(__name__)
#: The exception classes for errors from SQLite. Datasette's connections come
#: from pysqlite3 when that is installed (see datasette.utils.sqlite), whose
#: exceptions are not subclasses of the sqlite3 module's, and callers of
#: insert_rows() can pass a connection from either module
SQLITE_ERRORS = tuple({sqlite3.Error, _datasette_sqlite3.Error})
_SQLITE_DATABASE_ERRORS = tuple(
    {sqlite3.DatabaseError, _datasette_sqlite3.DatabaseError}
)
# Clean ups that import_rows() left running after it raised, kept here so
# they are not garbage collected before they finish
_BACKGROUND_TASKS = set()
# How long the conversion that a cancelled import_rows() leaves running in
# the background waits for the cancelled task to finish first (see there)
_HANDOFF_SECONDS = 1.0

INTEGER = "integer"
FLOAT = "float"
TEXT = "text"
BLOB = "blob"

# Types are handled as ranks; widening only ever increases the rank
_NONE, _I, _F, _T, _B = -1, 0, 1, 2, 3
_KINDS = (INTEGER, FLOAT, TEXT, BLOB)
_RANK = {INTEGER: _I, FLOAT: _F, TEXT: _T, BLOB: _B}
_SQL_TYPES = ("INTEGER", "REAL", "TEXT", "BLOB")
_TYPE_ALIASES = {
    "integer": INTEGER,
    "int": INTEGER,
    "float": FLOAT,
    "real": FLOAT,
    "text": TEXT,
    "str": TEXT,
    "blob": BLOB,
    "bytes": BLOB,
    int: INTEGER,
    float: FLOAT,
    str: TEXT,
    bytes: BLOB,
}
_NoneType = type(None)

# How long a write() (or convert()) spends converting stored numbers to text
# after a column widened to text - its UPDATE, not counting the COMMIT, which
# measured another 50-150% for these slices on a WAL file (writing the dirty
# pages and the automatic checkpoint) - and the slice size in rows before
# any timing is known. A slice is sized as if every row in it had to be
# rewritten, see TypedInserter._slice_rows(). With 0.05 the transactions
# that converted took 81-85ms (p50) and up to 123-186ms, COMMIT included,
# on big-1gb.csv; with 0.03, 50ms and up to 77-88ms, and the import was no
# slower (1M and 6M rows)
CONVERT_SECONDS = 0.03
_FIRST_CONVERT_ROWS = 5_000
_MIN_CONVERT_ROWS = 500
# A safety net in case a timing is wildly off: 200,000 rows are 160-270ms
# of rewriting at big-1gb.csv's width
_MAX_CONVERT_ROWS = 200_000
# A slice whose rows mostly needed rewriting (at least this fraction of its
# rowid range) measures the cost of rewriting a row
_DENSE_FRACTION = 0.25

# SQLite skips these around numbers when applying affinity (sqlite3Isspace)
_WS_CHARS = " \t\n\v\f\r"
_WS = "[ \\t\\n\\v\\f\\r]*"
# Canonical values: an INTEGER (or REAL) column stores them and gives them
# back as exactly the same text, so nothing has to be remembered. They are
# the shapes nearly every value takes, which makes them the fast path (see
# _canonical()). Integers are limited to 15 digits so that they stay exact
# if the column becomes REAL. Decimals must have no superfluous zeros, at
# most 15 significant digits - guaranteed by _REAL_LENGTH, cheaper than a
# lookahead - and be in the range where repr() does not use an exponent.
_INT_FAST = re.compile(r"-?[1-9][0-9]{0,14}|0")
_REAL_FAST = re.compile(
    r"-?[1-9][0-9]{0,14}(?:\.[0-9]*[1-9])?|-?0\.0{0,3}[1-9](?:[0-9]*[1-9])?|0"
)
_REAL_LENGTH = 16
# The classification rules, see classify()
_INT_RE = re.compile(_WS + r"[-+]?(?:0|[1-9][0-9]*)" + _WS)
_FLOAT_RE = re.compile(
    _WS + r"[-+]?(?:(?:0|[1-9][0-9]*)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?" + _WS
)
_EXPONENT = re.compile("[eE]")
_NONZERO = re.compile("[1-9]")
_SURROGATES = re.compile("[\ud800-\udfff]")
_INT_MIN = -(2**63)
_INT_MAX = 2**63 - 1
# Integers beyond this cannot be held exactly by a REAL
_EXACT = 2**53
# SQL function used to turn REAL values into text, see _render_real()
_RENDER_FUNCTION = "_datasette_import_real_text"


def classify(value):
    """
    Return the narrowest column type that holds ``value`` without changing
    it: ``"integer"``, ``"float"``, ``"text"`` or ``"blob"`` - or ``None``
    for ``None``, ``""`` and NaN, which carry no type information.
    """
    rank = _classify_rank(value)
    return None if rank == _NONE else _KINDS[rank]


def _classify_rank(value):
    kind = type(value)
    if kind is str:
        if not value:
            return _NONE
        if _INT_FAST.fullmatch(value):
            return _I
        if _INT_RE.fullmatch(value):
            # More than 19 digits cannot fit, and int() refuses over 4300
            if len(value.strip(_WS_CHARS).lstrip("+-")) > 19:
                return _T
            return _I if _INT_MIN <= int(value) <= _INT_MAX else _T
        if _FLOAT_RE.fullmatch(value):
            return _F if _float_holds(value) else _T
        return _T
    if value is None:
        return _NONE
    if kind is int or kind is bool:
        return _I if _INT_MIN <= value <= _INT_MAX else _T
    if kind is float:
        return _NONE if math.isnan(value) else _F
    if kind in (bytes, bytearray, memoryview):
        return _B
    if isinstance(value, int):
        return _I if _INT_MIN <= value <= _INT_MAX else _T
    if isinstance(value, float):
        return _NONE if math.isnan(value) else _F
    return _T


def _float_holds(text):
    "False if a float string overflows to infinity or underflows to zero."
    number = float(text)
    if math.isinf(number):
        return False
    if number == 0:
        mantissa = _EXPONENT.split(text, maxsplit=1)[0]
        return not _NONZERO.search(mantissa)
    return True


def _render_real(number):
    """
    The text a REAL becomes when its column widens to text: the integer for
    a whole number (``"6"`` - most likely an integer in the source, in a
    column that went integer -> float -> text), otherwise the shortest text
    that reads back as the same float (``"5005.5"``).
    """
    if number.is_integer() and -(2.0**63) <= number < 2.0**63:
        return str(int(number))
    return repr(number)


def _render_sql(value):
    return _render_real(value) if isinstance(value, float) else value


def _text_form(value):
    "What a TEXT column holds for ``value`` - and what it becomes on widening."
    kind = type(value)
    if kind is str or value is None:
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else _render_real(float(value))
    if isinstance(value, int):
        return str(int(value))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


_BYTES_TYPES = {bytes, bytearray, memoryview}
_NUMBER_TYPES = {int, float, bool, _NoneType}
_TEXT_TYPES = {str, bytes, _NoneType}
_NUMBERS_AND_STR = _NUMBER_TYPES | {str}
_NULLISH_TYPES = {str, _NoneType}
_STR = {str}
_SEQUENCE_TYPES = {list, tuple}
# _null_kind() result meaning "no spelling needs to be kept"
_NO_ENTRY = object()


def _canonical(value, rank):
    "Whether a non-empty string is canonical in an INTEGER or REAL column."
    if rank <= _I:
        return _INT_FAST.fullmatch(value) is not None
    return len(value) <= _REAL_LENGTH and _REAL_FAST.fullmatch(value) is not None


def _odd_positions(values, rank):
    """
    Positions of the non-empty strings that are not canonical for an INTEGER
    (``rank`` up to _I) or REAL column, or None if there are none - found at
    C speed in that case.
    """
    if rank <= _I:
        pattern, too_long = _INT_FAST, False
    else:
        pattern, too_long = _REAL_FAST, max(map(len, values)) > _REAL_LENGTH
    if not too_long and all(map(pattern.fullmatch, filter(None, values))):
        return None
    matched = list(map(bool, map(pattern.fullmatch, values)))
    odd = [i for i in _positions(matched, False) if values[i]]
    if too_long:
        odd = sorted(
            set(odd).union(i for i, v in enumerate(values) if len(v) > _REAL_LENGTH)
        )
    return odd


def _positions(items, target):
    "Indexes of ``target`` in ``items`` - a C-speed search per match."
    positions = []
    position = -1
    try:
        while True:
            position = items.index(target, position + 1)
            positions.append(position)
    except ValueError:
        return positions


# Whether SQLite turns float text into exactly the double float() gives,
# checked once per process by _sqlite_parses_floats_exactly()
_FLOATS_EXACT = None
# Shortest float reprs that SQLite 3.45 (x86-64, 80-bit long double) parses
# one unit in the last place off: about 1 in 12,000 random reprs is, too
# few for a random sample to be sure of finding one
_HARD_FLOATS = (
    "-9.55486443262481",
    "-307621.8384230058",
    "6146.289328387767",
    "41600.85519560998",
    "-37.39916031412913",
    "21600725.73404794",
    "-0.04574192847840702",
    "-7791788.365715886",
    "6946773.62119926",
    "7956.419716376417",
)


def _sqlite_parses_floats_exactly(conn):
    """
    A canonical float string is bound as text and keeps no spelling, which
    relies on SQLite parsing it into the same double as ``float()``: then
    the stored value turns back into exactly that text. SQLite 3.53 does
    for every value tested; builds whose text-to-float conversion is less
    precise exist (3.45 gets about 1 in 12,000 shortest reprs wrong), so
    check known hard cases and a sample once, and otherwise convert in
    Python.
    """
    global _FLOATS_EXACT
    if _FLOATS_EXACT is None:
        import random

        rng = random.Random(0)
        samples = ["0.1", "0.3", "2.675", "1.005", "9007199254740993"]
        samples.extend(_HARD_FLOATS)
        for _ in range(500):
            digits = rng.choice((15, 17))
            mantissa = rng.randrange(10 ** (digits - 1), 10**digits)
            samples.append(f"{mantissa}e{rng.randint(-25, 5)}")
        for _ in range(500):
            samples.append(f"{rng.randrange(10**12)}.{rng.randrange(1000):03d}")
        exact = True
        for start in range(0, len(samples), 250):
            chunk = samples[start : start + 250]
            parsed = conn.execute(
                "select " + ", ".join("cast(? as real)" for _ in chunk), chunk
            ).fetchone()
            exact = exact and all(a == float(b) for a, b in zip(parsed, chunk))
        _FLOATS_EXACT = exact
    return _FLOATS_EXACT


def _variable_limit(conn):
    """
    The most parameters one statement can bind on ``conn``.

    pysqlite3's connections have no ``getlimit()``. Without it the limit is
    the one SQLite was compiled with, which only SQLite's C API can lower:
    ``PRAGMA compile_options`` lists it if the build set it, and otherwise
    it is the default, 32766 since SQLite 3.32.0 and 999 before.
    """
    getlimit = getattr(conn, "getlimit", None)
    if getlimit is not None:
        return getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    try:
        for (option,) in conn.execute("PRAGMA compile_options"):
            if option.startswith("MAX_VARIABLE_NUMBER="):
                return int(option.partition("=")[2])
    except (*SQLITE_ERRORS, ValueError):
        pass
    version = conn.execute("select sqlite_version()").fetchone()[0]
    return 32766 if tuple(map(int, version.split(".")[:3])) >= (3, 32, 0) else 999


def _widest(values, rank):
    "The widest rank among ``values``, starting from ``rank``."
    for value in values:
        value_rank = _classify_rank(value)
        if value_rank > rank:
            rank = value_rank
            if rank == _B:
                break
    return rank


def _storable(value):
    "Convert a value that sqlite3 cannot bind (or would bind badly)."
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return int(value) if _INT_MIN <= value <= _INT_MAX else str(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    return str(value)


def _without_surrogates(value):
    if type(value) is str and not value.isascii():
        return _SURROGATES.sub("�", value)
    return value


def _quote(identifier):
    return '"{}"'.format(identifier.replace('"', '""'))


def normalize_type(type_):
    """
    Map ``int``, ``"INTEGER"``, ``"real"``, ``str``... to one of
    ``"integer"``, ``"float"``, ``"text"`` or ``"blob"``.
    """
    key = type_.lower() if isinstance(type_, str) else type_
    try:
        return _TYPE_ALIASES[key]
    except (KeyError, TypeError):
        raise ValueError(f"Unknown column type: {type_!r}") from None


@dataclasses.dataclass
class Widening:
    "A record of one column being widened during an import."

    column: str
    from_type: str
    to_type: str
    # Rows already written when the widening happened
    at_row: int
    # How the table was changed: "schema" (the declared type, edited in
    # place), "incremental" (that, with the stored numbers converted to
    # text a slice at a time afterwards) or "rebuild" (the table copied,
    # because it could not be edited in place)
    method: str
    # Time spent changing the table (shared by columns widened together).
    # For "incremental" that is only the declared type: converting the
    # stored values comes later, see TypedInserter.convert_seconds
    seconds: float = 0.0


@dataclasses.dataclass
class _Column:
    key: object
    name: str
    # Position in the table, also the column's id in the spellings table
    index: int
    # Logical type rank, _NONE until a value has been seen
    rank: int = _NONE
    # Rank declared in the table: the same as rank (at least _I) once the
    # column exists in the table, _NONE before
    declared: int = _NONE
    # The type came from an explicit schema - never widened
    fixed: bool = False
    # What the NULLs stored in this column while it is numeric stand for:
    # None until the first one, then "blank" ("" in the source) or "null"
    # (None or a missing key). NULLs of the other kind get a spelling.
    nulls: object = None
    # An integer beyond +-2**53 is stored, which a REAL would round
    big_ints: bool = False
    # Number of rows of this column in the spellings table
    spellings: int = 0

    @property
    def type(self):
        return _KINDS[max(self.rank, _I)]


class TypedInserter:
    """
    Incrementally create and fill one typed rowid table.

    Call :meth:`write` with chunks of rows: the first chunk is the sample
    used to detect column types and create the table. Call :meth:`finish`
    once at the end (or :meth:`discard` if the import is abandoned). Neither
    method commits - run each call in its own transaction
    (``db.execute_write_fn()`` does that for you). If a call raises, the
    inserter is left as it was before the call, matching the rolled back
    transaction, so the same chunk can be written again.

    Every call must use the same connection: until :meth:`finish`, the
    original text of values whose stored number would not reproduce it is
    kept in a ``TEMP`` table, which only that connection can see.

    :param table: name of the table to create - it must not exist yet
    :param schema: optional ``{key: type}`` from a loader that knows its
        types (``"integer"``, ``"float"``, ``"text"``, ``"blob"`` or the
        Python types). A ``None`` type means "detect this column"; that is
        how a loader declares column names and order, which matters for a
        zero-row file. Typed columns are never widened.

    Widening changes the declared type in ``sqlite_schema`` in place, which
    takes well under a millisecond at any table size. For integer -> float
    (and text -> blob) that is all: no stored value has to change. Widening
    to text also has to turn the numbers already stored into text, which is
    O(rows): the :meth:`write` that widens converts the first slice of them
    and each later one another slice, about ``CONVERT_SECONDS`` (30ms) of
    work at a time whatever the row width. :attr:`backlog` says how many
    rows are left; :meth:`convert` converts a slice on its own
    (:func:`insert_rows` and :func:`import_rows` call it, one transaction
    each, until none are left) and :meth:`finish` converts whatever
    remains. So the write lock is never held for long - but until the
    backlog is gone, readers see numbers (``5``, ``1.5``) in rows not yet
    converted, where the finished table has text (``'5'``, ``'1.50'``), and
    the column is declared ``BLOB`` (no affinity: SQLite's integrity_check
    reports a number in a TEXT column as an error), then ``TEXT``. A query
    can notice: ``typeof()``; ``=``, ``<`` or ``IN`` against text, which
    never matches a number (``?col=5`` in Datasette, and facets, which count
    ``5`` and ``'5'`` apart); ``ORDER BY``, which sorts numbers before all
    text; the JSON type of the value; the declared type. The table is
    complete, and passes integrity_check, in every committed transaction.
    If the process dies (SIGKILL, power loss) before the backlog is done,
    the column stays declared ``BLOB`` with numbers in the rows not yet
    converted, and the original text of the kept spellings, in the ``TEMP``
    table, is gone - ``"1.50"`` reads ``1.5``. The table still passes
    integrity_check, and a ``BLOB`` column that holds numbers is the sign.

    The table is copied into a new one instead - a *rebuild*, in the
    transaction that widens - if the connection refuses ``PRAGMA
    writable_schema`` (``SQLITE_DBCONFIG_DEFENSIVE``) or the table has been
    changed by someone else (indexes, triggers, an edited definition). A
    rebuild copies every row in one transaction, once per widening (a chunk
    that widens several columns rebuilds once): on big-1gb.csv's 10 columns
    that measured 0.8-1.3s at 1M rows and 9-15s, COMMIT included, at 5.7M -
    the write lock held throughout.
    """

    # Rows per slice of the conversion to text, instead of sizing slices to
    # take about CONVERT_SECONDS: for tests, which set it to make the
    # backlog last as long as possible
    _convert_rows = None

    def __init__(self, table, *, schema=None):
        self.table = table
        self.rows_written = 0
        # Values whose original text had to be kept, see the module docstring
        self.spellings_kept = 0
        self.widenings = []
        self.created = False
        self.finished = False
        # Stored numbers still to be turned into text after widenings to
        # text: column index -> [first rowid, last rowid] to convert
        self._backlog = {}
        # Seconds per row of a conversion slice in which every row has to be
        # rewritten, measured by slices that rewrote most of their rows -
        # and until one has, the seconds per row of inserting rows into this
        # table, which measured within 0.7-1.4x of it (see _slice_rows())
        self._rewrite_cost = None
        self._insert_cost = None
        # The rowid of the last row this inserter wrote, to tell that the
        # table is still the one it wrote (see _table_is_ours())
        self._last_rowid = 0
        #: Time spent converting stored numbers to text after widenings
        self.convert_seconds = 0.0
        self._columns = []
        self._by_key = {}
        self._used_names = set()
        self._keys = ()
        self._known_sql = None
        # INSERT INTO table (columns), and the statement for one row
        self._insert_head = None
        self._insert_sql = None
        # INSERT statements for that many rows at a time, see _insert(),
        # and the connection's limit on bound parameters
        self._multi_row_sql = {}
        self._variable_limit = None
        self._spellings_table = "temp." + _quote(
            f"_datasette_spellings_{uuid.uuid4().hex[:12]}"
        )
        self._spellings_created = False
        # Rows with no keys at all that arrived before any column was known
        self._pending_empty = 0
        # Bind floats rather than float text, see _sqlite_parses_floats_exactly
        self._parse_floats = None
        if schema:
            for key, type_ in schema.items():
                column = self._new_column(key)
                if type_ is not None:
                    column.rank = _RANK[normalize_type(type_)]
                    column.fixed = True

    @property
    def columns(self):
        "Mapping of original row key to SQL column name."
        return {column.key: column.name for column in self._columns}

    @property
    def types(self):
        "Mapping of SQL column name to its type."
        return {column.name: column.type for column in self._columns}

    @property
    def backlog(self):
        """
        How many rows (by rowid) may still hold numbers that have to become
        text after a column widened to text. 0 when there are none.
        """
        if not self._backlog:
            return 0
        low = min(low for low, _ in self._backlog.values())
        high = max(high for _, high in self._backlog.values())
        return high - low + 1

    # Writing ------------------------------------------------------------

    def write(self, conn, rows, columns=None):
        """
        Write one chunk of rows. The first non-empty chunk is the sample:
        types are detected from it and the table is created. Then convert a
        slice of the :attr:`backlog`, if there is one. Returns the number
        of rows written. Does not commit.

        Rows are dictionaries - or, with ``columns`` (a list of keys), lists
        or tuples of values in that order, which saves building a dictionary
        per row. A positional row can be shorter than ``columns``, and is
        then exactly the dictionary of the keys it reaches: the rest of its
        values are ``None``, and a key that no row has reached yet adds no
        column (new keys add columns at the end, in order). A later chunk
        can have more ``columns`` (or put them in another order). A
        dictionary among positional rows is read by its keys. A row that is
        a string, or not a sequence at all, raises ``TypeError``; one longer
        than ``columns`` raises ``ValueError``.
        """
        if self.finished:
            raise ValueError("finish() has already been called")
        if not isinstance(rows, list):
            rows = list(rows)
        if not rows:
            return 0
        if columns is not None:
            columns = list(columns)
            if len(set(columns)) != len(columns):
                raise ValueError(f"columns has duplicate keys: {columns!r}")
        snapshot = self.snapshot()
        try:
            written = self._write(conn, rows, columns)
            if self._backlog:
                self._convert_slice(conn, self._slice_rows())
            return written
        except BaseException:
            # The caller rolls the transaction back; match it
            self.restore(snapshot)
            raise

    def convert(self, conn, rows=None):
        """
        Convert the next slice of the :attr:`backlog` - stored numbers that
        have to become text since their column widened to text - sized to
        take about ``CONVERT_SECONDS`` unless ``rows`` says how many. Returns the backlog left. Does not commit: run each
        call in its own transaction so other writes can go in between.
        """
        if not self._backlog:
            return 0
        snapshot = self.snapshot()
        try:
            self._convert_slice(conn, rows or self._slice_rows())
        except BaseException:
            self.restore(snapshot)
            raise
        return self.backlog

    def snapshot(self):
        """
        Capture the inserter's state. If a transaction that ran
        :meth:`write` is rolled back for a reason the inserter cannot see
        (a ``write_wrapper`` plugin raising after the write, say), pass this
        to :meth:`restore` and the same chunk can be written again.
        :meth:`write` does this itself when it raises.
        """
        return (
            [dataclasses.replace(column) for column in self._columns],
            set(self._used_names),
            self._known_sql,
            self._insert_head,
            self._insert_sql,
            self.rows_written,
            self.spellings_kept,
            len(self.widenings),
            self.created,
            self.finished,
            self._spellings_created,
            self._pending_empty,
            {index: list(bounds) for index, bounds in self._backlog.items()},
            self._last_rowid,
        )

    def restore(self, snapshot):
        "Return to a state captured by :meth:`snapshot`."
        (
            columns,
            used_names,
            self._known_sql,
            self._insert_head,
            self._insert_sql,
            self.rows_written,
            self.spellings_kept,
            widening_count,
            self.created,
            self.finished,
            self._spellings_created,
            self._pending_empty,
            backlog,
            self._last_rowid,
        ) = snapshot
        # Made for the columns there were
        self._multi_row_sql = {}
        self._backlog = {index: list(bounds) for index, bounds in backlog.items()}
        self._columns = [dataclasses.replace(column) for column in columns]
        self._by_key = {column.key: column for column in self._columns}
        self._keys = tuple(column.key for column in self._columns)
        self._used_names = set(used_names)
        del self.widenings[widening_count:]

    def _write(self, conn, rows, columns=None):
        if not self._columns and not any(rows):
            # Only empty rows so far: a table needs at least one column, so
            # hold on to them until a key turns up
            self._pending_empty += len(rows)
            return 0
        if self._pending_empty:
            rows = [{}] * self._pending_empty + rows
            self._pending_empty = 0
        if self._parse_floats is None:
            self._parse_floats = not _sqlite_parses_floats_exactly(conn)
        if columns is None:
            values = self._row_values(rows)
        else:
            values = self._positional_values(rows, columns)
        changes, plans = self._scan(values)
        if not self.created:
            self._create(conn)
        else:
            if any(column.declared == _NONE for column in self._columns):
                self._add_columns(conn)
            if changes:
                self._widen(conn, changes)
        # After widening: what a NULL meant so far decides how a column
        # widened in this chunk converts, so only now take in this chunk
        spellings = []
        for index, col_values, types_present, rank, fast, odd in plans:
            # A column is declared BLOB while its stored numbers become
            # text: store text forms, there is no TEXT affinity to make them
            declared = _B if index in self._backlog else rank
            spellings.extend(
                self._convert(
                    values, index, col_values, types_present, rank, declared, fast, odd
                )
            )
        table = _quote(self.table)
        base = conn.execute(f"select max(rowid) from {table}").fetchone()[0] or 0
        began = time.perf_counter()
        try:
            self._insert(conn, values)
        except UnicodeEncodeError:
            # A lone surrogate: remove what got in, replace them and retry
            conn.execute(f"delete from {table} where rowid > ?", [base])
            values = [list(map(_without_surrogates, row)) for row in values]
            self._insert(conn, values)
        if len(values) >= 100:
            cost = (time.perf_counter() - began) / len(values)
            self._insert_cost = (
                cost if self._insert_cost is None else (self._insert_cost + cost) / 2
            )
        if spellings:
            self._keep_spellings(conn, spellings, base, len(values))
        self.rows_written += len(values)
        self._last_rowid = base + len(values)
        return len(values)

    def _insert(self, conn, values):
        """
        Insert rows (lists of a value per column, in order) with as few
        statements as SQLite's limit on bound parameters allows: ``INSERT
        ... VALUES (...), (...), ...`` with up to 32766 parameters on a
        modern SQLite, so a chunk of 1,000 rows of 10 columns is one
        statement.

        executemany() steps one statement per row, and the sqlite3 module
        releases the GIL around every step. While other threads want the
        GIL - pages being rendered, the event loop decoding the worker's
        rows - the write thread then waits to get it back once per row.
        A background import of 1M rows of big-1gb.csv took 35-46s with
        four clients browsing without pause against 13-16s this way, and
        8.0-8.8s either way with nothing else running. On its own, in one
        thread, building and binding the bigger statements costs 10-15%
        more CPU than executemany() (2.9-3.2s against 3.3-3.6s for those
        1M rows).
        """
        width = len(self._columns)
        if self._variable_limit is None:
            self._variable_limit = _variable_limit(conn)
        per_statement = self._variable_limit // width
        if per_statement <= 1 or len(values) == 1:
            conn.executemany(self._insert_sql, values)
            return
        chain = itertools.chain.from_iterable
        for start in range(0, len(values), per_statement):
            rows = values[start : start + per_statement]
            sql = self._multi_row_sql.get(len(rows))
            if sql is None:
                row = "({})".format(", ".join("?" * width))
                sql = self._insert_head + " VALUES " + ", ".join([row] * len(rows))
                self._multi_row_sql[len(rows)] = sql
            conn.execute(sql, list(chain(rows)))

    def finish(self, conn):
        """
        Complete the import: create the table if no rows arrived (only
        possible if a schema named the columns - otherwise no table is
        created), convert what is left of the :attr:`backlog` (all of it,
        in this transaction: call :meth:`convert` until it returns 0 first
        to keep transactions short) and drop the kept spellings. Returns
        self.
        Safe to call more than once. Does not commit.
        """
        if self.finished:
            return self
        snapshot = self.snapshot()
        try:
            return self._finish(conn)
        except BaseException:
            self.restore(snapshot)
            raise

    def discard(self, conn):
        """
        Drop the kept spellings of an import that will not be finished.
        Does not touch the table. Does not commit.
        """
        conn.execute(f"DROP TABLE IF EXISTS {self._spellings_table}")
        self._spellings_created = False

    def _table_is_ours(self, conn):
        """
        Whether the table is still the one this inserter created and wrote:
        the same definition, and its last row the last one written. Checked
        before finishing in the background after a cancelled import, when
        the caller may have dropped the table - and an import may even have
        created a new one by that name, which must not be "converted".
        """
        if not self.created or _table_sql(conn, self.table) != self._known_sql:
            return False
        last = conn.execute(f"select max(rowid) from {_quote(self.table)}")
        return (last.fetchone()[0] or 0) == self._last_rowid

    def _finish(self, conn):
        if not self.created and not self._columns:
            # Zero rows (or only empty ones) and no schema: there is nothing
            # to create a table from
            self.finished = True
            return self
        if not self.created:
            for column in self._columns:
                if column.rank == _NONE:
                    column.rank = _T
            self._create(conn)
        if self._backlog:
            self._convert_slice(conn, self.backlog)
        if self._spellings_created:
            self.discard(conn)
        self.finished = True
        return self

    # Columns ------------------------------------------------------------

    def _new_column(self, key):
        if isinstance(key, str):
            name = key
        elif key is None:
            name = ""
        else:
            name = str(key)
        name = _without_surrogates(name.replace("\x00", ""))
        if not name.strip():
            name = "untitled"
        # SQLite column names are case-insensitive. A column called "rowid"
        # would hide the real rowid, which Datasette uses for these tables.
        if name.lower() in self._used_names or name.lower() == "rowid":
            suffix = 2
            while f"{name}_{suffix}".lower() in self._used_names:
                suffix += 1
            name = f"{name}_{suffix}"
        self._used_names.add(name.lower())
        column = _Column(key=key, name=name, index=len(self._columns))
        if self.rows_written:
            # The rows written so far hold NULL for it
            column.nulls = "null"
        self._columns.append(column)
        self._by_key[key] = column
        self._keys = tuple(c.key for c in self._columns)
        return column

    def _row_values(self, rows):
        "Turn a list of dicts into lists of values, adding new columns."
        keys = self._keys
        by_key = self._by_key
        for row in rows:
            if tuple(row) != keys:
                for key in row:
                    if key not in by_key:
                        self._new_column(key)
                keys = self._keys
        return [list(map(row.get, keys)) for row in rows]

    def _positional_values(self, rows, columns):
        """
        Turn rows of values in ``columns`` order (and any dicts among them)
        into new lists in table column order, adding new columns.

        A row stands for the dictionary ``dict(zip(columns, row))``, so a
        key that no row is long enough to reach adds no column yet. The
        worker decoder announces keys ahead of the rows that have them: a
        column made for one of those now would be all NULL, an INTEGER
        column that its first text value then widened.
        """
        by_key = self._by_key
        kinds = set(map(type, rows))
        plain = kinds <= _SEQUENCE_TYPES
        too_long = False
        if plain:
            reach = max(map(len, rows))
            too_long = reach > len(columns)
            if not too_long:
                for key in columns[:reach]:
                    if key not in by_key:
                        self._new_column(key)
        else:
            reach = 0
            for row in rows:
                if isinstance(row, dict):
                    for key in row:
                        if key not in by_key:
                            self._new_column(key)
                elif isinstance(row, (str, bytes, bytearray)) or not isinstance(
                    row, Sequence
                ):
                    # A line of a file, say: its characters are not values
                    raise TypeError(
                        "Rows with columns= must be lists, tuples or dicts, "
                        f"not {type(row).__name__}"
                    )
                elif len(row) > len(columns):
                    too_long = True
                elif len(row) > reach:
                    # In row order, as for dictionaries
                    for key in columns[reach : len(row)]:
                        if key not in by_key:
                            self._new_column(key)
                    reach = len(row)
        if too_long:
            raise ValueError(f"A row has more values than the {len(columns)} columns")
        width = len(self._columns)
        positions = [by_key[key].index for key in columns[:reach]]
        if plain and positions == list(range(len(positions))):
            # The usual case: the columns are the table's, in its order
            values = list(map(list, rows))
            if min(map(len, values)) < width:
                for row in values:
                    if len(row) < width:
                        row.extend([None] * (width - len(row)))
            return values
        keys = self._keys
        values = []
        for row in rows:
            if isinstance(row, dict):
                values.append(list(map(row.get, keys)))
            else:
                row_values = [None] * width
                for position, value in zip(positions, row):
                    row_values[position] = value
                values.append(row_values)
        return values

    # Types --------------------------------------------------------------

    def _scan(self, values):
        """
        Check each column against this chunk. Returns ``{key: rank}`` for
        columns that must widen (before the table exists this sets the
        detected types instead) and the arguments for :meth:`_convert`.
        """
        changes = {}
        plans = []
        for index, column in enumerate(self._columns):
            current = column.rank
            col_values = [row[index] for row in values]
            types_present = set(map(type, col_values))
            # Every value is a canonical str, or every value an exact number
            fast = False
            # For a column of strings: positions of the non-canonical ones
            odd = None
            if column.fixed or current == _B:
                new_rank = current
            elif current == _T:
                # Only bytes can widen a text column
                new_rank = _B if types_present & _BYTES_TYPES else _T
            elif types_present == _STR:
                # CSV: check with one regex per value, in C, and classify
                # only the values that do not match
                odd = _odd_positions(col_values, current)
                new_rank = max(current, _I) if any(col_values) else current
                if odd is None:
                    fast = True
                else:
                    new_rank = _widest([col_values[i] for i in odd], new_rank)
            elif types_present <= _NUMBERS_AND_STR:
                # JSON numbers, perhaps with "" for missing values: no
                # per-value Python work
                numbers = (
                    [v for v in col_values if v is not None and v != ""]
                    if types_present & _NULLISH_TYPES
                    else col_values
                )
                if (
                    str in types_present
                    and not set(map(type, numbers)) <= _NUMBER_TYPES
                ):
                    new_rank = _widest(col_values, current)
                elif not numbers:
                    new_rank = current
                    fast = True
                elif (
                    min(numbers) < _INT_MIN
                    or max(numbers) > _INT_MAX
                    or (float in types_present and any(map(math.isnan, numbers)))
                ):
                    new_rank = _widest(col_values, current)
                else:
                    new_rank = max(current, _F if float in types_present else _I)
                    fast = -_EXACT <= min(numbers) and max(numbers) <= _EXACT
            else:
                new_rank = _widest(col_values, current)
            if new_rank > current:
                if column.declared == _NONE or new_rank <= column.declared:
                    # Not in the table yet, or no evidence so far and the
                    # column is already declared INTEGER: nothing to widen
                    column.rank = new_rank
                else:
                    changes[column.key] = new_rank
            plans.append(
                (index, col_values, types_present, max(new_rank, _I), fast, odd)
            )
        return changes, plans

    def _convert(
        self, values, index, col_values, types_present, rank, declared, fast, odd
    ):
        """
        Make the values of one column storable (in place) in a column of
        type ``rank``, declared ``declared`` (BLOB while a backlog is being
        converted, otherwise the same). Returns a list of ``(column, row,
        text)`` for values whose original text has to be kept because the
        stored value will not reproduce it.
        """
        column = self._columns[index]
        if declared >= _T:
            # TEXT or BLOB: values in their text form, bytes as they are.
            # TEXT affinity turns an integer into the same text str() does.
            if types_present - _TEXT_TYPES:
                keep_ints = declared == _T
                for row in values:
                    value = row[index]
                    kind = type(value)
                    if kind is str or value is None or kind is bytes:
                        continue
                    if kind is int and keep_ints and _INT_MIN <= value <= _INT_MAX:
                        continue
                    row[index] = _text_form(value)
            return ()
        if column.fixed:
            self._convert_fixed(values, index, col_values, types_present)
            return ()
        if not fast and odd is None:
            return self._convert_numeric(values, index, column, rank)
        # Canonical strings (but for the odd ones), or numbers within +-2**53
        has_blank = str in types_present and "" in col_values
        spellings = self._nulls(
            values, index, column, has_blank, _NoneType in types_present
        )
        if rank == _F and str in types_present and self._parse_floats:
            # The canonical ones; _odd_text() decides about the others
            skip = set(odd or ())
            for i, row in enumerate(values):
                if i not in skip and type(row[index]) is str:
                    row[index] = float(row[index])
        if odd:
            for i in odd:
                self._odd_text(values[i], index, i, column, rank, spellings)
        return spellings

    def _odd_text(self, row, index, i, column, rank, spellings):
        """
        A non-empty string in an INTEGER or REAL column that may not be
        canonical: bind it, keeping its text if the stored number will not
        give it back.
        """
        value = row[index]
        if _canonical(value, rank):
            # Canonical after all (e.g. "1.5" once the column is float)
            if rank == _F and self._parse_floats:
                row[index] = float(value)
            return
        if rank == _I and _classify_rank(value) == _I:
            # SQLite parses integers exactly: bind the text
            number = int(value)
            stored = str(number)
            if not -_EXACT <= number <= _EXACT:
                column.big_ints = True
        else:
            # Bind the float, so the stored value is exactly float()
            number = row[index] = float(value)
            stored = _render_real(number)
        if stored != value:
            spellings.append((column.index, i, value))

    def _null_kind(self, column, blank):
        "Record a NULL stored for a blank or a real NULL; maybe a spelling."
        kind = "blank" if blank else "null"
        if column.nulls is None:
            column.nulls = kind
            return _NO_ENTRY
        if column.nulls == kind:
            return _NO_ENTRY
        return "" if blank else None

    def _nulls(self, values, index, column, has_blank, has_null):
        "Turn blanks into NULLs in a numeric column, keeping what each was."
        if not has_blank and not has_null:
            return []
        kind = "blank" if has_blank else "null"
        if has_blank != has_null and column.nulls in (None, kind):
            column.nulls = kind
            if has_blank:
                for row in values:
                    if row[index] == "":
                        row[index] = None
            return []
        spellings = []
        for i, row in enumerate(values):
            value = row[index]
            if value is None or value == "":
                row[index] = None
                entry = self._null_kind(column, value == "")
                if entry is not _NO_ENTRY:
                    spellings.append((column.index, i, entry))
        return spellings

    def _convert_numeric(self, values, index, column, rank):
        """
        An INTEGER or REAL column where some values are not canonical:
        value by value, with a quick way through for the canonical ones.
        """
        spellings = []
        pattern = _INT_FAST if rank == _I else _REAL_FAST
        limit = _REAL_LENGTH if rank == _F else 20
        to_float = rank == _F and self._parse_floats
        for i, row in enumerate(values):
            value = row[index]
            kind = type(value)
            if kind is str:
                if value and len(value) <= limit and pattern.fullmatch(value):
                    if to_float:
                        row[index] = float(value)
                elif value:
                    self._odd_text(row, index, i, column, rank, spellings)
                else:
                    row[index] = None
                    entry = self._null_kind(column, True)
                    if entry is not _NO_ENTRY:
                        spellings.append((column.index, i, entry))
                continue
            if value is None or (isinstance(value, float) and math.isnan(value)):
                row[index] = None
                entry = self._null_kind(column, False)
                if entry is not _NO_ENTRY:
                    spellings.append((column.index, i, entry))
                continue
            if kind is not int and kind is not float:
                # bool, subclasses: plain numbers
                if isinstance(value, float):
                    row[index] = float(value)
                elif isinstance(value, int):
                    value = row[index] = int(value)
                else:
                    # Cannot happen in a numeric column, but stay storable
                    row[index] = _storable(value)
                    continue
            if type(value) is int and not -_EXACT <= value <= _EXACT:
                if rank == _I:
                    column.big_ints = True
                elif _render_real(float(value)) != str(value):
                    # A REAL rounds it
                    spellings.append((column.index, i, str(value)))
            # A float's text form is defined by the float itself
        return spellings

    def _convert_fixed(self, values, index, col_values, types_present):
        "A numeric column typed by the schema: trusted, never widened."
        if str in types_present and "" in col_values:
            # "" means NULL in a numeric column
            for row in values:
                if row[index] == "":
                    row[index] = None
        if types_present - {str, float, _NoneType}:
            for row in values:
                value = row[index]
                if value is not None and (
                    type(value) not in (str, int, float)
                    or (type(value) is int and not _INT_MIN <= value <= _INT_MAX)
                ):
                    row[index] = _storable(value)

    def _keep_spellings(self, conn, spellings, base, count):
        "Save original texts of rows just inserted after rowid ``base``."
        last = conn.execute(f"select max(rowid) from {_quote(self.table)}")
        if last.fetchone()[0] != base + count:
            raise RuntimeError("Inserted rows did not get consecutive rowids")
        self._ensure_spellings_table(conn)
        conn.executemany(
            f"INSERT INTO {self._spellings_table} (col, rid, val) VALUES (?, ?, ?)",
            [
                (col, base + 1 + i, _without_surrogates(text))
                for col, i, text in spellings
            ],
        )
        for col, _, _ in spellings:
            self._columns[col].spellings += 1
        self.spellings_kept += len(spellings)

    def _ensure_spellings_table(self, conn):
        if not self._spellings_created:
            conn.execute(
                f"CREATE TEMP TABLE IF NOT EXISTS {self._spellings_table} "
                "(col INTEGER, rid INTEGER, val TEXT, PRIMARY KEY (col, rid)) "
                "WITHOUT ROWID"
            )
            self._spellings_created = True

    # Table changes ------------------------------------------------------

    def _sql_rank(self, column):
        "The type a column is declared with in the table right now."
        if column.index in self._backlog:
            # Still converting to text: no affinity, see _edit_schema()
            return _B
        return max(column.declared, _I)

    def _create_sql(self, table=None):
        return "CREATE TABLE {} ({})".format(
            _quote(table or self.table),
            ", ".join(
                f"{_quote(c.name)} {_SQL_TYPES[self._sql_rank(c)]}"
                for c in self._columns
            ),
        )

    def _create(self, conn):
        for column in self._columns:
            column.declared = max(column.rank, _I)
        conn.execute(self._create_sql())
        self.created = True
        self._refresh(conn)

    def _refresh(self, conn):
        self._known_sql = _table_sql(conn, self.table)
        self._insert_head = "INSERT INTO {} ({})".format(
            _quote(self.table), ", ".join(_quote(c.name) for c in self._columns)
        )
        self._insert_sql = "{} VALUES ({})".format(
            self._insert_head, ", ".join("?" for _ in self._columns)
        )
        self._multi_row_sql = {}

    def _add_columns(self, conn):
        for column in self._columns:
            if column.declared == _NONE:
                column.declared = max(column.rank, _I)
                table, name = _quote(self.table), _quote(column.name)
                sql_type = _SQL_TYPES[column.declared]
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
        self._refresh(conn)

    def _widen(self, conn, changes):
        # Only the columns whose stored numbers become text have something
        # to do after the schema edit
        converting = {column.key for column in self._converting(changes)}
        new_widenings = []
        for key, rank in changes.items():
            column = self._by_key[key]
            new_widenings.append(
                Widening(
                    column=column.name,
                    from_type=column.type,
                    to_type=_KINDS[rank],
                    at_row=self.rows_written,
                    method="incremental" if key in converting else "schema",
                )
            )
            column.rank = rank
        self.widenings.extend(new_widenings)
        start = time.perf_counter()
        self._keep_big_ints(conn, changes)
        if not self._edit_schema(conn, changes):
            for widening in new_widenings:
                widening.method = "rebuild"
            self._rebuild(conn, changes)
        elapsed = time.perf_counter() - start
        for widening in new_widenings:
            widening.seconds = elapsed

    def _keep_big_ints(self, conn, changes):
        """
        Before an INTEGER column becomes REAL, keep the text of integers a
        REAL would round, in case the column widens to text later. A full
        scan, but only for columns that have held such an integer.
        """
        for key, rank in changes.items():
            column = self._by_key[key]
            if column.big_ints and column.declared == _I and rank == _F:
                self._ensure_spellings_table(conn)
                name = _quote(column.name)
                cursor = conn.execute(
                    f"INSERT OR IGNORE INTO {self._spellings_table} (col, rid, val) "
                    f"SELECT ?, rowid, CAST({name} AS TEXT) FROM {_quote(self.table)} "
                    f"WHERE typeof({name}) = 'integer' "
                    f"AND ({name} > {_EXACT} OR {name} < -{_EXACT})",
                    [column.index],
                )
                column.spellings += cursor.rowcount
                self.spellings_kept += cursor.rowcount
                column.big_ints = False

    def _text_expression(self, column, rids=None):
        """
        SQL turning a numeric column's stored values back into the text they
        came from: kept spellings first, then NULL (or "" if the NULLs stand
        for blanks), then the stored number as text. ``rids`` is SQL for
        the range of rowids the statement covers (``":a AND :b"``), so only
        the spellings in it are looked at.
        """
        name = _quote(column.name)
        null_text = "''" if column.nulls == "blank" else "NULL"
        expression = (
            f"CASE WHEN {name} IS NULL THEN {null_text} "
            f"WHEN typeof({name}) = 'integer' THEN CAST({name} AS TEXT) "
            f"WHEN typeof({name}) = 'real' THEN {_RENDER_FUNCTION}({name}) "
            f"ELSE {name} END"
        )
        if not column.spellings:
            return expression
        return (
            f"CASE WHEN {self._spelled(column, rids)} "
            f"THEN (SELECT val FROM {self._spellings_table} "
            f"WHERE col = {column.index} "
            f"AND rid = {_quote(self.table)}.rowid) ELSE {expression} END"
        )

    def _spelled(self, column, rids=None):
        "SQL: whether this row's value in ``column`` has a kept spelling."
        within = f" AND rid BETWEEN {rids}" if rids else ""
        return (
            f"rowid IN (SELECT rid FROM {self._spellings_table} "
            f"WHERE col = {column.index}{within})"
        )

    # Converting stored numbers to text, a slice at a time -------------

    def _slice_rows(self):
        """
        Rows (of rowid range) for the next slice of the backlog: about
        CONVERT_SECONDS' worth if every one of them has to be rewritten.

        Sized for the worst case because a slice cannot tell in advance how
        many of its rows hold a number: a column that is NULL for the first
        4.5M rows of 6M (a key that turns up late) costs 0.13-0.15us a row
        to skip and 0.8-1.4us a row to rewrite, so a slice sized from the
        speed of skipping would rewrite ~7x too many rows once it reached
        the numbers (the first version did, reaching 1M-row slices and
        380-520ms transactions). Skipping rows is then just quicker than
        planned: 100 short slices for a 4.5M-row gap.

        The cost of a rewrite comes from slices that rewrote most of their
        rows: the first such slice sets it, later ones raise it at once or
        lower it by halves. Until then it is the cost of inserting a row
        into this table, which scales with the row's width the same way: on
        big-1gb.csv's rows inserting measured 1.1us a row and rewriting
        0.8-1.35us.
        """
        if self._convert_rows:
            return self._convert_rows
        cost = self._rewrite_cost or self._insert_cost
        if not cost:
            return _FIRST_CONVERT_ROWS
        rows = int(CONVERT_SECONDS / cost)
        return min(max(rows, _MIN_CONVERT_ROWS), _MAX_CONVERT_ROWS)

    def _measure_slice(self, covered, rewritten, elapsed):
        "Learn the cost of rewriting a row from a slice, see _slice_rows()."
        if covered <= 0 or elapsed <= 0:
            return
        if rewritten >= 100 and rewritten >= covered * _DENSE_FRACTION:
            # An overestimate when not every row needed it, as it includes
            # skipping the others: at most 1.4x at 25%
            sample = elapsed / rewritten
        elif elapsed > CONVERT_SECONDS:
            # Over time with few rows rewritten: skipping is dearer than
            # expected (wide rows, a busy machine) - size the next slice so
            # that this one would have fit
            sample = elapsed / covered
        else:
            return
        if self._rewrite_cost is None or sample > self._rewrite_cost:
            self._rewrite_cost = sample
        else:
            self._rewrite_cost = (self._rewrite_cost + sample) / 2

    def _convert_slice(self, conn, rows):
        """
        Turn stored numbers into text in the next ``rows`` rowids of the
        backlog, with one UPDATE. Every column with backlog in that range is
        in it - rewriting a row applies every column's affinity, which
        would turn a number still waiting in another TEXT column into text
        SQLite's way, without its kept spelling - each only within its own
        range: rows before it are converted already (a NULL there is a
        real NULL), rows after it were inserted as text.
        """
        began = time.perf_counter()
        if conn.execute(
            "select 1 from sqlite_master where type = 'trigger' and tbl_name = ?",
            [self.table],
        ).fetchone():
            # Someone else's trigger would fire for every converted row:
            # copy the table instead, which keeps the trigger but runs it
            # for nothing (rare, and O(rows) in one transaction)
            self._rebuild(conn, {})
            self.convert_seconds += time.perf_counter() - began
            return
        start = min(low for low, _ in self._backlog.values())
        end = start + rows - 1
        sets, conditions = [], []
        params = {"start": start, "end": end}
        done = []
        for index in sorted(self._backlog):
            low, high = self._backlog[index]
            if low > end or high < start:
                continue
            column = self._columns[index]
            done.append((column, max(low, start), min(high, end)))
            name = _quote(column.name)
            params[f"lo{index}"] = low
            params[f"hi{index}"] = high
            in_range = f"rowid BETWEEN :lo{index} AND :hi{index}"
            sets.append(
                f"{name} = CASE WHEN {in_range} THEN "
                f"{self._text_expression(column, ':start AND :end')} "
                f"ELSE {name} END"
            )
            # Only rows with something to change: a number, a NULL that
            # stands for a blank, or a kept spelling. Not limited to the
            # column's range here - the CASE above does that: with more
            # rowid bounds in the WHERE than the slice's own, SQLite can
            # pick one of those for its range scan and visit the rest of
            # the backlog every slice (measured: 56ms for 500 rows with
            # 550,000 left, against 1ms) - EXPLAIN QUERY PLAN looks the same
            conditions.append(f"typeof({name}) IN ('integer', 'real')")
            if column.nulls == "blank":
                conditions.append(f"{name} IS NULL")
            if column.spellings:
                conditions.append(self._spelled(column, ":start AND :end"))
        conn.create_function(_RENDER_FUNCTION, 1, _render_sql, deterministic=True)
        rewritten = conn.execute(
            "UPDATE {} SET {} WHERE rowid BETWEEN :start AND :end AND ({})".format(
                _quote(self.table), ", ".join(sets), " OR ".join(conditions)
            ),
            params,
        ).rowcount
        completed = False
        for column, low, high in done:
            if column.spellings:
                cursor = conn.execute(
                    f"DELETE FROM {self._spellings_table} "
                    "WHERE col = ? AND rid BETWEEN ? AND ?",
                    [column.index, low, high],
                )
                column.spellings -= cursor.rowcount
            bounds = self._backlog[column.index]
            bounds[0] = end + 1
            if bounds[0] > bounds[1]:
                del self._backlog[column.index]
                completed = completed or column.declared < _B
        if completed and not self._write_schema(conn):
            # Columns that are all text now are declared TEXT - if editing
            # the schema is no longer possible, by copying the table
            self._rebuild(conn, {})
        elapsed = time.perf_counter() - began
        self.convert_seconds += elapsed
        covered = max(high for _, _, high in done) - start + 1
        self._measure_slice(covered, rewritten, elapsed)

    def _converting(self, changes):
        "Columns in ``changes`` whose stored numbers have to become text."
        return [
            self._by_key[key]
            for key, rank in changes.items()
            if rank >= _T and self._by_key[key].declared < _T
        ]

    def _converted(self, conn, columns):
        "Drop the spellings of columns that are text now."
        for column in columns:
            if column.spellings:
                conn.execute(
                    f"DELETE FROM {self._spellings_table} WHERE col = ?",
                    [column.index],
                )
                column.spellings = 0

    def _write_schema(self, conn):
        """
        Make the table's definition in sqlite_schema :meth:`_create_sql`,
        following https://www.sqlite.org/lang_altertable.html#otheralter -
        safe only because this inserter created the table, so no index,
        trigger or constraint depends on column affinity. Returns False,
        having changed nothing, if that cannot be guaranteed (someone else
        changed the table) or the edit is refused.
        """
        if _table_sql(conn, self.table) != self._known_sql:
            return False
        dependents = conn.execute(
            "select count(*) from sqlite_master where tbl_name = ? "
            "and type in ('index', 'trigger')",
            [self.table],
        ).fetchone()[0]
        if dependents:
            return False
        new_sql = self._create_sql()
        version = conn.execute("PRAGMA schema_version").fetchone()[0]
        try:
            conn.execute("PRAGMA writable_schema = ON")
            try:
                conn.execute(
                    "UPDATE sqlite_master SET sql = ? "
                    "WHERE type = 'table' AND name = ?",
                    [new_sql, self.table],
                )
                conn.execute(f"PRAGMA schema_version = {version + 1}")
            finally:
                conn.execute("PRAGMA writable_schema = OFF")
        except _SQLITE_DATABASE_ERRORS:
            # For example when SQLITE_DBCONFIG_DEFENSIVE is on
            return False
        self._refresh(conn)
        return True

    def _edit_schema(self, conn, changes):
        """
        Change declared types in place (:meth:`_write_schema`). Returns
        False, having changed nothing, if that is not possible.

        Changing a declared type does not rewrite stored values: an integer
        stored under REAL affinity reads back as a float, but numbers under
        TEXT affinity stay numbers until they are converted. They are added
        to the backlog that :meth:`_convert_slice` works through. Until its
        backlog is done such a column is declared ``BLOB``, an affinity that
        changes nothing: SQLite's integrity_check reports a number stored in
        a TEXT column as an error ("NUMERIC value in t.c"), so that would
        make a perfectly good table fail it until the conversion is over,
        and for good if the process died first.
        """
        converting = self._converting(changes)
        old_declared = {c.key: c.declared for c in self._columns}
        old_backlog = dict(self._backlog)
        for key in changes:
            self._by_key[key].declared = changes[key]
        bounds = None
        if converting:
            # Two subqueries: SQLite only finds a min() or max() of the
            # rowid without a full scan if the query has just the one
            table = _quote(self.table)
            low, high = conn.execute(
                f"select (select min(rowid) from {table}), "
                f"(select max(rowid) from {table})"
            ).fetchone()
            if high is not None:
                bounds = [low, high]
                for column in converting:
                    self._backlog[column.index] = list(bounds)
        if not self._write_schema(conn):
            for key, rank in old_declared.items():
                self._by_key[key].declared = rank
            self._backlog = old_backlog
            return False
        if bounds is None:
            # An empty table: nothing to convert
            self._converted(conn, converting)
        return True

    def _rebuild(self, conn, changes):
        """
        Copy the table into a new one declaring the new types, converting
        the stored numbers of columns that become text - and whatever is
        left of the backlog.
        """
        converting = self._converting(changes)
        indexes = {column.index for column in converting}
        selects = []
        for column in self._columns:
            if column.index in indexes:
                selects.append(self._text_expression(column))
            elif column.index in self._backlog:
                low, high = self._backlog[column.index]
                rids = f"{int(low)} AND {int(high)}"
                selects.append(
                    f"CASE WHEN rowid BETWEEN {rids} THEN "
                    f"{self._text_expression(column, rids)} "
                    f"ELSE {_quote(column.name)} END"
                )
                converting.append(column)
            else:
                selects.append(_quote(column.name))
        self._backlog = {}
        for column in self._columns:
            column.declared = max(column.rank, _I)
        if converting:
            conn.create_function(_RENDER_FUNCTION, 1, _render_sql, deterministic=True)
        table = _quote(self.table)
        temp_name = f"{self.table}_widen_{uuid.uuid4().hex[:8]}"
        temp = _quote(temp_name)
        conn.execute(self._create_sql(temp_name))
        conn.execute(
            "INSERT INTO {} (rowid, {}) SELECT rowid, {} FROM {}".format(
                temp,
                ", ".join(_quote(c.name) for c in self._columns),
                ", ".join(selects),
                table,
            )
        )
        # Indexes or triggers someone else added go when the table is dropped
        dependents = [
            row[0]
            for row in conn.execute(
                "select sql from sqlite_master where tbl_name = ? "
                "and type in ('index', 'trigger') and sql is not null",
                [self.table],
            )
        ]
        conn.execute(f"DROP TABLE {table}")
        # Since SQLite 3.26 RENAME checks every view and trigger in the
        # schema, so a view on this table (dropped a moment ago) or any
        # broken view would make it fail. As sqlite-utils' transform() does,
        # rename the legacy way, leaving view definitions alone.
        legacy = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
        conn.execute("PRAGMA legacy_alter_table = ON")
        try:
            conn.execute(f"ALTER TABLE {temp} RENAME TO {table}")
        finally:
            conn.execute(f"PRAGMA legacy_alter_table = {'ON' if legacy else 'OFF'}")
        for sql in dependents:
            conn.execute(sql)
        self._converted(conn, converting)
        self._refresh(conn)


def _table_sql(conn, table):
    row = conn.execute(
        "select sql from sqlite_master where type = 'table' and name = ?", [table]
    ).fetchone()
    return row[0] if row else None


# Driving the inserter -------------------------------------------------------


def iter_chunks(rows, sample_size=1000, batch_size=100):
    "Yield lists of rows: first up to ``sample_size``, then ``batch_size``."
    iterator = iter(rows)
    size = sample_size
    while True:
        chunk = _take(iterator, size)
        if not chunk:
            return
        yield chunk
        size = batch_size


def _take(iterator, size):
    chunk = []
    append = chunk.append
    for row in iterator:
        append(row)
        if len(chunk) >= size:
            break
    return chunk


@dataclasses.dataclass
class ImportResult:
    "What :func:`insert_rows` and :func:`import_rows` return."

    table: str
    # False for zero rows with no schema - there were no columns to create
    created: bool
    rows: int
    # SQL column name -> "integer", "float", "text" or "blob"
    types: dict
    # Row key -> SQL column name, where they differ
    renamed: dict
    widenings: list
    batches: int
    seconds: float
    # Values whose original text had to be kept until the import finished
    spellings_kept: int = 0
    # Time spent turning stored numbers into text after columns widened to
    # text, in all slices together
    convert_seconds: float = 0.0


@dataclasses.dataclass
class ImportStatus:
    """
    What an :func:`import_rows` or :func:`insert_rows` call has done, kept
    up to date while it runs. Pass one in as ``status=`` to find out after
    the call raised an exception or was cancelled.
    """

    #: True once this call's ``CREATE TABLE`` has been committed: the table
    #: is this import's (it did not exist before, and the call creates it)
    created: bool = False
    #: Rows committed so far
    rows: int = 0
    #: ``on_error="drop"`` dropped the table after a failure
    dropped: bool = False
    #: The exception the call raised (``CancelledError`` if it was
    #: cancelled), or None
    error: BaseException | None = None
    #: The call has returned, or raised and finished cleaning up. Every
    #: other field is final once this is True. A cancelled
    #: :func:`import_rows` raises before its clean up is over if that has
    #: stored numbers left to convert (or if it is cancelled again while
    #: cleaning up): the clean up carries on in the background and this
    #: becomes True when it is done - :meth:`wait` waits for that
    done: bool = False
    #: Rows (of rowid range) that may still hold numbers waiting to become
    #: text, see :attr:`TypedInserter.backlog` - converting them is the last
    #: part of an import, after every row has been written
    backlog: int = 0
    #: True once every row has been written: what is left is converting the
    #: :attr:`backlog` and finishing the table
    finishing: bool = False
    _done_event: object = dataclasses.field(
        default=None, init=False, repr=False, compare=False
    )
    # A future that import_rows() sets once the clean up of a failed or
    # cancelled call has made created, dropped and rows final: wait_settled()
    _settling: object = dataclasses.field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def table_exists(self):
        "Whether the table this call created is there."
        return self.created and not self.dropped

    async def wait_settled(self, timeout: float | None = None) -> bool:
        """
        Wait until :attr:`created`, :attr:`dropped` and :attr:`rows` are
        final. They are by the time :func:`import_rows` returns or raises,
        except after a call that was cancelled again while it cleaned up:
        that clean up carries on in the background, and a write of the
        call's that was still running when it raised may yet commit (and,
        with ``on_error="drop"``, the table be dropped). This never waits
        for stored numbers to be converted to text, which can take seconds
        after a cancellation: :meth:`wait` does. False if ``timeout`` ran
        out first.
        """
        settling = self._settling
        if settling is None or settling.done():
            return True
        try:
            await asyncio.wait_for(asyncio.shield(settling), timeout)
        except TimeoutError:
            return False
        return True

    async def wait(self):
        """
        Wait until :attr:`done`: after a cancelled :func:`import_rows`, until
        the clean up that carries on in the background has finished.
        """
        if self.done:
            return
        if self._done_event is None:
            self._done_event = (asyncio.get_running_loop(), asyncio.Event())
            # done may have been set (by insert_rows() in a thread) before
            # there was an event to set
            if self.done:
                return
        await self._done_event[1].wait()

    def _set_done(self):
        self.done = True
        if self._done_event is not None:
            loop, event = self._done_event
            with contextlib.suppress(RuntimeError):
                # Closed loop: nobody is waiting any more
                loop.call_soon_threadsafe(event.set)


ON_ERROR = ("keep", "drop")


def _result(inserter, start, batches):
    return ImportResult(
        table=inserter.table,
        created=inserter.created,
        rows=inserter.rows_written,
        types=inserter.types,
        renamed={k: v for k, v in inserter.columns.items() if k != v},
        widenings=inserter.widenings,
        batches=batches,
        seconds=time.perf_counter() - start,
        spellings_kept=inserter.spellings_kept,
        convert_seconds=inserter.convert_seconds,
    )


def _drop_table(conn, inserter):
    inserter.discard(conn)
    conn.execute(f"DROP TABLE IF EXISTS {_quote(inserter.table)}")


def insert_rows(
    conn,
    table,
    rows,
    *,
    schema=None,
    sample_size=1000,
    batch_size=100,
    progress=None,
    columns=None,
    on_error="keep",
    status=None,
):
    """
    Import ``rows`` into a new ``table`` using a plain
    ``sqlite3.Connection``, committing after every chunk - and after every
    slice of converting stored numbers to text that is left when the rows
    run out (see :class:`TypedInserter`).

    If the connection already has a transaction open (``sqlite3``'s legacy
    transaction control) the caller owns it and nothing is committed.
    ``progress``, if provided, is called with the number of rows written so
    far after each chunk, and after each slice of the conversion that
    follows the last one (``status.finishing`` is True by then and
    ``status.backlog`` says how much is left).

    ``columns`` makes rows lists or tuples of values in that order, as for
    :meth:`TypedInserter.write`; it is read again for every chunk, so it
    can be a list that grows while the rows are read.

    If anything raises (``KeyboardInterrupt`` too), the chunks already
    committed stay and, with ``on_error="keep"`` (the default), the table
    is completed as :meth:`TypedInserter.finish` would - numbers converted,
    kept spellings dropped - so it holds exactly the rows committed. With
    ``on_error="drop"`` the table is dropped instead, if this call created
    it. Then the exception is raised again. Pass an :class:`ImportStatus`
    as ``status`` to see what happened.

    Completing the table converts what is left of the stored numbers after
    a late widening to text, in transactions of their own: about 2.4s per
    million rows of big-1gb.csv, before the exception is raised again. A
    second ``KeyboardInterrupt`` stops that, leaving the rest unconverted
    (the column declared ``BLOB``, numbers in some of its rows, kept
    spellings lost with the connection). If the table is not wanted after
    a failure, pass ``on_error="drop"``, which converts nothing.
    """
    if on_error not in ON_ERROR:
        raise ValueError(f"on_error must be one of {ON_ERROR}")
    start = time.perf_counter()
    inserter = TypedInserter(table, schema=schema)
    if status is None:
        status = ImportStatus()

    def committed(fn):
        snapshot = inserter.snapshot()
        try:
            with _Transaction(conn):
                result = fn()
        except BaseException:
            # The COMMIT can fail after fn() returned
            inserter.restore(snapshot)
            raise
        status.created = inserter.created
        status.rows = inserter.rows_written
        status.backlog = inserter.backlog
        return result

    batches = 0
    try:
        for chunk in iter_chunks(rows, sample_size, batch_size):
            names = None if columns is None else list(columns)
            committed(
                lambda chunk=chunk, names=names: inserter.write(conn, chunk, names)
            )
            batches += 1
            if progress is not None:
                progress(inserter.rows_written)
        status.finishing = True
        while inserter.backlog:
            committed(lambda: inserter.convert(conn))
            if progress is not None:
                progress(inserter.rows_written)
        committed(lambda: inserter.finish(conn))
    except BaseException as ex:
        status.error = ex
        # Keep the original error
        with contextlib.suppress(Exception):
            if on_error == "drop" and inserter.created:
                committed(lambda: _drop_table(conn, inserter))
                status.dropped = True
            elif inserter.created:
                while inserter.backlog:
                    committed(lambda: inserter.convert(conn))
                committed(lambda: inserter.finish(conn))
        if not inserter.finished:
            with contextlib.suppress(Exception):
                committed(lambda: inserter.discard(conn))
        raise
    finally:
        status._set_done()
    return _result(inserter, start, batches)


class _Transaction:
    "BEGIN/COMMIT around a block, unless the caller already opened one."

    def __init__(self, conn):
        self.conn = conn
        self.owned = False

    def __enter__(self):
        # Python 3.12+ autocommit=False: always in a transaction, commit()
        # ends it and opens the next one
        self.manual = getattr(self.conn, "autocommit", None) is False
        if not self.manual and not self.conn.in_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
            self.owned = True

    def __exit__(self, exc_type, exc, tb):
        if self.manual:
            if exc_type:
                self.conn.rollback()
            else:
                self.conn.commit()
        elif self.owned:
            self.conn.execute("ROLLBACK" if exc_type else "COMMIT")


async def import_rows(
    db,
    table,
    rows,
    *,
    schema=None,
    sample_size=1000,
    batch_size=100,
    progress=None,
    columns=None,
    on_error="keep",
    status=None,
):
    """
    Import ``rows`` into a new ``table`` in a Datasette
    :class:`~datasette.database.Database`.

    Each chunk is written by its own ``await db.execute_write_fn()`` call, so
    each is a committed transaction: readers see rows arrive, other writes
    can run in between and ``write_wrapper`` plugins see every chunk. So is
    each slice of converting stored numbers to text that is left when the
    rows run out (see :class:`TypedInserter`), then ``finish()``.

    ``rows`` can be a regular iterable (for example from
    ``sqlite_utils.utils.rows_from_file()``), which is consumed in a thread
    so reading and parsing never blocks the event loop, or an async
    iterable, for example rows parsed from a worker subprocess. The next
    chunk is read while the current one is being written.

    ``columns`` makes rows lists or tuples of values in that order, as for
    :meth:`TypedInserter.write`. It is read as each chunk is sent to be
    written, so it can be a list that grows (only ever at the end) while
    rows arrive, like the column list of the worker protocol: a row read
    before columns were added is just shorter.

    ``progress``, if provided, is called (and awaited, if that returns an
    awaitable) with the number of rows written so far after every chunk,
    and after every slice of the conversion that follows the last one
    (``status.finishing`` is True by then and ``status.backlog`` says how
    much is left).

    If anything raises - a write, the rows, ``progress`` - or the call is
    cancelled, the chunks already committed stay and the clean up goes
    through the write queue, after any write of this import that is still
    running (a write the caller stopped waiting for still runs): with
    ``on_error="keep"`` (the default) the table is completed as
    ``finish()`` would - stored numbers converted, the ``TEMP`` table of
    kept spellings dropped - so it holds exactly the rows committed; with
    ``on_error="drop"`` the table is dropped if this call created it. The
    clean up is shielded from cancellation. Then the exception is raised
    again. Pass an :class:`ImportStatus` as ``status`` to find out what
    happened, in particular whether this call created the table. If the
    call is cancelled again while it cleans up, it raises at once and the
    clean up carries on: :meth:`ImportStatus.wait_settled` waits until the
    status says what it did.

    A cancelled call raises as soon as the write in progress (if any) is
    done and, with ``on_error="drop"``, the table dropped: a cancellation
    has to take effect at once, and stop the worker that feeds the rows.
    With ``on_error="keep"`` and stored numbers left to convert (a late
    widening to text, about 2.4s of work per million rows of big-1gb.csv),
    the conversion carries on in the background, a slice per write as
    before, starting once the cancelled task has finished (or after a
    second, if it carries on): ``status.done`` becomes True, and
    :meth:`ImportStatus.wait` returns, when it is over. Each slice first
    checks that the table is still the one this import wrote, so the
    caller can drop it meanwhile (that stops the conversion) - but pass
    ``on_error="drop"`` rather than dropping it yourself, which saves a
    write.
    """
    if on_error not in ON_ERROR:
        raise ValueError(f"on_error must be one of {ON_ERROR}")
    start = time.perf_counter()
    inserter = TypedInserter(table, schema=schema)
    if status is None:
        status = ImportStatus()

    async def report():
        outcome = progress(inserter.rows_written)
        if inspect.isawaitable(outcome):
            await outcome

    # The thread reading a chunk from a regular iterable, if any
    reading = None
    if isinstance(rows, AsyncIterable):
        async_iterator = rows.__aiter__()
        is_async = True

        async def pull(size):
            chunk = []
            while len(chunk) < size:
                try:
                    chunk.append(await async_iterator.__anext__())
                except StopAsyncIteration:
                    break
            return chunk

    elif isinstance(rows, Iterable):
        iterator = iter(rows)
        loop = asyncio.get_running_loop()
        is_async = False

        async def pull(size):
            nonlocal reading
            reading = loop.run_in_executor(None, _take, iterator, size)
            # Cancelling the pull can't stop the thread: keep its future
            return await asyncio.shield(reading)

    else:
        raise TypeError("rows must be an iterable or an async iterable")

    # A write whose outcome is not known yet: (task, snapshot before it)
    in_flight = None

    def update_status():
        status.created = inserter.created
        status.rows = inserter.rows_written
        status.backlog = inserter.backlog

    async def write(fn):
        nonlocal in_flight
        snapshot = inserter.snapshot()
        # Its own task, so that cancelling this call doesn't lose the result
        # of a write that the write thread will run anyway
        task = asyncio.ensure_future(db.execute_write_fn(fn))
        in_flight = (task, snapshot)
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                in_flight = None
            raise
        except BaseException:
            in_flight = None
            # The transaction may have been rolled back after fn returned,
            # by a write_wrapper or a failed COMMIT
            inserter.restore(snapshot)
            raise
        in_flight = None
        update_status()
        return result

    async def settle():
        "Wait for a write of this import that is still running, if any."
        nonlocal in_flight
        if in_flight is not None:
            task, snapshot = in_flight
            in_flight = None
            try:
                await task
            except BaseException:  # noqa: BLE001 - its outcome, not ours
                # That write was rolled back
                inserter.restore(snapshot)
        update_status()

    def guarded(method):
        # The caller may drop the table once this call has raised, and
        # something else may create one by that name: only convert the
        # table this import wrote
        def fn(conn):
            if not inserter._table_is_ours(conn):
                inserter.discard(conn)
                return False
            method(conn)
            return True

        return fn

    async def complete():
        "Convert the rest of the backlog, a slice per write, and finish."
        while inserter.backlog:
            if not await db.execute_write_fn(guarded(inserter.convert)):
                return False
            status.backlog = inserter.backlog
        return await db.execute_write_fn(guarded(inserter.finish))

    async def complete_in_background(caller):
        try:
            if caller is not None and not caller.done():
                # Let the cancelled task finish its own clean up first: it
                # may drop the table (which leaves nothing to convert), and
                # its writes would otherwise queue behind slices. Cancelling
                # a worker import at 1.9M rows took 200-250ms (keep) and
                # 410-430ms (drop) without this, 125-135ms and 235-250ms
                # with it. A task can carry on after a cancellation, so not
                # for long
                await asyncio.wait([caller], timeout=_HANDOFF_SECONDS)
            if not await complete():
                # Most likely the caller dropped it, as it may
                logger.debug(
                    "Table %r was dropped or changed while it was being "
                    "finished after a cancelled import; left as it was",
                    table,
                )
        except Exception:  # noqa: BLE001 - logged, nobody to raise it to
            # A database closed meanwhile (Datasette shutting down after
            # cancelling its imports) is no surprise
            closed = getattr(db, "_closed", False) is True
            (logger.debug if closed else logger.warning)(
                "Could not finish table %r after a cancelled import",
                table,
                exc_info=not closed,
            )
            with contextlib.suppress(Exception):
                await db.execute_write_fn(inserter.discard)
        finally:
            update_status()
            status._set_done()

    async def clean_up(cancelled, caller, settled):
        """
        Returns once the caller can be told: after a cancellation that
        leaves stored numbers to convert, the rest carries on in the
        background (its task is returned) - a cancelled call must not wait
        seconds for that, nor keep a worker that feeds it alive meanwhile.
        ``settled`` is set once created, dropped and rows are final: before
        that conversion, or finishing the table.
        """

        def mark_settled():
            if not settled.done():
                settled.set_result(None)

        handed_off = None
        try:
            await settle()
            # A failing clean up (a full disk, a closed database) must not
            # hide the original error
            with contextlib.suppress(Exception):
                if on_error == "drop" and inserter.created:
                    await db.execute_write_fn(lambda conn: _drop_table(conn, inserter))
                    status.dropped = True
            # settle() has recorded the write that was running, if any
            mark_settled()
            with contextlib.suppress(Exception):
                if on_error == "keep" and inserter.created:
                    if cancelled and inserter.backlog:
                        handed_off = asyncio.ensure_future(
                            complete_in_background(caller)
                        )
                        _BACKGROUND_TASKS.add(handed_off)
                        handed_off.add_done_callback(_BACKGROUND_TASKS.discard)
                        return handed_off
                    await complete()
            if not inserter.finished and inserter._spellings_created:
                with contextlib.suppress(Exception):
                    await db.execute_write_fn(inserter.discard)
        finally:
            mark_settled()
            if handed_off is None:
                update_status()
                status._set_done()

    batches = 0
    pending = asyncio.ensure_future(pull(sample_size))
    try:
        while True:
            chunk = await pending
            if not chunk:
                break
            pending = asyncio.ensure_future(pull(batch_size))
            names = None if columns is None else list(columns)
            await write(
                lambda conn, chunk=chunk, names=names: inserter.write(
                    conn, chunk, names
                )
            )
            batches += 1
            if progress is not None:
                await report()
        status.finishing = True
        while inserter.backlog:
            await write(inserter.convert)
            if progress is not None:
                await report()
        await write(inserter.finish)
    except BaseException as ex:
        status.error = ex
        if is_async and not pending.done():
            # No more rows are wanted, and an async source (a worker) may
            # take a while to send them
            pending.cancel()
        settled = asyncio.get_running_loop().create_future()
        status._settling = settled
        cleaning = asyncio.ensure_future(
            clean_up(
                isinstance(ex, asyncio.CancelledError),
                asyncio.current_task(),
                settled,
            )
        )
        try:
            await asyncio.shield(cleaning)
        except asyncio.CancelledError:
            # Cancelled again: the clean up carries on in the background
            _BACKGROUND_TASKS.add(cleaning)
            cleaning.add_done_callback(_BACKGROUND_TASKS.discard)
        raise
    finally:
        # Never leave a thread reading from the iterator behind
        if not pending.done():
            await asyncio.wait([pending])
        if not pending.cancelled():
            pending.exception()
        if reading is not None:
            if not reading.done():
                await asyncio.wait([reading])
            if not reading.cancelled():
                reading.exception()
    status._set_done()
    return _result(inserter, start, batches)
