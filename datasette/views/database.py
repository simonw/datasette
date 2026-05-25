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
from datasette.resources import DatabaseResource, QueryResource, TableResource
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
        canned_queries = queries_page["queries"]
        queries_more = queries_page["has_more"]

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
            "ok": True,
            "database": database,
            "private": private,
            "path": datasette.urls.database(database),
            "size": db.size,
            "tables": tables,
            "hidden_count": len([t for t in tables if t["hidden"]]),
            "views": sql_views,
            "queries": canned_queries,
            "queries_more": queries_more,
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
                    queries_more=queries_more,
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
    queries_more: bool = field(
        metadata={"help": "Boolean indicating if more saved queries are available"}
    )
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
    save_query_url: str = field(
        metadata={"help": "URL to save the current arbitrary SQL as a query"}
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


_query_name_re = re.compile(r"^[^/\.\n]+$")

_query_fields = {
    "sql",
    "title",
    "description",
    "description_html",
    "hide_sql",
    "fragment",
    "parameters",
    "params",
    "is_published",
    "on_success_message",
    "on_success_message_sql",
    "on_success_redirect",
    "on_error_message",
    "on_error_redirect",
}

_query_create_fields = _query_fields | {"name", "mode", "csrftoken"}
_query_update_fields = _query_fields
_query_write_fields = {
    "on_success_message",
    "on_success_message_sql",
    "on_success_redirect",
    "on_error_message",
    "on_error_redirect",
}


class QueryValidationError(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status


def _actor_id(actor):
    if isinstance(actor, dict):
        return actor.get("id")
    return None


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in {"1", "true", "t", "yes", "on"}
    return bool(value)


def _as_optional_bool(value, name):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"1", "true", "t", "yes", "on"}:
            return True
        if lowered in {"0", "false", "f", "no", "off"}:
            return False
    raise QueryValidationError("{} must be 0 or 1".format(name))


def _query_list_limit(value):
    if value in (None, ""):
        return 50
    try:
        return min(max(1, int(value)), 1000)
    except ValueError as ex:
        raise QueryValidationError("_size must be an integer") from ex


def _derived_query_parameters(sql):
    parameters = []
    seen = set()
    for parameter in derive_named_parameters(sql):
        if parameter.startswith("_"):
            raise QueryValidationError("Magic parameters are not allowed")
        if parameter not in seen:
            parameters.append(parameter)
            seen.add(parameter)
    return parameters


def _coerce_query_parameters(value, derived):
    if value is None:
        return derived
    if isinstance(value, str):
        parameters = [
            parameter.strip()
            for parameter in re.split(r"[\s,]+", value)
            if parameter.strip()
        ]
    elif isinstance(value, list):
        parameters = value
    else:
        raise QueryValidationError("parameters must be a list of strings")
    if not all(isinstance(parameter, str) for parameter in parameters):
        raise QueryValidationError("parameters must be a list of strings")
    if any(parameter.startswith("_") for parameter in parameters):
        raise QueryValidationError("Magic parameters are not allowed")
    if set(parameters) != set(derived):
        raise QueryValidationError("parameters must match SQL named parameters")
    return parameters


def _analysis_is_write(analysis):
    return any(
        access.operation in {"insert", "update", "delete"}
        for access in analysis.table_accesses
    )


def _block_framing(response):
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _wants_json(request, is_json, data):
    return (
        is_json
        or request.headers.get("accept") == "application/json"
        or (isinstance(data, dict) and data.get("_json"))
    )


async def _json_or_form_payload(request):
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        body = await request.post_body()
        try:
            return json.loads(body or b"{}"), True
        except json.JSONDecodeError as e:
            raise QueryValidationError("Invalid JSON: {}".format(e))
    return await request.post_vars(), False


async def _check_query_name(db, name, *, existing=False):
    if not name or not isinstance(name, str):
        raise QueryValidationError("Query name is required")
    if not _query_name_re.match(name):
        raise QueryValidationError("Invalid query name")
    if not existing and (await db.table_exists(name) or await db.view_exists(name)):
        raise QueryValidationError("Query name conflicts with a table or view")


async def _analyze_user_query(datasette, db, sql, *, actor, is_published):
    if not sql or not isinstance(sql, str):
        raise QueryValidationError("SQL is required")
    derived = _derived_query_parameters(sql)
    params = {parameter: "" for parameter in derived}
    try:
        analysis = await db.analyze_sql(sql, params)
    except sqlite3.DatabaseError as ex:
        raise QueryValidationError("Could not analyze query: {}".format(ex)) from ex

    is_write = _analysis_is_write(analysis)
    if is_write:
        if is_published:
            raise QueryValidationError("Writable queries cannot be published")
        try:
            await datasette.ensure_query_write_permissions(
                db.name, sql, actor=actor, analysis=analysis
            )
        except Forbidden as ex:
            raise QueryValidationError(str(ex), status=403) from ex
    else:
        try:
            validate_sql_select(sql)
        except InvalidSql as ex:
            raise QueryValidationError(str(ex)) from ex
    return is_write, derived, analysis


def _analysis_rows(analysis):
    write_actions = {
        "insert": "insert-row",
        "update": "update-row",
        "delete": "delete-row",
    }
    return [
        {
            "operation": access.operation,
            "database": access.database,
            "table": access.table,
            "required_permission": write_actions.get(access.operation, ""),
            "source": access.source,
        }
        for access in analysis.table_accesses
    ]


async def _analysis_rows_with_permissions(datasette, analysis, actor):
    rows = _analysis_rows(analysis)
    for row in rows:
        permission = row["required_permission"]
        if permission:
            row["allowed"] = await datasette.allowed(
                action=permission,
                resource=TableResource(row["database"], row["table"]),
                actor=actor,
            )
        else:
            row["allowed"] = None
    return rows


def _coerce_execute_write_payload(data, is_json):
    if not isinstance(data, dict):
        raise QueryValidationError("JSON must be a dictionary")
    if is_json:
        invalid_keys = set(data) - {"sql", "params"}
        if invalid_keys:
            raise QueryValidationError(
                "Invalid keys: {}".format(", ".join(sorted(invalid_keys)))
            )
        params = data.get("params") or {}
    else:
        params = {
            key: value
            for key, value in data.items()
            if key not in {"sql", "csrftoken", "_json"}
        }
    if not isinstance(params, dict):
        raise QueryValidationError("params must be a dictionary")
    return data.get("sql"), params


async def _prepare_execute_write(datasette, db, sql, params, actor):
    if not sql or not isinstance(sql, str):
        raise QueryValidationError("SQL is required")
    parameter_names = _derived_query_parameters(sql)
    extra_params = set(params) - set(parameter_names)
    if extra_params:
        raise QueryValidationError(
            "Unknown parameters: {}".format(", ".join(sorted(extra_params)))
        )
    params = {name: params.get(name, "") for name in parameter_names}
    try:
        analysis = await db.analyze_sql(sql, params)
    except sqlite3.DatabaseError as ex:
        raise QueryValidationError("Could not analyze query: {}".format(ex)) from ex
    if not _analysis_is_write(analysis):
        raise QueryValidationError(
            "Use /-/query for read-only SQL; this endpoint only executes writes"
        )
    try:
        await datasette.ensure_query_write_permissions(
            db.name, sql, actor=actor, analysis=analysis
        )
    except Forbidden as ex:
        raise QueryValidationError(str(ex), status=403) from ex
    return parameter_names, params, analysis


def _apply_query_data_types(data):
    typed = dict(data)
    for key in ("hide_sql", "is_published"):
        if key in typed:
            typed[key] = _as_bool(typed[key])
    return typed


async def _prepare_query_create(datasette, request, db, data):
    invalid_keys = set(data) - _query_create_fields
    if invalid_keys:
        raise QueryValidationError("Invalid keys: {}".format(", ".join(invalid_keys)))

    data = _apply_query_data_types(data)
    name = data.get("name")
    await _check_query_name(db, name)
    if await datasette.get_query(db.name, name) is not None:
        raise QueryValidationError("Query already exists")

    is_published = _as_bool(data.get("is_published"))
    is_write, derived, analysis = await _analyze_user_query(
        datasette,
        db,
        data.get("sql"),
        actor=request.actor,
        is_published=is_published,
    )
    if is_published and not await datasette.allowed(
        action="publish-query",
        resource=DatabaseResource(db.name),
        actor=request.actor,
    ):
        raise QueryValidationError("Permission denied: need publish-query", status=403)
    if not is_write and any(data.get(field) for field in _query_write_fields):
        raise QueryValidationError("Writable query fields require writable SQL")

    parameters = _coerce_query_parameters(
        data.get("parameters", data.get("params")),
        derived,
    )
    return {
        "name": name,
        "sql": data["sql"],
        "title": data.get("title"),
        "description": data.get("description"),
        "description_html": data.get("description_html"),
        "hide_sql": _as_bool(data.get("hide_sql")),
        "fragment": data.get("fragment"),
        "parameters": parameters,
        "is_write": is_write,
        "is_published": is_published,
        "source": "user",
        "owner_id": _actor_id(request.actor),
        "on_success_message": data.get("on_success_message"),
        "on_success_message_sql": data.get("on_success_message_sql"),
        "on_success_redirect": data.get("on_success_redirect"),
        "on_error_message": data.get("on_error_message"),
        "on_error_redirect": data.get("on_error_redirect"),
        "analysis": analysis,
    }


async def _prepare_query_update(datasette, request, db, existing, update):
    invalid_keys = set(update) - _query_update_fields
    if invalid_keys:
        raise QueryValidationError("Invalid keys: {}".format(", ".join(invalid_keys)))

    update = _apply_query_data_types(update)
    sql = update.get("sql", existing["sql"])
    is_published = update.get("is_published", existing["is_published"])
    query_is_write = existing["is_write"]
    derived = _derived_query_parameters(sql)
    parameters = None

    if "sql" in update:
        query_is_write, derived, _ = await _analyze_user_query(
            datasette,
            db,
            sql,
            actor=request.actor,
            is_published=is_published,
        )
    elif is_published and query_is_write:
        raise QueryValidationError("Writable queries cannot be published")
    if is_published and not existing["is_published"]:
        if not await datasette.allowed(
            action="publish-query",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            raise QueryValidationError(
                "Permission denied: need publish-query", status=403
            )

    if "parameters" in update or "params" in update:
        parameters = _coerce_query_parameters(
            update.get("parameters", update.get("params")),
            derived,
        )
    elif "sql" in update:
        parameters = derived

    if not query_is_write and any(update.get(field) for field in _query_write_fields):
        raise QueryValidationError("Writable query fields require writable SQL")

    field_values = {
        "sql": sql,
        "title": update.get("title"),
        "description": update.get("description"),
        "description_html": update.get("description_html"),
        "hide_sql": update.get("hide_sql"),
        "fragment": update.get("fragment"),
        "parameters": parameters,
        "is_write": query_is_write,
        "is_published": is_published,
        "on_success_message": update.get("on_success_message"),
        "on_success_message_sql": update.get("on_success_message_sql"),
        "on_success_redirect": update.get("on_success_redirect"),
        "on_error_message": update.get("on_error_message"),
        "on_error_redirect": update.get("on_error_redirect"),
    }
    update_kwargs = {}
    for field_name, value in field_values.items():
        if field_name in update:
            update_kwargs[field_name] = value
    if parameters is not None:
        update_kwargs["parameters"] = parameters
    if "sql" in update:
        update_kwargs["is_write"] = query_is_write
    return update_kwargs


class ExecuteWriteView(BaseView):
    name = "execute-write"
    has_json_alternate = False

    async def _render_form(
        self,
        request,
        db,
        *,
        sql="",
        parameter_values=None,
        analysis=None,
        analysis_error=None,
        execution_message=None,
        execution_ok=None,
        status=200,
    ):
        parameter_values = parameter_values or {}
        parameter_names = []
        analysis_rows = []
        if sql and analysis_error is None:
            try:
                parameter_names = _derived_query_parameters(sql)
                if analysis is None:
                    params = {parameter: "" for parameter in parameter_names}
                    analysis = await db.analyze_sql(sql, params)
                if _analysis_is_write(analysis):
                    analysis_rows = await _analysis_rows_with_permissions(
                        self.ds, analysis, request.actor
                    )
                else:
                    analysis_error = (
                        "Use /-/query for read-only SQL; "
                        "this endpoint only executes writes"
                    )
            except (QueryValidationError, sqlite3.DatabaseError) as ex:
                analysis_error = getattr(ex, "message", str(ex))

        response = await self.render(
            ["execute_write.html"],
            request,
            {
                "database": db.name,
                "database_color": db.color,
                "sql": sql,
                "parameter_names": parameter_names,
                "parameter_values": parameter_values,
                "analysis_error": analysis_error,
                "analysis_rows": analysis_rows,
                "execution_message": execution_message,
                "execution_ok": execution_ok,
                "execute_disabled": bool(
                    (not sql)
                    or analysis_error
                    or any(row["allowed"] is False for row in analysis_rows)
                ),
            },
        )
        response.status = status
        return _block_framing(response)

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        await self.ds.ensure_permission(
            action="execute-write-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        )
        return await self._render_form(
            request,
            db,
            sql=request.args.get("sql") or "",
        )

    async def post(self, request):
        db = await self.ds.resolve_database(request)
        if not await self.ds.allowed(
            action="execute-write-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            return _block_framing(
                _error(["Permission denied: need execute-write-sql"], 403)
            )
        if not db.is_mutable:
            return _block_framing(_error(["Database is immutable"], 403))

        data = {}
        is_json = request.headers.get("content-type", "").startswith("application/json")
        sql = ""
        provided_params = {}
        try:
            data, is_json = await _json_or_form_payload(request)
            sql, provided_params = _coerce_execute_write_payload(data, is_json)
            parameter_names, params, analysis = await _prepare_execute_write(
                self.ds, db, sql, provided_params, request.actor
            )
        except QueryValidationError as ex:
            if _wants_json(request, is_json, data):
                return _block_framing(_error([ex.message], ex.status))
            return await self._render_form(
                request,
                db,
                sql=sql or "",
                parameter_values=provided_params,
                analysis_error=ex.message,
                execution_message=ex.message,
                execution_ok=False,
                status=ex.status,
            )

        try:
            cursor = await db.execute_write(sql, params, request=request)
        except sqlite3.DatabaseError as ex:
            message = str(ex)
            if _wants_json(request, is_json, data):
                return _block_framing(_error([message], 400))
            return await self._render_form(
                request,
                db,
                sql=sql,
                parameter_values=params,
                analysis=analysis,
                execution_message=message,
                execution_ok=False,
                status=400,
            )

        message = "Query executed, {} row{} affected".format(
            cursor.rowcount, "" if cursor.rowcount == 1 else "s"
        )
        if _wants_json(request, is_json, data):
            return _block_framing(
                Response.json(
                    {
                        "ok": True,
                        "message": message,
                        "rowcount": cursor.rowcount,
                        "analysis": _analysis_rows(analysis),
                    }
                )
            )

        return await self._render_form(
            request,
            db,
            sql=sql,
            parameter_values={name: params.get(name, "") for name in parameter_names},
            analysis=analysis,
            execution_message=message,
            execution_ok=True,
        )


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
            limit = _query_list_limit(request.args.get("_size"))
            is_write = _as_optional_bool(request.args.get("is_write"), "is_write")
            is_published = _as_optional_bool(
                request.args.get("is_published"), "is_published"
            )
        except QueryValidationError as ex:
            return _error([ex.message], ex.status)

        page = await self.ds.list_queries(
            database,
            actor=request.actor,
            limit=limit,
            cursor=request.args.get("_next"),
            q=request.args.get("q") or None,
            is_write=is_write,
            is_published=is_published,
            source=request.args.get("source") or None,
            owner_id=request.args.get("owner_id") or None,
            include_private=True,
        )
        query_list_path = self.query_list_path(database)
        next_url = None
        if page["next"]:
            pairs = [
                (key, value)
                for key, value in parse_qsl(
                    request.query_string, keep_blank_values=True
                )
                if key != "_next"
            ]
            pairs.append(("_next", page["next"]))
            next_url = "{}?{}".format(
                query_list_path,
                urlencode(pairs),
            )

        data = {
            "ok": True,
            "database": database,
            "queries": page["queries"],
            "next": page["next"],
            "next_url": next_url,
            "has_more": page["has_more"],
            "limit": page["limit"],
            "query_list_path": query_list_path,
            "show_database": database is None,
            "filters": {
                "q": request.args.get("q") or "",
                "is_write": request.args.get("is_write") or "",
                "is_published": request.args.get("is_published") or "",
                "source": request.args.get("source") or "",
                "owner_id": request.args.get("owner_id") or "",
            },
        }
        if format_ == "json":
            return Response.json(data)
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

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        await self.ds.ensure_permission(
            action="execute-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        )
        await self.ds.ensure_permission(
            action="insert-query",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        )

        sql = request.args.get("sql") or ""
        analysis_error = None
        analysis_rows = []
        parameter_names = []
        if sql:
            try:
                parameter_names = _derived_query_parameters(sql)
                params = {parameter: "" for parameter in parameter_names}
                analysis = await db.analyze_sql(sql, params)
                analysis_rows = await _analysis_rows_with_permissions(
                    self.ds, analysis, request.actor
                )
            except (QueryValidationError, sqlite3.DatabaseError) as ex:
                analysis_error = getattr(ex, "message", str(ex))

        return await self.render(
            ["query_create.html"],
            request,
            {
                "database": db.name,
                "database_color": db.color,
                "sql": sql,
                "parameter_names": parameter_names,
                "can_publish": await self.ds.allowed(
                    action="publish-query",
                    resource=DatabaseResource(db.name),
                    actor=request.actor,
                ),
                "analysis_error": analysis_error,
                "analysis_rows": analysis_rows,
                "analysis_is_write": bool(
                    analysis_rows
                    and any(row["required_permission"] for row in analysis_rows)
                ),
                "save_disabled": bool(
                    analysis_error
                    or any(row["allowed"] is False for row in analysis_rows)
                ),
            },
        )


class QueryInsertView(BaseView):
    name = "query-insert"

    async def post(self, request):
        db = await self.ds.resolve_database(request)
        if not await self.ds.allowed(
            action="execute-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            return _error(["Permission denied: need execute-sql"], 403)
        if not await self.ds.allowed(
            action="insert-query",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            return _error(["Permission denied: need insert-query"], 403)

        try:
            data, is_json = await _json_or_form_payload(request)
            if not isinstance(data, dict):
                raise QueryValidationError("JSON must be a dictionary")
            query_data = data.get("query") if is_json else data
            if not isinstance(query_data, dict):
                raise QueryValidationError("JSON must contain a query dictionary")
            prepared = await _prepare_query_create(self.ds, request, db, query_data)
        except QueryValidationError as ex:
            return _error([ex.message], ex.status)

        prepared.pop("analysis")
        name = prepared.pop("name")
        try:
            await self.ds.add_query(db.name, name, replace=False, **prepared)
        except sqlite3.IntegrityError as ex:
            return _error([str(ex)], 400)

        query = await self.ds.get_query(db.name, name)
        if is_json:
            return Response.json({"ok": True, "query": query}, status=201)
        self.ds.add_message(request, "Query saved", self.ds.INFO)
        return Response.redirect(self.ds.urls.path(self.ds.urls.table(db.name, name)))


class QueryDefinitionView(BaseView):
    name = "query-definition"

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        query_name = tilde_decode(request.url_vars["query"])
        query = await self.ds.get_query(db.name, query_name)
        if query is None:
            return _error(["Query not found: {}".format(query_name)], 404)
        if not await self.ds.allowed(
            action="view-query",
            resource=QueryResource(db.name, query_name),
            actor=request.actor,
        ):
            return _error(["Permission denied"], 403)
        return Response.json({"ok": True, "query": query})


class QueryUpdateView(BaseView):
    name = "query-update"

    async def post(self, request):
        db = await self.ds.resolve_database(request)
        query_name = tilde_decode(request.url_vars["query"])
        existing = await self.ds.get_query(db.name, query_name)
        if existing is None:
            return _error(["Query not found: {}".format(query_name)], 404)
        if not await self.ds.allowed(
            action="update-query",
            resource=QueryResource(db.name, query_name),
            actor=request.actor,
        ):
            return _error(["Permission denied: need update-query"], 403)

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
            return _error([ex.message], ex.status)

        await self.ds.update_query(db.name, query_name, **update_kwargs)
        if data.get("return"):
            return Response.json(
                {
                    "ok": True,
                    "query": await self.ds.get_query(db.name, query_name),
                }
            )
        return Response.json({"ok": True})


class QueryDeleteView(BaseView):
    name = "query-delete"

    async def post(self, request):
        db = await self.ds.resolve_database(request)
        query_name = tilde_decode(request.url_vars["query"])
        existing = await self.ds.get_query(db.name, query_name)
        if existing is None:
            return _error(["Query not found: {}".format(query_name)], 404)
        if not await self.ds.allowed(
            action="delete-query",
            resource=QueryResource(db.name, query_name),
            actor=request.actor,
        ):
            return _error(["Permission denied: need delete-query"], 403)
        await self.ds.remove_query(db.name, query_name)
        return Response.json({"ok": True})


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

        if not await datasette.allowed(
            action="view-query",
            resource=QueryResource(database=db.name, query=canned_query["name"]),
            actor=request.actor,
        ):
            raise Forbidden("You do not have permission to view this query")

        if canned_query.get("write") and canned_query.get("source") == "user":
            await datasette.ensure_query_write_permissions(
                db.name, canned_query["sql"], actor=request.actor
            )

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
            cursor = await db.execute_write(
                canned_query["sql"], params_for_query, request=request
            )
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

            environment = datasette.get_jinja_environment(request)
            template = environment.select_template(templates)
            alternate_url_json = datasette.absolute_url(
                request,
                datasette.urls.path(path_with_format(request=request, format="json")),
            )
            data = {}
            headers.update(
                {
                    "Link": '<{}>; rel="alternate"; type="application/json+datasette"'.format(
                        alternate_url_json
                    )
                }
            )
            metadata = await datasette.get_database_metadata(database)

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

            allow_execute_sql = await datasette.allowed(
                action="execute-sql",
                resource=DatabaseResource(database=database),
                actor=request.actor,
            )
            allow_insert_query = await datasette.allowed(
                action="insert-query",
                resource=DatabaseResource(database=database),
                actor=request.actor,
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
                not canned_query
                and allow_execute_sql
                and allow_insert_query
                and is_validated_sql
                and ":_" not in sql
            ):
                save_query_url = (
                    datasette.urls.database(database)
                    + "/-/queries/-/create?"
                    + urlencode({"sql": sql})
                )

            async def query_actions():
                query_actions = []
                for hook in pm.hook.query_actions(
                    datasette=datasette,
                    actor=request.actor,
                    database=database,
                    query_name=canned_query["name"] if canned_query else None,
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
                        top_query=make_slot_function(
                            "top_query", datasette, request, database=database, sql=sql
                        ),
                        top_canned_query=make_slot_function(
                            "top_canned_query",
                            datasette,
                            request,
                            database=database,
                            query_name=canned_query["name"] if canned_query else None,
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
            schema = await db.execute_write_fn(create_table, request=request)
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
