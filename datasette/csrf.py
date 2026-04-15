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

DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def _normalize_headers(raw_headers):
    """Lowercase header names; for duplicates, last value wins."""
    result = {}
    for name, value in raw_headers:
        if isinstance(name, str):
            name = name.encode("latin-1")
        if isinstance(value, str):
            value = value.encode("latin-1")
        result[name.lower()] = value
    return result


def _origin_tuple(value):
    """
    Parse an origin-like string into ``(scheme, host, port)`` with default
    ports filled in. Raises ``ValueError`` for malformed input.
    """
    parsed = urllib.parse.urlsplit(value)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if not scheme or not host:
        raise ValueError("missing scheme or host in {!r}".format(value))
    port = parsed.port  # may raise ValueError on bad ports
    if port is None:
        port = DEFAULT_PORTS.get(scheme)
    if port is None:
        raise ValueError("unknown default port for scheme {!r}".format(scheme))
    return scheme, host, port


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

        headers = _normalize_headers(scope.get("headers") or [])

        authorization = headers.get(b"authorization", b"").decode("latin-1")
        cookie_header = headers.get(b"cookie")
        # Bearer-token requests are not ambient browser credentials, so they
        # are not CSRF-vulnerable. Narrowly exempt them from the header check
        # before evaluating Sec-Fetch-Site / Origin. Only "Bearer" is exempt;
        # schemes like Basic or Digest can be browser-managed and ambient.
        # If the request also carries a Cookie header, ambient cookie auth
        # could be in play, so do NOT treat it as exempt.
        if authorization and not cookie_header:
            parts = authorization.split(None, 1)
            if parts and parts[0].lower() == "bearer":
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

        # Fallback for older browsers: Origin must match the request's own
        # scheme + host + port. Compare full origin tuples, not host alone.
        request_scheme = self._request_scheme(scope)
        try:
            origin_tuple = _origin_tuple(origin)
            expected_tuple = _origin_tuple("{}://{}".format(request_scheme, host))
        except ValueError:
            await self._forbid(
                send,
                "Malformed Origin {!r} or Host {!r}".format(origin, host),
            )
            return

        if origin_tuple == expected_tuple:
            await self.app(scope, receive, send)
            return

        await self._forbid(
            send,
            "Origin {!r} does not match Host {!r}".format(origin, host),
        )

    def _request_scheme(self, scope):
        if self.datasette is not None:
            try:
                if self.datasette.setting("force_https_urls"):
                    return "https"
            except Exception:
                pass
        return scope.get("scheme") or "http"

    async def _forbid(self, send, reason):
        await asgi_send(
            send,
            content=await self.datasette.render_template(
                "csrf_error.html", {"reason": reason}
            ),
            status=403,
            content_type="text/html; charset=utf-8",
        )
