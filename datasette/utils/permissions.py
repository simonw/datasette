# perm_utils.py
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Sequence, Tuple
import sqlite3

from datasette.permissions import PermissionSQL
from datasette.plugins import pm
from datasette.utils import await_me_maybe


# Sentinel object to indicate permission checks should be skipped
SKIP_PERMISSION_CHECKS = object()


async def gather_permission_sql_from_hooks(
    *, datasette, actor: dict | None, action: str
) -> List[PermissionSQL] | object:
    """Collect PermissionSQL objects from the permission_resources_sql hook.

    Ensures that each returned PermissionSQL has a populated ``source``.

    Returns SKIP_PERMISSION_CHECKS sentinel if skip_permission_checks context variable
    is set, signaling that all permission checks should be bypassed.
    """
    from datasette.permissions import _skip_permission_checks

    # Check if we should skip permission checks BEFORE calling hooks
    # This avoids creating unawaited coroutines
    if _skip_permission_checks.get():
        return SKIP_PERMISSION_CHECKS

    hook_caller = pm.hook.permission_resources_sql
    hookimpls = hook_caller.get_hookimpls()
    hook_results = list(hook_caller(datasette=datasette, actor=actor, action=action))

    collected: List[PermissionSQL] = []
    actor_json = json.dumps(actor) if actor is not None else None
    actor_id = actor.get("id") if isinstance(actor, dict) else None

    for index, result in enumerate(hook_results):
        hookimpl = hookimpls[index]
        resolved = await await_me_maybe(result)
        default_source = _plugin_name_from_hookimpl(hookimpl)
        for permission_sql in _iter_permission_sql_from_result(resolved, action=action):
            if not permission_sql.source:
                permission_sql.source = default_source
            params = permission_sql.params or {}
            params.setdefault("action", action)
            params.setdefault("actor", actor_json)
            params.setdefault("actor_id", actor_id)
            collected.append(permission_sql)

    return collected


def _plugin_name_from_hookimpl(hookimpl) -> str:
    if getattr(hookimpl, "plugin_name", None):
        return hookimpl.plugin_name
    plugin = getattr(hookimpl, "plugin", None)
    if hasattr(plugin, "__name__"):
        return plugin.__name__
    return repr(plugin)


def _iter_permission_sql_from_result(
    result: Any, *, action: str
) -> Iterable[PermissionSQL]:
    if result is None:
        return []
    if isinstance(result, PermissionSQL):
        return [result]
    if isinstance(result, (list, tuple)):
        collected: List[PermissionSQL] = []
        for item in result:
            collected.extend(_iter_permission_sql_from_result(item, action=action))
        return collected
    if callable(result):
        permission_sql = result(action)  # type: ignore[call-arg]
        return _iter_permission_sql_from_result(permission_sql, action=action)
    raise TypeError(
        "Plugin providers must return PermissionSQL instances, sequences, or callables"
    )


# -----------------------------
# Plugin interface & utilities
# -----------------------------


def build_rules_union(
    actor: dict | None, plugins: Sequence[PermissionSQL]
) -> Tuple[str, Dict[str, Any]]:
    """
    Compose plugin SQL into a UNION ALL.

    Returns:
      union_sql: a SELECT with columns (parent, child, allow, reason, source_plugin)
      params:    dict of bound parameters including :actor (JSON), :actor_id, and plugin params

    Note: Plugins are responsible for ensuring their parameter names don't conflict.
    The system reserves these parameter names: :actor, :actor_id, :action, :filter_parent
    Plugin parameters should be prefixed with a unique identifier (e.g., source name).
    """
    parts: List[str] = []
    actor_json = json.dumps(actor) if actor else None
    actor_id = actor.get("id") if actor else None
    params: Dict[str, Any] = {"actor": actor_json, "actor_id": actor_id}

    for p in plugins:
        # No namespacing - just use plugin params as-is
        params.update(p.params or {})

        # Skip plugins that only provide restriction_sql (no permission rules)
        if p.sql is None:
            continue

        parts.append(
            f"""
            SELECT parent, child, allow, reason, '{p.source}' AS source_plugin FROM (
                {p.sql}
            )
            """.strip()
        )

    if not parts:
        # Empty UNION that returns no rows
        union_sql = "SELECT NULL parent, NULL child, NULL allow, NULL reason, 'none' source_plugin WHERE 0"
    else:
        union_sql = "\nUNION ALL\n".join(parts)

    return union_sql, params


# -----------------------------------------------
# Core resolvers (no temp tables, no custom UDFs)
# -----------------------------------------------


async def resolve_permissions_from_catalog(
    db,
    actor: dict | None,
    plugins: Sequence[Any],
    action: str,
    candidate_sql: str,
    candidate_params: Dict[str, Any] | None = None,
    *,
    implicit_deny: bool = True,
) -> List[Dict[str, Any]]:
    """
    Resolve permissions by embedding the provided *candidate_sql* in a CTE.

    Expectations:
      - candidate_sql SELECTs: parent TEXT, child TEXT
        (Use child=NULL for parent-scoped actions like "execute-sql".)
      - *db* exposes: rows = await db.execute(sql, params)
        where rows is an iterable of sqlite3.Row
      - plugins: hook results handled by await_me_maybe - can be sync/async,
        single PermissionSQL, list, or callable returning PermissionSQL
      - actor is the actor dict (or None), made available as :actor (JSON), :actor_id, and :action

    Decision policy:
      1) Specificity first: child (depth=2) > parent (depth=1) > root (depth=0)
      2) Within the same depth: deny (0) beats allow (1)
      3) If no matching rule:
         - implicit_deny=True  -> treat as allow=0, reason='implicit deny'
         - implicit_deny=False -> allow=None, reason=None

    Returns: list of dict rows
      - parent, child, allow, reason, source_plugin, depth
      - resource (rendered "/parent/child" or "/parent" or "/")
    """
    resolved_plugins: List[PermissionSQL] = []
    restriction_sqls: List[str] = []

    for plugin in plugins:
        if callable(plugin) and not isinstance(plugin, PermissionSQL):
            resolved = plugin(action)  # type: ignore[arg-type]
        else:
            resolved = plugin  # type: ignore[assignment]
        if not isinstance(resolved, PermissionSQL):
            raise TypeError("Plugin providers must return PermissionSQL instances")
        resolved_plugins.append(resolved)

        # Collect restriction SQL filters
        if resolved.restriction_sql:
            restriction_sqls.append(resolved.restriction_sql)

    union_sql, rule_params = build_rules_union(actor, resolved_plugins)
    all_params = {
        **(candidate_params or {}),
        **rule_params,
        "action": action,
    }

    sql = f"""
    WITH
    cands AS (
        {candidate_sql}
    ),
    rules AS (
        {union_sql}
    ),
    matched AS (
        SELECT
            c.parent, c.child,
            r.allow, r.reason, r.source_plugin,
            CASE
              WHEN r.child  IS NOT NULL THEN 2  -- child-level (most specific)
              WHEN r.parent IS NOT NULL THEN 1  -- parent-level
              ELSE 0                            -- root/global
            END AS depth
        FROM cands c
        JOIN rules r
          ON (r.parent IS NULL OR r.parent = c.parent)
         AND (r.child  IS NULL OR r.child  = c.child)
    ),
    ranked AS (
        SELECT *,
               ROW_NUMBER() OVER (
                 PARTITION BY parent, child
                 ORDER BY
                   depth DESC,                          -- specificity first
                   CASE WHEN allow=0 THEN 0 ELSE 1 END, -- then deny over allow at same depth
                   source_plugin                        -- stable tie-break
               ) AS rn
        FROM matched
    ),
    winner AS (
        SELECT parent, child,
               allow, reason, source_plugin, depth
        FROM ranked WHERE rn = 1
    )
    SELECT
      c.parent, c.child,
      COALESCE(w.allow, CASE WHEN :implicit_deny THEN 0 ELSE NULL END) AS allow,
      COALESCE(w.reason, CASE WHEN :implicit_deny THEN 'implicit deny' ELSE NULL END) AS reason,
      w.source_plugin,
      COALESCE(w.depth, -1) AS depth,
      :action AS action,
      CASE
        WHEN c.parent IS NULL THEN '/'
        WHEN c.child  IS NULL THEN '/' || c.parent
        ELSE '/' || c.parent || '/' || c.child
      END AS resource
    FROM cands c
    LEFT JOIN winner w
      ON ((w.parent = c.parent) OR (w.parent IS NULL AND c.parent IS NULL))
     AND ((w.child  = c.child ) OR (w.child  IS NULL AND c.child  IS NULL))
    ORDER BY c.parent, c.child
    """

    # If there are restriction filters, wrap the query with INTERSECT
    # This ensures only resources in the restriction allowlist are returned
    if restriction_sqls:
        # Start with the main query, but select only parent/child for the INTERSECT
        main_query_for_intersect = f"""
        WITH
        cands AS (
            {candidate_sql}
        ),
        rules AS (
            {union_sql}
        ),
        matched AS (
            SELECT
                c.parent, c.child,
                r.allow, r.reason, r.source_plugin,
                CASE
                  WHEN r.child  IS NOT NULL THEN 2  -- child-level (most specific)
                  WHEN r.parent IS NOT NULL THEN 1  -- parent-level
                  ELSE 0                            -- root/global
                END AS depth
            FROM cands c
            JOIN rules r
              ON (r.parent IS NULL OR r.parent = c.parent)
             AND (r.child  IS NULL OR r.child  = c.child)
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                     PARTITION BY parent, child
                     ORDER BY
                       depth DESC,                          -- specificity first
                       CASE WHEN allow=0 THEN 0 ELSE 1 END, -- then deny over allow at same depth
                       source_plugin                        -- stable tie-break
                   ) AS rn
            FROM matched
        ),
        winner AS (
            SELECT parent, child,
                   allow, reason, source_plugin, depth
            FROM ranked WHERE rn = 1
        ),
        permitted_resources AS (
            SELECT c.parent, c.child
            FROM cands c
            LEFT JOIN winner w
              ON ((w.parent = c.parent) OR (w.parent IS NULL AND c.parent IS NULL))
             AND ((w.child  = c.child ) OR (w.child  IS NULL AND c.child  IS NULL))
            WHERE COALESCE(w.allow, CASE WHEN :implicit_deny THEN 0 ELSE NULL END) = 1
        )
        SELECT parent, child FROM permitted_resources
        """

        # Build restriction list with INTERSECT (all must match)
        # Then filter to resources that match hierarchically
        # Wrap each restriction_sql in a subquery to avoid operator precedence issues
        # with UNION ALL inside the restriction SQL statements
        restriction_intersect = "\nINTERSECT\n".join(
            f"SELECT * FROM ({sql})" for sql in restriction_sqls
        )

        # Combine: resources allowed by permissions AND in restriction allowlist
        # Database-level restrictions (parent, NULL) should match all children (parent, *)
        filtered_resources = f"""
        WITH restriction_list AS (
            {restriction_intersect}
        ),
        permitted AS (
            {main_query_for_intersect}
        ),
        filtered AS (
            SELECT p.parent, p.child
            FROM permitted p
            WHERE EXISTS (
                SELECT 1 FROM restriction_list r
                WHERE (r.parent = p.parent OR r.parent IS NULL)
                  AND (r.child = p.child OR r.child IS NULL)
            )
        )
        """

        # Now join back to get full results for only the filtered resources
        sql = f"""
        {filtered_resources}
        , cands AS (
            {candidate_sql}
        ),
        rules AS (
            {union_sql}
        ),
        matched AS (
            SELECT
                c.parent, c.child,
                r.allow, r.reason, r.source_plugin,
                CASE
                  WHEN r.child  IS NOT NULL THEN 2  -- child-level (most specific)
                  WHEN r.parent IS NOT NULL THEN 1  -- parent-level
                  ELSE 0                            -- root/global
                END AS depth
            FROM cands c
            JOIN rules r
              ON (r.parent IS NULL OR r.parent = c.parent)
             AND (r.child  IS NULL OR r.child  = c.child)
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                     PARTITION BY parent, child
                     ORDER BY
                       depth DESC,                          -- specificity first
                       CASE WHEN allow=0 THEN 0 ELSE 1 END, -- then deny over allow at same depth
                       source_plugin                        -- stable tie-break
                   ) AS rn
            FROM matched
        ),
        winner AS (
            SELECT parent, child,
                   allow, reason, source_plugin, depth
            FROM ranked WHERE rn = 1
        )
        SELECT
          c.parent, c.child,
          COALESCE(w.allow, CASE WHEN :implicit_deny THEN 0 ELSE NULL END) AS allow,
          COALESCE(w.reason, CASE WHEN :implicit_deny THEN 'implicit deny' ELSE NULL END) AS reason,
          w.source_plugin,
          COALESCE(w.depth, -1) AS depth,
          :action AS action,
          CASE
            WHEN c.parent IS NULL THEN '/'
            WHEN c.child  IS NULL THEN '/' || c.parent
            ELSE '/' || c.parent || '/' || c.child
          END AS resource
        FROM filtered c
        LEFT JOIN winner w
          ON ((w.parent = c.parent) OR (w.parent IS NULL AND c.parent IS NULL))
         AND ((w.child  = c.child ) OR (w.child  IS NULL AND c.child  IS NULL))
        ORDER BY c.parent, c.child
        """

    rows_iter: Iterable[sqlite3.Row] = await db.execute(
        sql,
        {**all_params, "implicit_deny": 1 if implicit_deny else 0},
    )
    return [dict(r) for r in rows_iter]


async def resolve_permissions_with_candidates(
    db,
    actor: dict | None,
    plugins: Sequence[Any],
    candidates: List[Tuple[str, str | None]],
    action: str,
    *,
    implicit_deny: bool = True,
) -> List[Dict[str, Any]]:
    """
    Resolve permissions without any external candidate table by embedding
    the candidates as a UNION of parameterized SELECTs in a CTE.

    candidates: list of (parent, child) where child can be None for parent-scoped actions.
    actor: actor dict (or None), made available as :actor (JSON), :actor_id, and :action
    """
    # Build a small CTE for candidates.
    cand_rows_sql: List[str] = []
    cand_params: Dict[str, Any] = {}
    for i, (parent, child) in enumerate(candidates):
        pkey = f"cand_p_{i}"
        ckey = f"cand_c_{i}"
        cand_params[pkey] = parent
        cand_params[ckey] = child
        cand_rows_sql.append(f"SELECT :{pkey} AS parent, :{ckey} AS child")
    candidate_sql = (
        "\nUNION ALL\n".join(cand_rows_sql)
        if cand_rows_sql
        else "SELECT NULL AS parent, NULL AS child WHERE 0"
    )

    return await resolve_permissions_from_catalog(
        db,
        actor,
        plugins,
        action,
        candidate_sql=candidate_sql,
        candidate_params=cand_params,
        implicit_deny=implicit_deny,
    )
