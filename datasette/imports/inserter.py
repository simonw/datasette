"""
Turn a stream of rows (dictionaries) into a typed SQLite table.

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
   per chunk, :func:`insert_rows` commits after every chunk.

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
import json
import math
import re
import sqlite3
import time
import uuid
from collections.abc import AsyncIterable, Iterable

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

WIDEN_STRATEGIES = ("auto", "schema", "rebuild", "deferred")

# SQLite skips these around numbers when applying affinity (sqlite3Isspace)
_WS_CHARS = " \t\n\v\f\r"
_WS = "[ \\t\\n\\v\\f\\r]*"
# Canonical values: an INTEGER (or REAL) column stores them and gives them
# back as exactly the same text, so nothing has to be remembered. They are
# the shapes nearly every value takes, which makes them the fast path. Both
# also match "". Integers are limited to 15 digits so that they stay exact
# if the column becomes REAL; decimals to 15 significant digits and the
# range where repr() does not switch to an exponent.
_INT_FAST = re.compile(r"(?:0|-?[1-9][0-9]{0,14})?")
_REAL_FAST = re.compile(
    r"(?:0|-?(?:[1-9][0-9]{0,14}"
    r"|(?=[0-9.]{3,16}\Z)[1-9][0-9]*\.[0-9]*[1-9]"
    r"|0\.0{0,3}(?=[0-9]{1,15}\Z)[1-9](?:[0-9]*[1-9])?))?"
)
# The classification rules, see classify()
_INT_RE = re.compile(_WS + r"[-+]?(?:0|[1-9][0-9]*)" + _WS)
_FLOAT_RE = re.compile(
    _WS + r"[-+]?(?:(?:0|[1-9][0-9]*)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?" + _WS
)
# Text that SQLite's INTEGER and REAL affinity turn into a number
_SQLITE_INT = re.compile(_WS + r"[-+]?[0-9]+" + _WS)
_SQLITE_NUMBER = re.compile(
    _WS + r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?" + _WS
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


def _affinity_text(text, declared):
    """
    The text that ``text`` comes back as after a column with ``declared``
    (INTEGER or REAL) affinity stored it and then widened to text. Only
    called for text that SQLite's affinity turns into a number.
    """
    if (
        declared == _I
        and _SQLITE_INT.fullmatch(text)
        and len(text.strip(_WS_CHARS).lstrip("+-").lstrip("0")) <= 19
    ):
        number = int(text)
        if _INT_MIN <= number <= _INT_MAX:
            return str(number)
    # A REAL - INTEGER affinity also keeps one if it is not a whole number
    return _render_real(float(text))


_BYTES_TYPES = {bytes, bytearray, memoryview}
_NUMBER_TYPES = {int, float, bool, _NoneType}
_TEXT_TYPES = {str, bytes, _NoneType}
_STR = {str}
# _null_kind() result meaning "no spelling needs to be kept"
_NO_ENTRY = object()


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
    # "schema", "rebuild", "rebuild (fallback)" or "deferred"
    method: str
    # Time spent changing the table (shared by columns widened together)
    seconds: float = 0.0


@dataclasses.dataclass
class _Column:
    key: object
    name: str
    # Position in the table, also the column's id in the spellings table
    index: int
    # Logical type rank, _NONE until a value has been seen
    rank: int = _NONE
    # Rank declared in the table. Differs from rank only while a "deferred"
    # widening is pending, or before the column exists in the table (_NONE)
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
    :param widen: how to widen a column:

        - ``"auto"`` (default): integer -> float (and text -> blob) edits
          the declared type in ``sqlite_schema`` in place - no stored value
          has to change, so this takes well under a millisecond at any table
          size. Widening to text rebuilds the table, because the stored
          numbers have to become text and copying the table measured faster
          than an in-place ``UPDATE``.
        - ``"schema"``: always edit in place, with an ``UPDATE`` converting
          stored numbers when widening to text.
        - ``"rebuild"``: always copy the table into a new one.
        - ``"deferred"``: leave declared types alone while importing and
          rebuild once in :meth:`finish`. Readers see stale types until
          then, and every value that the stale affinity would change
          (``"007"`` in an INTEGER column) has its text kept until then.

        In-place edits fall back to a rebuild if the connection refuses
        ``PRAGMA writable_schema`` or the table has been changed by someone
        else (indexes, triggers, an edited definition).
    """

    def __init__(self, table, *, schema=None, widen="auto"):
        if widen not in WIDEN_STRATEGIES:
            raise ValueError(f"widen= must be one of {WIDEN_STRATEGIES}")
        self.table = table
        self.widen = widen
        self.rows_written = 0
        # Values whose original text had to be kept, see the module docstring
        self.spellings_kept = 0
        self.widenings = []
        self.created = False
        self.finished = False
        self._columns = []
        self._by_key = {}
        self._used_names = set()
        self._keys = ()
        self._known_sql = None
        self._insert_sql = None
        self._spellings_table = "temp." + _quote(
            f"_import_spellings_{uuid.uuid4().hex[:12]}"
        )
        self._spellings_created = False
        # Rows with no keys at all that arrived before any column was known
        self._pending_empty = 0
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

    # Writing ------------------------------------------------------------

    def write(self, conn, rows):
        """
        Write one chunk of rows (dictionaries). The first non-empty chunk
        is the sample: types are detected from it and the table is created.
        Returns the number of rows written. Does not commit.
        """
        if self.finished:
            raise ValueError("finish() has already been called")
        if not isinstance(rows, list):
            rows = list(rows)
        if not rows:
            return 0
        snapshot = self.snapshot()
        try:
            return self._write(conn, rows)
        except BaseException:
            # The caller rolls the transaction back; match it
            self.restore(snapshot)
            raise

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
            self._insert_sql,
            self.rows_written,
            self.spellings_kept,
            len(self.widenings),
            self.created,
            self.finished,
            self._spellings_created,
            self._pending_empty,
        )

    def restore(self, snapshot):
        "Return to a state captured by :meth:`snapshot`."
        (
            columns,
            used_names,
            self._known_sql,
            self._insert_sql,
            self.rows_written,
            self.spellings_kept,
            widening_count,
            self.created,
            self.finished,
            self._spellings_created,
            self._pending_empty,
        ) = snapshot
        self._columns = [dataclasses.replace(column) for column in columns]
        self._by_key = {column.key: column for column in self._columns}
        self._keys = tuple(column.key for column in self._columns)
        self._used_names = set(used_names)
        del self.widenings[widening_count:]

    def _write(self, conn, rows):
        if not self._columns and not any(rows):
            # Only empty dictionaries so far: a table needs at least one
            # column, so hold on to them until a key turns up
            self._pending_empty += len(rows)
            return 0
        if self._pending_empty:
            rows = [{}] * self._pending_empty + rows
            self._pending_empty = 0
        values = self._row_values(rows)
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
        for plan in plans:
            spellings.extend(self._convert(values, *plan))
        table = _quote(self.table)
        base = conn.execute(f"select max(rowid) from {table}").fetchone()[0] or 0
        try:
            conn.executemany(self._insert_sql, values)
        except UnicodeEncodeError:
            # A lone surrogate: remove what got in, replace them and retry
            conn.execute(f"delete from {table} where rowid > ?", [base])
            values = [list(map(_without_surrogates, row)) for row in values]
            conn.executemany(self._insert_sql, values)
        if spellings:
            self._keep_spellings(conn, spellings, base, len(values))
        self.rows_written += len(values)
        return len(values)

    def finish(self, conn):
        """
        Complete the import: create the table if no rows arrived (only
        possible if a schema named the columns - otherwise no table is
        created), apply widening that the ``"deferred"`` strategy postponed
        and drop the kept spellings. Returns self.
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
        pending = {
            column.key: column.rank
            for column in self._columns
            if max(column.rank, _I) > column.declared
        }
        if pending:
            start = time.perf_counter()
            self._rebuild(conn, pending)
            elapsed = time.perf_counter() - start
            for widening in self.widenings:
                widening.seconds = elapsed
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

    # Types --------------------------------------------------------------

    def _scan(self, values):
        """
        Check each column against this chunk. Returns ``{key: rank}`` for
        columns that must widen (before the table exists this sets the
        detected types instead) and the arguments for :meth:`_convert`.
        """
        changes = {}
        plans = []
        deferring = self.widen == "deferred" and self.created
        for index, column in enumerate(self._columns):
            current = column.rank
            col_values = [row[index] for row in values]
            types_present = set(map(type, col_values))
            # Every value is a canonical str, or every value an exact number
            fast = False
            if column.fixed or current == _B:
                new_rank = current
            elif current == _T:
                # Only bytes can widen a text column
                new_rank = _B if types_present & _BYTES_TYPES else _T
            elif types_present == _STR:
                # CSV: check with one regex per value, in C
                pattern = _INT_FAST if current <= _I else _REAL_FAST
                if all(map(pattern.fullmatch, col_values)):
                    fast = True
                    new_rank = max(current, _I) if any(col_values) else current
                else:
                    new_rank = _widest(col_values, current)
            elif types_present <= _NUMBER_TYPES:
                # JSON numbers: no per-value Python work
                numbers = (
                    [v for v in col_values if v is not None]
                    if _NoneType in types_present
                    else col_values
                )
                if not numbers:
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
            logical = max(new_rank, _I)
            if deferring and column.declared != _NONE:
                declared = column.declared
            else:
                declared = logical
            plans.append((index, col_values, types_present, logical, declared, fast))
        return changes, plans

    def _convert(
        self, values, index, col_values, types_present, logical, declared, fast
    ):
        """
        Make the values of one column storable (in place) in a column that
        is ``logical`` and declared ``declared``. Returns a list of
        ``(column, row, text)`` for values whose original text has to be
        kept because the stored value will not reproduce it.
        """
        column = self._columns[index]
        if declared >= _T:
            # TEXT or BLOB: values in their text form, bytes as they are
            if types_present - _TEXT_TYPES:
                for row in values:
                    value = row[index]
                    if value is not None and type(value) not in (str, bytes):
                        row[index] = _text_form(value)
            return ()
        if column.fixed:
            self._convert_fixed(values, index, col_values, types_present)
            return ()
        if logical >= _T:
            return self._convert_stale(values, index, column, declared)
        if not fast:
            return self._convert_numeric(values, index, column, logical, declared)
        # The fast paths: canonical strings, or numbers within +-2**53
        has_blank = str in types_present and "" in col_values
        spellings = self._nulls(
            values, index, column, has_blank, _NoneType in types_present
        )
        if logical == _F and str in types_present:
            # Bind floats, so what is stored is exactly float(text)
            for row in values:
                value = row[index]
                if value is not None:
                    row[index] = float(value)
        return spellings

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

    def _convert_numeric(self, values, index, column, logical, declared):
        "The slow path for an INTEGER or REAL column: value by value."
        spellings = []
        for i, row in enumerate(values):
            value = row[index]
            kind = type(value)
            if value is None or value == "" or (kind is float and math.isnan(value)):
                row[index] = None
                entry = self._null_kind(column, kind is str)
                if entry is not _NO_ENTRY:
                    spellings.append((column.index, i, entry))
                continue
            if kind is str:
                text = value
                if declared == _I and _classify_rank(value) == _I:
                    # SQLite parses integers exactly: bind the text
                    number = int(value)
                    stored = str(number)
                    if logical == _F:
                        row[index] = number
                else:
                    number = float(value)
                    row[index] = number
                    stored = _render_real(number)
            elif kind is float or isinstance(value, float):
                # Its text form is defined by the stored value
                row[index] = float(value)
                continue
            elif isinstance(value, int):
                number = int(value)
                row[index] = number
                text = str(number)
                stored = text if declared == _I else _render_real(float(number))
            else:
                # Cannot happen in a numeric column, but stay storable
                row[index] = _storable(value)
                continue
            if declared == _I and type(number) is int and abs(number) > _EXACT:
                column.big_ints = True
            if stored != text:
                spellings.append((column.index, i, text))
        return spellings

    def _convert_stale(self, values, index, column, declared):
        """
        ``"deferred"``: a column that is text by now but still declared
        INTEGER or REAL. Store the text forms, keeping those that the
        stale affinity turns into a number that does not reproduce them.
        """
        spellings = []
        for i, row in enumerate(values):
            value = row[index]
            if value is None or (type(value) is float and math.isnan(value)):
                row[index] = None
                entry = self._null_kind(column, False)
                if entry is not _NO_ENTRY:
                    spellings.append((column.index, i, entry))
                continue
            if type(value) is not str:
                value = row[index] = _text_form(value)
            if (
                type(value) is str
                and _SQLITE_NUMBER.fullmatch(value)
                and _affinity_text(value, declared) != value
            ):
                spellings.append((column.index, i, value))
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

    def _create_sql(self, table=None):
        return "CREATE TABLE {} ({})".format(
            _quote(table or self.table),
            ", ".join(
                f"{_quote(c.name)} {_SQL_TYPES[max(c.declared, _I)]}"
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
        self._insert_sql = "INSERT INTO {} ({}) VALUES ({})".format(
            _quote(self.table),
            ", ".join(_quote(c.name) for c in self._columns),
            ", ".join("?" for _ in self._columns),
        )

    def _add_columns(self, conn):
        for column in self._columns:
            if column.declared == _NONE:
                column.declared = max(column.rank, _I)
                table, name = _quote(self.table), _quote(column.name)
                sql_type = _SQL_TYPES[column.declared]
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
        self._refresh(conn)

    def _widen(self, conn, changes):
        method = self.widen
        new_widenings = []
        for key, rank in changes.items():
            column = self._by_key[key]
            new_widenings.append(
                Widening(
                    column=column.name,
                    from_type=column.type,
                    to_type=_KINDS[rank],
                    at_row=self.rows_written,
                    method=method,
                )
            )
            column.rank = rank
        self.widenings.extend(new_widenings)
        if method == "deferred":
            # Declared types stay as they are until finish()
            return
        start = time.perf_counter()
        self._keep_big_ints(conn, changes)
        if method == "auto":
            converts = any(
                rank >= _T and self._by_key[key].declared < _T
                for key, rank in changes.items()
            )
            method = "rebuild" if converts else "schema"
            for widening in new_widenings:
                widening.method = method
        if method == "rebuild" or not self._edit_schema(conn, changes):
            if method == "schema":
                for widening in new_widenings:
                    widening.method = "rebuild (fallback)"
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

    def _text_expression(self, column):
        """
        SQL turning a numeric column's stored values back into the text they
        came from: kept spellings first, then NULL (or "" if the NULLs stand
        for blanks), then the stored number as text.
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
        spellings = self._spellings_table
        return (
            f"CASE WHEN rowid IN (SELECT rid FROM {spellings} "
            f"WHERE col = {column.index}) "
            f"THEN (SELECT val FROM {spellings} WHERE col = {column.index} "
            f"AND rid = {_quote(self.table)}.rowid) ELSE {expression} END"
        )

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

    def _edit_schema(self, conn, changes):
        """
        Change declared types in place by editing sqlite_schema, following
        https://www.sqlite.org/lang_altertable.html#otheralter - safe only
        because this inserter created the table, so no index, trigger or
        constraint depends on column affinity. Returns False (having changed
        nothing) if that cannot be guaranteed or the edit is refused.
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
        converting = self._converting(changes)
        old_declared = {c.key: c.declared for c in self._columns}
        for key in changes:
            self._by_key[key].declared = changes[key]
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
        except sqlite3.DatabaseError:
            # For example when SQLITE_DBCONFIG_DEFENSIVE is on
            for key, rank in old_declared.items():
                self._by_key[key].declared = rank
            return False
        # Changing a declared type does not rewrite stored values: an integer
        # stored under REAL affinity reads back as a float, but numbers under
        # TEXT affinity stay numbers until they are converted
        if converting:
            # One UPDATE for all of them: writing a row applies every
            # column's affinity, so updating one column would let SQLite turn
            # the numbers of the next one into text its own way first
            conn.create_function(_RENDER_FUNCTION, 1, _render_sql, deterministic=True)
            sets, conditions = [], []
            for column in converting:
                name = _quote(column.name)
                sets.append(f"{name} = {self._text_expression(column)}")
                conditions.append(f"typeof({name}) IN ('integer', 'real')")
                if column.nulls == "blank" or column.spellings:
                    conditions.append(f"{name} IS NULL")
            conn.execute(
                "UPDATE {} SET {} WHERE {}".format(
                    _quote(self.table), ", ".join(sets), " OR ".join(conditions)
                )
            )
        self._converted(conn, converting)
        self._refresh(conn)
        return True

    def _rebuild(self, conn, changes):
        "Copy the table into a new one declaring the new types."
        converting = self._converting(changes)
        indexes = {column.index for column in converting}
        selects = [
            (
                self._text_expression(column)
                if column.index in indexes
                else _quote(column.name)
            )
            for column in self._columns
        ]
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
    )


def insert_rows(
    conn,
    table,
    rows,
    *,
    schema=None,
    sample_size=1000,
    batch_size=100,
    widen="auto",
    progress=None,
):
    """
    Import ``rows`` into a new ``table`` using a plain
    ``sqlite3.Connection``, committing after every chunk.

    If the connection already has a transaction open (``sqlite3``'s legacy
    transaction control) the caller owns it and nothing is committed.
    ``progress``, if provided, is called with the number of rows written so
    far after each chunk.
    """
    start = time.perf_counter()
    inserter = TypedInserter(table, schema=schema, widen=widen)
    batches = 0
    try:
        for chunk in iter_chunks(rows, sample_size, batch_size):
            with _Transaction(conn):
                inserter.write(conn, chunk)
            batches += 1
            if progress is not None:
                progress(inserter.rows_written)
        with _Transaction(conn):
            inserter.finish(conn)
    except BaseException:
        # Keep the original error
        with contextlib.suppress(Exception), _Transaction(conn):
            inserter.discard(conn)
        raise
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
            self.conn.execute("BEGIN")
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
    widen="auto",
    progress=None,
):
    """
    Import ``rows`` into a new ``table`` in a Datasette
    :class:`~datasette.database.Database`.

    Each chunk is written by its own ``await db.execute_write_fn()`` call, so
    each is a committed transaction: readers see rows arrive, other writes
    can run in between and ``write_wrapper`` plugins see every chunk.

    ``rows`` can be a regular iterable (for example from
    ``sqlite_utils.utils.rows_from_file()``), which is consumed in a thread
    so reading and parsing never blocks the event loop, or an async
    iterable, for example rows parsed from a worker subprocess. The next
    chunk is read while the current one is being written.

    ``progress``, if provided, is called (and awaited, if that returns an
    awaitable) with the number of rows written so far after every chunk.
    """
    start = time.perf_counter()
    inserter = TypedInserter(table, schema=schema, widen=widen)

    if isinstance(rows, AsyncIterable):
        async_iterator = rows.__aiter__()

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

        async def pull(size):
            return await loop.run_in_executor(None, _take, iterator, size)

    else:
        raise TypeError("rows must be an iterable or an async iterable")

    batches = 0
    pending = asyncio.ensure_future(pull(sample_size))
    try:
        while True:
            chunk = await pending
            if not chunk:
                break
            pending = asyncio.ensure_future(pull(batch_size))
            snapshot = inserter.snapshot()
            try:
                await db.execute_write_fn(
                    lambda conn, chunk=chunk: inserter.write(conn, chunk)
                )
            except Exception:
                # The transaction may have been rolled back after write()
                # returned, by a write_wrapper or a failed COMMIT. (Not on
                # cancellation: the write thread may still be running it.)
                inserter.restore(snapshot)
                raise
            batches += 1
            if progress is not None:
                outcome = progress(inserter.rows_written)
                if inspect.isawaitable(outcome):
                    await outcome
        await db.execute_write_fn(inserter.finish)
    except Exception:
        # Don't leave the kept spellings behind on the write connection
        with contextlib.suppress(Exception):
            await db.execute_write_fn(inserter.discard)
        raise
    finally:
        # Never leave a thread reading from the iterator behind
        if not pending.done():
            await asyncio.wait([pending])
        if not pending.cancelled():
            pending.exception()
    return _result(inserter, start, batches)
