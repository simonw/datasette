from datasette import hookimpl, Response
from .utils import add_cors_headers


@hookimpl(trylast=True)
def forbidden(datasette, request, message):
    async def inner():
        if (
            request.path.split("?")[0].endswith(".json")
            or "application/json" in (request.headers.get("accept") or "")
            or request.headers.get("content-type") == "application/json"
        ):
            headers = {}
            if datasette.cors:
                add_cors_headers(headers)
            return Response.error(message, 403, headers=headers)
        return Response.html(
            await datasette.render_template(
                "error.html",
                {
                    "title": "Forbidden",
                    "error": message,
                },
                request=request,
            ),
            status=403,
        )

    return inner
