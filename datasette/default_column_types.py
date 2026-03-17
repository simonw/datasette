import json
import re

import markupsafe

from datasette import hookimpl
from datasette.column_types import ColumnType


class UrlColumnType(ColumnType):
    name = "url"
    description = "URL"

    async def render_cell(self, value, column, table, database, datasette, request):
        if not value or not isinstance(value, str):
            return None
        escaped = markupsafe.escape(value.strip())
        return markupsafe.Markup(f'<a href="{escaped}">{escaped}</a>')

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


@hookimpl
def register_column_types(datasette):
    return [UrlColumnType, EmailColumnType, JsonColumnType]
