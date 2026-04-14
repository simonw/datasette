"""
Tests for the header-based CSRF (Cross-Origin) protection middleware.

Datasette uses the Sec-Fetch-Site + Origin header approach described in
Filippo Valsorda's article (https://words.filippo.io/csrf/) and implemented
in Go 1.25's http.CrossOriginProtection. This replaces the previous
token-based asgi-csrf mechanism.
"""

import pytest
from datasette.app import Datasette


@pytest.fixture
def ds():
    return Datasette(memory=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_methods_always_pass(ds, method):
    # Safe methods bypass CSRF entirely, even with hostile headers
    response = await ds.client.request(
        method,
        "/-/messages",
        headers={"sec-fetch-site": "cross-site", "origin": "http://evil.example"},
    )
    # Should not be blocked by CSRF (status may be 200/405/etc but never 403 for CSRF)
    assert response.status_code != 403 or "origin" not in response.text.lower()


@pytest.mark.asyncio
async def test_post_with_sec_fetch_site_same_origin_allowed(ds):
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hello", "message_class": "info"},
        headers={"sec-fetch-site": "same-origin"},
    )
    # Not blocked by CSRF (404 or 302 etc are fine, just not 403 CSRF)
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_post_with_sec_fetch_site_none_allowed(ds):
    # "none" = user-initiated direct navigation, e.g. bookmark/form in extension
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hello", "message_class": "info"},
        headers={"sec-fetch-site": "none"},
    )
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_post_with_sec_fetch_site_cross_site_blocked(ds):
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hello"},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_post_with_sec_fetch_site_same_site_blocked(ds):
    # same-site but different origin (e.g. different subdomain) must be blocked
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hello"},
        headers={"sec-fetch-site": "same-site"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_post_with_sec_fetch_site_cross_origin_blocked(ds):
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hello"},
        headers={"sec-fetch-site": "cross-origin"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_post_with_no_browser_headers_allowed(ds):
    # curl / Python requests / server-to-server: no Sec-Fetch-Site, no Origin.
    # Must pass through - CSRF is browser-specific.
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hello", "message_class": "info"},
    )
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_post_with_matching_origin_allowed(ds):
    # Fallback for older browsers without Sec-Fetch-Site: Origin must match Host
    # httpx sends host=localhost when talking to ds.client
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hello", "message_class": "info"},
        headers={"origin": "http://localhost"},
    )
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_post_with_mismatched_origin_blocked(ds):
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hello"},
        headers={"origin": "http://evil.example.com"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_csrf_error_page_renders(ds):
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hello"},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403
    assert "origin" in response.text.lower()


@pytest.mark.asyncio
async def test_websocket_scope_passes_through():
    # Non-http scope types should never be blocked by CSRF middleware
    from datasette.app import CrossOriginProtectionMiddleware

    called = []

    async def app(scope, receive, send):
        called.append(scope["type"])

    mw = CrossOriginProtectionMiddleware(app, datasette=None)
    await mw({"type": "websocket"}, None, None)
    await mw({"type": "lifespan"}, None, None)
    assert called == ["websocket", "lifespan"]


@pytest.mark.asyncio
async def test_middleware_unit_cross_site_blocked():
    # Direct unit test of the middleware without full Datasette wiring
    from datasette.app import CrossOriginProtectionMiddleware

    class FakeDs:
        async def render_template(self, name, ctx):
            return f"BLOCKED: {ctx.get('reason', '')}"

    sent = []

    async def app(scope, receive, send):
        raise AssertionError("Should not have called inner app")

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"sec-fetch-site", b"cross-site"),
            (b"host", b"example.com"),
        ],
    }
    mw = CrossOriginProtectionMiddleware(app, FakeDs())
    await mw(scope, None, send)
    start = [m for m in sent if m["type"] == "http.response.start"][0]
    assert start["status"] == 403


@pytest.mark.asyncio
async def test_middleware_unit_non_browser_allowed():
    from datasette.app import CrossOriginProtectionMiddleware

    inner_called = []

    async def app(scope, receive, send):
        inner_called.append(True)

    mw = CrossOriginProtectionMiddleware(app, datasette=None)
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"host", b"example.com")],
    }
    await mw(scope, None, None)
    assert inner_called == [True]
