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


# Truncate table list on homepage at:
TRUNCATE_AT = 5

# Only attempt counts if less than this many tables:
COUNT_TABLE_LIMIT = 30


class IndexView(RenderMixin):
    name = "index"

    def __init__(self, datasette):
        self.ds = datasette

    async def get(self, request, as_format):
        databases = []
        for name, db in self.ds.databases.items():
            table_names = await db.table_names()
            hidden_table_names = set(await db.hidden_table_names())
            views = await db.view_names()
            # Perform counts only for immutable or DBS with <= COUNT_TABLE_LIMIT tables
            table_counts = {}
            if not db.is_mutable or len(table_names) <= COUNT_TABLE_LIMIT:
                table_counts = await db.table_counts(10)
                # If any of these are None it means at least one timed out - ignore them all
                if any(v is None for v in table_counts.values()):
                    table_counts = {}
            tables = {}
            for table in table_names:
                table_columns = await self.ds.table_columns(name, table)
                tables[table] = {
                    "name": table,
                    "columns": table_columns,
                    "primary_keys": await self.ds.execute_against_connection_in_thread(
                        name, lambda conn: detect_primary_keys(conn, table)
                    ),
                    "count": table_counts.get(table),
                    "hidden": table in hidden_table_names,
                    "fts_table": await self.ds.execute_against_connection_in_thread(
                        name, lambda conn: detect_fts(conn, table)
                    ),
                    "num_relationships_for_sorting": 0,
                }

            if request.args.get("_sort") == "relationships" or not table_counts:
                # We will be sorting by number of relationships, so populate that field
                all_foreign_keys = await db.get_all_foreign_keys()
                for table, foreign_keys in all_foreign_keys.items():
                    count = len(foreign_keys["incoming"] + foreign_keys["outgoing"])
                    tables[table]["num_relationships_for_sorting"] = count

            hidden_tables = [t for t in tables.values() if t["hidden"]]
            visible_tables = [t for t in tables.values() if not t["hidden"]]

            tables_and_views_truncated = list(
                sorted(
                    (t for t in tables.values() if t not in hidden_tables),
                    key=lambda t: (
                        t["num_relationships_for_sorting"],
                        t["count"] or 0,
                        t["name"],
                    ),
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
                    "show_table_row_counts": bool(table_counts),
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
