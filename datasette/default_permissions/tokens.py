"""
Token authentication for Datasette.

Handles signed API tokens (dstok_ prefix).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from datasette.app import Datasette

import itsdangerous

from datasette import hookimpl


@hookimpl(specname="actor_from_request")
def actor_from_signed_api_token(datasette: "Datasette", request) -> Optional[dict]:
    """
    Authenticate requests using signed API tokens (dstok_ prefix).

    Token structure (signed JSON):
    {
        "a": "actor_id",      # Actor ID
        "t": 1234567890,      # Timestamp (Unix epoch)
        "d": 3600,            # Optional: Duration in seconds
        "_r": {...}           # Optional: Restrictions
    }
    """
    prefix = "dstok_"

    # Check if tokens are enabled
    if not datasette.setting("allow_signed_tokens"):
        return None

    max_signed_tokens_ttl = datasette.setting("max_signed_tokens_ttl")

    # Get authorization header
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None

    token = authorization[len("Bearer ") :]
    if not token.startswith(prefix):
        return None

    # Remove prefix and verify signature
    token = token[len(prefix) :]
    try:
        decoded = datasette.unsign(token, namespace="token")
    except itsdangerous.BadSignature:
        return None

    # Validate timestamp
    if "t" not in decoded:
        return None
    created = decoded["t"]
    if not isinstance(created, int):
        return None

    # Handle duration/expiry
    duration = decoded.get("d")
    if duration is not None and not isinstance(duration, int):
        return None

    # Apply max TTL if configured
    if (duration is None and max_signed_tokens_ttl) or (
        duration is not None
        and max_signed_tokens_ttl
        and duration > max_signed_tokens_ttl
    ):
        duration = max_signed_tokens_ttl

    # Check expiry
    if duration:
        if time.time() - created > duration:
            return None

    # Build actor dict
    actor = {"id": decoded["a"], "token": "dstok"}

    # Copy restrictions if present
    if "_r" in decoded:
        actor["_r"] = decoded["_r"]

    # Add expiry timestamp if applicable
    if duration:
        actor["token_expires"] = created + duration

    return actor
