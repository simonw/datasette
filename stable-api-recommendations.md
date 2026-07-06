# Datasette 1.0 Stable API — Consistency and Completeness Review

This review is based on `existing-api.md`, which documents the JSON API as
actually implemented in this codebase (`1.0a35`), derived from source. The
goal here is to identify everything that should be made consistent, fixed, or
explicitly scoped out **before** the 1.0 stability promise takes effect —
because after 1.0, every inconsistency below becomes a compatibility
commitment.

Findings are grouped by theme. Each carries a priority:

- **P1 — should block 1.0**: breaking to fix later, or a correctness/security
  concern.
- **P2 — strongly recommended**: fixable later only via awkward additive
  changes.
- **P3 — nice to have / documentation decision**: can be resolved by
  documenting the behavior as intentional.

---

## 1. Error responses: four shapes is three too many (P1) — ✅ IMPLEMENTED

> **Status:** implemented. All four shapes now delegate to a shared
> `error_body()` helper (`datasette/utils/__init__.py`) producing
> `{"ok": false, "error": "<joined>", "errors": [...], "status": <int>}`.
> The `title` key is no longer emitted in JSON; the bare `{"error": ...}`
> debug-endpoint shape is gone; `_shape=object` misuse now returns HTTP 400
> (part of §1b). Covered by `tests/test_error_shape.py` and documented in
> the "Error responses" section of `docs/json_api.rst`. §1a (`Forbidden` →
> JSON) and §1b (write canned-query 200) are now also implemented. Still
> open from this section's sub-items: the §1c status outliers.

The API currently produces four distinct JSON error shapes depending on which
internal layer generates the error:

| Shape | Producer | Example endpoints |
|---|---|---|
| `{"ok": false, "error", "status", "title"}` | exception handler (handle_exception.py:50-53) | 404s and `DatasetteError`s on any `.json` path |
| `{"ok": false, "errors": [...]}` | `_error()` helper (views/base.py:183-184) | all write endpoints, stored-query endpoints, execute-write |
| `{"ok": false, "error", "rows": [], "truncated": false}` | JSON renderer (renderer.py:52-56) | SQL errors on table/query reads |
| `{"error": "..."}` (no `ok`) | permission debug views (views/special.py) | `/-/allowed`, `/-/rules`, `/-/check`, POST `/-/permissions` |

Additionally, write canned queries report failure via a **fifth** vocabulary:
`{"ok": false, "message": ..., "redirect": ...}` with HTTP **200**
(views/database.py:678-690).

A 1.0 client cannot write a single error handler today. **Recommendation:**
pick one canonical error object — the singular/plural tension is easiest to
resolve as:

```json
{"ok": false, "error": "human-readable summary", "errors": ["detail", "..."], "status": 400}
```

where `errors` is optional and `error` is always present — and route every
error path through it (including the `forbidden` and `handle_exception`
defaults). At minimum, eliminate the bare `{"error": ...}` shape and the
`status`/`title` keys nobody else emits (`title` is a template-rendering
concern that leaked into the API).

### 1a. `Forbidden` returns an HTML 403 to JSON clients (P1) — ✅ IMPLEMENTED

> **Status:** implemented — the default `forbidden()` hook now returns the
> canonical JSON error for requests whose path ends in `.json` or that send
> `Accept: application/json` / `Content-Type: application/json`.

Read endpoints that deny access via `ensure_permission`/`check_visibility`
raise `Forbidden`, and the default `forbidden()` hook renders an **HTML error
page even for `.json` requests** (forbidden.py:4-19, app.py:2895-2904). So:

- `GET /db/table.json` without `view-table` → 403 **HTML**
- `POST /db/table/-/insert` without `insert-row` → 403 **JSON**

A JSON client gets unparseable output precisely when it most needs a
machine-readable answer. **Recommendation:** the default forbidden handler
must return the canonical JSON error when the path ends in `.json` or the
request prefers JSON, mirroring `handle_exception`.

### 1b. Errors that return HTTP 200 (P1) — ✅ IMPLEMENTED

> **Status:** implemented. `_shape=object` misuse returns 400 (done with
> §1), and write canned-query SQL failures now return **400** with the
> canonical error shape (plus the `redirect` context key); the
> `QueryWriteRejected` 403 branch also uses the canonical shape.

- `_shape=object` on a query or pk-less table → `{"ok": false, "error":
  "_shape=object is only available on tables"}` with **200**
  (renderer.py:73-90), while an unknown `_shape` value returns **400**
  (renderer.py:101-108). Same class of error, different status.
- Write canned-query SQL failure → **200** `{"ok": false, "message": ...}`
  (views/database.py:683-690), while the equivalent failure on
  `/-/execute-write` returns **400**.

**Recommendation:** all `ok: false` responses should carry a 4xx/5xx status.
(`/-/execute-write/analyze` returning `ok: false` with 200 for "analysis
completed, SQL is invalid" is defensible but should then not reuse the `ok`
key — see §2.)

### 1c. Wrong-status outliers (P2) — ✅ IMPLEMENTED

- ~~Row **delete** write failures return **500** (views/row.py:757) while row
  **update** write failures return **400** (views/row.py:832-835). Same
  failure class, different status; pick 400 (or 409 for constraint
  violations) for both.~~ ✅ **Done** — delete now returns 400, matching
  update and the rest of the write API.
- ~~Invalid or expired bearer tokens silently degrade the request to anonymous,
  so clients see a 403 permission error (or worse, anonymous-permitted data)
  rather than a 401 (tokens.py:147-193). For 1.0, a malformed/expired
  `Authorization: Bearer dstok_...` header should produce **401** with a
  distinguishable error, so clients can tell "renew your token" apart from
  "you lack permission".~~ ✅ **Done** — token handlers can raise
  `TokenInvalid`; Datasette responds 401 with the canonical body and a
  `WWW-Authenticate: Bearer error="invalid_token"` header. Unrecognized
  token prefixes still fall through to anonymous so auth plugins keep
  working.

---

## 2. Success envelope: `ok` is not universal, arrays are not extensible (P1/P2) — ✅ IMPLEMENTED (§2a-2c open)

> **Status:** recommendations 1-3 are implemented. Every JSON-object
> success response now includes `"ok": true` (`JsonDataView` injects it for
> dict responses; homepage, jump, schema, permission-debug and autocomplete
> views set it explicitly), and the three top-level-array endpoints now
> return objects: `/-/plugins` → `{"ok": true, "plugins": [...]}`,
> `/-/databases` → `{"ok": true, "databases": [...]}`, `/-/actions` →
> `{"ok": true, "actions": [...]}`. Covered by
> `tests/test_success_envelope.py`. The sub-findings §2a (collection
> representations), §2b (`_extra`/`_shape` coverage) and §2c (count
> truncation) remain open.

Endpoints disagree about the success envelope:

- **Have `ok: true`:** table/row/query reads, database view, all write
  endpoints, stored-query endpoints, `/-/allowed`-style debug data.
- **No `ok` key:** `/-/versions`, `/-/settings`, `/-/config`, `/-/threads`,
  `/-/actor`, `/-/jump`, `/-/schema` variants (`{"database", "schema"}`,
  `{"schemas": [...]}`), table `/-/schema.json`, `/-/autocomplete`
  (`{"rows": []}`), homepage `/.json`.
- **Top-level JSON arrays:** `/-/plugins`, `/-/databases`, `/-/actions`
  (app.py:2247-2304). A top-level array can never grow a sibling key
  (pagination, warnings, `ok`) without a breaking change.

**Recommendations:**

1. (P1) Wrap the three array endpoints in objects before 1.0:
   `{"ok": true, "plugins": [...]}` etc. This is the single cheapest
   future-proofing fix in this list.
2. (P2) Add `ok: true` to every JSON-object success response, or explicitly
   document that `ok` only exists on data endpoints. Half-consistency is the
   worst outcome.
3. (P2) `/db/-/schema.json` (`{"database", "schema"}`) and
   `/db/table/-/schema.json` should match the envelope style of their sibling
   endpoints (they are also the only data endpoints whose 404 uses the
   exception shape but whose success has no `ok`).

### 2a. Collection representations disagree (P2)

- ~~Homepage `/.json` returns `databases` as an **object keyed by name**
  (index.py:147-161); `/-/databases.json` returns an **array**; the database
  page returns `tables` as an array. Choose arrays-of-objects everywhere
  (objects-keyed-by-name break when names need ordering or pagination).~~
  ✅ **Done** — the homepage returns a list, matching `/-/databases.json`.
  The homepage JSON remains deliberately undocumented.
- ~~Insert/upsert with `return: true` respond with `rows` (plural, list); row
  update with `return: true` responds with `row` (singular, object)
  (views/row.py:837-844). Pick one (`rows` everywhere, even for one row,
  matches the read API).~~ ✅ **Done** — row update now returns
  `rows: [{...}]`.

### 2b. `_extra`/`_shape` support is uneven (P2) — partially implemented

> **Status:** unknown `_extra` names on data formats now return 400
> `Unknown _extra: <names>` (HTML pages still ignore them). Extending
> extras/shaping to database/instance scope remains open.

The extras system (`?_extra=`, scope-registered) is the 1.0 mechanism for
response shaping — but it only exists on table, row and query endpoints. The
database view builds JSON by hand and supports **neither `_extra` nor
`_shape`** (views/database.py:189-212); the homepage likewise. Either extend
extras to database/instance scope before 1.0 or document clearly that shaping
is a table/row/query feature. Also decide the contract for **unknown
`_extra` names, which are currently silently ignored** (extras.py:116-122) —
silent ignoring means typos return the default payload with no signal;
recommend a 400 or a `warnings` key.

### 2c. Count truncation is invisible in JSON (P2) — ✅ IMPLEMENTED

> **Status:** implemented — a public `count_truncated` extra now exists and
> is implicitly included whenever `count` is requested.

The `count` extra is computed with a `limit 10001` subquery, so `count:
10001` actually means "at least 10001" — the `count_truncated` flag exists
but only in the HTML template context, never in JSON (views/table.py:
2334-2337). Expose it (e.g. make `count` be `null` + add `count_estimate`,
or add `count_truncated` to the JSON) before clients start trusting the
number.

---

## 3. Pagination: three mechanisms, two contracts (P2) — partially implemented

> **Status:** `next_url` now accompanies `next` in the default table JSON
> keys (previously it required `?_extra=next_url`), so every response with
> a `next` token also carries the ready-to-follow URL. Pagination tokens
> are deliberately left undocumented as to their internal structure.
> `_size` is now the single page-size parameter with uniform table-style
> semantics everywhere: query lists accept `max` and 400 on out-of-range
> values (previously silently clamped), and the `/-/allowed` and
> `/-/rules` debug endpoints renamed `page`/`page_size` to
> `_page`/`_size` with the same validation (400 instead of silent
> capping at 200). `has_more` has been **removed** from the query-list
> JSON — `next: null` is the single end-of-results signal everywhere,
> keeping default response keys minimal (`total` remains a debug-endpoint
> nicety). Fixing this also uncovered and fixed a bug where the query
> list's JSON `next_url` pointed at the HTML page (it dropped the `.json`
> extension) and was relative where the table `next_url` is absolute.
> §3 is now fully resolved.

| Endpoint | Mechanism | Token | Extras |
|---|---|---|---|
| Table `.json` | keyset | tilde-encoded pk/sort values in `_next` | `next` always in body, `next_url` via `_extra`, `Link: rel=next` header |
| SQL view `.json` | **offset** | integer in the same `_next` parameter | same envelope |
| `/-/queries` lists | keyset | cursor in `_next` | `next`, `next_url`, **`has_more`** in body |
| `/-/allowed`, `/-/rules` | **page numbers** | `page`/`page_size` | `total`, `next_url`, `previous_url` |

Concerns:

1. The same `_next` parameter means "start after key" on tables but "row
   offset" on views. Offset pagination over views is also O(n) and skews
   under concurrent writes. If unifiable, unify; if not, document loudly.
2. `has_more` exists on query lists but not table pages; `total` exists on
   debug endpoints but not elsewhere. Standardize the pagination block
   (suggest: `next`, `next_url` — nullable — everywhere; treat `has_more` as
   `next != null`).
3. Page-size parameters: `_size` (default 100, `max` keyword allowed) on
   tables; `_size` (default 50 JSON, clamped 1–1000, no `max` keyword) on
   query lists; `page_size` (default 50, silently capped at 200) on debug
   endpoints. Align names, defaults and the cap behavior (silent capping vs
   400) as far as practical.

---

## 4. HTTP semantics (P2)

- ~~**201 vs 200:** insert → 201, upsert → 200 (views/table.py:1194), create
  table → 201, store query → 201. Insert-201/upsert-200 is defensible
  (upsert may not create) but it is undocumented subtlety; state it, or
  return 200 for both with an explicit `created` count.~~ ✅ **Done** —
  documented as deliberate in the upsert docs.
- **Destructive-action confirmation is asymmetric:** table drop requires
  `{"confirm": true}` and has a preview response (views/table.py:1346-1365);
  row delete executes immediately and ignores the body; query delete
  executes immediately. Decide the 1.0 rule (suggestion: confirmation only
  for schema-destroying operations, i.e. keep as is — but document it as a
  deliberate contract).
- **Content-type enforcement is inconsistent:** `/-/insert`, `/-/upsert`,
  `/-/alter`, `/-/set-column-type` demand `Content-Type: application/json`
  (400 otherwise); `/-/create` parses the body as JSON regardless of
  content type; execute-write and the query CRUD endpoints accept both JSON
  and form encodings. Pick one rule for JSON-only endpoints.
- **JSON-vs-HTML negotiation on POST differs per endpoint:** execute-write
  and canned queries key off `Accept: application/json` / a `_json` body
  field; the write API keys off nothing (always JSON); query store keys off
  request content type. A single documented rule ("responses are JSON if the
  request body was JSON or `Accept: application/json`") would cover all of
  them.
- **Endpoints named like actions but served over GET:**
  `/-/queries/analyze`, `/-/execute-write/analyze`,
  `/-/foreign-key-suggestions`, `/-/query/parameters` are all GET (correct,
  they are reads) — fine, but `analyze` under a POST-shaped path invites
  wrong calls; make sure 405 responses for POST on these return the JSON 405
  shape (they do only when the path ends `.json` or content type is JSON —
  a JSON POST to `/-/queries/analyze` gets JSON, a form POST gets text).

---

## 5. Naming and parameter conventions (P2/P3)

- ~~**`params` and `parameters` are duplicate keys** in every stored-query
  object (stored_queries.py:55-80). Delete one before 1.0 (suggest keeping
  `parameters`; the write side already accepts both on input).~~
  ✅ **Done** — output objects carry only `parameters` (matching
  `/-/query/parameters` and the analyze endpoints); `params` remains an
  accepted input alias for API creation and `datasette.yaml` config.
- **Three names for the same concept across error/message payloads:**
  `error`, `errors`, `message`. See §1.
- ~~**Boolean query parameters have at least three grammars:** `_nl=on`,
  `_labels=on/off`, `?all=1`, `is_write=1|0|true|false|t|f|yes|no|on|off`,
  `_nocount=1`. Adopt one accepted set (the query-list parser at
  query_helpers.py:81-94 is a good candidate) and apply it everywhere.~~
  ✅ **Documented** — the JSON API docs state the canonical grammar
  (`on/true/1`, `off/false/0`), which `value_as_boolean` already accepts
  everywhere it is used.
- ~~**`.jsono`** survives on the homepage route (identical output to `.json`)
  and as a row-view redirect. Remove it at 1.0; it is pure legacy.~~
  ✅ Removed: the homepage routes only accept `.json` and the row-view
  redirect is gone.
- **`_json` is overloaded:** on GET it is a renderer option naming a column
  to parse as JSON (repeatable); on canned-query POST a `_json` body field
  forces a JSON response. Two unrelated meanings for one name.
- The reserved `/-/` namespace is applied consistently across routes — this
  is in good shape. The one gap: table names matching `^-$`-adjacent shapes
  are protected by tilde-encoding; keep a test asserting `/-/` can never be
  shadowed by user data.

---

## 6. Permissions and security consistency (P1/P2)

- ~~**(P1) `/-/databases.json` ignores per-database permissions** — it lists
  every attached database (name, path on disk, size) to any actor holding
  `view-instance` (app.py:2157-2169), while the homepage and every other
  endpoint filter by `view-database`. On a public instance with private
  databases this leaks filesystem paths and database names. Filter it, or
  gate it behind `permissions-debug`.~~ ✅ **Done** — the endpoint now
  filters through `allowed_resources("view-database", actor)`.
- ~~**(P2) `/db/-/schema` checks existence before permission**
  (views/special.py:1308-1317): an actor without `view-database` can
  distinguish "database exists" (403) from "does not exist" (404).
  Standardize on permission-check-first (as the table view does) so
  unauthorized actors get a uniform response.~~ ✅ **Done** — permission is
  checked first; the table schema view also now 404s (instead of a 500
  KeyError) for an unknown database.
- ~~**(P2) `/-/threads` exposes runtime internals** (thread idents, asyncio
  task reprs including file paths) behind only `view-instance`. Consider
  `permissions-debug`, alongside `/-/actions` which already requires it.~~
  ✅ **Done** — `/-/threads` now requires `permissions-debug`.
- ~~**(P3) `/-/config` redaction is substring-based** on six key names
  (app.py:2502-2505); plugins storing secrets under other names leak. Worth
  a note in plugin authoring docs plus a `redact_keys` plugin hook.~~
  ✅ **Documented** — the plugin secrets docs now advise naming keys to
  match the redaction substrings (a `redact_keys` hook remains a possible
  future addition).
- **(P3) Database-level checks on `/-/create`** (insert-row/update-row
  checked against `DatabaseResource`, not the about-to-exist table —
  table_create_alter.py:819-856) vs table-level checks on `/-/insert`.
  Correct by necessity, but document that a token restricted to
  table-level `ir` cannot use `/-/create` with rows.

---

## 7. Completeness gaps for a 1.0 JSON API (P2/P3)

1. **(P2) No JSON API to create tokens.** `/-/create-token` is an HTML form
   only (`has_json_alternate = False`, form-encoded POST). Any automation
   that wants to mint scoped tokens must shell out to `datasette
   create-token`. An intentional JSON mode (actor-authenticated, same
   restriction vocabulary) rounds out the write API story — or explicitly
   document token minting as CLI/Python-only.
2. **(P2) Row JSON cannot expand foreign-key labels.** `_labels` works on
   table JSON but is silently ignored on row JSON (views/row.py:445-475
   expands only for HTML). Either support it or return 400 for unsupported
   parameters; silent ignoring is the worst option (see also §2b on unknown
   `_extra` values).
3. **(P2) No machine-readable "which write features does this instance/table
   support" endpoint.** Clients must probe (`/-/insert` on an immutable
   database → 403). The API explorer computes exactly this data for HTML
   (views/special.py:863-990); exposing it as JSON would let clients degrade
   gracefully. (`/-/allowed.json` covers the permission half already.)
4. **(P3) Table list pagination.** `/db.json` inlines all tables (with
   counts) and the homepage truncates to 5 per database; a 10,000-table
   database has no paginated table listing. Acceptable for 1.0 if
   documented; the internal catalog tables would support a real endpoint
   later.
5. **(P3) `Link: rel=next` header** exists on table JSON only. Harmless, but
   either add it to the other paginated endpoints or drop it from the
   contract (`Access-Control-Expose-Headers: Link` suggests it is meant to
   be part of the API).

---

## 8. Behavior that looks like a bug and should be resolved before freezing

1. ~~**Trusted queries: update is blocked, delete is not.**
   `QueryUpdateView` rejects `is_trusted` queries with 403
   (stored_queries.py:426-427) but `QueryDeleteView.post` never checks
   `is_trusted` — an actor with `delete-query` can delete a config-defined
   trusted query via the API (it will resync on restart, making the
   behavior confusing rather than catastrophic). Align delete with update.~~
   ✅ **Done** — both the POST endpoint and the HTML confirmation page now
   return 403 `"Trusted queries cannot be deleted using the API"`;
   `datasette.remove_query()` remains available for internal use.
2. ~~**GET `/db/-/query` with no `?sql=` returns 200 `{"ok": true, "rows":
   []}`** while `.csv` on the same request returns 400 `"?sql= is
   required"`. The JSON behavior masks caller bugs; return 400 on both.~~
   ✅ **Done** — all data formats now return 400; the HTML SQL editor page
   is unchanged.
3. **`_shape=object` HTTP 200 error** (§1b) — almost certainly unintended.
4. ~~**Row delete 500** (§1c) — inconsistent with every sibling endpoint.~~
   ✅ Done — now 400.
5. **The "SQL Interrupted" error embeds an HTML fragment in the JSON `error`
   value** (views/database.py:805-820). Error strings in the JSON API should
   be plain text.

---

## 9. Define stability tiers explicitly (P1 — documentation, not code) — ✅ IMPLEMENTED

> **Status:** implemented. Undocumented JSON endpoints self-describe with
> an `"unstable"` marker key, and `docs/json_api.rst` now opens with an
> "API stability" section (`json_api_stability`) declaring the 1.x
> promise: documented endpoints/keys are stable with additive-only
> changes, pagination tokens are opaque, the error format and token
> restriction semantics are stable, and the exempt tiers (marker-key
> endpoints, debug/support endpoints, explicitly-unstable keys) are
> listed. Cross-referenced from the introspection and permission-debug
> docs.

Not everything under `/-/` can or should carry a 1.0 guarantee. Recommend
shipping 1.0 with an explicit three-tier contract, per endpoint:

- **Stable (semver-protected):** table/row/query reads (`.json`, `_shape`,
  `_extra` public names, filters, pagination tokens as opaque strings), the
  write API (`/-/insert`, `/-/upsert`, `/-/alter`, `/-/drop`,
  `/-/set-column-type`, row `/-/update`, `/-/delete`, `/-/create`,
  `/-/execute-write`), stored-query CRUD + execution, `/-/versions`,
  `/-/plugins`, `/-/settings`, `/-/actor`, `/-/databases`, schema endpoints,
  token format & restriction semantics (`_r` abbreviations are wire format
  now — they are stored inside issued tokens and cannot change silently).
- **Unstable/debug (documented as exempt):** `/-/threads`, `/-/actions`,
  `/-/permissions`, `/-/allowed`, `/-/rules`, `/-/check`, `/-/messages`,
  `/-/allow-debug`, `/-/patterns`, `/-/debug/autocomplete`, the `debug` and
  `request` extras (the `debug` extra already self-describes as unstable),
  `/-/api` and `/-/jump` (UI support endpoints), `/-/autocomplete` and
  `/-/fragment` (UI support), `/-/foreign-key-suggestions` and
  `/-/foreign-key-targets` (heuristic outputs).
- **Internal:** anything HTML-only (`/-/edit`, `/-/create-token`,
  `/-/logout`, `/-/auth-token`).

Two details make tiering urgent rather than optional:

- **Extras are enumerable by clients** (`?_extra=extras` self-describes the
  registry), so every public extra name is de-facto API. Mark each extra
  stable or unstable in its class definition and surface that in the
  `extras` output.
- **Pagination tokens leak implementation** (tilde-encoded pk values for
  tables, plain integers for views). Declare them opaque now so the view
  token can become keyset later without a "breaking" change.

---

## 10. Summary of P1 items (the pre-1.0 checklist)

1. ~~One canonical JSON error shape; retire the other three (§1).~~ ✅ Done.
2. ~~`Forbidden` → JSON 403 for JSON requests (§1a).~~ ✅ Done.
3. ~~No `ok: false` with HTTP 200 (§1b: `_shape=object`, write canned-query
   SQL errors).~~ ✅ Done.
4. ~~Wrap `/-/plugins`, `/-/databases`, `/-/actions` top-level arrays in
   objects (§2).~~ ✅ Done.
5. ~~Filter `/-/databases.json` by `view-database` or gate it behind
   `permissions-debug` (§6).~~ ✅ Done.
6. ~~401 (not silent-anonymous) for invalid/expired bearer tokens (§1c).~~
   ✅ Done.
7. ~~Publish explicit stability tiers, including extras and pagination-token
   opacity (§9).~~ ✅ Done.
8. Resolve the looks-like-a-bug list (§8), especially ~~trusted-query delete
   and row-delete 500~~ (both done).

Everything in P2 is worth doing now because each item is breaking-to-fix
later; each P3 can be resolved by a sentence of documentation declaring the
current behavior intentional.
