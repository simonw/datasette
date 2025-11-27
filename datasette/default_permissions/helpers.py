"""
Shared helper utilities for default permission implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Set

if TYPE_CHECKING:
    from datasette.app import Datasette

from datasette.permissions import PermissionSQL


def get_action_name_variants(datasette: "Datasette", action: str) -> Set[str]:
    """
    Get all name variants for an action (full name and abbreviation).

    Example:
        get_action_name_variants(ds, "view-table") -> {"view-table", "vt"}
    """
    variants = {action}
    action_obj = datasette.actions.get(action)
    if action_obj and action_obj.abbr:
        variants.add(action_obj.abbr)
    return variants


def action_in_list(datasette: "Datasette", action: str, action_list: list) -> bool:
    """Check if an action (or its abbreviation) is in a list."""
    return bool(get_action_name_variants(datasette, action).intersection(action_list))


@dataclass
class PermissionRow:
    """A single permission rule row."""

    parent: Optional[str]
    child: Optional[str]
    allow: bool
    reason: str


class PermissionRowCollector:
    """Collects permission rows and converts them to PermissionSQL."""

    def __init__(self, prefix: str = "row"):
        self.rows: List[PermissionRow] = []
        self.prefix = prefix

    def add(
        self,
        parent: Optional[str],
        child: Optional[str],
        allow: bool,
        reason: str,
    ) -> None:
        """Add a permission row."""
        self.rows.append(PermissionRow(parent, child, allow, reason))

    def add_if_not_none(
        self,
        parent: Optional[str],
        child: Optional[str],
        result: Optional[bool],
        reason: str,
    ) -> None:
        """Add a row only if result is not None."""
        if result is not None:
            self.add(parent, child, result, reason)

    def to_permission_sql(self) -> Optional[PermissionSQL]:
        """Convert collected rows to a PermissionSQL object."""
        if not self.rows:
            return None

        parts = []
        params = {}

        for idx, row in enumerate(self.rows):
            key = f"{self.prefix}_{idx}"
            parts.append(
                f"SELECT :{key}_parent AS parent, :{key}_child AS child, "
                f":{key}_allow AS allow, :{key}_reason AS reason"
            )
            params[f"{key}_parent"] = row.parent
            params[f"{key}_child"] = row.child
            params[f"{key}_allow"] = 1 if row.allow else 0
            params[f"{key}_reason"] = row.reason

        sql = "\nUNION ALL\n".join(parts)
        return PermissionSQL(sql=sql, params=params)
