"""
The objects Datasette uses to represent the results of a query.

A :class:`Row` is one row of results. Every row belonging to the same set
of results shares a single :class:`Columns` object, which describes each
:class:`Column` - its name and, where the database can tell us, its
:class:`DatabaseType`, the table it came from and the foreign key it holds.

Nothing in this module knows about SQLite apart from
:meth:`DatabaseType.from_sqlite`. A database backend produces a tuple of
values for each row plus a description of the columns, and Datasette builds
these objects from that.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, overload

_NO_LABELS: Mapping[str, Any] = MappingProxyType({})


class TypeCategory(str, Enum):
    """
    A portable description of the kind of data a column is declared to hold.

    These are the same across database backends, so code can ask "is this a
    datetime column?" without knowing which database the column lives in.
    """

    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    BINARY = "binary"
    JSON = "json"
    DATE = "date"
    TIME = "time"
    DATETIME = "datetime"
    UUID = "uuid"
    ARRAY = "array"
    OTHER = "other"

    def __str__(self) -> str:
        return self.value


# SQLite has none of these types. It accepts any name as a declared type and
# only stores integers, floats, text and blobs. These are the names that are
# conventionally used to say what a column is meant to hold, so they are
# recognized here. The name is the declared type with any (length) removed,
# in upper case. Anything else falls back to SQLite's type affinity rules.
_SQLITE_NAMED_CATEGORIES: dict[str, TypeCategory] = {
    "BOOL": TypeCategory.BOOLEAN,
    "BOOLEAN": TypeCategory.BOOLEAN,
    "DATE": TypeCategory.DATE,
    "TIME": TypeCategory.TIME,
    "DATETIME": TypeCategory.DATETIME,
    "TIMESTAMP": TypeCategory.DATETIME,
    "JSON": TypeCategory.JSON,
    # SQLite does not store exact decimals: these hold integers or floats
    "DECIMAL": TypeCategory.DECIMAL,
    "NUMERIC": TypeCategory.DECIMAL,
}


@dataclass(frozen=True, slots=True)
class DatabaseType:
    """
    The type a database declares for a column.

    ``name`` is the type exactly as the database describes it, for example
    ``varchar(30)`` or ``timestamptz``. ``category`` is the portable
    :class:`TypeCategory` it belongs to.

    This describes the column, not the value. SQLite in particular will
    store a string in a column declared as ``INTEGER``.
    """

    name: str
    category: TypeCategory

    @classmethod
    def from_sqlite(cls, declared_type: str | None) -> DatabaseType | None:
        """
        Returns the type for a SQLite column declared as ``declared_type``, or
        ``None`` if the column was declared without a type.
        """
        if declared_type is None:
            return None
        name = declared_type.strip()
        if not name:
            return None
        normalized = name.upper()
        base = normalized.split("(")[0].strip()
        category = _SQLITE_NAMED_CATEGORIES.get(base)
        if category is None:
            # Otherwise follow SQLite's own type affinity rules:
            # https://www.sqlite.org/datatype3.html#determination_of_column_affinity
            if "INT" in normalized:
                category = TypeCategory.INTEGER
            elif any(token in normalized for token in ("CHAR", "CLOB", "TEXT")):
                category = TypeCategory.TEXT
            elif "BLOB" in normalized:
                category = TypeCategory.BINARY
            elif any(
                token in normalized
                for token in ("REAL", "FLOA", "DOUB")  # codespell:ignore doub
            ):
                category = TypeCategory.FLOAT
            else:
                category = TypeCategory.OTHER
        return cls(name=name, category=category)


@dataclass(frozen=True, slots=True)
class ForeignKey:
    """
    The table and column that a foreign key column refers to.

    ``column`` is ``None`` if the foreign key was declared without naming a
    column, in which case it refers to the primary key of the other table.
    """

    table: str
    column: str | None = None


@dataclass(frozen=True, slots=True)
class Column:
    """
    A column in a set of results.

    Only ``name`` is always available. The other attributes are ``None``
    when the database cannot tell Datasette about them, which is the case
    for the columns of an arbitrary SQL query against SQLite.
    """

    name: str
    type: DatabaseType | None = None
    table: str | None = None
    foreign_key: ForeignKey | None = None


class Columns:
    """
    The columns for a set of results, shared by every :class:`Row` in it.

    This is an immutable sequence of :class:`Column` objects. A column can
    be looked up by position or by name::

        columns[0]
        columns["id"]

    ``columns.names`` is a tuple of the column names.
    """

    __slots__ = ("_column_objects", "_index", "names")

    names: tuple[str, ...]
    _column_objects: tuple[Column, ...] | None
    _index: dict[str, int]

    def __init__(self, columns: Iterable[str | Column] = ()) -> None:
        items = tuple(columns)
        if all(item.__class__ is str for item in items):
            # Most queries never look at their Column objects, so those are
            # only created if something asks for them
            self.names = items  # type: ignore[assignment]
            self._column_objects = None
        else:
            self._column_objects = tuple(
                item if isinstance(item, Column) else Column(item) for item in items
            )
            self.names = tuple(column.name for column in self._column_objects)
        index: dict[str, int] = {}
        for position, name in enumerate(self.names):
            # If a name is repeated the first column with that name wins
            index.setdefault(name, position)
        self._index = index

    @property
    def _columns(self) -> tuple[Column, ...]:
        if self._column_objects is None:
            self._column_objects = tuple(Column(name) for name in self.names)
        return self._column_objects

    @overload
    def __getitem__(self, key: int | str) -> Column: ...
    @overload
    def __getitem__(self, key: slice) -> tuple[Column, ...]: ...
    def __getitem__(self, key: int | str | slice) -> Column | tuple[Column, ...]:
        if isinstance(key, str):
            return self._columns[self._index[key]]
        return self._columns[key]

    def __iter__(self) -> Iterator[Column]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self.names)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, str):
            return item in self._index
        return item in self._columns

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Columns):
            return self._columns == other._columns
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._columns)

    def __repr__(self) -> str:
        return f"<Columns: {', '.join(self.names)}>"

    def __reduce__(self) -> tuple[type[Columns], tuple[tuple[Column, ...]]]:
        return (Columns, (self._columns,))


class Row:
    """
    A row returned by a database query.

    Values can be read by column name or by position, and iterating over a
    row yields its values::

        row["name"]
        row[0]
        id, name = row

    ``row.columns`` is the :class:`Columns` object describing the columns.
    ``row.labels`` is a read-only dictionary of labels for any foreign key
    values in this row that Datasette has resolved, keyed by column name.

    Rows are immutable.
    """

    __slots__ = ("_columns", "_labels", "_values")

    _values: tuple[Any, ...]
    _columns: Columns
    _labels: Mapping[str, Any] | None

    def __init__(
        self,
        values: Iterable[Any],
        columns: Columns,
        labels: Mapping[str, Any] | None = None,
    ) -> None:
        if values.__class__ is not tuple:
            values = tuple(values)
        self._values = values
        self._columns = columns
        self._labels = MappingProxyType(dict(labels)) if labels else None

    @classmethod
    def from_dict(
        cls, d: Mapping[str, Any], labels: Mapping[str, Any] | None = None
    ) -> Row:
        "Build a row from a dictionary of ``{column_name: value}``"
        return cls(tuple(d.values()), Columns(d.keys()), labels)

    @property
    def columns(self) -> Columns:
        return self._columns

    @property
    def labels(self) -> Mapping[str, Any]:
        if self._labels is None:
            return _NO_LABELS
        return self._labels

    @overload
    def __getitem__(self, key: int | str) -> Any: ...
    @overload
    def __getitem__(self, key: slice) -> tuple[Any, ...]: ...
    def __getitem__(self, key: int | str | slice) -> Any:
        if key.__class__ is str:
            return self._values[self._columns._index[key]]
        try:
            return self._values[key]  # type: ignore[index]
        except TypeError:
            if isinstance(key, str):
                return self._values[self._columns._index[key]]
            raise

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, value: object) -> bool:
        return value in self._values

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Row):
            return (
                self._values == other._values
                and self._columns.names == other._columns.names
            )
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self._columns.names, self._values))

    def __repr__(self) -> str:
        bits = ", ".join(
            f"{name}={value!r}"
            for name, value in zip(self._columns.names, self._values)
        )
        return f"<Row: {bits}>"

    def __reduce__(
        self,
    ) -> tuple[type[Row], tuple[tuple[Any, ...], Columns, dict[str, Any] | None]]:
        labels = dict(self._labels) if self._labels else None
        return (Row, (self._values, self._columns, labels))

    def keys(self) -> list[str]:
        "A list of the column names, so that ``dict(row)`` works"
        return list(self._columns.names)


def rows_with_columns(rows: Iterable[Row], columns: Columns) -> list[Row]:
    "Returns copies of ``rows`` that all use the ``columns`` object"
    new_rows = []
    for row in rows:
        if len(row._values) != len(columns):
            raise ValueError(
                f"Row has {len(row._values)} values but there are "
                f"{len(columns)} columns"
            )
        new_row = Row.__new__(Row)
        new_row._values = row._values
        new_row._columns = columns
        new_row._labels = row._labels
        new_rows.append(new_row)
    return new_rows


def label_rows(
    rows: Iterable[Row], labels: Mapping[str, Mapping[Any, Any]]
) -> list[Row]:
    """
    Returns copies of ``rows`` with foreign key labels attached.

    ``labels`` is ``{column_name: {value: label}}``.
    """
    lookups = {column: lookup for column, lookup in labels.items() if lookup}
    if not lookups:
        return list(rows)
    labeled = []
    for row in rows:
        row_labels = {}
        for column, lookup in lookups.items():
            try:
                value = row[column]
            except KeyError:
                continue
            if value is None:
                continue
            try:
                row_labels[column] = lookup[value]
            except (KeyError, TypeError):
                # No label for this value, or the value is not hashable
                continue
        labeled.append(Row(row._values, row._columns, {**row.labels, **row_labels}))
    return labeled
