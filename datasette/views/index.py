import json

from datasette.plugins import pm
from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
    make_slot_function,
    CustomJSONEncoder,
)
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
        await self.ds.ensure_permission(action="view-instance", actor=request.actor)

        # Get all allowed databases and tables in bulk
        db_page = await self.ds.allowed_resources(
            "view-database", request.actor, include_is_private=True
        )
        allowed_databases = [r async for r in db_page.all()]
        allowed_db_dict = {r.parent: r for r in allowed_databases}

        # Group tables by database
        tables_by_db = {}
        table_page = await self.ds.allowed_resources(
            "view-table", request.actor, include_is_private=True
        )
        async for t in table_page.all():
            if t.parent not in tables_by_db:
                tables_by_db[t.parent] = {}
            tables_by_db[t.parent][t.child] = t

        databases = []
        # Iterate over allowed databases instead of all databases
        for name in allowed_db_dict.keys():
            db = self.ds.databases[name]
            database_private = allowed_db_dict[name].private

            # Get allowed tables/views for this database
            allowed_for_db = tables_by_db.get(name, {})

            # Get table names from allowed set instead of db.table_names()
            table_names = [child_name for child_name in allowed_for_db.keys()]

            hidden_table_names = set(await db.hidden_table_names())

            # Determine which allowed items are views
            view_names_set = set(await db.view_names())
            views = [
                {"name": child_name, "private": resource.private}
                for child_name, resource in allowed_for_db.items()
                if child_name in view_names_set
            ]

            # Filter to just tables (not views) for table processing
            table_names = [name for name in table_names if name not in view_names_set]

            # Perform counts only for immutable or DBS with <= COUNT_TABLE_LIMIT tables
            table_counts = {}
            if not db.is_mutable or db.size < COUNT_DB_SIZE_LIMIT:
                table_counts = await db.table_counts(10)
                # If any of these are None it means at least one timed out - ignore them all
                if any(v is None for v in table_counts.values()):
                    table_counts = {}

            tables = {}
            for table in table_names:
                # Check if table is in allowed set
                if table not in allowed_for_db:
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
                    "private": allowed_for_db[table].private,
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
                    "color": db.color,
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
                json.dumps(
                    {
                        "databases": {db["name"]: db for db in databases},
                        "metadata": await self.ds.get_instance_metadata(),
                    },
                    cls=CustomJSONEncoder,
                ),
                content_type="application/json; charset=utf-8",
                headers=headers,
            )
        else:
            homepage_actions = []
            for hook in pm.hook.homepage_actions(
                datasette=self.ds,
                actor=request.actor,
                request=request,
            ):
                extra_links = await await_me_maybe(hook)
                if extra_links:
                    homepage_actions.extend(extra_links)
            alternative_homepage = request.path == "/-/"
            return await self.render(
                ["default:index.html" if alternative_homepage else "index.html"],
                request=request,
                context={
                    "databases": databases,
                    "metadata": await self.ds.get_instance_metadata(),
                    "datasette_version": __version__,
                    "private": not await self.ds.allowed(
                        action="view-instance", actor=None
                    ),
                    "top_homepage": make_slot_function(
                        "top_homepage", self.ds, request
                    ),
                    "homepage_actions": homepage_actions,
                    "noindex": request.path == "/-/",
                },
            )
