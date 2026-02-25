"""
Token handler system for Datasette.

Provides a base class for token handlers and the default signed token handler.
Plugins can implement register_token_handler to provide custom token backends
(e.g. database-backed tokens that can be revoked and audited).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Dict, Iterable, Optional

import itsdangerous

if TYPE_CHECKING:
    from datasette.app import Datasette


class TokenHandler:
    """
    Base class for token handlers.

    Subclass this and implement create_token() and verify_token() to provide
    a custom token backend. Return an instance from the register_token_handler hook.
    """

    name: str = ""

    async def create_token(
        self,
        datasette: "Datasette",
        actor_id: str,
        *,
        expires_after: Optional[int] = None,
        restrict_all: Optional[Iterable[str]] = None,
        restrict_database: Optional[Dict[str, Iterable[str]]] = None,
        restrict_resource: Optional[Dict[str, Dict[str, Iterable[str]]]] = None,
    ) -> str:
        """Create and return a token string for the given actor."""
        raise NotImplementedError

    async def verify_token(
        self, datasette: "Datasette", token: str
    ) -> Optional[dict]:
        """
        Verify a token and return an actor dict, or None if this handler
        does not recognize the token.
        """
        raise NotImplementedError


class SignedTokenHandler(TokenHandler):
    """
    Default token handler using itsdangerous signed tokens (dstok_ prefix).
    """

    name = "signed"

    async def create_token(
        self,
        datasette: "Datasette",
        actor_id: str,
        *,
        expires_after: Optional[int] = None,
        restrict_all: Optional[Iterable[str]] = None,
        restrict_database: Optional[Dict[str, Iterable[str]]] = None,
        restrict_resource: Optional[Dict[str, Dict[str, Iterable[str]]]] = None,
    ) -> str:
        if not datasette.setting("allow_signed_tokens"):
            raise ValueError("Signed tokens are not enabled for this Datasette instance")

        token = {"a": actor_id, "t": int(time.time())}

        def abbreviate_action(action):
            action_obj = datasette.actions.get(action)
            if not action_obj:
                return action
            return action_obj.abbr or action

        if expires_after:
            token["d"] = expires_after
        if restrict_all or restrict_database or restrict_resource:
            token["_r"] = {}
            if restrict_all:
                token["_r"]["a"] = [abbreviate_action(a) for a in restrict_all]
            if restrict_database:
                token["_r"]["d"] = {}
                for database, actions in restrict_database.items():
                    token["_r"]["d"][database] = [
                        abbreviate_action(a) for a in actions
                    ]
            if restrict_resource:
                token["_r"]["r"] = {}
                for database, resources in restrict_resource.items():
                    for resource, actions in resources.items():
                        token["_r"]["r"].setdefault(database, {})[resource] = [
                            abbreviate_action(a) for a in actions
                        ]
        return "dstok_{}".format(datasette.sign(token, namespace="token"))

    async def verify_token(
        self, datasette: "Datasette", token: str
    ) -> Optional[dict]:
        prefix = "dstok_"

        if not datasette.setting("allow_signed_tokens"):
            return None

        max_signed_tokens_ttl = datasette.setting("max_signed_tokens_ttl")

        if not token.startswith(prefix):
            return None

        raw = token[len(prefix):]
        try:
            decoded = datasette.unsign(raw, namespace="token")
        except itsdangerous.BadSignature:
            return None

        if "t" not in decoded:
            return None
        created = decoded["t"]
        if not isinstance(created, int):
            return None

        duration = decoded.get("d")
        if duration is not None and not isinstance(duration, int):
            return None

        if (duration is None and max_signed_tokens_ttl) or (
            duration is not None
            and max_signed_tokens_ttl
            and duration > max_signed_tokens_ttl
        ):
            duration = max_signed_tokens_ttl

        if duration:
            if time.time() - created > duration:
                return None

        actor = {"id": decoded["a"], "token": "dstok"}

        if "_r" in decoded:
            actor["_r"] = decoded["_r"]

        if duration:
            actor["token_expires"] = created + duration

        return actor
