import csv
import hashlib
import sys

from datasette.utils.asgi import Request
from datasette.utils import (
    add_cors_headers,
    error_body,
    EscapeHtmlWriter,
    InvalidSql,
    LimitedWriter,
    path_from_row_pks,
    path_with_format,
    sqlite3,
)
from datasette.utils.asgi import (
    AsgiStream,
    Response,
    BadRequest,
)


class DatasetteError(Exception):
    def __init__(
        self,
        message,
        title=None,
        error_dict=None,
        status=500,
        template=None,
        message_is_html=False,
        plain_message=None,
    ):
        self.message = message
        self.title = title
        self.error_dict = error_dict or {}
        self.status = status
        self.message_is_html = message_is_html
        # Plain text used for JSON error responses when message is HTML
        self.plain_message = plain_message


class View:
    async def head(self, request, datasette):
        if not hasattr(self, "get"):
            return await self.method_not_allowed(request)
        response = await self.get(request, datasette)
        response.body = ""
        return response

    async def method_not_allowed(self, request):
        if (
            request.path.endswith(".json")
            or request.headers.get("content-type") == "application/json"
        ):
            response = Response.json(error_body("Method not allowed", 405), status=405)
        else:
            response = Response.text("Method not allowed", status=405)
        return response

    async def options(self, request, datasette):
        response = Response.text("ok")
        response.headers["allow"] = ", ".join(
            method.upper()
            for method in ("head", "get", "post", "put", "patch", "delete")
            if hasattr(self, method)
        )
        return response

    async def __call__(self, request, datasette):
        try:
            handler = getattr(self, request.method.lower())
        except AttributeError:
            return await self.method_not_allowed(request)
        return await handler(request, datasette)


class BaseView:
    ds = None
    has_json_alternate = True

    def __init__(self, datasette):
        self.ds = datasette

    async def head(self, *args, **kwargs):
        response = await self.get(*args, **kwargs)
        response.body = b""
        return response

    async def method_not_allowed(self, request):
        if (
            request.path.endswith(".json")
            or request.headers.get("content-type") == "application/json"
        ):
            response = Response.json(error_body("Method not allowed", 405), status=405)
        else:
            response = Response.text("Method not allowed", status=405)
        return response

    async def options(self, request, *args, **kwargs):
        return Response.text("ok")

    async def get(self, request, *args, **kwargs):
        return await self.method_not_allowed(request)

    async def post(self, request, *args, **kwargs):
        return await self.method_not_allowed(request)

    async def put(self, request, *args, **kwargs):
        return await self.method_not_allowed(request)

    async def patch(self, request, *args, **kwargs):
        return await self.method_not_allowed(request)

    async def delete(self, request, *args, **kwargs):
        return await self.method_not_allowed(request)

    async def dispatch_request(self, request):
        if self.ds:
            await self.ds.refresh_schemas()
        handler = getattr(self, request.method.lower(), None)
        response = await handler(request)
        if self.ds.cors:
            add_cors_headers(response.headers)
        return response

    async def render(self, templates, request, context=None):
        context = context or {}
        environment = self.ds.get_jinja_environment(request)
        template = environment.select_template(templates)
        template_context = {
            **context,
            **{
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
                self.ds.urls.path(
                    path_with_format(
                        request=request,
                        path=request.scope.get("route_path"),
                        format="json",
                    )
                ),
            )
            template_context["alternate_url_json"] = alternate_url_json
            headers.update(
                {
                    "Link": '<{}>; rel="alternate"; type="application/json+datasette"'.format(
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


def _error(messages, status=400):
    return Response.json(error_body(messages, status), status=status)


async def stream_csv(datasette, fetch_data, request, database):
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
            new_query_string = request.query_string + "&" + "&".join(extra_parameters)
        new_scope = dict(request.scope, query_string=new_query_string.encode("latin-1"))
        receive = request.receive
        request = Request(new_scope, receive)
    if stream:
        # Some quick soundness checks
        if not datasette.setting("allow_csv_stream"):
            raise BadRequest("CSV streaming is disabled")
        if request.args.get("_next"):
            raise BadRequest("_next not allowed for CSV streaming")
        kwargs["_size"] = "max"
    # Fetch the first page
    try:
        response_or_template_contexts = await fetch_data(request)
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
        limited_writer = LimitedWriter(r, datasette.setting("max_csv_mb"))
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
                    data, _, _ = await fetch_data(request, **kwargs)
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
                                    cell = datasette.absolute_url(
                                        request,
                                        datasette.urls.row_blob(
                                            database,
                                            data["table"],
                                            path_from_row_pks(row, pks, not pks),
                                            column,
                                        ),
                                    )
                                else:
                                    # Otherwise generate URL for this query
                                    url = datasette.absolute_url(
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
                                    if not isinstance(cell, dict):
                                        new_row.extend((cell, ""))
                                    else:
                                        new_row.append(cell["value"])
                                        new_row.append(cell["label"])
                            else:
                                new_row.append(cell)
                        await writer.writerow(new_row)
            except Exception as ex:
                sys.stderr.write("Caught this error: {}\n".format(ex))
                sys.stderr.flush()
                await r.write(str(ex))
                return
        await limited_writer.write(postamble)

    headers = {}
    if datasette.cors:
        add_cors_headers(headers)
    if request.args.get("_dl", None):
        if not trace:
            content_type = "text/csv; charset=utf-8"
        disposition = 'attachment; filename="{}.csv"'.format(
            request.url_vars.get("table", database)
        )
        headers["content-disposition"] = disposition

    return AsgiStream(stream_fn, headers=headers, content_type=content_type)
