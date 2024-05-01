from datasette import hookimpl
from datasette.views.error_module import Response

@hookimpl(trylast=True)
def forbidden(datasette, request, message):
    async def inner():
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
