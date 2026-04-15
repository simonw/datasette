"""
Tests for the header-based CSRF (Cross-Origin) protection middleware.

Datasette uses the Sec-Fetch-Site + Origin header approach described in
Filippo Valsorda's article (https://words.filippo.io/csrf/) and implemented
in Go 1.25's http.CrossOriginProtection. This replaces the previous
token-based asgi-csrf mechanism.
"""

import pluggy
import pytest

from datasette import hookimpl
from datasette.csrf import CrossOriginProtectionMiddleware, _install_legacy_csrftoken


async def _post(bare_ds, **kwargs):
    kwargs.setdefault("data", {"message": "hello", "message_class": "info"})
    return await bare_ds.client.post("/-/messages", **kwargs)


async def _run_middleware(scope):
    """
    Run CrossOriginProtectionMiddleware against a scope and return
    ("allowed",) if the inner app was called, or ("blocked", status)
    if the middleware sent a response itself.
    """

    class FakeDs:
        async def render_template(self, name, ctx):
            return "BLOCKED"

    inner_called = []

    async def app(scope, receive, send):
        inner_called.append(True)

    sent = []

    async def send(msg):
        sent.append(msg)

    mw = CrossOriginProtectionMiddleware(app, FakeDs())
    await mw(scope, None, send)
    if inner_called:
        return ("allowed",)
    start = [m for m in sent if m["type"] == "http.response.start"][0]
    return ("blocked", start["status"])


def _http_scope(headers, method="POST"):
    return {
        "type": "http",
        "method": method,
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_methods_always_pass(bare_ds, method):
    # Safe methods bypass CSRF entirely, even with hostile headers
    response = await bare_ds.client.request(
        method,
        "/-/messages",
        headers={"sec-fetch-site": "cross-site", "origin": "http://evil.example"},
    )
    assert response.status_code != 403 or "origin" not in response.text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("sec_fetch_site", ["same-origin", "none"])
async def test_post_with_trusted_sec_fetch_site_allowed(bare_ds, sec_fetch_site):
    # "same-origin" = first-party; "none" = user-initiated direct navigation
    response = await _post(bare_ds, headers={"sec-fetch-site": sec_fetch_site})
    assert response.status_code != 403


@pytest.mark.asyncio
@pytest.mark.parametrize("sec_fetch_site", ["cross-site", "same-site", "cross-origin"])
async def test_post_with_untrusted_sec_fetch_site_blocked(bare_ds, sec_fetch_site):
    # same-site is blocked too: different subdomains must not bypass CSRF
    response = await _post(
        bare_ds, data={"message": "hi"}, headers={"sec-fetch-site": sec_fetch_site}
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_post_with_no_browser_headers_allowed(bare_ds):
    # curl / requests / server-to-server: no Sec-Fetch-Site, no Origin.
    # CSRF is browser-specific so these pass through.
    response = await _post(bare_ds)
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_post_with_matching_origin_allowed(bare_ds):
    # Fallback for older browsers without Sec-Fetch-Site: Origin must match Host
    response = await _post(bare_ds, headers={"origin": "http://localhost"})
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_post_with_mismatched_origin_blocked(bare_ds):
    response = await _post(
        bare_ds, data={"message": "hi"}, headers={"origin": "http://evil.example.com"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_csrf_error_page_renders(bare_ds):
    response = await _post(
        bare_ds, data={"message": "hi"}, headers={"sec-fetch-site": "cross-site"}
    )
    assert response.status_code == 403
    assert "origin" in response.text.lower()


@pytest.mark.asyncio
async def test_csrf_error_page_title_has_no_typo(bare_ds):
    response = await _post(
        bare_ds, data={"message": "hi"}, headers={"sec-fetch-site": "cross-site"}
    )
    assert "<title>CSRF check failed</title>" in response.text
    assert "CSRF check failed)" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_type", ["websocket", "lifespan"])
async def test_non_http_scope_passes_through(scope_type):
    called = []

    async def app(scope, receive, send):
        called.append(scope["type"])

    mw = CrossOriginProtectionMiddleware(app, datasette=None)
    await mw({"type": scope_type}, None, None)
    assert called == [scope_type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label,headers,expected",
    [
        (
            "plain cross-site blocked",
            {"sec-fetch-site": "cross-site", "host": "example.com"},
            ("blocked", 403),
        ),
        (
            "basic auth does not bypass",
            {
                "sec-fetch-site": "cross-site",
                "host": "example.com",
                "authorization": "Basic dXNlcjpwYXNz",
            },
            ("blocked", 403),
        ),
        (
            "bearer auth bypasses",
            {
                "sec-fetch-site": "cross-site",
                "origin": "https://evil.example",
                "host": "example.com",
                "authorization": "Bearer dstok_abc",
            },
            ("allowed",),
        ),
        (
            "bearer scheme case-insensitive",
            {
                "sec-fetch-site": "cross-site",
                "host": "example.com",
                "authorization": "bearer dstok_abc",
            },
            ("allowed",),
        ),
        (
            "non-browser (no Sec-Fetch-Site, no Origin) allowed",
            {"host": "example.com"},
            ("allowed",),
        ),
    ],
)
async def test_middleware_unit(label, headers, expected):
    assert await _run_middleware(_http_scope(headers)) == expected


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
    scope = {}
    _install_legacy_csrftoken(scope)
    assert scope["csrftoken"]() == scope["csrftoken"]()


@pytest.mark.asyncio
async def test_cross_site_post_blocked_even_with_ds_csrftoken_cookie(bare_ds):
    # A stale ds_csrftoken cookie + csrftoken body field must NOT bypass
    # the header-based CSRF check.
    response = await _post(
        bare_ds,
        data={"message": "hi", "message_class": "info", "csrftoken": "abc"},
        headers={"sec-fetch-site": "cross-site"},
        cookies={"ds_csrftoken": "abc"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bearer_invalid_token_not_csrf_error(bare_ds):
    # Cross-site POST with bogus bearer must pass CSRF and be rejected
    # by auth/permission handling, not by the CSRF middleware.
    response = await _post(
        bare_ds,
        headers={
            "sec-fetch-site": "cross-site",
            "authorization": "Bearer totally-invalid-token",
        },
    )
    if response.status_code == 403:
        assert "origin" not in response.text.lower()
        assert "sec-fetch-site" not in response.text.lower()


@pytest.mark.asyncio
async def test_cross_site_post_without_auth_still_blocked(bare_ds):
    response = await _post(
        bare_ds, data={"message": "hi"}, headers={"sec-fetch-site": "cross-site"}
    )
    assert response.status_code == 403


def test_legacy_skip_csrf_hookimpl_does_not_break_loading():
    # Plugins that still define skip_csrf must load cleanly - pluggy ignores
    # unknown hook implementations - even though the hook is no longer
    # consulted by core. Use a throwaway PluginManager so that registering
    # this hookimpl does not leak a _HookCaller onto the real datasette.pm.
    class LegacyPlugin:
        __name__ = "legacy-skip-csrf-plugin"

        @hookimpl
        def skip_csrf(self, datasette, scope):
            return True

    throwaway = pluggy.PluginManager("datasette")
    plugin = LegacyPlugin()
    throwaway.register(plugin, name=LegacyPlugin.__name__)
    assert throwaway.is_registered(plugin)
