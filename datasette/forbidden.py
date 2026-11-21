from datasette import Response, hookimpl

from .utils import add_cors_headers, request_wants_json
from .views.base import ErrorContext


@hookimpl(trylast=True)
def forbidden(datasette, request, message):
    async def inner():
        headers = {}
        if datasette.cors:
            add_cors_headers(headers)
        # Errors on explicit .csv and .tsv URLs are plain text, matching
        # handle_exception()
        if request.path.split("?")[0].endswith((".csv", ".tsv")):
            return Response.text(message, status=403, headers=headers)
        if request_wants_json(request):
            return Response.error(message, 403, headers=headers)
        return Response.html(
            await datasette.render_template(
                "error.html",
                ErrorContext(ok=False, error=message, status=403, title="Forbidden"),
                request=request,
            ),
            status=403,
        )

    return inner
