"""
Config-based permission handling for Datasette.

Applies permission rules from datasette.yaml configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from datasette.app import Datasette

from datasette import hookimpl
from datasette.permissions import PermissionSQL
from datasette.utils import actor_matches_allow

from .helpers import PermissionRowCollector, get_action_name_variants


class ConfigPermissionProcessor:
    """
    Processes permission rules from datasette.yaml configuration.

    Configuration structure:

    permissions:                    # Root-level permissions block
      view-instance:
        id: admin

    databases:
      mydb:
        permissions:                # Database-level permissions
          view-database:
            id: admin
        allow:                      # Database-level allow block (for view-*)
          id: viewer
        allow_sql:                  # execute-sql allow block
          id: analyst
        tables:
          users:
            permissions:            # Table-level permissions
              view-table:
                id: admin
            allow:                  # Table-level allow block
              id: viewer
        queries:
          my_query:
            permissions:            # Query-level permissions
              view-query:
                id: admin
            allow:                  # Query-level allow block
              id: viewer
    """

    def __init__(
        self,
        datasette: "Datasette",
        actor: Optional[dict],
        action: str,
    ):
        self.datasette = datasette
        self.actor = actor
        self.action = action
        self.config = datasette.config or {}
        self.collector = PermissionRowCollector(prefix="cfg")

        # Pre-compute action variants
        self.action_checks = get_action_name_variants(datasette, action)
        self.action_obj = datasette.actions.get(action)

        # Parse restrictions if present
        self.has_restrictions = actor and "_r" in actor if actor else False
        self.restrictions = actor.get("_r", {}) if actor else {}

        # Pre-compute restriction info for efficiency
        self.restricted_databases: Set[str] = set()
        self.restricted_tables: Set[Tuple[str, str]] = set()

        if self.has_restrictions:
            self.restricted_databases = {
                db_name
                for db_name, db_actions in (self.restrictions.get("d") or {}).items()
                if self.action_checks.intersection(db_actions)
            }
            self.restricted_tables = {
                (db_name, table_name)
                for db_name, tables in (self.restrictions.get("r") or {}).items()
                for table_name, table_actions in tables.items()
                if self.action_checks.intersection(table_actions)
            }
            # Tables implicitly reference their parent databases
            self.restricted_databases.update(db for db, _ in self.restricted_tables)

    def evaluate_allow_block(self, allow_block: Any) -> Optional[bool]:
        """Evaluate an allow block against the current actor."""
        if allow_block is None:
            return None
        return actor_matches_allow(self.actor, allow_block)

    def is_in_restriction_allowlist(
        self,
        parent: Optional[str],
        child: Optional[str],
    ) -> bool:
        """Check if resource is allowed by actor restrictions."""
        if not self.has_restrictions:
            return True  # No restrictions, all resources allowed

        # Check global allowlist
        if self.action_checks.intersection(self.restrictions.get("a", [])):
            return True

        # Check database-level allowlist
        if parent and self.action_checks.intersection(
            self.restrictions.get("d", {}).get(parent, [])
        ):
            return True

        # Check table-level allowlist
        if parent:
            table_restrictions = (self.restrictions.get("r", {}) or {}).get(parent, {})
            if child:
                table_actions = table_restrictions.get(child, [])
                if self.action_checks.intersection(table_actions):
                    return True
            else:
                # Parent query should proceed if any child in this database is allowlisted
                for table_actions in table_restrictions.values():
                    if self.action_checks.intersection(table_actions):
                        return True

        # Parent/child both None: include if any restrictions exist for this action
        if parent is None and child is None:
            if self.action_checks.intersection(self.restrictions.get("a", [])):
                return True
            if self.restricted_databases:
                return True
            if self.restricted_tables:
                return True

        return False

    def add_permissions_rule(
        self,
        parent: Optional[str],
        child: Optional[str],
        permissions_block: Optional[dict],
        scope_desc: str,
    ) -> None:
        """Add a rule from a permissions:{action} block."""
        if permissions_block is None:
            return

        action_allow_block = permissions_block.get(self.action)
        result = self.evaluate_allow_block(action_allow_block)

        self.collector.add_if_not_none(
            parent,
            child,
            result,
            f"config {'allow' if result else 'deny'} {scope_desc}",
        )

    def add_allow_block_rule(
        self,
        parent: Optional[str],
        child: Optional[str],
        allow_block: Any,
        scope_desc: str,
    ) -> None:
        """
        Add rules from an allow:{} block.

        For allow blocks, if the block exists but doesn't match the actor,
        this is treated as a deny. We also handle the restriction-gate logic.
        """
        if allow_block is None:
            return

        # Skip if resource is not in restriction allowlist
        if not self.is_in_restriction_allowlist(parent, child):
            return

        result = self.evaluate_allow_block(allow_block)
        bool_result = bool(result)

        self.collector.add(
            parent,
            child,
            bool_result,
            f"config {'allow' if result else 'deny'} {scope_desc}",
        )

        # Handle restriction-gate: add explicit denies for restricted resources
        self._add_restriction_gate_denies(parent, child, bool_result, scope_desc)

    def _add_restriction_gate_denies(
        self,
        parent: Optional[str],
        child: Optional[str],
        is_allowed: bool,
        scope_desc: str,
    ) -> None:
        """
        When a config rule denies at a higher level, add explicit denies
        for restricted resources to prevent child-level allows from
        incorrectly granting access.
        """
        if is_allowed or child is not None or not self.has_restrictions:
            return

        if not self.action_obj:
            return

        reason = f"config deny {scope_desc} (restriction gate)"

        if parent is None:
            # Root-level deny: add denies for all restricted resources
            if self.action_obj.takes_parent:
                for db_name in self.restricted_databases:
                    self.collector.add(db_name, None, False, reason)
            if self.action_obj.takes_child:
                for db_name, table_name in self.restricted_tables:
                    self.collector.add(db_name, table_name, False, reason)
        else:
            # Database-level deny: add denies for tables in that database
            if self.action_obj.takes_child:
                for db_name, table_name in self.restricted_tables:
                    if db_name == parent:
                        self.collector.add(db_name, table_name, False, reason)

    def process(self) -> Optional[PermissionSQL]:
        """Process all config rules and return combined PermissionSQL."""
        self._process_root_permissions()
        self._process_databases()
        self._process_root_allow_blocks()

        return self.collector.to_permission_sql()

    def _process_root_permissions(self) -> None:
        """Process root-level permissions block."""
        root_perms = self.config.get("permissions") or {}
        self.add_permissions_rule(
            None,
            None,
            root_perms,
            f"permissions for {self.action}",
        )

    def _process_databases(self) -> None:
        """Process database-level and nested configurations."""
        databases = self.config.get("databases") or {}

        for db_name, db_config in databases.items():
            self._process_database(db_name, db_config or {})

    def _process_database(self, db_name: str, db_config: dict) -> None:
        """Process a single database's configuration."""
        # Database-level permissions block
        db_perms = db_config.get("permissions") or {}
        self.add_permissions_rule(
            db_name,
            None,
            db_perms,
            f"permissions for {self.action} on {db_name}",
        )

        # Process tables
        for table_name, table_config in (db_config.get("tables") or {}).items():
            self._process_table(db_name, table_name, table_config or {})

        # Process queries
        for query_name, query_config in (db_config.get("queries") or {}).items():
            self._process_query(db_name, query_name, query_config)

        # Database-level allow blocks
        self._process_database_allow_blocks(db_name, db_config)

    def _process_table(
        self,
        db_name: str,
        table_name: str,
        table_config: dict,
    ) -> None:
        """Process a single table's configuration."""
        # Table-level permissions block
        table_perms = table_config.get("permissions") or {}
        self.add_permissions_rule(
            db_name,
            table_name,
            table_perms,
            f"permissions for {self.action} on {db_name}/{table_name}",
        )

        # Table-level allow block (for view-table)
        if self.action == "view-table":
            self.add_allow_block_rule(
                db_name,
                table_name,
                table_config.get("allow"),
                f"allow for {self.action} on {db_name}/{table_name}",
            )

    def _process_query(
        self,
        db_name: str,
        query_name: str,
        query_config: Any,
    ) -> None:
        """Process a single query's configuration."""
        # Query config can be a string (just SQL) or dict
        if not isinstance(query_config, dict):
            return

        # Query-level permissions block
        query_perms = query_config.get("permissions") or {}
        self.add_permissions_rule(
            db_name,
            query_name,
            query_perms,
            f"permissions for {self.action} on {db_name}/{query_name}",
        )

        # Query-level allow block (for view-query)
        if self.action == "view-query":
            self.add_allow_block_rule(
                db_name,
                query_name,
                query_config.get("allow"),
                f"allow for {self.action} on {db_name}/{query_name}",
            )

    def _process_database_allow_blocks(
        self,
        db_name: str,
        db_config: dict,
    ) -> None:
        """Process database-level allow/allow_sql blocks."""
        # view-database allow block
        if self.action == "view-database":
            self.add_allow_block_rule(
                db_name,
                None,
                db_config.get("allow"),
                f"allow for {self.action} on {db_name}",
            )

        # execute-sql allow_sql block
        if self.action == "execute-sql":
            self.add_allow_block_rule(
                db_name,
                None,
                db_config.get("allow_sql"),
                f"allow_sql for {db_name}",
            )

        # view-table uses database-level allow for inheritance
        if self.action == "view-table":
            self.add_allow_block_rule(
                db_name,
                None,
                db_config.get("allow"),
                f"allow for {self.action} on {db_name}",
            )

        # view-query uses database-level allow for inheritance
        if self.action == "view-query":
            self.add_allow_block_rule(
                db_name,
                None,
                db_config.get("allow"),
                f"allow for {self.action} on {db_name}",
            )

    def _process_root_allow_blocks(self) -> None:
        """Process root-level allow/allow_sql blocks."""
        root_allow = self.config.get("allow")

        if self.action == "view-instance":
            self.add_allow_block_rule(
                None,
                None,
                root_allow,
                "allow for view-instance",
            )

        if self.action == "view-database":
            self.add_allow_block_rule(
                None,
                None,
                root_allow,
                "allow for view-database",
            )

        if self.action == "view-table":
            self.add_allow_block_rule(
                None,
                None,
                root_allow,
                "allow for view-table",
            )

        if self.action == "view-query":
            self.add_allow_block_rule(
                None,
                None,
                root_allow,
                "allow for view-query",
            )

        if self.action == "execute-sql":
            self.add_allow_block_rule(
                None,
                None,
                self.config.get("allow_sql"),
                "allow_sql",
            )


@hookimpl(specname="permission_resources_sql")
async def config_permissions_sql(
    datasette: "Datasette",
    actor: Optional[dict],
    action: str,
) -> Optional[List[PermissionSQL]]:
    """
    Apply permission rules from datasette.yaml configuration.

    This processes:
    - permissions: blocks at root, database, table, and query levels
    - allow: blocks for view-* actions
    - allow_sql: blocks for execute-sql action
    """
    processor = ConfigPermissionProcessor(datasette, actor, action)
    result = processor.process()

    if result is None:
        return []

    return [result]
