from datasette import hookimpl
from datasette.permissions import PermissionSQL
from datasette.utils import actor_matches_allow
import itsdangerous
import time


@hookimpl
async def permission_resources_sql(datasette, actor, action):
    rules: list[PermissionSQL] = []

    # Root user with root_enabled gets all permissions at global level
    # Config rules at more specific levels (database/table) can still override
    if datasette.root_enabled and actor and actor.get("id") == "root":
        # Add a single global-level allow rule (NULL, NULL) for root
        # This allows root to access everything by default, but database-level
        # and table-level deny rules in config can still block specific resources
        sql = "SELECT NULL AS parent, NULL AS child, 1 AS allow, 'root user' AS reason"
        rules.append(
            PermissionSQL(
                source="root_permissions",
                sql=sql,
                params={},
            )
        )

    config_rules = await _config_permission_rules(datasette, actor, action)
    rules.extend(config_rules)

    # Check default_allow_sql setting for execute-sql action
    if action == "execute-sql" and not datasette.setting("default_allow_sql"):
        # Return a deny rule for all databases
        sql = "SELECT NULL AS parent, NULL AS child, 0 AS allow, 'default_allow_sql is false' AS reason"
        rules.append(
            PermissionSQL(
                source="default_allow_sql_setting",
                sql=sql,
                params={},
            )
        )
        # Early return - don't add default allow rule
        if not rules:
            return None
        if len(rules) == 1:
            return rules[0]
        return rules

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
        sql = (
            "SELECT NULL AS parent, NULL AS child, 1 AS allow, " f"'{reason}' AS reason"
        )
        rules.append(
            PermissionSQL(
                source="default_permissions",
                sql=sql,
                params={},
            )
        )

    if not rules:
        return None
    if len(rules) == 1:
        return rules[0]
    return rules


async def _config_permission_rules(datasette, actor, action) -> list[PermissionSQL]:
    config = datasette.config or {}

    if actor is None:
        actor_dict: dict | None = None
    elif isinstance(actor, dict):
        actor_dict = actor
    else:
        actor_lookup = await datasette.actors_from_ids([actor])
        actor_dict = actor_lookup.get(actor) or {"id": actor}

    def evaluate(allow_block):
        if allow_block is None:
            return None
        return actor_matches_allow(actor_dict, allow_block)

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
        result = evaluate(allow_block)
        # If result is None (no match) or False, treat as deny
        rows.append(
            (
                parent,
                child,
                bool(result),  # None becomes False, False stays False, True stays True
                f"config {'allow' if result else 'deny'} {scope}",
            )
        )

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
    return [PermissionSQL(source="config_permissions", sql=sql, params=params)]


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
