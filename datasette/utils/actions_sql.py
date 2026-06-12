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

import asyncio
import re
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
    base_resources_sql = await action_obj.resource_class.resources_sql(
        datasette, actor=actor
    )

    permission_sqls = await gather_permission_sql_from_hooks(
        datasette=datasette,
        actor=actor,
        action=action,
    )

    # If permission_sqls is the sentinel, skip all permission checks
    # Return SQL that allows all resources
    from datasette.utils.permissions import SKIP_PERMISSION_CHECKS

    if permission_sqls is SKIP_PERMISSION_CHECKS:
        cols = "parent, child, 'skip_permission_checks' AS reason"
        if include_is_private:
            cols += ", 0 AS is_private"
        return f"SELECT {cols} FROM ({base_resources_sql})", {}

    all_params = {}
    rule_sqls = []
    restriction_sqls = []

    for permission_sql in permission_sqls:
        # Always collect params (even from restriction-only plugins)
        all_params.update(permission_sql.params or {})

        # Collect restriction SQL filters
        if permission_sql.restriction_sql:
            restriction_sqls.append(permission_sql.restriction_sql)

        # Skip plugins that only provide restriction_sql (no permission rules)
        if permission_sql.sql is None:
            continue
        rule_sqls.append(f"""
            SELECT parent, child, allow, reason, '{permission_sql.source}' AS source_plugin FROM (
                {permission_sql.sql}
            )
            """.strip())

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
            # Skip plugins that only provide restriction_sql (no permission rules)
            if permission_sql.sql is None:
                continue
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
        else:
            query_parts.extend(
                [
                    "anon_rules AS (",
                    "  SELECT NULL AS parent, NULL AS child, 0 AS allow, NULL AS reason WHERE 0",
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

    # Add restriction list CTE if there are restrictions
    if restriction_sqls:
        # Wrap each restriction_sql in a subquery to avoid operator precedence issues
        # with UNION ALL inside the restriction SQL statements
        restriction_intersect = "\nINTERSECT\n".join(
            f"SELECT * FROM ({sql})" for sql in restriction_sqls
        )
        query_parts.extend(
            [",", "restriction_list AS (", f"  {restriction_intersect}", ")"]
        )

    # Final SELECT
    select_cols = "parent, child, reason"
    if include_is_private:
        select_cols += ", is_private"

    query_parts.append(f"SELECT {select_cols}")
    query_parts.append("FROM decisions")
    query_parts.append("WHERE is_allowed = 1")

    # Add restriction filter if there are restrictions
    if restriction_sqls:
        query_parts.append("""
  AND EXISTS (
    SELECT 1 FROM restriction_list r
    WHERE (r.parent = decisions.parent OR r.parent IS NULL)
      AND (r.child = decisions.child OR r.child IS NULL)
  )""")

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

    # If permission_sqls is the sentinel, skip all permission checks
    # Return SQL that allows everything
    from datasette.utils.permissions import SKIP_PERMISSION_CHECKS

    if permission_sqls is SKIP_PERMISSION_CHECKS:
        return (
            "SELECT NULL AS parent, NULL AS child, 1 AS allow, 'skip_permission_checks' AS reason, 'skip' AS source_plugin",
            {},
            [],
        )

    if not permission_sqls:
        return (
            "SELECT NULL AS parent, NULL AS child, 0 AS allow, NULL AS reason, NULL AS source_plugin WHERE 0",
            {},
            [],
        )

    union_parts = []
    all_params = {}
    restriction_sqls = []

    for permission_sql in permission_sqls:
        all_params.update(permission_sql.params or {})

        # Collect restriction SQL filters
        if permission_sql.restriction_sql:
            restriction_sqls.append(permission_sql.restriction_sql)

        # Skip plugins that only provide restriction_sql (no permission rules)
        if permission_sql.sql is None:
            continue

        union_parts.append(f"""
            SELECT parent, child, allow, reason, '{permission_sql.source}' AS source_plugin FROM (
                {permission_sql.sql}
            )
            """.strip())

    rules_union = " UNION ALL ".join(union_parts)
    return rules_union, all_params, restriction_sqls


async def check_permissions_for_actions(
    *,
    datasette: "Datasette",
    actor: dict | None,
    actions: list[str],
    parent: str | None,
    child: str | None,
) -> dict[str, bool]:
    """
    Check several actions for one actor and resource in a single query.

    Args:
        datasette: The Datasette instance
        actor: The actor dict (or None)
        actions: List of action names to check
        parent: The parent resource identifier (e.g., database name, or None)
        child: The child resource identifier (e.g., table name, or None)

    Returns:
        Dict mapping each action name to True (allowed) or False (denied)

    Each action contributes its own tagged block of permission rules
    (gathered from the permission_resources_sql hook, with parameters
    namespaced per action to avoid collisions) plus an optional
    restriction allowlist CTE. One internal database query resolves
    the winning rule per action using the same specificity-then-deny
    ordering as the rest of the permission system.

    Note: this resolves each action independently - also_requires
    dependencies are handled by the caller (Datasette.allowed_many).
    """
    from datasette.utils.permissions import SKIP_PERMISSION_CHECKS

    for action in actions:
        if not datasette.actions.get(action):
            raise ValueError(f"Unknown action: {action}")

    # Dedupe while preserving order
    unique_actions = list(dict.fromkeys(actions))
    if not unique_actions:
        return {}

    # Gather hook results for each action concurrently - hooks within a
    # single action still run sequentially, preserving existing semantics
    gathered = await asyncio.gather(
        *(
            gather_permission_sql_from_hooks(
                datasette=datasette, actor=actor, action=action
            )
            for action in unique_actions
        )
    )

    if any(result is SKIP_PERMISSION_CHECKS for result in gathered):
        return {action: True for action in unique_actions}

    params = {"_check_parent": parent, "_check_child": child}
    ctes = []
    selects = []
    verdicts = {}

    for i, (action, permission_sqls) in enumerate(zip(unique_actions, gathered)):
        prefix = f"a{i}_"
        rule_parts = []
        restriction_parts = []

        for permission_sql in permission_sqls:
            sql = permission_sql.sql
            restriction_sql = permission_sql.restriction_sql
            # Namespace this block's params so identical names used for
            # different actions cannot collide
            for key in permission_sql.params or {}:
                new_key = prefix + key
                params[new_key] = permission_sql.params[key]
                pattern = re.compile(":" + re.escape(key) + r"(?![A-Za-z0-9_])")
                if sql:
                    sql = pattern.sub(":" + new_key, sql)
                if restriction_sql:
                    restriction_sql = pattern.sub(":" + new_key, restriction_sql)

            if restriction_sql:
                restriction_parts.append(restriction_sql)

            # Skip plugins that only provide restriction_sql (no permission rules)
            if sql is None:
                continue
            rule_parts.append(
                f"SELECT parent, child, allow, reason, '{permission_sql.source}' AS source_plugin FROM (\n{sql}\n)"
            )

        if not rule_parts:
            # No rules from any plugin - default deny. Restrictions can
            # only restrict, never grant, so no SQL is needed at all
            verdicts[action] = False
            continue
        ctes.append(f"a{i}_rules AS (\n" + "\nUNION ALL\n".join(rule_parts) + "\n)")

        # Winning rule for this action: most specific depth first, then
        # deny-beats-allow, then source_plugin as a stable tie-break
        verdict_sql = f"""COALESCE((
  SELECT allow FROM (
    SELECT allow, source_plugin,
      CASE
        WHEN child IS NOT NULL THEN 2
        WHEN parent IS NOT NULL THEN 1
        ELSE 0
      END AS depth
    FROM a{i}_rules
    WHERE (parent IS NULL OR parent = :_check_parent)
      AND (child IS NULL OR child = :_check_child)
    ORDER BY
      depth DESC,
      CASE WHEN allow = 0 THEN 0 ELSE 1 END,
      source_plugin
    LIMIT 1
  )
), 0)"""

        if restriction_parts:
            # Database-level restrictions (parent, NULL) match all children
            restriction_intersect = "\nINTERSECT\n".join(
                f"SELECT * FROM ({sql})" for sql in restriction_parts
            )
            ctes.append(f"a{i}_restriction AS (\n{restriction_intersect}\n)")
            verdict_sql = f"""({verdict_sql}) AND EXISTS (
  SELECT 1 FROM a{i}_restriction r
  WHERE (r.parent = :_check_parent OR r.parent IS NULL)
    AND (r.child = :_check_child OR r.child IS NULL)
)"""

        selects.append(f"SELECT {i} AS action_idx, ({verdict_sql}) AS is_allowed")

    if selects:
        query = "WITH\n" + ",\n".join(ctes) + "\n" + "\nUNION ALL\n".join(selects)
        result = await datasette.get_internal_database().execute(query, params)
        for row in result.rows:
            verdicts[unique_actions[row[0]]] = bool(row[1])
    return verdicts


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
    """
    results = await check_permissions_for_actions(
        datasette=datasette,
        actor=actor,
        actions=[action],
        parent=parent,
        child=child,
    )
    return results[action]
