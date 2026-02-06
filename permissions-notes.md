# SQL Permissions System - Deep Code Review Notes

## Overview

The SQL permissions system was introduced in Datasette 1.0a20 and subsequently refined through 1.0a24. It replaces the older plugin hook-based `permission_allowed` system with a SQL-driven approach where all permission decisions are resolved by executing SQL queries against the internal SQLite database.

Key commits:
- `95a1fef` (1.0a20): Initial introduction of `permissions.py`, `utils/permissions.py`, `default_permissions.py`
- `23a640d`: `--default-deny` option
- `d814e81`: `skip_permission_checks` context variable, `actions_sql.py`
- `0a92452`: Split `default_permissions.py` into a package with 7 modules
- `66d2a03`: Ruff lint fixes

## Architecture Summary

### Permission Check Flow

```
Request → Authentication → Action Check
                              ↓
                    permission_resources_sql hook
                              ↓
                    Multiple PermissionSQL objects collected
                              ↓
                    UNION ALL into rules CTE
                              ↓
                    Cascading evaluation:
                      child(2) → parent(1) → global(0)
                      DENY beats ALLOW at same level
                              ↓
                    restriction_sql INTERSECT filtering
                              ↓
                    Boolean result (or resource list)
```

### Two Code Paths

1. **Single resource check** (`check_permission_for_resource` in `actions_sql.py:494-587`): Uses `ROW_NUMBER() OVER (PARTITION BY ...)` with ORDER BY depth to pick a winner. Used by `datasette.allowed()`.

2. **All resources check** (`_build_single_action_sql` in `actions_sql.py:130-425`): Uses separate `child_lvl`, `parent_lvl`, `global_lvl` CTEs with `MAX(CASE ...)` aggregates, then a cascading CASE statement. Used by `datasette.allowed_resources()`.

These two code paths implement the **same cascading logic** but with completely different SQL structures.

### Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `datasette/permissions.py` | 210 | Core abstractions: `Resource`, `Action`, `PermissionSQL`, `SkipPermissions` |
| `datasette/resources.py` | 91 | `DatabaseResource`, `TableResource`, `QueryResource` |
| `datasette/utils/actions_sql.py` | 587 | SQL builders for `allowed_resources()` and `allowed()` |
| `datasette/utils/permissions.py` | 439 | Hook gathering, `resolve_permissions_from_catalog()` (3rd implementation) |
| `datasette/default_permissions/__init__.py` | 59 | Package init, re-exports, CSRF skip, canned_queries |
| `datasette/default_permissions/config.py` | 442 | `ConfigPermissionProcessor` - datasette.yaml rules |
| `datasette/default_permissions/defaults.py` | 70 | `DEFAULT_ALLOW_ACTIONS`, `default_allow_sql` |
| `datasette/default_permissions/restrictions.py` | 195 | Actor `_r` allowlist handling |
| `datasette/default_permissions/helpers.py` | 85 | `PermissionRowCollector`, action name variants |
| `datasette/default_permissions/root.py` | 29 | Root user global allow |
| `datasette/default_permissions/tokens.py` | 95 | Signed API token auth |

---

## Findings

### Tests: All Pass

263 tests pass, 3 xpassed. Test files:
- `test_permissions.py` (largest, 1713 lines)
- `test_config_permission_rules.py` (163 lines)
- `test_utils_permissions.py` (612 lines)
- `test_permission_endpoints.py` (501 lines)
- `test_default_deny.py` (129 lines)
- `test_restriction_sql.py`
- `test_allowed_resources.py`
- `test_actions_sql.py`

---

## Issues Found

### ISSUE 1 (Design Concern): Root user blocked by `allow:` blocks that don't include "root"

**Severity: Medium (by design per #2509, but potentially surprising UX)**

When a table has an `allow:` block in config like:
```yaml
databases:
  mydb:
    tables:
      secrets:
        allow:
          id: admin
```

The root user (--root) is **denied access** to that table. This happens because:

1. `root_user_permissions_sql()` returns a global (NULL, NULL) ALLOW
2. `config_permissions_sql()` generates a child-level (mydb, secrets) DENY for actors not matching `{id: admin}` (root's id is "root", not "admin")
3. The cascading logic says child-level beats global-level

**Observed behavior:**
```
curl -b [root-cookies] /test_perms/secrets.json → 403 Forbidden
```

**Rules visible in /-/rules.json:**
```json
[
  {"parent": null, "child": null, "allow": 1, "reason": "root user"},
  {"parent": "test_perms", "child": "secrets", "allow": 0, "reason": "config deny allow..."}
]
```

**This is intentional per issue #2509**: `test_root_user_respects_settings_deny` in `test_permission_endpoints.py:355` explicitly asserts that config deny rules override root. The same logic applies to `allow: {id: admin}` - since root's id doesn't match, it becomes a deny.

**However, this is a UX concern**: An admin starting Datasette with `--root` may reasonably expect full access. With `allow: {id: admin}`, the workaround is `allow: {id: [admin, root]}`, but with `allow: false` there is no config-based workaround.

**Recommendation**: Document this clearly in `--root` documentation. Consider whether a future `--root-bypass-config` flag or equivalent would be useful for debugging scenarios.

---

### ISSUE 2 (Design): Three separate implementations of cascading logic

The cascading permission resolution (child > parent > global, deny beats allow) is implemented in three different places:

1. **`actions_sql.py:_build_single_action_sql()`** (lines 246-384): Uses separate CTEs (`child_lvl`, `parent_lvl`, `global_lvl`) each doing `LEFT JOIN` + `GROUP BY` with `MAX()` aggregates, then a CASE cascade in `decisions`.

2. **`actions_sql.py:check_permission_for_resource()`** (lines 555-587): Uses `ROW_NUMBER() OVER (PARTITION BY parent, child ORDER BY depth DESC, ...)` to pick a single winner.

3. **`permissions.py:resolve_permissions_from_catalog()`** (lines 141-397): Yet another implementation using `ROW_NUMBER()` like #2 but with different structure, including massive SQL duplication when restriction_sql is present (the entire query is repeated in the restriction case).

**Important note**: `resolve_permissions_from_catalog()` is **only used in tests** (`test_utils_permissions.py`), not in any production code path. This means it's a test-only implementation of the same logic, which could drift out of sync with the actual production implementations (#1 and #2). If the production SQL is changed, these tests might still pass on the old test-only implementation while production behavior changes.

The two production paths (#1 and #2) implement the same cascading logic but with different SQL patterns. This is fragile - a logic change must be applied in both places.

---

### ISSUE 3 (Code Quality): Massive SQL duplication in `resolve_permissions_from_catalog()`

In `utils/permissions.py:256-391`, when `restriction_sqls` is present, the **entire CTE chain** (cands, rules, matched, ranked, winner) is duplicated - once for the main query and once for the restriction filtering. This results in ~135 lines of nearly identical SQL being emitted twice.

The restriction-with-restrictions path generates SQL that embeds the full resolution query inside a `permitted_resources` CTE, then creates a `filtered` CTE, and then re-creates cands/rules/matched/ranked/winner *again* to get the full output columns. This could be simplified significantly.

---

### ISSUE 4 (Code Quality): Global `_reason_id` counter in `PermissionSQL`

`permissions.py:157` has a module-level `_reason_id` counter that increments forever:

```python
_reason_id = 1

class PermissionSQL:
    @classmethod
    def allow(cls, reason, _allow=True):
        global _reason_id
        i = _reason_id
        _reason_id += 1
        ...
```

This means:
- Every `PermissionSQL.allow()` or `.deny()` call increments a process-global counter
- In a long-running server, param keys grow: `:reason_1`, `:reason_2`, ..., `:reason_100000`
- Not thread-safe (though Python's GIL provides some protection)
- Makes SQL non-deterministic between requests (harder to cache or compare)
- The counter never resets

This isn't a memory leak per se (the SQL is transient), but it's an unusual pattern. A better approach would be to use a per-call counter or deterministic naming.

---

### ISSUE 5 (Security): `source_plugin` name injected into SQL without parameterization

In three places, the plugin name is interpolated directly into SQL:

```python
# actions_sql.py:185
f"SELECT parent, child, allow, reason, '{permission_sql.source}' AS source_plugin FROM ..."

# actions_sql.py:484
f"SELECT parent, child, allow, reason, '{permission_sql.source}' AS source_plugin FROM ..."

# permissions.py:121
f"SELECT parent, child, allow, reason, '{p.source}' AS source_plugin FROM ..."
```

The `source` field comes from `_plugin_name_from_hookimpl()` which extracts the Python module name. While unlikely to contain SQL injection payloads in practice, a malicious plugin with a single-quote in its name could inject SQL. This should use parameterized values.

---

### ISSUE 6 (Security): `QueryResource.resources_sql()` uses manual quote escaping

In `resources.py:82-88`:
```python
db_escaped = db_name.replace("'", "''")
query_escaped = query_name.replace("'", "''")
selects.append(f"SELECT '{db_escaped}' AS parent, '{query_escaped}' AS child")
```

This manually escapes single quotes by doubling them instead of using parameterized queries. While the double-quote escape is the correct SQLite approach, parameterized queries would be safer and more robust.

The limitation here is that `resources_sql()` returns a SQL string, not (SQL, params) - so the API would need to change to support parameterization.

---

### ISSUE 7 (Performance): `include_is_private` doubles the permission SQL

When `include_is_private=True` is used (which is the default for database and index page views), the entire permission resolution is run twice:
1. Once for the actual actor
2. Once for `actor=None` (anonymous)

This generates separate `anon_rules`, `anon_child_lvl`, `anon_parent_lvl`, `anon_global_lvl`, and `anon_decisions` CTEs - effectively doubling the size and cost of the query.

Looking at the trace output for an anonymous user viewing the database page, the view-table permission query with `include_is_private=True` was the slowest query at ~4.2ms. For authenticated users with many rules, this would be worse.

**Optimization opportunity**: When the actor IS anonymous (`actor=None`), the `is_private` computation is trivially 0 for all allowed resources since the actor and anonymous actor are the same. This case could be short-circuited.

---

### ISSUE 8 (Performance): Homepage counts ALL tables, not just visible ones

In the trace for the homepage, `table_counts` queries are issued for ALL tables:
```
select count(*) from [posts] limit 10001    -- visible to anon
select count(*) from [secrets] limit 10001  -- NOT visible to anon
select count(*) from [users] limit 10001    -- NOT visible to anon
```

The count results for `secrets` and `users` are computed but then discarded because those tables aren't in the allowed set. This is wasteful, especially with large tables. The count queries should only be issued for tables the user can actually see.

---

### ISSUE 9 (Design): `allow:` blocks generate DENYs, not restrictions

The current design converts `allow: {id: admin}` blocks into **deny** rules for non-matching actors and **allow** rules for matching actors. This means:

```yaml
tables:
  secrets:
    allow:
      id: admin
```

Generates two separate rules depending on the actor:
- For admin: `(test_perms, secrets, allow=1, "config allow...")`
- For everyone else: `(test_perms, secrets, allow=0, "config deny...")`

The deny rule is emitted at the child level, which means it **cannot be overridden by any global or parent-level allow**. This is the root cause of Issue 1.

A more nuanced approach might:
- Only emit allow rules from `allow:` blocks
- Use a separate "last-resort" deny mechanism that doesn't interfere with higher-priority allows
- Or use a "priority" system where root > config > defaults

---

### ISSUE 10 (Design): No explicit deny mechanism for specific actors

The system has `allow:` blocks to restrict access to specific actors, but there's no explicit `deny:` block in config to deny specific actors while allowing everyone else. The only way to deny a specific actor is through the permission resolution system's cascading logic, which is indirect.

A `deny:` block could be useful:
```yaml
databases:
  mydb:
    deny:
      id: malicious_bot
```

---

### ISSUE 11 (Design Gap): `also_requires` only supports one level

The `Action` dataclass has `also_requires: str | None` which links one action to another (e.g., `execute-sql` requires `view-database`). This only supports one level of dependency. If action A requires B which requires C, the system doesn't automatically chain these.

Currently, `also_requires` is handled explicitly in both `allowed()` (recursive call) and `build_allowed_resources_sql()` (INNER JOIN of two queries). The recursive call in `allowed()` would handle chains, but `build_allowed_resources_sql()` only handles one level.

---

### ISSUE 12 (Observability): Permission reason tracking loses deny information

When a permission check results in a deny, the `allowed()` method logs the result as `result=False` but doesn't capture the reason. The `check_permission_for_resource()` function only returns a boolean, discarding the reason and source plugin information.

For debugging, it would be valuable to know *why* access was denied - especially for the root user scenario in Issue 1.

---

## Positive Observations

1. **Clean separation of concerns**: The `default_permissions/` package split is well-organized with each module having a clear, focused responsibility.

2. **Parameterized SQL throughout**: All user-controlled values (actor_id, action names, database names, table names in PermissionRowCollector) use parameterized queries. The exceptions noted above (source_plugin, QueryResource) are edge cases.

3. **Comprehensive test coverage**: 263 tests covering a wide range of scenarios including cascading logic, restrictions, config rules, default deny, and endpoints.

4. **Debuggability**: The `/-/rules.json` and `/-/allowed.json` endpoints make it straightforward to understand why a permission decision was made. The trace system exposes the actual SQL executed.

5. **Extension points**: The `permission_resources_sql` hook is well-designed for plugins to contribute rules. The `restriction_sql` mechanism for actor allowlists is elegant.

6. **Pagination**: `allowed_resources()` supports keyset pagination, which is important for instances with many tables/databases.

---

## Recommendations (Priority Order)

### P1: Document root user config interaction (Issue 1)

The `--root` flag documentation should explicitly note that `allow:` blocks in config can override root access. For users who want root to bypass all restrictions, they should include "root" in their allow blocks: `allow: {id: [admin, root]}`.

Consider in the future: a `--root-bypass-config` flag or similar for debugging scenarios where root truly needs unrestricted access.

### P1: Consolidate cascading logic (Issue 2)

Extract the cascading logic into a single shared SQL builder. Both `check_permission_for_resource()` and `_build_single_action_sql()` should call the same underlying function. The `resolve_permissions_from_catalog()` in `utils/permissions.py` should either be deprecated or aligned.

### P1: Fix `source_plugin` SQL injection (Issue 5)

Pass `source_plugin` as a parameter instead of interpolating it. This is a straightforward fix.

### P2: Optimize `include_is_private` for anonymous users (Issue 7)

Short-circuit when `actor=None` - the anonymous check is redundant.

### P2: Only count visible tables (Issue 8)

Pass the allowed table set to the counting logic to avoid wasted queries.

### P3: Replace global `_reason_id` counter (Issue 4)

Use a per-invocation counter or UUID-based naming for reason parameters.

### P3: Simplify `resolve_permissions_from_catalog()` restriction handling (Issue 3)

Refactor to avoid duplicating the entire CTE chain when restrictions are present.

### P4: Add deny reason to permission check logging (Issue 12)

Return `(allowed, reason)` tuples from `check_permission_for_resource()`.

---

## Proposal: Consolidating the Cascading Logic (Issues 2 + 3)

### The problem

The cascading logic ("child > parent > global; deny beats allow at each level") is implemented three times in two files:

| # | Function | File | Used by | SQL pattern |
|---|----------|------|---------|-------------|
| 1 | `_build_single_action_sql()` | `actions_sql.py:246-384` | `allowed_resources()` (production) | 3 separate CTEs (`child_lvl`, `parent_lvl`, `global_lvl`) with `LEFT JOIN` + `GROUP BY` + `MAX()`, then CASE cascade |
| 2 | `check_permission_for_resource()` | `actions_sql.py:555-587` | `allowed()` (production) | `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY depth DESC, ...)` + `LIMIT 1` |
| 3 | `resolve_permissions_from_catalog()` | `permissions.py:197-391` | Tests only | Same `ROW_NUMBER()` as #2, but with the entire CTE chain **tripled** when restrictions are present |

These three implementations must all agree on the resolution semantics. A logic change (e.g., adding a priority tier) would need to be replicated in all three places.

### Why three exist

Each serves a different purpose with different requirements:

- **#1 (bulk resources)**: Needs to evaluate every `(parent, child)` in the `base` CTE. Can't use `ROW_NUMBER()` as easily because it needs the per-resource aggregates available for the `include_is_private` anonymous pass too. Outputs `reason` as JSON array and `is_private`.
- **#2 (single resource)**: Only checks one `(parent, child)`. Much simpler — just filter matching rules, rank, pick winner. Returns boolean.
- **#3 (test utility)**: Returns full resolution details (allow, reason, source_plugin, depth) for every candidate. Used in tests to verify the cascading logic itself.

### Proposed design: One SQL builder, three callers

Introduce a single function `build_cascading_ctes()` that generates the shared CTE fragment, then each caller wraps it with its own `SELECT` and extras.

#### Step 1: Extract `build_rules_union_from_permission_sqls()`

Both production paths (#1 and #2) already have nearly identical code to iterate over `PermissionSQL` objects, collect params, collect `restriction_sqls`, and build the UNION ALL. Factor this into a single shared function:

```python
# In actions_sql.py (or a new shared module)

@dataclass
class CollectedRules:
    """Result of collecting PermissionSQL objects into SQL fragments."""
    rules_union: str          # UNION ALL of all rule SELECTs
    params: dict[str, Any]    # All collected params
    restriction_sqls: list[str]  # restriction_sql fragments

def collect_permission_rules(
    permission_sqls: list[PermissionSQL],
) -> CollectedRules | None:
    """
    Iterate PermissionSQL objects, build the UNION ALL, collect params
    and restriction_sqls.  Returns None if no rule SQL was found.
    """
    rule_parts = []
    all_params = {}
    restriction_sqls = []

    for i, psql in enumerate(permission_sqls):
        all_params.update(psql.params or {})
        if psql.restriction_sql:
            restriction_sqls.append(psql.restriction_sql)
        if psql.sql is None:
            continue
        # Parameterize source_plugin instead of interpolating (fixes Issue 5)
        source_key = f"_src_{i}"
        all_params[source_key] = psql.source
        rule_parts.append(
            f"SELECT parent, child, allow, reason, :{source_key} AS source_plugin"
            f" FROM ({psql.sql})"
        )

    if not rule_parts:
        return None

    return CollectedRules(
        rules_union=" UNION ALL ".join(rule_parts),
        params=all_params,
        restriction_sqls=restriction_sqls,
    )
```

This already fixes **Issue 5** (`source_plugin` injection) as a side effect.

#### Step 2: Extract `build_cascading_ctes()`

The core cascading logic — given a `base` CTE and an `all_rules` CTE, produce a `decisions` CTE — can be expressed as a single function that returns CTE SQL fragments:

```python
def build_cascading_ctes(
    *,
    rules_alias: str = "all_rules",
    base_alias: str = "base",
    include_reasons: bool = False,
) -> str:
    """
    Return CTE SQL for child_lvl, parent_lvl, global_lvl, decisions.

    Expects the caller to already have defined CTEs named `base_alias`
    (with columns: parent, child) and `rules_alias` (with columns:
    parent, child, allow, reason, source_plugin).

    The output `decisions` CTE has columns:
      parent, child, is_allowed, reason
    Where `reason` is either a json_group_array (include_reasons=True)
    or the single winning reason text.
    """
    # The three level CTEs
    level_ctes = []
    for level_name, join_condition in [
        ("child_lvl",  f"ar.parent = b.parent AND ar.child = b.child"),
        ("parent_lvl", f"ar.parent = b.parent AND ar.child IS NULL"),
        ("global_lvl", f"ar.parent IS NULL AND ar.child IS NULL"),
    ]:
        reason_cols = ""
        if include_reasons:
            reason_cols = (
                ",\n         json_group_array(CASE WHEN ar.allow = 0 "
                "THEN ar.source_plugin || ': ' || ar.reason END) AS deny_reasons"
                ",\n         json_group_array(CASE WHEN ar.allow = 1 "
                "THEN ar.source_plugin || ': ' || ar.reason END) AS allow_reasons"
            )
        level_ctes.append(f"""{level_name} AS (
  SELECT b.parent, b.child,
         MAX(CASE WHEN ar.allow = 0 THEN 1 ELSE 0 END) AS any_deny,
         MAX(CASE WHEN ar.allow = 1 THEN 1 ELSE 0 END) AS any_allow{reason_cols}
  FROM {base_alias} b
  LEFT JOIN {rules_alias} ar ON {join_condition}
  GROUP BY b.parent, b.child
)""")

    # The decisions CTE
    if include_reasons:
        reason_case = """
    CASE
      WHEN cl.any_deny = 1 THEN cl.deny_reasons
      WHEN cl.any_allow = 1 THEN cl.allow_reasons
      WHEN pl.any_deny = 1 THEN pl.deny_reasons
      WHEN pl.any_allow = 1 THEN pl.allow_reasons
      WHEN gl.any_deny = 1 THEN gl.deny_reasons
      WHEN gl.any_allow = 1 THEN gl.allow_reasons
      ELSE '[]'
    END AS reason"""
    else:
        reason_case = "'implicit deny' AS reason"  # simple placeholder

    null_safe_join = (
        "b.parent = {a}.parent AND "
        "(b.child = {a}.child OR (b.child IS NULL AND {a}.child IS NULL))"
    )

    decisions_cte = f"""decisions AS (
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
    {reason_case}
  FROM {base_alias} b
  JOIN child_lvl cl ON {null_safe_join.format(a='cl')}
  JOIN parent_lvl pl ON {null_safe_join.format(a='pl')}
  JOIN global_lvl gl ON {null_safe_join.format(a='gl')}
)"""

    return ",\n".join(level_ctes) + ",\n" + decisions_cte
```

#### Step 3: Extract `build_restriction_filter()`

Restriction handling is also duplicated. A single function can generate the restriction CTE and WHERE clause:

```python
def build_restriction_filter(restriction_sqls: list[str]) -> tuple[str, str]:
    """
    Returns (cte_sql, where_clause) for restriction filtering.

    cte_sql: ", restriction_list AS (...)" to append to WITH block
    where_clause: "AND EXISTS (...)" to append to WHERE
    """
    restriction_intersect = "\nINTERSECT\n".join(
        f"SELECT * FROM ({sql})" for sql in restriction_sqls
    )
    cte_sql = f",\nrestriction_list AS (\n  {restriction_intersect}\n)"
    where_clause = """
  AND EXISTS (
    SELECT 1 FROM restriction_list r
    WHERE (r.parent = decisions.parent OR r.parent IS NULL)
      AND (r.child = decisions.child OR r.child IS NULL)
  )"""
    return cte_sql, where_clause
```

#### Step 4: Rewrite the three callers

**`_build_single_action_sql()` (bulk resources)**

```python
async def _build_single_action_sql(datasette, actor, action, *, parent=None,
                                    include_is_private=False):
    action_obj = datasette.actions.get(action)
    base_resources_sql = await action_obj.resource_class.resources_sql(datasette)

    permission_sqls = await gather_permission_sql_from_hooks(...)
    if permission_sqls is SKIP_PERMISSION_CHECKS:
        return ...  # early return unchanged

    collected = collect_permission_rules(permission_sqls)
    if collected is None:
        return ...  # empty result unchanged

    all_params = collected.params

    # Build WITH clause
    cte_parts = [
        f"WITH\nbase AS (\n  {base_resources_sql}\n)",
        f"all_rules AS (\n  {collected.rules_union}\n)",
    ]

    # Anonymous rules for is_private (if needed)
    if include_is_private:
        anon_collected = ...  # same anon logic as before, but using collect_permission_rules
        cte_parts.append(f"anon_rules AS (\n  {anon_collected.rules_union}\n)")

    # Core cascading logic — ONE call
    cte_parts.append(build_cascading_ctes(include_reasons=True))

    if include_is_private:
        cte_parts.append(build_cascading_ctes(
            rules_alias="anon_rules",
            base_alias="base",
            # Use different CTE names to avoid collision:
            # This variant would need a prefix parameter, e.g. prefix="anon_"
        ))
        # ... or simpler: call a second time with aliased names

    # Restriction filter
    restriction_cte = ""
    restriction_where = ""
    if collected.restriction_sqls:
        restriction_cte, restriction_where = build_restriction_filter(
            collected.restriction_sqls
        )

    # Final SELECT
    select_cols = "parent, child, reason"
    if include_is_private:
        select_cols += ", is_private"

    query = (
        ",\n".join(cte_parts) + restriction_cte +
        f"\nSELECT {select_cols}\nFROM decisions\nWHERE is_allowed = 1"
        + restriction_where
        + (f"\n  AND parent = :filter_parent" if parent else "")
        + "\nORDER BY parent, child"
    )
    return query, all_params
```

**`check_permission_for_resource()` (single resource)**

This can now be rewritten to use the same `build_cascading_ctes()` with a single-row `base`:

```python
async def check_permission_for_resource(*, datasette, actor, action, parent, child):
    rules_union, all_params, restriction_sqls = await build_permission_rules_sql(
        datasette, actor, action
    )
    if not rules_union:
        return False

    all_params["_check_parent"] = parent
    all_params["_check_child"] = child

    # Check restrictions first (unchanged fast-path)
    if restriction_sqls:
        ...  # existing restriction check, unchanged

    # Use the shared cascading logic with a single-row base
    base_sql = "SELECT :_check_parent AS parent, :_check_child AS child"
    cascade = build_cascading_ctes()

    query = f"""
WITH
base AS ({base_sql}),
all_rules AS ({rules_union}),
{cascade}
SELECT COALESCE((SELECT is_allowed FROM decisions), 0) AS is_allowed
"""
    result = await datasette.get_internal_database().execute(query, all_params)
    return bool(result.rows[0][0]) if result.rows else False
```

This replaces the current depth/ROW_NUMBER approach with the same `child_lvl`/`parent_lvl`/`global_lvl` pattern, ensuring identical semantics.

**`resolve_permissions_from_catalog()` (test utility)**

This becomes a thin wrapper too. Since it's test-only, the main benefit is eliminating 250 lines of duplicated SQL:

```python
async def resolve_permissions_from_catalog(db, actor, plugins, action,
                                           candidate_sql, candidate_params=None,
                                           *, implicit_deny=True):
    # Resolve plugins (existing code, unchanged)
    resolved_plugins, restriction_sqls = ...

    union_sql, rule_params = build_rules_union(actor, resolved_plugins)
    all_params = {**(candidate_params or {}), **rule_params, "action": action}

    cascade = build_cascading_ctes(
        include_reasons=True,
        base_alias="cands",
        rules_alias="rules",
    )

    # One query, no duplication
    restriction_cte = ""
    restriction_where = ""
    if restriction_sqls:
        restriction_cte, restriction_where = build_restriction_filter(restriction_sqls)

    sql = f"""
    WITH
    cands AS ({candidate_sql}),
    rules AS ({union_sql}),
    {cascade}
    {restriction_cte}
    SELECT
      c.parent, c.child,
      COALESCE(d.is_allowed, CASE WHEN :implicit_deny THEN 0 ELSE NULL END) AS allow,
      d.reason, :action AS action,
      ...
    FROM cands c
    LEFT JOIN decisions d ON c.parent = d.parent AND c.child = d.child
    {restriction_where}
    ORDER BY c.parent, c.child
    """

    rows = await db.execute(sql, {**all_params, "implicit_deny": ...})
    return [dict(r) for r in rows]
```

This eliminates the 135-line SQL triplication entirely.

### The `include_is_private` complication

The `include_is_private` path is the one wrinkle. It needs to run the cascading logic twice: once for the real actor and once for anonymous. The current code duplicates all three level CTEs with `anon_` prefixes.

With the shared builder, we'd need `build_cascading_ctes()` to accept a `prefix` parameter so it can generate `anon_child_lvl`, `anon_parent_lvl`, etc.:

```python
def build_cascading_ctes(*, rules_alias="all_rules", base_alias="base",
                          include_reasons=False, prefix=""):
    # Use prefix for all CTE names:
    # f"{prefix}child_lvl", f"{prefix}parent_lvl", etc.
```

Then the caller does:

```python
ctes = build_cascading_ctes(include_reasons=True)          # -> child_lvl, decisions
ctes += build_cascading_ctes(rules_alias="anon_rules",     # -> anon_child_lvl, anon_decisions
                              prefix="anon_",
                              include_reasons=False)
```

And the final SELECT joins both `decisions` and `anon_decisions`.

### Impact summary

| Before | After |
|--------|-------|
| `_build_single_action_sql`: ~180 lines of CTE construction | ~40 lines + shared builder |
| `check_permission_for_resource`: ~35 lines of cascading SQL | ~10 lines + shared builder |
| `resolve_permissions_from_catalog`: ~250 lines, SQL tripled when restrictions present | ~30 lines + shared builder |
| `source_plugin` interpolated unsafely in 3 places | Parameterized in `collect_permission_rules()` |
| `build_rules_union` in `permissions.py` (test-only duplicate) | Replaced by shared `collect_permission_rules()` |

Total: ~465 lines of SQL-building code reduced to ~80 lines of callers + ~80 lines of shared builders. Three implementations of cascading logic become one.

### Migration plan

1. Add `collect_permission_rules()` and `build_cascading_ctes()` and `build_restriction_filter()` to `actions_sql.py` (or a new `datasette/utils/permission_sql_builder.py`)
2. Rewrite `check_permission_for_resource()` to use the shared builder
3. Rewrite `_build_single_action_sql()` to use the shared builder (including `include_is_private` prefix support)
4. Rewrite `resolve_permissions_from_catalog()` to use the shared builder
5. Delete `build_rules_union()` from `permissions.py`
6. Run the full test suite — all 263 tests must still pass since behavior is unchanged
7. Verify via `?_trace=1` that the generated SQL is correct and equivalently performant
