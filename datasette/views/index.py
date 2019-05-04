import hashlib
import json

from sanic import response

from datasette.utils import (
    CustomJSONEncoder,
    InterruptedError,
    detect_primary_keys,
    detect_fts,
)
from datasette.version import __version__

from .base import HASH_LENGTH, RenderMixin


class IndexView(RenderMixin):
    name = "index"

    def __init__(self, datasette):
        self.ds = datasette

    async def get(self, request, as_format):
        databases = []
        for name, db in self.ds.databases.items():
            table_counts = await db.table_counts(5)
            views = await db.view_names()
            tables = {}
            hidden_table_names = set(await db.hidden_table_names())
            for table in table_counts:
                table_columns = await self.ds.table_columns(name, table)
                tables[table] = {
                    "name": table,
                    "columns": table_columns,
                    "primary_keys": await self.ds.execute_against_connection_in_thread(
                        name, lambda conn: detect_primary_keys(conn, table)
                    ),
                    "count": table_counts[table],
                    "hidden": table in hidden_table_names,
                    "fts_table": await self.ds.execute_against_connection_in_thread(
                        name, lambda conn: detect_fts(conn, table)
                    ),
                }
            hidden_tables = [t for t in tables.values() if t["hidden"]]

            databases.append(
                {
                    "name": name,
                    "hash": db.hash,
                    "color": db.hash[:6]
                    if db.hash
                    else hashlib.md5(name.encode("utf8")).hexdigest()[:6],
                    "path": self.database_url(name),
                    "tables_truncated": sorted(
                        tables.values(), key=lambda t: t["count"] or 0, reverse=True
                    )[:5],
                    "tables_count": len(tables),
                    "tables_more": len(tables) > 5,
                    "table_rows_sum": sum((t["count"] or 0) for t in tables.values()),
                    "hidden_table_rows_sum": sum(
                        (t["count"] or 0) for t in hidden_tables
                    ),
                    "hidden_tables_count": len(hidden_tables),
                    "views_count": len(views),
                }
            )
        if as_format:
            headers = {}
            if self.ds.cors:
                headers["Access-Control-Allow-Origin"] = "*"
            return response.HTTPResponse(
                json.dumps({db["name"]: db for db in databases}, cls=CustomJSONEncoder),
                content_type="application/json",
                headers=headers,
            )
        else:
            return self.render(
                ["index.html"],
                databases=databases,
                metadata=self.ds.metadata(),
                datasette_version=__version__,
            )
