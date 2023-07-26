from asyncinject import Registry
import os
import hashlib
import itertools
import json
import markupsafe
from urllib.parse import parse_qsl, urlencode
import re
import sqlite_utils

from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
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

from .base import BaseView, DatasetteError, DataView, _error


async def database_view(request, datasette):
    format_ = request.url_vars.get("format") or "html"
    if format_ not in ("html", "json"):
        raise NotFound("Invalid format: {}".format(format_))

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
        validate_sql_select(sql)
        return await query_view(request, datasette)

    metadata = (datasette.metadata("databases") or {}).get(database, {})
    datasette.update_with_inherited_metadata(metadata)

    table_counts = await db.table_counts(5)
    hidden_table_names = set(await db.hidden_table_names())
    all_foreign_keys = await db.get_all_foreign_keys()

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

    tables = []
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
    canned_queries = []
    for query in (await datasette.get_canned_queries(database, request.actor)).values():
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
        return Response.json(json_data)

    assert format_ == "html"
    context = {
        **json_data,
        "database_actions": database_actions,
        "show_hidden": request.args.get("_show_hidden"),
        "editable": True,
        "metadata": metadata,
        "allow_download": datasette.setting("allow_download")
        and not db.is_mutable
        and not db.is_memory,
        "attached_databases": attached_databases,
        "database_color": lambda _: "#ff0000",
    }
    templates = (f"database-{to_css_class(database)}.html", "database.html")
    return Response.html(
        await datasette.render_template(templates, context, request=request)
    )


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


async def query_view(
    request,
    datasette,
    # canned_query=None,
    # _size=None,
    # named_parameters=None,
    # write=False,
):
    db = await datasette.resolve_database(request)
    database = db.name
    # TODO: Why do I do this? Is it to eliminate multi-args?
    # It's going to break ?_extra=...&_extra=...
    params = {key: request.args.get(key) for key in request.args}
    sql = ""
    if "sql" in params:
        sql = params.pop("sql")

    # TODO: Behave differently for canned query here:
    await datasette.ensure_permissions(request.actor, [("execute-sql", database)])

    _shape = None
    if "_shape" in params:
        _shape = params.pop("_shape")

    async def _results(_sql, _params):
        # Returns (results, error (can be None))
        try:
            results = await db.execute(_sql, _params, truncate=True)
            return results, None
        except Exception as e:
            return None, e

    async def shape_arrays(_results):
        results, error = _results
        if error:
            return {"ok": False, "error": str(error)}
        return {
            "ok": True,
            "rows": [list(r) for r in results.rows],
            "truncated": results.truncated,
        }

    async def shape_objects(_results):
        results, error = _results
        if error:
            return {"ok": False, "error": str(error)}
        return {
            "ok": True,
            "rows": [dict(r) for r in results.rows],
            "truncated": results.truncated,
        }

    async def shape_array(_results):
        results, error = _results
        if error:
            return {"ok": False, "error": str(error)}
        return [dict(r) for r in results.rows]

    async def shape_arrayfirst(_results):
        results, error = _results
        if error:
            return {"ok": False, "error": str(error)}
        return [r[0] for r in results.rows]

    shape_fn = {
        "arrays": shape_arrays,
        "objects": shape_objects,
        "array": shape_array,
        "arrayfirst": shape_arrayfirst,
    }[_shape or "objects"]

    registry = Registry.from_dict(
        {
            "_results": _results,
            "_shape": shape_fn,
        },
        parallel=False,
    )

    results = await registry.resolve_multi(
        ["_shape"],
        results={
            "_sql": sql,
            "_params": params,
        },
    )

    # If "shape" does not include "rows" we return that as the response
    # because it's likely [{...}] or similar, with no room to attach extras
    if "rows" not in results["_shape"]:
        return Response.json(results["_shape"])

    output = results["_shape"]
    # Include the extras:
    output.update(dict((k, v) for k, v in results.items() if not k.startswith("_")))
    return Response.json(output)


class QueryView(DataView):
    async def data(
        self,
        request,
        sql,
        editable=True,
        canned_query=None,
        metadata=None,
        _size=None,
        named_parameters=None,
        write=False,
        default_labels=None,
    ):
        db = await self.ds.resolve_database(request)
        database = db.name
        params = {key: request.args.get(key) for key in request.args}
        if "sql" in params:
            params.pop("sql")
        if "_shape" in params:
            params.pop("_shape")

        private = False
        if canned_query:
            # Respect canned query permissions
            visible, private = await self.ds.check_visibility(
                request.actor,
                permissions=[
                    ("view-query", (database, canned_query)),
                    ("view-database", database),
                    "view-instance",
                ],
            )
            if not visible:
                raise Forbidden("You do not have permission to view this query")

        else:
            await self.ds.ensure_permissions(request.actor, [("execute-sql", database)])

        # Extract any :named parameters
        named_parameters = named_parameters or await derive_named_parameters(
            self.ds.get_database(database), sql
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
        if _size:
            extra_args["page_size"] = _size

        templates = [f"query-{to_css_class(database)}.html", "query.html"]
        if canned_query:
            templates.insert(
                0,
                f"query-{to_css_class(database)}-{to_css_class(canned_query)}.html",
            )

        query_error = None

        # Execute query - as write or as read
        if write:
            if request.method == "POST":
                # If database is immutable, return an error
                if not db.is_mutable:
                    raise Forbidden("Database is immutable")
                body = await request.post_body()
                body = body.decode("utf-8").strip()
                if body.startswith("{") and body.endswith("}"):
                    params = json.loads(body)
                    # But we want key=value strings
                    for key, value in params.items():
                        params[key] = str(value)
                else:
                    params = dict(parse_qsl(body, keep_blank_values=True))
                # Should we return JSON?
                should_return_json = (
                    request.headers.get("accept") == "application/json"
                    or request.args.get("_json")
                    or params.get("_json")
                )
                if canned_query:
                    params_for_query = MagicParameters(params, request, self.ds)
                else:
                    params_for_query = params
                ok = None
                try:
                    cursor = await self.ds.databases[database].execute_write(
                        sql, params_for_query
                    )
                    message = metadata.get(
                        "on_success_message"
                    ) or "Query executed, {} row{} affected".format(
                        cursor.rowcount, "" if cursor.rowcount == 1 else "s"
                    )
                    message_type = self.ds.INFO
                    redirect_url = metadata.get("on_success_redirect")
                    ok = True
                except Exception as e:
                    message = metadata.get("on_error_message") or str(e)
                    message_type = self.ds.ERROR
                    redirect_url = metadata.get("on_error_redirect")
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
                    self.ds.add_message(request, message, message_type)
                    return self.redirect(request, redirect_url or request.path)
            else:

                async def extra_template():
                    return {
                        "request": request,
                        "db_is_immutable": not db.is_mutable,
                        "path_with_added_args": path_with_added_args,
                        "path_with_removed_args": path_with_removed_args,
                        "named_parameter_values": named_parameter_values,
                        "canned_query": canned_query,
                        "success_message": request.args.get("_success") or "",
                        "canned_write": True,
                    }

                return (
                    {
                        "database": database,
                        "rows": [],
                        "truncated": False,
                        "columns": [],
                        "query": {"sql": sql, "params": params},
                        "private": private,
                    },
                    extra_template,
                    templates,
                )
        else:  # Not a write
            if canned_query:
                params_for_query = MagicParameters(params, request, self.ds)
            else:
                params_for_query = params
            try:
                results = await self.ds.execute(
                    database, sql, params_for_query, truncate=True, **extra_args
                )
                columns = [r[0] for r in results.description]
            except sqlite3.DatabaseError as e:
                query_error = e
                results = None
                columns = []

        allow_execute_sql = await self.ds.permission_allowed(
            request.actor, "execute-sql", database
        )

        async def extra_template():
            display_rows = []
            truncate_cells = self.ds.setting("truncate_cells_html")
            for row in results.rows if results else []:
                display_row = []
                for column, value in zip(results.columns, row):
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
                        datasette=self.ds,
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
                                    "_blob_hash": hashlib.sha256(
                                        display_value
                                    ).hexdigest(),
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
                                display_value = (
                                    display_value[:truncate_cells] + "\u2026"
                                )
                    display_row.append(display_value)
                display_rows.append(display_row)

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
                    self.ds.urls.database(database)
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

            show_hide_hidden = ""
            if metadata.get("hide_sql"):
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
            return {
                "display_rows": display_rows,
                "custom_sql": True,
                "named_parameter_values": named_parameter_values,
                "editable": editable,
                "canned_query": canned_query,
                "edit_sql_url": edit_sql_url,
                "metadata": metadata,
                "settings": self.ds.settings_dict(),
                "request": request,
                "show_hide_link": self.ds.urls.path(show_hide_link),
                "show_hide_text": show_hide_text,
                "show_hide_hidden": markupsafe.Markup(show_hide_hidden),
                "hide_sql": hide_sql,
                "table_columns": await _table_columns(self.ds, database)
                if allow_execute_sql
                else {},
            }

        return (
            {
                "ok": not query_error,
                "database": database,
                "query_name": canned_query,
                "rows": results.rows if results else [],
                "truncated": results.truncated if results else False,
                "columns": columns,
                "query": {"sql": sql, "params": params},
                "error": str(query_error) if query_error else None,
                "private": private,
                "allow_execute_sql": allow_execute_sql,
            },
            extra_template,
            templates,
            400 if query_error else 200,
        )


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
    internal = datasette.get_database("_internal")
    result = await internal.execute(
        "select table_name, name from columns where database_name = ?",
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
