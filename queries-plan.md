# Queries in the internal database

Plan for <https://github.com/simonw/datasette/issues/2735>.

## Goal

Move named query definitions into Datasette's internal database, so hundreds or thousands of queries can be listed, searched, permission-filtered, managed, and executed efficiently.

Terminology change: these are now "queries", not "canned queries". Legacy code and documentation can mention the old name only when describing compatibility or migration.

## Decisions so far

- Internal table name: `queries`.
- Query definitions should use real columns, not a JSON blob for all options.
- Query parameter names live in a `parameters` text column as a JSON array. No default values for parameters in this pass.
- No `queries_database_is_published_idx` index.
- User-created queries require `execute-sql` and `insert-query` on the database. Writable queries additionally require matching table write permissions discovered by `Database.analyze_sql()`.
- `publish-query` is the permission for creating or updating a query so users without `execute-sql` can execute it.
- Add `update-query` and `delete-query`, so administrators can manage queries created by other users.
- Remove the old `canned_queries()` hook from core. If we want compatibility later, build a separate `datasette-old-canned-queries` plugin.
- Writable user-created queries can be supported using `Database.analyze_sql()`, provided we fail closed when analysis cannot prove the required permissions.

## Current shape

- Query definitions currently come from `datasette.yaml` or the `canned_queries()` plugin hook.
- `Datasette.get_canned_queries(database_name, actor)` calls that hook every time it needs query definitions.
- `QueryResource.resources_sql()` currently enumerates databases and calls the hook for each one, because permissions and `/-/jump` need query resources.
- Query pages execute if the actor has `view-query` for `QueryResource(database, query)`.
- Arbitrary SQL executes if the actor has `execute-sql` for `DatabaseResource(database)`.

The main performance and architecture win is making query resource enumeration a direct SQL query against the internal database.

## Proposed internal schema

Start with one `queries` table.

```sql
CREATE TABLE IF NOT EXISTS queries (
    database_name TEXT NOT NULL,
    name TEXT NOT NULL,
    sql TEXT NOT NULL,
    title TEXT,
    description TEXT,
    description_html TEXT,
    options TEXT NOT NULL DEFAULT '{}',
    parameters TEXT NOT NULL DEFAULT '[]',
    is_write INTEGER NOT NULL DEFAULT 0 CHECK (is_write IN (0, 1)),
    is_published INTEGER NOT NULL DEFAULT 0 CHECK (is_published IN (0, 1)),
    source TEXT NOT NULL DEFAULT 'user',
    owner_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (database_name, name),
    CHECK (is_write = 0 OR is_published = 0)
);

CREATE INDEX IF NOT EXISTS queries_owner_idx
    ON queries(owner_id);
```

Column notes:

- `database_name`, `name`, and `sql` are the routing and execution core.
- Display fields become columns: `title`, `description`, and `description_html`.
- Less common presentation and writable-query behavior lives in `options`, stored as a JSON object. That covers `hide_sql`, `fragment`, `on_success_message`, `on_success_message_sql`, `on_success_redirect`, `on_error_message`, and `on_error_redirect`.
- `parameters` is a JSON array of parameter names, stored as text. This preserves explicit parameter order, but does not support labels or default values.
- Existing writable query behavior gets `is_write` as a column. Success/error messages, success/error redirects, and `on_success_message_sql` are stored in `options`.
- `is_published` only applies to read-only queries. A writable query can still be public through explicit `view-query` permissions, but the "publish for users without execute-sql" shortcut should be read-only.
- `source` distinguishes `user`, `config`, and `plugin` rows.
- `owner_id` is the actor id for user-created rows. It is `NULL` for config/plugin rows.

No separate index is needed on `(database_name, name)` because the primary key already creates one. Do not add a `queries_database_is_published_idx` index for now.

`QueryResource.resources_sql()` can become:

```sql
SELECT q.database_name AS parent, q.name AS child
FROM queries q
JOIN catalog_databases cd ON cd.database_name = q.database_name
```

The join keeps persisted queries for detached databases from appearing as live resources.

## Config and plugin migration

`datasette.yaml` can continue to support `databases: {db}: queries:` blocks, but core should import them directly into the internal `queries` tables at startup:

1. Ensure the internal schema exists.
2. Delete previous `source='config'` rows.
3. Read configured query blocks for each live database.
4. Normalize string definitions to `{"sql": ...}`.
5. Insert rows into `queries`, storing explicit `params` as JSON in `parameters`.

Plugins should move to:

```python
await datasette.add_query(...)
await datasette.remove_query(...)
```

Remove the old `canned_queries()` hookspec and all core calls to it. If compatibility is needed, build `datasette-old-canned-queries` later as a plugin that restores the hook and imports old hook results using `datasette.add_query()`.

## Permission model

Add core actions:

- `insert-query`, database-level, for creating queries in a database.
- `publish-query`, database-level, for marking read-only queries as executable by actors who lack `execute-sql`.
- `update-query`, query-level, for modifying existing query definitions.
- `delete-query`, query-level, for deleting existing query definitions.

User-created query creation requires:

- `execute-sql` on `DatabaseResource(database)`
- `insert-query` on `DatabaseResource(database)`
- If analysis shows the query is writable, the table-level write permissions described in the writable query section.

Setting `is_published=1` requires:

- `publish-query` on `DatabaseResource(database)`
- The query must be read-only according to `Database.analyze_sql()`.

Updating an existing query requires:

- `update-query` on `QueryResource(database, query)` or default owner permission for a user-owned row.
- If the SQL changes, also require `execute-sql` on the database.
- If the changed SQL is writable, also require the table-level write permissions described in the writable query section.
- If `is_published` changes from `0` to `1`, also require `publish-query` on the database.

Deleting an existing query requires:

- `delete-query` on `QueryResource(database, query)` or default owner permission for a user-owned row.

Default owner permissions:

- For `source='user' AND owner_id = actor.id`, grant `update-query` and `delete-query`.
- Do not automatically grant execution if the user no longer has the execution permission described below.

## Executing queries

Default execution rule for read-only queries:

- If `is_published=0`, the actor needs `execute-sql` on the database.
- If `is_published=1`, the actor can execute the query without `execute-sql`.

Default execution rule for user-created writable queries:

- `is_published` must be `0`.
- The actor must have `view-query`.
- The actor must currently have every write permission required by fresh `Database.analyze_sql()` results for the query SQL.

Implementation:

- Remove `view-query` from the broad `DEFAULT_ALLOW_ACTIONS` set.
- Replace it with query-aware default `view-query` permission SQL.
- For `is_published=1 AND is_write=0`, emit a child-level `view-query` allow.
- For `is_published=0 AND is_write=0`, emit child-level `view-query` allows for queries whose parent database is in the actor's `execute-sql` allowed resources.
- For `is_write=1 AND source='user'`, emit `view-query` only for the owner or actors with explicit `view-query` permission, then have `QueryView` perform the fresh analysis/table-permission check before execution.
- For trusted writable queries, preserve current behavior by emitting child-level `view-query` allows for `is_write=1 AND source IN ('config', 'plugin')` when Datasette is not running with `--default-deny`.

For read-only queries this keeps `QueryView` simple: it checks `view-query` for the query resource, and the default permission hook encodes the relationship with `execute-sql`. User-created writable queries need one additional runtime permission check because their required table permissions are derived from fresh SQL analysis.

Explicit deny rules should still be able to block a published query.

## Writable queries

Writable user-created queries should be in scope, guarded by `Database.analyze_sql()`.

The secure rule: a user can create, update, or execute a writable user-created query only if they currently have the corresponding write permissions for every table the SQL can affect.

`Database.analyze_sql(sql, params=None)` runs the SQL through SQLite's authorizer on an isolated connection and returns a `SQLAnalysis` object containing `SQLTableAccess` rows:

- `operation`: `read`, `insert`, `update`, or `delete`
- `database`: Datasette database name for `main`, or SQLite schema name where no Datasette mapping exists
- `table`: affected table or view
- `columns`: read/updated columns where SQLite reports them
- `source`: trigger/view/CTE source when SQLite reports one

Validation flow for user-created queries:

1. Derive named parameters from the SQL and pass harmless placeholder values into `db.analyze_sql()` so SQLite can prepare statements with bindings.
2. If analysis raises a SQLite error, reject the query.
3. If every table access is `read`, treat the query as read-only and require `execute-sql` plus `insert-query`/`update-query` as described above.
4. If any table access is `insert`, `update`, or `delete`, treat the query as writable and force `is_published=0`.
5. Reject writable user-created queries that access a database other than the database they are being saved against, until `analyze_sql()` can reliably map attached SQLite schemas back to Datasette database names.
6. For every write access returned by analysis, require the corresponding permission on `TableResource(access.database, access.table)`:
   - `insert` -> `insert-row`
   - `update` -> `update-row`
   - `delete` -> `delete-row`
7. Include write accesses reported from triggers and views, since those are real side effects.
8. Re-run the same analysis and permission checks when SQL changes through `update_query()` or `POST .../-/update`.
9. Re-run analysis before executing user-created writable queries, so schema or trigger changes cannot leave a previously saved query with stale permission assumptions.

The user-facing API should not trust a submitted `is_write` value. It should derive `is_write` from analysis.

Trusted configuration and plugin code can still call `datasette.add_query(..., is_write=True, ...)`. Those are treated as deployment/admin-authored queries. They keep the existing execution model: they require `view-query`, and the default `view-query` hook should preserve current default-open behavior for trusted writable queries while still respecting `--default-deny`.

Fail closed cases for user-created writable queries:

- Analysis fails.
- Analysis reports any write operation that cannot be mapped to a Datasette table resource.
- Analysis reports writes outside the target database.
- The actor lacks any required table write permission.
- `is_published=1` is requested.

This gives us writable user-created queries without letting `execute-sql` alone become a path to create arbitrary write endpoints.

## HTTP API sketch

JSON endpoints should follow Datasette's existing write API style: use `POST` plus action paths such as `/-/insert`, `/-/update`, and `/-/delete`, not HTTP `PATCH` or `DELETE`.

Endpoints:

- `GET /{database}/-/queries` lists query definitions the actor can view or manage, probably paginated.
- `POST /{database}/-/queries/-/insert` creates a query.
- `GET /{database}/{query}/-/definition` returns one query definition without executing it.
- `POST /{database}/{query}/-/update` updates one query.
- `POST /{database}/{query}/-/delete` deletes one query.

Create request:

```json
{
  "query": {
    "name": "top_customers",
    "sql": "select * from customers order by revenue desc limit 20",
    "title": "Top customers",
    "description": "Highest revenue customers",
    "is_published": false,
    "parameters": ["region"]
  }
}
```

Successful create returns `201` and the created query definition:

```json
{
  "ok": true,
  "query": {
    "database": "fixtures",
    "name": "top_customers",
    "sql": "select * from customers order by revenue desc limit 20",
    "title": "Top customers",
    "description": "Highest revenue customers",
    "is_published": false,
    "parameters": ["region"]
  }
}
```

Update request, imitating `RowUpdateView`:

```json
{
  "update": {
    "title": "Top customers by revenue",
    "is_published": true
  },
  "return": true
}
```

Successful update returns `{"ok": true}` by default. With `"return": true`, return the updated query definition:

```json
{
  "ok": true,
  "query": {
    "database": "fixtures",
    "name": "top_customers",
    "sql": "select * from customers order by revenue desc limit 20",
    "title": "Top customers by revenue",
    "is_published": true
  }
}
```

Delete request:

```http
POST /{database}/{query}/-/delete
Content-Type: application/json
```

Successful delete returns:

```json
{
  "ok": true
}
```

Validation:

- Update bodies must be dictionaries containing an `update` dictionary, with optional `return`; invalid keys return `{"ok": false, "errors": [...]}`.
- Validate route-safe query names.
- Reject names that collide with a table or view in the same database, since table routes currently win over query routes.
- Analyze user-created SQL with `Database.analyze_sql()`.
- Use `validate_sql_select(sql)` as the read-only fast path when analysis shows only reads, but do not require it for writable queries that pass analysis and permission checks.
- Reject magic parameters such as `:_actor_id`, `:_cookie_*`, and `:_header_*` for user-created queries.
- Reject client-supplied `is_write`; derive it from analysis.
- Reject writable-only success/error fields for read-only queries.

## Python API sketch

Add methods on `Datasette`:

```python
await datasette.add_query(
    database,
    name,
    sql,
    title=None,
    description=None,
    description_html=None,
    hide_sql=False,
    fragment=None,
    parameters=None,
    is_write=False,
    is_published=False,
    source="plugin",
    owner_id=None,
    on_success_message=None,
    on_success_message_sql=None,
    on_success_redirect=None,
    on_error_message=None,
    on_error_redirect=None,
    replace=True,
)

await datasette.update_query(
    database,
    name,
    *,
    sql=UNCHANGED,
    title=UNCHANGED,
    description=UNCHANGED,
    description_html=UNCHANGED,
    hide_sql=UNCHANGED,
    fragment=UNCHANGED,
    parameters=UNCHANGED,
    is_write=UNCHANGED,
    is_published=UNCHANGED,
    source=UNCHANGED,
    owner_id=UNCHANGED,
    on_success_message=UNCHANGED,
    on_success_message_sql=UNCHANGED,
    on_success_redirect=UNCHANGED,
    on_error_message=UNCHANGED,
    on_error_redirect=UNCHANGED,
)

await datasette.remove_query(database, name, source=None)

await datasette.get_query(database, name)
await datasette.get_queries(database)
```

`update_query()` should use an internal sentinel default such as `UNCHANGED = object()` so callers can distinguish "leave this column alone" from "set this column to `NULL`":

```python
await datasette.update_query(
    "fixtures",
    "top_customers",
    on_success_redirect=None,
)
```

For column-backed fields, `None` should write SQL `NULL`. For option fields, `None` should remove that key from the JSON object so `get_query()` returns `None`; omitting the field should leave the existing option unchanged.

Implementation detail: build the `UPDATE` statement dynamically from fields whose value is not `UNCHANGED`, validate non-nullable fields before writing, and update `updated_at` whenever at least one field changes.

The read methods should reconstruct the existing dictionary shape used by query execution and templates, with `name`, `sql`, display fields, write fields, `params`, `is_published`, `owner_id`, and `source`. `parameters` should be returned as the decoded JSON array and exposed as `params` where existing query execution code expects that key. Option values should be unpacked from the `options` JSON object and returned as the same top-level keys accepted by `add_query()` and `update_query()`.

## Query page save UI

On `/{database}/-/query`, if the actor has both `execute-sql` and `insert-query`, show a save control for valid read-only SQL. That page already executes read-only arbitrary SQL, so the first UI can stay read-only even though the JSON API can accept writable SQL after `Database.analyze_sql()` validation.

The save form should call `POST /{database}/-/queries/-/insert` and default to `is_published=false`.

If the actor also has `publish-query`, include a publish control. The UI copy should make it clear that publishing allows people without arbitrary SQL permission to run this query.

## Dedicated create query UI

Add `/{database}/-/queries/-/create` for the fuller query authoring flow, including writable queries.

This page should require `execute-sql` and `insert-query` to access. It should provide a SQL editor and a mode control:

- Read-only
- Writable

Read-only mode can share the same fields as the arbitrary SQL save flow: name, title, description, parameters, and optional published status if the actor has `publish-query`.

Writable mode should always run `Database.analyze_sql()` and show an analysis panel before saving:

- detected operation
- database and table
- required permission
- whether the actor has that permission
- source, when the operation comes from a trigger or view

The Save button should be disabled until analysis succeeds and every required table write permission is allowed. Writable mode should not show a publish control, because user-created writable queries cannot be published.

The existing edit-SQL flow from query pages can continue to point back to arbitrary SQL. A later enhancement can add "update this query" when the actor owns it or has `update-query`.

## Test plan

- Internal schema creates `queries`.
- Query parameters are stored in the `queries.parameters` text column as a JSON array of names.
- Config `queries:` blocks import into internal tables.
- Legacy string query definitions normalize to SQL rows.
- The old `canned_queries()` hook is no longer called by core.
- `QueryResource.resources_sql()` returns rows from `queries`.
- Database page and `/-/jump` list queries from the internal DB.
- `view-query` is no longer globally default-allowed; default query permissions come from the query-aware hook.
- Unpublished read-only query requires `execute-sql` to execute.
- Published read-only query can be executed without `execute-sql`.
- Setting `is_published=true` requires `publish-query`.
- User-created query requires both `execute-sql` and `insert-query`.
- User-created writable query creation uses `Database.analyze_sql()` and requires matching `insert-row`, `update-row`, and/or `delete-row` permissions for every reported write access.
- `/{database}/-/queries/-/create` provides the writable-query authoring UI with an analysis panel and disabled save until all required write permissions pass.
- User-created writable query execution re-runs `Database.analyze_sql()` and re-checks table write permissions.
- User-created writable query cannot be published.
- Query update uses `POST /{database}/{query}/-/update` with an `{"update": {...}}` body.
- Query delete uses `POST /{database}/{query}/-/delete`.
- There are no `PATCH` or HTTP `DELETE` routes for query management.
- `datasette.update_query(..., field=None)` writes `NULL` for column-backed fields and removes JSON keys for option fields, while omitted fields are left unchanged.
- Owner gets default `update-query` and `delete-query` for their own user-created rows.
- Admin can manage other users' queries with `update-query` and `delete-query`.
- User API rejects magic parameters.
- User API rejects writable queries if analysis fails, reports writes outside the target database, or reports writes the actor is not allowed to perform.
- Trusted config/plugin writable queries still execute through `view-query`.
- Trusted config/plugin writable queries are not default-allowed under `--default-deny`.
- Persisted internal DB does not expose queries for detached databases.
