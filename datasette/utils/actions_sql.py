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

    # Continue with the cascading logic.
    # Aggregate the RULES by cascade level (small), rather than grouping
    # base x rules (which scales with the number of resources).
    def _agg(select_key, where, group_by):
        parts = [
            f"  SELECT {select_key}",
            "         MAX(CASE WHEN allow = 0 THEN 1 ELSE 0 END) AS any_deny,",
            "         MAX(CASE WHEN allow = 1 THEN 1 ELSE 0 END) AS any_allow,",
            "         json_group_array(CASE WHEN allow = 0 THEN source_plugin || ': ' || reason END) AS deny_reasons,",
            "         json_group_array(CASE WHEN allow = 1 THEN source_plugin || ': ' || reason END) AS allow_reasons",
            f"  FROM all_rules WHERE {where}",
        ]
        if group_by:
            parts.append(f"  GROUP BY {group_by}")
        return parts

    query_parts.extend(
        ["child_agg AS ("]
        + _agg(
            "parent, child,",
            "parent IS NOT NULL AND child IS NOT NULL",
            "parent, child",
        )
        + ["),", "parent_agg AS ("]
        + _agg("parent,", "parent IS NOT NULL AND child IS NULL", "parent")
        + ["),", "global_agg AS ("]
        + _agg("", "parent IS NULL AND child IS NULL", None)
        + ["),"]
    )

    # Add anonymous decision logic if needed
    if include_is_private:

        def _anon_agg(select_key, where, group_by):
            parts = [
                f"  SELECT {select_key}",
                "         MAX(CASE WHEN allow = 0 THEN 1 ELSE 0 END) AS any_deny,",
                "         MAX(CASE WHEN allow = 1 THEN 1 ELSE 0 END) AS any_allow",
                f"  FROM anon_rules WHERE {where}",
            ]
            if group_by:
                parts.append(f"  GROUP BY {group_by}")
            return parts

        query_parts.extend(
            ["anon_child_agg AS ("]
            + _anon_agg(
                "parent, child,",
                "parent IS NOT NULL AND child IS NOT NULL",
                "parent, child",
            )
            + ["),", "anon_parent_agg AS ("]
            + _anon_agg("parent,", "parent IS NOT NULL AND child IS NULL", "parent")
            + ["),", "anon_global_agg AS ("]
            + _anon_agg("", "parent IS NULL AND child IS NULL", None)
            + ["),"]
        )

    # Final decisions
    query_parts.extend(
        [
            "decisions AS (",
            "  SELECT",
            "    b.parent, b.child,",
            "    -- Cascading permission logic: child -> parent -> global, DENY beats ALLOW at each level",
            "    -- Priority order:",
            "    --   1. Child-level deny  2. Child-level allow",
            "    --   3. Parent-level deny 4. Parent-level allow",
            "    --   5. Global-level deny 6. Global-level allow",
            "    --   7. Default deny (no rules match)",
            "    CASE",
            "      WHEN ca.any_deny = 1 THEN 0",
            "      WHEN ca.any_allow = 1 THEN 1",
            "      WHEN pa.any_deny = 1 THEN 0",
            "      WHEN pa.any_allow = 1 THEN 1",
            "      WHEN ga.any_deny = 1 THEN 0",
            "      WHEN ga.any_allow = 1 THEN 1",
            "      ELSE 0",
            "    END AS is_allowed,",
            "    CASE",
            "      WHEN ca.any_deny = 1 THEN ca.deny_reasons",
            "      WHEN ca.any_allow = 1 THEN ca.allow_reasons",
            "      WHEN pa.any_deny = 1 THEN pa.deny_reasons",
            "      WHEN pa.any_allow = 1 THEN pa.allow_reasons",
            "      WHEN ga.any_deny = 1 THEN ga.deny_reasons",
            "      WHEN ga.any_allow = 1 THEN ga.allow_reasons",
            "      ELSE '[]'",
            "    END AS reason",
        ]
    )

    if include_is_private:
        query_parts.append(
            "    , CASE WHEN ("
            "CASE"
            " WHEN aca.any_deny = 1 THEN 0"
            " WHEN aca.any_allow = 1 THEN 1"
            " WHEN apa.any_deny = 1 THEN 0"
            " WHEN apa.any_allow = 1 THEN 1"
            " WHEN aga.any_deny = 1 THEN 0"
            " WHEN aga.any_allow = 1 THEN 1"
            " ELSE 0 END"
            ") = 0 THEN 1 ELSE 0 END AS is_private"
        )

    query_parts.extend(
        [
            "  FROM base b",
            "  LEFT JOIN child_agg ca ON ca.parent = b.parent AND ca.child = b.child",
            "  LEFT JOIN parent_agg pa ON pa.parent = b.parent",
            "  CROSS JOIN global_agg ga",
        ]
    )

    if include_is_private:
        query_parts.extend(
            [
                "  LEFT JOIN anon_child_agg aca ON aca.parent = b.parent AND aca.child = b.child",
                "  LEFT JOIN anon_parent_agg apa ON apa.parent = b.parent",
                "  CROSS JOIN anon_global_agg aga",
            ]
        )

    query_parts.append(")")

    # Add restriction list CTE if there are restrictions
    if restriction_sqls:
        # Wrap each restriction_sql in a subquery to avoid operator precedence issues
        # with UNION ALL inside the restriction SQL statements
        restriction_intersect = "\nINTERSECT\n".join(
            f"SELECT * FROM ({sql})" for sql in restriction_sqls
        )
        # Decompose by NULL-pattern so the final filter can use pure-equality
        # EXISTS lookups (satisfiable via automatic indexes) instead of a
        # correlated OR-scan over the whole list.
        query_parts.extend(
            [
                ",",
                "restriction_list AS (",
                f"  {restriction_intersect}",
                "),",
                "restriction_exact AS (",
                "  SELECT parent, child FROM restriction_list WHERE parent IS NOT NULL AND child IS NOT NULL",
                "),",
                "restriction_parent_any AS (",
                "  SELECT DISTINCT parent FROM restriction_list WHERE parent IS NOT NULL AND child IS NULL",
                "),",
                "restriction_child_any AS (",
                "  SELECT DISTINCT child FROM restriction_list WHERE parent IS NULL AND child IS NOT NULL",
                "),",
                "restriction_all AS (",
                "  SELECT 1 AS matched FROM restriction_list WHERE parent IS NULL AND child IS NULL LIMIT 1",
                ")",
            ]
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
  AND (
    EXISTS (SELECT 1 FROM restriction_all)
    OR EXISTS (SELECT 1 FROM restriction_parent_any r WHERE r.parent = decisions.parent)
    OR EXISTS (SELECT 1 FROM restriction_child_any r WHERE r.child = decisions.child)
    OR EXISTS (SELECT 1 FROM restriction_exact r WHERE r.parent = decisions.parent AND r.child = decisions.child)
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
    result_rows = []
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

        result_rows.append(f"({i}, ({verdict_sql}))")

    if result_rows:
        ctes.append(
            "results(action_idx, is_allowed) AS (VALUES\n"
            + ",\n".join(result_rows)
            + "\n)"
        )
        query = (
            "WITH\n" + ",\n".join(ctes) + "\nSELECT action_idx, is_allowed FROM results"
        )
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


async def explain_permission_for_resource(
    *,
    datasette: "Datasette",
    actor: dict | None,
    action: str,
    parent: str | None,
    child: str | None,
) -> dict:
    """Explain a permission decision for one action and resource.

    This is intended for Datasette's permission debugging tools. It uses the
    same ``permission_resources_sql`` hook results and the same resolution
    rules as :func:`check_permissions_for_actions`, but also returns the
    matching rules, actor restriction results and ``also_requires`` chain.

    The returned dictionary is part of Datasette's unstable debugging API.
    """

    action_obj = datasette.actions.get(action)
    if action_obj is None:
        raise ValueError(f"Unknown action: {action}")

    explanation = await _explain_single_action(
        datasette=datasette,
        actor=actor,
        action=action,
        parent=parent,
        child=child,
    )

    required_actions = []
    if action_obj.also_requires:
        required = await explain_permission_for_resource(
            datasette=datasette,
            actor=actor,
            action=action_obj.also_requires,
            parent=parent,
            child=child,
        )
        required_actions.append(required)

    explanation["required_actions"] = required_actions
    explanation["allowed"] = bool(
        explanation["rule_allowed"]
        and explanation["restriction_allowed"]
        and all(required["allowed"] for required in required_actions)
    )
    explanation["summary"] = _permission_explanation_summary(explanation)
    return explanation


async def _explain_single_action(
    *,
    datasette: "Datasette",
    actor: dict | None,
    action: str,
    parent: str | None,
    child: str | None,
) -> dict:
    """Return matching rules and restrictions for a single action."""
    from datasette.utils.permissions import SKIP_PERMISSION_CHECKS

    permission_sqls = await gather_permission_sql_from_hooks(
        datasette=datasette,
        actor=actor,
        action=action,
    )

    if permission_sqls is SKIP_PERMISSION_CHECKS:
        return {
            "action": action,
            "rule_allowed": True,
            "restriction_allowed": True,
            "winning_scope": "global",
            "matched_rules": [
                {
                    "scope": "global",
                    "effect": "allow",
                    "source": "skip_permission_checks",
                    "reason": "Permission checks were explicitly skipped",
                    "decisive": True,
                    "ignored_because": None,
                }
            ],
            "restrictions": [],
        }

    db = datasette.get_internal_database()
    matched_rules = []
    restrictions = []

    for permission_sql in permission_sqls:
        params = dict(permission_sql.params or {})
        parent_param = _unused_parameter_name(params, "_explain_parent")
        params[parent_param] = parent
        child_param = _unused_parameter_name(params, "_explain_child")
        params[child_param] = child

        if permission_sql.sql:
            rows = await db.execute(
                f"""
                SELECT parent, child, allow, reason
                FROM ({permission_sql.sql}) AS permission_rules
                WHERE (parent IS NULL OR parent = :{parent_param})
                  AND (child IS NULL OR child = :{child_param})
                """,
                params,
            )
            for row in rows:
                specificity = (
                    2
                    if row["child"] is not None
                    else 1 if row["parent"] is not None else 0
                )
                matched_rules.append(
                    {
                        "scope": ("resource", "parent", "global")[2 - specificity],
                        "effect": "allow" if row["allow"] else "deny",
                        "source": permission_sql.source,
                        "reason": row["reason"],
                        "_specificity": specificity,
                    }
                )

        if permission_sql.restriction_sql:
            restriction_row = (
                await db.execute(
                    f"""
                    SELECT EXISTS(
                        SELECT 1 FROM ({permission_sql.restriction_sql}) AS restriction_rules
                        WHERE (parent IS NULL OR parent = :{parent_param})
                          AND (child IS NULL OR child = :{child_param})
                    ) AS resource_is_in_allowlist
                    """,
                    params,
                )
            ).first()
            restriction_allowed = bool(restriction_row[0])
            restrictions.append(
                {
                    "source": permission_sql.source,
                    "allowed": restriction_allowed,
                    "reason": params.get("deny")
                    or (
                        "Resource is included in this restriction allowlist"
                        if restriction_allowed
                        else "Resource is not included in this restriction allowlist"
                    ),
                }
            )

    matched_rules.sort(
        key=lambda rule: (
            -rule["_specificity"],
            0 if rule["effect"] == "deny" else 1,
            rule["source"] or "",
            rule["reason"] or "",
        )
    )

    if matched_rules:
        winning_specificity = matched_rules[0]["_specificity"]
        winning_rules = [
            rule
            for rule in matched_rules
            if rule["_specificity"] == winning_specificity
        ]
        rule_allowed = not any(rule["effect"] == "deny" for rule in winning_rules)
        winning_scope = winning_rules[0]["scope"]
    else:
        winning_specificity = None
        rule_allowed = False
        winning_scope = None

    for rule in matched_rules:
        specificity = rule.pop("_specificity")
        if specificity != winning_specificity:
            rule["decisive"] = False
            rule["ignored_because"] = "A more specific rule matched"
        elif not rule_allowed and rule["effect"] == "allow":
            rule["decisive"] = False
            rule["ignored_because"] = "A deny rule matched at the same scope"
        else:
            rule["decisive"] = True
            rule["ignored_because"] = None

    return {
        "action": action,
        "rule_allowed": rule_allowed,
        "restriction_allowed": all(
            restriction["allowed"] for restriction in restrictions
        ),
        "winning_scope": winning_scope,
        "matched_rules": matched_rules,
        "restrictions": restrictions,
    }


def _unused_parameter_name(params: dict, preferred: str) -> str:
    """Return a SQL parameter name that is not already in ``params``."""
    candidate = preferred
    suffix = 2
    while candidate in params:
        candidate = f"{preferred}_{suffix}"
        suffix += 1
    return candidate


def _permission_explanation_summary(explanation: dict) -> str:
    denied_requirement = next(
        (
            required
            for required in explanation["required_actions"]
            if not required["allowed"]
        ),
        None,
    )
    if denied_requirement:
        return (
            f"Denied because {explanation['action']} also requires "
            f"{denied_requirement['action']}, which was denied."
        )
    if not explanation["matched_rules"]:
        return "Denied because no permission rule matched this actor and resource."
    if not explanation["rule_allowed"]:
        return (
            f"Denied by a {explanation['winning_scope']}-level rule. "
            "Deny rules take precedence over allow rules at the same scope."
        )
    if not explanation["restriction_allowed"]:
        return (
            "Denied because the resource is not included in the actor's restrictions."
        )
    return f"Allowed by the matching {explanation['winning_scope']}-level rule."
