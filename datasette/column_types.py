from enum import Enum


class SQLiteType(Enum):
    TEXT = "TEXT"
    INTEGER = "INTEGER"
    REAL = "REAL"
    BLOB = "BLOB"
    NULL = "NULL"


def sqlite_type_from_declared_type(declared_type: str | None) -> SQLiteType | None:
    if declared_type is None:
        return SQLiteType.NULL

    normalized = declared_type.strip().upper()
    if not normalized:
        return SQLiteType.NULL

    if normalized == SQLiteType.NULL.value:
        return SQLiteType.NULL
    if "INT" in normalized:
        return SQLiteType.INTEGER
    if any(token in normalized for token in ("CHAR", "CLOB", "TEXT")):
        return SQLiteType.TEXT
    if "BLOB" in normalized:
        return SQLiteType.BLOB
    if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
        return SQLiteType.REAL

    return None


class ColumnType:
    """
    Base class for column types.

    Subclasses must define ``name`` and ``description`` as class attributes:

    - ``name``: Unique identifier string. Lowercase, no spaces.
      Examples: "markdown", "file", "email", "url", "point", "image".
    - ``description``: Human-readable label for admin UI dropdowns.
      Examples: "Markdown text", "File reference", "Email address".
    - ``sqlite_types``: Optional tuple of SQLiteType values restricting
      which SQLite column types this ColumnType can be assigned to.

    Instantiate with an optional ``config`` dict to bind per-column
    configuration::

        ct = MyColumnType(config={"key": "value"})
        ct.config  # {"key": "value"}
    """

    name: str
    description: str
    sqlite_types: tuple[SQLiteType, ...] | None = None

    def __init__(self, config=None):
        self.config = config

    async def render_cell(self, value, column, table, database, datasette, request):
        """
        Return an HTML string to render this cell value, or None to
        fall through to the default render_cell plugin hook chain.
        """
        return None

    async def validate(self, value, datasette):
        """
        Validate a value before it is written. Return None if valid,
        or a string error message if invalid.
        """
        return None

    async def transform_value(self, value, datasette):
        """
        Transform a value before it appears in JSON API output.
        Return the transformed value. Default: return unchanged.
        """
        return value
