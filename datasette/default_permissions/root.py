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
) -> Optional[PermissionSQL]:
    """
    Grant root user full permissions when --root flag is used.
    """
    if not datasette.root_enabled:
        return None
    if actor is not None and actor.get("id") == "root":
        return PermissionSQL.allow(reason="root user")
