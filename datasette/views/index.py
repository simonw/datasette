import hashlib
import json

from datasette.utils import add_cors_headers, CustomJSONEncoder
from datasette.utils.asgi import Response
from datasette.version import __version__

from .base import BaseView


# Truncate table list on homepage at:
TRUNCATE_AT = 5

# Only attempt counts if database less than this size in bytes:
COUNT_DB_SIZE_LIMIT = 100 * 1024 * 1024


class IndexView(BaseView):
    name = "index"

    async def get(self, request):
        as_format = request.url_vars["format"]
        await self.ds.ensure_permissions(request.actor, ["view-instance"])
        databases = []
        for name, db in self.ds.databases.items():
            database_visible, database_private = await self.ds.check_visibility(
                request.actor,
                "view-database",
                name,
            )
            if not database_visible:
                continue
            table_names = await db.table_names()
            hidden_table_names = set(await db.hidden_table_names())

            views = []
            for view_name in await db.view_names():
                view_visible, view_private = await self.ds.check_visibility(
                    request.actor,
                    "view-table",
                    (name, view_name),
                )
                if view_visible:
                    views.append({"name": view_name, "private": view_private})

            # Perform counts only for immutable or DBS with <= COUNT_TABLE_LIMIT tables
            table_counts = {}
            if not db.is_mutable or db.size < COUNT_DB_SIZE_LIMIT:
                table_counts = await db.table_counts(10)
                # If any of these are None it means at least one timed out - ignore them all
                if any(v is None for v in table_counts.values()):
                    table_counts = {}

            tables = {}
            for table in table_names:
                visible, private = await self.ds.check_visibility(
                    request.actor,
                    "view-table",
                    (name, table),
                )
                if not visible:
                    continue
                table_columns = await db.table_columns(table)
                tables[table] = {
                    "name": table,
                    "columns": table_columns,
                    "primary_keys": await db.primary_keys(table),
                    "count": table_counts.get(table),
                    "hidden": table in hidden_table_names,
                    "fts_table": await db.fts_table(table),
                    "num_relationships_for_sorting": 0,
                    "private": private,
                }

            if request.args.get("_sort") == "relationships" or not table_counts:
                # We will be sorting by number of relationships, so populate that field
                all_foreign_keys = await db.get_all_foreign_keys()
                for table, foreign_keys in all_foreign_keys.items():
                    if table in tables.keys():
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
                for view in views[:num_views_to_add]:
                    tables_and_views_truncated.append(view)

            databases.append(
                {
                    "name": name,
                    "hash": db.hash,
                    "color": db.hash[:6]
                    if db.hash
                    else hashlib.md5(name.encode("utf8")).hexdigest()[:6],
                    "path": self.ds.urls.database(name),
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
                    "private": database_private,
                }
            )

        if as_format:
            headers = {}
            if self.ds.cors:
                add_cors_headers(headers)
            return Response(
                json.dumps({db["name"]: db for db in databases}, cls=CustomJSONEncoder),
                content_type="application/json; charset=utf-8",
                headers=headers,
            )
        else:
            return await self.render(
                ["index.html"],
                request=request,
                context={
                    "databases": databases,
                    "metadata": self.ds.metadata(),
                    "datasette_version": __version__,
                    "private": not await self.ds.permission_allowed(
                        None, "view-instance", default=True
                    ),
                },
            )
