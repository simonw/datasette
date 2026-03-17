class ColumnType:
    """
    Base class for column types.

    Subclasses must define ``name`` and ``description`` as class attributes:

    - ``name``: Unique identifier string. Lowercase, no spaces.
      Examples: "markdown", "file", "email", "url", "point", "image".
    - ``description``: Human-readable label for admin UI dropdowns.
      Examples: "Markdown text", "File reference", "Email address".
    """

    name: str
    description: str

    async def render_cell(
        self, value, column, table, database, datasette, request, config
    ):
        """
        Return an HTML string to render this cell value, or None to
        fall through to the default render_cell plugin hook chain.

        ``config`` is the parsed JSON config dict for this specific
        column assignment, or None.
        """
        return None

    async def validate(self, value, config, datasette):
        """
        Validate a value before it is written. Return None if valid,
        or a string error message if invalid.
        """
        return None

    async def transform_value(self, value, config, datasette):
        """
        Transform a value before it appears in JSON API output.
        Return the transformed value. Default: return unchanged.
        """
        return value
