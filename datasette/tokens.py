"""
Token handler system for Datasette.

Provides a base class for token handlers and the default signed token handler.
Plugins can implement register_token_handler to provide custom token backends
(e.g. database-backed tokens that can be revoked and audited).
"""

from __future__ import annotations

import dataclasses
import time
from typing import TYPE_CHECKING, Optional

import itsdangerous

if TYPE_CHECKING:
    from datasette.app import Datasette


@dataclasses.dataclass
class TokenRestrictions:
    """
    Restrictions to apply to a token, limiting which actions it can perform.

    Use the builder methods to construct restrictions::

        restrictions = (TokenRestrictions()
            .allow_all("view-instance")
            .allow_database("mydb", "create-table")
            .allow_resource("mydb", "mytable", "insert-row"))
    """

    all: list[str] = dataclasses.field(default_factory=list)
    database: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    resource: dict[str, dict[str, list[str]]] = dataclasses.field(default_factory=dict)

    def allow_all(self, action: str) -> "TokenRestrictions":
        """Allow an action across all databases and resources."""
        self.all.append(action)
        return self

    def allow_database(self, database: str, action: str) -> "TokenRestrictions":
        """Allow an action on a specific database."""
        self.database.setdefault(database, []).append(action)
        return self

    def allow_resource(
        self, database: str, resource: str, action: str
    ) -> "TokenRestrictions":
        """Allow an action on a specific resource within a database."""
        self.resource.setdefault(database, {}).setdefault(resource, []).append(action)
        return self


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
        restrictions: Optional[TokenRestrictions] = None,
    ) -> str:
        """Create and return a token string for the given actor."""
        raise NotImplementedError

    async def verify_token(self, datasette: "Datasette", token: str) -> Optional[dict]:
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
        restrictions: Optional[TokenRestrictions] = None,
    ) -> str:
        if not datasette.setting("allow_signed_tokens"):
            raise ValueError(
                "Signed tokens are not enabled for this Datasette instance"
            )

        token = {"a": actor_id, "t": int(time.time())}

        def abbreviate_action(action):
            action_obj = datasette.actions.get(action)
            if not action_obj:
                return action
            return action_obj.abbr or action

        if expires_after:
            token["d"] = expires_after
        if restrictions and (
            restrictions.all or restrictions.database or restrictions.resource
        ):
            token["_r"] = {}
            if restrictions.all:
                token["_r"]["a"] = [abbreviate_action(a) for a in restrictions.all]
            if restrictions.database:
                token["_r"]["d"] = {}
                for database, actions in restrictions.database.items():
                    token["_r"]["d"][database] = [abbreviate_action(a) for a in actions]
            if restrictions.resource:
                token["_r"]["r"] = {}
                for database, resources in restrictions.resource.items():
                    for resource, actions in resources.items():
                        token["_r"]["r"].setdefault(database, {})[resource] = [
                            abbreviate_action(a) for a in actions
                        ]
        return "dstok_{}".format(datasette.sign(token, namespace="token"))

    async def verify_token(self, datasette: "Datasette", token: str) -> Optional[dict]:
        prefix = "dstok_"

        if not datasette.setting("allow_signed_tokens"):
            return None

        max_signed_tokens_ttl = datasette.setting("max_signed_tokens_ttl")

        if not token.startswith(prefix):
            return None

        raw = token[len(prefix) :]
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
