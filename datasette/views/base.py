import asyncio

from datasette.extras import extra_names_from_request
from datasette.renderer import RowStream
from datasette.utils import (
    add_cors_headers,
    call_with_supported_arguments,
    path_with_format,
)
from datasette.utils.asgi import (
    BadRequest,
    NotFound,
    Response,
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
        # Plain text used for JSON and CSV error responses when message is HTML
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
            response = Response.error("Method not allowed", 405)
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
            response = Response.error("Method not allowed", 405)
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
            "select_templates": [
                f"{'*' if template_name == template.name else ''}{template_name}"
                for template_name in templates
            ],
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
                    "Link": f'<{alternate_url_json}>; rel="alternate"; type="application/json+datasette"'
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


def page_for_renderer(datasette, request, format_, data):
    """
    The page= dictionary passed to an output renderer's render function.

    This is everything the JSON for this page returns alongside its rows:
    the keys that type of page always includes (such as next and next_url
    for tables), the extras the renderer declared when it was registered
    and any requested using ?_extra=. Extras that are not available for
    this type of page are omitted.
    """
    names = set(datasette._renderer_extras.get(format_) or [])
    names.update(extra_names_from_request(request))
    return {
        key: value
        for key, value in data.items()
        if key not in ("ok", "rows")
        # Row and query pages always have columns, but it is only an
        # extra if it was asked for - see issue 2136
        and (key != "columns" or "columns" in names)
    }


def tsv_export_path(datasette, request):
    """Path to this page as TSV, or None if a plugin handles .tsv instead"""
    from datasette.csv_renderer import render_tsv

    if datasette.renderers.get("tsv", (None,))[0] is not render_tsv:
        return None
    # route_path is the path without base_url, set when base_url is not "/"
    path = request.scope.get("route_path") or request.path
    return datasette.urls.path(path_with_format(path=path, format="tsv"))


def stream_requested(datasette, request, format_):
    """
    True if this request should stream every row: it has ?_stream=1 and the
    output renderer for this format can stream. Formats that cannot stream
    ignore ?_stream=.
    """
    if not request.args.get("_stream"):
        return False
    if not datasette._renderer_can_stream.get(format_):
        return False
    if not datasette.setting("allow_csv_stream"):
        raise BadRequest("Streaming is disabled")
    if request.args.get("_next"):
        raise BadRequest("_next not allowed when streaming")
    return True


class StreamSizeExceeded(Exception):
    pass


async def _limit_stream_size(body, limit_mb):
    limit_bytes = limit_mb * 1024 * 1024
    sent = 0
    try:
        async for chunk in body:
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            sent += len(chunk)
            if sent > limit_bytes:
                raise StreamSizeExceeded(
                    f"Response is larger than the {limit_mb}MB max_csv_mb limit"
                )
            yield chunk
    finally:
        aclose = getattr(body, "aclose", None)
        if aclose is not None:
            await aclose()


async def render_with_output_renderer(
    datasette,
    request,
    format_,
    *,
    data,
    columns,
    rows,
    sql,
    query_name,
    database,
    table,
    view_name,
    truncated,
    error,
    fetch_next_page=None,
):
    """
    Call the output renderer registered for format_ and return its Response.

    fetch_next_page is an async function returning the next page of rows, or
    None when there are no more. It is only used for a request that streams
    every row, see stream_requested().
    """
    streaming = stream_requested(datasette, request, format_)
    result = call_with_supported_arguments(
        datasette.renderers[format_][0],
        datasette=datasette,
        columns=columns,
        rows=rows,
        row_stream=RowStream(rows, fetch_next_page if streaming else None),
        sql=sql,
        query_name=query_name,
        database=database,
        table=table,
        request=request,
        view_name=view_name,
        truncated=truncated,
        error=error,
        page=page_for_renderer(datasette, request, format_, data),
        # These will be deprecated in Datasette 1.0:
        args=request.args,
        data=data,
    )
    if asyncio.iscoroutine(result):
        result = await result
    if result is None:
        raise NotFound("No data")
    if isinstance(result, dict):
        response = Response(
            body=result.get("body"),
            status=result.get("status_code") or 200,
            content_type=result.get("content_type", "text/plain"),
            headers=result.get("headers"),
        )
    elif isinstance(result, Response):
        response = result
    else:
        assert False, f"{result} should be dict or Response"

    # ?_dl=1 downloads the response as a file
    if (
        request.args.get("_dl")
        and response.status == 200
        and not any(key.lower() == "content-disposition" for key in response.headers)
    ):
        filename = request.url_vars.get("table", database)
        response.headers["content-disposition"] = (
            f'attachment; filename="{filename}.{format_}"'
        )

    # Streamed responses are limited in size
    limit_mb = datasette.setting("max_csv_mb")
    if limit_mb and hasattr(response.body, "__aiter__"):
        response.body = _limit_stream_size(response.body, limit_mb)
    return response
