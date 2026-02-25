"""
Token authentication for Datasette.

Registers the default SignedTokenHandler and delegates token verification
to datasette.verify_token() so all registered handlers are tried.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from datasette.app import Datasette

from datasette import hookimpl
from datasette.tokens import SignedTokenHandler


@hookimpl
def register_token_handler(datasette: "Datasette"):
    """Register the default signed token handler."""
    return SignedTokenHandler()


@hookimpl(specname="actor_from_request")
async def actor_from_signed_api_token(
    datasette: "Datasette", request
) -> Optional[dict]:
    """
    Authenticate requests using API tokens by delegating to all registered
    token handlers via datasette.verify_token().
    """
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None

    token = authorization[len("Bearer ") :]
    return await datasette.verify_token(token)
