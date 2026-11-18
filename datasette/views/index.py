import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from datasette.homepage import Catalog
from datasette.plugins import pm
from datasette.utils import (
    UNSTABLE_API_MESSAGE,
    CustomJSONEncoder,
    await_me_maybe,
    make_slot_function,
)
from datasette.utils.asgi import Response

from . import Context, select_templates_field
from .base import BaseView

# Maximum number of plugin-provided jump items in homepage search results
OTHER_RESULTS_LIMIT = 10


@dataclass
class IndexContext(Context):
    "The homepage, listing the tables, views and queries across every database."

    documented_template = "index.html"

    databases: list[dict] = field(
        metadata={
            "json": True,
            "help": "One dictionary per database the actor can see, in the order they were attached, with keys ``name``, ``color``, ``private``, ``tables`` and ``views`` (counts matching the current search), ``url`` and ``size`` (bytes, or None for in-memory databases)",
        }
    )
    catalog: Catalog = field(
        metadata={
            "help": "The full catalog object: ``items`` and ``queries`` (see below), ``total`` (tables and views matching the search and database filter, across all pages), ``queries_total``, ``databases`` and ``totals`` (a dictionary of ``databases``, ``tables`` and ``views`` counts)"
        }
    )
    items: list[dict] = field(
        metadata={
            "json": True,
            "help": "The tables and views on this page. Each is a dictionary with ``database``, ``name``, ``type`` (``table`` or ``view``), ``private``, ``title``, ``description``, ``columns`` (up to eight column names), ``more_columns`` (the rest of the column names), ``matched_columns`` (those matching the search), ``column_count``, ``linked_tables`` (other tables linked to or from by a foreign key), ``has_fts``, ``count``, ``count_truncated``, ``url`` and ``color``. ``count`` is None if counting took too long",
        }
    )
    queries: list[dict] = field(
        metadata={
            "json": True,
            "help": "Up to five stored queries. Without a search these are queries from configuration or plugins, then the actor's own saved queries; with a search, any matching query the actor can see. Each is a dictionary with ``database``, ``name``, ``title``, ``description``, ``sql``, ``is_write``, ``private``, ``parameters``, ``url``, ``color`` and ``mine``",
        }
    )
    total: int = field(
        metadata={
            "json": True,
            "help": "Number of tables and views matching the search and database filter, across all pages",
        }
    )
    queries_total: int = field(
        metadata={
            "json": True,
            "help": "Number of stored queries matching the search and database filter, which can be more than the number in ``queries``",
        }
    )
    totals: dict = field(
        metadata={
            "json": True,
            "help": "Dictionary of ``databases``, ``tables`` and ``views`` counts. The table and view counts are those matching the search, across every database - the database filter is ignored",
        }
    )
    other_results: list[dict] = field(
        metadata={
            "help": "While searching, items added to the Jump to menu by plugins using :ref:`plugin_hook_jump_items_sql` that match the search, excluding databases, tables, views and queries. Each has ``name``, ``url``, ``type`` and ``description`` keys"
        }
    )
    q: str = field(metadata={"json": True, "help": "The current search, from ``?q=``"})
    database_filter: str | None = field(
        metadata={
            "json": True,
            "help": "Name of the database the list is filtered to with ``?database=``, or None",
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
        metadata={
            "json": True,
            "help": "URL of the previous page, or None on the first page",
        }
    )
    next_url: str | None = field(
        metadata={
            "json": True,
            "help": "URL of the next page, or None on the last page",
        }
    )
    metadata: dict = field(
        metadata={
            "json": True,
            "help": "Instance metadata, such as ``title``, ``description`` and ``license``",
        }
    )
    private: bool = field(
        metadata={
            "json": True,
            "help": "True if anonymous users cannot view this instance",
        }
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
    select_templates: list[str] = select_templates_field()
    alternate_url_json: str = field(
        default=None,
        metadata={
            "help": "URL of the JSON version of this page: ``/.json`` followed by the current query string"
        },
    )


class IndexView(BaseView):
    name = "index"

    async def get(self, request):
        as_format = request.url_vars["format"]
        await self.ds.ensure_permission(action="view-instance", actor=request.actor)
        if as_format:
            return await self.render_json(request)
        return await self.render_html(request)

    async def catalog_context(self, request, json_urls=False):
        """
        The search, filter and pagination state shared by the HTML and JSON
        versions of the homepage. With json_urls the pagination URLs are
        absolute and point at the JSON version.
        """
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
            qs = "?" + urllib.parse.urlencode(args) if args else ""
            if json_urls:
                return self.ds.absolute_url(
                    request, self.ds.urls.instance() + ".json" + qs
                )
            return self.ds.urls.instance() + qs

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

        return {
            "other_results": other_results,
            # Kept for custom index.html templates written against the
            # old context; each entry is a summary of one database
            "databases": catalog.databases,
            "catalog": catalog,
            "items": catalog.items,
            "total": catalog.total,
            "queries": catalog.queries,
            "queries_total": catalog.queries_total,
            "totals": catalog.totals,
            "q": q,
            "database_filter": database,
            "database_url": database_url,
            "page": page,
            "page_count": page_count,
            "page_size": PAGE_SIZE,
            "previous_url": page_url(page - 1) if page > 1 else None,
            "next_url": page_url(page + 1) if page < page_count else None,
        }

    async def render_json(self, request):
        context = await self.catalog_context(request, json_urls=True)
        data = {"ok": True, "unstable": UNSTABLE_API_MESSAGE}
        data.update(
            {key: context[key] for key in IndexContext.json_keys() if key in context}
        )
        # Colors and the split between previewed and remaining columns are
        # there for the templates - JSON gets one complete list of columns
        data["databases"] = [_without(d, "color") for d in data["databases"]]
        data["items"] = [
            {
                **_without(item, "color", "more_columns"),
                "columns": item["columns"] + item["more_columns"],
            }
            for item in data["items"]
        ]
        data["queries"] = [_without(query, "color") for query in data["queries"]]
        data["metadata"] = await self.ds.get_instance_metadata()
        data["private"] = not await self.ds.allowed(action="view-instance", actor=None)
        return Response.json(data, default=CustomJSONEncoder().default)

    async def render_html(self, request):
        context = await self.catalog_context(request)

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
            context=IndexContext(
                **context,
                metadata=await self.ds.get_instance_metadata(),
                private=not await self.ds.allowed(action="view-instance", actor=None),
                top_homepage=make_slot_function("top_homepage", self.ds, request),
                homepage_actions=homepage_actions,
                noindex=request.path == "/-/",
            ),
        )


def _without(d, *keys):
    return {key: value for key, value in d.items() if key not in keys}
