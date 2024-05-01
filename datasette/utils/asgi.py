import hashlib
import json
from datasette.utils import calculate_etag
from mimetypes import guess_type

from pathlib import Path
import aiofiles
import aiofiles.os


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
        headers = {k: v for k, v in self.headers.items() if k.lower() != "content-type"}
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
    headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
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
    send, filepath, filename=None, content_type=None, chunk_size=4096, headers=None
):
    headers = headers or {}
    if filename:
        headers["content-disposition"] = f'attachment; filename="{filename}"'

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
    root_path = Path(root_path)
    static_headers = {}

    if headers:
        static_headers = headers.copy()

    async def inner_static(request, send):
        path = request.scope["url_route"]["kwargs"]["path"]
        headers = static_headers.copy()
        try:
            full_path = (root_path / path).resolve().absolute()
        except FileNotFoundError:
            await asgi_send_html(send, "404: Directory not found", 404)
            return
        if full_path.is_dir():
            await asgi_send_html(send, "403: Directory listing is not allowed", 403)
            return
        # Ensure full_path is within root_path to avoid weird "../" tricks
        try:
            full_path.relative_to(root_path.resolve())
        except ValueError:
            await asgi_send_html(send, "404: Path not inside root path", 404)
            return
        try:
            # Calculate ETag for filepath
            etag = await calculate_etag(full_path, chunk_size=chunk_size)
            headers["ETag"] = etag
            if_none_match = request.headers.get("if-none-match")
            if if_none_match and if_none_match == etag:
                return await asgi_send(send, "", 304)
            await asgi_send_file(
                send, full_path, chunk_size=chunk_size, headers=headers
            )
        except FileNotFoundError:
            await asgi_send_html(send, "404: File not found", 404)
            return

    return inner_static

class AsgiFileDownload:
    def __init__(
        self,
        filepath,
        filename=None,
        content_type="application/octet-stream",
        headers=None,
    ):
        self.headers = headers or {}
        self.filepath = filepath
        self.filename = filename
        self.content_type = content_type

    async def asgi_send(self, send):
        return await asgi_send_file(
            send,
            self.filepath,
            filename=self.filename,
            content_type=self.content_type,
            headers=self.headers,
        )


class AsgiRunOnFirstRequest:
    def __init__(self, asgi, on_startup):
        assert isinstance(on_startup, list)
        self.asgi = asgi
        self.on_startup = on_startup
        self._started = False

    async def __call__(self, scope, receive, send):
        if not self._started:
            self._started = True
            for hook in self.on_startup:
                await hook()
        return await self.asgi(scope, receive, send)