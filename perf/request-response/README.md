# Datasette request/response performance on Python 3.15

This directory holds a profiling study of Datasette's core URL handling and
request/response cycle at commit `cf113a5`. It covers routing, middleware,
`Request`/`Response`, cookies and signing, URL building, template plumbing,
static files and error paths. Optimising SQL queries against user data was
out of scope. Per-request work that only *touches* SQLite without being a
data query, such as probing a throwaway `:memory:` connection on every
request, was in scope and is labelled "DB-adjacent".

Everything was measured on **Python 3.15.0b4** (installed with `uv python
install 3.15`). Profiling used the new statistical sampling profiler,
`python -m profiling.sampling` (PEP 799).

**Nothing in `datasette/` is changed by this branch.** The proposed changes
are the patch files in `patches/`, one per optimisation, plus
`patches/ALL-combined.diff`.

## Headline results

`ALL-combined.diff` applies 48 patches to `cf113a5`. With it applied, the
full test suite passes on 3.15: 2869 passed, which is the 2829 baseline
tests plus 40 new ones. Black and ruff are clean.

These timings are in-process: the harness calls the ASGI app directly, with
no HTTP server. The fixtures database is opened mutable (the `datasette
data.db` default) with 3 SQL threads. Each figure is the median of 5
interleaved rounds. Base and patched trees were checked out at the same path
depth.

| Request | Before | After | Change |
|---|---:|---:|---:|
| `GET /favicon.ico` | 897 µs | 31 µs | −97% |
| `GET /-/static/app.css` (83 KB) | 4,141 µs | 147 µs | −96% |
| `GET /-/static/app.css?_hash=…`, the URL every page links to | 4,386 µs | 200 µs | −95% |
| `GET /-/versions` (HTML) | 3,420 µs | 1,011 µs | −70% |
| `GET /-/versions.json` | 1,935 µs | 596 µs | −69% |
| `GET /fixtures/nope/nope/nope/nope` (404 page) | 1,509 µs | 484 µs | −68% |
| `GET /-` (301 redirect) | 120 µs | 39 µs | −67% |
| `GET /-/actor.json` with a `ds_actor` cookie | 206 µs | 80 µs | −61% |
| `GET /-/static/app.css` → 304 (`If-None-Match`) | 274 µs | 120 µs | −56% |
| `GET /fixtures/` (trailing-slash redirect) | 102 µs | 50 µs | −51% |
| `POST /-/actor.json` (405) | 118 µs | 58 µs | −51% |
| `GET /fixtures/facetable.json?_size=5…` | 6,794 µs | 3,635 µs | −46% |
| `GET /fixtures/facetable/1.json` | 3,031 µs | 1,894 µs | −38% |
| `GET /fixtures/compound_three_primary_keys?_size=100` | 48.8 ms | 32.3 ms | −34% |
| `GET /fixtures/facetable?_size=5` (HTML) | 58.2 ms | 43.0 ms | −26% |
| `GET /` (index HTML) | 62.2 ms | 49.8 ms | −20% |
| `GET /…/compound_three_primary_keys.csv?_size=1000` | 21.6 ms | 17.2 ms | −20% |
| `GET /fixtures` (database HTML) | 65.5 ms | 55.7 ms | −15% |
| `GET /.json` | 53.7 ms | 47.5 ms | −11% |

The same fixtures opened as an **immutable** database (`datasette -i`):

| Request | Before | After | Change |
|---|---:|---:|---:|
| `GET /fixtures/facetable.json?…` | 7,300 µs | 1,981 µs | −73% |
| `GET /fixtures/facetable/1.json` | 3,386 µs | 1,094 µs | −68% |
| `GET /fixtures` | 53.3 ms | 23.8 ms | −55% |
| `GET /fixtures/facetable` | 56.5 ms | 28.0 ms | −50% |

For the light endpoints, no patched round was slower than any base round.
The heavy pages (tens of ms, dominated by SQL thread-pool round trips) vary
±10–15% between rounds, so their gains are less precise. The raw per-round
numbers are in `results/`.

End-to-end results under a real `datasette serve` (uvicorn) are
[further down](#end-to-end-under-datasette-serve). There, the HTTP server
layer is a large fixed cost, so the in-process percentages are diluted on
light requests. Fixing the server stack is a lever of the same size.

## Where the time went (baseline)

Share of wall-clock samples on the event-loop thread, from 10 kHz wall-mode
profiles of the in-process harness at `cf113a5` (`results/baseline-profile-breakdown.txt`):

| Request | Biggest costs |
|---|---|
| Cookie-authenticated JSON (`/-/actor.json`) | re-parsing cookies, headers and query string 32%; itsdangerous signing 20%; GC 11%; dispatch (inspect, hooks, telemetry, routing) 10% |
| Anonymous redirect (`/-`) | dispatch 27% (17% is `inspect.signature()`); GC 16%; signing 10%; re-parsing 10% |
| 404 page | Jinja 78%: walking every template directory, plus `static()` calling `Path.resolve()` 5–12 times |
| `/-/versions.json` | SQLite work on the event loop 53%: a new `:memory:` connection and FTS probes on every request |
| `/-/static/app.css` | waiting on the thread pool 81%: one `aiofiles` hop per 4 KB chunk |
| `/fixtures/facetable.json` | waiting on the thread pool 83%: 12 SQL round trips per request, most of them schema introspection |

Most of the cost is re-doing work that never changes. Every access to
`request.cookies` re-parses the Cookie header, and there are three per
request. Every `sign`/`unsign` call builds a new serializer. Every request
unsigns a `ds_messages` cookie that isn't there, which raises an exception
and leaves a reference cycle for the GC. Every view call runs
`inspect.signature()`. Every 404 walks every template directory. Every static
asset is hashed or chunked in ways that cost far more than the bytes. None
of this is new work per request; it is repeated work.

## The proposed changes

Each patch applies on its own to `cf113a5` with `git apply`, unless noted.
They were found and prototyped by investigators working per area, with
interleaved A/B timing, profiler evidence and the relevant tests. Each area
was then reviewed adversarially for correctness, security and plugin
compatibility. Where the reviewer asked for changes, the corrected version
is the one in this directory.

"In-process effect" is from the final sequential run of each area's patches
alone against the same base (`results/inprocess-summary.txt`). Effects
below about ±8% are inside the noise of that run. Smaller wins are
supported by micro-benchmarks and profiler sample shares in the area notes.

### Request object and router — `request-object-*`, `signing-*`

Area effect: cookie-auth JSON −52%, redirect −39%, trailing slash −37%,
POST 405 −44%, static 304 −35%, index HTML −14%.

| Patch | What it does |
|---|---|
| `request-object-1-cache-request-headers` | `Request.headers` decoded every scope header on every access: 4–18 times per request. It is now cached per `Request` against a snapshot of `scope["headers"]`, so it stays correct when `route_path` reassigns `request.scope` or a plugin replaces or appends headers. It still returns a fresh dict. |
| `request-object-2-memoize-cookie-parse` | `Request.cookies` built a new `SimpleCookie` on every access. Parsing is now memoised by Cookie-header string (LRU of 128, headers up to 8 KB only), and a copy is returned. |
| `request-object-3-single-pass-send-wrapper` | The Cache-Control/Vary privacy wrapper in `route_path` built `request.headers` twice and made four passes over the response headers. It now makes one pass. Output was fuzzed as byte-identical. |
| `request-object-4-cache-request-args` | `Request.args` re-ran `parse_qs` 29–34 times per table request. It is now cached per query string, with fresh lists on each access. |
| `request-object-5-skip-absent-ds-messages-unsign` | `route_path` unsigned the `ds_messages` cookie even when absent. `unsign("")` always raises `BadSignature`, and the traceback it left behind formed a reference cycle that triggered GC. (`signing-2` is the same fix; apply only one.) |
| `signing-1-cache-serializer` | `Datasette.sign`/`unsign` built a new `URLSafeSerializer` per call: itsdangerous runs `json.dumps({})` to sniff the serializer and re-derives the key. Serializers are now cached per `(secret, namespace)`. Unhashable secrets, such as key-rotation lists, fall back to an uncached serializer. |
| `signing-3-actor-cookie-single-parse` | `actor_from_request` read `request.cookies` twice. |

### Routing and view dispatch — `routing-*`

Area effect: redirect −17%. `datasette.client` requests are 13–44% faster;
see `routing-2`.

| Patch | What it does |
|---|---|
| `routing-1-cache-view-signatures` | `_gather_arguments` called `inspect.signature()` on every request for every wrapped view and output renderer, and `wrap_view_function` called `inspect.iscoroutinefunction` per request. The parameter names are now cached in a `WeakKeyDictionary`, and the coroutine check moves to wrap time. |
| `routing-2-reuse-default-routes` | `DatasetteClient.app` calls `ds.app()` for **every** `datasette.client` request, which rebuilt about 61 routes and called `get_plugins()`. The default route table is now reused until the plugin registry or `static_mounts` changes. Plugin `register_routes` is still called every time. `ds.app()` drops from 318 to 5 µs. |

### Middleware, OpenTelemetry and GC — `middleware-gc-*`

Area effect: static 304 −11%, trailing slash −10%, index HTML −15%,
index JSON −12%.

| Patch | What it does |
|---|---|
| `middleware-gc-1-telemetry-no-provider-fast-path` | With no OpenTelemetry provider installed, `TelemetryMiddleware` still ran `extract()` and three nested no-op span context managers on every request, about 8 µs. It now skips the span when it would be `INVALID_SPAN` anyway. |
| `middleware-gc-2-skip-noop-db-query-spans` | Two no-op spans per SQL query (`db.query` and `db.query.execute`), about 10 µs of Python per query, which adds up on pages making 70+ queries. (Conflicts textually with patch 1; `ALL-combined.diff` resolves it.) |
| `middleware-gc-3-csrftoken-no-scope-cycle` | The legacy `scope["csrftoken"]` closure captured `scope` and was stored in it, so every request leaked a cycle to the GC. Combined with `request-object-5`, a request that makes no permission checks, such as `/-/actor.json`, now leaves **zero** cyclic garbage, down from 53 objects. Permission checks still leave about 13 each; see the bugs list. |
| `middleware-gc-4-router-single-scope-copy` | One `dict(scope, …)` copy per request instead of two, and a module-level import instead of one per request. |
| `middleware-gc-5-telemetry-header-getter-length-check` | The trace-context header getter lowercased every header name. It now compares lengths first. |
| `middleware-gc-7-tracer-trace-fast-path` | `datasette.tracer.trace()` looked up the asyncio task on every SQL query even with no `?_trace=1`. Decorator use of `@trace(...)` keeps working. |

### Static files — `static-files-*`

Area effect: favicon −92%, `app.css` −94%, `app.css?_hash=` −95%, 404 page
−47%.

| Patch | What it does |
|---|---|
| `static-files-1-cache-hash-param-sha256` | A `?_hash=` request, which is every static URL Datasette's templates emit, ran `sha256_file()` over the whole file **synchronously on the event loop** on every request. The hash is now cached per `(inode, size, mtime_ns, ctime_ns)`. |
| `static-files-2-send-file-fewer-thread-hops` | `asgi_send_file` did a thread-pool hop for stat, open, close and every 4 KB read: about 25 hops and 21 ASGI messages for `app.css`. It now reads files up to 1 MiB in one hop and streams 64 KB chunks above that. |
| `static-files-3-memoize-static-template-urls` | The template `static()` helper resolved paths with `Path.resolve()` (about 50–90 µs) on every call, 5–12 times per HTML page. Successful results are now memoised. |
| `static-files-4-favicon-from-memory` | The favicon was re-read from disk, using 4 thread hops, on every request. |
| `static-files-5-resolve-static-root-once` | The static root was resolved again on every request. The first version of this patch was **refuted** in review, because a symlinked `--static` root re-pointed at runtime broke. This version re-resolves when the root's `(st_dev, st_ino)` changes and adds a regression test. |
| `static-files-6-serve-small-static-files-from-memory` | Files up to 1 MiB are served from a 16 MiB stat-validated in-memory cache, with zero thread hops. It keeps a running byte counter, after review found an O(n) re-sum on every cache miss. |

### Error paths — `error-paths-*`

Area effect: 404 page −20%. `table_json` −8% and permission checks generally
are faster through patch 2.

| Patch | What it does |
|---|---|
| `error-paths-1-list-only-pages-templates` | Every 404 called `Environment.list_templates()`, walking every template directory (the default one twice), to look for `pages/{wildcard}.html`. It now lists only `pages/`, with the same results and order on 19 loader shapes. |
| `error-paths-2-permission-params-no-per-request-regex` | `PermissionSQL.allow()` names each parameter `:reason_N` with a global counter, so `check_permissions_for_actions` compiled a never-seen regex for every permission check. It now does a single-pass rename. |
| `error-paths-3-rich-traceback-only-for-5xx` | **Bug.** With `rich` installed, every 4xx printed a `show_locals=True` traceback, about **0.9 s** per table-not-found 404. Unmatched-route 404s **crashed** with `ValueError` (500), because `print_exception` ran outside an `except` block. Rich tracebacks now print only for 5xx errors, as the plain-Python path already does. |

### Template rendering plumbing — `template-plumbing-*`

Area effect: 100-row table page −16%, table HTML −7%. Render-only, the
100-row table page goes from 13.1 ms to 5.7 ms.

| Patch | What it does |
|---|---|
| `template-plumbing-1-buffer-table-rows-in-templates` | In Jinja's async mode, every output fragment travels through `async for event in gen: yield event` at each `extends`/`block`/`include` level, about 170 ns per level per fragment. A 100-row table yields 6,810 fragments through 4–5 levels. The row loops in `_table.html` and `_query_results.html` are now captured with `{% set %}…{% endset %}`. Output is byte-identical on 47 endpoints. Users' overridden `_table.html` templates are unaffected. |
| `template-plumbing-2-buffer-base-content-block` | Does the same for `base.html`'s content block, inside an empty `{% with %}` so the buffer variable cannot shadow a child template's or plugin's `content_html`. That collision was found in review. |
| `template-plumbing-3-absolute-url-plain-str` | `absolute_url()` passed a `PrefixedUrlString` to `urljoin`, costing about 58 µs per call. |
| `template-plumbing-4-skip-render-cell-hook-without-impls` | `render_cell` was dispatched once per **cell** even with no plugin implementing it. `DATASETTE_TRACE_PLUGINS` output is unchanged. |
| `template-plumbing-5-hoist-table-path-out-of-row-loop` | `urls.table()` was computed once per row. |
| `template-plumbing-6-skip-template-hooks-without-impls` | `render_template` called six hooks with no implementations on every page. This saves about 12 µs. |
| `template-plumbing-7-database-view-reuse-selected-template` | The database view selected its template twice. |

Jinja async mode itself costs 35–43% on a render microbenchmark compared
with sync mode. It cannot be turned off, because templates call async
functions (`crumb_items`, `menu_links`) and the plugin docs promise that
awaitables in template context are auto-awaited.

### URL building — `url-building-*`

Area effect: table JSON −10%. Facet-heavy HTML pages improve 4–19% in the
area's lower-noise harness.

| Patch | What it does |
|---|---|
| `url-building-1-prefixed-url-string-class-methods` | `PrefixedUrlString.__getattribute__` called `dir(str)` and built a closure on **every** attribute access, about 9.5 µs each, and `urljoin` makes about 7 per call. The wrapped methods are now defined once on the class. A property test over 180,000 calls gave identical results. |
| `url-building-2-tilde-encode-safe-fast-path` | `tilde_encode` ran a per-byte generator even for names that need no encoding. A regex fast path was checked identical on 200,000 strings. |
| `url-building-3-tilde-decode-skip-token-when-nothing-to-decode` | `tilde_decode` called `secrets.token_hex(16)`, an `os.urandom` syscall, on every call, including for every database and table name in every URL. |

### Response serialization — `response-serialization-*`

These matter most under a real server, so the in-process harness shows
little.

| Patch | What it does |
|---|---|
| `response-serialization-1-chunk-csv-stream-writes` | CSV sent **one ASGI message per row**. A 20,000-row `_stream=1` CSV was 20,003 messages. It now buffers up to 64K characters, flushed per page. Under uvicorn, a 1,000-row CSV goes from 45 ms to 17 ms and a 20k-row stream from 1.24 s to 0.67 s. Bytes are identical. |
| `response-serialization-2-json-renderer-skip-infinity-scan` | The JSON renderer scanned every value of every row in Python for infinities. It now encodes with `allow_nan=False` and falls back to the old path on `ValueError`. **Behaviour change:** `_shape=arrayfirst` with an infinity in a non-first column used to 500 (AssertionError) and now returns 200. A regression test is included. |
| `response-serialization-3-csv-bytes-check-without-genexpr` | Per-row `any(isinstance(…) for …)` becomes `any(map(…))`. |
| `response-serialization-4-head-skips-csv-generation` | `HEAD` on a CSV endpoint generated and streamed the whole CSV. For `_stream=1` that meant paginating the whole table: 680 ms became 6 ms. Status and headers are identical. |

### DB-adjacent per-request work — `db-adjacent-*`

These are not data queries. They are introspection and probes that ran on
every request. Area effect: `/-/versions.json` −50%, `/-/versions` −27%,
table JSON −22%. On an immutable database, table JSON is −57%, row JSON −45%
and the database page −45%.

| Patch | What it does |
|---|---|
| `db-adjacent-1-cache-versions-probe` | `/-/versions` opened a `:memory:` connection, ran every `prepare_connection` hook and created three FTS virtual tables on **every** request. The result is now cached until the plugin set changes. |
| `db-adjacent-2-memoize-detect-json1` | `detect_json1()` with no connection opened a fresh SQLite connection on every table HTML page. |
| `db-adjacent-3-column-types-skip-introspection` | `get_column_types` ran `PRAGMA table_xinfo` even when no column types were assigned. |
| `db-adjacent-4-reuse-table-columns-for-sort` | `table_columns()` was introspected twice per table request. |
| `db-adjacent-5-row-reuse-resolved-results` | The row page ran the same primary-key `SELECT` twice. |
| `db-adjacent-6-cache-immutable-db-introspection` | For immutable databases the schema cannot change, so introspection is cached from one `sqlite_master` read. Review bounded the cache to real schema names, so junk `?_through=` names cannot fill it. |
| `db-adjacent-7-batch-refresh-schemas-reads` | `refresh_schemas()` made 8 sequential round trips to read the internal catalog. |
| `db-adjacent-8-skip-unused-label-column-lookups` | `table_view_data` looked up a label column for every foreign key, then discarded the result. |

### Found by the completeness critic — `critic-*`

| Patch | What it does |
|---|---|
| `critic-1-permission-sql-stable-param-names` | Because of the `:reason_N` counter, **the SQL text of every permission check was unique**, so SQLite re-prepared it every time: about 170 µs instead of 10 µs on the internal database. Parameters are now named by position. This needs `error-paths-2` applied first. With `error-paths-2` and `critic-2`, the effect was `/-/versions.json` −20%, row JSON −16% and table JSON −18%. |
| `critic-2-table-rows-dict-zip` | `dict(sqlite3.Row)` does a case-insensitive name lookup per column, which is O(columns²) per row. It now uses `zip` when names are unique. |

## End-to-end under `datasette serve`

These runs use a real `datasette serve` on the same 4-vCPU VM. A
stdlib-only keep-alive load generator (`tools/load.py`) runs in separate
processes with 8 connections. Each figure is the median of 5 interleaved
rounds of 6 seconds. "Server CPU per request" is the server process's
user+system CPU from `/proc`, divided by requests served, and is the
steadier of the two metrics. Run it with `tools/e2e_bench.py`.

| Endpoint | Server | req/s | Server CPU per request | vs base |
|---|---|---:|---:|---:|
| `/-/actor.json + cookie` | cf113a5, stock serve (h11, access log) | 1,493 | 659 µs | – |
| `/-/actor.json + cookie` | cf113a5 + httptools | 2,162 | 461 µs | −30% |
| `/-/actor.json + cookie` | cf113a5, no access log | 1,805 | 560 µs | −15% |
| `/-/actor.json + cookie` | All patches, stock serve | 1,954 | 503 µs | −24% |
| `/-/actor.json + cookie` | All patches + httptools | 3,053 | 324 µs | −51% |
| `/-/actor.json + cookie` | All patches + httptools, no access log, Content-Length | 4,379 | 226 µs | −66% |
| `/-/versions.json` | cf113a5, stock serve (h11, access log) | 412 | 2,944 µs | – |
| `/-/versions.json` | cf113a5 + httptools | 446 | 2,761 µs | −6% |
| `/-/versions.json` | cf113a5, no access log | 432 | 2,825 µs | −4% |
| `/-/versions.json` | All patches, stock serve | 723 | 1,478 µs | −50% |
| `/-/versions.json` | All patches + httptools | 847 | 1,304 µs | −56% |
| `/-/versions.json` | All patches + httptools, no access log, Content-Length | 987 | 1,128 µs | −62% |
| `/-/static/app.css` | cf113a5, stock serve (h11, access log) | 166 | 6,954 µs | – |
| `/-/static/app.css` | cf113a5 + httptools | 190 | 6,034 µs | −13% |
| `/-/static/app.css` | cf113a5, no access log | 172 | 6,708 µs | −4% |
| `/-/static/app.css` | All patches, stock serve | 1,765 | 558 µs | −92% |
| `/-/static/app.css` | All patches + httptools | 2,314 | 424 µs | −94% |
| `/-/static/app.css` | All patches + httptools, no access log, Content-Length | 3,117 | 320 µs | −95% |
| `/fixtures/facetable.json?_size=5` | cf113a5, stock serve (h11, access log) | 79 | 15,866 µs | – |
| `/fixtures/facetable.json?_size=5` | cf113a5 + httptools | 80 | 15,952 µs | +1% |
| `/fixtures/facetable.json?_size=5` | cf113a5, no access log | 77 | 16,133 µs | +2% |
| `/fixtures/facetable.json?_size=5` | All patches, stock serve | 133 | 9,044 µs | −43% |
| `/fixtures/facetable.json?_size=5` | All patches + httptools | 139 | 8,713 µs | −45% |
| `/fixtures/facetable.json?_size=5` | All patches + httptools, no access log, Content-Length | 136 | 8,674 µs | −45% |

Latency with a single connection (p50):

| Endpoint | cf113a5 | All patches | All patches + httptools, no access log |
|---|---:|---:|---:|
| `/-/actor.json + cookie` | 681 µs | 582 µs | 334 µs |
| `/-/versions.json` | 2,403 µs | 988 µs | 772 µs |
| `/-/static/app.css` | 5,924 µs | 681 µs | 489 µs |
| `/fixtures/facetable.json?_size=5` | 6,393 µs | 3,832 µs | 3,781 µs |

What this shows:

- **The HTTP server is half the cost of a light request.** Profiling the
  live server in `attach` mode showed uvicorn's layers taking 53–64% of
  server CPU on `/-/actor.json` with stock `datasette serve`: h11 parsing
  and serialising about 20%, socket I/O 15–18%, the access log 8–10%, and
  uvicorn's protocol code 8–11%. Datasette's ASGI app was 26–38% and GC
  5–7%. So the patches' −61% in-process becomes −24% end to end on that
  request. After the patches the HTTP layer is about two-thirds of what is
  left.
- **Datasette runs on pure-Python h11 by default.** It depends on plain
  `uvicorn`, so httptools is never installed. With httptools installed,
  uvicorn's `http="auto"` picks it up with no code change. That alone cut
  server CPU per request by 30% on `/-/actor.json`, or 36% once the patches
  are applied. httptools 0.9.0 and uvloop 0.23.0 have cp315 wheels and
  install cleanly on 3.15.0b4.
- **The uvicorn access log**, which is always on under `datasette serve`,
  costs about 24 µs per request: 8–15% of a light request.
- **Static files gain the most**: 166 → 1,765 req/s with the patches on a
  stock server, and 3,117 req/s with the server changes too. The 4 KB
  thread-hop chunking was about 70% of static-asset cost under a real
  server, more than in-process.
- **The table endpoint is limited by SQL thread round trips**, so it gains
  from the DB-adjacent patches (79 → 133 req/s) and not from the HTTP
  server changes.
- The `patched_best` variant's Content-Length change is an experiment,
  monkeypatched in `tools/serve_variant.py`. It is not in `ALL-combined.diff`
  and contributes about 3%.

## Free-threaded Python 3.15 and the table-page extras

Table pages resolve about 30 "extras" (count, facets, suggested facets,
display columns and so on) through asyncinject with `parallel=True`, so each
extra is its own asyncio task and several SQL queries are in flight at once.
On the GIL build, making them sequential (`Registry(parallel=False)`) was
16–21% faster. This section asks why, and whether free-threaded Python
changes the answer.

**Setup.** `uv python install 3.15t` gives `3.15.0b4` free-threaded. Every
dependency installed from wheels (pydantic-core, PyYAML, MarkupSafe and
sqlite3 all declare free-threading support), and `sys._is_gil_enabled()`
stays `False` after Datasette starts and serves pages. **The full test
suite passes on the free-threaded build: 2829 passed**, the same as the GIL
build.

The benchmark added a 500,000-row table (`BIGDB=1`), so the count and facet
queries do real SQLite work, plus `CONCURRENCY=8` to keep 8 requests in
flight. Each cell is ms per page, from 5 interleaved rounds, with all
patches applied (`ALL-combined.diff`). Each "parallel / sequential" pair was
measured interleaved in the same run. Different rows come from different
runs, so compare across rows with about ±10% allowance.

One request at a time (latency):

| Build, SQL threads | Extras | Table page | 100-row page | 500k-row page | 500k, 2 facets |
|---|---|---:|---:|---:|---:|
| GIL, 3 (default) | parallel | 36.2 | 27.7 | 37.6 | 429 |
| GIL, 3 (default) | sequential | **30.0** | **21.9** | **30.5** | 429 |
| GIL, 1 | parallel | **28.4** | **21.7** | 31.2 | 433 |
| GIL, 1 | sequential | 29.5 | 25.8 | 34.7 | 427 |
| Free-threaded, 3 | parallel | 33.8 | 26.7 | **36.3** | 419 |
| Free-threaded, 3 | sequential | 33.4 | 26.9 | 40.4 | 437 |
| Free-threaded, 8 | parallel | 35.0 | 28.8 | 36.4 | 419 |
| Free-threaded, 8 | sequential | 29.8 | 30.1 | 38.9 | 440 |

8 requests in flight (ms per page = wall time ÷ pages served):

| Build, SQL threads | Extras | Table page | 100-row page | 500k-row page | 500k, 2 facets |
|---|---|---:|---:|---:|---:|
| GIL, 3 (default) | parallel | 42.0 | 35.1 | 48.1 | 167 |
| GIL, 3 (default) | sequential | 42.0 | 30.9 | 44.5 | 163 |
| GIL, 1 | parallel | 22.3 | 19.4 | 26.4 | 424 |
| GIL, 1 | sequential | 22.5 | 19.5 | 28.0 | 427 |
| Free-threaded, 3 | parallel | 13.4 | 15.7 | 18.6 | 153 |
| Free-threaded, 3 | sequential | 12.4 | 14.9 | 19.6 | 146 |
| Free-threaded, 8 | parallel | 15.2 | 15.9 | **17.2** | **72** |
| Free-threaded, 8 | sequential | 14.9 | 15.1 | 19.7 | 72 |

Without the patches the picture is the same: on the GIL build sequential
extras were 14–16% faster on the table and 500k-row pages, and on the
free-threaded build they were level or 8–13% slower. Under load the
unpatched table page went from 55.9 ms per page (GIL) to 20.3 ms
(free-threaded).

### Why parallel extras were slower on the GIL build

It is GIL contention between the SQL worker threads, not the extras
machinery.

- Python's `sqlite3` releases the GIL around each `sqlite3_step()` and takes
  it back for every row. Datasette also sets a Python `text_factory`, which
  runs for every TEXT value. A query that fetches rows therefore wants the GIL
  constantly.
- When parallel extras put several queries on the 3-thread pool at once,
  those threads and the event loop fight over the GIL. Each contended
  hand-off is a futex wait plus a context switch, and the same work costs
  far more CPU.
- **The 3.15 sampler shows it directly.** In `--mode cpu -a` profiles of the
  table page on the GIL build, sampled CPU per page was 57 ms with parallel
  extras against 33 ms sequential. The SQL worker function
  (`sql_operation_in_thread`, with the C sqlite calls charged to it) cost
  14.5 ms per page against 6.9 ms. On the free-threaded build both were about
  32 ms.
- **Shrinking the switch interval tenfold barely helps:** sequential still
  won by 13% at 0.5 ms, against 17% at the default 5 ms. So the cost is the
  per-row hand-offs, not the 5 ms interval.
- `tools/micro_gather.py` isolates it. It runs the same `db.execute()` 10 at
  a time with `asyncio.gather` instead of one at a time, and reports CPU per
  query relative to sequential:

| `num_sql_threads` | GIL build | Free-threaded |
|---|---:|---:|
| 1 | ×0.57–1.07 | ×0.67–0.78 |
| 3 (default) | **×2.3–5.0** | ×0.62–0.77 |
| 8 | **×3.6–31.5** | ×0.60–0.83 |

  With one SQL thread there is no contention, and the GIL penalty
  disappears. With 8 threads it collapses: ten concurrent 100-row queries
  take 9.2 ms each instead of 0.5 ms. On the free-threaded build, running
  queries together is always cheaper per query. Per-query wall time drops
  2.3–4.3× with 3 threads.

### What this means for the extras question

- **The sequential-extras win is an artefact of the GIL plus a multi-thread
  SQL pool.** On the free-threaded build, parallel and sequential extras are
  level within about ±5% on most pages, with no consistent winner: the
  500k-row page favours parallel by 7–11%, and with 8 threads the small table
  page favours sequential by 15%. With 1 SQL thread on the GIL build,
  parallel extras *beat* sequential (sequential is 19% slower on the
  100-row page). So
  `critic-3-extras-sequential` should not land as written.
- **`patches/extras-parallel-only-without-gil.diff`** is the targeted
  version. It resolves extras in parallel only when `sys._is_gil_enabled()`
  is false, checked when each registry is first built, so the setting
  follows the GIL if a later extension import turns it back on. That gives
  the best-measured arm with the default 3 threads on each build. The full
  suite passes with it on the GIL build, where its behaviour changes.
- **The bigger lever on GIL builds is the thread pool itself.** One SQL
  thread nearly doubled table-page throughput under load (42.0 → 22.3 ms per
  page). It removes contention between concurrent *requests*, not just
  between one request's extras. But it serialises long SQLite-bound queries:
  the 500k-row facet page went from 167 to 424 ms per page. Neither default
  suits both workloads. Reducing per-row GIL traffic would help either way:
  dropping the Python `text_factory` for a plain `str` with a fallback on
  decode errors, the completeness critic's item 8.
- **Free-threading is the largest throughput change measured in this whole
  study.** With 8 in flight it served table pages 2.2–3.1× faster than the
  GIL build with the default pool. Heavy SQLite queries scaled with threads:
  the 500k-row facet page took 72 ms per page with 8 threads, against 167 ms
  on the GIL build. The single-request cost is pure-Python overhead: light
  requests were 40–70% slower (redirect 94 → 160 µs), and single table pages
  were 11–22% slower than the best GIL configuration. It would pay off for busy deployments and cost a little for
  quiet ones.

**Correctness under real parallelism.** `tools/ft_stress.py` sent 800
requests with 16 in flight to 8 table pages on the free-threaded build.
The pages covered the fixtures tables, the 500k-row table and a named
in-memory database (the case behind #2189), all with parallel extras. It
compared every response with the first. That was 3,200 responses across both
trees and 3 or 8 SQL threads, with **zero failures**. The GIL-build control
lost one suggested facet in 800 requests: a suggestion query exceeded the
50 ms `facet_suggest_time_limit_ms` under contention. Suggested facets can
vary under load on GIL builds for the same reason.

## Bugs found along the way

Fixed by the patches above:

- **`rich` makes 4xx responses very slow and makes unmatched routes crash**
  (`error-paths-3`).
- **`HEAD` on CSV URLs generates the entire CSV** (`response-serialization-4`).
- **`?_hash=` static requests hash the whole file on the event loop**
  (`static-files-1`).
- **Every permission query is a fresh SQLite statement** (`critic-1`).
- **`_shape=arrayfirst` returns a 500** when a row holds an infinity outside
  the first column (`response-serialization-2` changes this to 200).

Not fixed, reported for follow-up:

- **httpx2 retries a failed `import sniffio` on every `datasette.client`
  request.** sniffio is no longer installed, and a failed import is not
  cached, so `sys.path` finders are walked on every client and `TestClient`
  request. That is about 29% of a client request (454 → 324 µs when the
  failure is cached). Fix: depend on `sniffio`, or fix it upstream in httpx2.
- **`calculate_etag`'s cache is never invalidated.** After a static file is
  edited in place, the old ETag keeps being served and clients get 304s for
  changed content. Keying it on `(size, mtime_ns)`, as `static-files-1` does
  for the hash, would fix it.
- **`tilde_decode` is non-deterministic** for input where a `~` escape is cut
  short by `%`. For example, `tilde_decode("90~3%")` returns `90%` only about
  63% of the time, because the random placeholder token gets decoded.
- **`--pdb` with an unmatched-route 404 crashes** in
  `pdb.post_mortem(None)`.
- **`database_download` sends both `Content-Length` and
  `Transfer-Encoding: chunked`.**
- **Foreign-key label links use `tilde_encode(database_name)`** instead of the
  database route (`views/table.py` `LINK_WITH_LABEL`).
- **Each permission check leaves about 13 cyclic objects** through the
  self-recursive closures in `Datasette._allowed_many`.
- **`tests/test_plugins.py` unregisters the wrong plugin** around line 1547.

## Further proposals, not in `ALL-combined.diff`

These are prototypes in `patches/optional/`. Each was measured, but either
not adversarially reviewed or carrying a trade-off to decide first:

- **Install httptools by default**, for example through a `datasette[fast]`
  extra or `uvicorn[standard]`. See the end-to-end section. No code change.
- **Add `--no-access-log` to `datasette serve`.** The uvicorn access log is
  8–13% of server CPU on light requests.
- **Send `Content-Length` on non-streaming responses.** About 3% end to end.
  It also gives clients sizes and progress.
- `critic-3-extras-sequential`: always resolve table-page extras
  sequentially. It measured −16 to −21% on GIL builds, but see
  [the free-threading section](#free-threaded-python-315-and-the-table-page-extras).
  The win comes from GIL contention in the SQL pool and disappears on
  free-threaded Python, so prefer `patches/extras-parallel-only-without-gil.diff`.
- `critic-5-request-scoped-introspection-memo`: foreign keys are
  introspected 5×, and primary keys and `PRAGMA schema_version` 2×, per
  table HTML request. A request-scoped memo plus a one-trip prefetch
  measured −25% on table JSON. **It does not yet clear the memo on writes**
  within the same request.
- `critic-6-inline-tableless-permission-checks`: permission checks whose
  SQL reads no table could run inline (7 µs) instead of through the thread
  pool (330 µs). That would take `/-/versions.json` from 567 to 238 µs.
- `critic-4-skip-fts-lookup-for-data-formats`: one FTS-detection round trip
  per JSON/CSV table request that only HTML uses.
- `middleware-gc-6-serve-gc-freeze-after-startup`: `gc.freeze()` after
  startup removes about 40 ms gen-2 pauses on query-heavy pages. It made no
  difference end to end on light requests, and it hides startup objects
  from `gc.get_objects()`. **Not recommended** without an opt-out.

## Ideas measured and rejected

- **uvloop.** `datasette serve` never uses it: `cli.py` calls
  `asyncio.run()`, which bypasses uvicorn's `loop="auto"`. Forcing it gained
  nothing measurable.
- **One combined alternation regex, or prefix dispatch, for routing.** Route
  matching is 0.3–15 µs (2.5% of a row request). A combined regex is only
  1.4× faster and unsafe for arbitrary plugin regexes.
- **Sync Jinja.** It is faster, but async template functions are a
  documented API.
- **`auto_reload=False`, and caching negative template lookups.** These
  would stop new or edited templates being picked up without a restart.
- **GC threshold tuning.** Within noise. GC came from cycles, which are now
  removed, not from heap size.
- **Coalescing uvicorn's header and body writes.** No gain, because only the
  first write of a response, the one that wakes the peer, is expensive.
- **Reusing one `httpx2.AsyncClient` across `datasette.client` calls.** It
  would leak cookies between calls.
- **Running introspection on the event loop thread.** A locked database
  would block the loop for up to the 5 s busy timeout.
- **Overlapping thread-pool trips with `asyncio.gather`.** This was 5–14%
  *slower*: more wake-ups and GIL contention.

## The Python 3.15 sampling profiler

Every finding started from `profiling.sampling`. Examples:

```bash
# in-process steady-state loop, 10 kHz, collapsed stacks for analysis
python -m profiling.sampling run -r 10khz --mode wall --collapsed -o x.collapsed \
    tools/bench_asgi.py loop actor_json_cookie --seconds 6
python tools/analyze_collapsed.py x.collapsed --buckets
python tools/analyze_collapsed.py x.collapsed --children "DatasetteRouter.handle_404"

# attach to a running `datasette serve` while load runs
python -m profiling.sampling attach -r 10khz -d 10 --mode cpu --collapsed -o serve.collapsed PID

# which tree is a server running? dump shows file paths in the live stack
python -m profiling.sampling dump PID
```

These features were all useful: wall, `cpu` and `gil` modes; `-a` for all
threads; `--async-aware`; `--opcodes` (with `--heatmap`/`--flamegraph`);
`--binary` plus `replay`; `--diff-flamegraph` against a binary baseline;
`--heatmap`; and `--blocking`. Sampling is out-of-process, so it slows the
target very little, and `attach` works on a production-shaped server with
no restart.

Things to know when reading its output on 3.15.0b4:

- **Many samples fail in non-blocking mode: 25–65% "error rate"**, worst
  inside Jinja-compiled template code. Frames for included templates and
  child blocks often never appear, so template-body cost shows up on
  pass-through lines. `--blocking` drops errors to about 0.5% but slows the
  target about 3×. The layer split matched between the two within about 6
  points.
- **`<GC>` is attributed to whichever frame next hits the eval breaker**, so
  it can appear under unrelated code such as `Datasette.setting`.
- **`run` puts the current directory first on `sys.path`** (like `-m` and
  `-c`). Run it from outside a source tree you don't intend to import.
- **`--opcodes` is not accepted with `--collapsed` or `--pstats`.**
- **`--async-aware --async-mode all` failed with 100% errors** on the
  table endpoint. It worked on lighter ones.
- **Line numbers after `--binary` + `replay` were sometimes implausible**,
  for example comment lines. Line-level shares here come from direct
  `--collapsed` runs.

## Reproducing

```bash
uv python install 3.15
uv venv -p 3.15 /tmp/venv315
uv pip install -p /tmp/venv315 -e . pytest pytest-asyncio pytest-xdist beautifulsoup4 \
    pytest-timeout trustme cogapp opentelemetry-sdk "multipart-form-data-conformance==0.1a0"

# two trees at the same path depth: Datasette.static() calls Path.resolve(),
# which costs more for deeper install paths and skews HTML-page comparisons
git worktree add --detach ../t/base cf113a5
git worktree add --detach ../t/patched cf113a5
(cd ../t/patched && git apply "$OLDPWD"/perf/request-response/patches/ALL-combined.diff)

/tmp/venv315/bin/python perf/request-response/tools/measure_ab.py \
    --tree base=../t/base --tree patched=../t/patched \
    --endpoints actor_json_cookie,redirect_dash,versions_json,not_found,static_css \
    --n 1000 --rounds 5
```

`tools/bench_asgi.py` drives the ASGI app directly with realistic browser
headers. `tools/measure_ab.py` interleaves trees round by round, using a
fresh process per run. `tools/analyze_collapsed.py` attributes collapsed
stacks to components. The measurements were taken on a 4-vCPU cloud VM, so
compare ratios rather than absolute numbers.
