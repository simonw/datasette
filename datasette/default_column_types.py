import json
import re

import markupsafe

from datasette import hookimpl
from datasette.column_types import ColumnType, SQLiteType
from datasette.utils import truncate_url


class UrlColumnType(ColumnType):
    name = "url"
    description = "URL"
    sqlite_types = (SQLiteType.TEXT,)

    async def render_cell(self, value, column, table, database, datasette, request):
        if not value or not isinstance(value, str):
            return None
        url = value.strip()
        escaped = markupsafe.escape(url)
        truncated = markupsafe.escape(
            truncate_url(url, datasette.setting("truncate_cells_html"))
        )
        return markupsafe.Markup(f'<a href="{escaped}">{truncated}</a>')

    async def validate(self, value, datasette):
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            return "URL must be a string"
        if not re.match(r"^https?://\S+$", value.strip()):
            return "Invalid URL"
        return None


class EmailColumnType(ColumnType):
    name = "email"
    description = "Email address"
    sqlite_types = (SQLiteType.TEXT,)

    async def render_cell(self, value, column, table, database, datasette, request):
        if not value or not isinstance(value, str):
            return None
        escaped = markupsafe.escape(value.strip())
        return markupsafe.Markup(f'<a href="mailto:{escaped}">{escaped}</a>')

    async def validate(self, value, datasette):
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            return "Email must be a string"
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value.strip()):
            return "Invalid email address"
        return None


class JsonColumnType(ColumnType):
    name = "json"
    description = "JSON data"
    sqlite_types = (SQLiteType.TEXT,)

    async def render_cell(self, value, column, table, database, datasette, request):
        if value is None:
            return None
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
            formatted = json.dumps(parsed, indent=2)
            escaped = markupsafe.escape(formatted)
            return markupsafe.Markup(f"<pre>{escaped}</pre>")
        except (json.JSONDecodeError, TypeError):
            return None

    async def validate(self, value, datasette):
        if value is None or value == "":
            return None
        if isinstance(value, str):
            try:
                json.loads(value)
            except json.JSONDecodeError:
                return "Invalid JSON"
        return None


class TextareaColumnType(ColumnType):
    name = "textarea"
    description = "Multiline text"
    sqlite_types = (SQLiteType.TEXT,)


@hookimpl
def register_column_types(datasette):
    return [UrlColumnType, EmailColumnType, JsonColumnType, TextareaColumnType]
