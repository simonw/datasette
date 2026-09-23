import traceback

from markupsafe import Markup

from datasette import Response, hookimpl

from .telemetry import tracer
from .telemetry_registry import RENDER_TEMPLATE, TEMPLATE_NAME
from .utils import add_cors_headers, error_body
from .utils.asgi import (
    Base400,
)
from .views.base import DatasetteError

# Debugger imports are deliberate - they back the "pdb" setting, which drops
# into a debugger on unhandled exceptions
try:
    import ipdb as pdb  # noqa: T100
except ImportError:
    import pdb  # noqa: T100

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
        plain_message = None
        if isinstance(exception, Base400):
            status = exception.status
            info = {}
            message = exception.args[0]
        elif isinstance(exception, DatasetteError):
            status = exception.status
            info = exception.error_dict
            message = exception.message
            plain_message = exception.plain_message
            if exception.message_is_html:
                message = Markup(message)
            title = exception.title
        else:
            status = 500
            info = {}
            message = str(exception)
            traceback.print_exc()
        templates = [f"{status}.html", "error.html"]
        headers = {}
        if datasette.cors:
            add_cors_headers(headers)
        if request.path.split("?")[0].endswith(".json"):
            body = dict(info)
            body.update(error_body(plain_message or message, status))
            return Response.json(body, status=status, headers=headers)
        if request.path.split("?")[0].endswith(".csv"):
            return Response.text(
                plain_message or message, status=status, headers=headers
            )
        info.update(
            {
                "ok": False,
                "error": message,
                "status": status,
                "title": title,
            }
        )
        environment = datasette.get_jinja_environment(request)
        # Error pages render outside Datasette.render_template(), so they get
        # their own render span here
        with tracer.start_as_current_span(RENDER_TEMPLATE) as span:
            template = environment.select_template(templates)
            if span.is_recording():
                span.set_attribute(TEMPLATE_NAME, template.name)
            body = await template.render_async(
                dict(
                    info,
                    urls=datasette.urls,
                    menu_links=list,
                )
            )
        return Response.html(body, status=status, headers=headers)

    return inner
