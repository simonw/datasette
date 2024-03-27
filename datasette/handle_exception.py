from datasette import hookimpl, Response
from .utils import add_cors_headers
from .utils.asgi import (
    Base400,
)
from .views.base import DatasetteError
from markupsafe import Markup
import pdb
import traceback

try:
    import rich
except ImportError:
    rich = None


@hookimpl(trylast=True)
def handle_exception(datasette, request, exception):
    async def inner():
        if datasette.pdb:
            pdb.post_mortem(exception.__traceback__)

        if rich is not None:
            rich.get_console().print_exception(show_locals=True)

        title = None
        if isinstance(exception, Base400):
            status = exception.status
            info = {}
            message = exception.args[0]
        elif isinstance(exception, DatasetteError):
            status = exception.status
            info = exception.error_dict
            message = exception.message
            if exception.message_is_html:
                message = Markup(message)
            title = exception.title
        else:
            status = 500
            info = {}
            message = str(exception)
            traceback.print_exc()
        templates = [f"{status}.html", "error.html"]
        info.update(
            {
                "ok": False,
                "error": message,
                "status": status,
                "title": title,
            }
        )
        headers = {}
        if datasette.cors:
            add_cors_headers(headers)
        if request.path.split("?")[0].endswith(".json"):
            return Response.json(info, status=status, headers=headers)
        else:
            environment = datasette.get_jinja_environment(request)
            template = environment.select_template(templates)
            return Response.html(
                await template.render_async(
                    dict(
                        info,
                        urls=datasette.urls,
                        app_css_hash=datasette.app_css_hash(),
                        menu_links=lambda: [],
                    )
                ),
                status=status,
                headers=headers,
            )

    return inner
