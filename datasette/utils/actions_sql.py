"""
SQL query builder for hierarchical permission checking.

This module implements a cascading permission system based on the pattern
from the sqlite-permissions-poc. It builds SQL queries that:

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

from typing import Optional
from datasette.plugins import pm
from datasette.utils import await_me_maybe
from datasette.permissions import PermissionSQL


async def build_allowed_resources_sql(
    datasette,
    actor: dict | None,
    action: str,
) -> tuple[str, dict]:
    """
    Build a SQL query that returns all resources the actor can access for this action.

    Args:
        datasette: The Datasette instance
        actor: The actor dict (or None for unauthenticated)
        action: The action name (e.g., "view-table", "view-database")

    Returns:
        A tuple of (sql_query, params_dict)

    The returned SQL query will have three columns:
        - parent: The parent resource identifier (or NULL)
        - child: The child resource identifier (or NULL)
        - reason: The reason from the rule that granted access

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

    # Get base resources SQL from the resource class
    base_resources_sql = action_obj.resource_class.resources_sql()

    # Get all permission rule fragments from plugins via the hook
    rule_results = pm.hook.permission_resources_sql(
        datasette=datasette,
        actor=actor,
        action=action,
    )

    # Combine rule fragments and collect parameters
    all_params = {}
    rule_sqls = []

    for result in rule_results:
        result = await await_me_maybe(result)
        if result is None:
            continue
        if isinstance(result, list):
            for plugin_sql in result:
                if isinstance(plugin_sql, PermissionSQL):
                    rule_sqls.append(plugin_sql.sql)
                    all_params.update(plugin_sql.params)
        elif isinstance(result, PermissionSQL):
            rule_sqls.append(result.sql)
            all_params.update(result.params)

    # If no rules, return empty result (deny all)
    if not rule_sqls:
        return "SELECT NULL AS parent, NULL AS child WHERE 0", {}

    # Build the cascading permission query
    rules_union = " UNION ALL ".join(rule_sqls)

    query = f"""
WITH
base AS (
  {base_resources_sql}
),
all_rules AS (
  {rules_union}
),
child_lvl AS (
  SELECT b.parent, b.child,
         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,
         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow,
         MAX(CASE WHEN ar.allow = 0 THEN ar.reason ELSE NULL END) AS deny_reason,
         MAX(CASE WHEN ar.allow = 1 THEN ar.reason ELSE NULL END) AS allow_reason
  FROM base b
  LEFT JOIN all_rules ar ON ar.parent = b.parent AND ar.child = b.child
  GROUP BY b.parent, b.child
),
parent_lvl AS (
  SELECT b.parent, b.child,
         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,
         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow,
         MAX(CASE WHEN ar.allow = 0 THEN ar.reason ELSE NULL END) AS deny_reason,
         MAX(CASE WHEN ar.allow = 1 THEN ar.reason ELSE NULL END) AS allow_reason
  FROM base b
  LEFT JOIN all_rules ar ON ar.parent = b.parent AND ar.child IS NULL
  GROUP BY b.parent, b.child
),
global_lvl AS (
  SELECT b.parent, b.child,
         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,
         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow,
         MAX(CASE WHEN ar.allow = 0 THEN ar.reason ELSE NULL END) AS deny_reason,
         MAX(CASE WHEN ar.allow = 1 THEN ar.reason ELSE NULL END) AS allow_reason
  FROM base b
  LEFT JOIN all_rules ar ON ar.parent IS NULL AND ar.child IS NULL
  GROUP BY b.parent, b.child
),
decisions AS (
  SELECT
    b.parent, b.child,
    CASE
      WHEN cl.any_deny = 1 THEN 0
      WHEN cl.any_allow = 1 THEN 1
      WHEN pl.any_deny = 1 THEN 0
      WHEN pl.any_allow = 1 THEN 1
      WHEN gl.any_deny = 1 THEN 0
      WHEN gl.any_allow = 1 THEN 1
      ELSE 0
    END AS is_allowed,
    CASE
      WHEN cl.any_deny = 1 THEN cl.deny_reason
      WHEN cl.any_allow = 1 THEN cl.allow_reason
      WHEN pl.any_deny = 1 THEN pl.deny_reason
      WHEN pl.any_allow = 1 THEN pl.allow_reason
      WHEN gl.any_deny = 1 THEN gl.deny_reason
      WHEN gl.any_allow = 1 THEN gl.allow_reason
      ELSE 'default deny'
    END AS reason
  FROM base b
  JOIN child_lvl cl USING (parent, child)
  JOIN parent_lvl pl USING (parent, child)
  JOIN global_lvl gl USING (parent, child)
)
SELECT parent, child, reason
FROM decisions
WHERE is_allowed = 1
ORDER BY parent, child
"""
    return query.strip(), all_params


async def check_permission_for_resource(
    datasette,
    actor: dict | None,
    action: str,
    parent: Optional[str],
    child: Optional[str],
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
    # Get the Action object
    action_obj = datasette.actions.get(action)
    if not action_obj:
        raise ValueError(f"Unknown action: {action}")

    # Get all permission rule fragments from plugins via the hook
    rule_results = pm.hook.permission_resources_sql(
        datasette=datasette,
        actor=actor,
        action=action,
    )

    # Combine rule fragments and collect parameters
    all_params = {}
    rule_sqls = []

    for result in rule_results:
        result = await await_me_maybe(result)
        if result is None:
            continue
        if isinstance(result, list):
            for plugin_sql in result:
                if isinstance(plugin_sql, PermissionSQL):
                    rule_sqls.append(plugin_sql.sql)
                    all_params.update(plugin_sql.params)
        elif isinstance(result, PermissionSQL):
            rule_sqls.append(result.sql)
            all_params.update(result.params)

    # If no rules, default deny
    if not rule_sqls:
        return False

    # Build a simplified query that just checks for this one resource
    rules_union = " UNION ALL ".join(rule_sqls)

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
