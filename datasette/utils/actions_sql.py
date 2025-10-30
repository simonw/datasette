"""
SQL query builder for hierarchical permission checking.

This module implements a cascading permission system based on the pattern
from https://github.com/simonw/research/tree/main/sqlite-permissions-poc

It builds SQL queries that:

1. Start with all resources of a given type (from resource_type.resources_sql())
2. Gather permission rules from plugins (via permission_resources_sql hook)
3. Apply cascading logic: child → parent → global
4. Apply DENY-beats-ALLOW at each level

The core pattern is:
- Resources are identified by (parent, child) tuples
- Rules are evaluated at three levels:
  - child: exact match on (parent, child)
  - parent: match on (parent, NULL)
  - global: match on (NULL, NULL)
- At the same level, DENY (allow=0) beats ALLOW (allow=1)
- Across levels, child beats parent beats global
"""

from typing import TYPE_CHECKING

from datasette.utils.permissions import gather_permission_sql_from_hooks

if TYPE_CHECKING:
    from datasette.app import Datasette


async def build_allowed_resources_sql(
    datasette: "Datasette",
    actor: dict | None,
    action: str,
    *,
    parent: str | None = None,
    include_is_private: bool = False,
) -> tuple[str, dict]:
    """
    Build a SQL query that returns all resources the actor can access for this action.

    Args:
        datasette: The Datasette instance
        actor: The actor dict (or None for unauthenticated)
        action: The action name (e.g., "view-table", "view-database")
        parent: Optional parent filter to limit results (e.g., database name)
        include_is_private: If True, add is_private column showing if anonymous cannot access

    Returns:
        A tuple of (sql_query, params_dict)

    The returned SQL query will have three columns (or four with include_is_private):
        - parent: The parent resource identifier (or NULL)
        - child: The child resource identifier (or NULL)
        - reason: The reason from the rule that granted access
        - is_private: (if include_is_private) 1 if anonymous cannot access, 0 otherwise

    Example:
        For action="view-table", this might return:
        SELECT parent, child, reason FROM ... WHERE is_allowed = 1

        Results would be like:
        ('analytics', 'users', 'role-based: analysts can access analytics DB')
        ('analytics', 'events', 'role-based: analysts can access analytics DB')
        ('production', 'orders', 'business-exception: allow production.orders for carol')
    """
    # Get the Action object
    action_obj = datasette.actions.get(action)
    if not action_obj:
        raise ValueError(f"Unknown action: {action}")

    # If this action also_requires another action, we need to combine the queries
    if action_obj.also_requires:
        # Build both queries
        main_sql, main_params = await _build_single_action_sql(
            datasette,
            actor,
            action,
            parent=parent,
            include_is_private=include_is_private,
        )
        required_sql, required_params = await _build_single_action_sql(
            datasette,
            actor,
            action_obj.also_requires,
            parent=parent,
            include_is_private=False,
        )

        # Merge parameters - they should have identical values for :actor, :actor_id, etc.
        all_params = {**main_params, **required_params}
        if parent is not None:
            all_params["filter_parent"] = parent

        # Combine with INNER JOIN - only resources allowed by both actions
        combined_sql = f"""
WITH
main_allowed AS (
{main_sql}
),
required_allowed AS (
{required_sql}
)
SELECT m.parent, m.child, m.reason"""

        if include_is_private:
            combined_sql += ", m.is_private"

        combined_sql += """
FROM main_allowed m
INNER JOIN required_allowed r
  ON ((m.parent = r.parent) OR (m.parent IS NULL AND r.parent IS NULL))
 AND ((m.child = r.child) OR (m.child IS NULL AND r.child IS NULL))
"""

        if parent is not None:
            combined_sql += "WHERE m.parent = :filter_parent\n"

        combined_sql += "ORDER BY m.parent, m.child"

        return combined_sql, all_params

    # No also_requires, build single action query
    return await _build_single_action_sql(
        datasette, actor, action, parent=parent, include_is_private=include_is_private
    )


async def _build_single_action_sql(
    datasette: "Datasette",
    actor: dict | None,
    action: str,
    *,
    parent: str | None = None,
    include_is_private: bool = False,
) -> tuple[str, dict]:
    """
    Build SQL for a single action (internal helper for build_allowed_resources_sql).

    This contains the original logic from build_allowed_resources_sql, extracted
    to allow combining multiple actions when also_requires is used.
    """
    # Get the Action object
    action_obj = datasette.actions.get(action)
    if not action_obj:
        raise ValueError(f"Unknown action: {action}")

    # Get base resources SQL from the resource class
    base_resources_sql = await action_obj.resource_class.resources_sql(datasette)

    permission_sqls = await gather_permission_sql_from_hooks(
        datasette=datasette,
        actor=actor,
        action=action,
    )

    all_params = {}
    rule_sqls = []

    for permission_sql in permission_sqls:
        rule_sqls.append(
            f"""
            SELECT parent, child, allow, reason, '{permission_sql.source}' AS source_plugin FROM (
                {permission_sql.sql}
            )
            """.strip()
        )
        all_params.update(permission_sql.params or {})

    # If no rules, return empty result (deny all)
    if not rule_sqls:
        empty_cols = "NULL AS parent, NULL AS child, NULL AS reason"
        if include_is_private:
            empty_cols += ", NULL AS is_private"
        return f"SELECT {empty_cols} WHERE 0", {}

    # Build the cascading permission query
    rules_union = " UNION ALL ".join(rule_sqls)

    # Build the main query
    query_parts = [
        "WITH",
        "base AS (",
        f"  {base_resources_sql}",
        "),",
        "all_rules AS (",
        f"  {rules_union}",
        "),",
    ]

    # If include_is_private, we need to build anonymous permissions too
    if include_is_private:
        anon_permission_sqls = await gather_permission_sql_from_hooks(
            datasette=datasette,
            actor=None,
            action=action,
        )
        anon_sqls_rewritten = []
        anon_params = {}

        for permission_sql in anon_permission_sqls:
            rewritten_sql = permission_sql.sql
            for key, value in (permission_sql.params or {}).items():
                anon_key = f"anon_{key}"
                anon_params[anon_key] = value
                rewritten_sql = rewritten_sql.replace(f":{key}", f":{anon_key}")
            anon_sqls_rewritten.append(rewritten_sql)

        all_params.update(anon_params)

        if anon_sqls_rewritten:
            anon_rules_union = " UNION ALL ".join(anon_sqls_rewritten)
            query_parts.extend(
                [
                    "anon_rules AS (",
                    f"  {anon_rules_union}",
                    "),",
                ]
            )

    # Continue with the cascading logic
    query_parts.extend(
        [
            "child_lvl AS (",
            "  SELECT b.parent, b.child,",
            "         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,",
            "         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow,",
            "         json_group_array(CASE WHEN ar.allow = 0 THEN ar.source_plugin || ': ' || ar.reason END) AS deny_reasons,",
            "         json_group_array(CASE WHEN ar.allow = 1 THEN ar.source_plugin || ': ' || ar.reason END) AS allow_reasons",
            "  FROM base b",
            "  LEFT JOIN all_rules ar ON ar.parent = b.parent AND ar.child = b.child",
            "  GROUP BY b.parent, b.child",
            "),",
            "parent_lvl AS (",
            "  SELECT b.parent, b.child,",
            "         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,",
            "         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow,",
            "         json_group_array(CASE WHEN ar.allow = 0 THEN ar.source_plugin || ': ' || ar.reason END) AS deny_reasons,",
            "         json_group_array(CASE WHEN ar.allow = 1 THEN ar.source_plugin || ': ' || ar.reason END) AS allow_reasons",
            "  FROM base b",
            "  LEFT JOIN all_rules ar ON ar.parent = b.parent AND ar.child IS NULL",
            "  GROUP BY b.parent, b.child",
            "),",
            "global_lvl AS (",
            "  SELECT b.parent, b.child,",
            "         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,",
            "         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow,",
            "         json_group_array(CASE WHEN ar.allow = 0 THEN ar.source_plugin || ': ' || ar.reason END) AS deny_reasons,",
            "         json_group_array(CASE WHEN ar.allow = 1 THEN ar.source_plugin || ': ' || ar.reason END) AS allow_reasons",
            "  FROM base b",
            "  LEFT JOIN all_rules ar ON ar.parent IS NULL AND ar.child IS NULL",
            "  GROUP BY b.parent, b.child",
            "),",
        ]
    )

    # Add anonymous decision logic if needed
    if include_is_private:
        query_parts.extend(
            [
                "anon_child_lvl AS (",
                "  SELECT b.parent, b.child,",
                "         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,",
                "         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow",
                "  FROM base b",
                "  LEFT JOIN anon_rules ar ON ar.parent = b.parent AND ar.child = b.child",
                "  GROUP BY b.parent, b.child",
                "),",
                "anon_parent_lvl AS (",
                "  SELECT b.parent, b.child,",
                "         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,",
                "         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow",
                "  FROM base b",
                "  LEFT JOIN anon_rules ar ON ar.parent = b.parent AND ar.child IS NULL",
                "  GROUP BY b.parent, b.child",
                "),",
                "anon_global_lvl AS (",
                "  SELECT b.parent, b.child,",
                "         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,",
                "         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow",
                "  FROM base b",
                "  LEFT JOIN anon_rules ar ON ar.parent IS NULL AND ar.child IS NULL",
                "  GROUP BY b.parent, b.child",
                "),",
                "anon_decisions AS (",
                "  SELECT",
                "    b.parent, b.child,",
                "    CASE",
                "      WHEN acl.any_deny = 1 THEN 0",
                "      WHEN acl.any_allow = 1 THEN 1",
                "      WHEN apl.any_deny = 1 THEN 0",
                "      WHEN apl.any_allow = 1 THEN 1",
                "      WHEN agl.any_deny = 1 THEN 0",
                "      WHEN agl.any_allow = 1 THEN 1",
                "      ELSE 0",
                "    END AS anon_is_allowed",
                "  FROM base b",
                "  JOIN anon_child_lvl acl ON b.parent = acl.parent AND (b.child = acl.child OR (b.child IS NULL AND acl.child IS NULL))",
                "  JOIN anon_parent_lvl apl ON b.parent = apl.parent AND (b.child = apl.child OR (b.child IS NULL AND apl.child IS NULL))",
                "  JOIN anon_global_lvl agl ON b.parent = agl.parent AND (b.child = agl.child OR (b.child IS NULL AND agl.child IS NULL))",
                "),",
            ]
        )

    # Final decisions
    query_parts.extend(
        [
            "decisions AS (",
            "  SELECT",
            "    b.parent, b.child,",
            "    -- Cascading permission logic: child → parent → global, DENY beats ALLOW at each level",
            "    -- Priority order:",
            "    --   1. Child-level deny (most specific, blocks access)",
            "    --   2. Child-level allow (most specific, grants access)",
            "    --   3. Parent-level deny (intermediate, blocks access)",
            "    --   4. Parent-level allow (intermediate, grants access)",
            "    --   5. Global-level deny (least specific, blocks access)",
            "    --   6. Global-level allow (least specific, grants access)",
            "    --   7. Default deny (no rules match)",
            "    CASE",
            "      WHEN cl.any_deny = 1 THEN 0",
            "      WHEN cl.any_allow = 1 THEN 1",
            "      WHEN pl.any_deny = 1 THEN 0",
            "      WHEN pl.any_allow = 1 THEN 1",
            "      WHEN gl.any_deny = 1 THEN 0",
            "      WHEN gl.any_allow = 1 THEN 1",
            "      ELSE 0",
            "    END AS is_allowed,",
            "    CASE",
            "      WHEN cl.any_deny = 1 THEN cl.deny_reasons",
            "      WHEN cl.any_allow = 1 THEN cl.allow_reasons",
            "      WHEN pl.any_deny = 1 THEN pl.deny_reasons",
            "      WHEN pl.any_allow = 1 THEN pl.allow_reasons",
            "      WHEN gl.any_deny = 1 THEN gl.deny_reasons",
            "      WHEN gl.any_allow = 1 THEN gl.allow_reasons",
            "      ELSE '[]'",
            "    END AS reason",
        ]
    )

    if include_is_private:
        query_parts.append(
            "    , CASE WHEN ad.anon_is_allowed = 0 THEN 1 ELSE 0 END AS is_private"
        )

    query_parts.extend(
        [
            "  FROM base b",
            "  JOIN child_lvl cl ON b.parent = cl.parent AND (b.child = cl.child OR (b.child IS NULL AND cl.child IS NULL))",
            "  JOIN parent_lvl pl ON b.parent = pl.parent AND (b.child = pl.child OR (b.child IS NULL AND pl.child IS NULL))",
            "  JOIN global_lvl gl ON b.parent = gl.parent AND (b.child = gl.child OR (b.child IS NULL AND gl.child IS NULL))",
        ]
    )

    if include_is_private:
        query_parts.append(
            "  JOIN anon_decisions ad ON b.parent = ad.parent AND (b.child = ad.child OR (b.child IS NULL AND ad.child IS NULL))"
        )

    query_parts.append(")")

    # Final SELECT
    select_cols = "parent, child, reason"
    if include_is_private:
        select_cols += ", is_private"

    query_parts.append(f"SELECT {select_cols}")
    query_parts.append("FROM decisions")
    query_parts.append("WHERE is_allowed = 1")

    # Add parent filter if specified
    if parent is not None:
        query_parts.append("  AND parent = :filter_parent")
        all_params["filter_parent"] = parent

    query_parts.append("ORDER BY parent, child")

    query = "\n".join(query_parts)
    return query, all_params


async def build_permission_rules_sql(
    datasette: "Datasette", actor: dict | None, action: str
) -> tuple[str, dict]:
    """
    Build the UNION SQL and params for all permission rules for a given actor and action.

    Returns:
        A tuple of (sql, params) where sql is a UNION ALL query that returns
        (parent, child, allow, reason, source_plugin) rows.
    """
    # Get the Action object
    action_obj = datasette.actions.get(action)
    if not action_obj:
        raise ValueError(f"Unknown action: {action}")

    permission_sqls = await gather_permission_sql_from_hooks(
        datasette=datasette,
        actor=actor,
        action=action,
    )

    if not permission_sqls:
        return (
            "SELECT NULL AS parent, NULL AS child, 0 AS allow, NULL AS reason, NULL AS source_plugin WHERE 0",
            {},
        )

    union_parts = []
    all_params = {}
    for permission_sql in permission_sqls:
        union_parts.append(
            f"""
            SELECT parent, child, allow, reason, '{permission_sql.source}' AS source_plugin FROM (
                {permission_sql.sql}
            )
            """.strip()
        )
        all_params.update(permission_sql.params or {})

    rules_union = " UNION ALL ".join(union_parts)
    return rules_union, all_params


async def check_permission_for_resource(
    *,
    datasette: "Datasette",
    actor: dict | None,
    action: str,
    parent: str | None,
    child: str | None,
) -> bool:
    """
    Check if an actor has permission for a specific action on a specific resource.

    Args:
        datasette: The Datasette instance
        actor: The actor dict (or None)
        action: The action name
        parent: The parent resource identifier (e.g., database name, or None)
        child: The child resource identifier (e.g., table name, or None)

    Returns:
        True if the actor is allowed, False otherwise

    This builds the cascading permission query and checks if the specific
    resource is in the allowed set.
    """
    rules_union, all_params = await build_permission_rules_sql(datasette, actor, action)

    # If no rules (empty SQL), default deny
    if not rules_union:
        return False

    # Add parameters for the resource we're checking
    all_params["_check_parent"] = parent
    all_params["_check_child"] = child

    query = f"""
WITH
all_rules AS (
  {rules_union}
),
child_lvl AS (
  SELECT
         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,
         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow
  FROM all_rules ar
  WHERE ar.parent = :_check_parent AND ar.child = :_check_child
),
parent_lvl AS (
  SELECT
         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,
         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow
  FROM all_rules ar
  WHERE ar.parent = :_check_parent AND ar.child IS NULL
),
global_lvl AS (
  SELECT
         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,
         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow
  FROM all_rules ar
  WHERE ar.parent IS NULL AND ar.child IS NULL
)
SELECT
  CASE
    WHEN cl.any_deny = 1 THEN 0
    WHEN cl.any_allow = 1 THEN 1
    WHEN pl.any_deny = 1 THEN 0
    WHEN pl.any_allow = 1 THEN 1
    WHEN gl.any_deny = 1 THEN 0
    WHEN gl.any_allow = 1 THEN 1
    ELSE 0
  END AS is_allowed
FROM child_lvl cl, parent_lvl pl, global_lvl gl
"""

    # Execute the query against the internal database
    result = await datasette.get_internal_database().execute(query, all_params)
    if result.rows:
        return bool(result.rows[0][0])
    return False
