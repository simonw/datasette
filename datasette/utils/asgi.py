import json
from datasette.utils import MultiParams
from mimetypes import guess_type
from urllib.parse import parse_qs, urlunparse, parse_qsl
from pathlib import Path
from html import escape
from http.cookies import SimpleCookie, Morsel
import re
import aiofiles
import aiofiles.os

# Workaround for adding samesite support to pre 3.8 python
Morsel._reserved["samesite"] = "SameSite"
# Thanks, Starlette:
# https://github.com/encode/starlette/blob/519f575/starlette/responses.py#L17


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


SAMESITE_VALUES = ("strict", "lax", "none")


class Request:
    def __init__(self, scope, receive):
        self.scope = scope
        self.receive = receive

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
        return dict(
            [
                (k.decode("latin-1").lower(), v.decode("latin-1"))
                for k, v in self.scope.get("headers") or []
            ]
        )

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
            return self.scope["raw_path"].decode("latin-1")
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
    def args(self):
        return MultiParams(parse_qs(qs=self.query_string))

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
    def fake(cls, path_with_query_string, method="GET", scheme="http"):
        "Useful for constructing Request objects for tests"
        path, _, query_string = path_with_query_string.partition("?")
        scope = {
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode("latin-1"),
            "query_string": query_string.encode("latin-1"),
            "scheme": scheme,
            "type": "http",
        }
        return cls(scope, None)


class AsgiLifespan:
    def __init__(self, app, on_startup=None, on_shutdown=None):
        self.app = app
        on_startup = on_startup or []
        on_shutdown = on_shutdown or []
        if not isinstance(on_startup or [], list):
            on_startup = [on_startup]
        if not isinstance(on_shutdown or [], list):
            on_shutdown = [on_shutdown]
        self.on_startup = on_startup
        self.on_shutdown = on_shutdown

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    for fn in self.on_startup:
                        await fn()
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    for fn in self.on_shutdown:
                        await fn()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        else:
            await self.app(scope, receive, send)


class AsgiStream:
    def __init__(self, stream_fn, status=200, headers=None, content_type="text/plain"):
        self.stream_fn = stream_fn
        self.status = status
        self.headers = headers or {}
        self.content_type = content_type

    async def asgi_send(self, send):
        # Remove any existing content-type header
        headers = dict(
            [(k, v) for k, v in self.headers.items() if k.lower() != "content-type"]
        )
        headers["content-type"] = self.content_type
        await send(
            {
                "type": "http.response.start",
                "status": self.status,
                "headers": [
                    [key.encode("utf-8"), value.encode("utf-8")]
                    for key, value in headers.items()
                ],
            }
        )
        w = AsgiWriter(send)
        await self.stream_fn(w)
        await send({"type": "http.response.body", "body": b""})


class AsgiWriter:
    def __init__(self, send):
        self.send = send

    async def write(self, chunk):
        await self.send(
            {
                "type": "http.response.body",
                "body": chunk.encode("utf-8"),
                "more_body": True,
            }
        )


async def asgi_send_json(send, info, status=200, headers=None):
    headers = headers or {}
    await asgi_send(
        send,
        json.dumps(info),
        status=status,
        headers=headers,
        content_type="application/json; charset=utf-8",
    )


async def asgi_send_html(send, html, status=200, headers=None):
    headers = headers or {}
    await asgi_send(
        send,
        html,
        status=status,
        headers=headers,
        content_type="text/html; charset=utf-8",
    )


async def asgi_send_redirect(send, location, status=302):
    await asgi_send(
        send,
        "",
        status=status,
        headers={"Location": location},
        content_type="text/html; charset=utf-8",
    )


async def asgi_send(send, content, status, headers=None, content_type="text/plain"):
    await asgi_start(send, status, headers, content_type)
    await send({"type": "http.response.body", "body": content.encode("utf-8")})


async def asgi_start(send, status, headers=None, content_type="text/plain"):
    headers = headers or {}
    # Remove any existing content-type header
    headers = dict([(k, v) for k, v in headers.items() if k.lower() != "content-type"])
    headers["content-type"] = content_type
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                [key.encode("latin1"), value.encode("latin1")]
                for key, value in headers.items()
            ],
        }
    )


async def asgi_send_file(
    send, filepath, filename=None, content_type=None, chunk_size=4096
):
    headers = {}
    if filename:
        headers["content-disposition"] = 'attachment; filename="{}"'.format(filename)
    first = True
    headers["content-length"] = str((await aiofiles.os.stat(str(filepath))).st_size)
    async with aiofiles.open(str(filepath), mode="rb") as fp:
        if first:
            await asgi_start(
                send,
                200,
                headers,
                content_type or guess_type(str(filepath))[0] or "text/plain",
            )
            first = False
        more_body = True
        while more_body:
            chunk = await fp.read(chunk_size)
            more_body = len(chunk) == chunk_size
            await send(
                {"type": "http.response.body", "body": chunk, "more_body": more_body}
            )


def asgi_static(root_path, chunk_size=4096, headers=None, content_type=None):
    async def inner_static(request, send):
        path = request.scope["url_route"]["kwargs"]["path"]
        try:
            full_path = (Path(root_path) / path).resolve().absolute()
        except FileNotFoundError:
            await asgi_send_html(send, "404", 404)
            return
        if full_path.is_dir():
            await asgi_send_html(send, "403: Directory listing is not allowed", 403)
            return
        # Ensure full_path is within root_path to avoid weird "../" tricks
        try:
            full_path.relative_to(root_path)
        except ValueError:
            await asgi_send_html(send, "404", 404)
            return
        try:
            await asgi_send_file(send, full_path, chunk_size=chunk_size)
        except FileNotFoundError:
            await asgi_send_html(send, "404", 404)
            return

    return inner_static


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
    def json(cls, body, status=200, headers=None):
        return cls(
            json.dumps(body),
            status=status,
            headers=headers,
            content_type="application/json; charset=utf-8",
        )

    @classmethod
    def redirect(cls, path, status=302, headers=None):
        headers = headers or {}
        headers["Location"] = path
        return cls("", status=status, headers=headers)


class AsgiFileDownload:
    def __init__(
        self, filepath, filename=None, content_type="application/octet-stream"
    ):
        self.filepath = filepath
        self.filename = filename
        self.content_type = content_type

    async def asgi_send(self, send):
        return await asgi_send_file(
            send, self.filepath, filename=self.filename, content_type=self.content_type
        )
