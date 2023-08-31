from datasette import hookimpl, Permission
from datasette.utils import actor_matches_allow
import itsdangerous
import time
from typing import Union, Tuple


@hookimpl
def register_permissions():
    return (
        # name, abbr, description, takes_database, takes_resource, default
        Permission(
            name="view-instance",
            abbr="vi",
            description="View Datasette instance",
            takes_database=False,
            takes_resource=False,
            default=True,
        ),
        Permission(
            name="view-database",
            abbr="vd",
            description="View database",
            takes_database=True,
            takes_resource=False,
            default=True,
            implies_can_view=True,
        ),
        Permission(
            name="view-database-download",
            abbr="vdd",
            description="Download database file",
            takes_database=True,
            takes_resource=False,
            default=True,
        ),
        Permission(
            name="view-table",
            abbr="vt",
            description="View table",
            takes_database=True,
            takes_resource=True,
            default=True,
            implies_can_view=True,
        ),
        Permission(
            name="view-query",
            abbr="vq",
            description="View named query results",
            takes_database=True,
            takes_resource=True,
            default=True,
            implies_can_view=True,
        ),
        Permission(
            name="execute-sql",
            abbr="es",
            description="Execute read-only SQL queries",
            takes_database=True,
            takes_resource=False,
            default=True,
            implies_can_view=True,
        ),
        Permission(
            name="permissions-debug",
            abbr="pd",
            description="Access permission debug tool",
            takes_database=False,
            takes_resource=False,
            default=False,
        ),
        Permission(
            name="debug-menu",
            abbr="dm",
            description="View debug menu items",
            takes_database=False,
            takes_resource=False,
            default=False,
        ),
        Permission(
            name="insert-row",
            abbr="ir",
            description="Insert rows",
            takes_database=True,
            takes_resource=True,
            default=False,
        ),
        Permission(
            name="delete-row",
            abbr="dr",
            description="Delete rows",
            takes_database=True,
            takes_resource=True,
            default=False,
        ),
        Permission(
            name="update-row",
            abbr="ur",
            description="Update rows",
            takes_database=True,
            takes_resource=True,
            default=False,
        ),
        Permission(
            name="create-table",
            abbr="ct",
            description="Create tables",
            takes_database=True,
            takes_resource=False,
            default=False,
        ),
        Permission(
            name="drop-table",
            abbr="dt",
            description="Drop tables",
            takes_database=True,
            takes_resource=True,
            default=False,
        ),
    )


@hookimpl(tryfirst=True, specname="permission_allowed")
def permission_allowed_default(datasette, actor, action, resource):
    async def inner():
        # id=root gets some special permissions:
        if action in (
            "permissions-debug",
            "debug-menu",
            "insert-row",
            "create-table",
            "drop-table",
            "delete-row",
            "update-row",
        ):
            if actor and actor.get("id") == "root":
                return True

        # Resolve metadata view permissions
        if action in (
            "view-instance",
            "view-database",
            "view-table",
            "view-query",
            "execute-sql",
        ):
            result = await _resolve_metadata_view_permissions(
                datasette, actor, action, resource
            )
            if result is not None:
                return result

        # Check custom permissions: blocks
        result = await _resolve_metadata_permissions_blocks(
            datasette, actor, action, resource
        )
        if result is not None:
            return result

        # --setting default_allow_sql
        if action == "execute-sql" and not datasette.setting("default_allow_sql"):
            return False

    return inner


async def _resolve_metadata_permissions_blocks(datasette, actor, action, resource):
    # Check custom permissions: blocks
    metadata = datasette.metadata()
    root_block = (metadata.get("permissions", None) or {}).get(action)
    if root_block:
        root_result = actor_matches_allow(actor, root_block)
        if root_result is not None:
            return root_result
    # Now try database-specific blocks
    if not resource:
        return None
    if isinstance(resource, str):
        database = resource
    else:
        database = resource[0]
    database_block = (
        (metadata.get("databases", {}).get(database, {}).get("permissions", None)) or {}
    ).get(action)
    if database_block:
        database_result = actor_matches_allow(actor, database_block)
        if database_result is not None:
            return database_result
    # Finally try table/query specific blocks
    if not isinstance(resource, tuple):
        return None
    database, table_or_query = resource
    table_block = (
        (
            metadata.get("databases", {})
            .get(database, {})
            .get("tables", {})
            .get(table_or_query, {})
            .get("permissions", None)
        )
        or {}
    ).get(action)
    if table_block:
        table_result = actor_matches_allow(actor, table_block)
        if table_result is not None:
            return table_result
    # Finally the canned queries
    query_block = (
        (
            metadata.get("databases", {})
            .get(database, {})
            .get("queries", {})
            .get(table_or_query, {})
            .get("permissions", None)
        )
        or {}
    ).get(action)
    if query_block:
        query_result = actor_matches_allow(actor, query_block)
        if query_result is not None:
            return query_result
    return None


async def _resolve_metadata_view_permissions(datasette, actor, action, resource):
    if action == "view-instance":
        allow = datasette.metadata("allow")
        if allow is not None:
            return actor_matches_allow(actor, allow)
    elif action == "view-database":
        database_allow = datasette.metadata("allow", database=resource)
        if database_allow is None:
            return None
        return actor_matches_allow(actor, database_allow)
    elif action == "view-table":
        database, table = resource
        tables = datasette.metadata("tables", database=database) or {}
        table_allow = (tables.get(table) or {}).get("allow")
        if table_allow is None:
            return None
        return actor_matches_allow(actor, table_allow)
    elif action == "view-query":
        # Check if this query has a "allow" block in metadata
        database, query_name = resource
        query = await datasette.get_canned_query(database, query_name, actor)
        assert query is not None
        allow = query.get("allow")
        if allow is None:
            return None
        return actor_matches_allow(actor, allow)
    elif action == "execute-sql":
        # Use allow_sql block from database block, or from top-level
        database_allow_sql = datasette.metadata("allow_sql", database=resource)
        if database_allow_sql is None:
            database_allow_sql = datasette.metadata("allow_sql")
        if database_allow_sql is None:
            return None
        return actor_matches_allow(actor, database_allow_sql)


def restrictions_allow_action(
    datasette: "Datasette",
    restrictions: dict,
    action: str,
    resource: Union[str, Tuple[str, str]],
):
    "Do these restrictions allow the requested action against the requested resource?"
    if action == "view-instance":
        # Special case for view-instance: it's allowed if the restrictions include any
        # permissions that have the implies_can_view=True flag set
        all_rules = restrictions.get("a") or []
        for database_rules in (restrictions.get("d") or {}).values():
            all_rules += database_rules
        for database_resource_rules in (restrictions.get("r") or {}).values():
            for resource_rules in database_resource_rules.values():
                all_rules += resource_rules
        permissions = [datasette.get_permission(action) for action in all_rules]
        if any(p for p in permissions if p.implies_can_view):
            return True

    if action == "view-database":
        # Special case for view-database: it's allowed if the restrictions include any
        # permissions that have the implies_can_view=True flag set AND takes_database
        all_rules = restrictions.get("a") or []
        database_rules = list((restrictions.get("d") or {}).get(resource) or [])
        all_rules += database_rules
        resource_rules = ((restrictions.get("r") or {}).get(resource) or {}).values()
        for resource_rules in (restrictions.get("r") or {}).values():
            for table_rules in resource_rules.values():
                all_rules += table_rules
        permissions = [datasette.get_permission(action) for action in all_rules]
        if any(p for p in permissions if p.implies_can_view and p.takes_database):
            return True

    # Does this action have an abbreviation?
    to_check = {action}
    permission = datasette.permissions.get(action)
    if permission and permission.abbr:
        to_check.add(permission.abbr)

    # If restrictions is defined then we use those to further restrict the actor
    # Crucially, we only use this to say NO (return False) - we never
    # use it to return YES (True) because that might over-ride other
    # restrictions placed on this actor
    all_allowed = restrictions.get("a")
    if all_allowed is not None:
        assert isinstance(all_allowed, list)
        if to_check.intersection(all_allowed):
            return True
    # How about for the current database?
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
    # Or the current table? That's any time the resource is (database, table)
    if resource is not None and not isinstance(resource, str) and len(resource) == 2:
        database, table = resource
        table_allowed = restrictions.get("r", {}).get(database, {}).get(table)
        # TODO: What should this do for canned queries?
        if table_allowed is not None:
            assert isinstance(table_allowed, list)
            if to_check.intersection(table_allowed):
                return True

    # This action is not specifically allowed, so reject it
    return False


@hookimpl(specname="permission_allowed")
def permission_allowed_actor_restrictions(datasette, actor, action, resource):
    if actor is None:
        return None
    if "_r" not in actor:
        # No restrictions, so we have no opinion
        return None
    _r = actor.get("_r")
    if restrictions_allow_action(datasette, _r, action, resource):
        # Return None because we do not have an opinion here
        return None
    else:
        # Block this permission check
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
