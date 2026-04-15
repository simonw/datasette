"""
Header-based CSRF (Cross-Origin) protection.

Datasette uses the Sec-Fetch-Site + Origin header approach described in
Filippo Valsorda's article (https://words.filippo.io/csrf/) and implemented
in Go 1.25's http.CrossOriginProtection. This replaces the previous
token-based asgi-csrf mechanism.
"""

from __future__ import annotations

import secrets
import urllib.parse

from .utils.asgi import asgi_send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _install_legacy_csrftoken(scope):
    """
    Populate ``scope["csrftoken"]`` with a callable returning a per-request
    random token. Provided for plugin compatibility only - core no longer
    uses this value for CSRF enforcement.
    """

    def csrftoken():
        if "_datasette_legacy_csrftoken" not in scope:
            scope["_datasette_legacy_csrftoken"] = secrets.token_urlsafe(32)
        return scope["_datasette_legacy_csrftoken"]

    scope["csrftoken"] = csrftoken


class CrossOriginProtectionMiddleware:
    """
    Modern CSRF protection using the Sec-Fetch-Site and Origin headers.

    Based on Filippo Valsorda's algorithm, as implemented in Go 1.25's
    http.CrossOriginProtection. See https://words.filippo.io/csrf/

    Unsafe-method requests are allowed through only if they look same-origin.
    Non-browser clients (curl, etc.) send neither Sec-Fetch-Site nor Origin
    and are passed through unchanged - CSRF is a browser-only attack.
    """

    SAFE_METHODS = SAFE_METHODS

    def __init__(self, app, datasette):
        self.app = app
        self.datasette = datasette

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        _install_legacy_csrftoken(scope)

        if scope.get("method", "GET") in self.SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])

        # Bearer-token requests are not ambient browser credentials, so they
        # are not CSRF-vulnerable. Narrowly exempt them from the header check
        # before evaluating Sec-Fetch-Site / Origin. Only "Bearer" is exempt;
        # schemes like Basic or Digest can be browser-managed and ambient.
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        if authorization:
            scheme = authorization.split(None, 1)[0].lower()
            if scheme == "bearer":
                await self.app(scope, receive, send)
                return

        origin_bytes = headers.get(b"origin")
        sec_fetch_site_bytes = headers.get(b"sec-fetch-site")
        host_bytes = headers.get(b"host", b"")
        origin = origin_bytes.decode("latin-1") if origin_bytes else None
        sec_fetch_site = (
            sec_fetch_site_bytes.decode("latin-1") if sec_fetch_site_bytes else None
        )
        host = host_bytes.decode("latin-1")

        # Primary defense: Sec-Fetch-Site (set by browsers, unforgeable from JS)
        if sec_fetch_site is not None:
            if sec_fetch_site in ("same-origin", "none"):
                await self.app(scope, receive, send)
                return
            await self._forbid(
                send,
                "Sec-Fetch-Site was {!r}, expected 'same-origin' or 'none'".format(
                    sec_fetch_site
                ),
            )
            return

        # No Sec-Fetch-Site and no Origin -> non-browser client (curl, API, etc.)
        if origin is None:
            await self.app(scope, receive, send)
            return

        # Fallback for older browsers: Origin host must match Host header
        parsed = urllib.parse.urlparse(origin)
        origin_host = parsed.hostname or ""
        if parsed.port:
            origin_host = "{}:{}".format(origin_host, parsed.port)
        if origin_host == host:
            await self.app(scope, receive, send)
            return

        await self._forbid(
            send,
            "Origin {!r} does not match Host {!r}".format(origin, host),
        )

    async def _forbid(self, send, reason):
        await asgi_send(
            send,
            content=await self.datasette.render_template(
                "csrf_error.html", {"reason": reason}
            ),
            status=403,
            content_type="text/html; charset=utf-8",
        )
