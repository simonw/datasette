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
  what ``sqlite-utils insert --csv`` (which detects types) produces. If a
  numeric column later widens to text, those NULLs become ``""`` again -
  unless the column also holds real NULLs, which cannot be told apart.
- An integer string must be canonical: ``"007"`` is text, so zero-padded
  codes keep their zeros even if the column only turns out to be text much
  later. Surrounding whitespace and a leading ``+`` are accepted (SQLite's
  affinity accepts them too).
- A float string is anything SQLite's REAL affinity converts, except for
  superfluous leading zeros: ``"1.50"``, ``"1e3"`` and ``".5"`` are floats.
- ``nan``, ``inf``, ``1_000`` and non-ASCII digits are text, because SQLite
  stores them as text.
- A Python ``int`` outside the signed 64-bit range is stored as text.
- ``list``, ``dict`` and ``tuple`` values are stored as JSON text.
- A column with no values at all stays ``integer`` (all NULL), as
  ``sqlite-utils`` does: the narrowest type, so later rows can still widen
  it. A table imported from zero rows gets ``text`` columns.
"""

import asyncio
import dataclasses
import inspect
import json
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
_WS = "[ \\t\\n\\v\\f\\r]*"
# Fast paths: the shapes nearly every value takes. The 18 digit limit keeps
# the fast integer path inside the signed 64-bit range.
_INT_FAST = re.compile(r"-?[1-9][0-9]{0,17}|0")
_FLOAT_FAST = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
# The full rules, see classify()
_INT_RE = re.compile(_WS + r"[-+]?(?:0|[1-9][0-9]*)" + _WS)
_FLOAT_RE = re.compile(
    _WS + r"[-+]?(?:(?:0|[1-9][0-9]*)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?" + _WS
)
_INT_MIN = -(2**63)
_INT_MAX = 2**63 - 1


def classify(value):
    """
    Return the narrowest column type that holds ``value`` without changing
    it: ``"integer"``, ``"float"``, ``"text"`` or ``"blob"`` - or ``None``
    for ``None`` and ``""``, which carry no type information.
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
            return _I if _INT_MIN <= int(value) <= _INT_MAX else _T
        if _FLOAT_RE.fullmatch(value):
            return _F
        return _T
    if value is None:
        return _NONE
    if kind is int or kind is bool:
        return _I if _INT_MIN <= value <= _INT_MAX else _T
    if kind is float:
        return _F
    if kind in (bytes, bytearray, memoryview):
        return _B
    if isinstance(value, int):
        return _I if _INT_MIN <= value <= _INT_MAX else _T
    if isinstance(value, float):
        return _F
    return _T


_BYTES_TYPES = {bytes, bytearray, memoryview}
_NUMBER_TYPES = {int, float, bool, _NoneType}


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
    # Logical type rank, _NONE until a value has been seen
    rank: int = _NONE
    # Rank declared in the table. Differs from rank only while a "deferred"
    # widening is pending, or before the column exists in the table (_NONE)
    declared: int = _NONE
    # The type came from an explicit schema - never widened
    fixed: bool = False
    # A real NULL (None, or a missing key) has been stored. While False every
    # NULL in a numeric column was originally "", so widening to text can
    # restore those "" values.
    nullable: bool = False

    @property
    def type(self):
        return _KINDS[max(self.rank, _I)]


class TypedInserter:
    """
    Incrementally create and fill one typed rowid table.

    Call :meth:`write` with chunks of rows: the first chunk is the sample
    used to detect column types and create the table. Call :meth:`finish`
    once at the end. Neither method commits - run each call in its own
    transaction (``db.execute_write_fn()`` does that for you). If a call
    raises, discard the inserter.

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
        - ``"deferred"``: leave declared types alone while importing (SQLite
          keeps most odd values as text or real) and rebuild once in
          :meth:`finish`. Cheapest, but lossy for values that the stale
          affinity converts: ``"007"`` arriving in an integer column is
          stored as 7, a 20 digit integer string as a rounded float.

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
        self.widenings = []
        self.created = False
        self.finished = False
        self._columns = []
        self._by_key = {}
        self._used_names = set()
        self._keys = ()
        self._known_sql = None
        self._insert_sql = None
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
        values = self._row_values(rows)
        changes = self._scan(values)
        if not self.created:
            self._create(conn)
        else:
            if any(column.declared == _NONE for column in self._columns):
                self._add_columns(conn)
            if changes:
                self._widen(conn, changes)
        conn.executemany(self._insert_sql, values)
        self.rows_written += len(values)
        return len(values)

    def finish(self, conn):
        """
        Complete the import: create the table if no rows arrived (only
        possible if a schema named the columns - otherwise no table is
        created) and apply widening that the ``"deferred"`` strategy
        postponed. Returns self.
        Safe to call more than once. Does not commit.
        """
        if self.finished:
            return self
        if not self.created and not self._columns:
            # Zero rows and no schema: there is nothing to create a table from
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
        name = name.replace("\x00", "")
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
        column = _Column(key=key, name=name)
        self._columns.append(column)
        self._by_key[key] = column
        self._keys = tuple(c.key for c in self._columns)
        return column

    def _row_values(self, rows):
        "Turn a list of dicts into lists of values, adding new columns."
        keys = self._keys
        by_key = self._by_key
        new_columns = []
        for row in rows:
            if tuple(row) != keys:
                for key in row:
                    if key not in by_key:
                        new_columns.append(self._new_column(key))
                keys = self._keys
        for column in new_columns:
            # NULL in earlier rows, or in rows of this chunk that lack it
            if self.rows_written or any(column.key not in row for row in rows):
                column.nullable = True
        return [list(map(row.get, keys)) for row in rows]

    # Types --------------------------------------------------------------

    def _scan(self, values):
        """
        Check each column against this chunk, convert values that need it
        (in place) and return ``{key: rank}`` for columns that must widen.
        Before the table exists this sets the detected types instead.
        """
        changes = {}
        for index, column in enumerate(self._columns):
            current = column.rank
            col_values = [row[index] for row in values]
            types_present = set(map(type, col_values))
            if column.fixed or current == _B:
                new_rank = current
            elif current == _T:
                # Only bytes can widen a text column
                new_rank = _B if types_present & _BYTES_TYPES else _T
            elif types_present == {str}:
                # CSV: check with one regex per value, in C
                fast = _INT_FAST if current <= _I else _FLOAT_FAST
                if all(map(fast.fullmatch, col_values)):
                    new_rank = max(current, _I)
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
                elif min(numbers) < _INT_MIN or max(numbers) > _INT_MAX:
                    new_rank = _widest(col_values, current)
                else:
                    new_rank = max(current, _F if float in types_present else _I)
            else:
                new_rank = _widest(col_values, current)
            if _NoneType in types_present:
                column.nullable = True
            if new_rank > current:
                if column.declared == _NONE or new_rank <= column.declared:
                    # Not in the table yet, or no evidence so far and the
                    # column is already declared INTEGER: nothing to widen
                    column.rank = new_rank
                else:
                    changes[column.key] = new_rank
            self._convert(
                values, index, col_values, types_present, max(new_rank, _I), column
            )
        return changes

    def _convert(self, values, index, col_values, types_present, rank, column):
        "Make the values of one column storable in a column of ``rank``."
        if rank < _T:
            if str in types_present and "" in col_values:
                # "" means NULL in a numeric column
                for row in values:
                    if row[index] == "":
                        row[index] = None
            odd = types_present - {str, int, float, _NoneType}
            if odd or (column.fixed and int in types_present):
                for row in values:
                    value = row[index]
                    if value is not None and (
                        type(value) not in (str, int, float)
                        or (type(value) is int and not _INT_MIN <= value <= _INT_MAX)
                    ):
                        row[index] = _storable(value)
        elif types_present - {str, bytes, _NoneType}:
            for row in values:
                value = row[index]
                if value is not None and type(value) not in (str, bytes):
                    row[index] = _storable(value)

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

    def _text_expression(self, column):
        """
        SQL converting a numeric column's values to text, restoring "" for
        NULLs that were originally empty strings.
        """
        name = _quote(column.name)
        # A float with no fractional part becomes "6", not "6.0": it was most
        # likely an integer in the source (a column that went integer ->
        # float -> text), and this keeps "schema" and "rebuild" identical -
        # SQLite stores such floats as integers in REAL columns anyway
        expression = (
            f"CASE WHEN typeof({name}) = 'real' AND {name} = CAST({name} AS INTEGER) "
            f"THEN CAST(CAST({name} AS INTEGER) AS TEXT) ELSE CAST({name} AS TEXT) END"
        )
        if column.nullable:
            return expression
        return f"COALESCE({expression}, '')"

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
        for key, rank in changes.items():
            if rank >= _T and old_declared[key] < _T:
                column = self._by_key[key]
                name = _quote(column.name)
                conn.execute(
                    "UPDATE {} SET {} = {} WHERE typeof({}) IN ({})".format(
                        _quote(self.table),
                        name,
                        self._text_expression(column),
                        name,
                        (
                            "'integer', 'real'"
                            if column.nullable
                            else "'integer', 'real', 'null'"
                        ),
                    )
                )
        self._refresh(conn)
        return True

    def _rebuild(self, conn, changes):
        "Copy the table into a new one declaring the new types."
        selects = []
        for column in self._columns:
            name = _quote(column.name)
            if (
                column.key in changes
                and max(column.rank, _I) >= _T
                and column.declared < _T
            ):
                selects.append(self._text_expression(column))
            else:
                selects.append(name)
            column.declared = max(column.rank, _I)
        temp = f"{self.table}_widen_{uuid.uuid4().hex[:8]}"
        conn.execute(self._create_sql(temp))
        conn.execute(
            "INSERT INTO {} (rowid, {}) SELECT rowid, {} FROM {}".format(
                _quote(temp),
                ", ".join(_quote(c.name) for c in self._columns),
                ", ".join(selects),
                _quote(self.table),
            )
        )
        conn.execute(f"DROP TABLE {_quote(self.table)}")
        conn.execute(f"ALTER TABLE {_quote(temp)} RENAME TO {_quote(self.table)}")
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
    for chunk in iter_chunks(rows, sample_size, batch_size):
        with _Transaction(conn):
            inserter.write(conn, chunk)
        batches += 1
        if progress is not None:
            progress(inserter.rows_written)
    with _Transaction(conn):
        inserter.finish(conn)
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
            await db.execute_write_fn(
                lambda conn, chunk=chunk: inserter.write(conn, chunk)
            )
            batches += 1
            if progress is not None:
                outcome = progress(inserter.rows_written)
                if inspect.isawaitable(outcome):
                    await outcome
        await db.execute_write_fn(inserter.finish)
    finally:
        # Never leave a thread reading from the iterator behind
        if not pending.done():
            await asyncio.wait([pending])
        if not pending.cancelled():
            pending.exception()
    return _result(inserter, start, batches)
