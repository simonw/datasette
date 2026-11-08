"""
Plugin hooks for loading data: the read-only ``scratch`` database that
``datasette data.csv`` loads into, and the progress of background imports.

Read-only is enforced twice:

1. ``permission_resources_sql`` denies every action that would change the
   database - for every actor, root included. A deny for the database
   outranks root's instance-wide allow, and a deny for each table outranks
   a table-level allow from configuration. The UI hides the write actions
   and the JSON write API answers 403.
2. ``write_wrapper`` refuses any write through ``execute_write()``,
   ``execute_write_script()``, ``execute_write_many()`` or
   ``execute_write_fn()`` to a scratch database that the import process did
   not make itself, which also covers code that skips permission checks:
   trusted stored queries from configuration, plugins writing through those
   methods, ``skip_permission_checks``. ``Database.execute_isolated_fn()``
   opens a write connection of its own that ``write_wrapper`` does not
   see, so it is not covered; nothing reachable over HTTP runs user SQL
   through it (``analyze_sql`` uses it for an ``EXPLAIN`` and a
   ``CREATE VIEW`` that is rolled back, which refusing would break).

``top_database`` shows, on the scratch database page, the ``--save``
command that would load the same data into a file, to actors who may view
every table it loads.

Background imports (:mod:`datasette.imports.background`):

- ``top_homepage``, ``top_database`` and ``top_table`` show a notice for
  each import that is still running, and for each one that failed or was
  cancelled (its table is incomplete), with the loader's notes about how
  it read the file - on the homepage and database page not for one whose
  rows were removed again (an upload), and at most the newest
  ``MAX_FINISHED_NOTICES`` finished ones. The table page also shows those
  notes once its import is done. While anything is running, a little
  script polls ``/-/imports.json`` every second and updates the notices;
  it stops when nothing is running.
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
from datasette.imports import ACTIVE_STATES

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
# Finished imports listed on the homepage and database page, newest first;
# the rest are on /-/imports
MAX_FINISHED_NOTICES = 10


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
    datasette,
    request,
    jobs,
    *,
    intro=None,
    poll_url,
    link_tables=True,
    left_out=0,
    pending_table=False,
    show_source=False,
):
    """
    HTML for a list of imports, with cancel buttons for actors allowed to
    cancel, and the polling script if any import is still running.

    - ``intro`` is a sentence before the list, which the script rewrites
      when the imports have finished.
    - ``link_tables`` links each name to its table once it exists (the
      script adds the link if it appears while the page is open).
    - ``left_out`` older finished imports are not listed: a link to
      ``/-/imports`` says so.
    - ``pending_table``: this is the page for a table that does not exist
      yet, which the script reloads once it does.
    - ``show_source`` adds where each import's data came from.

    Imports into more than one database are labelled ``database / table``.
    """
    h = markupsafe.escape
    active = any(job.active for job in jobs)
    several_databases = len({job.database for job in jobs}) > 1
    items = []
    for job in jobs:
        label = job.table
        if several_databases:
            label = f"{job.database} / {job.table}"
        name = h(label)
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
                f'data-confirm="{h(_cancel_question(job))}" '
                'style="display: inline">'
                f'<input type="hidden" name="next" value="{h(next_url)}">'
                '<button type="submit">Cancel import</button></form>'
            )
        source = ""
        if show_source:
            source = f' <span class="import-source">(from {h(job.title)})</span>'
        items.append(
            f'<li data-import-id="{h(job.id)}" data-state="{h(job.state)}">'
            f'<span class="import-name">{name}</span>: '
            f'<span class="import-status">{h(job.status())}</span>'
            f"{source}{progress}{cancel}{_notes_html(job.notes)}</li>"
        )
    attributes = ""
    if active:
        attributes += f' data-poll="{h(poll_url)}"'
    if link_tables:
        attributes += " data-link-tables"
    if pending_table:
        attributes += " data-pending-table"
    parts = [
        f'<div class="imports-notice {_notice_class(jobs)}"{attributes}>',
        f'<p class="import-intro">{intro}</p>' if intro else "",
        f'<ul class="import-list">{"".join(items)}</ul>',
    ]
    if left_out:
        parts.append(
            f'<p class="imports-more">{left_out:,} older finished '
            f"import{'' if left_out == 1 else 's'} not listed here: see "
            f'<a href="{h(datasette.urls.path("-/imports"))}">all imports</a>.</p>'
        )
    if active:
        parts.append(
            '<p class="imports-finished" hidden>Finished: '
            '<a href="">reload this page</a> to see the data.</p>'
        )
    parts.append("</div>")
    if active:
        parts.append(_POLL_SCRIPT)
    return markupsafe.Markup("".join(parts))


def _cancel_question(job):
    "What the cancel button asks before it cancels."
    if job.on_error == "drop":
        outcome = "The rows imported so far will be removed."
    else:
        outcome = (
            "The rows imported so far will be kept, and the table marked incomplete."
        )
    return f"Stop importing {job.table}? {outcome}"


def _notes_html(notes):
    "The loader's notes about an import, as a list inside its item."
    if not notes:
        return ""
    h = markupsafe.escape
    return '<ul class="import-notes">{}</ul>'.format(
        "".join(f"<li>{h(note)}</li>" for note in notes)
    )


# Updates the notices in place: the status text, the progress bar, the
# loader's notes, links to tables that have appeared and the notice's
# colour, then the sentence before the list once nothing is running.
# Reloads the page for a table that did not exist yet once it does. Asks
# before a cancel button cancels. Stops when nothing is running, or after
# repeated errors
_POLL_SCRIPT = (
    """<script>
(function () {
  if (window.datasetteImportPolling) return;
  window.datasetteImportPolling = true;
  var ACTIVE = """
    + json.dumps(list(ACTIVE_STATES))
    + """;
  var failures = 0;
  var reloading = false;
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.matches || !form.matches("form.import-cancel[data-confirm]")) return;
    if (!window.confirm(form.getAttribute("data-confirm"))) {
      event.preventDefault();
    }
  });
  function notices() {
    return document.querySelectorAll(".imports-notice[data-poll]");
  }
  function linkName(item, job) {
    var name = item.querySelector(".import-name");
    if (!name || name.querySelector("a") || !job.table_url) return;
    var link = document.createElement("a");
    link.href = job.table_url;
    link.textContent = name.textContent;
    name.textContent = "";
    name.appendChild(link);
  }
  function update(notice, data) {
    var running = false, failed = false, cancelled = false, ready = false;
    var appeared = false;
    (data.imports || []).forEach(function (job) {
      var item = notice.querySelector('[data-import-id="' + job.id + '"]');
      if (!item) return;
      var status = item.querySelector(".import-status");
      if (status && status.textContent !== job.status) {
        status.textContent = job.status;
      }
      item.setAttribute("data-state", job.state);
      if (job.table_exists) {
        appeared = true;
        if (notice.hasAttribute("data-link-tables")) linkName(item, job);
      }
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
        ready = ready || (job.state === "done" && job.table_exists);
      }
    });
    if (appeared && notice.hasAttribute("data-pending-table") && !reloading) {
      // The table this page is for exists now: show it, with its rows
      reloading = true;
      window.location.reload();
      return;
    }
    if (failed || cancelled) {
      notice.classList.remove("message-info", "message-warning");
      notice.classList.add(failed ? "message-error" : "message-warning");
    }
    if (!running) {
      notice.removeAttribute("data-poll");
      var intro = notice.querySelector(".import-intro");
      if (intro) {
        intro.textContent = failed
          ? "Import failed."
          : cancelled
          ? "Import cancelled."
          : "Import finished.";
      }
      var finished = notice.querySelector(".imports-finished");
      if (finished && ready) finished.hidden = false;
    }
  }
  function poll() {
    if (reloading) return;
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


def _left_nothing(job):
    """
    A failed or cancelled import whose rows were removed with its table
    (``on_error="drop"``: an upload, or an import into a ``--save``
    file): nothing on the site is incomplete because of it.
    """
    return (
        job.state in ("failed", "cancelled")
        and job.on_error == "drop"
        and not job.table_exists
    )


def _shown(jobs):
    """
    Of the imports that say what state each table is in
    (``registry.tables()``), those the homepage and database page show:
    running imports, and failed or cancelled ones whose tables are
    incomplete, or were never created. Finished ones too while others run,
    so the list does not jump about. Never an import that failed because
    its table was there already (it did nothing to it), or one whose rows
    were removed again (anyone could otherwise leave a message there for
    every visitor by uploading a file that fails). ``/-/imports`` lists
    them all.
    """
    jobs = [job for job in jobs if not job.existing_table and not _left_nothing(job)]
    if any(job.active for job in jobs):
        return [job for job in jobs if job.table_exists or job.state != "done"]
    return [job for job in jobs if job.state in ("failed", "cancelled")]


async def _shown_to(datasette, actor, jobs):
    """
    :func:`_shown` for what ``actor`` may see, but for the oldest finished
    imports beyond ``MAX_FINISHED_NOTICES``. Returns the imports and how
    many were left out. Permissions are checked only for imports that
    :func:`_shown` keeps.
    """
    # Again for the visible ones: "while others run" is about what this
    # actor can see running
    jobs = _shown(await _visible(datasette, actor, _shown(jobs)))
    finished = [job for job in jobs if not job.active]
    if len(finished) <= MAX_FINISHED_NOTICES:
        return jobs, 0
    left_out = {job.id for job in finished[:-MAX_FINISHED_NOTICES]}
    return [job for job in jobs if job.id not in left_out], len(left_out)


@hookimpl
def top_homepage(datasette, request):
    registry = _registry(datasette)
    if registry is None or not registry.imports():
        return None

    async def inner():
        jobs, left_out = await _shown_to(datasette, request.actor, registry.tables())
        if not jobs:
            return None
        return await _render_notice(
            datasette,
            request,
            jobs,
            poll_url=_poll_url(datasette),
            left_out=left_out,
        )

    return inner


@hookimpl
def top_table(datasette, request, database, table):
    registry = _registry(datasette)
    job = registry.get(database, table) if registry is not None else None
    if job is not None and job.state == "done" and job.table_exists and job.notes:
        # Complete, but the loader had something to say about how it read
        # the file
        return _done_notes_notice(job.notes)
    if job is None:
        # Loaded before Datasette served anything (--get)
        notes = _loaded_notes(datasette, database, table)
        if notes:
            return _done_notes_notice(notes)
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


def _done_notes_notice(notes):
    return markupsafe.Markup(
        '<div class="imports-notice message-info">'
        f"<p>Notes from importing this table:</p>{_notes_html(notes)}</div>"
    )


def _loaded_notes(datasette, database, table):
    "The notes of the scratch table ``table`` loaded without the registry."
    from datasette.imports.scratch import is_scratch

    db = datasette.databases.get(database)
    if db is None or not is_scratch(db):
        return []
    for loaded in reversed(db.loaded):
        if loaded.table == table:
            return loaded.notes
    return []


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
        shown, left_out = await _shown_to(datasette, request.actor, jobs)
        if shown:
            parts.append(
                await _render_notice(
                    datasette,
                    request,
                    shown,
                    poll_url=_poll_url(datasette, database),
                    left_out=left_out,
                )
            )
        if is_scratch(db) and db.save_command:
            parts.append(
                await _save_banner(datasette, request.actor, db, database, jobs)
            )
        return markupsafe.Markup("".join(parts))

    return inner


async def _can_view_table(datasette, actor, database, table):
    return await datasette.allowed(
        action="view-table",
        resource=_table_resource(database, table),
        actor=actor,
    )


async def _save_banner(datasette, actor, db, database, jobs):
    """
    What the scratch database is, the tables in it that ``actor`` may view,
    and - if they may view every table the command line loads into it - the
    ``--save`` command that loads the same inputs into a file, which names
    them all.
    """
    from datasette.imports.sources import quote_command

    h = markupsafe.escape
    if jobs:
        names = [
            job.table
            for job in await _visible(
                datasette, actor, [job for job in jobs if job.table_exists]
            )
        ]
    else:
        names = [
            loaded.table
            for loaded in db.loaded
            if loaded.created
            and await _can_view_table(datasette, actor, database, loaded.table)
        ]
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
    ]
    for table in getattr(db, "save_tables", None) or ():
        if not await _can_view_table(datasette, actor, database, table):
            # The command names every input, this table's source too
            parts.append("</div>")
            return "".join(parts)
    parts += [
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
        from datasette.utils import error_body
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
            # Datasette's standard error format, and the import
            return Response.json(
                {
                    **error_body(message, 404),
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
            pending_table=True,
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
    database = request.args.get("database")
    table = request.args.get("table")
    jobs = registry.imports(database, table) if registry is not None else []
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
            show_source=True,
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
    Errors use Datasette's standard JSON error format (``ok``, ``error``,
    ``errors``, ``status``); a 409 for an import that is not running has
    ``import`` too.
    """
    from datasette.utils import error_body, tilde_decode
    from datasette.utils.asgi import Response

    database = tilde_decode(request.url_vars["database"])
    table = tilde_decode(request.url_vars["table"])
    content_type = request.headers.get("content-type", "")
    is_form = content_type.startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    )

    def error(message, status, headers=None, **extra):
        # Datasette's standard error format, see error_body()
        return Response.json(
            {**error_body(message, status), **extra}, status=status, headers=headers
        )

    if request.method != "POST":
        return error("Use POST to cancel an import", 405, headers={"allow": "POST"})
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
    was_active = job.cancel(actor=request.actor)
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
        return error(
            f"The import into {table} is not running ({job.state})",
            409,
            **(await details()),
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
