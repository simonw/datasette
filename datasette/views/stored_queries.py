from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode

from datasette.resources import DatabaseResource, QueryResource
from datasette.stored_queries import StoredQuery, stored_query_to_dict
from datasette.utils import (
    UNSTABLE_API_MESSAGE,
    named_parameters,
    sqlite3,
    tilde_decode,
)
from datasette.utils.asgi import Response

from . import Context, alternate_url_json_field, select_templates_field
from .base import BaseView
from .query_helpers import (
    QueryValidationError,
    _as_bool,
    _as_optional_bool,
    _block_framing,
    _derived_query_parameters,
    _json_or_form_payload,
    _prepare_query_create,
    _prepare_query_update,
    _query_create_analysis_data,
    _query_create_form_context,
    _query_create_form_error_message,
    _query_edit_form_context,
    _query_list_limit,
)


@dataclass
class QueryListContext(Context):
    "The list of stored queries, at ``/-/queries`` for every database and ``/<database>/-/queries`` for one database."

    documented_template = "query_list.html"

    ok: bool = field(metadata={"help": "Always ``True``"})
    database: str | None = field(
        metadata={
            "help": "Name of the database the list is for, or None for the list covering every database"
        }
    )
    database_color: str | None = field(
        metadata={"help": "Color assigned to the database, or None"}
    )
    queries: list[StoredQuery] = field(
        metadata={
            "help": "The stored queries on this page that the actor can see. Each has ``database``, ``name``, ``sql``, ``title``, ``description``, ``description_html``, ``parameters``, ``is_write``, ``is_private``, ``is_trusted``, ``source`` and ``owner_id`` attributes"
        }
    )
    next: str | None = field(
        metadata={"help": "Pagination token for the next page, or None"}
    )
    next_url: str | None = field(
        metadata={"help": "URL of the next page of queries, or None"}
    )
    limit: int = field(metadata={"help": "Maximum number of queries per page"})
    total: int = field(
        metadata={
            "help": "Number of queries matching the current search and filters, across all pages"
        }
    )
    start: int = field(
        metadata={
            "help": "Number of queries on earlier pages, used to display the position of this page"
        }
    )
    show_private_note: bool = field(
        metadata={"help": "True if any query on this page is private"}
    )
    show_trusted_note: bool = field(
        metadata={
            "help": "True if any query on this page is trusted, meaning it skips the permission checks for the SQL it runs"
        }
    )
    query_list_path: str = field(
        metadata={
            "help": "Path to this list without its query string, used as the action for the search form"
        }
    )
    show_database: bool = field(
        metadata={
            "help": "True if the list covers every database, so each query should be shown with its database"
        }
    )
    facets: list[dict] = field(
        metadata={
            "help": "Filters shown next to the list. Each has a ``title`` and a list of ``items``, each with ``label``, ``count``, ``href`` (to toggle the filter, or None) and ``active`` keys"
        }
    )
    filters: dict = field(
        metadata={
            "help": "The current values of the ``q``, ``is_write``, ``is_private``, ``source`` and ``owner_id`` query string arguments, as strings that are empty if not set"
        }
    )
    database_chips: list[dict] = field(
        metadata={
            "help": "Links for switching between databases, each with ``name``, ``count``, ``color``, ``selected`` and ``href`` keys. Empty if fewer than two databases have queries"
        }
    )
    all_queries_href: str | None = field(
        metadata={
            "help": "URL of the list covering every database, keeping the current search and filters"
        }
    )
    actor_id: str | None = field(metadata={"help": "ID of the current actor, or None"})
    query_parameters: dict = field(
        metadata={
            "help": "Dictionary mapping ``(database, name)`` tuples to the list of named parameters for that query"
        }
    )
    select_templates: list[str] = select_templates_field()
    alternate_url_json: str = alternate_url_json_field()


@dataclass
class QueryCreateContext(Context):
    "The form for saving a new stored query, at ``/<database>/-/queries/store``."

    documented_template = "query_create.html"

    database: str = field(metadata={"help": "Database name"})
    database_color: str = field(metadata={"help": "Color assigned to the database"})
    sql: str = field(metadata={"help": "The SQL for the query"})
    name: str = field(
        metadata={"help": "The name entered for the query, used in its URL"}
    )
    title: str = field(metadata={"help": "The title entered for the query"})
    description: str = field(metadata={"help": "The description entered for the query"})
    is_private: bool = field(
        metadata={"help": "True if the private checkbox should be checked"}
    )
    ok: bool = field(metadata={"help": "True if the SQL was analyzed without an error"})
    parameters: list[str] = field(
        metadata={"help": "Names of the ``:named`` parameters found in the SQL"}
    )
    analysis_error: str | None = field(
        metadata={
            "help": "Error message if the SQL could not be analyzed, for example because it is not valid, otherwise None"
        }
    )
    analysis_rows: list[dict] = field(
        metadata={
            "help": "The operations the SQL would perform, one per table it reads or writes. Each has ``operation``, ``database``, ``table``, ``required_permission``, ``message``, ``source`` and ``allowed`` keys. ``allowed`` is False if the actor may not perform that operation, and None for operations that need no permission check"
        }
    )
    has_sql: bool = field(metadata={"help": "True if any SQL has been entered"})
    analysis_is_write: bool = field(
        metadata={"help": "True if the SQL writes to the database"}
    )
    save_disabled: bool = field(
        metadata={
            "help": "True if the query cannot be saved, because there is no SQL, it could not be analyzed or the actor lacks a permission it needs"
        }
    )
    select_templates: list[str] = select_templates_field()


@dataclass
class QueryEditContext(Context):
    "The form for editing a stored query, at ``/<database>/<query>/-/edit``."

    documented_template = "query_edit.html"

    database: str = field(metadata={"help": "Database name"})
    database_color: str = field(metadata={"help": "Color assigned to the database"})
    name: str = field(metadata={"help": "Name of the query being edited"})
    sql: str = field(metadata={"help": "The SQL for the query"})
    title: str = field(metadata={"help": "The title of the query, or an empty string"})
    description: str = field(
        metadata={"help": "The description of the query, or an empty string"}
    )
    is_private: bool = field(
        metadata={"help": "True if the private checkbox should be checked"}
    )
    query_url: str = field(metadata={"help": "URL of the page for the query"})
    ok: bool = field(metadata={"help": "True if the SQL was analyzed without an error"})
    parameters: list[str] = field(
        metadata={"help": "Names of the ``:named`` parameters found in the SQL"}
    )
    analysis_error: str | None = field(
        metadata={
            "help": "Error message if the SQL could not be analyzed, for example because it is not valid, otherwise None"
        }
    )
    analysis_rows: list[dict] = field(
        metadata={
            "help": "The operations the SQL would perform, one per table it reads or writes. Each has ``operation``, ``database``, ``table``, ``required_permission``, ``message``, ``source`` and ``allowed`` keys. ``allowed`` is False if the actor may not perform that operation, and None for operations that need no permission check"
        }
    )
    has_sql: bool = field(metadata={"help": "True if any SQL has been entered"})
    analysis_is_write: bool = field(
        metadata={"help": "True if the SQL writes to the database"}
    )
    save_disabled: bool = field(
        metadata={
            "help": "True if the query cannot be saved, because there is no SQL, it could not be analyzed or the actor lacks a permission it needs"
        }
    )
    select_templates: list[str] = select_templates_field()


class QueryParametersView(BaseView):
    name = "query-parameters"
    has_json_alternate = False

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        if not await self.ds.allowed(
            action="execute-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            return _block_framing(
                Response.error(["Permission denied: need execute-sql"], 403)
            )

        invalid_keys = set(request.args) - {"sql"}
        if invalid_keys:
            return _block_framing(
                Response.error(
                    ["Invalid keys: {}".format(", ".join(sorted(invalid_keys)))],
                    400,
                )
            )
        try:
            parameters = _derived_query_parameters(request.args.get("sql") or "")
        except QueryValidationError as ex:
            return _block_framing(Response.error([ex.message], ex.status))
        return _block_framing(
            Response.json(
                {
                    "ok": True,
                    "unstable": UNSTABLE_API_MESSAGE,
                    "parameters": parameters,
                }
            )
        )


def _query_list_url(path, query_string, *, set_args=None, remove_args=None):
    set_args = set_args or {}
    remove_args = set(remove_args or ())
    skip = set(set_args) | remove_args | {"_next", "_fragment", "_start"}
    pairs = [
        (key, value)
        for key, value in parse_qsl(query_string, keep_blank_values=True)
        if key not in skip
    ]
    for key, value in set_args.items():
        if value not in (None, ""):
            pairs.append((key, value))
    return path + (("?" + urlencode(pairs)) if pairs else "")


class QueryListView(BaseView):
    name = "query-list"

    async def database_name(self, request):
        return (await self.ds.resolve_database(request)).name

    def query_list_path(self, database):
        return self.ds.urls.database(database) + "/-/queries"

    async def get(self, request):
        database = await self.database_name(request)
        format_ = request.url_vars.get("format") or "html"
        try:
            limit = _query_list_limit(
                request.args.get("_size"),
                default=20 if format_ == "html" else 50,
                maximum=self.ds.max_returned_rows,
            )
            is_write = _as_optional_bool(request.args.get("is_write"), "is_write")
            is_private = _as_optional_bool(request.args.get("is_private"), "is_private")
        except QueryValidationError as ex:
            return Response.error([ex.message], ex.status)

        page = await self.ds.list_queries(
            database,
            actor=request.actor,
            limit=limit,
            cursor=request.args.get("_next"),
            q=request.args.get("q") or None,
            is_write=is_write,
            is_private=is_private,
            source=request.args.get("source") or None,
            owner_id=request.args.get("owner_id") or None,
            include_private=True,
        )
        query_list_path = self.query_list_path(database)
        # The cursor does not say how far into the list it is, so HTML pages
        # carry the position in ?_start= for the "Showing 21-40 of 87" footer
        try:
            start = max(int(request.args.get("_start") or 0), 0)
        except ValueError:
            start = 0
        if not request.args.get("_next"):
            start = 0
        next_url = None
        if page.next:
            pairs = [
                (key, value)
                for key, value in parse_qsl(
                    request.query_string, keep_blank_values=True
                )
                if key not in ("_next", "_fragment", "_start")
            ]
            pairs.append(("_next", page.next))
            if format_ == "html":
                pairs.append(("_start", str(start + len(page.queries))))
            next_url = self.ds.absolute_url(
                request,
                f"{request.path}?{urlencode(pairs)}",
            )

        current_filters = {
            "actor": request.actor,
            "q": request.args.get("q") or None,
            "is_write": is_write,
            "is_private": is_private,
            "source": request.args.get("source") or None,
            "owner_id": request.args.get("owner_id") or None,
        }

        async def facet_count(field, value):
            if current_filters[field] is not None and current_filters[field] != value:
                return 0
            filters = dict(current_filters)
            filters[field] = value
            return await self.ds.count_queries(database, **filters)

        def facet_href(field, value):
            if current_filters[field] == value:
                return _query_list_url(
                    query_list_path,
                    request.query_string,
                    remove_args=[field],
                )
            if current_filters[field] is not None:
                return None
            return _query_list_url(
                query_list_path,
                request.query_string,
                set_args={field: str(int(value))},
            )

        async def facet_item(label, field, value):
            count = await facet_count(field, value)
            active = current_filters[field] == value
            if not active and not count:
                return None
            return {
                "label": label,
                "count": count,
                "href": facet_href(field, value) if active or count else None,
                "active": active,
            }

        async def facet_items(items):
            return [
                item
                for item in [
                    await facet_item(label, field, value)
                    for label, field, value in items
                ]
                if item is not None
            ]

        facets = [
            {
                "title": "Mode",
                "items": await facet_items(
                    [
                        ("Read-only", "is_write", False),
                        ("Writable", "is_write", True),
                    ]
                ),
            },
            {
                "title": "Visibility",
                "items": await facet_items(
                    [
                        ("Not private", "is_private", False),
                        ("Private", "is_private", True),
                    ]
                ),
            },
        ]

        total = await self.ds.count_queries(database, **current_filters)

        # Database chips, as on the homepage: "All" plus every database that
        # has queries, each with how many match the current filters
        database_chips = []
        all_queries_href = None
        if format_ == "html":
            chip_query_string = _query_list_url("", request.query_string)
            for name, db in self.ds.databases.items():
                if name == "_internal":
                    continue
                if name != database and not await self.ds.count_queries(
                    name, actor=request.actor
                ):
                    continue
                database_chips.append(
                    {
                        "name": name,
                        "count": await self.ds.count_queries(name, **current_filters),
                        "color": db.color,
                        "selected": name == database,
                        "href": self.ds.urls.database(name)
                        + "/-/queries"
                        + chip_query_string,
                    }
                )
            if len(database_chips) < 2:
                database_chips = []
            all_queries_href = self.ds.urls.path("/-/queries") + chip_query_string

        actor_id = (request.actor or {}).get("id")
        data = {
            "ok": True,
            "database": database,
            "database_color": (
                self.ds.get_database(database).color if database is not None else None
            ),
            "queries": page.queries,
            "next": page.next,
            "next_url": next_url,
            "limit": page.limit,
            "show_private_note": any(query.is_private for query in page.queries),
            "show_trusted_note": any(query.is_trusted for query in page.queries),
            "query_list_path": query_list_path,
            "show_database": database is None,
            "facets": facets,
            "filters": {
                "q": request.args.get("q") or "",
                "is_write": request.args.get("is_write") or "",
                "is_private": request.args.get("is_private") or "",
                "source": request.args.get("source") or "",
                "owner_id": request.args.get("owner_id") or "",
            },
        }
        if format_ == "json":
            return Response.json(
                {
                    **data,
                    "queries": [stored_query_to_dict(query) for query in page.queries],
                }
            )
        data.update(
            {
                "total": total,
                "start": start,
                "database_chips": database_chips,
                "all_queries_href": all_queries_href,
                "actor_id": actor_id,
                # Inputs for each query, read from the SQL when not declared
                "query_parameters": {
                    (query.database, query.name): list(query.parameters)
                    or list(dict.fromkeys(named_parameters(query.sql)))
                    for query in page.queries
                },
            }
        )
        context = QueryListContext(**data)
        if request.args.get("_fragment") == "results":
            response = await self.render(
                ["_query_list_fragment.html"], request, context
            )
            response.headers["X-Robots-Tag"] = "noindex"
            return response
        return await self.render(["query_list.html"], request, context)


class GlobalQueryListView(QueryListView):
    name = "global-query-list"

    async def database_name(self, request):
        return None

    def query_list_path(self, database):
        return self.ds.urls.path("/-/queries")


class QueryCreateView(BaseView):
    name = "query-create"
    has_json_alternate = False

    async def _render_form(
        self,
        request,
        db,
        *,
        sql="",
        name="",
        title="",
        description="",
        is_private=True,
        status=200,
    ):
        response = await self.render(
            ["query_create.html"],
            request,
            QueryCreateContext(
                **await _query_create_form_context(
                    self.ds,
                    request,
                    db,
                    sql=sql,
                    name=name,
                    title=title,
                    description=description,
                    is_private=is_private,
                )
            ),
        )
        response.status = status
        return _block_framing(response)

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        await self.ds.ensure_permission(
            action="execute-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        )
        await self.ds.ensure_permission(
            action="store-query",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        )

        return await self._render_form(request, db, sql=request.args.get("sql") or "")


class QueryCreateAnalyzeView(BaseView):
    name = "query-create-analyze"
    has_json_alternate = False

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        if not await self.ds.allowed(
            action="execute-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            return _block_framing(
                Response.error(["Permission denied: need execute-sql"], 403)
            )
        if not await self.ds.allowed(
            action="store-query",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            return _block_framing(
                Response.error(["Permission denied: need store-query"], 403)
            )

        invalid_keys = set(request.args) - {"sql"}
        if invalid_keys:
            return _block_framing(
                Response.error(
                    ["Invalid keys: {}".format(", ".join(sorted(invalid_keys)))],
                    400,
                )
            )
        sql = request.args.get("sql") or ""
        analysis = await _query_create_analysis_data(self.ds, db, sql, request.actor)
        analysis["unstable"] = UNSTABLE_API_MESSAGE
        return _block_framing(Response.json(analysis))


class QueryStoreView(QueryCreateView):
    name = "query-store"

    async def _error_response(self, request, db, query_data, message, status):
        message = _query_create_form_error_message(message)
        self.ds.add_message(request, message, self.ds.ERROR)
        return await self._render_form(
            request,
            db,
            sql=query_data.get("sql") or "",
            name=query_data.get("name") or "",
            title=query_data.get("title") or "",
            description=query_data.get("description") or "",
            is_private=_as_bool(query_data.get("is_private", True)),
            status=status,
        )

    async def post(self, request):
        db = await self.ds.resolve_database(request)
        if not await self.ds.allowed(
            action="execute-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            return Response.error(["Permission denied: need execute-sql"], 403)
        if not await self.ds.allowed(
            action="store-query",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            return Response.error(["Permission denied: need store-query"], 403)

        is_json = False
        query_data = {}
        try:
            data, is_json = await _json_or_form_payload(request)
            if not isinstance(data, dict):
                raise QueryValidationError("JSON must be a dictionary")
            query_data = data.get("query") if is_json else data
            if not isinstance(query_data, dict):
                raise QueryValidationError("JSON must contain a query dictionary")
            prepared = await _prepare_query_create(self.ds, request, db, query_data)
        except QueryValidationError as ex:
            if not is_json and isinstance(query_data, dict):
                return await self._error_response(
                    request, db, query_data, ex.message, ex.status
                )
            return Response.error([ex.message], ex.status)

        prepared.pop("analysis")
        name = prepared.pop("name")
        try:
            await self.ds.add_query(db.name, name, replace=False, **prepared)
        except sqlite3.IntegrityError as ex:
            if not is_json and isinstance(query_data, dict):
                return await self._error_response(request, db, query_data, str(ex), 400)
            return Response.error([str(ex)], 400)

        query = await self.ds.get_query(db.name, name)
        assert query is not None
        if is_json:
            return Response.json(
                {
                    "ok": True,
                    "unstable": UNSTABLE_API_MESSAGE,
                    "query": stored_query_to_dict(query),
                },
                status=201,
            )
        self.ds.add_message(request, "Query saved", self.ds.INFO)
        return Response.redirect(self.ds.urls.path(self.ds.urls.table(db.name, name)))


class QueryDefinitionView(BaseView):
    name = "query-definition"

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        query_name = tilde_decode(request.url_vars["query"])
        query = await self.ds.get_query(db.name, query_name)
        if query is None:
            return Response.error([f"Query not found: {query_name}"], 404)
        if not await self.ds.allowed(
            action="view-query",
            resource=QueryResource(db.name, query_name),
            actor=request.actor,
        ):
            return Response.error(["Permission denied"], 403)
        return Response.json(
            {
                "ok": True,
                "unstable": UNSTABLE_API_MESSAGE,
                "query": stored_query_to_dict(query),
            }
        )


class QueryUpdateView(BaseView):
    name = "query-update"

    async def post(self, request):
        db = await self.ds.resolve_database(request)
        query_name = tilde_decode(request.url_vars["query"])
        existing = await self.ds.get_query(db.name, query_name)
        if existing is None:
            return Response.error([f"Query not found: {query_name}"], 404)
        if not await self.ds.allowed(
            action="update-query",
            resource=QueryResource(db.name, query_name),
            actor=request.actor,
        ):
            return Response.error(["Permission denied: need update-query"], 403)
        if existing.is_trusted:
            return Response.error(
                ["Trusted queries cannot be updated using the API"], 403
            )

        try:
            data, _ = await _json_or_form_payload(request)
            if not isinstance(data, dict):
                raise QueryValidationError("JSON must be a dictionary")
            invalid_keys = set(data) - {"update", "return"}
            if invalid_keys:
                raise QueryValidationError(
                    "Invalid keys: {}".format(", ".join(invalid_keys))
                )
            update = data.get("update")
            if not isinstance(update, dict):
                raise QueryValidationError("JSON must contain an update dictionary")
            if "sql" in update and not await self.ds.allowed(
                action="execute-sql",
                resource=DatabaseResource(db.name),
                actor=request.actor,
            ):
                raise QueryValidationError(
                    "Permission denied: need execute-sql", status=403
                )
            update_kwargs = await _prepare_query_update(
                self.ds, request, db, existing, update
            )
        except QueryValidationError as ex:
            return Response.error([ex.message], ex.status)

        await self.ds.update_query(db.name, query_name, **update_kwargs)
        if data.get("return"):
            query = await self.ds.get_query(db.name, query_name)
            assert query is not None
            return Response.json(
                {
                    "ok": True,
                    "query": stored_query_to_dict(query),
                }
            )
        return Response.json({"ok": True})


class QueryEditView(BaseView):
    name = "query-edit"
    has_json_alternate = False

    async def _load(self, request):
        db = await self.ds.resolve_database(request)
        query_name = tilde_decode(request.url_vars["query"])
        existing = await self.ds.get_query(db.name, query_name)
        return db, query_name, existing

    async def _render_form(
        self,
        request,
        db,
        existing,
        *,
        sql=None,
        title=None,
        description=None,
        is_private=None,
        status=200,
    ):
        response = await self.render(
            ["query_edit.html"],
            request,
            QueryEditContext(
                **await _query_edit_form_context(
                    self.ds,
                    request,
                    db,
                    existing,
                    sql=sql,
                    title=title,
                    description=description,
                    is_private=is_private,
                )
            ),
        )
        response.status = status
        return _block_framing(response)

    async def get(self, request):
        db, query_name, existing = await self._load(request)
        if existing is None:
            return Response.error([f"Query not found: {query_name}"], 404)
        await self.ds.ensure_permission(
            action="update-query",
            resource=QueryResource(db.name, query_name),
            actor=request.actor,
        )
        if existing.is_trusted:
            return Response.error(["Trusted queries cannot be edited"], 403)
        return await self._render_form(request, db, existing)

    async def post(self, request):
        db, query_name, existing = await self._load(request)
        if existing is None:
            return Response.error([f"Query not found: {query_name}"], 404)
        if not await self.ds.allowed(
            action="update-query",
            resource=QueryResource(db.name, query_name),
            actor=request.actor,
        ):
            return Response.error(["Permission denied: need update-query"], 403)
        if existing.is_trusted:
            return Response.error(["Trusted queries cannot be edited"], 403)

        data, _ = await _json_or_form_payload(request)
        if not isinstance(data, dict):
            return Response.error(["Invalid form submission"], 400)
        sql = data.get("sql")
        sql = existing.sql if sql is None else sql.strip()
        title = data.get("title") or ""
        description = data.get("description") or ""
        is_private = _as_bool(data.get("is_private"))

        update = {
            "title": title,
            "description": description,
            "is_private": is_private,
        }
        if sql != existing.sql:
            if not await self.ds.allowed(
                action="execute-sql",
                resource=DatabaseResource(db.name),
                actor=request.actor,
            ):
                self.ds.add_message(
                    request,
                    "Permission denied: need execute-sql to change the SQL",
                    self.ds.ERROR,
                )
                return await self._render_form(
                    request,
                    db,
                    existing,
                    sql=sql,
                    title=title,
                    description=description,
                    is_private=is_private,
                    status=403,
                )
            update["sql"] = sql

        try:
            update_kwargs = await _prepare_query_update(
                self.ds, request, db, existing, update
            )
        except QueryValidationError as ex:
            self.ds.add_message(request, ex.message, self.ds.ERROR)
            return await self._render_form(
                request,
                db,
                existing,
                sql=sql,
                title=title,
                description=description,
                is_private=is_private,
                status=ex.status,
            )

        await self.ds.update_query(db.name, query_name, **update_kwargs)
        self.ds.add_message(request, "Query updated", self.ds.INFO)
        return Response.redirect(
            self.ds.urls.path(self.ds.urls.table(db.name, query_name))
        )


class QueryDeleteView(BaseView):
    name = "query-delete"
    has_json_alternate = False

    async def _load(self, request):
        db = await self.ds.resolve_database(request)
        query_name = tilde_decode(request.url_vars["query"])
        existing = await self.ds.get_query(db.name, query_name)
        return db, query_name, existing

    async def get(self, request):
        db, query_name, existing = await self._load(request)
        if existing is None:
            return Response.error([f"Query not found: {query_name}"], 404)
        await self.ds.ensure_permission(
            action="delete-query",
            resource=QueryResource(db.name, query_name),
            actor=request.actor,
        )
        if existing.is_trusted:
            return Response.error(
                ["Trusted queries cannot be deleted using the API"], 403
            )
        return _block_framing(
            await self.render(
                ["query_delete.html"],
                request,
                {
                    "database": db.name,
                    "database_color": db.color,
                    "query": stored_query_to_dict(existing),
                    "query_url": self.ds.urls.table(db.name, query_name),
                },
            )
        )

    async def post(self, request):
        db, query_name, existing = await self._load(request)
        if existing is None:
            return Response.error([f"Query not found: {query_name}"], 404)
        if not await self.ds.allowed(
            action="delete-query",
            resource=QueryResource(db.name, query_name),
            actor=request.actor,
        ):
            return Response.error(["Permission denied: need delete-query"], 403)
        if existing.is_trusted:
            return Response.error(
                ["Trusted queries cannot be deleted using the API"], 403
            )

        _data, is_json = await _json_or_form_payload(request)
        await self.ds.remove_query(db.name, query_name)
        if is_json:
            return Response.json({"ok": True})
        self.ds.add_message(
            request,
            f"Query “{existing.title or query_name}” deleted",
            self.ds.INFO,
        )
        return Response.redirect(self.ds.urls.path(self.ds.urls.database(db.name)))
