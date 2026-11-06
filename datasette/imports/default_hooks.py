"""
Plugin hooks for the read-only ``scratch`` database that ``datasette
data.csv`` loads into.

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
"""

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


def _scratch_names(datasette):
    from datasette.imports.scratch import is_scratch

    return [db.name for db in datasette.databases.values() if is_scratch(db)]


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


@hookimpl
def top_database(datasette, request, database):
    from datasette.imports.scratch import is_scratch

    db = datasette.databases.get(database)
    if db is None or not is_scratch(db) or not db.save_command:
        return None
    h = markupsafe.escape
    tables = ", ".join(
        f'<a href="{h(datasette.urls.table(database, loaded.table))}">'
        f"{h(loaded.table)}</a>"
        for loaded in db.loaded
        if loaded.created
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
    if db.from_stdin:
        parts.append(
            "<p>The <code>-</code> argument read standard input, so pipe the "
            "same data in to that command again.</p>"
        )
    parts.append(
        "<p>Then <code>datasette data.db</code> opens it without loading "
        "anything.</p></div>"
    )
    return markupsafe.Markup("".join(parts))
