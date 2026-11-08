"""
``POST /<database>/-/upload``: create a table from an uploaded file.

The file is saved to a private temporary directory and checked - is it
empty, is it a SQLite database, does a loader recognize it - before
anything else happens, so those errors come straight back. Then it is
imported in the background like a file on the ``datasette data.csv``
command line (:mod:`datasette.imports.background`): a worker subprocess
runs the loader, plugin loaders included, and the rows are inserted here a
chunk at a time. The response is a ``202`` with the import's progress and
the URLs to follow it, and the table page shows the progress until it is
done.

- Only uploaded files, never URLs: the server fetches nothing on behalf of
  a request.
- The uploaded file's name is only used to pick the default table name
  and to describe the import; the file is always saved as ``upload`` in a
  directory made for it, which is removed when the import ends however it
  ends (finished, failed, cancelled, or Datasette shutting down normally)
  and straight away for an upload that is refused.
- An import that fails or is cancelled drops its table: nothing
  half-imported is left in the database (``on_error="drop"``). Only a
  process that is killed outright (``SIGKILL``, a crash) part way through
  an import leaves its partial table, and its upload directory, behind.
- Needs ``create-table`` on the database and ``insert-row`` on the new
  table, like ``/<database>/-/create`` with rows. Never into the read-only
  ``scratch`` database or an immutable one.
- At most :ref:`setting_max_upload_mb` megabytes.
- Not where subprocesses cannot be started (Pyodide), for now.
- Imports share the import worker slots
  (:func:`~datasette.imports.subprocess_import.worker_slots`), so uploads
  beyond those wait, queued, for a turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import urllib.parse

from datasette.events import (
    AlterTableEvent,
    CreateTableEvent,
    DropTableEvent,
    InsertRowsEvent,
)
from datasette.imports.sources import strip_invisible
from datasette.resources import DatabaseResource, TableResource
from datasette.utils.asgi import BadRequest, DatabaseNotFound, Response
from datasette.utils.multipart import UploadedFile

from .base import BaseView

logger = logging.getLogger(__name__)

# Room in the request body for the multipart boundaries, part headers and
# the table and format fields, on top of the file itself
FORM_OVERHEAD_BYTES = 64 * 1024
MAX_FIELD_BYTES = 16 * 1024
MAX_FIELDS = 20
COPY_CHUNK_BYTES = 1024 * 1024
# What the multipart parser says when a limit is exceeded
_TOO_LARGE_MESSAGES = ("File too large", "Request body too large")
# The uploaded file is always saved under this name: the name the browser
# sent is never used as a path
SAVED_NAME = "upload"


class UploadRefused(Exception):
    "An upload that cannot be imported: becomes a JSON error response."

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def max_upload_bytes(datasette) -> int | None:
    "The :ref:`setting_max_upload_mb` limit in bytes, or None for no limit."
    megabytes = datasette.setting("max_upload_mb")
    if not megabytes or megabytes < 0:
        return None
    return int(megabytes) * 1024 * 1024


def uploads_available() -> bool:
    "False where the import worker subprocess cannot run (Pyodide)."
    from datasette.imports.subprocess_import import subprocesses_available

    return subprocesses_available()


def upload_formats() -> list[dict]:
    "The formats the import loaders read: ``name`` and ``description``."
    from datasette.imports.loaders import get_loaders

    return sorted(
        (
            {"name": loader.name, "description": loader.description or loader.name}
            for loader in get_loaders()
        ),
        key=lambda item: item["name"],
    )


def upload_path(datasette, database: str) -> str:
    return datasette.urls.database(database) + "/-/upload"


def upload_ui_context(datasette, db, can_insert_rows) -> dict | None:
    """
    What the create table dialog needs to offer importing a file on the
    server - ``path``, ``maxBytes`` (``null`` for no limit) and
    ``formats`` - or None if it should parse files in the browser instead:
    uploads are unavailable here, or the actor may not insert rows.
    ``can_insert_rows`` is the database-level ``insert-row`` check that
    decides whether the dialog offers creating a table from data at all.
    """
    from datasette.imports.scratch import is_scratch

    if not uploads_available() or not db.is_mutable or is_scratch(db):
        return None
    if not can_insert_rows:
        return None
    return {
        "path": upload_path(datasette, db.name),
        "maxBytes": max_upload_bytes(datasette),
        "formats": upload_formats(),
    }


def display_filename(filename: str | None) -> str:
    """
    The uploaded file's name, for messages, the import's ``source`` and
    the lines printed to the terminal: without any directories a browser
    sent, control or formatting characters (bidirectional overrides,
    say) or excessive length.
    """
    name = re.split(r"[\\/]", filename or "")[-1]
    name = strip_invisible(name).strip()
    if len(name) > 200:
        name = name[:197] + "..."
    return name or "uploaded file"


def default_table_name(filename: str | None) -> str:
    "The table name the command line would give a file with this name."
    from datasette.imports.sources import table_name_for_path

    name = re.split(r"[\\/]", filename or "")[-1]
    return table_name_for_path(strip_invisible(name))


def check_table_name(name: str) -> None:
    if "\n" in name or "\r" in name:
        raise UploadRefused("Table name cannot contain newlines")
    if "\x00" in name:
        raise UploadRefused("Table name cannot contain NUL characters")
    if strip_invisible(name) != name:
        # Escape sequences, bidirectional overrides, zero-width characters:
        # the name is shown on pages and printed to the terminal
        raise UploadRefused(
            "Table name cannot contain control or formatting characters"
        )
    if name.lower().startswith("sqlite_"):
        raise UploadRefused("Table names starting with sqlite_ are reserved by SQLite")


def resolve_format(name: str | None) -> str | None:
    "The loader name for a ``format`` field, or None to detect the format."
    from datasette.imports.loaders import get_loaders
    from datasette.imports.sources import FORMAT_ALIASES

    if name is None or not name.strip():
        return None
    key = name.strip().lower()
    key = FORMAT_ALIASES.get(key, key)
    names = sorted(loader.name for loader in get_loaders())
    if key not in names:
        raise UploadRefused(
            f"Unknown format {name.strip()!r}, available formats: {', '.join(names)}"
        )
    return key


def _empty_json_array(first_bytes: bytes) -> bool:
    """
    Is this the whole of a JSON array with no rows in it - ``[]``, or
    objects with no keys? The ``json`` loader reads that without an error,
    but finds no rows and no columns, so no table could be created.
    """
    from datasette.imports.loaders import peek_json

    peek = peek_json(first_bytes)
    return (
        peek is not None
        and peek.complete
        and not peek.trailing
        and isinstance(peek.value, list)
        and all(isinstance(item, dict) and not item for item in peek.value)
    )


def sniff_upload(path: str, filename: str, format_name: str | None):
    """
    The loader for the saved upload, run in a thread: ``format_name``'s if
    given, otherwise the one that recognizes the start of the file. Raises
    :class:`UploadRefused` for an empty file, a SQLite database, a format
    nothing recognizes, or a JSON array with no rows in it.
    """
    from datasette.imports import loaders as _loaders
    from datasette.imports.sources import SQLITE_HEADER

    loaders = _loaders.get_loaders()
    with open(path, "rb") as fp:
        first_bytes = fp.read(_loaders.SNIFF_BYTES)
    if first_bytes.startswith(SQLITE_HEADER):
        raise UploadRefused(
            f"{filename} is a SQLite database. Uploads create a table from "
            "a data file - to serve a database file, start Datasette with it"
        )
    if format_name is not None:
        loader = _loaders.get_loader(format_name, loaders)
    else:
        loader = _sniff(path, filename, loaders)
    if loader.name == "json" and _empty_json_array(first_bytes):
        raise UploadRefused(
            f"{filename} has no rows to import: it is a JSON array with no "
            "objects in it, or only empty ones"
        )
    return loader


def _sniff(path, filename, loaders):
    from datasette.imports import loaders as _loaders

    try:
        first_bytes, scores = _loaders.sniff_path(path, loaders)
        return _loaders.choose_loader(scores, first_bytes)
    except _loaders.EmptyFile:
        raise UploadRefused(f"{filename} is empty") from None
    except _loaders.UnrecognizedFormat as ex:
        # The loaders' scores are for whoever debugs a loader, not the reader
        logger.debug("Could not recognize %s: %s", filename, ex)
        names = ", ".join(sorted(loader.name for loader in loaders))
        raise UploadRefused(
            f"Could not import {filename}: it does not look like any format "
            f"Datasette can read ({names}). To import it as one of these, "
            "choose that format"
        ) from None


async def _save(uploaded: UploadedFile, path: str) -> int:
    "Copy the parser's spooled upload to ``path``; returns the size."
    await uploaded.seek(0)
    size = 0
    # "x": never follow or replace something already at that path
    out = await asyncio.to_thread(open, path, "xb")
    try:
        while True:
            chunk = await uploaded.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            await asyncio.to_thread(out.write, chunk)
    finally:
        await asyncio.to_thread(out.close)
    return size


async def _name_taken_by(db, table: str) -> str | None:
    """
    The type of what is already called ``table`` - ``"table"``, ``"view"``
    or ``"index"``, which share one namespace in SQLite - or None.
    Case-insensitive, as SQLite compares names.
    """
    row = (
        await db.execute(
            "select type from sqlite_master where type in ('table', 'view', "
            "'index') and name = ? collate nocase",
            [table],
        )
    ).first()
    return row[0] if row is not None else None


async def _table_schema(db, table: str) -> str | None:
    row = (
        await db.execute(
            "select sql from sqlite_master where type = 'table' and name = ?",
            [table],
        )
    ).first()
    return row[0] if row is not None else None


class UploadEvents:
    """
    A registry listener for one upload's import that fires the events for
    the changes the uploading actor made, in order: ``create-table`` once
    the import has created the table, ``alter-table`` if the import
    changed the type of a column after that (``integer`` to ``float``,
    say, for a value further down the file), and ``insert-rows`` with the
    number of rows once it has finished. If the import fails or is
    cancelled after creating the table, the table is dropped
    (``on_error="drop"``) and ``drop-table`` follows the ``create-table``.
    An import that never created a table fires nothing.

    The ``drop-table`` after a failed import is the uploading actor's, as
    the consequence of their upload. After a cancellation it is the actor
    who cancelled it (``TableImport.cancelled_by``) - perhaps someone else
    with the ``cancel-import`` permission - or ``None`` if Datasette
    shutting down cancelled it.
    """

    def __init__(self, datasette, actor, job):
        self.datasette = datasette
        self.actor = actor
        self.job = job
        self.created_schema = None
        self._created = False
        self._finished = False
        self._tail = None
        # Strong references to the tasks firing events, until they are done
        self._tasks = set()

    def __call__(self, job, event):
        if job is not self.job or self._finished:
            return
        if job.table_exists and not self._created:
            self._created = True
            self._schedule(self._fire_create)
        if job.active:
            return
        self._finished = True
        job.registry.remove_listener(self)
        if job.table_exists:
            self._schedule(self._fire_finished, job.rows)
        elif self._created:
            actor = job.cancelled_by if job.state == "cancelled" else self.actor
            self._schedule(self._fire_drop, actor)

    def _schedule(self, fire, *args):
        previous = self._tail

        async def run():
            if previous is not None:
                # In order: wait for the event before this one
                await asyncio.wait([previous])
            try:
                await fire(*args)
            except Exception:
                logger.exception("Could not track an event for %r", self.job)

        task = asyncio.ensure_future(run())
        self._tail = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fire_create(self):
        job = self.job
        self.created_schema = await _table_schema(job.db, job.table) or ""
        await self.datasette.track_event(
            CreateTableEvent(
                self.actor,
                database=job.database,
                table=job.table,
                schema=self.created_schema,
            )
        )

    async def _fire_finished(self, rows):
        job = self.job
        schema = await _table_schema(job.db, job.table)
        if schema is not None and self.created_schema not in (None, schema):
            await self.datasette.track_event(
                AlterTableEvent(
                    self.actor,
                    database=job.database,
                    table=job.table,
                    before_schema=self.created_schema,
                    after_schema=schema,
                )
            )
        if rows:
            await self.datasette.track_event(
                InsertRowsEvent(
                    self.actor,
                    database=job.database,
                    table=job.table,
                    num_rows=rows,
                    ignore=False,
                    replace=False,
                )
            )

    async def _fire_drop(self, actor):
        job = self.job
        await self.datasette.track_event(
            DropTableEvent(actor, database=job.database, table=job.table)
        )


async def _client_disconnected(request) -> bool:
    """
    Has the client gone - closed the connection after sending all of the
    file, while it was being saved and checked? Then nobody is waiting to
    hear that its import started. Looks without waiting: once the body has
    been read, ASGI servers answer ``receive()`` with ``http.disconnect``
    at once if the client has gone, and otherwise not until it does.
    """
    receive = asyncio.ensure_future(request.receive())
    await asyncio.sleep(0)
    if not receive.done():
        receive.cancel()
        # Let it finish, without raising whatever it raised
        await asyncio.wait([receive])
    if receive.cancelled() or receive.exception() is not None:
        return False
    message = receive.result()
    return isinstance(message, dict) and message.get("type") == "http.disconnect"


def _error(message, status=400):
    return Response.error([message], status)


class TableImportView(BaseView):
    name = "table-import"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        from datasette.imports.scratch import is_scratch

        try:
            db = await self.ds.resolve_database(request)
        except DatabaseNotFound:
            return _error("Database not found", 404)
        if not uploads_available():
            return _error(
                "Uploads are not available here: importing a file needs a "
                "worker subprocess, which this platform cannot start"
            )
        if is_scratch(db):
            return _error(
                f"{db.name} is a temporary, read-only database: upload into "
                "another database",
                403,
            )
        if not db.is_mutable:
            return _error("Database is immutable", 403)
        if not await self.ds.allowed(
            action="create-table",
            resource=DatabaseResource(database=db.name),
            actor=request.actor,
        ):
            return _error("Permission denied: need create-table", 403)

        limit = max_upload_bytes(self.ds)
        too_large = (
            f"File too large: uploads are limited to "
            f"{self.ds.setting('max_upload_mb')} MB"
        )
        if limit is not None:
            try:
                declared = int(request.headers.get("content-length", ""))
            except ValueError:
                declared = None
            if declared is not None and declared > limit + FORM_OVERHEAD_BYTES:
                return _error(too_large, 413)

        # Unlimited: the parser still needs numbers to compare with
        no_limit = 2**62
        try:
            form = await request.form(
                files=True,
                max_file_size=limit if limit is not None else no_limit,
                max_request_size=(
                    limit + FORM_OVERHEAD_BYTES if limit is not None else no_limit
                ),
                max_files=1,
                max_fields=MAX_FIELDS,
                max_field_size=MAX_FIELD_BYTES,
            )
        except BadRequest as ex:
            message = str(ex)
            if message in _TOO_LARGE_MESSAGES:
                return _error(too_large, 413)
            if message.startswith(("Missing Content-Type", "Unsupported Content-Type")):
                message = "Send the file as multipart/form-data, in a field called file"
            return _error(message)
        async with form:
            try:
                return await self._import(request, db, form)
            except UploadRefused as ex:
                return _error(ex.message, ex.status)

    async def _import(self, request, db, form):
        from datasette.imports.background import import_registry
        from datasette.imports.scratch import TableExists, is_reserved
        from datasette.imports.sources import Source, remove_tree

        uploaded = form.get("file")
        if uploaded is None:
            raise UploadRefused(
                "No file uploaded: send it as multipart/form-data, in a field "
                "called file"
            )
        if uploaded == "":
            # What a form with an empty file input sends, from some clients
            raise UploadRefused("No file uploaded: the file field is empty")
        if not isinstance(uploaded, UploadedFile):
            raise UploadRefused(
                "file must be an uploaded file - URLs and pasted text are not "
                "accepted"
            )
        filename = display_filename(uploaded.filename)
        if uploaded.size == 0:
            if not uploaded.filename:
                raise UploadRefused("No file uploaded: the file field is empty")
            raise UploadRefused(f"{filename} is empty")

        table = form.get("table")
        if not isinstance(table, str) or not table.strip():
            table = default_table_name(uploaded.filename)
        table = table.strip()
        check_table_name(table)
        format_field = form.get("format")
        format_name = resolve_format(
            format_field if isinstance(format_field, str) else None
        )

        if not await self.ds.allowed(
            action="insert-row",
            resource=TableResource(database=db.name, table=table),
            actor=request.actor,
        ):
            raise UploadRefused("Permission denied: need insert-row", 403)

        registry = import_registry(self.ds)
        running = registry.get(db.name, table)
        if (running is not None and running.active) or is_reserved(db, table):
            raise UploadRefused(
                f"Table {table!r} is already being imported into {db.name}", 409
            )
        taken_by = await _name_taken_by(db, table)
        if taken_by is not None:
            raise UploadRefused(
                f"{taken_by.capitalize()} {table!r} already exists in "
                f"{db.name}: choose another table name",
                409,
            )

        directory = await asyncio.to_thread(
            tempfile.mkdtemp, prefix="datasette-upload-"
        )
        try:
            path = os.path.join(directory, SAVED_NAME)
            await _save(uploaded, path)
            # Release the parser's copy now rather than when the response
            # has been sent
            await uploaded.close()
            loader = await asyncio.to_thread(sniff_upload, path, filename, format_name)
            if await _client_disconnected(request):
                # Gone while a large file was saved and checked - the page
                # was closed, say. Nobody would know this import had
                # started, and only root could cancel it
                raise UploadRefused("The client disconnected before the import started")
            source = Source(
                filename,
                "file",
                path,
                table=table,
                path=path,
                temporary=True,
                loader=loader,
            )
            try:
                job = registry.start(
                    db,
                    source,
                    title=filename,
                    on_error="drop",
                    on_finish=lambda _job: remove_tree(directory),
                )
            except TableExists as ex:
                raise UploadRefused(str(ex), 409) from None
        except BaseException:
            await asyncio.to_thread(remove_tree, directory)
            raise
        # Nothing can have happened to the import yet: its task has not had
        # a chance to run
        registry.add_listener(UploadEvents(self.ds, request.actor, job))

        table_path = self.ds.urls.table(db.name, table)
        imports_path = (
            self.ds.urls.path("-/imports.json")
            + "?"
            + urllib.parse.urlencode({"database": db.name, "table": table})
        )
        return Response.json(
            {
                "ok": True,
                "database": db.name,
                "table": table,
                "table_url": self.ds.absolute_url(request, table_path),
                "import": job.to_dict(),
                "imports_url": self.ds.absolute_url(request, imports_path),
                "cancel_url": self.ds.absolute_url(
                    request, table_path + "/-/cancel-import"
                ),
            },
            status=202,
        )
