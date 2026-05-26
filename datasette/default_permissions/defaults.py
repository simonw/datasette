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


def _configured_query_restriction_selects(datasette: "Datasette") -> tuple[list[str], dict]:
    selects = []
    params = {}
    for index, (database_name, db_config) in enumerate(
        ((datasette.config or {}).get("databases") or {}).items()
    ):
        for query_name, query_config in (db_config.get("queries") or {}).items():
            if isinstance(query_config, dict) and query_config.get("is_private"):
                continue
            parent_param = f"query_config_parent_{index}_{len(selects)}"
            child_param = f"query_config_child_{index}_{len(selects)}"
            selects.append(
                f"""
                SELECT :{parent_param} AS parent, :{child_param} AS child
                WHERE NOT EXISTS (
                    SELECT 1 FROM queries
                    WHERE database_name = :{parent_param}
                      AND name = :{child_param}
                )
                """
            )
            params[parent_param] = database_name
            params[child_param] = query_name
    return selects, params


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

    params = {"query_owner_id": actor_id}
    rule_sqls = []
    if not datasette.default_deny:
        rule_sqls.append(
            """
            SELECT database_name AS parent, name AS child, 1 AS allow,
              'non-private query' AS reason
            FROM queries
            WHERE is_private = 0
            """
        )

    if actor_id is not None:
        rule_sqls.append(
            """
            SELECT database_name AS parent, name AS child, 1 AS allow,
              'query owner' AS reason
            FROM queries
            WHERE owner_id = :query_owner_id
            """
        )

    config_restriction_selects, config_restriction_params = (
        _configured_query_restriction_selects(datasette)
    )

    restriction_sqls = [
        """
        SELECT database_name AS parent, name AS child
        FROM queries
        WHERE is_private = 0
           OR owner_id = :query_owner_id
        """
    ]
    restriction_sqls.extend(config_restriction_selects)
    params.update(config_restriction_params)

    return PermissionSQL(
        sql="\nUNION ALL\n".join(rule_sqls) if rule_sqls else None,
        restriction_sql="\nUNION ALL\n".join(restriction_sqls),
        params=params,
    )
