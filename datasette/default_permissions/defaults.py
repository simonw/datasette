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
        if not datasette.setting("default_allow_sql"):
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


@hookimpl(specname="permission_resources_sql")
async def default_query_permissions_sql(
    datasette: "Datasette",
    actor: Optional[dict],
    action: str,
) -> Optional[PermissionSQL]:
    actor_id = actor.get("id") if isinstance(actor, dict) else None

    if action in {"update-query", "delete-query"}:
        if actor_id is None:
            return None
        return PermissionSQL(
            sql="""
            SELECT database_name AS parent, name AS child, 1 AS allow,
              'query owner' AS reason
            FROM queries
            WHERE source = 'user'
              AND owner_id = :query_owner_id
            """,
            params={"query_owner_id": actor_id},
        )

    if action != "view-query":
        return None

    execute_sql = await datasette.allowed_resources_sql(
        action="execute-sql", actor=actor
    )
    sql = execute_sql.sql
    params = {}
    for key, value in execute_sql.params.items():
        new_key = f"query_execute_sql_{key}"
        sql = sql.replace(f":{key}", f":{new_key}")
        params[new_key] = value

    trusted_writable_sql = ""
    if not datasette.default_deny:
        trusted_writable_sql = """
            UNION ALL
            SELECT database_name AS parent, name AS child, 1 AS allow,
              'trusted writable query' AS reason
            FROM queries
            WHERE is_write = 1
              AND source IN ('config', 'plugin')
        """

    user_writable_sql = ""
    if actor_id is not None:
        params["query_owner_id"] = actor_id
        user_writable_sql = """
            UNION ALL
            SELECT database_name AS parent, name AS child, 1 AS allow,
              'query owner' AS reason
            FROM queries
            WHERE is_write = 1
              AND source = 'user'
              AND owner_id = :query_owner_id
        """

    return PermissionSQL(
        sql=f"""
        WITH execute_sql_allowed AS (
            {sql}
        )
        SELECT database_name AS parent, name AS child, 1 AS allow,
          'published query' AS reason
        FROM queries
        WHERE is_write = 0
          AND published = 1
        UNION ALL
        SELECT q.database_name AS parent, q.name AS child, 1 AS allow,
          'execute-sql allows query' AS reason
        FROM queries q
        JOIN execute_sql_allowed es
          ON es.parent = q.database_name
         AND es.child IS NULL
        WHERE q.is_write = 0
          AND q.published = 0
        {trusted_writable_sql}
        {user_writable_sql}
        """,
        params=params,
    )
