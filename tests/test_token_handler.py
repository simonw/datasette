"""
Tests for the register_token_handler plugin hook.
"""

from datasette.app import Datasette
from datasette.hookspecs import hookimpl
from datasette.plugins import pm
from datasette.tokens import TokenHandler, TokenRestrictions, SignedTokenHandler
import pytest


@pytest.fixture
def datasette():
    return Datasette()


@pytest.mark.asyncio
async def test_default_signed_handler_registered(datasette):
    """The default SignedTokenHandler should be registered automatically."""
    handlers = datasette._token_handlers()
    assert len(handlers) >= 1
    assert any(isinstance(h, SignedTokenHandler) for h in handlers)
    assert any(h.name == "signed" for h in handlers)


@pytest.mark.asyncio
async def test_create_token_default(datasette):
    """create_token() with handler='signed' should create a signed token."""
    token = await datasette.create_token("test_actor", handler="signed")
    assert token.startswith("dstok_")


@pytest.mark.asyncio
async def test_create_token_with_restrictions(datasette):
    """create_token() should handle restriction parameters."""
    token = await datasette.create_token(
        "test_actor",
        handler="signed",
        expires_after=3600,
        restrictions=TokenRestrictions().allow_all("view-instance"),
    )
    assert token.startswith("dstok_")
    # Verify the token contains the expected data
    decoded = datasette.unsign(token[len("dstok_") :], namespace="token")
    assert decoded["a"] == "test_actor"
    assert decoded["d"] == 3600
    assert "_r" in decoded
    assert "a" in decoded["_r"]


@pytest.mark.asyncio
async def test_verify_token_default(datasette):
    """verify_token() should verify signed tokens."""
    token = await datasette.create_token("test_actor", handler="signed")
    actor = await datasette.verify_token(token)
    assert actor is not None
    assert actor["id"] == "test_actor"
    assert actor["token"] == "dstok"


@pytest.mark.asyncio
async def test_verify_token_unknown_returns_none(datasette):
    """verify_token() should return None for unrecognized tokens."""
    result = await datasette.verify_token("unknown_token_format_xyz")
    assert result is None


@pytest.mark.asyncio
async def test_verify_token_bad_signature_returns_none(datasette):
    """verify_token() should return None for tokens with bad signatures."""
    result = await datasette.verify_token("dstok_tampered_data_here")
    assert result is None


@pytest.mark.asyncio
async def test_create_token_with_named_handler(datasette):
    """create_token(handler='signed') should select the signed handler."""
    token = await datasette.create_token("test_actor", handler="signed")
    assert token.startswith("dstok_")


@pytest.mark.asyncio
async def test_create_token_unknown_handler_raises(datasette):
    """create_token(handler='nonexistent') should raise ValueError."""
    with pytest.raises(ValueError, match="Token handler 'nonexistent' not found"):
        await datasette.create_token("test_actor", handler="nonexistent")


@pytest.mark.asyncio
async def test_custom_token_handler(datasette):
    """A custom token handler should be usable for both create and verify."""

    class CustomHandler(TokenHandler):
        name = "custom"

        async def create_token(self, datasette, actor_id, **kwargs):
            return f"custom_{actor_id}"

        async def verify_token(self, datasette, token):
            if token.startswith("custom_"):
                return {"id": token[len("custom_") :], "token": "custom"}
            return None

    class Plugin:
        __name__ = "CustomTokenPlugin"

        @staticmethod
        @hookimpl
        def register_token_handler(datasette):
            return CustomHandler()

    pm.register(Plugin(), name="test_custom_handler")
    try:
        handlers = datasette._token_handlers()
        assert any(h.name == "custom" for h in handlers)

        # Create with custom handler
        token = await datasette.create_token("alice", handler="custom")
        assert token == "custom_alice"

        # Verify custom token
        actor = await datasette.verify_token("custom_alice")
        assert actor is not None
        assert actor["id"] == "alice"
        assert actor["token"] == "custom"

        # Signed tokens should still work
        signed_token = await datasette.create_token("bob", handler="signed")
        assert signed_token.startswith("dstok_")
        actor = await datasette.verify_token(signed_token)
        assert actor["id"] == "bob"
    finally:
        pm.unregister(name="test_custom_handler")


@pytest.mark.asyncio
async def test_verify_token_tries_all_handlers(datasette):
    """verify_token() should try each handler until one matches."""

    class HandlerA(TokenHandler):
        name = "handler_a"

        async def create_token(self, datasette, actor_id, **kwargs):
            return f"a_{actor_id}"

        async def verify_token(self, datasette, token):
            if token.startswith("a_"):
                return {"id": token[2:], "token": "handler_a"}
            return None

    class HandlerB(TokenHandler):
        name = "handler_b"

        async def create_token(self, datasette, actor_id, **kwargs):
            return f"b_{actor_id}"

        async def verify_token(self, datasette, token):
            if token.startswith("b_"):
                return {"id": token[2:], "token": "handler_b"}
            return None

    class PluginA:
        __name__ = "PluginA"

        @staticmethod
        @hookimpl
        def register_token_handler(datasette):
            return HandlerA()

    class PluginB:
        __name__ = "PluginB"

        @staticmethod
        @hookimpl
        def register_token_handler(datasette):
            return HandlerB()

    pm.register(PluginA(), name="test_handler_a")
    pm.register(PluginB(), name="test_handler_b")
    try:
        # Both handler tokens should verify
        actor_a = await datasette.verify_token("a_alice")
        assert actor_a is not None
        assert actor_a["id"] == "alice"
        assert actor_a["token"] == "handler_a"

        actor_b = await datasette.verify_token("b_bob")
        assert actor_b is not None
        assert actor_b["id"] == "bob"
        assert actor_b["token"] == "handler_b"

        # Unknown token should return None
        assert await datasette.verify_token("c_charlie") is None
    finally:
        pm.unregister(name="test_handler_a")
        pm.unregister(name="test_handler_b")


@pytest.mark.asyncio
async def test_token_handler_via_http(datasette):
    """Default signed tokens should work through HTTP auth."""
    token = await datasette.create_token("http_user", handler="signed")
    response = await datasette.client.get(
        "/-/actor.json",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    actor = response.json()["actor"]
    assert actor["id"] == "http_user"
    assert actor["token"] == "dstok"


@pytest.mark.asyncio
async def test_custom_handler_via_http(datasette):
    """Custom handler tokens should work through HTTP auth."""

    class CustomHandler(TokenHandler):
        name = "custom_http"

        async def create_token(self, datasette, actor_id, **kwargs):
            return f"chttp_{actor_id}"

        async def verify_token(self, datasette, token):
            if token.startswith("chttp_"):
                return {"id": token[len("chttp_") :], "token": "custom_http"}
            return None

    class Plugin:
        __name__ = "CustomHTTPPlugin"

        @staticmethod
        @hookimpl
        def register_token_handler(datasette):
            return CustomHandler()

    pm.register(Plugin(), name="test_custom_http")
    try:
        token = await datasette.create_token("web_user", handler="custom_http")
        response = await datasette.client.get(
            "/-/actor.json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        actor = response.json()["actor"]
        assert actor["id"] == "web_user"
        assert actor["token"] == "custom_http"
    finally:
        pm.unregister(name="test_custom_http")


@pytest.mark.asyncio
async def test_token_handler_base_class_raises():
    """TokenHandler base class methods should raise NotImplementedError."""
    handler = TokenHandler()
    ds = Datasette()
    with pytest.raises(NotImplementedError):
        await handler.create_token(ds, "test")
    with pytest.raises(NotImplementedError):
        await handler.verify_token(ds, "test")


@pytest.mark.asyncio
async def test_restrictions_round_trip(datasette):
    """Tokens with database/resource restrictions should round-trip correctly."""
    restrictions = (
        TokenRestrictions()
        .allow_all("view-instance")
        .allow_database("docs", "view-query")
        .allow_resource("docs", "attachments", "insert-row")
    )
    token = await datasette.create_token(
        "test_actor", handler="signed", restrictions=restrictions
    )
    actor = await datasette.verify_token(token)
    assert actor is not None
    assert actor["id"] == "test_actor"
    assert actor["_r"]["a"] == ["view-instance"]
    assert actor["_r"]["d"] == {"docs": ["view-query"]}
    assert actor["_r"]["r"] == {"docs": {"attachments": ["insert-row"]}}


@pytest.mark.asyncio
async def test_expires_after_round_trip(datasette):
    """Tokens with expires_after should include token_expires in the actor."""
    token = await datasette.create_token(
        "test_actor", handler="signed", expires_after=3600
    )
    actor = await datasette.verify_token(token)
    assert actor is not None
    assert actor["id"] == "test_actor"
    assert "token_expires" in actor


@pytest.mark.asyncio
async def test_signed_tokens_disabled():
    """create_token and verify_token should fail/skip when signed tokens are disabled."""
    ds = Datasette(settings={"allow_signed_tokens": False})
    with pytest.raises(ValueError, match="Signed tokens are not enabled"):
        await ds.create_token("test_actor", handler="signed")
    # verify_token should return None rather than raising
    assert await ds.verify_token("dstok_anything") is None
