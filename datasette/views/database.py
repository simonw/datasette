import os
import hashlib
import itertools
import jinja2
import json
from urllib.parse import parse_qsl, urlencode

from datasette.utils import (
    await_me_maybe,
    check_visibility,
    to_css_class,
    validate_sql_select,
    is_url,
    path_with_added_args,
    path_with_format,
    path_with_removed_args,
    InvalidSql,
)
from datasette.utils.asgi import AsgiFileDownload, Response, Forbidden
from datasette.plugins import pm

from .base import DatasetteError, DataView


class DatabaseView(DataView):
    name = "database"

    async def data(self, request, database, hash, default_labels=False, _size=None):
        await self.check_permissions(
            request,
            [
                ("view-database", database),
                "view-instance",
            ],
        )
        metadata = (self.ds.metadata("databases") or {}).get(database, {})
        self.ds.update_with_inherited_metadata(metadata)

        if request.args.get("sql"):
            sql = request.args.get("sql")
            validate_sql_select(sql)
            return await QueryView(self.ds).data(
                request, database, hash, sql, _size=_size, metadata=metadata
            )

        db = self.ds.databases[database]

        table_counts = await db.table_counts(5)
        hidden_table_names = set(await db.hidden_table_names())
        all_foreign_keys = await db.get_all_foreign_keys()

        views = []
        for view_name in await db.view_names():
            visible, private = await check_visibility(
                self.ds,
                request.actor,
                "view-table",
                (database, view_name),
            )
            if visible:
                views.append(
                    {
                        "name": view_name,
                        "private": private,
                    }
                )

        tables = []
        for table in table_counts:
            visible, private = await check_visibility(
                self.ds,
                request.actor,
                "view-table",
                (database, table),
            )
            if not visible:
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
                    "private": private,
                }
            )

        tables.sort(key=lambda t: (t["hidden"], t["name"]))
        canned_queries = []
        for query in (
            await self.ds.get_canned_queries(database, request.actor)
        ).values():
            visible, private = await check_visibility(
                self.ds,
                request.actor,
                "view-query",
                (database, query["name"]),
            )
            if visible:
                canned_queries.append(dict(query, private=private))

        async def database_actions():
            links = []
            for hook in pm.hook.database_actions(
                datasette=self.ds,
                database=database,
                actor=request.actor,
            ):
                extra_links = await await_me_maybe(hook)
                if extra_links:
                    links.extend(extra_links)
            return links

        return (
            {
                "database": database,
                "path": self.ds.urls.database(database),
                "size": db.size,
                "tables": tables,
                "hidden_count": len([t for t in tables if t["hidden"]]),
                "views": views,
                "queries": canned_queries,
                "private": not await self.ds.permission_allowed(
                    None, "view-database", database, default=True
                ),
                "allow_execute_sql": await self.ds.permission_allowed(
                    request.actor, "execute-sql", database, default=True
                ),
            },
            {
                "database_actions": database_actions,
                "show_hidden": request.args.get("_show_hidden"),
                "editable": True,
                "metadata": metadata,
                "allow_download": self.ds.setting("allow_download")
                and not db.is_mutable
                and database != ":memory:",
            },
            (f"database-{to_css_class(database)}.html", "database.html"),
        )


class DatabaseDownload(DataView):
    name = "database_download"

    async def view_get(self, request, database, hash, correct_hash_present, **kwargs):
        await self.check_permissions(
            request,
            [
                ("view-database-download", database),
                ("view-database", database),
                "view-instance",
            ],
        )
        if database not in self.ds.databases:
            raise DatasetteError("Invalid database", status=404)
        db = self.ds.databases[database]
        if db.is_memory:
            raise DatasetteError("Cannot download :memory: database", status=404)
        if not self.ds.setting("allow_download") or db.is_mutable:
            raise Forbidden("Database download is forbidden")
        if not db.path:
            raise DatasetteError("Cannot download database", status=404)
        filepath = db.path
        headers = {}
        if self.ds.cors:
            headers["Access-Control-Allow-Origin"] = "*"
        headers["Transfer-Encoding"] = "chunked"
        return AsgiFileDownload(
            filepath,
            filename=os.path.basename(filepath),
            content_type="application/octet-stream",
            headers=headers,
        )


class QueryView(DataView):
    async def data(
        self,
        request,
        database,
        hash,
        sql,
        editable=True,
        canned_query=None,
        metadata=None,
        _size=None,
        named_parameters=None,
        write=False,
    ):
        params = {key: request.args.get(key) for key in request.args}
        if "sql" in params:
            params.pop("sql")
        if "_shape" in params:
            params.pop("_shape")

        private = False
        if canned_query:
            # Respect canned query permissions
            await self.check_permissions(
                request,
                [
                    ("view-query", (database, canned_query)),
                    ("view-database", database),
                    "view-instance",
                ],
            )
            private = not await self.ds.permission_allowed(
                None, "view-query", (database, canned_query), default=True
            )
        else:
            await self.check_permission(request, "execute-sql", database)

        # Extract any :named parameters
        named_parameters = named_parameters or self.re_named_parameter.findall(sql)
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

        # Execute query - as write or as read
        if write:
            if request.method == "POST":
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
                        sql, params_for_query, block=True
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
            results = await self.ds.execute(
                database, sql, params_for_query, truncate=True, **extra_args
            )
            columns = [r[0] for r in results.description]

        if canned_query:
            templates.insert(
                0,
                f"query-{to_css_class(database)}-{to_css_class(canned_query)}.html",
            )

        allow_execute_sql = await self.ds.permission_allowed(
            request.actor, "execute-sql", database, default=True
        )

        async def extra_template():
            display_rows = []
            for row in results.rows:
                display_row = []
                for column, value in zip(results.columns, row):
                    display_value = value
                    # Let the plugins have a go
                    # pylint: disable=no-member
                    plugin_value = pm.hook.render_cell(
                        value=value,
                        column=column,
                        table=None,
                        database=database,
                        datasette=self.ds,
                    )
                    if plugin_value is not None:
                        display_value = plugin_value
                    else:
                        if value in ("", None):
                            display_value = jinja2.Markup("&nbsp;")
                        elif is_url(str(display_value).strip()):
                            display_value = jinja2.Markup(
                                '<a href="{url}">{url}</a>'.format(
                                    url=jinja2.escape(value.strip())
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
                            display_value = jinja2.Markup(
                                '<a class="blob-download" href="{}">&lt;Binary:&nbsp;{}&nbsp;byte{}&gt;</a>'.format(
                                    blob_url,
                                    len(display_value),
                                    "" if len(value) == 1 else "s",
                                )
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
            return {
                "display_rows": display_rows,
                "custom_sql": True,
                "named_parameter_values": named_parameter_values,
                "editable": editable,
                "canned_query": canned_query,
                "edit_sql_url": edit_sql_url,
                "metadata": metadata,
                "config": self.ds.config_dict(),
                "request": request,
                "path_with_added_args": path_with_added_args,
                "path_with_removed_args": path_with_removed_args,
                "hide_sql": "_hide_sql" in params,
            }

        return (
            {
                "database": database,
                "query_name": canned_query,
                "rows": results.rows,
                "truncated": results.truncated,
                "columns": columns,
                "query": {"sql": sql, "params": params},
                "private": private,
                "allow_execute_sql": allow_execute_sql,
            },
            extra_template,
            templates,
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
