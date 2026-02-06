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
