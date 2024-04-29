from datasette.utils.asgi import (
    Response
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
    ):
        self.message = message
        self.title = title
        self.error_dict = error_dict or {}
        self.status = status
        self.message_is_html = message_is_html


class RowError(Exception):
    def __init__(self, error):
        self.error = error


class StartupError(Exception):
    pass


def _error(messages, status=400):
    return Response.json({"ok": False, "errors": messages}, status=status)
