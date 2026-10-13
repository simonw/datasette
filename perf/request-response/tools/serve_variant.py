"""
Run exactly `datasette serve ...` (datasette.cli.cli), but let environment
variables tweak how uvicorn is configured, WITHOUT editing datasette source:

  E2E_HTTP=h11|httptools|auto     -> uvicorn.Config(http=...)
  E2E_ACCESS_LOG=0                -> uvicorn.Config(access_log=False)
  E2E_LOOP=uvloop                 -> asyncio.run(..., loop_factory=uvloop.new_event_loop)
  E2E_GC_FREEZE=1                 -> gc.collect(); gc.freeze() just before uvicorn serves
  E2E_SWITCH=0.0005               -> sys.setswitchinterval(0.0005) (GIL convoy experiment)
  E2E_CONTENT_LENGTH=1            -> (experiment) Response.asgi_send adds a
                                     content-length header, so h11 uses
                                     Content-Length framing instead of chunked
  E2E_COALESCE=1                  -> (experiment) uvicorn h11 cycle buffers the
                                     status line+headers and writes them together
                                     with the body in one transport write

Usage: python serve_variant.py serve fixtures.db -p 8801 --secret s ...
"""

import asyncio
import gc
import os
import sys

import uvicorn

_orig_config_init = uvicorn.Config.__init__


def _config_init(self, app, **kw):
    http = os.environ.get("E2E_HTTP")
    if http:
        kw["http"] = http
    if os.environ.get("E2E_ACCESS_LOG") == "0":
        kw["access_log"] = False
    _orig_config_init(self, app, **kw)


uvicorn.Config.__init__ = _config_init

if os.environ.get("E2E_GC_FREEZE") == "1":
    _orig_serve = uvicorn.Server.serve

    async def _serve(self, sockets=None):
        gc.collect()
        gc.freeze()
        return await _orig_serve(self, sockets)

    uvicorn.Server.serve = _serve

if os.environ.get("E2E_LOOP") == "uvloop":
    import uvloop

    _orig_run = asyncio.run

    def _run(main, **kw):
        kw.setdefault("loop_factory", uvloop.new_event_loop)
        return _orig_run(main, **kw)

    asyncio.run = _run

if os.environ.get("E2E_CONTENT_LENGTH") == "1":
    from datasette.utils import asgi as _dsasgi

    async def _asgi_send(self, send):
        headers = {}
        headers.update(self.headers)
        headers["content-type"] = self.content_type
        body = self.body
        if not isinstance(body, bytes):
            body = body.encode("utf-8")
        raw_headers = [
            [key.encode("utf-8"), value.encode("utf-8")]
            for key, value in headers.items()
        ]
        if not any(k.lower() == "content-length" for k in headers):
            raw_headers.append([b"content-length", str(len(body)).encode("latin-1")])
        for set_cookie in self._set_cookie_headers:
            raw_headers.append([b"set-cookie", set_cookie.encode("utf-8")])
        await send(
            {
                "type": "http.response.start",
                "status": self.status,
                "headers": raw_headers,
            }
        )
        await send({"type": "http.response.body", "body": body})

    _dsasgi.Response.asgi_send = _asgi_send

if os.environ.get("E2E_COALESCE") == "1":
    from uvicorn.protocols.http import h11_impl as _h11_impl

    class _BufferedTransport:
        __slots__ = ("_buf", "_t")

        def __init__(self, t):
            self._t = t
            self._buf = []

        def write(self, data):
            if data:
                self._buf.append(data)

        def flush(self):
            if self._buf:
                buf = self._buf
                self._buf = []
                self._t.write(buf[0] if len(buf) == 1 else b"".join(buf))

        def close(self):
            self.flush()
            self._t.close()

        def __getattr__(self, name):
            return getattr(self._t, name)

    _orig_cycle_send = _h11_impl.RequestResponseCycle.send

    async def _cycle_send(self, message):
        t = self.transport
        if not isinstance(t, _BufferedTransport):
            t = self.transport = _BufferedTransport(t)
        await _orig_cycle_send(self, message)
        if message["type"] != "http.response.start":
            t.flush()

    _h11_impl.RequestResponseCycle.send = _cycle_send

if os.environ.get("E2E_STATIC_CHUNK"):
    # (experiment) asgi_static(root_path, chunk_size=4096, ...) -> bigger chunks
    from datasette.utils import asgi as _dsasgi2

    _d = list(_dsasgi2.asgi_static.__defaults__)
    _d[0] = int(os.environ["E2E_STATIC_CHUNK"])
    _dsasgi2.asgi_static.__defaults__ = tuple(_d)

if os.environ.get("E2E_SWITCH"):
    # (experiment) shorter GIL switch interval (default 0.005s)
    sys.setswitchinterval(float(os.environ["E2E_SWITCH"]))

from datasette.cli import cli

if __name__ == "__main__":
    sys.argv[0] = "datasette"
    cli(sys.argv[1:])
