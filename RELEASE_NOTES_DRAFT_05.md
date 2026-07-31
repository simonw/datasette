# Release notes draft — ticket 05 (wrapper reorder)

Scratch file: content to be folded into `docs/changelog.rst` by ticket 07
(`todos/first-request/07-docs-and-changelog.md`). Not part of the shipped
docs on its own.

## Plugin hooks

- Plugin `asgi_wrapper` middleware now always runs **after** Datasette
  startup has completed. Wrappers can rely on startup hooks — including
  internal-database migrations run by other plugins' `startup()` hooks —
  having already executed before their code sees an `http` or `websocket`
  ASGI scope. This applies on every deployment path: behind a real ASGI
  lifespan-aware server, and on the first-request fallback used by bare
  `app()` embedding and test clients that never send lifespan events.
- Short-circuiting wrappers — ones that return a response without calling
  the wrapped application, such as an auth plugin returning a 401/403 or a
  CORS plugin answering a preflight request — no longer defer startup
  indefinitely. Startup now runs unconditionally before any wrapper sees
  the scope, so it can no longer be skipped by requests that never reach
  the inner app.
- `lifespan` scopes are unaffected by this change and continue to flow
  through plugin `asgi_wrapper` middleware exactly as before, so plugins
  that inspect or wrap lifespan events keep working unmodified.
