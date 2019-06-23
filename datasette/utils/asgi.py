import json
from mimetypes import guess_type
from sanic.views import HTTPMethodView
from sanic.request import Request as SanicRequest
from pathlib import Path
from html import escape
import re
import aiofiles


class AsgiRouter:
    def __init__(self, routes=None):
        routes = routes or []
        self.routes = [
            # Compile any strings to regular expressions
            ((re.compile(pattern) if isinstance(pattern, str) else pattern), view)
            for pattern, view in routes
        ]

    async def __call__(self, scope, receive, send):
        # Because we care about "foo/bar" v.s. "foo%2Fbar" we decode raw_path ourselves
        path = scope["raw_path"].decode("ascii")
        for regex, view in self.routes:
            match = regex.match(path)
            if match is not None:
                new_scope = dict(scope, url_route={"kwargs": match.groupdict()})
                try:
                    return await view(new_scope, receive, send)
                except Exception as exception:
                    return await self.handle_500(scope, receive, send, exception)
        return await self.handle_404(scope, receive, send)

    async def handle_404(self, scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [[b"content-type", b"text/html"]],
            }
        )
        await send({"type": "http.response.body", "body": b"<h1>404</h1>"})

    async def handle_500(self, scope, receive, send, exception):
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [[b"content-type", b"text/html"]],
            }
        )
        html = "<h1>500</h1><pre{}></pre>".format(escape(repr(exception)))
        await send({"type": "http.response.body", "body": html.encode("utf8")})


class AsgiLifespan:
    def __init__(self, app, on_startup=None, on_shutdown=None):
        print("Wrapping {}".format(app))
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


class AsgiView(HTTPMethodView):
    @classmethod
    def as_asgi(cls, *class_args, **class_kwargs):
        async def view(scope, receive, send):
            # Uses scope to create a Sanic-compatible request object,
            # then dispatches that to self.get(...) or self.options(...)
            # along with keyword arguments that were already tucked
            # into scope["url_route"]["kwargs"] by the router
            # https://channels.readthedocs.io/en/latest/topics/routing.html#urlrouter
            path = scope.get("raw_path", scope["path"].encode("utf8"))
            if scope["query_string"]:
                path = path + b"?" + scope["query_string"]
            request = SanicRequest(
                path,
                {
                    "Host": dict(scope.get("headers") or [])
                    .get(b"host", b"")
                    .decode("utf8")
                },
                "1.1",
                scope["method"],
                None,
            )

            # TODO: Remove need for this
            class Woo:
                def get_extra_info(self, key):
                    return False

            request.app = Woo()
            request.app.websocket_enabled = False
            request.transport = Woo()
            self = view.view_class(*class_args, **class_kwargs)
            response = await self.dispatch_request(
                request, **scope["url_route"]["kwargs"]
            )
            if hasattr(response, "asgi_send"):
                await response.asgi_send(send)
            else:
                headers = {}
                headers.update(response.headers)
                headers["content-type"] = response.content_type
                await send(
                    {
                        "type": "http.response.start",
                        "status": response.status,
                        "headers": [
                            [key.encode("utf-8"), value.encode("utf-8")]
                            for key, value in headers.items()
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": response.body})

        view.view_class = cls
        view.__doc__ = cls.__doc__
        view.__module__ = cls.__module__
        view.__name__ = cls.__name__
        return view


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
                "body": chunk.encode("utf8"),
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
        send, html, status=status, headers=headers, content_type="text/html"
    )


async def asgi_send_redirect(send, location, status=302):
    await asgi_send(
        send,
        "",
        status=status,
        headers={"Location": location},
        content_type="text/html",
    )


async def asgi_send(send, content, status, headers, content_type="text/plain"):
    await asgi_start(send, status, headers, content_type)
    await send({"type": "http.response.body", "body": content.encode("utf8")})


async def asgi_start(send, status, headers, content_type="text/plain"):
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
        headers["Content-Disposition"] = 'attachment; filename="{}"'.format(filename)
    first = True
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
    async def inner_static(scope, receive, send):
        path = scope["url_route"]["kwargs"]["path"]
        full_path = (Path(root_path) / path).absolute()
        # Ensure full_path is within root_path to avoid weird "../" tricks
        try:
            full_path.relative_to(root_path)
        except ValueError:
            await asgi_send_html(send, "404", 404)
            return
        first = True
        try:
            await asgi_send_file(send, full_path, chunk_size=chunk_size)
        except FileNotFoundError:
            await asgi_send_html(send, "404", 404)
            return

    return inner_static


class AsgiFileDownload:
    def __init__(
        self, filepath, filename=None, content_type="application/octet-stream"
    ):
        self.filepath = filepath
        self.filename = filename
        self.content_type = content_type

    async def asgi_send(self, send):
        return await asgi_send_file(send, self.filepath, content_type=self.content_type)
