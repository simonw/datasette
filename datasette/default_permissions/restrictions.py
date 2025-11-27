"""
Actor restriction handling for Datasette permissions.

This module handles the _r (restrictions) key in actor dictionaries, which
contains allowlists of resources the actor can access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from datasette.app import Datasette

from datasette import hookimpl
from datasette.permissions import PermissionSQL

from .helpers import action_in_list, get_action_name_variants


@dataclass
class ActorRestrictions:
    """Parsed actor restrictions from the _r key."""

    global_actions: List[str]  # _r.a - globally allowed actions
    database_actions: dict  # _r.d - {db_name: [actions]}
    table_actions: dict  # _r.r - {db_name: {table: [actions]}}

    @classmethod
    def from_actor(cls, actor: Optional[dict]) -> Optional["ActorRestrictions"]:
        """Parse restrictions from actor dict. Returns None if no restrictions."""
        if not actor:
            return None
        assert isinstance(actor, dict), "actor must be a dictionary"

        restrictions = actor.get("_r")
        if restrictions is None:
            return None

        return cls(
            global_actions=restrictions.get("a", []),
            database_actions=restrictions.get("d", {}),
            table_actions=restrictions.get("r", {}),
        )

    def is_action_globally_allowed(self, datasette: "Datasette", action: str) -> bool:
        """Check if action is in the global allowlist."""
        return action_in_list(datasette, action, self.global_actions)

    def get_allowed_databases(self, datasette: "Datasette", action: str) -> Set[str]:
        """Get database names where this action is allowed."""
        allowed = set()
        for db_name, db_actions in self.database_actions.items():
            if action_in_list(datasette, action, db_actions):
                allowed.add(db_name)
        return allowed

    def get_allowed_tables(
        self, datasette: "Datasette", action: str
    ) -> Set[Tuple[str, str]]:
        """Get (database, table) pairs where this action is allowed."""
        allowed = set()
        for db_name, tables in self.table_actions.items():
            for table_name, table_actions in tables.items():
                if action_in_list(datasette, action, table_actions):
                    allowed.add((db_name, table_name))
        return allowed


@hookimpl(specname="permission_resources_sql")
async def actor_restrictions_sql(
    datasette: "Datasette",
    actor: Optional[dict],
    action: str,
) -> Optional[List[PermissionSQL]]:
    """
    Handle actor restriction-based permission rules.

    When an actor has an "_r" key, it contains an allowlist of resources they
    can access. This function returns restriction_sql that filters the final
    results to only include resources in that allowlist.

    The _r structure:
    {
        "a": ["vi", "pd"],           # Global actions allowed
        "d": {"mydb": ["vt", "es"]}, # Database-level actions
        "r": {"mydb": {"users": ["vt"]}}  # Table-level actions
    }
    """
    if not actor:
        return None

    restrictions = ActorRestrictions.from_actor(actor)

    if restrictions is None:
        # No restrictions - all resources allowed
        return []

    # If globally allowed, no filtering needed
    if restrictions.is_action_globally_allowed(datasette, action):
        return []

    # Build restriction SQL
    allowed_dbs = restrictions.get_allowed_databases(datasette, action)
    allowed_tables = restrictions.get_allowed_tables(datasette, action)

    # If nothing is allowed for this action, return empty-set restriction
    if not allowed_dbs and not allowed_tables:
        return [
            PermissionSQL(
                params={"deny": f"actor restrictions: {action} not in allowlist"},
                restriction_sql="SELECT NULL AS parent, NULL AS child WHERE 0",
            )
        ]

    # Build UNION of allowed resources
    selects = []
    params = {}
    counter = 0

    # Database-level entries (parent, NULL) - allows all children
    for db_name in allowed_dbs:
        key = f"restr_{counter}"
        counter += 1
        selects.append(f"SELECT :{key}_parent AS parent, NULL AS child")
        params[f"{key}_parent"] = db_name

    # Table-level entries (parent, child)
    for db_name, table_name in allowed_tables:
        key = f"restr_{counter}"
        counter += 1
        selects.append(f"SELECT :{key}_parent AS parent, :{key}_child AS child")
        params[f"{key}_parent"] = db_name
        params[f"{key}_child"] = table_name

    restriction_sql = "\nUNION ALL\n".join(selects)

    return [PermissionSQL(params=params, restriction_sql=restriction_sql)]


def restrictions_allow_action(
    datasette: "Datasette",
    restrictions: dict,
    action: str,
    resource: Optional[str | Tuple[str, str]],
) -> bool:
    """
    Check if restrictions allow the requested action on the requested resource.

    This is a synchronous utility function for use by other code that needs
    to quickly check restriction allowlists.

    Args:
        datasette: The Datasette instance
        restrictions: The _r dict from an actor
        action: The action name to check
        resource: None for global, str for database, (db, table) tuple for table

    Returns:
        True if allowed, False if denied
    """
    # Does this action have an abbreviation?
    to_check = get_action_name_variants(datasette, action)

    # Check global level (any resource)
    all_allowed = restrictions.get("a")
    if all_allowed is not None:
        assert isinstance(all_allowed, list)
        if to_check.intersection(all_allowed):
            return True

    # Check database level
    if resource:
        if isinstance(resource, str):
            database_name = resource
        else:
            database_name = resource[0]
        database_allowed = restrictions.get("d", {}).get(database_name)
        if database_allowed is not None:
            assert isinstance(database_allowed, list)
            if to_check.intersection(database_allowed):
                return True

    # Check table/resource level
    if resource is not None and not isinstance(resource, str) and len(resource) == 2:
        database, table = resource
        table_allowed = restrictions.get("r", {}).get(database, {}).get(table)
        if table_allowed is not None:
            assert isinstance(table_allowed, list)
            if to_check.intersection(table_allowed):
                return True

    # This action is not explicitly allowed, so reject it
    return False
