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


TRUNCATE_AT = 5


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
            visible_tables = [t for t in tables.values() if not t["hidden"]]

            tables_and_views_truncated = list(
                sorted(
                    (t for t in tables.values() if t not in hidden_tables),
                    key=lambda t: t["count"] or 0,
                    reverse=True,
                )[:TRUNCATE_AT]
            )

            # Only add views if this is less than TRUNCATE_AT
            if len(tables_and_views_truncated) < TRUNCATE_AT:
                num_views_to_add = TRUNCATE_AT - len(tables_and_views_truncated)
                for view_name in views[:num_views_to_add]:
                    tables_and_views_truncated.append({"name": view_name})

            databases.append(
                {
                    "name": name,
                    "hash": db.hash,
                    "color": db.hash[:6]
                    if db.hash
                    else hashlib.md5(name.encode("utf8")).hexdigest()[:6],
                    "path": self.database_url(name),
                    "tables_and_views_truncated": tables_and_views_truncated,
                    "tables_and_views_more": (len(visible_tables) + len(views))
                    > TRUNCATE_AT,
                    "tables_count": len(visible_tables),
                    "table_rows_sum": sum((t["count"] or 0) for t in visible_tables),
                    "hidden_table_rows_sum": sum(
                        t["count"] for t in hidden_tables if t["count"] is not None
                    ),
                    "hidden_tables_count": len(hidden_tables),
                    "views_count": len(views),
                }
            )

        databases.sort(key=lambda database: database["name"])

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
