# Datasette JSON API — As Implemented

This document describes the JSON API of this Datasette codebase (version `1.0a35`) as
derived directly from the source code. It intentionally ignores the existing `docs/`
directory: every claim below is based on the route table in `datasette/app.py`
(`Datasette._routes()`, app.py:2507-2767) and the view implementations in
`datasette/views/`.

## Contents

- [Cross-cutting behavior](#cross-cutting-behavior)
- [Instance endpoints](#instance-endpoints)
- [Database endpoints](#database-endpoints)
- [Table and row read endpoints](#table-and-row-read-endpoints)
- [The write API](#the-write-api)
- [Stored (canned) queries API](#stored-canned-queries-api)
- [Authentication and tokens](#authentication-and-tokens)
- [Appendix: registered actions (permissions)](#appendix-registered-actions-permissions)

---

## Cross-cutting behavior

### URL formats and content negotiation

- Most read endpoints are registered with an optional format suffix:
  `/(...)(\.(?P<format>json))?$`. The bare path returns HTML; the `.json`
  extension returns JSON.
- Table, row and query routes accept any `\w+` format extension; formats other
  than the built-in `html`, `json`, `csv`, `blob` must be provided by a plugin
  via `register_output_renderer`, otherwise the request 404s.
- HTML responses include a `Link: <...>; rel="alternate";
  type="application/json+datasette"` header pointing at the `.json` variant
  (views/base.py:141-159), unless the view opts out with
  `has_json_alternate = False`.
- Database, table, row and query names in paths are **tilde-encoded**
  (a percent-encoding variant using `~` as the escape character;
  utils/__init__.py `_TILDE_ENCODING_SAFE`). Multi-column primary keys in row
  URLs are comma-separated.
- JSON responses are always compact `json.dumps` output serialized by
  `CustomJSONEncoder`; there is no pretty-printing query parameter. Binary
  values are serialized as `{"$base64": true, "encoded": "..."}`.
- Success content type: `application/json; charset=utf-8`
  (`_shape=array&_nl=on` responses use `text/plain`).

### Success envelope

Every JSON endpoint that returns an object includes `"ok": true` on
success. `JsonDataView` injects it automatically for dict responses
(views/special.py); the homepage, jump, schema, permission-debug and
autocomplete views add it explicitly. The remaining top-level-array
endpoints (`/-/plugins`, `/-/databases`, `/-/actions`) are being converted
to objects.

### Error shape (canonical)

Every JSON error response uses one canonical shape, built by `error_body()`
(utils/__init__.py):

```json
{
  "ok": false,
  "error": "all messages joined with '; '",
  "errors": ["message", "..."],
  "status": 404
}
```

- `errors` is a list of one or more message strings (multi-message
  validation errors, e.g. per-row insert errors, list them all).
- `error` is the messages joined with `"; "`.
- `status` always matches the HTTP status code.

The shape is produced by four code paths, all delegating to `error_body()`:

1. **Exception handler** (handle_exception.py) — `NotFound`,
   `DatasetteError`, `BadRequest` etc. on `.json` paths. `DatasetteError`
   `error_dict` context keys are merged in; the legacy `title` key is no
   longer emitted in JSON (it survives in the HTML error template context).
2. **The `_error()` helper** (views/base.py:183-184) — the write API,
   stored-query API, execute-write and permission-denied paths.
3. **JSON renderer errors** (renderer.py) — SQL errors on table/query
   endpoints return HTTP 400 with the canonical keys **plus** the context
   keys of the response it could not produce:

   ```json
   {"ok": false, "error": "no such table: x", "errors": ["no such table: x"],
    "status": 400, "rows": [], "truncated": false}
   ```

   Invalid `_shape=` values and `_shape=object` misuse (on queries or
   pk-less tables) also return canonical 400 errors.
4. **Permission debug endpoints** (`/-/allowed`, `/-/rules`, `/-/check`,
   POST `/-/permissions`) — canonical shape (previously bare
   `{"error": ...}` objects).

Method-not-allowed responses return HTTP 405 with the canonical shape when
the path ends in `.json` or the request content type is `application/json`;
plain text otherwise (views/base.py).

**`Forbidden` is special:** when a view raises `Forbidden` (e.g. via
`ensure_permission`), the default `forbidden()` plugin hook renders an **HTML
error page with status 403 even for `.json` requests**
(forbidden.py:4-19, app.py:2895-2904). Endpoints that check permissions
themselves and return `_error(..., 403)` produce JSON instead. So a JSON
client may receive either an HTML 403 page or a JSON 403 body depending on
the endpoint.

### CORS

When Datasette is started with `--cors`, responses gain
(utils/__init__.py:1297-1302):

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Headers: Authorization, Content-Type
Access-Control-Expose-Headers: Link
Access-Control-Allow-Methods: GET, POST, HEAD, OPTIONS
Access-Control-Max-Age: 3600
```

### CSRF / cross-origin protection

Datasette uses header-based cross-origin protection
(`CrossOriginProtectionMiddleware`, csrf.py:67-178) rather than CSRF tokens
for API calls. For non-GET/HEAD/OPTIONS requests:

1. Requests carrying `Authorization: Bearer ...` **and no `Cookie` header**
   bypass the check entirely (csrf.py:98-110).
2. Otherwise `Sec-Fetch-Site` must be `same-origin` or `none`; other values → 403.
3. If neither `Sec-Fetch-Site` nor `Origin` is present (curl, API clients),
   the request passes.
4. Fallback: `Origin` must exactly match the request scheme/host/port → else 403.

Plain JSON API clients (no cookies, no browser headers) are never blocked;
`Content-Type: application/json` itself plays no role in the CSRF decision.

### Settings that govern the API

From `SETTINGS` (app.py:197-287): `default_page_size` (100),
`max_returned_rows` (1000), `max_insert_rows` (100), `sql_time_limit_ms`
(1000), `default_facet_size` (30), `facet_time_limit_ms` (200),
`allow_facet` (true), `allow_download` (true), `allow_signed_tokens` (true),
`default_allow_sql` (true), `max_signed_tokens_ttl` (0), `default_cache_ttl`
(5), `allow_csv_stream` (true), `max_csv_mb` (100), `force_https_urls`
(false), `trace_debug` (false), `base_url` ("/").

### The JSON renderer: `_shape`, `_nl`, `_json`, `_json_infinity`

`json_renderer` (renderer.py:31-126) processes `.json` output for table, row
and query views (but **not** for the instance/database/debug endpoints, which
build JSON directly):

- **`_shape`** (default `objects`):
  - `objects` — `{"ok": true, "rows": [{col: val}, ...], "truncated": false, ...}`
  - `arrays` — same envelope, each row a list of values
  - `array` — response body is a bare JSON array of row objects
  - `arrayfirst` — bare JSON array of the first column's values
  - `object` — table views only: an object keyed by primary-key string.
    On queries or tables without primary keys: a canonical 400 error
    (`_shape=object is only available on tables` /
    `_shape=object not available for tables with no primary keys`).
  - anything else — canonical HTTP 400 error `Invalid _shape: x`
- **`_nl=on`** — with `_shape=array` only: newline-delimited JSON, `text/plain`.
- **`_json=COLUMN`** (repeatable) — parse that column's string values with
  `json.loads` so they nest as JSON; parse failures leave the value unchanged.
- **`_json_infinity=1`** — preserve `Infinity`/`-Infinity`; by default they
  are replaced with `null`.
- `columns` is stripped from dict-shaped output unless `?_extra=columns` was
  requested (renderer.py:110-113).
- If a SQL error occurred, `_shape` is ignored, HTTP status is 400 and the
  envelope carries the canonical error keys alongside `rows`/`truncated`.

### The `?_extra=` system

Table, row and query JSON responses support `?_extra=` (repeatable and/or
comma-separated, extras.py:9-14) to add keys to the response. Extras are
scope-registered (`ExtraScope.TABLE` / `ROW` / `QUERY`) and only **public**
extras are available over JSON (extras.py:73-92). Unknown extra names are
silently ignored. The available names per scope are listed with the relevant
endpoints below.

---

## Instance endpoints

Most of these are implemented with `JsonDataView` (views/special.py:30-79):
GET-only; bare path renders an HTML page (`show_json.html`), `.json` returns
the data; permission defaults to `view-instance` and denial raises
`Forbidden` → **HTML** 403 page.

### GET /

Routes: `/(\.(?P<format>jsono?))?$` and `/-/(\.(?P<format>jsono?))?$`
(app.py:2517-2518); `/-` permanently redirects to `/-/`. `IndexView`
(views/index.py:22-189). `GET /.json`, `/.jsono` and `/-/.json` return JSON.

- **Permission:** `view-instance` (denied → 403). Databases and tables are
  further filtered by `view-database` / `view-table` for the actor.
- **Parameters:** `_sort=relationships` sorts each database's truncated table
  list by foreign-key relationship count.
- **JSON response** (index.py:147-161) — includes `ok: true` plus:
  - `databases` — an **object keyed by database name** (not a list). Each
    value: `name`, `hash` (or null), `color`, `path`,
    `tables_and_views_truncated` (up to 5 items: `name`, `columns`,
    `primary_keys`, `count` (int or null), `hidden`, `fts_table`,
    `num_relationships_for_sorting`, `private`; view items are just
    `{"name", "private"}`), `tables_and_views_more` (bool), `tables_count`,
    `table_rows_sum`, `show_table_row_counts`, `hidden_table_rows_sum`,
    `hidden_tables_count`, `views_count`, `private`.
  - `metadata` — instance metadata object.

### GET /-/versions(.json)

`JsonDataView` over `Datasette._versions` (app.py:2548-2551, 2171-2245).
Permission `view-instance`. No parameters.

Response keys: `python` (`{version, full}`), `datasette` (`{version}` plus
optional `note`), `asgi` (`"3.0"`), `uvicorn` (string or null), `sqlite`
(`{version, fts_versions, extensions, compile_options}`; `extensions`
includes `json1` and optionally `spatialite`), `pysqlite3` (only when
running under pysqlite3).

### GET /-/plugins(.json)

app.py:2552-2557, `Datasette._plugins` (app.py:2247-2266). Permission
`view-instance`.

- **Parameters:** `?all=1` — include Datasette's built-in default plugins
  (filtered out by default).
- **Response:** a JSON **array**, sorted by name, of
  `{"name", "static", "templates", "version", "hooks"}`.

### GET /-/settings(.json)

app.py:2558-2561. Permission `view-instance`. No parameters. Returns a flat
object mapping every setting name (see [Settings](#settings-that-govern-the-api))
to its effective value.

### GET /-/config(.json)

app.py:2562-2565. Permission `view-instance`. No parameters. Returns the full
`datasette.yaml` configuration dict passed through
`redact_keys(config, ("secret", "key", "password", "token", "hash", "dsn"))`
(app.py:2502-2505) — any dict key containing one of those substrings has its
value replaced by `"***"` (utils/__init__.py:1532-1556).

### GET /-/threads(.json)

app.py:2566-2569, `Datasette._threads` (app.py:2268-2285). Permission
`view-instance`. No parameters.

Response: `num_threads`, `threads` (list of `{name, ident, daemon}`),
`num_tasks`, `tasks` (asyncio task repr strings). When the
`num_sql_threads` setting is 0 the response is exactly
`{"num_threads": 0, "threads": []}`.

### GET /-/databases(.json)

app.py:2570-2573, `Datasette._connected_databases` (app.py:2157-2169).
Permission `view-instance`. No parameters.

Response: a JSON array of `{"name", "route", "path", "size", "is_mutable",
"is_memory", "hash"}` — **all attached databases are listed regardless of
per-database `view-database` permissions**.

### GET /-/actor(.json)

app.py:2574-2579, registered with `permission=None` — **accessible to any
request including anonymous**. No parameters.

Response: `{"ok": true, "actor": {...}}` or `{"ok": true, "actor": null}` (app.py:2287-2288).

### GET /-/actions(.json)

app.py:2580-2589. Permission **`permissions-debug`**. No parameters.

Response: a JSON array, sorted by name, of `{"name", "abbr", "description",
"takes_parent", "takes_child", "resource_class", "also_requires"}`
(app.py:2290-2304).

### GET /-/auth-token

`AuthTokenView` (app.py:2590-2593, views/special.py:198-217). GET only, no
`.json` variant, HTML/redirect only.

- **Parameter:** `token` — the one-time secret printed by `datasette --root`.
- Match → invalidates the token, sets the signed `ds_actor` cookie to
  `{"id": "root"}` and 302-redirects to the homepage. Mismatch or reuse →
  `Forbidden` → 403 HTML.

### GET/POST /-/create-token

`CreateTokenView` (app.py:2594-2597, views/special.py:727-856). **HTML form
endpoint only — there is no JSON request/response mode in this codebase**
(`has_json_alternate = False`; the POST body must be form-encoded, a JSON
content type raises `BadRequest` → 400).

- **Gates** (each failure → `Forbidden` → 403): `allow_signed_tokens` must be
  on; request must have an actor with an `id`; the actor must not itself be
  token-derived.
- **POST fields:** `expire_type` (`""`/`minutes`/`hours`/`days`),
  `expire_duration` (positive int), plus restriction checkboxes named
  `all:<action>`, `database:<db>:<action>`,
  `resource:<db>:<table>:<action>`.
- **Response:** HTML page containing the new `dstok_` token.
- Programmatic alternatives: `datasette create-token` CLI or
  `datasette.create_token()`.

### GET /-/api

`ApiExplorerView` (app.py:2598-2601, views/special.py:859-1020). HTML API
explorer, GET only. Permission `view-instance` (403 on denial).

### GET /-/jump(.json)

`JumpView` (app.py:2602-2605, views/special.py:1023-1201). The route allows
an optional `.json` suffix but the view **always returns JSON**.

- **Permission:** none checked directly; results are filtered via
  `allowed_resources_sql` for the current actor (default items come from the
  `jump_items_sql` plugin hook).
- **Parameter:** `q` — whitespace-split terms matched as a case-insensitive
  `%term1%term2%` LIKE pattern.
- **Response:** `{"ok": true, "matches": [...], "truncated": bool}`; each match:
  `name`, `url`, `type` (`database`/`table`/`view`/`query`/plugin-defined),
  `description`, optional `display_name`. Capped at 100 matches.

### GET /-/schema(.json|.md)

`InstanceSchemaView` (app.py:2610-2613, views/special.py:1257-1293).

- **Permission:** no explicit check; only databases the actor can
  `view-database` are included (others silently omitted).
- **Formats:** no extension → HTML; `.json` →
  `{"ok": true, "schemas": [{"database": name, "schema": "..."}]}`; `.md` →
  `text/markdown` rendering.

### GET/POST /-/logout

`LogoutView` (app.py:2614-2617, views/special.py:220-238). HTML endpoint.
GET renders a confirmation page (or redirects if anonymous); POST deletes the
`ds_actor` cookie and 302-redirects to `/`.

### GET/POST /-/permissions

`PermissionsDebugView` (app.py:2618-2621, views/special.py:241-295). No
`.json` route. Both methods require `view-instance` **and**
`permissions-debug` (403 on denial).

- **GET** — HTML permission-check log; `?filter=all|exclude-yours|only-yours`.
- **POST** — form-encoded `actor` (JSON string), `permission`, optional
  `resource_1`, `resource_2`; returns **JSON**
  `{"action", "allowed", "resource": {"parent", "child", "path"}}` plus
  `actor_id` when present. Errors: unknown action → 404; child without
  parent → 400 (both canonical error shape).

### GET /-/allowed(.json)

`AllowedResourcesView` (app.py:2622-2625, views/special.py:298-460). Bare
path always renders the HTML form; `.json` returns JSON.

- **Permission:** none — reports the **current actor's own** allowed
  resources. Items gain a `reason` field if the actor also holds
  `permissions-debug`.
- **Parameters:** `action` (required; missing → 400 canonical error, unknown
  → 404), `parent`, `child` (requires `parent`), `page` (default 1),
  `page_size` (default 50, silently capped at 200).
- **Response:** `{"action", "actor_id", "page", "page_size", "total",
  "items": [{"parent", "child", "resource"}]}` with optional `next_url` /
  `previous_url`.

### GET /-/rules(.json)

`PermissionRulesView` (app.py:2626-2629, views/special.py:463-584).
Permission `view-instance` **and** `permissions-debug`. Parameters and error
shapes as `/-/allowed`. Response items:
`{"parent", "child", "resource", "allow" (1|0), "reason", "source_plugin"}`.

### GET /-/check(.json)

`PermissionCheckView` (app.py:2630-2633, views/special.py:633-662).
Permission `permissions-debug`. Parameters `action` (required), `parent`,
`child`. Checks the **current request's actor**; response
`{"action", "allowed", "resource": {...}}` plus `actor_id`.

### GET/POST /-/messages

`MessagesDebugView` (app.py:2634-2637, views/special.py:703-724). HTML debug
tool for flash messages; permission `view-instance`; POST is form-encoded
(`message`, `message_type` = INFO/WARNING/ERROR/all) and 302-redirects.

### GET /-/allow-debug

`AllowDebugView` (app.py:2638-2641, views/special.py:665-700). GET only, HTML
only, **no permission required**. Parameters `actor` and `allow` (JSON
strings); renders the result of `actor_matches_allow()` in the page.

### GET /-/patterns

Pattern portfolio page (app.py:2642-2645). HTML only; not part of the JSON API.

### GET /-/debug/autocomplete

`AutocompleteDebugView` (app.py:2646-2649, views/special.py:94-195). HTML
debug page for the table autocomplete API; permission `view-instance` plus
`view-table` when `?database=&table=` are supplied.

---

## Database endpoints

### GET /\<database\>.db

Downloads the raw SQLite file. Route → `database_download`
(app.py:2650-2653; views/database.py:533-570).

- **Permission:** `view-database-download` (denied → `Forbidden` → 403 HTML).
- **Other gates:** unknown database → 404 `"Invalid database"`; in-memory
  database → 404; `allow_download` off **or** mutable database →
  `Forbidden("Database download is forbidden")`; no file path → 404.
- **Response:** streamed `application/octet-stream` with a
  `content-disposition` attachment; immutable databases with a known hash set
  `Etag` and honor `If-None-Match` → 304.

### GET /\<database\>(.json)

`DatabaseView` (app.py:2654-2657; views/database.py:71-277). Only `html` and
`json` formats are accepted; any other extension → 404 `"Invalid format: ..."`.

- **Permission:** `view-database` via `check_visibility` (denied →
  `Forbidden` → 403 HTML). Table/view listings are filtered by `view-table`;
  stored queries by `view-query`.
- **Parameters:**
  - `?sql=` — non-blank value 302-redirects to `/<database>/-/query?...`
    preserving the query string and format.
  - No `?_extra=` and no `_shape` support — the JSON is built directly and
    returned via `Response.json`, bypassing the JSON renderer
    (views/database.py:189-212).
- **JSON response** (all keys always present):
  - `ok` — always `true`
  - `database` — name; `private` — bool; `path` — URL path; `size` — bytes
  - `tables` — list (includes hidden tables), each:
    `name`, `columns` (names), `primary_keys`, `count` (int or null,
    time-boxed), `count_truncated` (bool — count is a capped lower bound),
    `hidden`, `fts_table`, `foreign_keys` (`{incoming: [...], outgoing: [...]}`
    of `{other_table, column, other_column}`), `private`
  - `hidden_count` — number of hidden tables
  - `views` — list of `{name, private}`
  - `queries` — **up to 5** stored queries (canonical stored-query objects,
    see the stored-queries section); `queries_more` (bool);
    `queries_count` (total visible)
  - `allow_execute_sql` — bool for this actor
  - `table_columns` — `{table: [columns]}`, empty `{}` unless
    `allow_execute_sql` (views map to `[]`)
  - `metadata` — database metadata dict

### GET /\<database\>/-/query(.json) — arbitrary SQL

`QueryView` (app.py:2691-2694; views/database.py:573-1130). The same class
also executes stored queries dispatched from the table route (see stored
queries section).

- **Permission:** `execute-sql` on the database via `check_visibility`
  (denied → `Forbidden` → 403 HTML).
- **Parameters:**
  - `sql` — SQL to run. Must pass `validate_sql_select`
    (utils/__init__.py:345-354): after stripping `--` comment lines it must
    start with `select`, `with` or an `explain` variant, and must not contain
    `pragma` (except allowlisted `pragma_*()` table-valued functions).
    Failure → 400 `DatasetteError` titled `"Invalid SQL"` → JSON
    `{"ok": false, "error": "Statement must be a SELECT", "status": 400,
    "title": "Invalid SQL"}`.
  - Any other `name=value` pair supplies the `:name` named parameter; missing
    parameters default to `""`. Names starting with `_` are excluded.
  - `_timelimit` — per-request SQL time limit in ms.
  - `_shape`, `_nl`, `_json`, `_json_infinity` — see the JSON renderer section.
  - `_extra` — QUERY-scope extras: `columns`, `debug`, `request`,
    `render_cell`, `query` (`{"sql", "params"}`), `metadata`, `database`,
    `database_color`, `private`, `extras`.
- **Response** (default shape):
  `{"ok": true, "rows": [{col: val}, ...], "truncated": false}` plus any
  requested extras. `truncated: true` when the result hit `max_returned_rows`.
- **Errors:**
  - SQLite errors (e.g. `no such table`) are **not** raised — they surface as
    HTTP 400 `{"ok": false, "error": "<message>", "rows": [], "truncated": false}`.
  - Time limit → 400 titled `"SQL Interrupted"` (the `error` value contains
    an HTML fragment).
  - `?sql=` omitted → 200 `{"ok": true, "rows": [], "truncated": false}`
    (the CSV format instead errors 400 `"?sql= is required"`).
- `.csv` streams CSV; unknown extensions → 404.

### GET /\<database\>/-/query/parameters

`QueryParametersView` (app.py:2687-2690; views/stored_queries.py:26-51).

- **Permission:** `execute-sql` → 403 JSON
  `{"ok": false, "errors": ["Permission denied: need execute-sql"]}`.
- **Parameters:** only `sql` (default `""`); any other key → 400
  `"Invalid keys: ..."`.
- **Response:** 200 `{"ok": true, "parameters": ["name1", ...]}`. SQL with a
  parameter beginning `_` → 400 `"Magic parameters are not allowed"`.
- Responses carry `Content-Security-Policy: frame-ancestors 'none'` and
  `X-Frame-Options: DENY`.

### POST /\<database\>/-/create

`TableCreateView` (app.py:2658; views/table_create_alter.py:785-962).
GET → 405. Body is parsed as JSON regardless of content type; invalid JSON →
400 `{"ok": false, "errors": ["Invalid JSON: ..."]}`.

- **Permissions** (all denials → 403 canonical error JSON,
  all checked at the **database** level):
  - `create-table` — always required (`["Permission denied"]`)
  - `insert-row` — if `rows`/`row` provided (`need insert-row`)
  - `update-row` — if `replace: true` (`need update-row`)
  - `alter-table` — if `alter: true` on an **existing** table
    (`need alter-table`); when the table does not exist yet and rows are
    supplied, alter is enabled automatically.
- **Request schema** (pydantic `CreateTableRequest`, extra keys forbidden →
  400 `"Invalid keys: a, b"`):
  - `table` (required) — must match `^(?!sqlite_)[^\n]+$`
  - `rows` (list of objects) / `row` (single object) — mutually exclusive
  - `columns` — list of `{name, type, fk_table, fk_column, not_null,
    default, default_expr}`; mutually exclusive with `rows`/`row`; `type` one
    of `text`/`integer`/`float`/`blob` (default `text`); `default` and
    `default_expr` mutually exclusive; `default_expr` one of
    `current_timestamp`, `current_date`, `current_time`, `current_unixtime`,
    `current_unixtime_ms`. At least one of `columns`/`rows`/`row` required.
  - `pk` (string) / `pks` (list) — mutually exclusive. For an existing table
    a differing pk → 400 `"pk cannot be changed for existing table"`.
  - `ignore` / `replace` (bools) — mutually exclusive; require `row`/`rows`
    and `pk`/`pks`.
  - `alter` (bool) — add missing columns when inserting into an existing table.
- **Success** — **201**:
  ```json
  {"ok": true, "database": "...", "table": "...",
   "table_url": "https://.../db/table", "table_api_url": "https://.../db/table.json",
   "schema": "CREATE TABLE ...", "row_count": 2}
  ```
  `row_count` only when rows were inserted. Write failures → 400
  `{"ok": false, "errors": ["<sqlite message>"]}`. Emits `create-table` /
  `insert-rows` / `alter-table` events.

### POST /\<database\>/-/execute-write

`ExecuteWriteView` (app.py:2679-2682; views/execute_write.py:236-476). GET on
the same path renders an HTML form (requires `execute-write-sql`).

- **Permission (POST):** `execute-write-sql` → 403
  `{"ok": false, "errors": ["Permission denied: need execute-write-sql"]}`;
  immutable database → 403 `["Database is immutable"]`.
- **Per-statement permissions:** the SQL is analyzed
  (`decision_for_write_sql_operation`, write_sql.py:63-189) and each
  operation must pass:

  | Operation | Requirement |
  |---|---|
  | `select` / internal ops / function calls | ignored |
  | read of a table | `view-table` on that table |
  | `insert` or `update` | **all of** `insert-row`, `update-row`, `delete-row` on the table |
  | `delete` | `delete-row` |
  | `create table` | `create-table` on the database |
  | `alter table`, `create index`, `drop index` | `alter-table` on the table |
  | `drop table` | `drop-table` |
  | `VACUUM`, virtual-table writes, shadow-table writes | rejected outright (403) |
  | statements touching attached databases | rejected (403) |

- **Body:** JSON (`{"sql": ..., "params": {...}}` — only those two keys) or
  form-encoded (`sql` plus one field per parameter, `_sql_param_` prefix
  stripped). Validation errors (400): `"SQL is required"`,
  `"params must be a dictionary"`, `"Unknown parameters: a, b"`,
  `"Magic parameters are not allowed"`, `"Could not analyze query: ..."`,
  `"Use /-/query for read-only SQL; this endpoint only executes writes"`.
- **JSON is returned when** the body was JSON, `Accept: application/json`, or
  a truthy `_json` field is present; otherwise HTML.
- **Success** — 200:
  ```json
  {"ok": true, "message": "Query executed, 1 row affected", "rowcount": 1,
   "rows": [], "truncated": false,
   "analysis": [{"operation": "insert", "database": "db", "table": "t",
                  "required_permission": "insert-row, update-row, delete-row",
                  "source": null}]}
  ```
  `rows` is populated by `RETURNING` clauses. SQLite errors → 400
  `{"ok": false, "errors": ["<message>"]}`. Anti-framing headers on all
  responses.

### GET /\<database\>/-/execute-write/analyze

`ExecuteWriteAnalyzeView` (app.py:2675-2678; views/execute_write.py:479-507).

- **Permission:** `execute-write-sql` → 403 `errors` JSON.
- **Parameters:** only `sql` allowed (else 400 `"Invalid keys: ..."`).
- **Response** — 200 even when analysis fails (`ok: false` in body):
  `{"ok", "parameters", "analysis_error", "analysis_rows":
  [{operation, database, table, required_permission, source, allowed}],
  "execute_disabled", "execute_disabled_reason"}`. `allowed` is a per-actor
  permission check result (true/false/null).

### GET /\<database\>/-/foreign-key-targets

`DatabaseForeignKeyTargetsView` (app.py:2659-2662;
views/table_create_alter.py:965-1005).

- **Parameter:** `table` (optional) — only used for the permission check.
- **Permission:** `create-table` on the database, **or** `alter-table` on
  `?table=` when it names an existing table. Neither → 403
  `{"ok": false, "errors": ["Permission denied: need create-table"]}`.
- **Response:** 200 `{"ok": true, "database": "...", "targets":
  [{"fk_table", "fk_column", "type"}]}` — every non-hidden table with exactly
  one primary-key column; `type` is the pk's SQLite type affinity.

### GET /\<database\>/-/schema(.json|.md)

`DatabaseSchemaView` (app.py:2683-2686; views/special.py:1296-1329).

- **Permission:** `view-database` (denied → `Forbidden` → 403 HTML).
- **Unknown database** → 404; for `.json`:
  `{"ok": false, "error": "Database not found"}`. (The existence check runs
  before the permission check.)
- **Responses:** `.json` → 200 `{"ok": true, "database": "<name>", "schema": "<SQL>"}`
  (concatenated `sqlite_master.sql` joined with `;\n`); `.md` →
  `text/markdown`; no extension → HTML.

---

## Table and row read endpoints

### GET /\<database\>/\<table\>.json

Route `r"/(?P<database>[^\/\.]+)/(?P<table>[^\/\.]+)(\.(?P<format>\w+))?$"` →
`table_view` (app.py:2711-2714; views/table.py:1670). Serves both tables and
SQL views. GET/HEAD only — POST returns a plain-text 405. If the name is
neither a table nor a view but matches a stored query, the request is
dispatched to `QueryView` (views/table.py:1703-1712).

**Permission:** `view-table` via `check_visibility`; denial raises
`Forbidden` → **HTML** 403 page even for `.json`. Unknown table →
`TableNotFound` → 404 (JSON error shape for `.json` paths).

**Default JSON keys** (views/table.py:2308-2332 + renderer):

| Key | Meaning |
|---|---|
| `ok` | `true` when data was retrieved without error |
| `next` | pagination token string, or `null` on the last page |
| `rows` | list of row objects `{column: value}` (default `_shape=objects`) |
| `truncated` | always present; `false` for table pages |

`columns` is computed but removed unless `?_extra=columns` was requested.
When there is a next page the response carries a
`Link: <next_url>; rel="next"` header (views/table.py:1911-1912).

**`?_extra=` options** (TABLE scope; registry
views/table_extras.py:1197-1235; unknown names silently ignored):

| `_extra=` | Returns |
|---|---|
| `count` | total matching-row count, computed with a `limit 10001` subquery so it caps at 10001; `null` with `_nocount` or on count timeout |
| `count_sql` | the SQL used for the count |
| `facet_results` | `{"results": {name: facet}, "timed_out": [...]}`; each facet: `{name, type, hideable, toggle_url, results: [{value, label, count, toggle_url, selected}], truncated}` |
| `facets_timed_out` | facet names that exceeded `facet_time_limit_ms` |
| `suggested_facets` | `[{name, toggle_url, (type)}]`; empty when suggestion is disabled or paginating |
| `human_description_en` | English description of filters + sort |
| `next_url` | absolute URL of the next page or `null` |
| `columns` | column names of the returned rows |
| `all_columns` | all table columns regardless of `_col`/`_nocol` |
| `primary_keys` | pk column names (empty for rowid tables and views) |
| `display_columns` | HTML-oriented column metadata |
| `render_cell` | per-row plugin-rendered HTML strings |
| `debug` | `{url_vars, resolved, nofacet, nosuggest}` — explicitly unstable |
| `request` | `{url, path, full_path, host, args}` |
| `query` | `{sql, params}` of the main query |
| `column_types` | `{column: {type, config}}` assigned column types |
| `set_column_type_ui` | UI helper, `null` unless actor has `set-column-type` |
| `metadata` | table metadata dict including column descriptions |
| `extras` | self-describing list of all available extras |
| `database`, `table`, `database_color` | identity/display values |
| `renderers` | `{format_name: url}` of formats that can render this data |
| `custom_table_templates` | template lookup list |
| `sorted_facet_results` | facets as a display-ordered list |
| `table_definition` | `CREATE TABLE` SQL |
| `view_definition` | `CREATE VIEW` SQL, `null` for tables |
| `is_view` | boolean |
| `private` | `true` if visible to this actor but not anonymously |
| `expandable_columns` | `[[foreign_key, label_column_or_null], ...]` |
| `form_hidden_args` | pairs of `_`-prefixed args for HTML forms |

Non-public extras (`actions`, `filters`, `display_rows`) are HTML-only and
never appear in JSON. `_extra=_html` expands to the full HTML bundle
(views/table_extras.py:1162-1194). Any `_facet*` argument implicitly adds
`facet_results`; `_shape=object` implicitly adds `primary_keys`
(views/table.py:2252-2256). There is **no** `filtered_table_rows_count`
extra — it was replaced by `count`.

**Column filters `?<column>__<op>=<value>`** (filters.py:260-427). Any
querystring key not starting with `_` is a filter; bare `?column=value` means
`exact`. Columns whose names start with `_` can be filtered as
`?_col__exact=`. Operators:

| op | SQL |
|---|---|
| `exact` | `"col" = :p` (default) |
| `not` | `"col" != :p` |
| `contains` / `notcontains` | `like '%v%'` / `not like '%v%'` |
| `endswith` / `startswith` | `like '%v'` / `like 'v%'` |
| `gt` / `gte` / `lt` / `lte` | `>` `>=` `<` `<=` (numeric strings cast to int) |
| `like` / `notlike` | raw `like` / `not like` pattern |
| `glob` | `glob` |
| `in` / `notin` | comma-separated list, or JSON array if the value starts with `[` |
| `arraycontains` / `arraynotcontains` | `[not] in (select value from json_each("col"))` (requires JSON1) |
| `date` | `date("col") = :p` |
| `isnull` / `notnull` | `is null` / `is not null` (no value) |
| `isblank` / `notblank` | `(is null or = '')` / opposite (no value) |

**Special (underscore) parameters:**

| Param | Behavior |
|---|---|
| `_where=SQL` | extra raw where clause (repeatable); requires `execute-sql` else 403 `"_where= is not allowed"` |
| `_search=q` | FTS against the table's FTS table |
| `_search_<column>=q` | FTS restricted to one column; 400 if invalid |
| `_searchmode=raw` | pass the query straight to `match` |
| `_fts_table=` / `_fts_pk=` | override the FTS table / pk used for joins |
| `_through={"table","column","value"}` | filter via an incoming foreign key (repeatable, JSON value) |
| `_sort=col` / `_sort_desc=col` | sort; 400 if both given or column not sortable |
| `_next=token` | pagination token |
| `_size=N\|max` | page size; default `default_page_size` (100); `max` = `max_returned_rows` (1000); 400 on invalid |
| `_col=name` (repeatable) | return only pks + these columns; 400 on invalid |
| `_nocol=name` (repeatable) | exclude columns; 400 if invalid or a pk |
| `_labels=on` | expand every FK column into `{"value", "label"}` |
| `_label=col` (repeatable) | expand only the named FK column(s) |
| `_facet=col` | request a facet; 400 `"_facet= is not allowed"` when `allow_facet` off |
| `_facet_array=col` / `_facet_date=col` | typed facets |
| `_facet_size=N\|max` | facet bucket count, default 30, capped at `max_returned_rows` |
| `_nocount=1` | skip count (`count` extra → null) |
| `_nofacet=1` | skip facets and suggestions |
| `_nosuggest=1` | skip facet suggestions only |
| `_shape=` | see renderer section; `array`/`object` also force `_nocount` and `_nofacet` |
| `_nl=on` | NDJSON with `_shape=array` |
| `_json=col` / `_json_infinity=1` | renderer options |
| `_timelimit=ms` | custom SQL time limit |
| `_ttl=seconds` | `Cache-Control: max-age=N` (`0` → `no-cache`); default `default_cache_ttl` (5) |
| `_trace=1` | append `_trace` key (requires `trace_debug` setting) |
| `_extra=` | see above |

**Pagination** is keyset-based for tables: `page_size + 1` rows are fetched;
`next` is built from the last row of the page — comma-joined tilde-encoded
primary-key values, prefixed by the sort value when sorted (`$null` for null
sort values) (views/table.py:2041-2111, 2421-2482). `next_url` is the
absolute URL with `_next` replaced.

### GET /\<database\>/\<view_name\>.json (SQL views)

Same code path with `is_view=True`. Differences:

- No primary keys: `primary_keys` → `[]`; `_shape=object` fails; base query
  has no `order by`.
- **Pagination is offset-based**: `_next` is an integer offset applied as
  `limit N offset M` (views/table.py:2047-2049, 2438-2439) — unlike the
  keyset tokens used for tables.
- `view_definition` returns the `CREATE VIEW` SQL; `table_definition` is null.

### GET /\<database\>/\<table\>/\<pks\>.json

`RowView` (app.py:2715-2718; views/row.py:137). `<pks>` is comma-separated
tilde-encoded primary key values (rowid for rowid tables).

- **Permission:** `view-table` (denied → `Forbidden` → 403 HTML). Missing row
  → 404 `"Record not found: [...]"`.
- **Default JSON keys:** `ok`, `database`, `table`, `rows` (single-element
  list), `primary_keys`, `primary_key_values`, `query_ms`,
  `truncated: false`; `columns` only with `?_extra=columns`.
- **`?_extra=` (ROW scope):** `columns`, `primary_keys`, `render_cell`,
  `debug`, `request`, `query`, `column_types`, `metadata`, `extras`,
  `database`, `table`, `database_color`, `private`, `foreign_key_tables`
  (incoming FKs with `count` and `link`; single-pk rows only).
- **Foreign-key label expansion does not apply to row JSON** — `_labels` has
  no effect here; expansion happens only in the HTML path
  (views/row.py:445-475).
- `_shape`, `_json`, `_nl`, `_json_infinity`, `_ttl` apply.

### The .blob format

`/<database>/<table>/<pks>.blob?_blob_column=col` (also on query pages) —
fetches raw binary bytes (blob_renderer.py:10-61). `_blob_column` required
(400 if missing/invalid); optional `_blob_hash` must equal the value's
SHA-256 (else 400 `"Link has expired..."`). Returns `application/binary` as a
download attachment. In JSON output, binary cells appear as
`{"$base64": true, "encoded": "..."}`.

### GET /\<database\>/\<table\>/-/schema(.json|.md)

`TableSchemaView` (app.py:2751-2754; views/special.py:1332-1378).

- **Permission:** `view-table` via `ensure_permission` (denied → 403 HTML).
- **Responses:** `.json` → 200 `{"ok": true, "database", "table", "schema"}`;
  `.md` → `text/markdown`; no extension → HTML. Missing table → 404
  `{"ok": false, "error": "Table not found"}` for `.json`.

### GET /\<database\>/\<table\>/-/fragment

`TableFragmentView` (app.py:2739-2742; views/table.py:1385-1418).
**HTML-only** — returns the `_table.html` partial; no JSON variant. Accepts
table querystring parameters plus `_row=<pk-path>` to render a single row.

### GET /\<database\>/\<table\>/-/autocomplete

`TableAutocompleteView` (app.py:2743-2746; views/table.py:1492-1595). Tables
only — views get 400 `"Autocomplete is only available for tables"`.

- **Permission:** `view-table` (denied → `Forbidden` → 403).
- **Parameters:** `q` (matched with escaped `LIKE %q%` against pk columns and
  the label column) and `_initial` (truthy: with empty `q`, return the 10
  most recent rows). Neither → `{"ok": true, "rows": []}`.
- **Response:** `{"rows": [{"pks": {pk_name: value}, "label": "..."}]}` — max
  10 items; 500 ms query budget with fallbacks, timing out to
  `{"ok": true, "rows": []}`.

---

## The write API

All write endpoints return errors via `_error()` (the canonical error
shape) and check permissions with
`datasette.allowed()` directly, so their 403s are JSON (unlike the
`Forbidden`-raising read endpoints). Routes: app.py:2719-2762.

### POST /\<database\>/\<table\>/-/insert

`TableInsertView` (views/table.py:907-1194).

- **Permissions:** `insert-row` on the table (denied → 403
  `["Permission denied"]`); `update-row` additionally required for
  `replace: true` (403 `need update-row to use "replace"`); `alter-table`
  additionally required for `alter: true` (403
  `Permission denied for alter-table`). Immutable database → 403
  `Database is immutable`.
- **Request** — requires `Content-Type: application/json` (else 400
  `"Invalid content-type, must be application/json"`). Body:

  | Field | Rules |
  |---|---|
  | `row` | single object; mutually exclusive with `rows`; forces `return: true` |
  | `rows` | list of objects; max `max_insert_rows` (default 100), else 400 `"Too many rows, maximum allowed is 100"` |
  | `ignore` | skip rows whose pk already exists; mutually exclusive with `replace` |
  | `replace` | replace rows with matching pks (needs `update-row`) |
  | `alter` | add missing columns (needs `alter-table`) |
  | `return` | include inserted rows in the response |

  One of `row`/`rows` required. Unknown keys → 400 `"Invalid parameter: ..."`.
  Unless `alter`, row keys must be existing columns → per-row 400
  `"Row 0 has invalid columns: x, y"`. Values are validated against assigned
  column types.
- **Response** — **201** `{"ok": true}`; with `return: true` also `rows`
  (the rows as stored, re-fetched by rowid). SQLite errors during the write →
  400 with the message. Emits `insert-rows` (and possibly `alter-table`)
  events.

### POST /\<database\>/\<table\>/-/upsert

`TableUpsertView` — subclasses insert (views/table.py:1197-1201).

- **Permissions:** **both** `insert-row` and `update-row` (403
  `need both insert-row and update-row`); `alter: true` needs `alter-table`.
- **Request:** same as insert, except `ignore`/`replace` are rejected (400
  `"Upsert does not support ignore or replace"`) and **every row must contain
  the table's primary key(s)** (per-row 400
  `Row 0 is missing primary key column(s): "id"` / `has null primary key`).
- **Response** — **200** (note: insert returns 201) `{"ok": true}`; with
  `return: true`, `rows` re-fetched by pk. Emits `upsert-rows`.

### POST /\<database\>/\<table\>/-/alter

`TableAlterView` (views/table_create_alter.py:1130-1353).

- **Permission:** `alter-table` (403 `need alter-table`); immutable → 403.
- **Request:** `{"operations": [{"op": ..., "args": {...}}, ...]}` — a
  non-empty list, validated by pydantic (extra keys forbidden anywhere;
  errors → 400 `location: message`):

  | `op` | `args` |
  |---|---|
  | `add_column` | `name` (required), `type` (`text`/`integer`/`float`/`blob`, default `text`), `not_null`, `default` xor `default_expr`; `not_null: true` requires a default |
  | `rename_column` | `name`, `to` |
  | `rename_table` | `to` (must not start `sqlite_`) |
  | `alter_column` | `name` + at least one of `type`, `not_null`, `default`, `default_expr` |
  | `drop_column` | `name` |
  | `set_primary_key` | `columns` (non-empty list) |
  | `reorder_columns` | `columns` (non-empty list) |
  | `add_foreign_key` | `column`, `fk_table`, optional `fk_column` |
  | `drop_foreign_key` | `column` |
  | `set_foreign_keys` | `foreign_keys`: list of `{column, fk_table, fk_column?}` |

  `default_expr` must be one of the five `current_*` keywords. Operations are
  applied in a single write transaction; any failure → 400.
- **Response** — 200:
  ```json
  {"ok": true, "database": "...", "table": "<possibly renamed>",
   "table_url": "...", "table_api_url": "...",
   "altered": true, "schema": "...", "before_schema": "...",
   "operations_applied": 2}
  ```

### POST /\<database\>/\<table\>/-/drop

`TableDropView` (views/table.py:1320-1382).

- **Permission:** `drop-table` (403 `Permission denied`); immutable → 403.
- **Confirmation flow:** without `{"confirm": true}` in the body, nothing is
  dropped and a 200 preview is returned:
  `{"ok": true, "database", "table", "row_count",
  "message": "Pass \"confirm\": true to confirm"}`. With `confirm: true` →
  200 `{"ok": true}`. Emits `drop-table`.

### POST /\<database\>/\<table\>/-/set-column-type

`TableSetColumnTypeView` (views/table.py:1204-1317). Assigns a Datasette
*column type* (metadata stored in the internal `column_types` table) — it
does not change the SQLite schema.

- **Permission:** `set-column-type` (403 `Permission denied`).
- **Request** (JSON content type required): `{"column": "name",
  "column_type": {"type": "url", "config": {...}?} | null}`. Unknown
  keys/invalid structure → detailed 400 errors; unknown type → 400
  `"Unknown column type: x"`. Default registered types (via the
  `register_column_types` hook): `url`, `email`, `json`, `textarea`.
- **Response** — 200 `{"ok": true, "database", "table", "column",
  "column_type": {...} | null}`.

### GET /\<database\>/\<table\>/-/foreign-key-suggestions

`TableForeignKeySuggestionsView` (views/table_create_alter.py:1008-1127).
**GET only** (read-only despite living beside the write endpoints).

- **Permission:** `alter-table` (403 `need alter-table`); views → 400
  `"Cannot suggest foreign keys for a view"`.
- **Response** — 200: `{"ok": true, "database", "table",
  "row_check": {attempted, status, row_limit, sampled_rows, checked_options},
  "columns": [{column, type, affinity, current,
  "suggestions": [{fk_table, fk_column, confidence, sampled_values, reasons}],
  "options": [...]}]}`. Samples up to 500 rows within 50 ms/200 ms budgets.

### POST /\<database\>/\<table\>/\<pks\>/-/update

`RowUpdateView` (views/row.py:781-870).

- **Permissions:** `update-row` (403 `Permission denied`); `alter: true`
  additionally requires `alter-table` (403
  `Permission denied for alter-table`).
- **404s:** `Database not found: x` / `Table not found: x` /
  `Record not found: [pks]`.
- **Request:** `{"update": {column: value, ...}, "return"?: true,
  "alter"?: true}`. Missing/non-dict `update` → 400
  `"JSON must contain an update dictionary"`; unknown keys → 400
  `"Invalid keys: ..."`; write failures (bad column, constraint violation) →
  400 with the message.
- **Response** — 200 `{"ok": true}`; with `return: true`,
  `{"ok": true, "row": {...}}` (singular `row`, unlike insert/upsert's
  `rows`). Emits `update-row`.

### POST /\<database\>/\<table\>/\<pks\>/-/delete

`RowDeleteView` (views/row.py:738-778).

- **Permission:** `delete-row` (403 `Permission denied`). 404s as update.
- **Request:** no body required (any body is ignored — there is no
  confirmation step, unlike table drop).
- **Response** — 200 `{"ok": true}`; with `?_redirect_to_table` a `redirect`
  key is added. A failure during the write returns **500** with the message
  (unlike update's 400). Emits `delete-row`.

---

## Stored (canned) queries API

Stored queries live in the internal database's `queries` table
(utils/internal_db.py:116-133). Queries defined in `datasette.yaml` are
synced in at startup with `source="config"` and `is_trusted` defaulting to
true; queries created via the API get `source="user"`, `is_trusted=false`,
`owner_id` = actor id.

**Canonical stored-query JSON object** (`stored_query_to_dict`,
stored_queries.py:55-80):

```json
{
  "database": "...", "name": "...", "sql": "...",
  "title": null, "description": null, "description_html": null,
  "hide_sql": false, "fragment": null,
  "params": ["p"], "parameters": ["p"],
  "is_write": false, "is_private": true, "is_trusted": false,
  "source": "user", "owner_id": "...",
  "on_success_message": null, "on_success_message_sql": null,
  "on_success_redirect": null,
  "on_error_message": null, "on_error_redirect": null,
  "private": true
}
```

`params` and `parameters` are identical lists, both always present.
`private` appears only in list responses.

**Default permission rules for queries** (default_permissions/defaults.py):
`view-query` is default-allow, but private queries are visible only to their
owner; the owner may `update-query`/`delete-query` their `source='user'`
queries.

### GET /-/queries(.json) and GET /\<database\>/-/queries(.json)

`GlobalQueryListView` / `QueryListView` (app.py:2606-2609, 2663-2666;
views/stored_queries.py:69-238). The global variant lists queries across all
databases (`database`/`database_color` are null, `show_database` true).

- **Permissions:** no single gate; results filtered per query by
  `view-query` (private queries appear only for their owner).
- **Parameters:** `_size` (default 20 HTML / **50 JSON**, clamped 1–1000;
  non-integer → 400), `_next` (cursor), `q` (substring search over
  name/title/description/sql), `is_write` / `is_private` (booleans; invalid →
  400 `"is_write must be 0 or 1"`), `source`, `owner_id`.
- **Response** — 200:
  `{"ok": true, "database", "database_color", "queries": [...], "next",
  "next_url", "has_more", "limit", "show_private_note",
  "show_trusted_note", "query_list_path", "show_database",
  "facets": [{title, items: [{label, count, href, active}]}],
  "filters": {q, is_write, is_private, source, owner_id}}`.

### GET /\<database\>/-/queries/analyze

`QueryCreateAnalyzeView` (app.py:2667-2670; views/stored_queries.py:290-322).
**GET only** despite being an "analyze" action — POST → 405.

- **Permissions:** `execute-sql` then `store-query` (each denial → 403
  `errors` JSON).
- **Parameters:** only `sql` (others → 400 `"Invalid keys: ..."`).
- **Response** — 200: `{"ok", "parameters", "analysis_error",
  "analysis_rows": [{operation, database, table, required_permission,
  source, allowed}], "has_sql", "analysis_is_write", "save_disabled"}`.

### POST /\<database\>/-/queries/store

`QueryStoreView` (app.py:2671-2674; views/stored_queries.py:325-388). GET on
the same path renders the HTML create form.

- **Permissions:** `execute-sql` + `store-query` (403 `errors` JSON).
- **Request:** JSON bodies must wrap the fields:
  `{"query": {...fields...}}`; form bodies pass fields flat. Fields:
  `name` (required; `^[^/\.\n]+$`; conflicts with tables/views or existing
  queries → 400), `sql` (required; read SQL must pass `validate_sql_select`;
  write SQL must pass per-operation permission checks), `title`,
  `description`, `hide_sql`, `fragment`, `parameters`/`params` (must exactly
  match the SQL's named parameters; magic parameters rejected),
  `is_private` (**default true**), and — only for write SQL —
  `on_success_message`, `on_success_redirect`, `on_error_message`,
  `on_error_redirect`. `is_write` is derived from SQL analysis;
  `is_trusted`, `description_html` and `on_success_message_sql` cannot be
  set through this API.
- **Response:** JSON request → **201** `{"ok": true, "query": {...}}`; form
  request → 302 redirect.

### GET /\<database\>/\<query\>/-/definition

`QueryDefinitionView` (app.py:2695-2698; views/stored_queries.py:391-408).

- **Permission:** `view-query` (403 `["Permission denied"]`).
- **Response:** 200 `{"ok": true, "query": {...}}`; 404
  `["Query not found: x"]`.

### GET/POST /\<database\>/\<query\>/-/edit

`QueryEditView` (app.py:2699-2702) — **HTML form endpoint**
(`has_json_alternate = False`), not part of the JSON API. Programmatic
updates use `/-/update`.

### POST /\<database\>/\<query\>/-/update

`QueryUpdateView` (app.py:2703-2706; views/stored_queries.py:411-465).

- **Permissions:** `update-query` (403 `need update-query`); trusted queries
  → 403 `"Trusted queries cannot be updated using the API"`; changing `sql`
  additionally requires `execute-sql`.
- **Request:** `{"update": {...partial fields...}, "return"?: true}` — other
  top-level keys → 400. Updatable fields: `sql`, `title`, `description`,
  `hide_sql`, `fragment`, `parameters`/`params`, `is_private`, `on_*`
  fields (write SQL only). New SQL is re-analyzed and `is_write` recomputed.
- **Response:** 200 `{"ok": true}` (plus `query` with `return: true`); 404
  `"Query not found: x"`.

### POST /\<database\>/\<query\>/-/delete

`QueryDeleteView` (app.py:2707-2710; views/stored_queries.py:594-644). GET
renders an HTML confirmation page.

- **Permission:** `delete-query` (403 `need delete-query`). Unlike update,
  **trusted queries are not blocked** from API deletion.
- **Response:** JSON request → 200 `{"ok": true}`; form → 302; 404
  `"Query not found: x"`. No `confirm` field required (unlike table drop).

### GET/POST /\<database\>/\<query-name\>(.json) — executing a stored query

No dedicated route: the table route resolves the name, and on `TableNotFound`
the request is dispatched to `QueryView` when a stored query matches
(views/table.py:1698-1712). Covers both config-defined and API-stored
queries.

**GET (read queries)** — `QueryView.get` (views/database.py:695-1130):

- **Permissions:** `view-query` (denied → `Forbidden` → 403 HTML). Read
  queries then require `execute-sql` unless `is_trusted`. Write queries are
  **not executed** on GET — JSON returns empty `rows`; HTML shows a POST form.
- **Parameters:** each named `:param` is read from the query string (missing
  → `""`); `_timelimit`; renderer options (`_shape`, `_nl`, `_json`,
  `_json_infinity`); `_extra` (QUERY scope).
- **Response:** `{"ok": true, "rows": [...], "truncated": false}` + extras.
  SQL errors → 400 with `error` in the envelope.

**POST (write queries)** — `QueryView.post` (views/database.py:574-693):

- **Permissions:** `view-query`; then, unless `is_trusted`:
  `execute-write-sql` on the database **plus** per-operation write
  permissions (same table as `/-/execute-write`). Rejection → 403
  `{"ok": false, "message": "...", "redirect": null}` for JSON clients.
  Immutable database → 403.
- **Body:** form-encoded or JSON `param=value` pairs (values coerced to
  strings).
- **JSON is returned when** `Accept: application/json`, `?_json=1`, or a
  `_json` body field is present; otherwise 302 + flash message.
- **Magic parameters** (`:_<prefix>_<key>`, resolved server-side; registered
  via `register_magic_parameters`, default_magic_parameters.py):
  `_now_epoch`, `_now_date_utc`, `_now_datetime_utc`, `_actor_<key>`,
  `_random_chars_<N>`, `_cookie_<name>`, `_header_<name>` (underscores →
  hyphens). User-stored queries cannot contain magic parameters — they are a
  feature of config/trusted queries.
- **Response — 200 for both success and SQL failure** (only permission
  rejection is 403):
  `{"ok": true|false, "message": "...", "redirect": "..."|null}` —
  `message` honors `on_success_message_sql` / `on_success_message` /
  `on_error_message`, falling back to `"Query executed"` or
  `"Query executed, N rows affected"`.

---

## Authentication and tokens

### Bearer tokens (`dstok_`)

Signed API tokens are sent as `Authorization: Bearer dstok_...`. The
`actor_from_signed_api_token` hook (default_permissions/tokens.py:25-40)
passes the token to `datasette.verify_token()`, which tries every handler
registered via `register_token_handler`; the default is
`SignedTokenHandler` (tokens.py:117-193).

- **Format:** `dstok_` + itsdangerous-signed payload (namespace `token`)
  containing `a` (actor id), `t` (creation Unix time), optional `d`
  (duration seconds), optional `_r` (restrictions).
- **Verification** returns no actor when: `allow_signed_tokens` is off, the
  signature is invalid, `t` is missing/non-integer, or the token is expired.
  The effective duration is `d` capped by `max_signed_tokens_ttl` (default 0
  = no cap; a non-zero setting also imposes a TTL on tokens without `d`).
- **Resulting actor:** `{"id": <a>, "token": "dstok"}` plus `"_r"` and
  `"token_expires"` when applicable. Invalid/expired tokens silently produce
  an anonymous request (no 401) — the failure then surfaces as a 403 from
  whatever permission check the request hits.

**Restrictions (`_r`)** (default_permissions/restrictions.py):

- `"a"`: list of actions allowed on any resource
- `"d"`: `{database_name: [actions]}`
- `"r"`: `{database_name: {table_name: [actions]}}`

Actions are stored as abbreviations when available (see appendix); checks
accept either the full name or the abbreviation. Restrictions are an
allowlist filter layered on top of normal permission resolution — a
restricted token can never do more than its allowlist, and never more than
the underlying actor could do anyway.

### Token creation

- **`/-/create-token`** is an HTML form endpoint only (see the instance
  section) — there is no JSON API to mint tokens in this codebase.
- Programmatic alternatives: the `datasette create-token` CLI command and
  the `datasette.create_token()` Python API.
- `/-/auth-token` is the one-time `--root` login mechanism, unrelated to API
  tokens.

### Cookie authentication

Browser sessions use the signed `ds_actor` cookie (set by `/-/auth-token`,
plugins, or login flows; cleared by `/-/logout`). API POSTs from browsers are
subject to the cross-origin checks described in
[CSRF](#csrf--cross-origin-protection).

---

## Appendix: registered actions (permissions)

From `datasette/default_actions.py` (registered via the `register_actions`
hook). Token restrictions store the abbreviation when available.

| Action | Abbr | Resource level | Notes |
|---|---|---|---|
| `view-instance` | `vi` | global | |
| `permissions-debug` | `pd` | global | gates the debug endpoints |
| `debug-menu` | `dm` | global | UI only |
| `view-database` | `vd` | database | |
| `view-database-download` | `vdd` | database | `also_requires="view-database"` |
| `execute-sql` | `es` | database | `also_requires="view-database"`; denied when the `default_allow_sql` setting is off |
| `execute-write-sql` | `ews` | database | `also_requires="view-database"` |
| `create-table` | `ct` | database | |
| `store-query` | `sq` | database | `also_requires="execute-sql"` |
| `view-table` | `vt` | table | |
| `insert-row` | `ir` | table | |
| `delete-row` | `dr` | table | |
| `update-row` | `ur` | table | |
| `alter-table` | `at` | table | |
| `set-column-type` | `sct` | table | |
| `drop-table` | `dt` | table | |
| `view-query` | `vq` | query | default-allow; private queries restricted to their owner |
| `update-query` | `uq` | query | query owner allowed by default (source=`user` only) |
| `delete-query` | `dq` | query | query owner allowed by default (source=`user` only) |
