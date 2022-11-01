import asyncio
import csv
import hashlib
import sys
import textwrap
import time
import urllib
from markupsafe import escape


import pint

from datasette import __version__
from datasette.database import QueryInterrupted
from datasette.utils.asgi import Request
from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
    EscapeHtmlWriter,
    InvalidSql,
    LimitedWriter,
    call_with_supported_arguments,
    tilde_decode,
    path_from_row_pks,
    path_with_added_args,
    path_with_removed_args,
    path_with_format,
    sqlite3,
)
from datasette.utils.asgi import (
    AsgiStream,
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
    has_json_alternate = True

    def __init__(self, datasette):
        self.ds = datasette

    async def head(self, *args, **kwargs):
        response = await self.get(*args, **kwargs)
        response.body = b""
        return response

    def database_color(self, database):
        return "ff0000"

    async def options(self, request, *args, **kwargs):
        return Response.text("Method not allowed", status=405)

    async def post(self, request, *args, **kwargs):
        return Response.text("Method not allowed", status=405)

    async def put(self, request, *args, **kwargs):
        return Response.text("Method not allowed", status=405)

    async def patch(self, request, *args, **kwargs):
        return Response.text("Method not allowed", status=405)

    async def delete(self, request, *args, **kwargs):
        return Response.text("Method not allowed", status=405)

    async def dispatch_request(self, request):
        if self.ds:
            await self.ds.refresh_schemas()
        handler = getattr(self, request.method.lower(), None)
        return await handler(request)

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
        headers = {}
        if self.has_json_alternate:
            alternate_url_json = self.ds.absolute_url(
                request,
                self.ds.urls.path(path_with_format(request=request, format="json")),
            )
            template_context["alternate_url_json"] = alternate_url_json
            headers.update(
                {
                    "Link": '{}; rel="alternate"; type="application/json+datasette"'.format(
                        alternate_url_json
                    )
                }
            )
        return Response.html(
            await self.ds.render_template(
                template,
                template_context,
                request=request,
                view_name=self.name,
            ),
            headers=headers,
        )

    @classmethod
    def as_view(cls, *class_args, **class_kwargs):
        async def view(request, send):
            self = view.view_class(*class_args, **class_kwargs)
            return await self.dispatch_request(request)

        view.view_class = cls
        view.__doc__ = cls.__doc__
        view.__module__ = cls.__module__
        view.__name__ = cls.__name__
        return view


class DataView(BaseView):
    name = ""

    async def options(self, request, *args, **kwargs):
        r = Response.text("ok")
        if self.ds.cors:
            add_cors_headers(r.headers)
        return r

    def redirect(self, request, path, forward_querystring=True, remove_args=None):
        if request.query_string and "?" not in path and forward_querystring:
            path = f"{path}?{request.query_string}"
        if remove_args:
            path = path_with_removed_args(request, remove_args, path=path)
        r = Response.redirect(path)
        r.headers["Link"] = f"<{path}>; rel=preload"
        if self.ds.cors:
            add_cors_headers(r.headers)
        return r

    async def data(self, request):
        raise NotImplementedError

    def get_templates(self, database, table=None):
        assert NotImplemented

    async def as_csv(self, request, database):
        kwargs = {}
        stream = request.args.get("_stream")
        # Do not calculate facets or counts:
        extra_parameters = [
            "{}=1".format(key)
            for key in ("_nofacet", "_nocount")
            if not request.args.get(key)
        ]
        if extra_parameters:
            # Replace request object with a new one with modified scope
            if not request.query_string:
                new_query_string = "&".join(extra_parameters)
            else:
                new_query_string = (
                    request.query_string + "&" + "&".join(extra_parameters)
                )
            new_scope = dict(
                request.scope, query_string=new_query_string.encode("latin-1")
            )
            receive = request.receive
            request = Request(new_scope, receive)
        if stream:
            # Some quick soundness checks
            if not self.ds.setting("allow_csv_stream"):
                raise BadRequest("CSV streaming is disabled")
            if request.args.get("_next"):
                raise BadRequest("_next not allowed for CSV streaming")
            kwargs["_size"] = "max"
        # Fetch the first page
        try:
            response_or_template_contexts = await self.data(request)
            if isinstance(response_or_template_contexts, Response):
                return response_or_template_contexts
            elif len(response_or_template_contexts) == 4:
                data, _, _, _ = response_or_template_contexts
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

        content_type = "text/plain; charset=utf-8"
        preamble = ""
        postamble = ""

        trace = request.args.get("_trace")
        if trace:
            content_type = "text/html; charset=utf-8"
            preamble = (
                "<html><head><title>CSV debug</title></head>"
                '<body><textarea style="width: 90%; height: 70vh">'
            )
            postamble = "</textarea></body></html>"

        async def stream_fn(r):
            nonlocal data, trace
            limited_writer = LimitedWriter(r, self.ds.setting("max_csv_mb"))
            if trace:
                await limited_writer.write(preamble)
                writer = csv.writer(EscapeHtmlWriter(limited_writer))
            else:
                writer = csv.writer(limited_writer)
            first = True
            next = None
            while first or (next and stream):
                try:
                    kwargs = {}
                    if next:
                        kwargs["_next"] = next
                    if not first:
                        data, _, _ = await self.data(request, **kwargs)
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
                                        url = self.ds.absolute_url(
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
                                        cell = url.replace("&_nocount=1", "").replace(
                                            "&_nofacet=1", ""
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
            await limited_writer.write(postamble)

        headers = {}
        if self.ds.cors:
            add_cors_headers(headers)
        if request.args.get("_dl", None):
            if not trace:
                content_type = "text/csv; charset=utf-8"
            disposition = 'attachment; filename="{}.csv"'.format(
                request.url_vars.get("table", database)
            )
            headers["content-disposition"] = disposition

        return AsgiStream(stream_fn, headers=headers, content_type=content_type)

    async def get(self, request):
        database_route = tilde_decode(request.url_vars["database"])

        try:
            db = self.ds.get_database(route=database_route)
        except KeyError:
            raise NotFound("Database not found: {}".format(database_route))
        database = db.name

        _format = request.url_vars["format"]
        data_kwargs = {}

        if _format == "csv":
            return await self.as_csv(request, database_route)

        if _format is None:
            # HTML views default to expanding all foreign key labels
            data_kwargs["default_labels"] = True

        extra_template_data = {}
        start = time.perf_counter()
        status_code = None
        templates = []
        try:
            response_or_template_contexts = await self.data(request, **data_kwargs)
            if isinstance(response_or_template_contexts, Response):
                return response_or_template_contexts
            # If it has four items, it includes an HTTP status code
            if len(response_or_template_contexts) == 4:
                (
                    data,
                    extra_template_data,
                    templates,
                    status_code,
                ) = response_or_template_contexts
            else:
                data, extra_template_data, templates = response_or_template_contexts
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
                        escape(ex.sql)
                    )
                ).strip(),
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
                    status=result.get("status_code", status_code or 200),
                    content_type=result.get("content_type", "text/plain"),
                    headers=result.get("headers"),
                )
            elif isinstance(result, Response):
                r = result
                if status_code is not None:
                    # Over-ride the status code
                    r.status = status_code
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
                    renderers[key] = self.ds.urls.path(
                        path_with_format(
                            request=request, format=key, extra_qs={**url_labels_extra}
                        )
                    )

            url_csv_args = {"_size": "max", **url_labels_extra}
            url_csv = self.ds.urls.path(
                path_with_format(request=request, format="csv", extra_qs=url_csv_args)
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
                    "settings": self.ds.settings_dict(),
                },
            }
            if "metadata" not in context:
                context["metadata"] = self.ds.metadata
            r = await self.render(templates, request=request, context=context)
            if status_code is not None:
                r.status = status_code

        ttl = request.args.get("_ttl", None)
        if ttl is None or not ttl.isdigit():
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
            add_cors_headers(response.headers)
        return response
