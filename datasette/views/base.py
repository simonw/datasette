import asyncio
import csv
import hashlib
import re
import sys
import time
import urllib

import pint

from datasette import __version__
from datasette.plugins import pm
from datasette.database import QueryInterrupted
from datasette.utils import (
    await_me_maybe,
    InvalidSql,
    LimitedWriter,
    call_with_supported_arguments,
    path_from_row_pks,
    path_with_added_args,
    path_with_removed_args,
    path_with_format,
    resolve_table_and_format,
    sqlite3,
    HASH_LENGTH,
)
from datasette.utils.asgi import (
    AsgiStream,
    Forbidden,
    NotFound,
    Response,
    BadRequest,
)

ureg = pint.UnitRegistry()


class DatasetteError(Exception):
    def __init__(
        self,
        message,
        title=None,
        error_dict=None,
        status=500,
        template=None,
        message_is_html=False,
    ):
        self.message = message
        self.title = title
        self.error_dict = error_dict or {}
        self.status = status
        self.message_is_html = message_is_html


class BaseView:
    ds = None

    def __init__(self, datasette):
        self.ds = datasette

    async def head(self, *args, **kwargs):
        response = await self.get(*args, **kwargs)
        response.body = b""
        return response

    async def check_permission(self, request, action, resource=None):
        ok = await self.ds.permission_allowed(
            request.actor,
            action,
            resource=resource,
            default=True,
        )
        if not ok:
            raise Forbidden(action)

    async def check_permissions(self, request, permissions):
        """permissions is a list of (action, resource) tuples or 'action' strings"""
        for permission in permissions:
            if isinstance(permission, str):
                action = permission
                resource = None
            elif isinstance(permission, (tuple, list)) and len(permission) == 2:
                action, resource = permission
            else:
                assert (
                    False
                ), "permission should be string or tuple of two items: {}".format(
                    repr(permission)
                )
            ok = await self.ds.permission_allowed(
                request.actor,
                action,
                resource=resource,
                default=None,
            )
            if ok is not None:
                if ok:
                    return
                else:
                    raise Forbidden(action)

    def database_color(self, database):
        return "ff0000"

    async def options(self, request, *args, **kwargs):
        return Response.text("Method not allowed", status=405)

    async def put(self, request, *args, **kwargs):
        return Response.text("Method not allowed", status=405)

    async def patch(self, request, *args, **kwargs):
        return Response.text("Method not allowed", status=405)

    async def delete(self, request, *args, **kwargs):
        return Response.text("Method not allowed", status=405)

    async def dispatch_request(self, request, *args, **kwargs):
        if self.ds:
            await self.ds.refresh_schemas()
        handler = getattr(self, request.method.lower(), None)
        return await handler(request, *args, **kwargs)

    async def render(self, templates, request, context=None):
        context = context or {}
        template = self.ds.jinja_env.select_template(templates)
        template_context = {
            **context,
            **{
                "database_color": self.database_color,
                "select_templates": [
                    f"{'*' if template_name == template.name else ''}{template_name}"
                    for template_name in templates
                ],
            },
        }
        return Response.html(
            await self.ds.render_template(
                template, template_context, request=request, view_name=self.name
            )
        )

    @classmethod
    def as_view(cls, *class_args, **class_kwargs):
        async def view(request, send):
            self = view.view_class(*class_args, **class_kwargs)
            return await self.dispatch_request(
                request, **request.scope["url_route"]["kwargs"]
            )

        view.view_class = cls
        view.__doc__ = cls.__doc__
        view.__module__ = cls.__module__
        view.__name__ = cls.__name__
        return view


class DataView(BaseView):
    name = ""
    re_named_parameter = re.compile(":([a-zA-Z0-9_]+)")

    async def options(self, request, *args, **kwargs):
        r = Response.text("ok")
        if self.ds.cors:
            r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    def redirect(self, request, path, forward_querystring=True, remove_args=None):
        if request.query_string and "?" not in path and forward_querystring:
            path = f"{path}?{request.query_string}"
        if remove_args:
            path = path_with_removed_args(request, remove_args, path=path)
        r = Response.redirect(path)
        r.headers["Link"] = f"<{path}>; rel=preload"
        if self.ds.cors:
            r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    async def data(self, request, database, hash, **kwargs):
        raise NotImplementedError

    async def resolve_db_name(self, request, db_name, **kwargs):
        hash = None
        name = None
        db_name = urllib.parse.unquote_plus(db_name)
        if db_name not in self.ds.databases and "-" in db_name:
            # No matching DB found, maybe it's a name-hash?
            name_bit, hash_bit = db_name.rsplit("-", 1)
            if name_bit not in self.ds.databases:
                raise NotFound(f"Database not found: {name}")
            else:
                name = name_bit
                hash = hash_bit
        else:
            name = db_name

        try:
            db = self.ds.databases[name]
        except KeyError:
            raise NotFound(f"Database not found: {name}")

        # Verify the hash
        expected = "000"
        if db.hash is not None:
            expected = db.hash[:HASH_LENGTH]
        correct_hash_provided = expected == hash

        if not correct_hash_provided:
            if "table_and_format" in kwargs:

                async def async_table_exists(t):
                    return await db.table_exists(t)

                table, _format = await resolve_table_and_format(
                    table_and_format=urllib.parse.unquote_plus(
                        kwargs["table_and_format"]
                    ),
                    table_exists=async_table_exists,
                    allowed_formats=self.ds.renderers.keys(),
                )
                kwargs["table"] = table
                if _format:
                    kwargs["as_format"] = f".{_format}"
            elif kwargs.get("table"):
                kwargs["table"] = urllib.parse.unquote_plus(kwargs["table"])

            should_redirect = self.ds.urls.path(f"{name}-{expected}")
            if kwargs.get("table"):
                should_redirect += "/" + urllib.parse.quote_plus(kwargs["table"])
            if kwargs.get("pk_path"):
                should_redirect += "/" + kwargs["pk_path"]
            if kwargs.get("as_format"):
                should_redirect += kwargs["as_format"]
            if kwargs.get("as_db"):
                should_redirect += kwargs["as_db"]

            if (
                (self.ds.setting("hash_urls") or "_hash" in request.args)
                and
                # Redirect only if database is immutable
                not self.ds.databases[name].is_mutable
            ):
                return name, expected, correct_hash_provided, should_redirect

        return name, expected, correct_hash_provided, None

    def get_templates(self, database, table=None):
        assert NotImplemented

    async def get(self, request, db_name, **kwargs):
        (
            database,
            hash,
            correct_hash_provided,
            should_redirect,
        ) = await self.resolve_db_name(request, db_name, **kwargs)
        if should_redirect:
            return self.redirect(request, should_redirect, remove_args={"_hash"})

        return await self.view_get(
            request, database, hash, correct_hash_provided, **kwargs
        )

    async def as_csv(self, request, database, hash, **kwargs):
        stream = request.args.get("_stream")
        if stream:
            # Some quick sanity checks
            if not self.ds.setting("allow_csv_stream"):
                raise BadRequest("CSV streaming is disabled")
            if request.args.get("_next"):
                raise BadRequest("_next not allowed for CSV streaming")
            kwargs["_size"] = "max"
        # Fetch the first page
        try:
            response_or_template_contexts = await self.data(
                request, database, hash, **kwargs
            )
            if isinstance(response_or_template_contexts, Response):
                return response_or_template_contexts
            else:
                data, _, _ = response_or_template_contexts
        except (sqlite3.OperationalError, InvalidSql) as e:
            raise DatasetteError(str(e), title="Invalid SQL", status=400)

        except sqlite3.OperationalError as e:
            raise DatasetteError(str(e))

        except DatasetteError:
            raise

        # Convert rows and columns to CSV
        headings = data["columns"]
        # if there are expanded_columns we need to add additional headings
        expanded_columns = set(data.get("expanded_columns") or [])
        if expanded_columns:
            headings = []
            for column in data["columns"]:
                headings.append(column)
                if column in expanded_columns:
                    headings.append(f"{column}_label")

        async def stream_fn(r):
            nonlocal data
            writer = csv.writer(LimitedWriter(r, self.ds.setting("max_csv_mb")))
            first = True
            next = None
            while first or (next and stream):
                try:
                    if next:
                        kwargs["_next"] = next
                    if not first:
                        data, _, _ = await self.data(request, database, hash, **kwargs)
                    if first:
                        if request.args.get("_header") != "off":
                            await writer.writerow(headings)
                        first = False
                    next = data.get("next")
                    for row in data["rows"]:
                        if any(isinstance(r, bytes) for r in row):
                            new_row = []
                            for column, cell in zip(headings, row):
                                if isinstance(cell, bytes):
                                    # If this is a table page, use .urls.row_blob()
                                    if data.get("table"):
                                        pks = data.get("primary_keys") or []
                                        cell = self.ds.absolute_url(
                                            request,
                                            self.ds.urls.row_blob(
                                                database,
                                                data["table"],
                                                path_from_row_pks(row, pks, not pks),
                                                column,
                                            ),
                                        )
                                    else:
                                        # Otherwise generate URL for this query
                                        cell = self.ds.absolute_url(
                                            request,
                                            path_with_format(
                                                request=request,
                                                format="blob",
                                                extra_qs={
                                                    "_blob_column": column,
                                                    "_blob_hash": hashlib.sha256(
                                                        cell
                                                    ).hexdigest(),
                                                },
                                                replace_format="csv",
                                            ),
                                        )
                                new_row.append(cell)
                            row = new_row
                        if not expanded_columns:
                            # Simple path
                            await writer.writerow(row)
                        else:
                            # Look for {"value": "label": } dicts and expand
                            new_row = []
                            for heading, cell in zip(data["columns"], row):
                                if heading in expanded_columns:
                                    if cell is None:
                                        new_row.extend(("", ""))
                                    else:
                                        assert isinstance(cell, dict)
                                        new_row.append(cell["value"])
                                        new_row.append(cell["label"])
                                else:
                                    new_row.append(cell)
                            await writer.writerow(new_row)
                except Exception as e:
                    sys.stderr.write("Caught this error: {}\n".format(e))
                    sys.stderr.flush()
                    await r.write(str(e))
                    return

        content_type = "text/plain; charset=utf-8"
        headers = {}
        if self.ds.cors:
            headers["Access-Control-Allow-Origin"] = "*"
        if request.args.get("_dl", None):
            content_type = "text/csv; charset=utf-8"
            disposition = 'attachment; filename="{}.csv"'.format(
                kwargs.get("table", database)
            )
            headers["content-disposition"] = disposition

        return AsgiStream(stream_fn, headers=headers, content_type=content_type)

    async def get_format(self, request, database, args):
        """Determine the format of the response from the request, from URL
        parameters or from a file extension.

        `args` is a dict of the path components parsed from the URL by the router.
        """
        # If ?_format= is provided, use that as the format
        _format = request.args.get("_format", None)
        if not _format:
            _format = (args.pop("as_format", None) or "").lstrip(".")
        else:
            args.pop("as_format", None)
        if "table_and_format" in args:
            db = self.ds.databases[database]

            async def async_table_exists(t):
                return await db.table_exists(t)

            table, _ext_format = await resolve_table_and_format(
                table_and_format=urllib.parse.unquote_plus(args["table_and_format"]),
                table_exists=async_table_exists,
                allowed_formats=self.ds.renderers.keys(),
            )
            _format = _format or _ext_format
            args["table"] = table
            del args["table_and_format"]
        elif "table" in args:
            args["table"] = urllib.parse.unquote_plus(args["table"])
        return _format, args

    async def view_get(self, request, database, hash, correct_hash_provided, **kwargs):
        _format, kwargs = await self.get_format(request, database, kwargs)

        if _format == "csv":
            return await self.as_csv(request, database, hash, **kwargs)

        if _format is None:
            # HTML views default to expanding all foreign key labels
            kwargs["default_labels"] = True

        extra_template_data = {}
        start = time.perf_counter()
        status_code = 200
        templates = []
        try:
            response_or_template_contexts = await self.data(
                request, database, hash, **kwargs
            )
            if isinstance(response_or_template_contexts, Response):
                return response_or_template_contexts

            else:
                data, extra_template_data, templates = response_or_template_contexts
        except QueryInterrupted:
            raise DatasetteError(
                """
                SQL query took too long. The time limit is controlled by the
                <a href="https://docs.datasette.io/en/stable/config.html#sql-time-limit-ms">sql_time_limit_ms</a>
                configuration option.
            """,
                title="SQL Interrupted",
                status=400,
                message_is_html=True,
            )
        except (sqlite3.OperationalError, InvalidSql) as e:
            raise DatasetteError(str(e), title="Invalid SQL", status=400)

        except sqlite3.OperationalError as e:
            raise DatasetteError(str(e))

        except DatasetteError:
            raise

        end = time.perf_counter()
        data["query_ms"] = (end - start) * 1000
        for key in ("source", "source_url", "license", "license_url"):
            value = self.ds.metadata(key)
            if value:
                data[key] = value

        # Special case for .jsono extension - redirect to _shape=objects
        if _format == "jsono":
            return self.redirect(
                request,
                path_with_added_args(
                    request,
                    {"_shape": "objects"},
                    path=request.path.rsplit(".jsono", 1)[0] + ".json",
                ),
                forward_querystring=False,
            )

        if _format in self.ds.renderers.keys():
            # Dispatch request to the correct output format renderer
            # (CSV is not handled here due to streaming)
            result = call_with_supported_arguments(
                self.ds.renderers[_format][0],
                datasette=self.ds,
                columns=data.get("columns") or [],
                rows=data.get("rows") or [],
                sql=data.get("query", {}).get("sql", None),
                query_name=data.get("query_name"),
                database=database,
                table=data.get("table"),
                request=request,
                view_name=self.name,
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
                    status=result.get("status_code", 200),
                    content_type=result.get("content_type", "text/plain"),
                    headers=result.get("headers"),
                )
            elif isinstance(result, Response):
                r = result
            else:
                assert False, f"{result} should be dict or Response"
        else:
            extras = {}
            if callable(extra_template_data):
                extras = extra_template_data()
                if asyncio.iscoroutine(extras):
                    extras = await extras
            else:
                extras = extra_template_data
            url_labels_extra = {}
            if data.get("expandable_columns"):
                url_labels_extra = {"_labels": "on"}

            renderers = {}
            for key, (_, can_render) in self.ds.renderers.items():
                it_can_render = call_with_supported_arguments(
                    can_render,
                    datasette=self.ds,
                    columns=data.get("columns") or [],
                    rows=data.get("rows") or [],
                    sql=data.get("query", {}).get("sql", None),
                    query_name=data.get("query_name"),
                    database=database,
                    table=data.get("table"),
                    request=request,
                    view_name=self.name,
                )
                it_can_render = await await_me_maybe(it_can_render)
                if it_can_render:
                    renderers[key] = path_with_format(
                        request=request, format=key, extra_qs={**url_labels_extra}
                    )

            url_csv_args = {"_size": "max", **url_labels_extra}
            url_csv = path_with_format(
                request=request, format="csv", extra_qs=url_csv_args
            )
            url_csv_path = url_csv.split("?")[0]
            context = {
                **data,
                **extras,
                **{
                    "renderers": renderers,
                    "url_csv": url_csv,
                    "url_csv_path": url_csv_path,
                    "url_csv_hidden_args": [
                        (key, value)
                        for key, value in urllib.parse.parse_qsl(request.query_string)
                        if key not in ("_labels", "_facet", "_size")
                    ]
                    + [("_size", "max")],
                    "datasette_version": __version__,
                    "config": self.ds.config_dict(),
                },
            }
            if "metadata" not in context:
                context["metadata"] = self.ds.metadata
            r = await self.render(templates, request=request, context=context)
            r.status = status_code

        ttl = request.args.get("_ttl", None)
        if ttl is None or not ttl.isdigit():
            if correct_hash_provided:
                ttl = self.ds.setting("default_cache_ttl_hashed")
            else:
                ttl = self.ds.setting("default_cache_ttl")

        return self.set_response_headers(r, ttl)

    def set_response_headers(self, response, ttl):
        # Set far-future cache expiry
        if self.ds.cache_headers and response.status == 200:
            ttl = int(ttl)
            if ttl == 0:
                ttl_header = "no-cache"
            else:
                ttl_header = f"max-age={ttl}"
            response.headers["Cache-Control"] = ttl_header
        response.headers["Referrer-Policy"] = "no-referrer"
        if self.ds.cors:
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response
