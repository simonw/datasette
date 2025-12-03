"""
Default permission implementations for Datasette.

This module provides the built-in permission checking logic through implementations
of the permission_resources_sql hook. The hooks are organized by their purpose:

1. Actor Restrictions - Enforces _r allowlists embedded in actor tokens
2. Root User - Grants full access when --root flag is used
3. Config Rules - Applies permissions from datasette.yaml
4. Default Settings - Enforces default_allow_sql and default view permissions

IMPORTANT: These hooks return PermissionSQL objects that are combined using SQL
UNION/INTERSECT operations. The order of evaluation is:
  - restriction_sql fields are INTERSECTed (all must match)
  - Regular sql fields are UNIONed and evaluated with cascading priority
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from datasette.app import Datasette

from datasette import hookimpl

# Re-export all hooks and public utilities
from .restrictions import (
    actor_restrictions_sql,
    restrictions_allow_action,
    ActorRestrictions,
)
from .root import root_user_permissions_sql
from .config import config_permissions_sql
from .defaults import (
    default_allow_sql_check,
    default_action_permissions_sql,
    DEFAULT_ALLOW_ACTIONS,
)
from .tokens import actor_from_signed_api_token


@hookimpl
def skip_csrf(scope) -> Optional[bool]:
    """Skip CSRF check for JSON content-type requests."""
    if scope["type"] == "http":
        headers = scope.get("headers") or {}
        if dict(headers).get(b"content-type") == b"application/json":
            return True
    return None


@hookimpl
def canned_queries(datasette: "Datasette", database: str, actor) -> dict:
    """Return canned queries defined in datasette.yaml configuration."""
    queries = (
        ((datasette.config or {}).get("databases") or {}).get(database) or {}
    ).get("queries") or {}
    return queries
