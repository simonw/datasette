from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, urlencode
import asyncio
import hashlib
import itertools
import json
import markupsafe
import os
import re
import sqlite_utils
import textwrap

from datasette.database import QueryInterrupted
from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
    call_with_supported_arguments,
    derive_named_parameters,
    format_bytes,
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

from .base import BaseView, DatasetteError, View, _error, stream_csv


class DatabaseView(View):
    async def get(self, request, datasette):
        format_ = request.url_vars.get("format") or "html"

        await datasette.refresh_schemas()

        db = await datasette.resolve_database(request)
        database = db.name

        visible, private = await datasette.check_visibility(
            request.actor,
            permissions=[
                ("view-database", database),
                "view-instance",
            ],
        )
        if not visible:
            raise Forbidden("You do not have permission to view this database")

        sql = (request.args.get("sql") or "").strip()
        if sql:
            return await QueryView()(request, datasette)

        if format_ not in ("html", "json"):
            raise NotFound("Invalid format: {}".format(format_))

        metadata = (datasette.metadata("databases") or {}).get(database, {})
        datasette.update_with_inherited_metadata(metadata)

        sql_views = []
        for view_name in await db.view_names():
            view_visible, view_private = await datasette.check_visibility(
                request.actor,
                permissions=[
                    ("view-table", (database, view_name)),
                    ("view-database", database),
                    "view-instance",
                ],
            )
            if view_visible:
                sql_views.append(
                    {
                        "name": view_name,
                        "private": view_private,
                    }
                )

        tables = await get_tables(datasette, request, db)
        canned_queries = []
        for query in (
            await datasette.get_canned_queries(database, request.actor)
        ).values():
            query_visible, query_private = await datasette.check_visibility(
                request.actor,
                permissions=[
                    ("view-query", (database, query["name"])),
                    ("view-database", database),
                    "view-instance",
                ],
            )
            if query_visible:
                canned_queries.append(dict(query, private=query_private))

        async def database_actions():
            links = []
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

        allow_execute_sql = await datasette.permission_allowed(
            request.actor, "execute-sql", database
        )
        json_data = {
            "database": database,
            "private": private,
            "path": datasette.urls.database(database),
            "size": db.size,
            "tables": tables,
            "hidden_count": len([t for t in tables if t["hidden"]]),
            "views": sql_views,
            "queries": canned_queries,
            "allow_execute_sql": allow_execute_sql,
            "table_columns": await _table_columns(datasette, database)
            if allow_execute_sql
            else {},
        }

        if format_ == "json":
            response = Response.json(json_data)
            if datasette.cors:
                add_cors_headers(response.headers)
            return response

        assert format_ == "html"
        alternate_url_json = datasette.absolute_url(
            request,
            datasette.urls.path(path_with_format(request=request, format="json")),
        )
        templates = (f"database-{to_css_class(database)}.html", "database.html")
        template = datasette.jinja_env.select_template(templates)
        context = {
            **json_data,
            "database_color": db.color,
            "database_actions": database_actions,
            "show_hidden": request.args.get("_show_hidden"),
            "editable": True,
            "metadata": metadata,
            "allow_download": datasette.setting("allow_download")
            and not db.is_mutable
            and not db.is_memory,
            "attached_databases": attached_databases,
            "alternate_url_json": alternate_url_json,
            "select_templates": [
                f"{'*' if template_name == template.name else ''}{template_name}"
                for template_name in templates
            ],
        }
        return Response.html(
            await datasette.render_template(
                templates,
                context,
                request=request,
                view_name="database",
            ),
            headers={
                "Link": '{}; rel="alternate"; type="application/json+datasette"'.format(
                    alternate_url_json
                )
            },
        )


@dataclass
class QueryContext:
    database: str = field(metadata={"help": "The name of the database being queried"})
    database_color: str = field(metadata={"help": "The color of the database"})
    query: dict = field(
        metadata={"help": "The SQL query object containing the `sql` string"}
    )
    canned_query: str = field(
        metadata={"help": "The name of the canned query if this is a canned query"}
    )
    private: bool = field(
        metadata={"help": "Boolean indicating if this is a private database"}
    )
    # urls: dict = field(
    #     metadata={"help": "Object containing URL helpers like `database()`"}
    # )
    canned_query_write: bool = field(
        metadata={
            "help": "Boolean indicating if this is a canned query that allows writes"
        }
    )
    metadata: dict = field(
        metadata={"help": "Metadata about the database or the canned query"}
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
    tables: list = field(metadata={"help": "List of table objects in the database"})
    named_parameter_values: dict = field(
        metadata={"help": "Dictionary of parameter names/values"}
    )
    edit_sql_url: str = field(
        metadata={"help": "URL to edit the SQL for a canned query"}
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


async def get_tables(datasette, request, db):
    tables = []
    database = db.name
    table_counts = await db.table_counts(5)
    hidden_table_names = set(await db.hidden_table_names())
    all_foreign_keys = await db.get_all_foreign_keys()

    for table in table_counts:
        table_visible, table_private = await datasette.check_visibility(
            request.actor,
            permissions=[
                ("view-table", (database, table)),
                ("view-database", database),
                "view-instance",
            ],
        )
        if not table_visible:
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
                "private": table_private,
            }
        )
    tables.sort(key=lambda t: (t["hidden"], t["name"]))
    return tables


async def database_download(request, datasette):
    database = tilde_decode(request.url_vars["database"])
    await datasette.ensure_permissions(
        request.actor,
        [
            ("view-database-download", database),
            ("view-database", database),
            "view-instance",
        ],
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

        # We must be a canned query
        table_found = False
        try:
            await datasette.resolve_table(request)
            table_found = True
        except TableNotFound as table_not_found:
            canned_query = await datasette.get_canned_query(
                table_not_found.database_name, table_not_found.table, request.actor
            )
            if canned_query is None:
                raise
        if table_found:
            # That should not have happened
            raise DatasetteError("Unexpected table found on POST", status=404)

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
        params_for_query = MagicParameters(params, request, datasette)
        ok = None
        redirect_url = None
        try:
            cursor = await db.execute_write(canned_query["sql"], params_for_query)
            # success message can come from on_success_message or on_success_message_sql
            message = None
            message_type = datasette.INFO
            on_success_message_sql = canned_query.get("on_success_message_sql")
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
                message = canned_query.get(
                    "on_success_message"
                ) or "Query executed, {} row{} affected".format(
                    cursor.rowcount, "" if cursor.rowcount == 1 else "s"
                )

            redirect_url = canned_query.get("on_success_redirect")
            ok = True
        except Exception as ex:
            message = canned_query.get("on_error_message") or str(ex)
            message_type = datasette.ERROR
            redirect_url = canned_query.get("on_error_redirect")
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

        db = await datasette.resolve_database(request)
        database = db.name

        # Are we a canned query?
        canned_query = None
        canned_query_write = False
        if "table" in request.url_vars:
            try:
                await datasette.resolve_table(request)
            except TableNotFound as table_not_found:
                # Was this actually a canned query?
                canned_query = await datasette.get_canned_query(
                    table_not_found.database_name, table_not_found.table, request.actor
                )
                if canned_query is None:
                    raise
                canned_query_write = bool(canned_query.get("write"))

        private = False
        if canned_query:
            # Respect canned query permissions
            visible, private = await datasette.check_visibility(
                request.actor,
                permissions=[
                    ("view-query", (database, canned_query["name"])),
                    ("view-database", database),
                    "view-instance",
                ],
            )
            if not visible:
                raise Forbidden("You do not have permission to view this query")

        else:
            await datasette.ensure_permissions(
                request.actor, [("execute-sql", database)]
            )

        # Flattened because of ?sql=&name1=value1&name2=value2 feature
        params = {key: request.args.get(key) for key in request.args}
        sql = None

        if canned_query:
            sql = canned_query["sql"]
        elif "sql" in params:
            sql = params.pop("sql")

        # Extract any :named parameters
        named_parameters = []
        if canned_query and canned_query.get("params"):
            named_parameters = canned_query["params"]
        if not named_parameters:
            named_parameters = await derive_named_parameters(
                datasette.get_database(database), sql
            )
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

        if not canned_query_write:
            try:
                if not canned_query:
                    # For regular queries we only allow SELECT, plus other rules
                    validate_sql_select(sql)
                else:
                    # Canned queries can run magic parameters
                    params_for_query = MagicParameters(params, request, datasette)
                results = await datasette.execute(
                    database, sql, params_for_query, truncate=True, **extra_args
                )
                columns = results.columns
                rows = results.rows
            except QueryInterrupted as ex:
                raise DatasetteError(
                    textwrap.dedent(
                        """
                    <p>SQL query took too long. The time limit is controlled by the
                    <a href="https://docs.datasette.io/en/stable/settings.html#sql-time-limit-ms">sql_time_limit_ms</a>
                    configuration option.</p>
                    <textarea style="width: 90%">{}</textarea>
                    <script>
                    let ta = document.querySelector("textarea");
                    ta.style.height = ta.scrollHeight + "px";
                    </script>
                """.format(
                            markupsafe.escape(ex.sql)
                        )
                    ).strip(),
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

        # Handle formats from plugins
        if format_ == "csv":

            async def fetch_data_for_csv(request, _next=None):
                results = await db.execute(sql, params, truncate=True)
                data = {"rows": results.rows, "columns": results.columns}
                return data, None, None

            return await stream_csv(datasette, fetch_data_for_csv, request, db.name)
        elif format_ in datasette.renderers.keys():
            # Dispatch request to the correct output format renderer
            # (CSV is not handled here due to streaming)
            result = call_with_supported_arguments(
                datasette.renderers[format_][0],
                datasette=datasette,
                columns=columns,
                rows=rows,
                sql=sql,
                query_name=canned_query["name"] if canned_query else None,
                database=database,
                table=None,
                request=request,
                view_name="table",
                truncated=results.truncated if results else False,
                error=query_error,
                # These will be deprecated in Datasette 1.0:
                args=request.args,
                data={"ok": True, "rows": rows, "columns": columns},
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
            if canned_query:
                templates.insert(
                    0,
                    f"query-{to_css_class(database)}-{to_css_class(canned_query['name'])}.html",
                )

            template = datasette.jinja_env.select_template(templates)
            alternate_url_json = datasette.absolute_url(
                request,
                datasette.urls.path(path_with_format(request=request, format="json")),
            )
            data = {}
            headers.update(
                {
                    "Link": '{}; rel="alternate"; type="application/json+datasette"'.format(
                        alternate_url_json
                    )
                }
            )
            metadata = (datasette.metadata("databases") or {}).get(database, {})
            datasette.update_with_inherited_metadata(metadata)

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
                        path_with_format(request=request, format=key)
                    )

            allow_execute_sql = await datasette.permission_allowed(
                request.actor, "execute-sql", database
            )

            show_hide_hidden = ""
            if canned_query and canned_query.get("hide_sql"):
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
            try:
                validate_sql_select(sql)
                is_validated_sql = True
            except InvalidSql:
                pass
            if allow_execute_sql and is_validated_sql and ":_" not in sql:
                edit_sql_url = (
                    datasette.urls.database(database)
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
                        canned_query=canned_query["name"] if canned_query else None,
                        private=private,
                        canned_query_write=canned_query_write,
                        db_is_immutable=not db.is_mutable,
                        error=query_error,
                        hide_sql=hide_sql,
                        show_hide_link=datasette.urls.path(show_hide_link),
                        show_hide_text=show_hide_text,
                        editable=not canned_query,
                        allow_execute_sql=allow_execute_sql,
                        tables=await get_tables(datasette, request, db),
                        named_parameter_values=named_parameter_values,
                        edit_sql_url=edit_sql_url,
                        display_rows=await display_rows(
                            datasette, database, request, rows, columns
                        ),
                        table_columns=await _table_columns(datasette, database)
                        if allow_execute_sql
                        else {},
                        columns=columns,
                        renderers=renderers,
                        url_csv=datasette.urls.path(
                            path_with_format(
                                request=request, format="csv", extra_qs={"_size": "max"}
                            )
                        ),
                        show_hide_hidden=markupsafe.Markup(show_hide_hidden),
                        metadata=canned_query or metadata,
                        alternate_url_json=alternate_url_json,
                        select_templates=[
                            f"{'*' if template_name == template.name else ''}{template_name}"
                            for template_name in templates
                        ],
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
    def __init__(self, data, request, datasette):
        super().__init__(data)
        self._request = request
        self._magics = dict(
            itertools.chain.from_iterable(
                pm.hook.register_magic_parameters(datasette=datasette)
            )
        )

    def __len__(self):
        # Workaround for 'Incorrect number of bindings' error
        # https://github.com/simonw/datasette/issues/967#issuecomment-692951144
        return super().__len__() or 1

    def __getitem__(self, key):
        if key.startswith("_") and key.count("_") >= 2:
            prefix, suffix = key[1:].split("_", 1)
            if prefix in self._magics:
                try:
                    return self._magics[prefix](suffix, self._request)
                except KeyError:
                    return super().__getitem__(key)
        else:
            return super().__getitem__(key)


class TableCreateView(BaseView):
    name = "table-create"

    _valid_keys = {"table", "rows", "row", "columns", "pk", "pks", "ignore", "replace"}
    _supported_column_types = {
        "text",
        "integer",
        "float",
        "blob",
    }
    # Any string that does not contain a newline or start with sqlite_
    _table_name_re = re.compile(r"^(?!sqlite_)[^\n]+$")

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        db = await self.ds.resolve_database(request)
        database_name = db.name

        # Must have create-table permission
        if not await self.ds.permission_allowed(
            request.actor, "create-table", resource=database_name
        ):
            return _error(["Permission denied"], 403)

        body = await request.post_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return _error(["Invalid JSON: {}".format(e)])

        if not isinstance(data, dict):
            return _error(["JSON must be an object"])

        invalid_keys = set(data.keys()) - self._valid_keys
        if invalid_keys:
            return _error(["Invalid keys: {}".format(", ".join(invalid_keys))])

        # ignore and replace are mutually exclusive
        if data.get("ignore") and data.get("replace"):
            return _error(["ignore and replace are mutually exclusive"])

        # ignore and replace only allowed with row or rows
        if "ignore" in data or "replace" in data:
            if not data.get("row") and not data.get("rows"):
                return _error(["ignore and replace require row or rows"])

        # ignore and replace require pk or pks
        if "ignore" in data or "replace" in data:
            if not data.get("pk") and not data.get("pks"):
                return _error(["ignore and replace require pk or pks"])

        ignore = data.get("ignore")
        replace = data.get("replace")

        if replace:
            # Must have update-row permission
            if not await self.ds.permission_allowed(
                request.actor, "update-row", resource=database_name
            ):
                return _error(["Permission denied - need update-row"], 403)

        table_name = data.get("table")
        if not table_name:
            return _error(["Table is required"])

        if not self._table_name_re.match(table_name):
            return _error(["Invalid table name"])

        table_exists = await db.table_exists(data["table"])
        columns = data.get("columns")
        rows = data.get("rows")
        row = data.get("row")
        if not columns and not rows and not row:
            return _error(["columns, rows or row is required"])

        if rows and row:
            return _error(["Cannot specify both rows and row"])

        if rows or row:
            # Must have insert-row permission
            if not await self.ds.permission_allowed(
                request.actor, "insert-row", resource=database_name
            ):
                return _error(["Permission denied - need insert-row"], 403)

        if columns:
            if rows or row:
                return _error(["Cannot specify columns with rows or row"])
            if not isinstance(columns, list):
                return _error(["columns must be a list"])
            for column in columns:
                if not isinstance(column, dict):
                    return _error(["columns must be a list of objects"])
                if not column.get("name") or not isinstance(column.get("name"), str):
                    return _error(["Column name is required"])
                if not column.get("type"):
                    column["type"] = "text"
                if column["type"] not in self._supported_column_types:
                    return _error(
                        ["Unsupported column type: {}".format(column["type"])]
                    )
            # No duplicate column names
            dupes = {c["name"] for c in columns if columns.count(c) > 1}
            if dupes:
                return _error(["Duplicate column name: {}".format(", ".join(dupes))])

        if row:
            rows = [row]

        if rows:
            if not isinstance(rows, list):
                return _error(["rows must be a list"])
            for row in rows:
                if not isinstance(row, dict):
                    return _error(["rows must be a list of objects"])

        pk = data.get("pk")
        pks = data.get("pks")

        if pk and pks:
            return _error(["Cannot specify both pk and pks"])
        if pk:
            if not isinstance(pk, str):
                return _error(["pk must be a string"])
        if pks:
            if not isinstance(pks, list):
                return _error(["pks must be a list"])
            for pk in pks:
                if not isinstance(pk, str):
                    return _error(["pks must be a list of strings"])

        # If table exists already, read pks from that instead
        if table_exists:
            actual_pks = await db.primary_keys(table_name)
            # if pk passed and table already exists check it does not change
            bad_pks = False
            if len(actual_pks) == 1 and data.get("pk") and data["pk"] != actual_pks[0]:
                bad_pks = True
            elif (
                len(actual_pks) > 1
                and data.get("pks")
                and set(data["pks"]) != set(actual_pks)
            ):
                bad_pks = True
            if bad_pks:
                return _error(["pk cannot be changed for existing table"])
            pks = actual_pks

        def create_table(conn):
            table = sqlite_utils.Database(conn)[table_name]
            if rows:
                table.insert_all(rows, pk=pks or pk, ignore=ignore, replace=replace)
            else:
                table.create(
                    {c["name"]: c["type"] for c in columns},
                    pk=pks or pk,
                )
            return table.schema

        try:
            schema = await db.execute_write_fn(create_table)
        except Exception as e:
            return _error([str(e)])
        table_url = self.ds.absolute_url(
            request, self.ds.urls.table(db.name, table_name)
        )
        table_api_url = self.ds.absolute_url(
            request, self.ds.urls.table(db.name, table_name, format="json")
        )
        details = {
            "ok": True,
            "database": db.name,
            "table": table_name,
            "table_url": table_url,
            "table_api_url": table_api_url,
            "schema": schema,
        }
        if rows:
            details["row_count"] = len(rows)
        return Response.json(details, status=201)


async def _table_columns(datasette, database_name):
    internal_db = datasette.get_internal_database()
    result = await internal_db.execute(
        "select table_name, name from catalog_columns where database_name = ?",
        [database_name],
    )
    table_columns = {}
    for row in result.rows:
        table_columns.setdefault(row["table_name"], []).append(row["name"])
    # Add views
    db = datasette.get_database(database_name)
    for view_name in await db.view_names():
        table_columns[view_name] = []
    return table_columns


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
                database=database,
                datasette=datasette,
                request=request,
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
                            ' title="{}"'.format(formatted)
                            if "bytes" not in formatted
                            else "",
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
