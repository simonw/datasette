# Datasette Metadata System Investigation

## 1. How the Metadata System Works End-to-End

### Overview

Datasette's metadata system provides descriptive information about instances, databases, tables, and columns. Metadata can come from YAML/JSON files on disk and is ultimately stored in four SQLite tables inside an **internal database** (`__INTERNAL__`). Plugins can read and write these tables at runtime via the `datasette` object.

### The Full Lifecycle

```
CLI startup (metadata.yaml/json)
         │
         ▼
  Datasette.__init__()              (app.py:389-416)
  ├── Parse file via parse_metadata()
  ├── move_plugins_and_allow()      ← extracts plugins/allow → config
  ├── move_table_config()           ← extracts table config keys → config
  └── Store remainder in self._metadata_local (in-memory dict)
         │
         ▼
  AsgiRunOnFirstRequest on_startup  (app.py:2143)
  ├── setup_db()                    ← table counts for immutable DBs
  └── invoke_startup()              (app.py:657-701)
      ├── register_events hooks
      ├── register_actions hooks
      ├── prepare_jinja2_environment hooks
      └── startup hooks              ← PLUGINS RUN HERE
         │
         ▼
  First HTTP request arrives
  └── BaseView.dispatch_request()   (views/base.py:130-132)
      └── await self.ds.refresh_schemas()
          └── _refresh_schemas()    (app.py:602-640)
              ├── init_internal_db()       ← creates the 4 metadata tables
              ├── apply_metadata_json()    ← populates tables from _metadata_local
              └── populate_schema_tables() ← catalogs all DBs/tables/columns
         │
         ▼
  Views call get_*_metadata()       (app.py:840-891)
  └── SELECT from internal DB tables → dict returned to templates/JSON
```

### Key Source Locations

| Component | File | Lines |
|-----------|------|-------|
| SETTINGS tuple | `datasette/app.py` | 169-259 |
| `Datasette.__init__()` | `datasette/app.py` | 303-478 |
| `apply_metadata_json()` | `datasette/app.py` | 537-566 |
| `_refresh_schemas()` | `datasette/app.py` | 602-640 |
| `invoke_startup()` | `datasette/app.py` | 657-701 |
| `get_*_metadata()` methods | `datasette/app.py` | 840-891 |
| `set_*_metadata()` methods | `datasette/app.py` | 893-944 |
| Internal DB schema | `datasette/utils/internal_db.py` | 75-106 |
| Migration functions | `datasette/utils/__init__.py` | 1401-1470 |
| ASGI app wiring | `datasette/app.py` | 2110-2146 |

---

## 2. Internal Database Schema

Four tables are created in the internal SQLite database (`datasette/utils/internal_db.py:75-106`):

```sql
CREATE TABLE IF NOT EXISTS metadata_instance (
    key text,
    value text,
    unique(key)
);

CREATE TABLE IF NOT EXISTS metadata_databases (
    database_name text,
    key text,
    value text,
    unique(database_name, key)
);

CREATE TABLE IF NOT EXISTS metadata_resources (
    database_name text,
    resource_name text,
    key text,
    value text,
    unique(database_name, resource_name, key)
);

CREATE TABLE IF NOT EXISTS metadata_columns (
    database_name text,
    resource_name text,
    column_name text,
    key text,
    value text,
    unique(database_name, resource_name, column_name, key)
);
```

All values are stored as **text strings**. Complex values (lists, dicts) are JSON-encoded via a `_to_string()` helper before storage.

All `set_*_metadata()` methods use SQLite **upsert** (`ON CONFLICT ... DO UPDATE SET value = excluded.value`), so later writes to the same key silently overwrite earlier ones.

---

## 3. How Plugin-Set Metadata Interacts with File Metadata

### The Critical Ordering

This is the most important architectural detail:

1. **`invoke_startup()` runs FIRST** — this is where the `startup` plugin hook fires (app.py:699)
2. **`refresh_schemas()` runs LATER** — only on the first HTTP request (views/base.py:132), which calls `apply_metadata_json()` (app.py:606)

This means:

- If a plugin calls `await datasette.set_instance_metadata("title", "Plugin Title")` in its `startup` hook, the internal DB tables **may not even exist yet** at that point (they are created in `init_internal_db()` which runs inside `_refresh_schemas()`).
- When the first request arrives and `apply_metadata_json()` runs, it uses **upsert** semantics. So if a metadata.yaml file has `title: "File Title"`, it will **overwrite** any value a plugin set during startup for the same key.

### Resolution Order (Last Write Wins)

Since the internal DB uses upserts, the resolution is simple: **last write wins**.

The actual execution order is:

```
1. startup hook fires          → plugin can call set_*_metadata()
                                  BUT internal DB tables may not exist yet!
2. First request arrives
3. init_internal_db()          → creates the metadata tables
4. apply_metadata_json()       → writes ALL keys from metadata file (upserts)
5. Views read via get_*_metadata() → sees file values, plugin values lost
```

**Therefore: metadata from files will overwrite metadata set by plugins during startup, because `apply_metadata_json()` runs after `startup` hooks and uses upsert.**

### If a Plugin Writes Metadata AFTER Startup

If a plugin writes metadata in response to a request (e.g., in a view or hook that runs after the first `refresh_schemas()`), then:

- The plugin's write will succeed (tables exist)
- The value will persist until the next `apply_metadata_json()` call
- `apply_metadata_json()` only runs once (`self.internal_db_created` flag at app.py:607 prevents re-runs), so the plugin's value will **stick** for the lifetime of the server

### Practical Implications

- **Plugins wanting to set metadata should NOT use the `startup` hook** for this purpose, because the internal DB tables haven't been created yet at that point.
- **Plugins wanting to override file metadata** should use a hook that runs after the first request (or explicitly call `refresh_schemas()` first, then write).
- **There is no dedicated metadata hook** — no `get_metadata`, `metadata_updated`, or similar hookspec exists. Plugins interact with metadata solely through the `datasette.set_*_metadata()` / `datasette.get_*_metadata()` methods.
- **The internal database is ephemeral by default** (in-memory). If `--internal` is passed to specify a file path, metadata persists across restarts. Otherwise, it's rebuilt from the metadata file every time.

---

## 4. Config vs Metadata: Complete Separation

### Historical Context

Before Datasette 1.0, **everything** lived in `metadata.yaml` — settings, plugin config, permissions, AND descriptive metadata. Starting in 1.0alpha, these were separated:

| Version | Change |
|---------|--------|
| 1.0a5 (Aug 2023) | Introduced `datasette.yaml` config file with `settings` section |
| 1.0a8 (Feb 2024) | Moved `plugins` and `allow` from metadata → config; backward-compat migration |
| 1.0a14 (Aug 2024) | Moved metadata storage from in-memory dict to internal database tables |

### Current State

**Metadata** (`-m` / `--metadata` flag, or `metadata.yaml/json` in config dir):
- **Purpose:** Describes *what the data is* — titles, descriptions, licenses, sources
- **Storage:** Internal SQLite database tables (4 tables)
- **Access:** `datasette.get_*_metadata()` async methods

**Config** (`-c` / `--config` flag, or `datasette.yaml/json` in config dir):
- **Purpose:** Controls *how Datasette behaves* — settings, plugins, permissions
- **Storage:** In-memory Python dict (`self.config`) and flattened settings (`self._settings`)
- **Access:** `datasette.config`, `datasette.setting("key")`

### Automatic Migration (Backward Compatibility)

Two functions in `datasette/utils/__init__.py` silently migrate old-style metadata into config during `__init__()`:

**`move_plugins_and_allow()`** (line 1401): Extracts `plugins` and `allow` keys at all levels (instance, database, table) from metadata → config.

**`move_table_config()`** (line 1449): Extracts these 10 table-level keys from metadata → config:
- `hidden`, `sort`, `sort_desc`, `size`, `sortable_columns`
- `label_column`, `facets`, `fts_table`, `fts_pk`, `searchmode`

This means old `metadata.yaml` files that mix everything together still work — the migration happens silently at startup.

---

## 5. Complete List of All Config Settings

### Settings (23 total)

Defined in `datasette/app.py:169-259` as the `SETTINGS` tuple. These go under `settings:` in `datasette.yaml`.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `default_page_size` | int | 100 | Default page size for the table view |
| `max_returned_rows` | int | 1000 | Maximum rows returned from a table or custom query |
| `max_insert_rows` | int | 100 | Maximum rows in bulk insert API |
| `num_sql_threads` | int | 3 | Thread pool size for SQLite queries |
| `sql_time_limit_ms` | int | 1000 | SQL query timeout in milliseconds |
| `default_facet_size` | int | 30 | Number of values to return for requested facets |
| `facet_time_limit_ms` | int | 200 | Time limit for calculating a requested facet |
| `facet_suggest_time_limit_ms` | int | 50 | Time limit for calculating a suggested facet |
| `allow_facet` | bool | True | Allow `?_facet=` parameter |
| `allow_download` | bool | True | Allow database file download |
| `allow_signed_tokens` | bool | True | Allow API token creation |
| `default_allow_sql` | bool | True | Allow arbitrary SQL execution |
| `max_signed_tokens_ttl` | int | 0 | Max API token expiry (0 = no limit) |
| `suggest_facets` | bool | True | Calculate and display suggested facets |
| `default_cache_ttl` | int | 5 | HTTP cache TTL in seconds |
| `cache_size_kb` | int | 0 | SQLite cache size in KB (0 = SQLite default) |
| `allow_csv_stream` | bool | True | Allow `?_stream=1` CSV download |
| `max_csv_mb` | int | 100 | Max CSV export size in MB (0 = no limit) |
| `truncate_cells_html` | int | 2048 | Truncate HTML cells (chars, 0 = disable) |
| `force_https_urls` | bool | False | Force https:// in API output |
| `template_debug` | bool | False | Allow `?_context=1` template debug |
| `trace_debug` | bool | False | Allow `?_trace=1` SQL trace debug |
| `base_url` | str | "/" | Base path for all Datasette URLs |

**Obsolete settings** (app.py:261-264):
- `hash_urls` → removed, use `datasette-hashed-urls` plugin
- `default_cache_ttl_hashed` → removed, same reason

### Settings Merge Order (app.py:473)

```python
self._settings = dict(DEFAULT_SETTINGS, **(config_settings), **(settings or {}))
```

Priority: **Defaults < Config file < CLI `--setting` flags**

### Other Config Keys (in `datasette.yaml`)

Beyond `settings:`, the config file supports:

```yaml
settings:
  default_page_size: 50
  sql_time_limit_ms: 3500

plugins:
  datasette-my-plugin:
    key: value

allow:
  id: [user1, user2]

extra_css_urls:
  - url: https://example.com/style.css
    sri: sha384-...

extra_js_urls:
  - url: https://example.com/script.js
    module: true

databases:
  my_database:
    plugins:
      datasette-my-plugin:
        key: database_value
    allow:
      id: admin_user
    queries:
      my_query:
        sql: SELECT * FROM table
    tables:
      my_table:
        allow:
          id: viewer
        plugins:
          datasette-my-plugin:
            key: table_value
        hidden: false
        sort: id
        sort_desc: false
        size: 50
        sortable_columns: [id, name]
        label_column: name
        facets: [category]
        fts_table: fts_my_table
        fts_pk: id
        searchmode: raw
```

---

## 6. Complete List of All Metadata Keys

These go in `metadata.yaml` and are stored in the internal database tables.

### Instance-level (top-level in metadata file → `metadata_instance` table)

| Key | Description |
|-----|-------------|
| `title` | Custom title for the Datasette instance |
| `description` | Plain text description |
| `description_html` | HTML-formatted description |
| `license` | License name |
| `license_url` | License URL |
| `source` | Data source name |
| `source_url` | Data source URL |

### Database-level (under `databases.<name>` → `metadata_databases` table)

| Key | Description |
|-----|-------------|
| `source` | Data source name for this database |
| `source_url` | Data source URL |
| `license` | License name |
| `license_url` | License URL |
| `about` | About text for this database |
| `about_url` | About URL |
| `description` | Description of this database |
| `description_html` | HTML description |

### Table/View-level (under `databases.<name>.tables.<table>` → `metadata_resources` table)

| Key | Description |
|-----|-------------|
| `source` | Data source name for this table |
| `source_url` | Data source URL |
| `license` | License name |
| `license_url` | License URL |
| `about` | About text |
| `about_url` | About URL |
| `description` | Table description |
| `description_html` | HTML description |

### Column-level (under `databases.<name>.tables.<table>.columns` → `metadata_columns` table)

| Key | Description |
|-----|-------------|
| `description` | Human-readable description of what this column contains |

### Keys That Were MOVED to Config (No Longer Metadata)

These are automatically migrated from metadata → config at startup:

| Key | Level | Now Lives In |
|-----|-------|-------------|
| `plugins` | instance/database/table | `datasette.yaml` at same level |
| `allow` | instance/database/table | `datasette.yaml` at same level |
| `hidden` | table | `datasette.yaml databases.*.tables.*` |
| `sort` | table | `datasette.yaml databases.*.tables.*` |
| `sort_desc` | table | `datasette.yaml databases.*.tables.*` |
| `size` | table | `datasette.yaml databases.*.tables.*` |
| `sortable_columns` | table | `datasette.yaml databases.*.tables.*` |
| `label_column` | table | `datasette.yaml databases.*.tables.*` |
| `facets` | table | `datasette.yaml databases.*.tables.*` |
| `fts_table` | table | `datasette.yaml databases.*.tables.*` |
| `fts_pk` | table | `datasette.yaml databases.*.tables.*` |
| `searchmode` | table | `datasette.yaml databases.*.tables.*` |

---

## 7. API Endpoints for Inspection

| Endpoint | Source | Content |
|----------|--------|---------|
| `/-/settings.json` | `self._settings` | All 23 settings with current values |
| `/-/config.json` | `self._config()` | Full config (sensitive keys redacted) |
| `/<database>/-/metadata.json` | Internal DB | Database-level metadata |

Config redaction (`app.py:1886`) hides values of keys containing: `secret`, `key`, `password`, `token`, `hash`, `dsn`.

---

## 8. Summary: The Plugin vs File Conflict Answer

**Q: How does Datasette resolve a value set by a plugin if different metadata comes from a file?**

**A: Last write wins, and the file always writes last.**

The execution order is:
1. `startup` plugin hook fires (but internal DB tables may not exist yet)
2. First HTTP request triggers `_refresh_schemas()`
3. `init_internal_db()` creates the metadata tables
4. `apply_metadata_json()` upserts ALL file-sourced metadata into the tables

Since `apply_metadata_json()` uses `ON CONFLICT ... DO UPDATE`, any key that exists in both the file and a plugin's startup write will be overwritten by the file value.

**The only way a plugin can reliably override file metadata** is to write to the internal DB *after* `apply_metadata_json()` has run — i.e., in response to a request or event that occurs after the first `refresh_schemas()` call. Since `apply_metadata_json()` only runs once (guarded by `self.internal_db_created` flag), subsequent plugin writes will persist for the server's lifetime.

There is **no priority system or merge logic** — it is purely a temporal "last write wins" upsert model. There are **no dedicated metadata plugin hooks** (no `get_metadata` hookspec). Plugins must use the `datasette.set_*_metadata()` / `datasette.get_*_metadata()` methods directly.
