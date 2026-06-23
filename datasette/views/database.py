from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode
import asyncio
import hashlib
import itertools
import json
import markupsafe
import os
import textwrap

from datasette.extras import extra_names_from_request
from datasette.database import QueryInterrupted
from datasette.resources import DatabaseResource, QueryResource
from datasette.stored_queries import stored_query_to_dict
from datasette.write_sql import QueryWriteRejected
from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
    call_with_supported_arguments,
    named_parameters as derive_named_parameters,
    format_bytes,
    make_slot_function,
    tilde_decode,
    to_css_class,
    validate_sql_select,
    is_url,
    path_with_added_args,
    path_with_format,
    path_with_removed_args,
    sqlite3,
    truncate_url,
    InvalidSql,
)
from datasette.utils.asgi import AsgiFileDownload, NotFound, Response, Forbidden
from datasette.plugins import pm

from .base import DatasetteError, View, stream_csv
from .query_helpers import _ensure_stored_query_execution_permissions, _table_columns
from .table_extras import (
    QueryExtraContext,
    resolve_query_extras,
    table_extra_registry,
)
from .table_create_alter import _create_table_ui_context
from . import Context


class DatabaseView(View):
    async def get(self, request, datasette):
        format_ = request.url_vars.get("format") or "html"

        await datasette.refresh_schemas()

        db = await datasette.resolve_database(request)
        database = db.name

        visible, private = await datasette.check_visibility(
            request.actor,
            action="view-database",
            resource=DatabaseResource(database=database),
        )
        if not visible:
            raise Forbidden("You do not have permission to view this database")

        sql = (request.args.get("sql") or "").strip()
        if sql:
            redirect_url = datasette.urls.database(database) + "/-/query"
            if request.url_vars.get("format"):
                redirect_url = path_with_format(
                    path=redirect_url, format=request.url_vars.get("format")
                )
            redirect_url += "?" + request.query_string
            response = Response.redirect(redirect_url)
            if datasette.cors:
                add_cors_headers(response.headers)
            return response

        if format_ not in ("html", "json"):
            raise NotFound("Invalid format: {}".format(format_))

        metadata = await datasette.get_database_metadata(database)

        # Get all tables/views this actor can see in bulk with private flag
        allowed_tables_page = await datasette.allowed_resources(
            "view-table",
            request.actor,
            parent=database,
            include_is_private=True,
            limit=1000,
        )
        # Create lookup dict for quick access
        allowed_dict = {r.child: r for r in allowed_tables_page.resources}

        # Filter to just views
        view_names_set = set(await db.view_names())
        sql_views = [
            {"name": name, "private": allowed_dict[name].private}
            for name in allowed_dict
            if name in view_names_set
        ]

        tables = await get_tables(datasette, request, db, allowed_dict)

        queries_page = await datasette.list_queries(
            database,
            actor=request.actor,
            limit=5,
            include_private=True,
        )
        stored_queries = queries_page.queries
        queries_more = queries_page.has_more
        queries_count = (
            await datasette.count_queries(database, actor=request.actor)
            if queries_more
            else len(stored_queries)
        )

        # Resolve the registered database-level actions for this database in
        # one batched query, seeding the request permission cache so allowed()
        # calls made inside plugin hooks below are served from the cache.
        database_action_permissions = await datasette.allowed_many(
            actions=[
                name
                for name, action in datasette.actions.items()
                if action.resource_class is DatabaseResource
            ],
            resource=DatabaseResource(database),
            actor=request.actor,
        )
        create_table_ui = await _create_table_ui_context(
            datasette, request, db, database, database_action_permissions
        )

        async def database_actions():
            links = []
            if create_table_ui:
                links.append(
                    {
                        "type": "button",
                        "label": "Create table",
                        "description": "Create a new table in this database.",
                        "attrs": {
                            "aria-label": "Create table in {}".format(database),
                            "data-database-action": "create-table",
                        },
                    }
                )
            for hook in pm.hook.database_actions(
                datasette=datasette,
                database=database,
                actor=request.actor,
                request=request,
            ):
                extra_links = await await_me_maybe(hook)
                if extra_links:
                    links.extend(extra_links)
            return links

        attached_databases = [d.name for d in await db.attached_databases()]

        allow_execute_sql = await datasette.allowed(
            action="execute-sql",
            resource=DatabaseResource(database=database),
            actor=request.actor,
        )
        json_data = {
            "ok": True,
            "database": database,
            "private": private,
            "path": datasette.urls.database(database),
            "size": db.size,
            "tables": tables,
            "hidden_count": len([t for t in tables if t["hidden"]]),
            "views": sql_views,
            "queries": [stored_query_to_dict(query) for query in stored_queries],
            "queries_more": queries_more,
            "queries_count": queries_count,
            "allow_execute_sql": allow_execute_sql,
            "table_columns": (
                await _table_columns(datasette, database) if allow_execute_sql else {}
            ),
            "metadata": await datasette.get_database_metadata(database),
        }

        if format_ == "json":
            response = Response.json(json_data)
            if datasette.cors:
                add_cors_headers(response.headers)
            return response

        assert format_ == "html"
        alternate_url_json = datasette.absolute_url(
            request,
            datasette.urls.path(
                path_with_format(
                    request=request,
                    path=request.scope.get("route_path"),
                    format="json",
                )
            ),
        )
        templates = (f"database-{to_css_class(database)}.html", "database.html")
        environment = datasette.get_jinja_environment(request)
        template = environment.select_template(templates)
        return Response.html(
            await datasette.render_template(
                templates,
                DatabaseContext(
                    database=database,
                    private=private,
                    path=datasette.urls.database(database),
                    size=db.size,
                    tables=tables,
                    hidden_count=len([t for t in tables if t["hidden"]]),
                    views=sql_views,
                    queries=stored_queries,
                    queries_more=queries_more,
                    queries_count=queries_count,
                    allow_execute_sql=allow_execute_sql,
                    table_columns=(
                        await _table_columns(datasette, database)
                        if allow_execute_sql
                        else {}
                    ),
                    metadata=metadata,
                    database_color=db.color,
                    database_page_data=(
                        {"createTable": create_table_ui} if create_table_ui else {}
                    ),
                    database_actions=database_actions,
                    show_hidden=request.args.get("_show_hidden"),
                    editable=True,
                    count_limit=db.count_limit,
                    allow_download=datasette.setting("allow_download")
                    and not db.is_mutable
                    and not db.is_memory,
                    attached_databases=attached_databases,
                    alternate_url_json=alternate_url_json,
                    select_templates=[
                        f"{'*' if template_name == template.name else ''}{template_name}"
                        for template_name in templates
                    ],
                    top_database=make_slot_function(
                        "top_database", datasette, request, database=database
                    ),
                ),
                request=request,
                view_name="database",
            ),
            headers={
                "Link": '<{}>; rel="alternate"; type="application/json+datasette"'.format(
                    alternate_url_json
                )
            },
        )


@dataclass
class DatabaseContext(Context):
    "The page listing the tables, views and queries in a database, e.g. /fixtures."

    template = "database.html"

    database: str = field(metadata={"help": "The name of the database"})
    private: bool = field(
        metadata={"help": "Boolean indicating if this is a private database"}
    )
    path: str = field(metadata={"help": "The URL path to this database"})
    size: int = field(metadata={"help": "The size of the database in bytes"})
    tables: list = field(metadata={"help": "List of table objects in the database"})
    hidden_count: int = field(metadata={"help": "Count of hidden tables"})
    views: list = field(metadata={"help": "List of view objects in the database"})
    queries: list = field(metadata={"help": "List of stored query objects"})
    queries_more: bool = field(
        metadata={"help": "Boolean indicating if more stored queries are available"}
    )
    queries_count: int = field(metadata={"help": "Count of visible stored queries"})
    allow_execute_sql: bool = field(
        metadata={"help": "Boolean indicating if custom SQL can be executed"}
    )
    table_columns: dict = field(
        metadata={"help": "Dictionary mapping table names to their column lists"}
    )
    metadata: dict = field(metadata={"help": "Metadata for the database"})
    database_color: str = field(metadata={"help": "The color assigned to the database"})
    database_page_data: dict = field(
        metadata={"help": "JSON data used by JavaScript on the database page"}
    )
    database_actions: callable = field(
        metadata={
            "help": "Callable returning list of action links for the database menu"
        }
    )
    show_hidden: str = field(metadata={"help": "Value of _show_hidden query parameter"})
    editable: bool = field(
        metadata={"help": "Boolean indicating if the database is editable"}
    )
    count_limit: int = field(metadata={"help": "The maximum number of rows to count"})
    allow_download: bool = field(
        metadata={"help": "Boolean indicating if database download is allowed"}
    )
    attached_databases: list = field(
        metadata={"help": "List of names of attached databases"}
    )
    alternate_url_json: str = field(
        metadata={"help": "URL for the alternate JSON version of this page"}
    )
    select_templates: list = field(
        metadata={
            "help": "List of templates that were considered for rendering this page"
        }
    )
    top_database: callable = field(
        metadata={"help": "Callable to render the top_database slot"}
    )


@dataclass
class QueryContext(Context):
    "The page for arbitrary SQL queries (/database/-/query?sql=...) and stored queries (/database/query-name)."

    template = "query.html"

    database: str = field(metadata={"help": "The name of the database being queried"})
    database_color: str = field(metadata={"help": "The color of the database"})
    query: dict = field(
        metadata={"help": "The SQL query object containing the `sql` string"}
    )
    stored_query: str = field(
        metadata={"help": "The name of the stored query if this is a stored query"}
    )
    private: bool = field(
        metadata={"help": "Boolean indicating if this is a private database"}
    )
    # urls: dict = field(
    #     metadata={"help": "Object containing URL helpers like `database()`"}
    # )
    stored_query_write: bool = field(
        metadata={
            "help": "Boolean indicating if this is a stored query that allows writes"
        }
    )
    metadata: dict = field(
        metadata={"help": "Metadata about the database or the stored query"}
    )
    db_is_immutable: bool = field(
        metadata={"help": "Boolean indicating if this database is immutable"}
    )
    error: str = field(metadata={"help": "Any query error message"})
    hide_sql: bool = field(
        metadata={"help": "Boolean indicating if the SQL should be hidden"}
    )
    show_hide_link: str = field(
        metadata={"help": "The URL to toggle showing/hiding the SQL"}
    )
    show_hide_text: str = field(
        metadata={"help": "The text for the show/hide SQL link"}
    )
    editable: bool = field(
        metadata={"help": "Boolean indicating if the SQL can be edited"}
    )
    allow_execute_sql: bool = field(
        metadata={"help": "Boolean indicating if custom SQL can be executed"}
    )
    save_query_url: str = field(
        metadata={"help": "URL to save the current arbitrary SQL as a query"}
    )
    tables: list = field(metadata={"help": "List of table objects in the database"})
    named_parameter_values: dict = field(
        metadata={"help": "Dictionary of parameter names/values"}
    )
    edit_sql_url: str = field(
        metadata={"help": "URL to edit the SQL for a stored query"}
    )
    display_rows: list = field(metadata={"help": "List of result rows to display"})
    columns: list = field(metadata={"help": "List of column names"})
    renderers: dict = field(metadata={"help": "Dictionary of renderer name to URL"})
    url_csv: str = field(metadata={"help": "URL for CSV export"})
    show_hide_hidden: str = field(
        metadata={"help": "Hidden input field for the _show_sql parameter"}
    )
    table_columns: dict = field(
        metadata={"help": "Dictionary of table name to list of column names"}
    )
    alternate_url_json: str = field(
        metadata={"help": "URL for alternate JSON version of this page"}
    )
    # TODO: refactor this to somewhere else, probably ds.render_template()
    select_templates: list = field(
        metadata={
            "help": "List of templates that were considered for rendering this page"
        }
    )
    top_query: callable = field(
        metadata={"help": "Callable to render the top_query slot"}
    )
    top_stored_query: callable = field(
        metadata={"help": "Callable to render the top_stored_query slot"}
    )
    query_actions: callable = field(
        metadata={
            "help": "Callable returning a list of links for the query action menu"
        }
    )


async def get_tables(datasette, request, db, allowed_dict):
    """
    Get list of tables with metadata for the database view.

    Args:
        datasette: The Datasette instance
        request: The current request
        db: The database
        allowed_dict: Dict mapping table name -> Resource object with .private attribute
    """
    tables = []
    table_counts = await db.table_counts(100)
    hidden_table_names = set(await db.hidden_table_names())
    all_foreign_keys = await db.get_all_foreign_keys()

    for table in table_counts:
        if table not in allowed_dict:
            continue

        table_columns = await db.table_columns(table)
        tables.append(
            {
                "name": table,
                "columns": table_columns,
                "primary_keys": await db.primary_keys(table),
                "count": table_counts[table],
                "hidden": table in hidden_table_names,
                "fts_table": await db.fts_table(table),
                "foreign_keys": all_foreign_keys[table],
                "private": allowed_dict[table].private,
            }
        )
    tables.sort(key=lambda t: (t["hidden"], t["name"]))
    return tables


async def database_download(request, datasette):
    from datasette.resources import DatabaseResource

    database = tilde_decode(request.url_vars["database"])
    await datasette.ensure_permission(
        action="view-database-download",
        resource=DatabaseResource(database=database),
        actor=request.actor,
    )
    try:
        db = datasette.get_database(route=database)
    except KeyError:
        raise DatasetteError("Invalid database", status=404)

    if db.is_memory:
        raise DatasetteError("Cannot download in-memory databases", status=404)
    if not datasette.setting("allow_download") or db.is_mutable:
        raise Forbidden("Database download is forbidden")
    if not db.path:
        raise DatasetteError("Cannot download database", status=404)
    filepath = db.path
    headers = {}
    if datasette.cors:
        add_cors_headers(headers)
    if db.hash:
        etag = '"{}"'.format(db.hash)
        headers["Etag"] = etag
        # Has user seen this already?
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match == etag:
            return Response("", status=304)
    headers["Transfer-Encoding"] = "chunked"
    return AsgiFileDownload(
        filepath,
        filename=os.path.basename(filepath),
        content_type="application/octet-stream",
        headers=headers,
    )


class QueryView(View):
    async def post(self, request, datasette):
        from datasette.app import TableNotFound

        db = await datasette.resolve_database(request)

        # We must be a stored query
        table_found = False
        try:
            await datasette.resolve_table(request)
            table_found = True
        except TableNotFound as table_not_found:
            stored_query = await datasette.get_query(
                table_not_found.database_name, table_not_found.table
            )
            if stored_query is None:
                raise
        if table_found:
            # That should not have happened
            raise DatasetteError("Unexpected table found on POST", status=404)

        if not await datasette.allowed(
            action="view-query",
            resource=QueryResource(database=db.name, query=stored_query.name),
            actor=request.actor,
        ):
            raise Forbidden("You do not have permission to view this query")

        try:
            await _ensure_stored_query_execution_permissions(
                datasette, db, stored_query, request.actor
            )
        except QueryWriteRejected as ex:
            if request.headers.get("accept") == "application/json" or request.args.get(
                "_json"
            ):
                return Response.json(
                    {
                        "ok": False,
                        "message": ex.message,
                        "redirect": None,
                    },
                    status=403,
                )
            datasette.add_message(request, ex.message, datasette.ERROR)
            return Response.redirect(stored_query.on_error_redirect or request.path)

        # If database is immutable, return an error
        if not db.is_mutable:
            raise Forbidden("Database is immutable")

        # Process the POST
        body = await request.post_body()
        body = body.decode("utf-8").strip()
        if body.startswith("{") and body.endswith("}"):
            params = json.loads(body)
            # But we want key=value strings
            for key, value in params.items():
                params[key] = str(value)
        else:
            params = dict(parse_qsl(body, keep_blank_values=True))

        # Don't ever send csrftoken as a SQL parameter
        params.pop("csrftoken", None)

        # Should we return JSON?
        should_return_json = (
            request.headers.get("accept") == "application/json"
            or request.args.get("_json")
            or params.get("_json")
        )
        params_for_query = MagicParameters(stored_query.sql, params, request, datasette)
        await params_for_query.execute_params()
        ok = None
        redirect_url = None
        try:
            cursor = await db.execute_write(
                stored_query.sql, params_for_query, request=request
            )
            # success message can come from on_success_message or on_success_message_sql
            message = None
            message_type = datasette.INFO
            on_success_message_sql = stored_query.on_success_message_sql
            if on_success_message_sql:
                try:
                    message_result = (
                        await db.execute(on_success_message_sql, params_for_query)
                    ).first()
                    if message_result:
                        message = message_result[0]
                except Exception as ex:
                    message = "Error running on_success_message_sql: {}".format(ex)
                    message_type = datasette.ERROR
            if not message:
                if stored_query.on_success_message:
                    message = stored_query.on_success_message
                elif cursor.rowcount == -1:
                    message = "Query executed"
                else:
                    message = "Query executed, {} row{} affected".format(
                        cursor.rowcount, "" if cursor.rowcount == 1 else "s"
                    )

            redirect_url = stored_query.on_success_redirect
            ok = True
        except Exception as ex:
            message = stored_query.on_error_message or str(ex)
            message_type = datasette.ERROR
            redirect_url = stored_query.on_error_redirect
            ok = False
        if should_return_json:
            return Response.json(
                {
                    "ok": ok,
                    "message": message,
                    "redirect": redirect_url,
                }
            )
        else:
            datasette.add_message(request, message, message_type)
            return Response.redirect(redirect_url or request.path)

    async def get(self, request, datasette):
        from datasette.app import TableNotFound

        await datasette.refresh_schemas()

        db = await datasette.resolve_database(request)
        database = db.name

        # Get all tables/views this actor can see in bulk with private flag
        allowed_tables_page = await datasette.allowed_resources(
            "view-table",
            request.actor,
            parent=database,
            include_is_private=True,
            limit=1000,
        )
        # Create lookup dict for quick access
        allowed_dict = {r.child: r for r in allowed_tables_page.resources}

        # Are we a stored query?
        stored_query = None
        stored_query_write = False
        if "table" in request.url_vars:
            try:
                await datasette.resolve_table(request)
            except TableNotFound as table_not_found:
                # Was this actually a stored query?
                stored_query = await datasette.get_query(
                    table_not_found.database_name, table_not_found.table
                )
                if stored_query is None:
                    raise
                stored_query_write = stored_query.is_write

        private = False
        if stored_query:
            # Respect stored query permissions
            visible, private = await datasette.check_visibility(
                request.actor,
                action="view-query",
                resource=QueryResource(database=database, query=stored_query.name),
            )
            if not visible:
                raise Forbidden("You do not have permission to view this query")
            if not stored_query_write:
                await _ensure_stored_query_execution_permissions(
                    datasette, db, stored_query, request.actor
                )

        else:
            visible, private = await datasette.check_visibility(
                request.actor,
                action="execute-sql",
                resource=DatabaseResource(database=database),
            )
            if not visible:
                raise Forbidden("execute-sql")

        # Flattened because of ?sql=&name1=value1&name2=value2 feature
        params = {key: request.args.get(key) for key in request.args}
        sql = None

        if stored_query:
            sql = stored_query.sql
        elif "sql" in params:
            sql = params.pop("sql")

        # Extract any :named parameters
        named_parameters = []
        if stored_query and stored_query.parameters:
            named_parameters = stored_query.parameters
        if not named_parameters and sql:
            named_parameters = derive_named_parameters(sql)
        named_parameter_values = {
            named_parameter: params.get(named_parameter) or ""
            for named_parameter in named_parameters
            if not named_parameter.startswith("_")
        }
        # Set to blank string if missing from params
        for named_parameter in named_parameters:
            if named_parameter not in params and not named_parameter.startswith("_"):
                params[named_parameter] = ""

        extra_args = {}
        if params.get("_timelimit"):
            extra_args["custom_time_limit"] = int(params["_timelimit"])

        format_ = request.url_vars.get("format") or "html"

        query_error = None
        results = None
        rows = []
        columns = []

        params_for_query = params

        if sql and not stored_query_write:
            try:
                if not stored_query:
                    # For regular queries we only allow SELECT, plus other rules
                    validate_sql_select(sql)
                else:
                    # Stored queries can run magic parameters
                    params_for_query = MagicParameters(sql, params, request, datasette)
                    await params_for_query.execute_params()
                results = await datasette.execute(
                    database, sql, params_for_query, truncate=True, **extra_args
                )
                columns = results.columns
                rows = results.rows
            except QueryInterrupted as ex:
                raise DatasetteError(
                    textwrap.dedent("""
                    <p>SQL query took too long. The time limit is controlled by the
                    <a href="https://docs.datasette.io/en/stable/settings.html#sql-time-limit-ms">sql_time_limit_ms</a>
                    configuration option.</p>
                    <textarea style="width: 90%">{}</textarea>
                    <script>
                    let ta = document.querySelector("textarea");
                    ta.style.height = ta.scrollHeight + "px";
                    </script>
                """.format(markupsafe.escape(ex.sql))).strip(),
                    title="SQL Interrupted",
                    status=400,
                    message_is_html=True,
                )
            except sqlite3.DatabaseError as ex:
                query_error = str(ex)
                results = None
                rows = []
                columns = []
            except (sqlite3.OperationalError, InvalidSql) as ex:
                raise DatasetteError(str(ex), title="Invalid SQL", status=400)
            except sqlite3.OperationalError as ex:
                raise DatasetteError(str(ex))
            except DatasetteError:
                raise

        async def query_metadata():
            if stored_query:
                metadata = stored_query_to_dict(stored_query)
                metadata.pop("source", None)
                return metadata
            return await datasette.get_database_metadata(database)

        # Handle formats from plugins
        if format_ == "csv":
            if not sql:
                raise DatasetteError("?sql= is required", status=400)

            async def fetch_data_for_csv(request, _next=None):
                results = await db.execute(sql, params, truncate=True)
                data = {"rows": results.rows, "columns": results.columns}
                return data, None, None

            return await stream_csv(datasette, fetch_data_for_csv, request, db.name)
        elif format_ in datasette.renderers.keys():
            data = {"ok": True, "rows": rows, "columns": columns}
            extras = extra_names_from_request(request)
            if extras:
                query_extra_context = QueryExtraContext(
                    datasette=datasette,
                    request=request,
                    db=db,
                    database_name=database,
                    private=private,
                    rows=rows,
                    columns=columns,
                    sql=sql,
                    params=named_parameter_values,
                    query_name=stored_query.name if stored_query else None,
                    metadata=await query_metadata(),
                    extras=extras,
                    extra_registry=table_extra_registry,
                )
                data.update(await resolve_query_extras(extras, query_extra_context))
            # Dispatch request to the correct output format renderer
            # (CSV is not handled here due to streaming)
            result = call_with_supported_arguments(
                datasette.renderers[format_][0],
                datasette=datasette,
                columns=columns,
                rows=rows,
                sql=sql,
                query_name=stored_query.name if stored_query else None,
                database=database,
                table=None,
                request=request,
                view_name="table",
                truncated=results.truncated if results else False,
                error=query_error,
                # These will be deprecated in Datasette 1.0:
                args=request.args,
                data=data,
            )
            if asyncio.iscoroutine(result):
                result = await result
            if result is None:
                raise NotFound("No data")
            if isinstance(result, dict):
                r = Response(
                    body=result.get("body"),
                    status=result.get("status_code") or 200,
                    content_type=result.get("content_type", "text/plain"),
                    headers=result.get("headers"),
                )
            elif isinstance(result, Response):
                r = result
                # if status_code is not None:
                #     # Over-ride the status code
                #     r.status = status_code
            else:
                assert False, f"{result} should be dict or Response"
        elif format_ == "html":
            headers = {}
            templates = [f"query-{to_css_class(database)}.html", "query.html"]
            if stored_query:
                templates.insert(
                    0,
                    f"query-{to_css_class(database)}-{to_css_class(stored_query.name)}.html",
                )

            environment = datasette.get_jinja_environment(request)
            template = environment.select_template(templates)
            alternate_url_json = datasette.absolute_url(
                request,
                datasette.urls.path(
                    path_with_format(
                        request=request,
                        path=request.scope.get("route_path"),
                        format="json",
                    )
                ),
            )
            data = {
                "ok": query_error is None,
                "rows": rows,
                "columns": columns,
                "query": {"sql": sql, "params": params},
                "query_name": stored_query.name if stored_query else None,
                "database": database,
                "table": None,
            }
            headers.update(
                {
                    "Link": '<{}>; rel="alternate"; type="application/json+datasette"'.format(
                        alternate_url_json
                    )
                }
            )
            metadata = await query_metadata()
            renderers = {}
            for key, (_, can_render) in datasette.renderers.items():
                it_can_render = call_with_supported_arguments(
                    can_render,
                    datasette=datasette,
                    columns=data.get("columns") or [],
                    rows=data.get("rows") or [],
                    sql=data.get("query", {}).get("sql", None),
                    query_name=data.get("query_name"),
                    database=database,
                    table=data.get("table"),
                    request=request,
                    view_name="database",
                )
                it_can_render = await await_me_maybe(it_can_render)
                if it_can_render:
                    renderers[key] = datasette.urls.path(
                        path_with_format(
                            request=request,
                            path=request.scope.get("route_path"),
                            format=key,
                        )
                    )

            allow_execute_sql = await datasette.allowed(
                action="execute-sql",
                resource=DatabaseResource(database=database),
                actor=request.actor,
            )
            allow_store_query = await datasette.allowed(
                action="store-query",
                resource=DatabaseResource(database=database),
                actor=request.actor,
            )

            show_hide_hidden = ""
            if stored_query and stored_query.hide_sql:
                if bool(params.get("_show_sql")):
                    show_hide_link = path_with_removed_args(request, {"_show_sql"})
                    show_hide_text = "hide"
                    show_hide_hidden = (
                        '<input type="hidden" name="_show_sql" value="1">'
                    )
                else:
                    show_hide_link = path_with_added_args(request, {"_show_sql": 1})
                    show_hide_text = "show"
            else:
                if bool(params.get("_hide_sql")):
                    show_hide_link = path_with_removed_args(request, {"_hide_sql"})
                    show_hide_text = "show"
                    show_hide_hidden = (
                        '<input type="hidden" name="_hide_sql" value="1">'
                    )
                else:
                    show_hide_link = path_with_added_args(request, {"_hide_sql": 1})
                    show_hide_text = "hide"
            hide_sql = show_hide_text == "show"

            # Show 'Edit SQL' button only if:
            # - User is allowed to execute SQL
            # - SQL is an approved SELECT statement
            # - No magic parameters, so no :_ in the SQL string
            edit_sql_url = None
            is_validated_sql = False
            if sql:
                try:
                    validate_sql_select(sql)
                    is_validated_sql = True
                except InvalidSql:
                    pass
                if allow_execute_sql and is_validated_sql and ":_" not in sql:
                    edit_sql_url = (
                        datasette.urls.database(database)
                        + "/-/query"
                        + "?"
                        + urlencode(
                            {
                                **{
                                    "sql": sql,
                                },
                                **named_parameter_values,
                            }
                        )
                    )
            save_query_url = None
            if (
                not stored_query
                and allow_execute_sql
                and allow_store_query
                and is_validated_sql
                and ":_" not in sql
            ):
                save_query_url = (
                    datasette.urls.database(database)
                    + "/-/queries/store?"
                    + urlencode({"sql": sql})
                )

            async def query_actions():
                query_actions = []
                for hook in pm.hook.query_actions(
                    datasette=datasette,
                    actor=request.actor,
                    database=database,
                    query_name=stored_query.name if stored_query else None,
                    request=request,
                    sql=sql,
                    params=params,
                ):
                    extra_links = await await_me_maybe(hook)
                    if extra_links:
                        query_actions.extend(extra_links)
                return query_actions

            r = Response.html(
                await datasette.render_template(
                    template,
                    QueryContext(
                        database=database,
                        database_color=db.color,
                        query={
                            "sql": sql,
                            "params": params,
                        },
                        stored_query=stored_query.name if stored_query else None,
                        private=private,
                        stored_query_write=stored_query_write,
                        db_is_immutable=not db.is_mutable,
                        error=query_error,
                        hide_sql=hide_sql,
                        show_hide_link=datasette.urls.path(show_hide_link),
                        show_hide_text=show_hide_text,
                        editable=not stored_query,
                        allow_execute_sql=allow_execute_sql,
                        save_query_url=save_query_url,
                        tables=await get_tables(datasette, request, db, allowed_dict),
                        named_parameter_values=named_parameter_values,
                        edit_sql_url=edit_sql_url,
                        display_rows=await display_rows(
                            datasette, database, request, rows, columns
                        ),
                        table_columns=(
                            await _table_columns(datasette, database)
                            if allow_execute_sql
                            else {}
                        ),
                        columns=columns,
                        renderers=renderers,
                        url_csv=datasette.urls.path(
                            path_with_format(
                                request=request,
                                path=request.scope.get("route_path"),
                                format="csv",
                                extra_qs={"_size": "max"},
                            )
                        ),
                        show_hide_hidden=markupsafe.Markup(show_hide_hidden),
                        metadata=metadata,
                        alternate_url_json=alternate_url_json,
                        select_templates=[
                            f"{'*' if template_name == template.name else ''}{template_name}"
                            for template_name in templates
                        ],
                        top_query=make_slot_function(
                            "top_query", datasette, request, database=database, sql=sql
                        ),
                        top_stored_query=make_slot_function(
                            "top_stored_query",
                            datasette,
                            request,
                            database=database,
                            query_name=stored_query.name if stored_query else None,
                        ),
                        query_actions=query_actions,
                    ),
                    request=request,
                    view_name="database",
                ),
                headers=headers,
            )
        else:
            assert False, "Invalid format: {}".format(format_)
        if datasette.cors:
            add_cors_headers(r.headers)
        return r


class MagicParameters(dict):
    def __init__(self, sql, data, request, datasette):
        super().__init__(data)
        self._sql = sql
        self._request = request
        self._magics = dict(
            itertools.chain.from_iterable(
                pm.hook.register_magic_parameters(datasette=datasette)
            )
        )
        self._prepared = {}

    async def execute_params(self):
        for key in derive_named_parameters(self._sql):
            if key.startswith("_") and key.count("_") >= 2:
                prefix, suffix = key[1:].split("_", 1)
                if prefix in self._magics:
                    result = await await_me_maybe(
                        self._magics[prefix](suffix, self._request)
                    )
                    self._prepared[key] = result

    def __len__(self):
        # Workaround for 'Incorrect number of bindings' error
        # https://github.com/simonw/datasette/issues/967#issuecomment-692951144
        return super().__len__() or 1

    def __getitem__(self, key):
        if key.startswith("_") and key.count("_") >= 2:
            if key in self._prepared:
                return self._prepared[key]
            # Try the other route
            prefix, suffix = key[1:].split("_", 1)
            if prefix in self._magics:
                try:
                    return self._magics[prefix](suffix, self._request)
                except KeyError:
                    return super().__getitem__(key)
        else:
            return super().__getitem__(key)


async def display_rows(datasette, database, request, rows, columns):
    display_rows = []
    truncate_cells = datasette.setting("truncate_cells_html")
    for row in rows:
        display_row = []
        for column, value in zip(columns, row):
            display_value = value
            # Let the plugins have a go
            # pylint: disable=no-member
            plugin_display_value = None
            for candidate in pm.hook.render_cell(
                row=row,
                value=value,
                column=column,
                table=None,
                pks=[],
                database=database,
                datasette=datasette,
                request=request,
                column_type=None,
            ):
                candidate = await await_me_maybe(candidate)
                if candidate is not None:
                    plugin_display_value = candidate
                    break
            if plugin_display_value is not None:
                display_value = plugin_display_value
            else:
                if value in ("", None):
                    display_value = markupsafe.Markup("&nbsp;")
                elif is_url(str(display_value).strip()):
                    display_value = markupsafe.Markup(
                        '<a href="{url}">{truncated_url}</a>'.format(
                            url=markupsafe.escape(value.strip()),
                            truncated_url=markupsafe.escape(
                                truncate_url(value.strip(), truncate_cells)
                            ),
                        )
                    )
                elif isinstance(display_value, bytes):
                    blob_url = path_with_format(
                        request=request,
                        format="blob",
                        extra_qs={
                            "_blob_column": column,
                            "_blob_hash": hashlib.sha256(display_value).hexdigest(),
                        },
                    )
                    formatted = format_bytes(len(value))
                    display_value = markupsafe.Markup(
                        '<a class="blob-download" href="{}"{}>&lt;Binary:&nbsp;{:,}&nbsp;byte{}&gt;</a>'.format(
                            blob_url,
                            (
                                ' title="{}"'.format(formatted)
                                if "bytes" not in formatted
                                else ""
                            ),
                            len(value),
                            "" if len(value) == 1 else "s",
                        )
                    )
                else:
                    display_value = str(value)
                    if truncate_cells and len(display_value) > truncate_cells:
                        display_value = display_value[:truncate_cells] + "\u2026"
            display_row.append(display_value)
        display_rows.append(display_row)
    return display_rows
