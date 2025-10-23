# perm_utils.py
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union
import sqlite3

from datasette.permissions import PermissionSQL


# -----------------------------
# Plugin interface & utilities
# -----------------------------


def _namespace_params(i: int, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Rewrite parameter placeholders to distinct names per plugin block.
    Returns (rewritten_sql, namespaced_params).
    """

    replacements = {key: f"{key}_{i}" for key in params.keys()}

    def rewrite(s: str) -> str:
        for key in sorted(replacements.keys(), key=len, reverse=True):
            s = s.replace(f":{key}", f":{replacements[key]}")
        return s

    namespaced: Dict[str, Any] = {}
    for key, value in params.items():
        namespaced[replacements[key]] = value
    return rewrite, namespaced


PluginProvider = Callable[[str], PermissionSQL]
PluginOrFactory = Union[PermissionSQL, PluginProvider]


def build_rules_union(
    actor: str, plugins: Sequence[PermissionSQL]
) -> Tuple[str, Dict[str, Any]]:
    """
    Compose plugin SQL into a UNION ALL with namespaced parameters.

    Returns:
      union_sql: a SELECT with columns (parent, child, allow, reason, source_plugin)
      params:    dict of bound parameters including :actor and namespaced plugin params
    """
    parts: List[str] = []
    params: Dict[str, Any] = {"actor": actor}

    for i, p in enumerate(plugins):
        rewrite, ns_params = _namespace_params(i, p.params)
        sql_block = rewrite(p.sql)
        params.update(ns_params)

        parts.append(
            f"""
            SELECT parent, child, allow, reason, '{p.source}' AS source_plugin FROM (
                {sql_block}
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
    actor: str,
    plugins: Sequence[PluginOrFactory],
    action: str,
    candidate_sql: str,
    candidate_params: Optional[Dict[str, Any]] = None,
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
      - plugins are either PermissionSQL objects or callables accepting (action: str)
        and returning PermissionSQL instances selecting (parent, child, allow, reason)

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
    for plugin in plugins:
        if callable(plugin) and not isinstance(plugin, PermissionSQL):
            resolved = plugin(action)  # type: ignore[arg-type]
        else:
            resolved = plugin  # type: ignore[assignment]
        if not isinstance(resolved, PermissionSQL):
            raise TypeError("Plugin providers must return PermissionSQL instances")
        resolved_plugins.append(resolved)

    union_sql, rule_params = build_rules_union(actor, resolved_plugins)
    all_params = {
        **(candidate_params or {}),
        **rule_params,
        "actor": actor,
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
                   CASE WHEN allow=0 THEN 0 ELSE 1 END, -- deny over allow at same depth
                   source_plugin                         -- stable tie-break
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

    rows_iter: Iterable[sqlite3.Row] = await db.execute(
        sql,
        {**all_params, "implicit_deny": 1 if implicit_deny else 0},
    )
    return [dict(r) for r in rows_iter]


async def resolve_permissions_with_candidates(
    db,
    actor: str,
    plugins: Sequence[PluginOrFactory],
    candidates: List[Tuple[str, Optional[str]]],
    action: str,
    *,
    implicit_deny: bool = True,
) -> List[Dict[str, Any]]:
    """
    Resolve permissions without any external candidate table by embedding
    the candidates as a UNION of parameterized SELECTs in a CTE.

    candidates: list of (parent, child) where child can be None for parent-scoped actions.
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
