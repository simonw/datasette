"""
Root user permission handling for Datasette.

Grants full permissions to the root user when --root flag is used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from datasette.app import Datasette

from datasette import hookimpl
from datasette.permissions import PermissionSQL


@hookimpl(specname="permission_resources_sql")
async def root_user_permissions_sql(
    datasette: "Datasette",
    actor: Optional[dict],
    action: str,
) -> Optional[PermissionSQL]:
    """
    Grant root user full permissions when --root flag is used.

    This adds a global-level allow rule (NULL, NULL). Note that database or
    table-level deny rules in config can still block access - the root user
    is not completely immune to denies.
    """
    is_root = datasette.root_enabled and actor is not None and actor.get("id") == "root"

    if is_root:
        return PermissionSQL.allow(reason="root user")

    return None
