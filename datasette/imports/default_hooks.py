"""
Plugin hooks for loading data: the read-only ``scratch`` database that
``datasette data.csv`` loads into, and the progress of background imports.

Read-only is enforced twice:

1. ``permission_resources_sql`` denies every action that would change the
   database - for every actor, root included. A deny for the database
   outranks root's instance-wide allow, and a deny for each table outranks
   a table-level allow from configuration. The UI hides the write actions
   and the JSON write API answers 403.
2. ``write_wrapper`` refuses any write to a scratch database that the
   import process did not make itself, which also covers code that skips
   permission checks: trusted stored queries from configuration, plugins
   writing directly, ``skip_permission_checks``.

``top_database`` shows, on the scratch database page, the ``--save``
command that would load the same data into a file.

Background imports (:mod:`datasette.imports.background`):

- ``top_homepage``, ``top_database`` and ``top_table`` show a notice for
  each import that is still running, and for each one that failed or was
  cancelled (its table is incomplete), with the loader's notes about how
  it read the file. The table page also shows those notes once its import
  is done. While anything is running, a little script polls
  ``/-/imports.json`` every second and updates the text and the notes; it
  stops when nothing is running.
- ``/-/imports`` and ``/-/imports.json`` list the imports the actor can see:
  those into tables they have ``view-table`` for.
- ``POST /DATABASE/TABLE/-/cancel-import`` cancels one, for actors with the
  ``cancel-import`` action - nobody but root by default. It changes what
  ends up in a table, so like other actions that change data it has to be
  granted, and like ``insert-row`` it is enough on its own (a token made
  with ``create-token --all cancel-import`` can cancel); the details of
  the import in the response need ``view-table`` too. CSRF protection is
  Datasette's usual ``Sec-Fetch-Site`` / ``Origin`` check on every POST.
- A request for a table that an import has not created yet (or never
  will, because it failed) gets a 404 that says so, with the progress. A
  request that looked for the table just before the import's first rows
  were committed is told to try again: a page is redirected to itself.
"""

import json

import markupsafe

from datasette import hookimpl

# Actions that would change a database. set-column-type is not here: column
# types live in the internal database, not in scratch.
WRITE_ACTIONS = {
    "insert-row",
    "update-row",
    "delete-row",
    "create-table",
    "create-view",
    "alter-table",
    "drop-table",
    "drop-view",
    "execute-write-sql",
}
REASON = "The scratch database is read-only: only the import process writes to it"
CANCEL_ACTION = "cancel-import"
# How often the progress notices poll /-/imports.json while anything runs
POLL_MILLISECONDS = 1000


def _scratch_names(datasette):
    from datasette.imports.scratch import is_scratch

    return [db.name for db in datasette.databases.values() if is_scratch(db)]


@hookimpl
def register_actions(datasette):
    from datasette.permissions import Action
    from datasette.resources import TableResource

    return [
        Action(
            name=CANCEL_ACTION,
            abbr="ci",
            description="Cancel an import that is loading data into a table",
            resource_class=TableResource,
        )
    ]


@hookimpl
def permission_resources_sql(datasette, actor, action):
    if action not in WRITE_ACTIONS:
        return None
    names = _scratch_names(datasette)
    if not names:
        return None
    from datasette.permissions import PermissionSQL

    params = {"scratch_reason": REASON}
    placeholders = []
    for i, name in enumerate(names):
        params[f"scratch_db_{i}"] = name
        placeholders.append(f":scratch_db_{i}")
    in_list = ", ".join(placeholders)
    database_rules = " union all ".join(
        f"select {p} as parent, null as child, 0 as allow, :scratch_reason as reason"
        for p in placeholders
    )
    sql = f"""
        {database_rules}
        union all
        select database_name, table_name, 0, :scratch_reason
        from catalog_tables where database_name in ({in_list})
        union all
        select database_name, view_name, 0, :scratch_reason
        from catalog_views where database_name in ({in_list})
    """
    return PermissionSQL(sql=sql, params=params)


@hookimpl
def write_wrapper(datasette, database, request, transaction):
    from datasette.imports.scratch import import_writes_allowed, is_scratch

    db = datasette.databases.get(database)
    if db is None or not is_scratch(db) or import_writes_allowed():
        return None

    def refuse(conn):
        from datasette.utils.asgi import Forbidden

        raise Forbidden(REASON)
        yield  # pragma: no cover

    return refuse


# Import progress --------------------------------------------------------------------


def _registry(datasette):
    from datasette.imports.background import existing_registry

    return existing_registry(datasette)


def _table_resource(database, table):
    from datasette.resources import TableResource

    return TableResource(database=database, table=table)


async def _visible(datasette, actor, jobs):
    "The imports whose table ``actor`` may view."
    visible = []
    for job in jobs:
        if await datasette.allowed(
            action="view-table",
            resource=_table_resource(job.database, job.table),
            actor=actor,
        ):
            visible.append(job)
    return visible


async def _can_cancel(datasette, actor, job):
    return await datasette.allowed(
        action=CANCEL_ACTION,
        resource=_table_resource(job.database, job.table),
        actor=actor,
    )


async def _import_data(datasette, request, job):
    data = job.to_dict()
    data["table_url"] = datasette.absolute_url(
        request, datasette.urls.table(job.database, job.table)
    )
    data["cancel_url"] = datasette.absolute_url(request, _cancel_path(datasette, job))
    data["can_cancel"] = job.active and await _can_cancel(datasette, request.actor, job)
    return data


def _cancel_path(datasette, job):
    return datasette.urls.table(job.database, job.table) + "/-/cancel-import"


def _poll_url(datasette, database=None, table=None):
    import urllib.parse

    params = {}
    if database is not None:
        params["database"] = database
    if table is not None:
        params["table"] = table
    url = datasette.urls.path("-/imports.json")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def _notice_class(jobs):
    states = {job.state for job in jobs}
    if "failed" in states:
        return "message-error"
    if "cancelled" in states:
        return "message-warning"
    return "message-info"


async def _render_notice(
    datasette, request, jobs, *, intro=None, poll_url, link_tables=True
):
    """
    HTML for a list of imports, with cancel buttons for actors allowed to
    cancel, and the polling script if any import is still running.
    """
    h = markupsafe.escape
    active = any(job.active for job in jobs)
    items = []
    for job in jobs:
        name = h(job.table)
        if link_tables and job.table_exists:
            href = datasette.urls.table(job.database, job.table)
            name = f'<a href="{h(href)}">{name}</a>'
        progress = ""
        if job.active:
            value = "" if job.percent is None else f' value="{job.percent}"'
            progress = f' <progress max="100"{value}></progress>'
        cancel = ""
        if job.active and await _can_cancel(datasette, request.actor, job):
            next_url = request.path + (
                f"?{request.query_string}" if request.query_string else ""
            )
            cancel = (
                f' <form class="import-cancel" method="post" '
                f'action="{h(_cancel_path(datasette, job))}" '
                'style="display: inline">'
                f'<input type="hidden" name="next" value="{h(next_url)}">'
                '<button type="submit">Cancel import</button></form>'
            )
        items.append(
            f'<li data-import-id="{h(job.id)}" data-state="{h(job.state)}">'
            f'{name}: <span class="import-status">{h(job.status())}</span>'
            f"{progress}{cancel}{_notes_html(job.notes)}</li>"
        )
    poll = f' data-poll="{h(poll_url)}"' if active else ""
    parts = [
        f'<div class="imports-notice {_notice_class(jobs)}"{poll}>',
        f"<p>{intro}</p>" if intro else "",
        f'<ul class="import-list">{"".join(items)}</ul>',
    ]
    if active:
        parts.append(
            '<p class="imports-finished" hidden>Finished: '
            '<a href="">reload this page</a> to see the data.</p>'
        )
    parts.append("</div>")
    if active:
        parts.append(_POLL_SCRIPT)
    return markupsafe.Markup("".join(parts))


def _notes_html(notes):
    "The loader's notes about an import, as a list inside its item."
    if not notes:
        return ""
    h = markupsafe.escape
    return '<ul class="import-notes">{}</ul>'.format(
        "".join(f"<li>{h(note)}</li>" for note in notes)
    )


# Updates the notices in place: the status text, the progress bar, the
# loader's notes and the notice's colour. Stops when nothing is running, or
# after repeated errors
_POLL_SCRIPT = (
    """<script>
(function () {
  if (window.datasetteImportPolling) return;
  window.datasetteImportPolling = true;
  var ACTIVE = ["queued", "downloading", "importing"];
  var failures = 0;
  function notices() {
    return document.querySelectorAll(".imports-notice[data-poll]");
  }
  function update(notice, data) {
    var running = false, failed = false, cancelled = false;
    (data.imports || []).forEach(function (job) {
      var item = notice.querySelector('[data-import-id="' + job.id + '"]');
      if (!item) return;
      var status = item.querySelector(".import-status");
      if (status && status.textContent !== job.status) {
        status.textContent = job.status;
      }
      item.setAttribute("data-state", job.state);
      var notes = job.notes || [];
      var list = item.querySelector(".import-notes");
      if (notes.length && (!list || list.children.length !== notes.length)) {
        if (!list) {
          list = document.createElement("ul");
          list.className = "import-notes";
          item.appendChild(list);
        }
        list.textContent = "";
        notes.forEach(function (note) {
          var li = document.createElement("li");
          li.textContent = note;
          list.appendChild(li);
        });
      }
      var bar = item.querySelector("progress");
      if (ACTIVE.indexOf(job.state) !== -1) {
        running = true;
        if (bar && job.percent !== null) bar.value = job.percent;
        if (bar && job.percent === null) bar.removeAttribute("value");
      } else {
        if (bar) bar.remove();
        var form = item.querySelector("form");
        if (form) form.remove();
        failed = failed || job.state === "failed";
        cancelled = cancelled || job.state === "cancelled";
      }
    });
    if (failed || cancelled) {
      notice.classList.remove("message-info", "message-warning");
      notice.classList.add(failed ? "message-error" : "message-warning");
    }
    if (!running) {
      notice.removeAttribute("data-poll");
      var finished = notice.querySelector(".imports-finished");
      if (finished) finished.hidden = false;
    }
  }
  function poll() {
    var list = Array.prototype.slice.call(notices());
    if (!list.length) return;
    if (document.hidden) {
      setTimeout(poll, """
    + str(POLL_MILLISECONDS * 2)
    + """);
      return;
    }
    Promise.all(list.map(function (notice) {
      return fetch(notice.getAttribute("data-poll"), {
        credentials: "same-origin",
        headers: {"accept": "application/json"}
      }).then(function (response) {
        if (!response.ok) throw new Error(response.status);
        return response.json();
      }).then(function (data) { update(notice, data); });
    })).then(function () {
      failures = 0;
    }, function () {
      failures += 1;
    }).then(function () {
      if (failures < 10) {
        setTimeout(poll, """
    + str(POLL_MILLISECONDS)
    + """ * (failures + 1));
      }
    });
  }
  setTimeout(poll, """
    + str(POLL_MILLISECONDS)
    + """);
})();
</script>"""
)


def _incomplete(job):
    "A failed or cancelled import whose partial table is still there."
    return job.state in ("failed", "cancelled") and job.table_exists


def _shown(jobs):
    """
    Of the imports that say what state each table is in
    (``registry.tables()``): running imports, and failed or cancelled ones
    - their tables are incomplete, or were never created. Finished ones too
    while others run, so the list does not jump about. Never an import that
    failed because its table was there already: it did nothing to it.
    """
    jobs = [job for job in jobs if not job.existing_table]
    if any(job.active for job in jobs):
        return [job for job in jobs if job.table_exists or job.state != "done"]
    return [job for job in jobs if job.state in ("failed", "cancelled")]


@hookimpl
def top_homepage(datasette, request):
    registry = _registry(datasette)
    if registry is None or not registry.imports():
        return None

    async def inner():
        jobs = _shown(await _visible(datasette, request.actor, registry.tables()))
        if not jobs:
            return None
        return await _render_notice(
            datasette, request, jobs, poll_url=_poll_url(datasette)
        )

    return inner


@hookimpl
def top_table(datasette, request, database, table):
    registry = _registry(datasette)
    job = registry.get(database, table) if registry is not None else None
    if job is not None and job.state == "done" and job.table_exists and job.notes:
        # Complete, but the loader had something to say about how it read
        # the file
        return _done_notes_notice(job)
    # Only an import whose rows these are: one still running, or one that
    # failed or was cancelled and left its partial table. Not one that
    # failed because this table was there already, or created no table
    if job is None or not (job.active or _incomplete(job)):
        return None

    async def inner():
        if job.active:
            intro = (
                "This table is still being imported, so you are seeing the "
                "rows loaded so far. Counts, facets and sorting will change "
                "as more arrive."
            )
        else:
            intro = "This table is incomplete."
        return await _render_notice(
            datasette,
            request,
            [job],
            intro=intro,
            poll_url=_poll_url(datasette, database, table),
            link_tables=False,
        )

    return inner


def _done_notes_notice(job):
    return markupsafe.Markup(
        '<div class="imports-notice message-info">'
        f"<p>Notes from importing this table:</p>{_notes_html(job.notes)}</div>"
    )


@hookimpl
def top_database(datasette, request, database):
    from datasette.imports.scratch import is_scratch

    db = datasette.databases.get(database)
    if db is None:
        return None
    registry = _registry(datasette)
    jobs = registry.tables(database) if registry is not None else []
    if not jobs and not (is_scratch(db) and db.save_command):
        return None

    async def inner():
        parts = []
        shown = _shown(await _visible(datasette, request.actor, jobs))
        if shown:
            parts.append(
                await _render_notice(
                    datasette,
                    request,
                    shown,
                    poll_url=_poll_url(datasette, database),
                )
            )
        if is_scratch(db) and db.save_command:
            parts.append(_save_banner(datasette, db, database, jobs))
        return markupsafe.Markup("".join(parts))

    return inner


def _save_banner(datasette, db, database, jobs):
    from datasette.imports.sources import quote_command

    h = markupsafe.escape
    if jobs:
        names = [job.table for job in jobs if job.table_exists]
    else:
        names = [loaded.table for loaded in db.loaded if loaded.created]
    tables = ", ".join(
        f'<a href="{h(datasette.urls.table(database, name))}">{h(name)}</a>'
        for name in names
    )
    parts = [
        (
            '<div class="scratch-save" style="border: 1px solid #ccc; '
            'padding: 0.5em 1em; margin: 1em 0">'
        ),
        (
            "<p>This is a temporary, read-only database, deleted when "
            f"Datasette stops. Loaded: {tables or 'no tables'}.</p>"
        ),
        "<p>To keep this data in a SQLite file, run:</p>",
        f'<pre class="scratch-save-command">{h(db.save_command)}</pre>',
    ]
    if getattr(db, "redacted", False):
        # Anyone who can see this page can see the command, so passwords,
        # tokens and signatures in URLs are not shown
        parts.append(
            "<p>Passwords, keys and tokens in the URLs are shown as "
            "<code>REDACTED</code>: put the real values back before "
            "running it.</p>"
        )
    if db.from_stdin:
        parts.append(
            "<p>The <code>-</code> argument read standard input, so pipe the "
            "same data in to that command again.</p>"
        )
    save_path = getattr(db, "save_path", None) or "data.db"
    parts.append(
        f"<p>Then <code>{h(quote_command(['datasette', save_path]))}</code> "
        "opens it without loading anything.</p></div>"
    )
    return "".join(parts)


@hookimpl
def handle_exception(datasette, request, exception):
    from datasette.utils.asgi import TableNotFound

    if not isinstance(exception, TableNotFound):
        return None
    registry = _registry(datasette)
    if registry is None:
        return None
    job = registry.get(exception.database_name, exception.table)
    if job is None or job.existing_table or (job.table_exists and not job.active):
        return None

    async def inner():
        from datasette.utils.asgi import Response

        if not (await _visible(datasette, request.actor, [job])):
            return None
        is_json = request.path.endswith(".json")
        db = datasette.databases.get(job.database)
        if job.table_exists and db is not None and await db.table_exists(job.table):
            if not await db.table_exists(exception.table):
                # There, but under another case (/scratch/EVENTS for
                # events): resolve_table() compares names exactly, so this
                # is an ordinary 404 - and redirecting would loop
                return None
            # A race, not a stale read: resolve_table() looked in
            # sqlite_master just before the import's first chunk was
            # committed, and the table is there now. A page asks the
            # browser to try again; JSON says what happened
            if not is_json and request.method in ("GET", "HEAD"):
                return Response.redirect(
                    request.full_path,
                    status=307,
                    headers={"cache-control": "no-store"},
                )
            message = (
                f"Table {job.table} was created while this request was being "
                "answered: try again"
            )
        elif job.active:
            message = f"Table {job.table} is not ready yet: {job.status()}"
        else:
            message = f"Table {job.table} was not created: {job.status()}"
        if is_json:
            return Response.json(
                {
                    "ok": False,
                    "error": message,
                    "status": 404,
                    "title": None,
                    "import": await _import_data(datasette, request, job),
                },
                status=404,
            )
        notice = await _render_notice(
            datasette,
            request,
            [job],
            intro=(
                "This table is being imported and will appear here soon."
                if job.active
                else "This table could not be imported."
            ),
            poll_url=_poll_url(datasette, job.database, job.table),
            link_tables=False,
        )
        html = await datasette.render_template(
            "imports.html",
            {
                "title": job.table,
                "database": job.database,
                "notice": notice,
                "imports_url": datasette.urls.path("-/imports"),
            },
            request=request,
            view_name="imports",
        )
        return Response.html(html, status=404)

    return inner


@hookimpl
def register_routes(datasette):
    return [
        (r"^/-/imports(\.(?P<format>json))?$", imports_view),
        (
            r"^/(?P<database>[^/]+)/(?P<table>[^/]+)/-/cancel-import$",
            cancel_import_view,
        ),
    ]


async def imports_view(datasette, request):
    """
    ``/-/imports.json``: the imports the actor can see, optionally only those
    into ``?database=`` or ``?table=``. ``/-/imports`` is the same as a page.
    """
    from datasette.utils.asgi import Response

    await datasette.ensure_permission(action="view-instance", actor=request.actor)
    registry = _registry(datasette)
    jobs = registry.imports() if registry is not None else []
    database = request.args.get("database")
    table = request.args.get("table")
    if database is not None:
        jobs = [job for job in jobs if job.database == database]
    if table is not None:
        jobs = [job for job in jobs if job.table.lower() == table.lower()]
    jobs = await _visible(datasette, request.actor, jobs)
    if request.url_vars.get("format") == "json":
        headers = {"cache-control": "no-store"}
        if datasette.cors:
            from datasette.utils import add_cors_headers

            add_cors_headers(headers)
        return Response.json(
            {
                "ok": True,
                "active": sum(1 for job in jobs if job.active),
                "imports": [await _import_data(datasette, request, j) for j in jobs],
            },
            headers=headers,
        )
    notice = ""
    if jobs:
        notice = await _render_notice(
            datasette,
            request,
            jobs,
            poll_url=_poll_url(datasette, database, table),
        )
    return Response.html(
        await datasette.render_template(
            "imports.html",
            {
                "title": "Imports",
                "database": None,
                "notice": notice,
                "imports_url": None,
            },
            request=request,
            view_name="imports",
        )
    )


async def cancel_import_view(datasette, request):
    """
    ``POST /DATABASE/TABLE/-/cancel-import`` cancels the import into that
    table and waits (up to five seconds) for it to stop. A form post
    redirects back to the page it came from, with a message; anything
    else gets JSON: ``{"ok": true, "import": {...}}``, with ``import`` only
    for actors who may also view the table. Needs ``cancel-import``: 403
    without it, or 404 for an actor who cannot see the table either.
    """
    from datasette.utils import tilde_decode
    from datasette.utils.asgi import Response

    database = tilde_decode(request.url_vars["database"])
    table = tilde_decode(request.url_vars["table"])
    content_type = request.headers.get("content-type", "")
    is_form = content_type.startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    )

    def error(message, status):
        return Response.json({"ok": False, "errors": [message]}, status=status)

    if request.method != "POST":
        return Response.json(
            {"ok": False, "errors": ["Use POST to cancel an import"]},
            status=405,
            headers={"allow": "POST"},
        )
    registry = _registry(datasette)
    job = registry.get(database, table) if registry is not None else None
    if job is None:
        return error(f"No import into {database}.{table}", 404)
    # cancel-import on its own is enough to cancel, as insert-row is enough
    # to insert rows (a token made with --all cancel-import works). What
    # the import is - its source, rows, error - is only shown to actors who
    # may also view the table
    can_cancel = await _can_cancel(datasette, request.actor, job)
    visible = bool(await _visible(datasette, request.actor, [job]))
    if not can_cancel:
        if not visible:
            return error(f"No import into {database}.{table}", 404)
        return error(
            "Permission denied: you cannot cancel imports into this table", 403
        )

    async def details():
        if not visible:
            return {}
        return {"import": await _import_data(datasette, request, job)}

    next_url = None
    if is_form:
        next_url = (await request.post_vars()).get("next")
    else:
        body = await request.post_body()
        if body.strip():
            try:
                json.loads(body)
            except ValueError:
                return error("Invalid JSON body", 400)
    was_active = job.cancel()
    if was_active:
        await job.wait(timeout=5)
    if is_form:
        from datasette.app import Datasette

        if was_active:
            datasette.add_message(
                request, f"Cancelled the import into {table}", Datasette.INFO
            )
        else:
            datasette.add_message(
                request,
                f"The import into {table} had already finished",
                Datasette.WARNING,
            )
        return Response.redirect(_safe_next(datasette, next_url, job))
    if not was_active:
        return Response.json(
            {
                "ok": False,
                "errors": [f"The import into {table} is not running ({job.state})"],
                **(await details()),
            },
            status=409,
        )
    return Response.json({"ok": True, **(await details())})


def _safe_next(datasette, next_url, job):
    """
    Where a cancel form redirects to: ``next`` if it is a path on this site,
    otherwise the table. Never ``//evil.example`` or ``https://...``, and
    nothing with whitespace or control characters in it - browsers drop a
    tab or newline before parsing, so ``/<TAB>/evil.example`` would be
    ``//evil.example``.
    """
    import urllib.parse

    if (
        next_url
        and next_url.startswith("/")
        and not next_url.startswith("//")
        and "\\" not in next_url
        and not any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in next_url)
    ):
        parts = urllib.parse.urlsplit(next_url)
        if not parts.scheme and not parts.netloc:
            return next_url
    return datasette.urls.table(job.database, job.table)
