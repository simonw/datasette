from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datasette.app import Datasette

from datasette import hookimpl
from datasette.permissions import PermissionSQL
from datasette.utils import actor_matches_allow
import itsdangerous
import time


@hookimpl(specname="permission_resources_sql")
async def actor_restrictions_sql(datasette, actor, action):
    """Handle actor restriction-based permission rules (_r key)."""
    if not actor:
        return None

    restrictions = actor.get("_r") if isinstance(actor, dict) else None
    if restrictions is None:
        return []

    # Check if this action appears in restrictions (with abbreviations)
    action_obj = datasette.actions.get(action)
    action_checks = {action}
    if action_obj and action_obj.abbr:
        action_checks.add(action_obj.abbr)

    # Check if globally allowed in restrictions
    global_actions = restrictions.get("a", [])
    is_globally_allowed = action_checks.intersection(global_actions)

    if is_globally_allowed:
        # Globally allowed - no restriction filtering needed
        return []

    # Not globally allowed - build restriction_sql that lists allowlisted resources
    restriction_selects = []
    restriction_params = {}
    param_counter = 0

    # Add database-level allowlisted resources
    db_restrictions = restrictions.get("d", {})
    for db_name, db_actions in db_restrictions.items():
        if action_checks.intersection(db_actions):
            prefix = f"restr_{param_counter}"
            param_counter += 1
            restriction_selects.append(
                f"SELECT :{prefix}_parent AS parent, NULL AS child"
            )
            restriction_params[f"{prefix}_parent"] = db_name

    # Add table-level allowlisted resources
    resource_restrictions = restrictions.get("r", {})
    for db_name, tables in resource_restrictions.items():
        for table_name, table_actions in tables.items():
            if action_checks.intersection(table_actions):
                prefix = f"restr_{param_counter}"
                param_counter += 1
                restriction_selects.append(
                    f"SELECT :{prefix}_parent AS parent, :{prefix}_child AS child"
                )
                restriction_params[f"{prefix}_parent"] = db_name
                restriction_params[f"{prefix}_child"] = table_name

    if not restriction_selects:
        # Action not in allowlist - return empty restriction (INTERSECT will return no results)
        return [
            PermissionSQL(
                params={"deny": f"actor restrictions: {action} not in allowlist"},
                restriction_sql="SELECT NULL AS parent, NULL AS child WHERE 0",  # Empty set
            )
        ]

    # Build restriction SQL that returns allowed (parent, child) pairs
    restriction_sql = "\nUNION ALL\n".join(restriction_selects)

    # Return restriction-only PermissionSQL (sql=None means no permission rules)
    # The restriction_sql does the actual filtering via INTERSECT
    return [
        PermissionSQL(
            params=restriction_params,
            restriction_sql=restriction_sql,
        )
    ]


@hookimpl(specname="permission_resources_sql")
async def root_user_permissions_sql(datasette, actor, action):
    """Grant root user full permissions when enabled."""
    if datasette.root_enabled and actor and actor.get("id") == "root":
        # Add a single global-level allow rule (NULL, NULL) for root
        # This allows root to access everything by default, but database-level
        # and table-level deny rules in config can still block specific resources
        return PermissionSQL.allow(reason="root user")
    return None


@hookimpl(specname="permission_resources_sql")
async def config_permissions_sql(datasette, actor, action):
    """Apply config-based permission rules from datasette.yaml."""
    config = datasette.config or {}

    def evaluate(allow_block):
        if allow_block is None:
            return None
        return actor_matches_allow(actor, allow_block)

    has_restrictions = actor and "_r" in actor if actor else False
    restrictions = actor.get("_r", {}) if actor else {}

    action_obj = datasette.actions.get(action)
    action_checks = {action}
    if action_obj and action_obj.abbr:
        action_checks.add(action_obj.abbr)

    restricted_databases: set[str] = set()
    restricted_tables: set[tuple[str, str]] = set()
    if has_restrictions:
        restricted_databases = {
            db_name
            for db_name, db_actions in (restrictions.get("d") or {}).items()
            if action_checks.intersection(db_actions)
        }
        restricted_tables = {
            (db_name, table_name)
            for db_name, tables in (restrictions.get("r") or {}).items()
            for table_name, table_actions in tables.items()
            if action_checks.intersection(table_actions)
        }
        # Tables implicitly reference their parent databases
        restricted_databases.update(db for db, _ in restricted_tables)

    def is_in_restriction_allowlist(parent, child, action_name):
        """Check if a resource is in the actor's restriction allowlist for this action"""
        if not has_restrictions:
            return True  # No restrictions, all resources allowed

        # Check global allowlist
        if action_checks.intersection(restrictions.get("a", [])):
            return True

        # Check database-level allowlist
        if parent and action_checks.intersection(
            restrictions.get("d", {}).get(parent, [])
        ):
            return True

        # Check table-level allowlist
        if parent:
            table_restrictions = (restrictions.get("r", {}) or {}).get(parent, {})
            if child:
                table_actions = table_restrictions.get(child, [])
                if action_checks.intersection(table_actions):
                    return True
            else:
                # Parent query should proceed if any child in this database is allowlisted
                for table_actions in table_restrictions.values():
                    if action_checks.intersection(table_actions):
                        return True

        # Parent/child both None: include if any restrictions exist for this action
        if parent is None and child is None:
            if action_checks.intersection(restrictions.get("a", [])):
                return True
            if restricted_databases:
                return True
            if restricted_tables:
                return True

        return False

    rows = []

    def add_row(parent, child, result, scope):
        if result is None:
            return
        rows.append(
            (
                parent,
                child,
                bool(result),
                f"config {'allow' if result else 'deny'} {scope}",
            )
        )

    def add_row_allow_block(parent, child, allow_block, scope):
        """For 'allow' blocks, always add a row if the block exists - deny if no match"""
        if allow_block is None:
            return

        # If actor has restrictions and this resource is NOT in allowlist, skip this config rule
        # Restrictions act as a gating filter - config cannot grant access to restricted-out resources
        if not is_in_restriction_allowlist(parent, child, action):
            return

        result = evaluate(allow_block)
        bool_result = bool(result)
        # If result is None (no match) or False, treat as deny
        rows.append(
            (
                parent,
                child,
                bool_result,  # None becomes False, False stays False, True stays True
                f"config {'allow' if result else 'deny'} {scope}",
            )
        )
        if has_restrictions and not bool_result and child is None:
            reason = f"config deny {scope} (restriction gate)"
            if parent is None:
                # Root-level deny: add more specific denies for restricted resources
                if action_obj and action_obj.takes_parent:
                    for db_name in restricted_databases:
                        rows.append((db_name, None, 0, reason))
                if action_obj and action_obj.takes_child:
                    for db_name, table_name in restricted_tables:
                        rows.append((db_name, table_name, 0, reason))
            else:
                # Database-level deny: add child-level denies for restricted tables
                if action_obj and action_obj.takes_child:
                    for db_name, table_name in restricted_tables:
                        if db_name == parent:
                            rows.append((db_name, table_name, 0, reason))

    root_perm = (config.get("permissions") or {}).get(action)
    add_row(None, None, evaluate(root_perm), f"permissions for {action}")

    for db_name, db_config in (config.get("databases") or {}).items():
        db_perm = (db_config.get("permissions") or {}).get(action)
        add_row(
            db_name, None, evaluate(db_perm), f"permissions for {action} on {db_name}"
        )

        for table_name, table_config in (db_config.get("tables") or {}).items():
            table_perm = (table_config.get("permissions") or {}).get(action)
            add_row(
                db_name,
                table_name,
                evaluate(table_perm),
                f"permissions for {action} on {db_name}/{table_name}",
            )

            if action == "view-table":
                table_allow = (table_config or {}).get("allow")
                add_row_allow_block(
                    db_name,
                    table_name,
                    table_allow,
                    f"allow for {action} on {db_name}/{table_name}",
                )

        for query_name, query_config in (db_config.get("queries") or {}).items():
            # query_config can be a string (just SQL) or a dict (with SQL and options)
            if isinstance(query_config, dict):
                query_perm = (query_config.get("permissions") or {}).get(action)
                add_row(
                    db_name,
                    query_name,
                    evaluate(query_perm),
                    f"permissions for {action} on {db_name}/{query_name}",
                )
                if action == "view-query":
                    query_allow = query_config.get("allow")
                    add_row_allow_block(
                        db_name,
                        query_name,
                        query_allow,
                        f"allow for {action} on {db_name}/{query_name}",
                    )

        if action == "view-database":
            db_allow = db_config.get("allow")
            add_row_allow_block(
                db_name, None, db_allow, f"allow for {action} on {db_name}"
            )

        if action == "execute-sql":
            db_allow_sql = db_config.get("allow_sql")
            add_row_allow_block(db_name, None, db_allow_sql, f"allow_sql for {db_name}")

        if action == "view-table":
            # Database-level allow block affects all tables in that database
            db_allow = db_config.get("allow")
            add_row_allow_block(
                db_name, None, db_allow, f"allow for {action} on {db_name}"
            )

        if action == "view-query":
            # Database-level allow block affects all queries in that database
            db_allow = db_config.get("allow")
            add_row_allow_block(
                db_name, None, db_allow, f"allow for {action} on {db_name}"
            )

    # Root-level allow block applies to all view-* actions
    if action == "view-instance":
        allow_block = config.get("allow")
        add_row_allow_block(None, None, allow_block, "allow for view-instance")

    if action == "view-database":
        # Root-level allow block also applies to view-database
        allow_block = config.get("allow")
        add_row_allow_block(None, None, allow_block, "allow for view-database")

    if action == "view-table":
        # Root-level allow block also applies to view-table
        allow_block = config.get("allow")
        add_row_allow_block(None, None, allow_block, "allow for view-table")

    if action == "view-query":
        # Root-level allow block also applies to view-query
        allow_block = config.get("allow")
        add_row_allow_block(None, None, allow_block, "allow for view-query")

    if action == "execute-sql":
        allow_sql = config.get("allow_sql")
        add_row_allow_block(None, None, allow_sql, "allow_sql")

    if not rows:
        return []

    parts = []
    params = {}
    for idx, (parent, child, allow, reason) in enumerate(rows):
        key = f"cfg_{idx}"
        parts.append(
            f"SELECT :{key}_parent AS parent, :{key}_child AS child, :{key}_allow AS allow, :{key}_reason AS reason"
        )
        params[f"{key}_parent"] = parent
        params[f"{key}_child"] = child
        params[f"{key}_allow"] = 1 if allow else 0
        params[f"{key}_reason"] = reason

    sql = "\nUNION ALL\n".join(parts)
    return [PermissionSQL(sql=sql, params=params)]


@hookimpl(specname="permission_resources_sql")
async def default_allow_sql_check(datasette, actor, action):
    """Enforce default_allow_sql setting for execute-sql action."""
    if action == "execute-sql" and not datasette.setting("default_allow_sql"):
        return PermissionSQL.deny(reason="default_allow_sql is false")
    return None


@hookimpl(specname="permission_resources_sql")
async def default_action_permissions_sql(datasette, actor, action):
    """Apply default allow rules for standard view/execute actions.

    With the INTERSECT-based restriction approach, these defaults are always generated
    and then filtered by restriction_sql if the actor has restrictions.
    """
    default_allow_actions = {
        "view-instance",
        "view-database",
        "view-database-download",
        "view-table",
        "view-query",
        "execute-sql",
    }
    if action in default_allow_actions:
        reason = f"default allow for {action}".replace("'", "''")
        return PermissionSQL.allow(reason=reason)

    return None


def restrictions_allow_action(
    datasette: "Datasette",
    restrictions: dict,
    action: str,
    resource: str | tuple[str, str],
):
    """
    Check if actor restrictions allow the requested action against the requested resource.

    Restrictions work on an exact-match basis: if an actor has view-table permission,
    they can view tables, but NOT automatically view-instance or view-database.
    Each permission is checked independently without implication logic.
    """
    # Does this action have an abbreviation?
    to_check = {action}
    action_obj = datasette.actions.get(action)
    if action_obj and action_obj.abbr:
        to_check.add(action_obj.abbr)

    # Check if restrictions explicitly allow this action
    # Restrictions can be at three levels:
    # - "a": global (any resource)
    # - "d": per-database
    # - "r": per-table/resource

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


@hookimpl
def actor_from_request(datasette, request):
    prefix = "dstok_"
    if not datasette.setting("allow_signed_tokens"):
        return None
    max_signed_tokens_ttl = datasette.setting("max_signed_tokens_ttl")
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer ") :]
    if not token.startswith(prefix):
        return None
    token = token[len(prefix) :]
    try:
        decoded = datasette.unsign(token, namespace="token")
    except itsdangerous.BadSignature:
        return None
    if "t" not in decoded:
        # Missing timestamp
        return None
    created = decoded["t"]
    if not isinstance(created, int):
        # Invalid timestamp
        return None
    duration = decoded.get("d")
    if duration is not None and not isinstance(duration, int):
        # Invalid duration
        return None
    if (duration is None and max_signed_tokens_ttl) or (
        duration is not None
        and max_signed_tokens_ttl
        and duration > max_signed_tokens_ttl
    ):
        duration = max_signed_tokens_ttl
    if duration:
        if time.time() - created > duration:
            # Expired
            return None
    actor = {"id": decoded["a"], "token": "dstok"}
    if "_r" in decoded:
        actor["_r"] = decoded["_r"]
    if duration:
        actor["token_expires"] = created + duration
    return actor


@hookimpl
def skip_csrf(scope):
    # Skip CSRF check for requests with content-type: application/json
    if scope["type"] == "http":
        headers = scope.get("headers") or {}
        if dict(headers).get(b"content-type") == b"application/json":
            return True


@hookimpl
def canned_queries(datasette, database, actor):
    """Return canned queries from datasette configuration."""
    queries = (
        ((datasette.config or {}).get("databases") or {}).get(database) or {}
    ).get("queries") or {}
    return queries
