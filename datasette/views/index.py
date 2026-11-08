import json
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from datasette.homepage import Catalog
from datasette.plugins import pm
from datasette.utils import (
    UNSTABLE_API_MESSAGE,
    CustomJSONEncoder,
    add_cors_headers,
    await_me_maybe,
    make_slot_function,
)
from datasette.utils.asgi import Response
from datasette.version import __version__

from . import Context
from .base import BaseView

# Maximum number of plugin-provided jump items in homepage search results
OTHER_RESULTS_LIMIT = 10

# Truncate table list on homepage at:
TRUNCATE_AT = 5

# Only attempt counts if database less than this size in bytes:
COUNT_DB_SIZE_LIMIT = 100 * 1024 * 1024


@dataclass
class IndexContext(Context):
    "The homepage, listing the tables, views and queries across every database."

    documented_template = "index.html"

    databases: list[dict] = field(
        metadata={
            "help": "One dictionary per database the actor can see, in the order they were attached, with keys ``name``, ``color``, ``private``, ``tables`` and ``views`` (counts matching the current search), ``url`` and ``size`` (bytes, or None for in-memory databases)"
        }
    )
    catalog: Catalog = field(
        metadata={
            "help": "The full catalog object: ``items`` and ``queries`` (see below), ``total`` (tables and views matching the search and database filter, across all pages), ``queries_total``, ``databases`` and ``totals`` (a dictionary of ``databases``, ``tables`` and ``views`` counts)"
        }
    )
    items: list[dict] = field(
        metadata={
            "help": "The tables and views on this page. Each is a dictionary with ``database``, ``name``, ``type`` (``table`` or ``view``), ``private``, ``title``, ``description``, ``columns`` (up to eight column names), ``matched_columns`` (those matching the search), ``column_count``, ``relationships`` (foreign keys in and out), ``has_fts``, ``count``, ``count_truncated``, ``url`` and ``color``. ``count`` is None if counting took too long"
        }
    )
    queries: list[dict] = field(
        metadata={
            "help": "Up to five stored queries. Without a search these are queries from configuration or plugins, then the actor's own saved queries; with a search, any matching query the actor can see. Each is a dictionary with ``database``, ``name``, ``title``, ``description``, ``sql``, ``is_write``, ``private``, ``parameters``, ``url``, ``color`` and ``mine``"
        }
    )
    other_results: list[dict] = field(
        metadata={
            "help": "While searching, items added to the Jump to menu by plugins using :ref:`plugin_hook_jump_items_sql` that match the search, excluding databases, tables, views and queries. Each has ``name``, ``url``, ``type`` and ``description`` keys"
        }
    )
    q: str = field(metadata={"help": "The current search, from ``?q=``"})
    database_filter: str | None = field(
        metadata={
            "help": "Name of the database the list is filtered to with ``?database=``, or None"
        }
    )
    database_url: Callable = field(
        metadata={
            "help": "Function that takes a database name, or None for all databases, and returns the homepage URL filtered to that database, keeping the current search"
        }
    )
    page: int = field(
        metadata={"help": "The current page of tables and views, from ``?page=``"}
    )
    page_count: int = field(metadata={"help": "The total number of pages"})
    page_size: int = field(metadata={"help": "The number of tables and views per page"})
    previous_url: str | None = field(
        metadata={"help": "URL of the previous page, or None on the first page"}
    )
    next_url: str | None = field(
        metadata={"help": "URL of the next page, or None on the last page"}
    )
    metadata: dict = field(
        metadata={
            "help": "Instance metadata, such as ``title``, ``description`` and ``license``"
        }
    )
    private: bool = field(
        metadata={"help": "True if anonymous users cannot view this instance"}
    )
    top_homepage: Callable = field(
        metadata={
            "help": "Function returning HTML from plugins that implement :ref:`plugin_hook_top_homepage`"
        }
    )
    homepage_actions: list[dict] = field(
        metadata={
            "help": "Links for the homepage actions menu, from plugins implementing :ref:`plugin_hook_homepage_actions`"
        }
    )
    noindex: bool = field(
        metadata={
            "help": "True for the ``/-/`` alternative homepage, which should not be indexed by search engines"
        }
    )
    alternate_url_json: str = field(
        metadata={"help": "URL of the JSON version of this page, ``/.json``"}
    )
    select_templates: list[str] = field(
        metadata={
            "help": "The template names Datasette considered, with an asterisk next to the one that was used"
        }
    )


class IndexView(BaseView):
    name = "index"

    async def get(self, request):
        as_format = request.url_vars["format"]
        await self.ds.ensure_permission(action="view-instance", actor=request.actor)
        if not as_format:
            return await self.render_html(request)

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
        for name, allowed_db in allowed_db_dict.items():
            db = self.ds.databases[name]
            database_private = allowed_db.private

            # Get allowed tables/views for this database
            allowed_for_db = tables_by_db.get(name, {})

            # Get table names from allowed set instead of db.table_names()
            table_names = [child_name for child_name in allowed_for_db]

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
                    if table in tables:
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
                tables_and_views_truncated.extend(views[:num_views_to_add])

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

    async def render_html(self, request):
        from datasette.homepage import PAGE_SIZE, build_catalog

        q = request.args.get("q", "")
        database = request.args.get("database") or None
        try:
            page = max(int(request.args.get("page", "1")), 1)
        except ValueError:
            page = 1
        catalog = await build_catalog(
            self.ds, request.actor, q=q, database=database, page=page
        )
        if database not in {d["name"] for d in catalog.databases}:
            database = None
        page_count = max((catalog.total + PAGE_SIZE - 1) // PAGE_SIZE, 1)

        def page_url(n):
            args = []
            if q:
                args.append(("q", q))
            if database:
                args.append(("database", database))
            if n > 1:
                args.append(("page", str(n)))
            return self.ds.urls.instance() + (
                "?" + urllib.parse.urlencode(args) if args else ""
            )

        def database_url(name):
            args = []
            if q:
                args.append(("q", q))
            if name:
                args.append(("database", name))
            return self.ds.urls.instance() + (
                "?" + urllib.parse.urlencode(args) if args else ""
            )

        # Anything else plugins add to the Jump to menu with jump_items_sql
        # (apps, say) shows up in search results too, using the same
        # permission checks and ranking as /-/jump
        other_results = []
        if q.strip() and not database:
            from .special import JumpView

            other_results, _ = await JumpView(self.ds).matches(
                request,
                q,
                exclude_types=["database", "table", "view", "query"],
                limit=OTHER_RESULTS_LIMIT,
            )

        context = {
            "other_results": other_results,
            # Kept for custom index.html templates written against the
            # old context; each entry is a summary of one database
            "databases": catalog.databases,
            "catalog": catalog,
            "items": catalog.items,
            "queries": catalog.queries,
            "q": q,
            "database_filter": database,
            "database_url": database_url,
            "page": page,
            "page_count": page_count,
            "page_size": PAGE_SIZE,
            "previous_url": page_url(page - 1) if page > 1 else None,
            "next_url": page_url(page + 1) if page < page_count else None,
        }

        # Live search on the homepage asks for just the summary line and the
        # results, rendered by the same templates as the full page
        if request.args.get("_fragment") == "results":
            response = await self.render(
                ["_homepage_fragment.html"], request=request, context=context
            )
            response.headers["X-Robots-Tag"] = "noindex"
            return response

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
                **context,
                "metadata": await self.ds.get_instance_metadata(),
                "datasette_version": __version__,
                "private": not await self.ds.allowed(
                    action="view-instance", actor=None
                ),
                "top_homepage": make_slot_function("top_homepage", self.ds, request),
                "homepage_actions": homepage_actions,
                "noindex": request.path == "/-/",
            },
        )
