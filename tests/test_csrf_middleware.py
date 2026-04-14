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
async def test_csrf_error_page_title_has_no_typo(ds):
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hello"},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert "<title>CSRF check failed</title>" in response.text
    assert "CSRF check failed)" not in response.text


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


def test_legacy_csrftoken_scope_value_nonempty(app_client):
    # GET /post/ calls request.scope["csrftoken"]() - must not 500
    response = app_client.get("/post/")
    assert response.status == 200
    assert response.text.strip() != ""
    assert len(response.text.strip()) >= 20


def test_legacy_csrftoken_no_ds_csrftoken_cookie(app_client):
    response = app_client.get("/post/")
    assert "ds_csrftoken" not in response.cookies


def test_legacy_csrftoken_varies_across_requests(app_client):
    r1 = app_client.get("/post/").text.strip()
    r2 = app_client.get("/post/").text.strip()
    assert r1 != r2


def test_legacy_csrftoken_stable_within_request():
    # Two calls in the same request return the same value
    from datasette.app import _install_legacy_csrftoken

    scope = {}
    _install_legacy_csrftoken(scope)
    assert scope["csrftoken"]() == scope["csrftoken"]()


def test_legacy_csrftoken_template_helper_renders(
    restore_working_directory, tmpdir_factory
):
    from tests.fixtures import make_app_client

    templates = tmpdir_factory.mktemp("templates")
    (templates / "csrftoken_form.html").write_text(
        "CSRFTOKEN:{{ csrftoken() }}:END", "utf-8"
    )
    with make_app_client(template_dir=templates) as client:
        response = client.get("/csrftoken-form/")
        assert response.status_code == 200
        assert response.text.startswith("CSRFTOKEN:")
        assert response.text.endswith(":END")
        token = response.text[len("CSRFTOKEN:") : -len(":END")]
        assert len(token) >= 20
        assert "ds_csrftoken" not in response.cookies


@pytest.mark.asyncio
async def test_cross_site_post_blocked_even_with_ds_csrftoken_cookie(ds):
    # A stale ds_csrftoken cookie + csrftoken body field must NOT bypass
    # the header-based CSRF check.
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hi", "message_class": "info", "csrftoken": "abc"},
        headers={"sec-fetch-site": "cross-site"},
        cookies={"ds_csrftoken": "abc"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bearer_auth_cross_site_bypasses_csrf():
    # Cross-site browser POST with Authorization: Bearer must bypass CSRF
    from datasette.app import CrossOriginProtectionMiddleware

    inner_called = []

    async def app(scope, receive, send):
        inner_called.append(True)

    mw = CrossOriginProtectionMiddleware(app, datasette=None)
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"sec-fetch-site", b"cross-site"),
            (b"origin", b"https://evil.example"),
            (b"host", b"example.com"),
            (b"authorization", b"Bearer dstok_abc"),
        ],
    }
    await mw(scope, None, None)
    assert inner_called == [True]


@pytest.mark.asyncio
async def test_bearer_auth_scheme_case_insensitive():
    from datasette.app import CrossOriginProtectionMiddleware

    inner_called = []

    async def app(scope, receive, send):
        inner_called.append(True)

    mw = CrossOriginProtectionMiddleware(app, datasette=None)
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"sec-fetch-site", b"cross-site"),
            (b"host", b"example.com"),
            (b"authorization", b"bearer dstok_abc"),
        ],
    }
    await mw(scope, None, None)
    assert inner_called == [True]


@pytest.mark.asyncio
async def test_basic_auth_cross_site_still_blocked():
    # Only Bearer is exempt - Basic auth is ambient and must not bypass
    class FakeDs:
        async def render_template(self, name, ctx):
            return "BLOCKED"

    from datasette.app import CrossOriginProtectionMiddleware

    sent = []

    async def app(scope, receive, send):
        raise AssertionError("should not reach inner app")

    async def send(msg):
        sent.append(msg)

    mw = CrossOriginProtectionMiddleware(app, FakeDs())
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"sec-fetch-site", b"cross-site"),
            (b"host", b"example.com"),
            (b"authorization", b"Basic dXNlcjpwYXNz"),
        ],
    }
    await mw(scope, None, send)
    start = [m for m in sent if m["type"] == "http.response.start"][0]
    assert start["status"] == 403


@pytest.mark.asyncio
async def test_bearer_invalid_token_not_csrf_error(ds):
    # Cross-site POST with bogus bearer must pass CSRF and be rejected
    # by auth/permission handling, not by the CSRF middleware.
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hi", "message_class": "info"},
        headers={
            "sec-fetch-site": "cross-site",
            "authorization": "Bearer totally-invalid-token",
        },
    )
    # /-/messages happens to accept this; the key property is
    # "not a CSRF 403 with the CSRF error page"
    if response.status_code == 403:
        assert "origin" not in response.text.lower()
        assert "sec-fetch-site" not in response.text.lower()


@pytest.mark.asyncio
async def test_cross_site_post_without_auth_still_blocked(ds):
    response = await ds.client.post(
        "/-/messages",
        data={"message": "hi"},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403


def test_legacy_skip_csrf_hookimpl_does_not_break_loading():
    # Plugins that still define skip_csrf must load cleanly - pluggy ignores
    # unknown hook implementations - even though the hook is no longer
    # consulted by core.
    from datasette.plugins import pm
    from datasette import hookimpl

    class LegacyPlugin:
        __name__ = "legacy-skip-csrf-plugin"

        @hookimpl
        def skip_csrf(self, datasette, scope):
            return True

    plugin = LegacyPlugin()
    pm.register(plugin, name=LegacyPlugin.__name__)
    try:
        assert pm.is_registered(plugin)
    finally:
        pm.unregister(plugin)


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
