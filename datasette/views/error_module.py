from datasette.utils import MultiParams
from urllib.parse import parse_qsl, urlunparse, parse_qs
from http.cookies import SimpleCookie, Morsel
import json

# Workaround for adding samesite support to pre 3.8 python
Morsel._reserved["samesite"] = "SameSite"
# Thanks, Starlette:
# https://github.com/encode/starlette/blob/519f575/starlette/responses.py#L17

SAMESITE_VALUES = ("strict", "lax", "none")


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


class Base400(Exception):
    status = 400


class NotFound(Base400):
    status = 404


class DatabaseNotFound(NotFound):
    def __init__(self, message, database_name):
        super().__init__(message)
        self.database_name = database_name


class TableNotFound(NotFound):
    def __init__(self, message, database_name, table):
        super().__init__(message)
        self.database_name = database_name
        self.table = table


class RowNotFound(NotFound):
    def __init__(self, message, database_name, table, pk_values):
        super().__init__(message)
        self.database_name = database_name
        self.table_name = table
        self.pk_values = pk_values


class Forbidden(Base400):
    status = 403


class BadRequest(Base400):
    status = 400


class Request:
    def __init__(self, scope, receive):
        self.scope = scope
        self.receive = receive

    def __repr__(self):
        return '<asgi.Request method="{}" url="{}">'.format(self.method, self.url)

    @property
    def method(self):
        return self.scope["method"]

    @property
    def url(self):
        return urlunparse(
            (self.scheme, self.host, self.path, None, self.query_string, None)
        )

    @property
    def url_vars(self):
        return (self.scope.get("url_route") or {}).get("kwargs") or {}

    @property
    def scheme(self):
        return self.scope.get("scheme") or "http"

    @property
    def headers(self):
        return {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in self.scope.get("headers") or []
        }

    @property
    def host(self):
        return self.headers.get("host") or "localhost"

    @property
    def cookies(self):
        cookies = SimpleCookie()
        cookies.load(self.headers.get("cookie", ""))
        return {key: value.value for key, value in cookies.items()}

    @property
    def path(self):
        if self.scope.get("raw_path") is not None:
            return self.scope["raw_path"].decode("latin-1").partition("?")[0]
        else:
            path = self.scope["path"]
            if isinstance(path, str):
                return path
            else:
                return path.decode("utf-8")

    @property
    def query_string(self):
        return (self.scope.get("query_string") or b"").decode("latin-1")

    @property
    def full_path(self):
        qs = self.query_string
        return "{}{}".format(self.path, ("?" + qs) if qs else "")

    @property
    def args(self):
        return MultiParams(parse_qs(qs=self.query_string, keep_blank_values=True))

    @property
    def actor(self):
        return self.scope.get("actor", None)

    async def post_body(self):
        body = b""
        more_body = True
        while more_body:
            message = await self.receive()
            assert message["type"] == "http.request", message
            body += message.get("body", b"")
            more_body = message.get("more_body", False)
        return body

    async def post_vars(self):
        body = await self.post_body()
        return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))

    @classmethod
    def fake(cls, path_with_query_string, method="GET", scheme="http", url_vars=None):
        """Useful for constructing Request objects for tests"""
        path, _, query_string = path_with_query_string.partition("?")
        scope = {
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path_with_query_string.encode("latin-1"),
            "query_string": query_string.encode("latin-1"),
            "scheme": scheme,
            "type": "http",
        }
        if url_vars:
            scope["url_route"] = {"kwargs": url_vars}
        return cls(scope, None)


class Response:
    def __init__(self, body=None, status=200, headers=None, content_type="text/plain"):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self._set_cookie_headers = []
        self.content_type = content_type

    async def asgi_send(self, send):
        headers = {}
        headers.update(self.headers)
        headers["content-type"] = self.content_type
        raw_headers = [
            [key.encode("utf-8"), value.encode("utf-8")]
            for key, value in headers.items()
        ]
        for set_cookie in self._set_cookie_headers:
            raw_headers.append([b"set-cookie", set_cookie.encode("utf-8")])
        await send(
            {
                "type": "http.response.start",
                "status": self.status,
                "headers": raw_headers,
            }
        )
        body = self.body
        if not isinstance(body, bytes):
            body = body.encode("utf-8")
        await send({"type": "http.response.body", "body": body})

    def set_cookie(
            self,
            key,
            value="",
            max_age=None,
            expires=None,
            path="/",
            domain=None,
            secure=False,
            httponly=False,
            samesite="lax",
    ):
        assert samesite in SAMESITE_VALUES, "samesite should be one of {}".format(
            SAMESITE_VALUES
        )
        cookie = SimpleCookie()
        cookie[key] = value
        for prop_name, prop_value in (
                ("max_age", max_age),
                ("expires", expires),
                ("path", path),
                ("domain", domain),
                ("samesite", samesite),
        ):
            if prop_value is not None:
                cookie[key][prop_name.replace("_", "-")] = prop_value
        for prop_name, prop_value in (("secure", secure), ("httponly", httponly)):
            if prop_value:
                cookie[key][prop_name] = True
        self._set_cookie_headers.append(cookie.output(header="").strip())

    @classmethod
    def html(cls, body, status=200, headers=None):
        return cls(
            body,
            status=status,
            headers=headers,
            content_type="text/html; charset=utf-8",
        )

    @classmethod
    def text(cls, body, status=200, headers=None):
        return cls(
            str(body),
            status=status,
            headers=headers,
            content_type="text/plain; charset=utf-8",
        )

    @classmethod
    def json(cls, body, status=200, headers=None, default=None):
        return cls(
            json.dumps(body, default=default),
            status=status,
            headers=headers,
            content_type="application/json; charset=utf-8",
        )

    @classmethod
    def redirect(cls, path, status=302, headers=None):
        headers = headers or {}
        headers["Location"] = path
        return cls("", status=status, headers=headers)
