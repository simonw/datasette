from urllib.parse import parse_qsl, urlencode

from datasette.resources import DatabaseResource, QueryResource
from datasette.stored_queries import stored_query_to_dict
from datasette.utils import UNSTABLE_API_MESSAGE, sqlite3, tilde_decode
from datasette.utils.asgi import Response

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
    skip = set(set_args) | remove_args | {"_next"}
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
        next_url = None
        if page.next:
            pairs = [
                (key, value)
                for key, value in parse_qsl(
                    request.query_string, keep_blank_values=True
                )
                if key != "_next"
            ]
            pairs.append(("_next", page.next))
            next_url = self.ds.absolute_url(
                request,
                "{}?{}".format(request.path, urlencode(pairs)),
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
        return await self.render(
            ["query_list.html"],
            request,
            data,
        )


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
            await _query_create_form_context(
                self.ds,
                request,
                db,
                sql=sql,
                name=name,
                title=title,
                description=description,
                is_private=is_private,
            ),
        )
        response.status = status
        return response

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
            return Response.error(["Query not found: {}".format(query_name)], 404)
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
            return Response.error(["Query not found: {}".format(query_name)], 404)
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
            await _query_edit_form_context(
                self.ds,
                request,
                db,
                existing,
                sql=sql,
                title=title,
                description=description,
                is_private=is_private,
            ),
        )
        response.status = status
        return response

    async def get(self, request):
        db, query_name, existing = await self._load(request)
        if existing is None:
            return Response.error(["Query not found: {}".format(query_name)], 404)
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
            return Response.error(["Query not found: {}".format(query_name)], 404)
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
            return Response.error(["Query not found: {}".format(query_name)], 404)
        await self.ds.ensure_permission(
            action="delete-query",
            resource=QueryResource(db.name, query_name),
            actor=request.actor,
        )
        if existing.is_trusted:
            return Response.error(
                ["Trusted queries cannot be deleted using the API"], 403
            )
        return await self.render(
            ["query_delete.html"],
            request,
            {
                "database": db.name,
                "database_color": db.color,
                "query": stored_query_to_dict(existing),
                "query_url": self.ds.urls.table(db.name, query_name),
            },
        )

    async def post(self, request):
        db, query_name, existing = await self._load(request)
        if existing is None:
            return Response.error(["Query not found: {}".format(query_name)], 404)
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

        data, is_json = await _json_or_form_payload(request)
        await self.ds.remove_query(db.name, query_name)
        if is_json:
            return Response.json({"ok": True})
        self.ds.add_message(
            request,
            "Query “{}” deleted".format(existing.title or query_name),
            self.ds.INFO,
        )
        return Response.redirect(self.ds.urls.path(self.ds.urls.database(db.name)))
