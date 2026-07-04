# Design review: Datasette's SQL-based permission system

*Reviewed at commit `58c07cc` (main, July 2026). All benchmark numbers and bug
reproductions in this document were verified against this checkout; repro
scripts are in Appendix B.*

## 1. Executive summary

The core architectural bet — **compile permission rules from every source into
one SQL query against the internal catalog, so that "list everything this
actor can see" is a single query rather than N Python checks** — is the right
bet. It solves the historic `permission_allowed()` N+1 problem, it gives
plugins real expressive power, and the `reason`/`source_plugin` columns bake
explainability into the data model rather than bolting it on.

The current execution of that bet has three classes of problems:

1. **The primary goal is not yet met.** On a vanilla 2,000-table instance with
   *zero* custom rules, `allowed_resources("view-table", include_is_private=True)`
   exceeds the internal database's time limit and the homepage returns
   **HTTP 500**. Point checks degrade from 0.6ms to 76ms each with 200 config
   rules. The generated SQL shape — three `LEFT JOIN` + `GROUP BY` passes over
   `resources × rules`, JSON reason aggregation always on, rule rows inlined as
   `UNION ALL` text — is the cause, and it is fixable without changing the
   architecture (§4.1, §6-R3).

2. **There are three parallel implementations of the resolution semantics**
   (listing, point-check, plus a third used only by tests), and they have
   already diverged: `datasette.allowed()` and `datasette.allowed_resources()`
   give **different answers** for actions with chained `also_requires`
   (§4.2). Several smaller contract bugs — automatic parameters that
   aren't always bound, silent parameter collisions, misattributed rule
   sources — all stem from the same root: the plugin contract is *SQL strings
   plus conventions*, and each code path re-implements the conventions
   slightly differently (§4.3–4.6, §5.2).

3. **Auditability is designed-in but under-delivered.** Reasons and source
   attribution exist, and there are five debug endpoints — but the tools are
   fragmented, one of them paginates in Python contradicting the SQL design,
   the trace only shows the *winning* rules, the `restriction_sql` half of the
   plugin contract is completely undocumented, and the central docs section
   explaining resolution contains typos and no worked example (§5.6, §5.7).

Recommended path: fix the verified defects now (§4), consolidate to **one rule
compiler with data-first rules and a temp-table execution strategy** (§6), and
seriously evaluate the "compiled grants ledger" model (§7.1) — which keeps the
SQL execution model but moves rule *evaluation* from request time to
write time, making permissions indexed, diffable, and auditable as a table.

---

## 2. The system as built

### 2.1 Concepts

| Concept | Where | Role |
|---|---|---|
| `Action` | `datasette/permissions.py` | Named operation (`view-table`), optional `abbr` (`vt`), optional `resource_class`, optional `also_requires` chain |
| `Resource` | `datasette/permissions.py`, `datasette/resources.py` | Typed `(parent, child)` pair; hierarchy hard-capped at 2 levels; subclasses supply `resources_sql()` returning *all* resources of that type from the catalog |
| `PermissionSQL` | `datasette/permissions.py` | A plugin's contribution: SQL yielding `(parent, child, allow, reason)` rows, bound `params`, and/or a `restriction_sql` allowlist filter |
| `permission_resources_sql` hook | `hookspecs.py` | Called per `(actor, action)`; returns `PermissionSQL` objects |
| Internal catalog | `catalog_databases`, `catalog_tables`, `catalog_views`, `queries` in the internal DB | The "base" set that rules are joined against |

### 2.2 Rule sources shipped in core

All of core's own behavior goes through the same hook
(`datasette/default_permissions/`):

- `defaults.py` — root-level allow rows for the default-public actions
  (`view-instance`, `view-table`, …) unless `--default-deny`; the
  `default_allow_sql` deny; query-ownership rules for stored queries.
- `config.py` — `ConfigPermissionProcessor` walks `datasette.yaml`
  (`permissions:` blocks at root/db/table/query level, `allow:` /`allow_sql:`
  blocks), evaluates each allow block against the actor **in Python**
  (`actor_matches_allow`), and emits the verdicts as constant
  `SELECT :p AS parent, …` rows.
- `restrictions.py` — the `_r` actor key (API-token restrictions) becomes a
  `restriction_sql` allowlist, `INTERSECT`ed across providers and applied as a
  final `EXISTS` filter that can only *remove* results.
- `root.py` — a root-level allow row for the `--root` user.

### 2.3 Resolution semantics

1. **Specificity cascade:** child-level rules beat parent-level rules beat
   global rules.
2. **Deny beats allow within the same level.**
3. **Implicit deny** if no rule matches.
4. **Restrictions** filter the result set afterwards; they can never grant.
5. `also_requires` composes actions (`execute-sql` also requires
   `view-database`).

### 2.4 The three resolvers

The semantics above are implemented **three times**:

| Path | File | Strategy |
|---|---|---|
| Listing (`allowed_resources[_sql]`) | `utils/actions_sql.py::_build_single_action_sql` | Three `LEFT JOIN`+`GROUP BY` passes (`child_lvl`/`parent_lvl`/`global_lvl`) over `base × rules`, `CASE` cascade, JSON reason aggregation; duplicated again for anonymous when `include_is_private=True` |
| Point check (`allowed`/`allowed_many`) | `utils/actions_sql.py::check_permissions_for_actions` | Per-action rules CTE, depth-ranked `ORDER BY … LIMIT 1` verdict |
| `resolve_permissions_from_catalog` | `utils/permissions.py` | `ROW_NUMBER()` window-ranked winner — **only referenced by tests**; ships in the package as dead weight |

`allowed_many` batches several actions into one query, expands
`also_requires` transitively in Python, and consults a request-scoped
`contextvars` cache. The listing path handles `also_requires` differently — by
`INNER JOIN`ing two independently built listing queries (see §4.2).

### 2.5 Debug and audit surface

- `/-/permissions` — recent-checks log (in-memory `deque(maxlen=200)`) plus a
  playground for checking an arbitrary actor/action/resource.
- `/-/allowed` — list resources for an action for the *current* actor, with
  reasons if you hold `permissions-debug`.
- `/-/rules` — dump the assembled rule rows (`parent, child, allow, reason,
  source_plugin`) per action.
- `/-/check` — point-check API for the current actor.
- `/-/allow-debug` — test an allow block against an actor document.

This is a genuinely better debug surface than most permission systems ship
with. Its problems are fragmentation and depth, not absence (§5.6).

---

## 3. Assessment against the stated goals

### Goal: efficiently list all resources an actor can act on

**Not currently met.** Measured on this checkout (one database, 2,000 tables,
in-memory internal DB, default settings; script in Appendix B):

| Scenario | Result |
|---|---|
| `allowed_resources("view-table")`, 0 config rules, first 1,000 rows | 839 ms |
| Same with 200 table-level config rules | 934 ms |
| Same with `include_is_private=True` (any rule count, even zero) | **`QueryInterrupted` — exceeds the 1s internal time limit** |
| `GET /` (homepage uses `include_is_private=True`) | **HTTP 500 in 1.2s** |
| Single `allowed()` point check, 0 config rules | 0.6 ms |
| Single `allowed()` point check, 50 config rules | 3.9 ms |
| Single `allowed()` point check, 200 config rules | **75.9 ms** |

Why (all fixable, see §6-R2/R3):

- The rules CTE is inlined **SQL text** — one `SELECT :cfg_N_parent …` per
  rule joined with `UNION ALL`. 200 config rules ≈ 100KB of SQL and 800+ bound
  parameters *per check*, re-generated and re-parsed on every call. SQL text
  size — not query execution — dominates the point-check numbers.
- CTE results have no indexes, so each of the three level-joins is a nested
  loop over `2,000 tables × R rules`, and the cascade does that three times
  (six with `include_is_private`).
- `json_group_array(...)` reason aggregation runs on every row of every level
  even when the caller never asked for reasons.
- `include_is_private=True` rebuilds the entire anonymous-actor cascade inside
  the same query rather than reusing anything.
- Pagination (`LIMIT`) is applied *after* the full cascade is computed, so
  every page pays the full O(N×R) cost; `PaginatedResources.all()` re-runs
  the whole thing per page with default `limit=100` — the homepage on the
  2,000-table instance would run the failing query 20 times even if each
  succeeded.

### Goal: flexibility for plugins

**Strong — the best part of the design.** Arbitrary SQL against the catalog
means a plugin can express "tables whose name starts with `temp_`", "rows in
my own grants table", "databases tagged in a metadata table" without core
anticipating any of it. Custom `Resource` subclasses + `resources_sql()` let
plugins bring entirely new resource types (documents, models) into the same
machinery, including listing. `restriction_sql` gives token-scoping plugins a
sound "can only narrow" primitive.

The flexibility has sharp edges, though: the contract is stringly-typed
(column names, parameter conventions, "please prefix your params" in the docs)
and core cannot inspect, validate, optimize, or safely compose what plugins
hand it (§5.2). Everything is possible; nothing is checkable.

### Goal: understandable and auditable by administrators

**Mixed.** Right instincts — reasons attached to every verdict, source
attribution, a debug playground, `--default-deny`. But:

- An administrator cannot answer "*who* can see table X and *why*" in one
  place; they must mentally join five debug tools, and none shows losing
  rules, restriction filtering, or the `also_requires` chain (§5.6).
- The precedence rule that a **more-specific allow overrides a broader deny**
  surprises anyone with AWS-IAM/Postgres expectations, and it means *any
  installed plugin can grant access to anything* — config has no "final deny"
  (§5.3).
- Actor restrictions are a second, parallel permission mini-language (`_r`,
  `a`/`d`/`r` keys, action abbreviations) with its own semantics and its own
  code paths (§5.4).
- The docs' central "How permissions are resolved" section is thin, contains
  typos ("actor cas access", "permission chucks", "replying True"), and
  `restriction_sql` — half the plugin contract — is documented **nowhere**
  (§5.7).

---

## 4. Verified defects

Each of these was reproduced against this checkout (Appendix B).

### 4.1 Homepage 500 / listing performance cliff

As measured above: `GET /` on a 2,000-table instance returns HTTP 500 because
the `view-table` + `include_is_private` listing query exceeds the internal
database time limit. Note the failure mode compounds: a permission query that
times out surfaces as an unhandled `QueryInterrupted` → 500, rather than a
clear "permission resolution timed out" error.

### 4.2 `allowed()` and `allowed_resources()` disagree on chained `also_requires`

`allowed_many()` expands `also_requires` **transitively** in Python
(`store-query` → `execute-sql` → `view-database`). The listing path
(`build_allowed_resources_sql`) combines only the *first* hop: it INNER JOINs
`store-query` with `execute-sql` and never consults `view-database`.

Verified: with a plugin that denies `view-database` but allows `store-query`
and `execute-sql` globally:

```
datasette.allowed(action="store-query", resource=DatabaseResource("_memory"))  # False
datasette.allowed_resources("store-query", actor)                              # ['_memory']  ← disagrees
```

This is the drift risk of three resolvers made concrete. It is
security-relevant: any code that trusts the listing path (menus, plugin UIs,
the `/-/allowed` API) will advertise — and potentially act on — permissions
the enforcement path denies. The same divergence class will reappear unless
the resolvers are unified (§6-R1).

### 4.3 The documented "automatic" parameters are not reliably bound

`internals.rst` promises `:actor`, `:actor_id` and `:action` are "automatically
available" in `PermissionSQL` SQL. The implementation
(`gather_permission_sql_from_hooks`) does:

```python
params = permission_sql.params or {}   # fresh dict if params is None…
params.setdefault("actor", actor_json) # …mutated…
```

…and the fresh dict is then **discarded** (never assigned back to
`permission_sql.params`). The promise only holds if *some other* collected
rule happens to carry a non-None params dict into the shared merge. Core's
default-allow rules usually do — so it works by accident. Under
`--default-deny` with no config rules, a plugin using `:actor_id` with
`params=None` crashes every check with
`ProgrammingError: You did not supply a value for binding parameter :actor_id.`
(verified).

### 4.4 Rule sources are misattributed

`gather_permission_sql_from_hooks` pairs hook results with hook
implementations by index:

```python
hookimpls = hook_caller.get_hookimpls()
hook_results = list(hook_caller(...))
for index, result in enumerate(hook_results):
    hookimpl = hookimpls[index]
```

But pluggy **omits `None` results** from `hook_results` while `hookimpls`
retains every implementation, so the lists misalign whenever any hook returns
`None` — which is the normal case (most hooks return `None` for most actions).
Verified: a third-party plugin's rules were attributed to
`datasette.default_permissions` in the generated SQL. This silently corrupts
exactly the metadata (`source_plugin`, shown in `/-/rules` and in reasons)
that the auditability story depends on.

### 4.5 Parameter namespacing is inconsistent; collisions are silent (by inspection)

- Listing path: `all_params.update(p.params)` — **no namespacing**. Two
  plugins that both bind `:user_id` (or one plugin returning two
  `PermissionSQL`s reusing a name) silently last-write-wins, changing the
  *other* plugin's rule semantics. The docs handle this by asking plugins to
  prefix their params — a convention, unenforced.
- Point-check path: params are rewritten with a regex **per action**
  (`a0_user_id`) — but still not per plugin, so cross-plugin collisions
  survive there too.
- The `include_is_private` anonymous-rules branch rewrites params with plain
  `str.replace(":key", ":anon_key")` — no word boundary, so `:p` corrupts
  `:p2` — while the point-check path uses a correct
  regex-with-lookahead. Same job, three implementations, one of them wrong.

Related: `PermissionSQL.allow()/deny()` mint parameter names from a global
module-level counter (`_reason_id`) — global mutable state where content
hashing or per-gather counters would do; and `p.source` is interpolated into
the SQL as `'{p.source}'` unescaped, so a plugin name containing `'` breaks
every query it participates in (robustness, not injection — the value comes
from the plugin itself).

### 4.6 Assorted smaller issues (by inspection)

- `build_permission_rules_sql` docstring says it returns a 2-tuple; it returns
  a 3-tuple.
- Keyset pagination encodes a `NULL` child as the literal string `"None"`
  (`tilde_encode(str(None))`) and its `WHERE (parent > :p OR (parent = :p AND
  child > :c))` silently drops rows with `NULL` children on continuation
  pages. It happens to work for the built-in resource types (databases have
  unique parents; tables/queries always have children) but is a trap for any
  plugin resource type with NULL children.
- `defaults.py` still does `reason.replace("'", "''")` on a value that is
  passed as a bound parameter — leftover from a string-interpolation era;
  reads as if interpolation might still happen somewhere.
- The obsolete `Permission` dataclass ships with a comment saying it is
  obsolete; `resolve_permissions_from_catalog` / `resolve_permissions_with_candidates`
  (~300 lines including a third copy of the cascade) are exercised only by
  tests.

---

## 5. Design concerns

### 5.1 Three resolvers, one intended semantics

§4.2 is the proof that this is not hypothetical. Cascade precedence,
`also_requires`, restriction filtering, param handling, and skip-checks each
exist in 2–3 variants. There is no test asserting the core invariant:

> for every actor, action, resource:
> `allowed(action, r, actor)` ⇔ `r ∈ allowed_resources(action, actor)`

That property test would have caught §4.2 and will catch the next drift.

### 5.2 The plugin contract is "SQL strings + conventions"

Column names, parameter naming, reserved parameters, source attribution,
quoting — all conventions enforced by nothing. Because rules arrive as opaque
SQL text, core cannot:

- validate a rule at registration time (typos surface as runtime SQL errors
  inside a 100KB generated query);
- index or pre-aggregate rules (root cause of §4.1);
- show an administrator "the rules" in any form other than *executing*
  everything (`/-/rules` runs the SQL to show its output — correct, but
  policies can't be reviewed statically);
- statically analyze policies (find shadowed rules, contradictions, or answer
  "which rules mention table X?").

The telling detail: core's own `config.py` doesn't want the SQL flexibility —
it evaluates everything in Python and emits *constant rows* through
`PermissionRowCollector`. The majority use case is rows, not SQL; the design
taxes the common case with the escape hatch's costs. (§6-R2 proposes inverting
this.)

### 5.3 "Specific allow beats broader deny" + "any plugin can grant" needs guardrails

The cascade's child-allow-overrides-parent-deny rule is a defensible design
choice (it's what makes "deny the db, allow one table" expressible), but it
combines badly with the open hook: an administrator who writes a root-level
deny in `datasette.yaml` has **no way to make it final**. Any installed
plugin can emit a child-level allow row that silently wins. For an
administrator, "what can Alice see?" is only answerable by trusting every
installed plugin's rule emission.

Options worth considering, in increasing strength:

1. Document it loudly ("installing a plugin extends the set of parties who can
   grant access") and surface *which plugin granted* prominently in every
   debug view (blocked today by §4.4).
2. Rule *tiers*: config rules could optionally be marked `final`, evaluated
   after plugin rules with deny-wins.
3. A `--paranoid` mode where config is the ceiling: plugins may only narrow.

### 5.4 Restrictions are a second permission language

The `_r` mechanism has its own vocabulary (`a`/`d`/`r`, action
abbreviations), its own resolution semantics (pure allowlist + `INTERSECT`
across providers), its own Python fast path (`restrictions_allow_action`), and
special-case interplay with config (`_add_restriction_gate_denies`, the
hardest ~40 lines in `config.py`, exist solely to stop a child-level config
allow from defeating a restriction). The config processor's
`is_in_restriction_allowlist` additionally has a "parent proceeds if any child
is allowlisted" special case that the SQL `EXISTS` filter does not mirror —
another place semantics live twice, subtly differently.

The concept is right (attenuated tokens must never escalate). The
implementation would be simpler as a first-class post-filter stage in the one
canonical compiler, with a documented wire format — and §7.3 argues
restrictions and grants may want to become the *same* algebra.

Also: action abbreviations (`vt`, `es`) exist to keep tokens small, but they
leak into every comparison via `get_action_name_variants` — dual-name matching
in at least four call sites. Consider making abbreviation expansion a
token-decode concern, so the rest of the system only ever sees full names.

### 5.5 The two-level hierarchy is a hard cap

`Resource.__init_subclass__` raises on a third level. Fine for
instance/database/table, but plugins with deeper models
(collection/document/section) must flatten, and a future column-level
permission would break the world. The `(parent, child)` schema also leaks
generic names into every API response and debug view where
`database`/`table` would read better. Not urgent — but this is exactly the
kind of decision that becomes unfixable after a 1.0 API freeze, so it deserves
an explicit "yes, forever" or a path-style key design (§7.1's ledger uses
one) now.

### 5.6 Debug tooling: right pieces, missing the whole

- Five endpoints with overlapping-but-different capabilities and no
  cross-links; an admin must already understand the system to know which tool
  answers which question.
- `/-/allowed` fetches **all** rows into Python, then applies the `child`
  filter and offset pagination in Python — quietly contradicting (and
  bypassing) the keyset-pagination design directly underneath it, and turning
  the debug tool into the least scalable consumer of the API it demonstrates.
- Reasons only surface the winning level's rules. "Why *can't* Alice see
  X?" — the auditor's most common question — has no answer today: you can't
  see the losing allow that was beaten by a deny, the restriction that
  filtered a granted row out, or the `also_requires` link that failed.
- The `/-/permissions` check log is a process-local `deque(maxlen=200)` —
  gone on restart, per-process on multi-worker deploys.

### 5.7 Documentation and naming residue

- `restriction_sql`: undocumented (zero occurrences under `docs/`).
- `internals.rst` documents the automatic parameters unconditionally (§4.3
  makes that false), and documents `PermissionSQL` with a stale field order.
- `authentication.rst`'s "How permissions are resolved" — the section an
  auditor most needs — has typos ("actor cas access", "permission chucks",
  "replying ``True`` to all permission chucks") and describes the mechanism in
  prose without a precedence table or a single worked multi-rule example.
- Terminology drift: the hook is `permission_resources_sql`, the registry is
  `datasette.actions`, registered by `register_actions`, holding `Action`
  objects, documented under "Permissions"; the obsolete `Permission` class is
  still importable. Pick "action" everywhere and finish the migration before
  1.0 freezes the names.

---

## 6. Recommendations (incremental — keep the architecture)

Ordered so that each unlocks the next; R1–R5 are pre-1.0 material because they
change plugin-visible behavior.

**R1. One rule compiler, one semantics, one parity test.**
Extract a single module that owns: gathering hook results, param namespacing,
`also_requires` expansion (transitive, in one place — or better, resolve the
chain into a frozen set per action at registration time), restriction
collection, and cascade compilation. Both `allowed_many` and
`allowed_resources_sql` consume it; delete the test-only third resolver and
the obsolete `Permission` class. Add the property test from §5.1 (hypothesis
over random rule sets, or brute-force over fixture matrices) so listing and
point-check can never disagree again. This closes §4.2 structurally, not just
locally.

**R2. Make rules data-first; SQL becomes the escape hatch.**
`PermissionRowCollector` already proves core wants rows. Let
`permission_resources_sql` (or a successor hook name like `permission_rules`)
return row objects (`Rule(parent, child, allow, reason)`) as the primary form,
with `PermissionSQL` still accepted for genuinely dynamic cases. Then the
compiler can:

- insert row-rules into an **indexed temp table** once per request (or cache
  by `(actor-hash, action)`), instead of generating O(rules) SQL text — this
  alone removes the 76ms point-check pathology (§4.1), which is dominated by
  SQL parse size, and gives the listing query indexed joins;
- validate rules at collection time (types, unknown actions, reserved names)
  with plugin-attributed errors;
- namespace parameters automatically per (plugin, hook-result) for the SQL
  escape hatch, using the one correct regex implementation (§4.5), and always
  bind `:actor`/`:actor_id`/`:action` at the query level rather than
  per-rule-params (§4.3);
- fix source attribution by carrying the plugin name from the hookimpl at
  gather time, matched correctly (§4.4 — pluggy's
  `hook_caller.call_extra`/wrapper mechanisms or simply wrapping each impl can
  give exact pairing).

**R3. Fix the listing query shape.**
Replace the three `LEFT JOIN`+`GROUP BY` level passes with the single
depth-ranked pass that already exists in the codebase (the `ROW_NUMBER()`
winner CTE), computed off the indexed rules table from R2:

- compute reasons only when `include_reasons=True` (the JSON aggregation is
  pure overhead otherwise);
- for `include_is_private`, evaluate the anonymous verdict from the *same*
  rules table (anon rules are a second small rule set, not a reason to
  duplicate the whole query);
- keyset-paginate with NULL-safe comparisons and an explicit NULL token
  encoding rather than `"None"` (§4.6);
- add a CI benchmark fixture (e.g. 5,000 tables × 500 rules) with a budget
  assertion, so the homepage-500 class of regression (§4.1) is caught by
  tests, not users.

The point of the original three-pass shape was clarity of the cascade; that
clarity should live in the one compiler's tests, not in the runtime query
plan.

**R4. Decide the trust model and say it out loud.**
Whichever option from §5.3 is chosen (even "option 1: document it"), the
decision belongs in `authentication.rst` next to a precedence table and a
worked example: rules from three sources, one resource, showing exactly which
row wins and why. Fail closed *gracefully*: a permission query that errors or
times out should produce a clear "permission resolution failed" 500 with the
action named, not a raw `QueryInterrupted` (§4.1) — and the internal DB may
deserve a higher/separate time limit for permission queries than user-facing
SQL.

**R5. Fold restriction handling into the compiler.**
One implementation of the allowlist semantics (SQL `EXISTS` version), used by
both paths; `restrictions_allow_action` and the config restriction-gate become
thin delegations or disappear. Expand abbreviations at token decode. Document
the `_r` format as a reference table.

**R6. Unify the debug tools around "explain".**
One endpoint (and matching CLI) that answers the auditor's actual questions:

```
/-/permissions/explain?actor={...}&action=view-table&parent=db&child=t
```

returning the full trace: every candidate rule from every source (winning
*and* losing, with source plugin — fixed by R2), the specificity level at
which the decision was made, restriction filtering before/after, the
`also_requires` chain with each link's verdict, and the final answer. The
existing five pages become views over this one trace. Add:

- `datasette permissions list|explain|diff|dump` CLI (works offline against
  config + plugins; `diff actor-a.json actor-b.json` for "what does this role
  change?"; `dump --csv` for compliance export);
- a persistent, opt-in check log (internal DB table with a cap) replacing the
  in-memory deque for multi-process deployments.

**R7. Documentation pass.**
Fix the typos in the resolution section; document `restriction_sql`, the
automatic-parameter contract (after R2 makes it true), the trust model (R4),
the `_r` reference; add a "Debugging permissions" guide that walks one
scenario through the explain tool; add a cookbook (default-deny + groups
plugin, public-except-one-table, token-scoped API access).

---

## 7. Radically different approaches

The stated goals pull in different directions: *arbitrary per-request SQL*
(flexibility) fights *indexed lookup* (listing speed) fights *static
reviewability* (audit). The current design sits at the "maximum flexibility"
corner and pays for it at the other two. Both alternatives below deliberately
move the trade-off point.

### 7.1 The compiled grants ledger (recommended candidate)

**Idea: stop evaluating rules at request time. Evaluate them when they
*change*, into a physical table; requests just read the table.** "Compile,
don't interpret."

Split the problem in two:

**Phase A — actor → principals (request time, Python, cheap).**
A new hook resolves an actor into a set of principal strings:

```python
@hookimpl
def actor_principals(datasette, actor):
    # e.g. ["anyone", "authenticated", "id:alice", "team:analytics", "role:admin"]
    ...
```

This is where per-request dynamism lives (group membership, IdP claims,
"business hours"). It is pure Python, trivially testable, and — crucially —
plugins express *identity*, not *policy*.

**Phase B — grants ledger (write time, SQL, indexed).**
A real table in the internal database:

```sql
CREATE TABLE grants (
    principal TEXT NOT NULL,     -- "team:analytics", "anyone", …
    action    TEXT NOT NULL,     -- full names only
    parent    TEXT,              -- NULL = all
    child     TEXT,              -- NULL = all at parent level
    child_like TEXT,             -- optional pattern grant: 'temp_%'
    allow     INTEGER NOT NULL,  -- 1 grant / 0 deny
    tier      INTEGER NOT NULL DEFAULT 0,  -- e.g. config-final > plugin > default
    source    TEXT NOT NULL,     -- plugin/config attribution
    reason    TEXT NOT NULL,
    created_at TEXT, expires_at TEXT
);
CREATE INDEX idx_grants_lookup ON grants (action, principal, parent, child);
```

Populated by: the config compiler at startup; plugins via a
`register_grants` hook or by writing rows directly and emitting an
invalidation event; token restrictions as deny-tier rows scoped to a
`token:<id>` principal. Rules that today are dynamic SQL over the catalog
("all tables starting with `temp_`") become pattern rows or are re-expanded by
a catalog-change listener (Datasette already has `refresh_schemas` as the
natural hook point).

**Reads become trivial and fast:**

```sql
-- Point check: microseconds, fully indexed
SELECT allow FROM grants
WHERE action = :action AND principal IN (:p1, :p2, :p3)
  AND (parent IS NULL OR parent = :parent)
  AND (child IS NULL OR child = :child OR :child LIKE child_like)
ORDER BY tier DESC,
         (child IS NOT NULL OR child_like IS NOT NULL) DESC,
         (parent IS NOT NULL) DESC,
         allow ASC
LIMIT 1;

-- Listing: one indexed join against the catalog — same cascade, same
-- deny-beats-allow, but O(matching grants) instead of O(resources × rules),
-- and the query text is CONSTANT SIZE regardless of rule count.
```

**What this buys, measured against the three goals:**

- *Listing*: indexed join, constant-size SQL. Thousands of tables ×
  hundreds of grants is interactive by construction. Pagination is ordinary
  SQL pagination.
- *Flexibility*: preserved but relocated — plugins do identity (Phase A)
  and grant management (writes), instead of per-request policy SQL. A
  compatibility shim can run legacy `PermissionSQL` plugins by materializing
  their output into session-scoped grants, with a deprecation warning on
  divergence.
- *Auditability — the transformative win*: **the policy is a table.**
  `SELECT * FROM grants WHERE parent='accounting'` *is* the audit. Dump it,
  diff it between deploys, keep a `grants_history` trigger for "who could see
  this table last March?", review it in a PR when config changes. The `tier`
  column gives administrators the "final deny" that §5.3 cannot express
  today. Explain-tooling becomes a `SELECT`, not a query-plan archaeology
  session.

**Costs and open problems, honestly:**

- Rules conditioned on arbitrary actor JSON must be expressible as principals;
  pathological cases ("actors whose email domain matches a table naming
  scheme") get awkward. Keeping a narrow `PermissionSQL` escape hatch that is
  documented as *slow path, unindexed* is probably the right release valve.
- Cache invalidation is now a real subsystem (schema changes × plugin grant
  changes × config reloads). Datasette's catalog-refresh machinery is the
  precedent, but it must be airtight because staleness here is a security bug
  — an *allow* that outlives its revocation. Mitigations: version-stamp the
  ledger and rebuild on any registered source's version bump; deny-tier rows
  take effect immediately by also being checked from Phase A.
- Ephemeral principals (a token minted per request) need session-scoped grant
  overlays — which is what `restriction_sql` is today, kept as a read-time
  `EXISTS` filter against a small per-request set.

### 7.2 Policies as data (Cedar-style), compiled to SQL

A middle path that keeps request-time evaluation but replaces *SQL strings*
with *declarative policy objects*:

```python
Rule(
    effect="allow",
    principals={"team": "analytics"},     # allow-block-style actor matcher
    actions=["view-table", "view-query"],
    resources=ResourceMatch(parent="analytics", child_like="*"),
    priority=10,
    reason="analytics team reads analytics DB",
)
```

Core compiles these to exactly the SQL it generates today — but because it
*understands* the rules, it can also: statically list all policies touching a
resource, detect shadowed/contradictory rules at startup, render
human-readable policy summaries for admins, and generate the explain trace
without executing anything. This is essentially R2 taken to its logical
conclusion (the allow-block language generalized and given to plugins), and it
composes with either the current engine or the ledger of §7.1 — policy objects
are what you'd *write*, the ledger is what they'd *compile to*. If §7.1 feels
too big for one step, §7.2 is the radical change with a migration path
measured in weeks: the hook keeps its shape, but returns data instead of SQL.

### 7.3 Authentication-time capabilities

Invert the lookup entirely: resolve permissions **once, when the actor is
established**, and carry them in the actor — a generalization of the existing
`_r` restrictions from "attenuation only" to the full grant set:

```json
{"id": "alice", "_caps": {"view-table": ["analytics/*", "prod/orders"],
                           "execute-sql": ["analytics"]}}
```

Checks become pure functions of `(actor, catalog)` — no rule gathering, no
per-request SQL. Listing is one indexed match of patterns against the catalog.
Signed tokens make the whole thing stateless across processes and even across
services (a companion API can verify capabilities without running Datasette).

Honest assessment: revocation latency (capabilities live until the
cookie/token expires), token size pressure (hence patterns, hence the
abbreviation problem again), and login-time cost make this wrong as *the*
system. But it is worth naming because Datasette already has half of it
(`_r`), and the current design's most confusing aspect is that grants and
restrictions are *different algebras*. A unified capability algebra — grants
computed per §7.1, attenuated by tokens using the *same* representation —
would delete an entire subsystem's worth of special cases (§5.4).

### Comparison

| | Current (SQL-per-request) | §7.1 Grants ledger | §7.2 Policy objects | §7.3 Capabilities |
|---|---|---|---|---|
| List 10k tables | Seconds / times out (today) | ms, indexed | Same as current unless compiled to ledger | ms, pattern match |
| Point check | ms→tens of ms, scales with rules | µs | ms | µs |
| Plugin flexibility | Maximal (arbitrary SQL) | Identity + grant writes; SQL escape hatch | Declarative matchers | Login-time resolution |
| Admin audit | Execute-and-inspect only | **Policy is a diffable table** | Statically analyzable | Read the token |
| Revocation | Immediate | Immediate (invalidation must be airtight) | Immediate | Token lifetime |
| Migration cost | — | High (shim possible) | Moderate | High |

---

## 8. Suggested sequencing

1. **Now (bugfix, no API change):** §4.3 param binding, §4.4 attribution, §4.5
   anon-rewrite regex, §4.6 nits; graceful failure for interrupted permission
   queries; parity property test (will initially fail on §4.2).
2. **Pre-1.0 (contract-affecting):** R1 single compiler (fixes §4.2), R2
   data-first rules + auto-namespacing, R5 restriction unification, R4 trust
   model decision — these change what plugins are promised, so they must land
   before the 1.0 freeze.
3. **Performance:** R3 query shape + temp-table rules + CI benchmark. Success
   criterion: 2,000-table homepage under 100ms; point check under 2ms at 500
   rules.
4. **Audit surface:** R6 explain endpoint + CLI, R7 docs pass.
5. **Post-1.0 exploration:** prototype §7.1 (optionally expressed via §7.2
   policy objects) as a plugin first — the hook architecture is flexible
   enough to host its own successor, which is itself a good sign about the
   hook architecture.

---

## Appendix A: benchmark detail

Setup: one SQLite database with 2,000 tables (`t00000`…`t01999`), default
settings, in-memory internal database, config granting `allow: {id: alice}` on
the first N tables. Times are steady-state (after warm-up) on this review
container; absolute numbers will vary but the *shape* (linear SQL-text growth,
O(N×R) joins, time-limit interrupt) is structural.

| Config rules | First page (1,000) `view-table` | + `include_is_private` | Point check |
|---:|---:|---|---:|
| 0 | 839 ms | `QueryInterrupted` | 0.6 ms |
| 50 | 875 ms | `QueryInterrupted` | 3.9 ms |
| 200 | 934 ms | `QueryInterrupted` | 75.9 ms |
| 1,000 | `QueryInterrupted` | — | — |

`GET /` (which calls `allowed_resources("view-table", include_is_private=True)`):
HTTP 500 in 1.2s at every tested rule count.

## Appendix B: reproduction scripts

**B.1 — `also_requires` divergence (§4.2):** register a plugin returning
`PermissionSQL.deny()` for `view-database` and `PermissionSQL.allow()` for
`store-query`/`execute-sql`; compare
`await ds.allowed(action="store-query", resource=DatabaseResource("_memory"), actor={"id":"bob"})`
(→ `False`) with
`await ds.allowed_resources("store-query", {"id":"bob"})` (→ contains `_memory`).

**B.2 — unbound automatic params (§4.3):** run `Datasette(memory=True,
default_deny=True)` with a plugin returning
`PermissionSQL(sql="SELECT NULL, NULL, CASE WHEN :actor_id='alice' THEN 1 ELSE 0 END, 'r'")`
(no `params`); any `allowed_resources("view-table", {"id":"alice"})` call
raises `ProgrammingError: You did not supply a value for binding parameter :actor_id`.
Remove `default_deny` and it "works" because core's default rules smuggle the
binding in.

**B.3 — source misattribution (§4.4):** with the B.2 plugin registered under
name `no_params_plugin`, inspect
`(await ds.allowed_resources_sql(action="view-table", actor={"id":"alice"})).sql`
— the plugin's SELECT appears tagged
`'datasette.default_permissions' AS source_plugin`.

**B.4 — performance (§4.1, Appendix A):** create 2,000 tables, start
`Datasette(["bench.db"])`, `await ds.client.get("/")` → HTTP 500 with
`QueryInterrupted` from `views/index.py:41`.
