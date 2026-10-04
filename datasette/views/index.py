import json

from datasette.database import DatasetteClosedError
from datasette.plugins import pm
from datasette.utils import (
    UNSTABLE_API_MESSAGE,
    CustomJSONEncoder,
    add_cors_headers,
    await_me_maybe,
    make_slot_function,
    sqlite3,
    tilde_encode,
)
from datasette.utils.asgi import Response
from datasette.utils.catalog import (
    catalog_relationship_counts,
    catalog_summaries,
    catalog_table_details,
)
from datasette.version import __version__

from .base import BaseView

# Truncate table list on homepage at:
TRUNCATE_AT = 5

# Only attempt counts if database less than this size in bytes:
COUNT_DB_SIZE_LIMIT = 100 * 1024 * 1024

# Row counts need a count(*) per table in every database listed, so the
# index page only runs them when it lists at most this many databases that
# would need counting. Above that it shows table counts only - plus row
# counts that are already known without opening the database (immutable
# databases with inspect data, or whose counts a page computed earlier).
# Everything else on the page comes from the _internal catalog, so the
# index page never opens a database it is not counting.
COUNT_MAX_DATABASES = 20


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
        # One snapshot for the whole request: add_database() and
        # remove_database() replace ds.databases rather than changing it, so
        # a database removed while this page is being built is still listed
        # instead of failing the page with a KeyError
        all_databases = self.ds.databases
        allowed_db_dict = {
            r.parent: r for r in allowed_databases if r.parent in all_databases
        }

        # Group tables by database
        tables_by_db = {}
        table_page = await self.ds.allowed_resources(
            "view-table", request.actor, include_is_private=True
        )
        async for t in table_page.all():
            if t.parent not in tables_by_db:
                tables_by_db[t.parent] = {}
            tables_by_db[t.parent][t.child] = t

        names = list(allowed_db_dict)
        summaries = await catalog_summaries(self.ds, names)
        all_counts = await self._table_counts(names, all_databases)
        sort_by_relationships = request.args.get("_sort") == "relationships"
        need_relationships = {
            name for name in names if sort_by_relationships or not all_counts[name]
        }
        relationship_counts = (
            await catalog_relationship_counts(self.ds, need_relationships)
            if need_relationships
            else {}
        )

        prepared = []
        for name, allowed_db in allowed_db_dict.items():
            summary = summaries[name]
            # Get allowed tables/views for this database
            allowed_for_db = tables_by_db.get(name, {})
            db_config = self.ds.config.get("databases", {}).get(name, {})
            config_hidden = {
                t
                for t, table_config in db_config.get("tables", {}).items()
                if table_config.get("hidden")
            }
            views = [
                {"name": child_name, "private": resource.private}
                for child_name, resource in allowed_for_db.items()
                if child_name in summary.views
            ]
            table_counts = all_counts[name]
            relationships = relationship_counts.get(name, {})
            tables = {}
            for table, resource in allowed_for_db.items():
                if table in summary.views:
                    continue
                tables[table] = {
                    "name": table,
                    "count": table_counts.get(table),
                    "hidden": summary.is_hidden(table, config_hidden),
                    "num_relationships_for_sorting": (
                        relationships.get(table, 0) if name in need_relationships else 0
                    ),
                    "private": resource.private,
                }

            hidden_tables = [t for t in tables.values() if t["hidden"]]
            visible_tables = [t for t in tables.values() if not t["hidden"]]

            tables_and_views_truncated = list(
                sorted(
                    visible_tables,
                    key=lambda t: (
                        t["num_relationships_for_sorting"],
                        t["count"] or 0,
                        t["name"],
                    ),
                    reverse=True,
                )[:TRUNCATE_AT]
            )
            prepared.append(
                (
                    name,
                    allowed_db,
                    tables,
                    views,
                    hidden_tables,
                    visible_tables,
                    table_counts,
                    tables_and_views_truncated,
                )
            )

        # Columns, primary keys and FTS tables, for the tables shown only
        details = await catalog_table_details(
            self.ds,
            [(name, t["name"]) for name, *_, truncated in prepared for t in truncated],
        )

        databases = []
        for (
            name,
            allowed_db,
            tables,
            views,
            hidden_tables,
            visible_tables,
            table_counts,
            tables_and_views_truncated,
        ) in prepared:
            db = all_databases[name]
            tables_and_views_truncated = [
                {
                    "name": t["name"],
                    "columns": details[(name, t["name"])]["columns"],
                    "primary_keys": details[(name, t["name"])]["primary_keys"],
                    "count": t["count"],
                    "hidden": t["hidden"],
                    "fts_table": details[(name, t["name"])]["fts_table"],
                    "num_relationships_for_sorting": t["num_relationships_for_sorting"],
                    "private": t["private"],
                }
                for t in tables_and_views_truncated
            ]
            # Only add views if this is less than TRUNCATE_AT
            if len(tables_and_views_truncated) < TRUNCATE_AT:
                num_views_to_add = TRUNCATE_AT - len(tables_and_views_truncated)
                tables_and_views_truncated.extend(views[:num_views_to_add])

            databases.append(
                {
                    "name": name,
                    "hash": db.hash,
                    "color": db.color,
                    # From the snapshot: name may have been removed since
                    "path": self.ds.urls.path(tilde_encode(db.route)),
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
                    "private": allowed_db.private,
                }
            )

        # Leave out databases removed while this page was being built: the
        # HTML template links to them by name
        databases = [d for d in databases if d["name"] in self.ds.databases]

        if as_format:
            headers = {}
            if self.ds.cors:
                add_cors_headers(headers)
            return Response(
                json.dumps(
                    {
                        "ok": True,
                        "unstable": UNSTABLE_API_MESSAGE,
                        "databases": databases,
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

    async def _table_counts(self, names, all_databases):
        """``{database: {table: count}}`` - empty for a database whose
        counts are skipped or timed out (see COUNT_MAX_DATABASES)."""
        counts = {}
        to_count = []
        for name in names:
            db = all_databases[name]
            if not db.is_mutable and db.cached_table_counts is not None:
                # Known without opening the database
                counts[name] = db.cached_table_counts
            else:
                counts[name] = {}
                to_count.append(db)
        if len(to_count) > COUNT_MAX_DATABASES:
            return counts
        for db in to_count:
            if db.is_mutable:
                try:
                    if db.size >= COUNT_DB_SIZE_LIMIT:
                        continue
                except OSError:
                    # The file has gone: there is nothing to count
                    continue
            try:
                table_counts = await db.table_counts(10)
            except (DatasetteClosedError, sqlite3.Error):
                # Closed or removed while this page was being built, or its
                # file deleted or replaced: show it without counts
                continue
            # If any of these are None it means at least one timed out - ignore them all
            if any(v is None for v in table_counts.values()):
                table_counts = {}
            counts[db.name] = table_counts
        return counts
