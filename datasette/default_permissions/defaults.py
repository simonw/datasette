"""
Default permission settings for Datasette.

Provides default allow rules for standard view/execute actions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from datasette.app import Datasette

from datasette import hookimpl
from datasette.permissions import PermissionSQL


# Actions that are allowed by default (unless --default-deny is used)
DEFAULT_ALLOW_ACTIONS = frozenset(
    {
        "view-instance",
        "view-database",
        "view-database-download",
        "view-table",
        "view-query",
        "execute-sql",
    }
)


@hookimpl(specname="permission_resources_sql")
async def default_allow_sql_check(
    datasette: "Datasette",
    actor: Optional[dict],
    action: str,
) -> Optional[PermissionSQL]:
    """
    Enforce the default_allow_sql setting.

    When default_allow_sql is false (the default), execute-sql is denied
    unless explicitly allowed by config or other rules.
    """
    if action == "execute-sql":
        if not await datasette.setting("default_allow_sql"):
            return PermissionSQL.deny(reason="default_allow_sql is false")

    return None


@hookimpl(specname="permission_resources_sql")
async def default_action_permissions_sql(
    datasette: "Datasette",
    actor: Optional[dict],
    action: str,
) -> Optional[PermissionSQL]:
    """
    Provide default allow rules for standard view/execute actions.

    These defaults are skipped when datasette is started with --default-deny.
    The restriction_sql mechanism (from actor_restrictions_sql) will still
    filter these results if the actor has restrictions.
    """
    if datasette.default_deny:
        return None

    if action in DEFAULT_ALLOW_ACTIONS:
        reason = f"default allow for {action}".replace("'", "''")
        return PermissionSQL.allow(reason=reason)

    return None
