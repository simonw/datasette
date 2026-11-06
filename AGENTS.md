# Instructions for AI Agents Working on Datasette

This document outlines key security principles, architecture conventions, and patterns to audit when contributing code to Datasette.

Whenever developing features, bug fixes, or reviewing PRs, verify that proposed changes do not introduce regressions in these security-sensitive areas.

---

## 1. Permissions and Access Control

Datasette uses a granular permissions system to protect databases, tables, queries, and rows.

### Case-Insensitive SQLite Identifiers
* SQLite treats unquoted table and view names case-insensitively in SQL execution.
* Permission checks must account for case variance (e.g. normalize table names or ensure permission rules check against canonical/case-insensitive identifiers). A request for `SECRET` must not bypass a permission rule configured for `secret`.

### Whitespace and Trailing Newline Normalization
* URL paths and query parameters representing table or column names must be sanitized and stripped of trailing newlines or whitespace (`\n`, `\r`, `\t`).
* A requested table name like `private_table\n` must never bypass permission checks while still resolving against the underlying SQLite table (`GHSA-h547-rmjf-5m2m`).

### Virtual Table Shadow Tables
* Virtual tables (such as FTS4 / FTS5 full-text search indexes) generate shadow tables (e.g., `<table_name>_fts`, `<table_name>_fts_content`, `<table_name>_fts_data`, `<table_name>_fts_idx`).
* Viewing or querying an FTS index or shadow table must require `view-table` permission on the originating base table.

### Internal SQLite Tables
* Direct access to SQLite internal and statistics tables (e.g. `sqlite_stat1`, `sqlite_stat2`, `sqlite_stat3`, `sqlite_stat4`, `sqlite_master`, `sqlite_sequence`) must be denied by default or gated behind administrative permissions.

### Secondary Disclosure & Indirect Access
Permission checks must not only protect the primary table endpoint, but also secondary mechanisms that expose data:
* **Schema introspection:** Displaying table schema, column lists, and DDL must check `view-table`.
* **Row counts & facet suggestions:** Gated by `view-table`.
* **Foreign key suggestions:** Target tables, suggestion APIs, and incoming relationship counts must verify `view-table` permission on target tables.
* **Filter joins (`?_through=`):** Must require `view-table` on intermediate and join tables.

### Timing & Existence Oracles
* Do not reveal whether a hidden row or private primary key exists through differential status codes (e.g. returning `403 Forbidden` if it exists vs `404 Not Found` if it does not).
* Validate permissions before checking whether a record exists.

---

## 2. SQL Construction and Identifier Escaping

Datasette frequently constructs dynamic SQL for sorting, filtering, facets, and introspection.

### Mandatory Parameterization
* User-provided values must **never** be formatted or concatenated directly into SQL query strings.
* Always use SQLite parameter substitution (`?` or `:name`).

### Identifier Escaping
* All table, column, view, and index identifiers included dynamically in SQL statements must be escaped using `datasette.utils.escape_sqlite(identifier)`.
* Do not assume column or table names are alphanumeric. They may contain spaces, quotes, closing brackets (`]`), or Unicode characters.

### FTS Tokenizers and Configuration
* Custom arguments passed to SQLite extensions or PRAGMAs (such as `enable_fts(..., tokenize=...)`) must be safely validated and quoted to prevent SQL injection.

---

## 3. Cross-Site Scripting (XSS) and Output Sanitization

### Untrusted Database Schemas and Metadata
* Schema metadata (column names, table names, view definitions, constraint names) can originate from untrusted SQLite databases uploaded or mounted by users.
* Treat schema names and identifiers as untrusted text: ensure proper HTML escaping in templates and UI responses.

### Avoid Bypassing Auto-Escaping
* Jinja2 auto-escaping is active by default. Never use the `|safe` filter on user-controlled or database-derived content without strict sanitization.

### Link and URL Rendering
* When auto-linking URL column values in HTML views, only allow `http://` and `https://` schemes. Reject `javascript:`, `data:`, and other URI schemes.

---

## 4. HTTP Headers, Caching, and Authentication

### Cache-Control on Dynamic Responses
* Any endpoint returning private or personalized data must send `Cache-Control: private, no-store`.
* Anonymous dynamic responses should include appropriate `Vary: Cookie, Authorization` headers if content depends on authentication status.

### Framing Protection (Clickjacking)
* Sensitive actions, including forms for creating, editing, or deleting queries and tables, must block framing using `X-Frame-Options` or CSP `frame-ancestors`.

### Secret Redaction
* Configuration inspection endpoints (e.g. `/-/settings` or plugin configurations) must redact sensitive keys case-insensitively (e.g., matching `api_key`, `API_KEY`, `secret`, `token`).

### Request Body Caps
* Endpoints that read complete POST bodies into memory must enforce size limits (e.g., `max_post_body_bytes`) and return `413 Payload Too Large` to prevent denial-of-service via memory exhaustion.

---

## 5. Security Checklist for Agents

Before submitting a PR to Datasette:
1. [ ] Does this endpoint check permissions using `await datasette.ensure_permission(...)`?
2. [ ] Are table and column identifiers escaped using `escape_sqlite(...)`?
3. [ ] Are all query parameters passed as SQL parameters (`?`) rather than f-strings?
4. [ ] Are table names stripped of whitespace/newlines and normalized against SQLite case insensitivity?
5. [ ] Does this change avoid leaking private table data through schema introspection, facets, or foreign keys?
6. [ ] Are HTML outputs properly escaped without unvetted `|safe` filters?
7. [ ] Are unit tests included that verify both authorized and unauthorized access behaviors?
