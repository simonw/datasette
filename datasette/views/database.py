from dataclasses import dataclass, field
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

from datasette.events import AlterTableEvent, CreateTableEvent, InsertRowsEvent
from datasette.database import QueryInterrupted
from datasette.resources import DatabaseResource, QueryResource
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

from .base import BaseView, DatasetteError, View, _error, stream_csv
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
            redirect_url = "/" + request.url_vars.get("database") + "/-/query"
            if request.url_vars.get("format"):
                redirect_url += "." + request.url_vars.get("format")
            redirect_url += "?" + request.query_string
            return Response.redirect(redirect_url)
            return await QueryView()(request, datasette)

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

        # Get allowed queries using the new permission system
        allowed_query_page = await datasette.allowed_resources(
            "view-query",
            request.actor,
            parent=database,
            include_is_private=True,
            limit=1000,
        )

        # Build canned_queries list by looking up each allowed query
        all_queries = await datasette.get_canned_queries(database, request.actor)
        canned_queries = []
        for query_resource in allowed_query_page.resources:
            query_name = query_resource.child
            if query_name in all_queries:
                canned_queries.append(
                    dict(all_queries[query_name], private=query_resource.private)
                )

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

        allow_execute_sql = await datasette.allowed(
            action="execute-sql",
            resource=DatabaseResource(database=database),
            actor=request.actor,
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
            datasette.urls.path(path_with_format(request=request, format="json")),
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
                    queries=canned_queries,
                    allow_execute_sql=allow_execute_sql,
                    table_columns=(
                        await _table_columns(datasette, database)
                        if allow_execute_sql
                        else {}
                    ),
                    metadata=metadata,
                    database_color=db.color,
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
    database: str = field(metadata={"help": "The name of the database"})
    private: bool = field(
        metadata={"help": "Boolean indicating if this is a private database"}
    )
    path: str = field(metadata={"help": "The URL path to this database"})
    size: int = field(metadata={"help": "The size of the database in bytes"})
    tables: list = field(metadata={"help": "List of table objects in the database"})
    hidden_count: int = field(metadata={"help": "Count of hidden tables"})
    views: list = field(metadata={"help": "List of view objects in the database"})
    queries: list = field(metadata={"help": "List of canned query objects"})
    allow_execute_sql: bool = field(
        metadata={"help": "Boolean indicating if custom SQL can be executed"}
    )
    table_columns: dict = field(
        metadata={"help": "Dictionary mapping table names to their column lists"}
    )
    metadata: dict = field(metadata={"help": "Metadata for the database"})
    database_color: str = field(metadata={"help": "The color assigned to the database"})
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
    top_query: callable = field(
        metadata={"help": "Callable to render the top_query slot"}
    )
    top_canned_query: callable = field(
        metadata={"help": "Callable to render the top_canned_query slot"}
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
        params_for_query = MagicParameters(
            canned_query["sql"], params, request, datasette
        )
        await params_for_query.execute_params()
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
        from datasette.query_page import QueryPage

        await datasette.refresh_schemas()

        db = await datasette.resolve_database(request)
        database = db.name

        # Are we a canned query?
        canned_query = None
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

        private = False
        if canned_query:
            # Respect canned query permissions
            visible, private = await datasette.check_visibility(
                request.actor,
                action="view-query",
                resource=QueryResource(database=database, query=canned_query["name"]),
            )
            if not visible:
                raise Forbidden("You do not have permission to view this query")
        else:
            await datasette.ensure_permission(
                action="execute-sql",
                resource=DatabaseResource(database=database),
                actor=request.actor,
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
            named_parameters = derive_named_parameters(sql)
        # Set to blank string if missing from params
        for named_parameter in named_parameters:
            if named_parameter not in params and not named_parameter.startswith("_"):
                params[named_parameter] = ""

        # Delegate to QueryPage for query execution and rendering
        page = QueryPage(
            datasette,
            request,
            database=database,
            sql=sql,
            params=params,
            editable=not canned_query,
            canned_query=canned_query,
            private=private,
        )
        return await page.response()


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


class TableCreateView(BaseView):
    name = "table-create"

    _valid_keys = {
        "table",
        "rows",
        "row",
        "columns",
        "pk",
        "pks",
        "ignore",
        "replace",
        "alter",
    }
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
        if not await self.ds.allowed(
            action="create-table",
            resource=DatabaseResource(database=database_name),
            actor=request.actor,
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
            if not await self.ds.allowed(
                action="update-row",
                resource=DatabaseResource(database=database_name),
                actor=request.actor,
            ):
                return _error(["Permission denied: need update-row"], 403)

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
            if not await self.ds.allowed(
                action="insert-row",
                resource=DatabaseResource(database=database_name),
                actor=request.actor,
            ):
                return _error(["Permission denied: need insert-row"], 403)

        alter = False
        if rows or row:
            if not table_exists:
                # if table is being created for the first time, alter=True
                alter = True
            else:
                # alter=True only if they request it AND they have permission
                if data.get("alter"):
                    if not await self.ds.allowed(
                        action="alter-table",
                        resource=DatabaseResource(database=database_name),
                        actor=request.actor,
                    ):
                        return _error(["Permission denied: need alter-table"], 403)
                    alter = True

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

        initial_schema = None
        if table_exists:
            initial_schema = await db.execute_fn(
                lambda conn: sqlite_utils.Database(conn)[table_name].schema
            )

        def create_table(conn):
            table = sqlite_utils.Database(conn)[table_name]
            if rows:
                table.insert_all(
                    rows, pk=pks or pk, ignore=ignore, replace=replace, alter=alter
                )
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

        if initial_schema is not None and initial_schema != schema:
            await self.ds.track_event(
                AlterTableEvent(
                    request.actor,
                    database=database_name,
                    table=table_name,
                    before_schema=initial_schema,
                    after_schema=schema,
                )
            )

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

        if not table_exists:
            # Only log creation if we created a table
            await self.ds.track_event(
                CreateTableEvent(
                    request.actor, database=db.name, table=table_name, schema=schema
                )
            )
        if rows:
            await self.ds.track_event(
                InsertRowsEvent(
                    request.actor,
                    database=db.name,
                    table=table_name,
                    num_rows=len(rows),
                    ignore=ignore,
                    replace=replace,
                )
            )
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
    """Format raw query result rows for HTML display.

    This is a thin wrapper around :func:`datasette.query_page._display_rows`
    for backwards compatibility.
    """
    from datasette.query_page import _display_rows

    return await _display_rows(datasette, database, request, rows, columns)
