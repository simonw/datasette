"""
Background imports: loading a file, a URL or standard input into a new table
while Datasette serves requests.

Every import is a :class:`TableImport` in the Datasette instance's
:class:`ImportRegistry` (:func:`import_registry`), keyed by database and
table, and runs as a task of its own under ``datasette.add_background_task()``
called ``import DATABASE.TABLE``, so it is listed in ``/-/tasks``. The
registry is the single source of truth for the progress notices on the
homepage, database and table pages, for ``/-/imports.json``, for the lines
the command line prints, and for cancelling. The command line registers one
import per input; an upload endpoint can do the same with the file it saved::

    source = Source(name, "file", path, path=path, temporary=True, table=table)
    job = import_registry(datasette).start(db, source, title=name)

Life of an import::

    queued -> [downloading] -> [queued] -> importing -> done | failed | cancelled

- ``queued``: registered and waiting - for background tasks to start
  (``waiting_for="start"``) or for one of the import worker slots
  (``waiting_for="worker"``, see
  :func:`~datasette.imports.subprocess_import.worker_slots`)
- ``downloading``: a URL is downloaded, or standard input or a pipe copied,
  to a temporary file, with byte progress (and the total, for downloads
  that send ``Content-Length``)
- ``importing``: a worker subprocess parses the file
  (:func:`~datasette.imports.subprocess_import.import_file`) and the rows are
  inserted here a chunk per ``execute_write_fn()`` call, so they can be
  queried as they arrive. ``fraction`` is how far the worker has read into
  the file
- ``done``, ``failed``, ``cancelled``: finished.

When an import fails or is cancelled, ``on_error`` decides what happens to
the rows imported so far, as it does for
:func:`~datasette.imports.subprocess_import.import_file`: ``"keep"`` (the
default, and what the command line uses for ``scratch``) keeps them - the
table holds exactly the first ``rows_kept`` rows of the input - and the
import stays in the registry as failed or cancelled, which the pages show as
an "incomplete table" notice. ``"drop"`` (what the command line uses for a
``--save`` file, which has nowhere to record that a table is incomplete)
drops the table. Either way only a table this import created is touched.

The names of the tables being imported are reserved in this process with
:func:`~datasette.imports.scratch.reserve_table` from :meth:`ImportRegistry.start`
until the import ends, the same reservation
:func:`~datasette.imports.scratch.load_source` uses, so two imports can
never both create a table.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import itertools
import logging
import os
import sqlite3
import sys
import tempfile
import time
import weakref
from collections.abc import Callable

from datasette.imports import loaders as _loaders
from datasette.imports.scratch import (
    DEFAULT_BATCH_SIZE,
    ImportFailed,
    TableExists,
    drop_table,
    import_writes,
    is_scratch,
    release_table,
    reserve_table,
    sniff,
)
from datasette.imports.sources import (
    LARGE_DOWNLOAD_BYTES,
    Source,
    SourceError,
    display_spec,
    fetch,
    human_size,
    remove_temporary,
    remove_tree,
)
from datasette.imports.subprocess_import import (
    WorkerImportError,
    import_file,
    worker_slots,
)

logger = logging.getLogger(__name__)

ACTIVE_STATES = ("queued", "downloading", "importing")
FINISHED_STATES = ("done", "failed", "cancelled")
STATES = ACTIVE_STATES + FINISHED_STATES
ON_ERROR = ("keep", "drop")
# Finished imports kept in the registry, beyond which the oldest are
# forgotten (an upload endpoint could otherwise grow it without bound)
MAX_FINISHED = 200


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


class TableImport:
    """
    One import into one table: its progress and outcome, and a handle to
    cancel it. Created by :meth:`ImportRegistry.start`; read the attributes,
    :attr:`fraction`, :meth:`describe` or :meth:`to_dict`.
    """

    def __init__(
        self,
        registry,
        db,
        source: Source,
        *,
        title,
        on_error,
        workdir,
        sample_size,
        batch_size,
        use_worker,
        loaders,
        delete_input,
        before,
        on_fetched,
        on_finish,
        worker_options,
    ):
        self.registry = registry
        self.id = str(next(registry._ids))
        self.db = db
        self.source = source
        #: Database and table names
        self.database = db.name
        self.table = source.table
        #: How to refer to the input: a path, a URL (credentials redacted),
        #: "standard input", an uploaded file's name
        self.title = title
        #: "file", "url" or "stdin" (``Source.kind``)
        self.kind = source.kind
        self.state = "queued"
        #: While queued: "start" (background tasks have not started yet) or
        #: "worker" (waiting for a worker slot)
        self.waiting_for = "start"
        self.on_error = on_error
        self.loader = source.loader.name if source.loader is not None else None
        #: Rows committed to the table so far (on success: rows in the table)
        self.rows = 0
        #: How far the worker has read into the file, and the file's size
        self.bytes = None
        self.size = None
        #: Bytes downloaded (or read from standard input) and the expected
        #: total, if the server said
        self.downloaded = None
        self.download_size = None
        self.created_at = _now_iso()
        self.started_at = None
        self.finished_at = None
        self._started = None
        self._import_started = None
        self._finished = None
        #: The error message, for a failed import
        self.error = None
        #: Whether the table exists: created by this import and not dropped
        self.table_exists = False
        #: True once every row of the input is in the table
        self.complete = False
        self.widenings = []
        self.warnings = []
        #: The worker's process ID while it runs
        self.pid = None
        #: The :class:`~datasette.imports.subprocess_import.WorkerImportResult`
        self.result = None
        #: The :class:`~datasette.background_tasks.BackgroundTask`
        self.task = None
        self.cancel_requested = False
        self._workdir = workdir
        self._sample_size = sample_size
        self._batch_size = batch_size
        self._use_worker = use_worker
        self._loaders = loaders
        self._delete_input = delete_input
        self._before = before
        self._on_fetched = on_fetched
        self._on_finish = on_finish
        self._worker_options = worker_options or {}
        self._done = asyncio.Event()
        self._released = False

    def __repr__(self):
        return (
            f"<TableImport {self.database}.{self.table} state={self.state!r} "
            f"rows={self.rows}>"
        )

    # Reading ----------------------------------------------------------------

    @property
    def key(self):
        return (self.database, self.table)

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def fraction(self) -> float | None:
        """
        From 0.0 to 1.0: how much has been downloaded, while downloading,
        and how far into the file the worker has read, while importing.
        ``None`` when that is not known.
        """
        if self.state == "done":
            return 1.0
        if self.state == "downloading":
            if self.downloaded is not None and self.download_size:
                return min(self.downloaded / self.download_size, 1.0)
            return None
        if self.state == "importing" and self.bytes is not None and self.size:
            if not self.bytes and self.rows:
                # Rows but no bytes read: the loader has the file open in a
                # way the worker cannot see into (the JSON loader reports 0
                # throughout), so the position says nothing
                return None
            return min(self.bytes / self.size, 1.0)
        return None

    @property
    def percent(self) -> int | None:
        fraction = self.fraction
        if fraction is None:
            return None
        percent = int(fraction * 100)
        # 100% only once it is done
        return percent if self.state == "done" else min(percent, 99)

    @property
    def rows_kept(self) -> int | None:
        "Rows left in the table by a failed or cancelled import."
        if self.state not in ("failed", "cancelled"):
            return None
        return self.rows if self.table_exists else 0

    @property
    def seconds(self) -> float | None:
        "Seconds since the import started, or that it took."
        if self._started is None:
            return None
        end = self._finished if self._finished is not None else time.monotonic()
        return end - self._started

    def _outcome(self) -> str:
        kept = self.rows_kept or 0
        if self.on_error == "drop":
            return "the table was removed" if self.rows else "no table was created"
        if kept:
            return f"the table has the first {_plural(kept, 'row')}"
        return "no rows were imported"

    def status(self) -> str:
        """
        What is happening, without the table name: ``importing 40%
        (2,560,000 rows)``, ``queued, waiting for an import worker``,
        ``import failed: ... (the table has the first 3,000 rows)``.
        """
        if self.state == "queued":
            if self.waiting_for == "worker":
                return "queued, waiting for an import worker"
            return "queued"
        if self.state == "downloading":
            verb = "reading standard input" if self.kind == "stdin" else "downloading"
            done = human_size(self.downloaded or 0)
            if self.download_size:
                total = human_size(self.download_size)
                return f"{verb} {self.percent}% ({done} of {total})"
            return f"{verb}: {done}"
        if self.state == "importing":
            rows = _plural(self.rows, "row")
            if self.percent is None:
                return f"importing: {rows} so far"
            return f"importing {self.percent}% ({rows})"
        if self.state == "done":
            if not self.table_exists:
                return "imported: no rows, so no table was created"
            return f"imported {_plural(self.rows, 'row')}"
        if self.state == "failed":
            return f"import failed: {self.error} ({self._outcome()})"
        return f"import cancelled ({self._outcome()})"

    def describe(self) -> str:
        """
        One line of text for people, with the table name: ``importing
        events: 40% (2,560,000 rows)``, ``import of events failed: ...``.
        """
        name = self.table
        if self.state == "queued":
            if self.waiting_for == "worker":
                return f"queued {name}: waiting for an import worker"
            return f"queued {name}"
        if self.state == "downloading":
            verb = (
                "reading standard input for" if self.kind == "stdin" else "downloading"
            )
            done = human_size(self.downloaded or 0)
            if self.download_size:
                total = human_size(self.download_size)
                return f"{verb} {name}: {self.percent}% ({done} of {total})"
            return f"{verb} {name}: {done}"
        if self.state == "importing":
            rows = _plural(self.rows, "row")
            if self.percent is None:
                return f"importing {name}: {rows} so far"
            return f"importing {name}: {self.percent}% ({rows})"
        if self.state == "done":
            if not self.table_exists:
                return f"imported {name}: no rows, so no table was created"
            return f"imported {name}: {_plural(self.rows, 'row')}"
        if self.state == "failed":
            return f"import of {name} failed: {self.error} ({self._outcome()})"
        return f"import of {name} was cancelled ({self._outcome()})"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "database": self.database,
            "table": self.table,
            "source": self.title,
            "kind": self.kind,
            "state": self.state,
            "waiting_for": self.waiting_for if self.state == "queued" else None,
            "loader": self.loader,
            "rows": self.rows,
            "bytes": self.bytes,
            "size": self.size,
            "downloaded": self.downloaded,
            "download_size": self.download_size,
            "fraction": self.fraction,
            "percent": self.percent,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "seconds": (round(self.seconds, 3) if self.seconds is not None else None),
            "error": self.error,
            "on_error": self.on_error,
            "complete": self.complete,
            "table_exists": self.table_exists,
            "rows_kept": self.rows_kept,
            "widenings": [
                {
                    "column": w.column,
                    "from": w.from_type,
                    "to": w.to_type,
                    "at_row": w.at_row,
                }
                for w in self.widenings
            ],
            "task": self.task.name if self.task is not None else None,
            "status": self.status(),
            "description": self.describe(),
        }

    # Control ----------------------------------------------------------------

    def cancel(self) -> bool:
        """
        Cancel the import if it is still running: its worker is killed and,
        with ``on_error="keep"``, the rows already imported stay. Returns
        False if it had already finished. Use :meth:`wait` to wait for the
        cancellation to finish.
        """
        self._reconcile()
        if not self.active:
            return False
        self.cancel_requested = True
        handle = self.task
        if handle is not None:
            # Cancels the asyncio task, or - if background tasks have not
            # started yet - takes it off the list of tasks to start
            handle.cancel()
        if self._started is None:
            # _run() never runs now: a task cancelled before its first step
            # never enters its coroutine
            self._finish("cancelled")
            self._cleanup(fetched_here=False)
        return True

    async def wait(self, timeout: float | None = None) -> bool:
        "Wait until the import has finished. False if ``timeout`` ran out."
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._reconcile()
            if self._done.is_set():
                return True
            step = 0.5
            if deadline is not None:
                step = min(step, deadline - time.monotonic())
                if step <= 0:
                    return False
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._done.wait(), step)

    def _reconcile(self):
        """
        Notice a task that ended without running _run(): cancelled before
        its first step (by the supervisor at shutdown, say), so _run()'s
        own clean-up never happened.
        """
        handle = self.task
        if (
            self.active
            and self._started is None
            and handle is not None
            and handle.task is not None
            and handle.task.done()
        ):
            self._finish("cancelled")
            self._cleanup(fetched_here=False)

    # Running ----------------------------------------------------------------

    def _set_state(self, state, waiting_for=None):
        self.state = state
        self.waiting_for = waiting_for
        event = "waiting" if state == "queued" else state
        self.registry._notify(self, event)

    def _on_download(self, done, total):
        self.downloaded = done
        self.download_size = total
        self.registry._notify(self, "progress")

    def _on_progress(self, progress):
        self.rows = progress.rows_written
        self.bytes = progress.bytes
        self.size = progress.size
        self.pid = progress.pid
        if progress.loader:
            self.loader = progress.loader
        created = bool(progress.rows_written) and not self.table_exists
        if created:
            self.table_exists = True
        self.registry._notify(self, "progress")
        if created:
            # The first chunk has created the table. Datasette refreshes its
            # catalog of tables at most once a second, as requests arrive:
            # until then the table page would be a 404
            return self._refresh_catalog()
        return None

    async def _refresh_catalog(self):
        datasette = self.registry.datasette
        if datasette is None:
            return
        try:
            await datasette.refresh_schemas(force=True)
        except Exception:
            logger.exception("Could not refresh the schema for %r", self)

    def _finish(self, state, error=None):
        self.state = state
        self.waiting_for = None
        self.error = error
        self.finished_at = _now_iso()
        self._finished = time.monotonic()
        self.pid = None
        self._done.set()
        if self._on_finish is not None:
            try:
                self._on_finish(self)
            except Exception:
                logger.exception("on_finish callback for %r failed", self)
        self.registry._notify(self, state)

    def _cleanup(self, fetched_here):
        if not self._released:
            self._released = True
            release_table(self.db, self.table)
        source = self.source
        if source.temporary and (fetched_here or self._delete_input):
            remove_temporary([source])
        if self._private_dir is not None:
            remove_tree(self._private_dir)
            self._private_dir = None

    _private_dir = None

    def _fetch_dir(self):
        if self._workdir is not None:
            return self._workdir
        self._private_dir = tempfile.mkdtemp(prefix="datasette-import-")
        return self._private_dir

    async def _run(self, datasette):
        db = self.db
        source = self.source
        fetched_here = False
        self.started_at = _now_iso()
        self._started = time.monotonic()
        try:
            if self._before is not None:
                await self._before(self)
            if source.path is None:
                copy = source.needs_copy
                if copy:
                    self._set_state("downloading")
                await fetch(
                    source,
                    self._fetch_dir() if copy else None,
                    progress=self._on_download,
                )
                fetched_here = True
                if copy and self._on_fetched is not None:
                    self._on_fetched(self)
            if source.loader is None:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, sniff, source, self._loaders)
            self.loader = source.loader.name
            self._set_state("queued", "worker")
            # Waiters get a slot in the order they asked, so files load in
            # the order they were given - nothing above waits for a local
            # file that has been sniffed already
            async with worker_slots(datasette):
                if await _table_taken(db, self.table):
                    raise TableExists(
                        f"Table {self.table!r} already exists in {db.name}"
                    )
                self._set_state("importing")
                self._import_started = time.monotonic()
                with import_writes():
                    result = await import_file(
                        db,
                        self.table,
                        source.path,
                        use_worker=self._use_worker,
                        format=self.loader,
                        sample_size=self._sample_size,
                        batch_size=self._batch_size,
                        progress=self._on_progress,
                        on_error=self.on_error,
                        # This import holds a slot already
                        slots=contextlib.nullcontext(),
                        **self._worker_options,
                    )
            self._succeeded(result)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._count_kept_rows())
            finally:
                self._finish("cancelled")
            raise
        except WorkerImportError as ex:
            self._take_result(ex.result)
            self._finish("failed", str(ex))
        except (SourceError, _loaders.LoaderError) as ex:
            self._finish("failed", str(ex))
        except Exception as ex:
            # sqlite3.Error or OSError from inserting (a full disk, a
            # write_wrapper plugin refusing), or a bug. The rows committed
            # before it stay, as for a worker failure. Anything but an
            # sqlite3.Error or OSError is re-raised, so the background task
            # supervisor logs its traceback
            expected = isinstance(ex, (sqlite3.Error, OSError))
            try:
                await asyncio.shield(self._count_kept_rows())
            finally:
                message = str(ex) if expected else f"{type(ex).__name__}: {ex}"
                self._finish("failed", message)
            if not expected:
                raise
        finally:
            self._cleanup(fetched_here)

    def _take_result(self, result):
        if result is None:
            return
        self.result = result
        self.table_exists = result.table_exists
        if result.table_exists:
            self.rows = result.rows
        elif result.import_result is not None:
            # Dropped (on_error="drop"): say how many rows went with it
            self.rows = result.import_result.rows
        self.warnings = list(result.warnings)
        if result.import_result is not None:
            self.widenings = list(result.import_result.widenings)
        if result.size is not None:
            self.size = result.size

    def _succeeded(self, result):
        self._take_result(result)
        self.complete = True
        self.bytes = self.size
        self._finish("done")

    async def _count_kept_rows(self):
        """
        After a cancellation or an insert that failed: is the table there,
        and how many rows does it have? The worker import has finished its
        writes by the time it re-raises (its clean-up writes queue behind
        them), so this sees the final state.

        A cancellation can arrive while the chunk that creates the table is
        being written, before any progress was reported. In scratch -
        where only imports write, and this name is reserved - a table that
        exists by then is this import's own. Anywhere else it is left alone
        unless this import is known to have written to it.
        """
        db = self.db
        try:
            exists = await _table_taken(db, self.table)
            ours = exists and (self.rows > 0 or self.table_exists or is_scratch(db))
            if not ours:
                self.table_exists = False
                return
            if self.on_error == "drop":
                await drop_table(db, self.table)
                self.table_exists = False
                return
            count = (
                await db.execute(f"select count(*) from {_quote(self.table)}")
            ).first()[0]
        except Exception:  # noqa: BLE001 - only a count for the notice
            return
        self.rows = count
        self.table_exists = True


def _quote(name: str) -> str:
    return "[{}]".format(name.replace("]", "]]"))


async def _table_taken(db, table: str) -> bool:
    result = await db.execute(
        "select 1 from sqlite_master where type in ('table', 'view') "
        "and name = ? collate nocase",
        [table],
    )
    return result.first() is not None


def _title(source: Source) -> str:
    if source.kind == "stdin":
        return source.label
    # The argument as typed, credentials redacted, without the table name
    spec = display_spec(source)
    if source.name is not None and spec.startswith(source.name + ":"):
        spec = spec[len(source.name) + 1 :]
    return spec


class ImportRegistry:
    """
    Every background import for one Datasette instance, keyed by database
    and table: get it with :func:`import_registry`. Holds the latest import
    for each table, running or finished, until it is replaced by a new
    import of the same table or there are more than ``MAX_FINISHED``
    finished ones.
    """

    def __init__(self, datasette):
        self._datasette = weakref.ref(datasette)
        self._imports: dict[tuple, TableImport] = {}
        self._listeners: list[Callable] = []
        self._ids = itertools.count(1)
        #: Goes up by one on every change, for cheap "has anything changed"
        self.version = 0

    @property
    def datasette(self):
        return self._datasette()

    @staticmethod
    def _key(database, table):
        # SQLite table names are case-insensitive
        return (database, table.lower())

    def get(self, database: str, table: str) -> TableImport | None:
        job = self._imports.get(self._key(database, table))
        if job is not None:
            job._reconcile()
        return job

    def imports(self, database: str | None = None) -> list[TableImport]:
        "Imports in the order they were started, optionally for one database."
        jobs = [
            job
            for job in self._imports.values()
            if database is None or job.database == database
        ]
        for job in jobs:
            job._reconcile()
        return jobs

    def active(self, database: str | None = None) -> list[TableImport]:
        return [job for job in self.imports(database) if job.active]

    def add_listener(self, listener: Callable) -> None:
        """
        Call ``listener(job, event)`` on every change: ``event`` is
        ``"queued"`` (started), ``"waiting"`` (queued for a worker),
        ``"downloading"``, ``"importing"``, ``"progress"`` (often - after
        each chunk of rows and each progress report from the worker or the
        download), ``"done"``, ``"failed"`` or ``"cancelled"``.
        """
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def _notify(self, job, event):
        self.version += 1
        if job.task is not None:
            job.task.status = job.describe()
        for listener in list(self._listeners):
            try:
                listener(job, event)
            except Exception:
                logger.exception("Import listener %r failed", listener)

    def start(
        self,
        db,
        source: Source,
        *,
        title: str | None = None,
        on_error: str = "keep",
        workdir: str | None = None,
        sample_size: int = 1000,
        batch_size: int = DEFAULT_BATCH_SIZE,
        use_worker: bool | None = None,
        loaders=None,
        delete_input: bool = True,
        before=None,
        on_fetched=None,
        on_finish=None,
        worker_options: dict | None = None,
    ) -> TableImport:
        """
        Import ``source`` into a new table ``source.table`` in ``db``, as a
        background task. Returns the :class:`TableImport` at once.

        Raises :class:`~datasette.imports.scratch.TableExists` if that table
        is being imported already. A table that exists already is found
        when the task runs: the import then fails.

        :param source: what to import. ``source.path`` is the file to load;
            if it is ``None`` the source is fetched first (a URL downloaded,
            standard input or a pipe copied) into ``workdir``, or a private
            temporary directory
        :param title: how to refer to the source, by default the argument
            as it was typed with any credentials in a URL redacted
        :param on_error: ``"keep"`` or ``"drop"`` the rows imported before a
            failure or cancellation, see the module documentation
        :param delete_input: delete ``source.path`` afterwards if it is a
            temporary copy (``source.temporary``), as an uploaded file is.
            A copy that this import fetches itself is always deleted unless
            ``on_fetched`` sets ``source.temporary = False``
        :param before: ``async before(job)``, awaited when the task starts
            (the command line drops the old version of a table there)
        :param on_fetched: ``on_fetched(job)`` once a copy has been fetched
        :param on_finish: ``on_finish(job)`` when the import has finished,
            however it finished
        :param worker_options: passed on to
            :func:`~datasette.imports.subprocess_import.import_file`
        """
        if on_error not in ON_ERROR:
            raise ValueError(f"on_error must be one of {ON_ERROR}")
        if not source.table:
            raise ValueError("source.table is not set: see assign_table_names()")
        datasette = self.datasette
        key = self._key(db.name, source.table)
        current = self._imports.get(key)
        if current is not None and current.active:
            raise TableExists(
                f"Table {source.table!r} is already being imported into {db.name}"
            )
        reserve_table(db, source.table)
        job = TableImport(
            self,
            db,
            source,
            title=title or _title(source),
            on_error=on_error,
            workdir=workdir,
            sample_size=sample_size,
            batch_size=batch_size,
            use_worker=use_worker,
            loaders=loaders,
            delete_input=delete_input,
            before=before,
            on_fetched=on_fetched,
            on_finish=on_finish,
            worker_options=worker_options,
        )
        self._imports.pop(key, None)
        self._imports[key] = job
        self._prune()
        try:
            job.task = datasette.add_background_task(
                job._run, name=f"import {db.name}.{source.table}"
            )
        except BaseException:
            self._imports.pop(key, None)
            job._cleanup(fetched_here=False)
            raise
        if job.task.task is not None:
            # Background tasks are running already, so this one started
            job.waiting_for = None
            job.task.task.add_done_callback(lambda _task: job._reconcile())
        self._notify(job, "queued")
        return job

    def _prune(self):
        finished = [job for job in self._imports.values() if not job.active]
        for job in finished[: max(0, len(finished) - MAX_FINISHED)]:
            self._imports.pop(self._key(job.database, job.table), None)

    def forget(self, database: str, table: str) -> bool:
        "Remove a finished import from the registry."
        job = self.get(database, table)
        if job is None or job.active:
            return False
        del self._imports[self._key(database, table)]
        self.version += 1
        return True

    def cancel(self, database: str, table: str) -> TableImport | None:
        "Cancel the import into that table, if one is running."
        job = self.get(database, table)
        if job is not None:
            job.cancel()
        return job

    def cancel_all(self) -> list[TableImport]:
        jobs = self.active()
        for job in jobs:
            job.cancel()
        return jobs

    async def wait(self, timeout: float | None = None) -> bool:
        """
        Wait until no import is running, including imports started while
        waiting. False if ``timeout`` ran out first.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            jobs = self.active()
            if not jobs:
                return True
            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
            if not await jobs[0].wait(remaining):
                return False


_REGISTRIES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def import_registry(datasette) -> ImportRegistry:
    "The :class:`ImportRegistry` of a Datasette instance, created on first use."
    registry = _REGISTRIES.get(datasette)
    if registry is None:
        registry = ImportRegistry(datasette)
        _REGISTRIES[datasette] = registry
    return registry


def existing_registry(datasette) -> ImportRegistry | None:
    "The registry if anything has used one, without creating it."
    return _REGISTRIES.get(datasette)


# Reporting on a terminal ----------------------------------------------------------


class TerminalReporter:
    """
    A registry listener that prints a line when each import starts, every
    ``interval`` seconds while it runs, and when it finishes - plain lines on
    standard error, suitable for a long-running server whose log is
    interleaved with uvicorn's (no progress bar rewriting itself in place).

    ``where`` is added after "into table NAME" (`` in data.db``).
    ``warn_large`` warns that big downloads are lost at exit (not with
    ``--save``).
    """

    def __init__(
        self, stream=None, *, interval: float = 10.0, where="", warn_large=True
    ):
        self.stream = stream
        self.interval = interval
        self.where = where
        self.warn_large = warn_large
        self._last = {}
        self._warned = set()
        self._started = None
        self._seen = set()

    def _write(self, text):
        stream = self.stream if self.stream is not None else sys.stderr
        try:
            stream.write(text + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass

    def __call__(self, job, event):
        now = time.monotonic()
        if self._started is None:
            self._started = now
        self._seen.add(job.id)
        where = f"table {job.table}{self.where}"
        if event == "downloading":
            self._last[job.id] = now
            if job.kind == "stdin":
                self._write(f"Reading {job.title} for {where}")
            else:
                self._write(f"Downloading {job.title} for {where}")
        elif event == "waiting":
            slots = worker_slots(job.registry.datasette)
            if slots.locked():
                self._write(
                    f"Queued {job.title} for {where}: waiting for an import worker"
                )
        elif event == "importing":
            self._last[job.id] = now
            size = f", {human_size(job.downloaded)}" if job.downloaded else ""
            if job.kind == "file" and job.source.path:
                with contextlib.suppress(OSError):
                    size = f", {human_size(os.path.getsize(job.source.path))}"
            self._write(f"Importing {job.title} ({job.loader}{size}) into {where}")
        elif event == "progress":
            if (
                self.warn_large
                and job.state == "downloading"
                and job.id not in self._warned
                and (job.download_size or job.downloaded or 0) > LARGE_DOWNLOAD_BYTES
            ):
                self._warned.add(job.id)
                size = job.download_size or job.downloaded
                self._write(
                    f"{job.title} is {human_size(size)}"
                    f"{'' if job.download_size else ' so far'}. The scratch "
                    "database is deleted when Datasette stops: add --save "
                    "data.db to keep the data."
                )
            if now - self._last.get(job.id, now) >= self.interval:
                self._last[job.id] = now
                self._write(f"  {job.describe()} after {job.seconds:.0f}s")
        elif event in FINISHED_STATES:
            self._last.pop(job.id, None)
            self._write(self.finished_line(job))
            registry = job.registry
            if not registry.active() and len(self._seen) > 1:
                jobs = [j for j in registry.imports() if j.id in self._seen]
                done = [j for j in jobs if j.state == "done"]
                rows = sum(j.rows for j in done)
                elapsed = now - self._started
                problems = len(jobs) - len(done)
                extra = f", {problems} failed or cancelled" if problems else ""
                self._write(
                    f"Imported {len(done)} of {len(jobs)} inputs "
                    f"({rows:,} rows) in {elapsed:.1f}s{extra}"
                )
                self._seen.clear()
                self._started = None

    def finished_line(self, job) -> str:
        where = f"table {job.table}{self.where}"
        if job.state == "done":
            if not job.table_exists:
                return f"{job.title} has no rows, so no table was created"
            line = (
                f"Loaded {_plural(job.rows, 'row')} from {job.title} "
                f"({job.loader}) into {where} in {job.seconds:.2f}s"
            )
            for widening in job.widenings:
                line += (
                    f"\n  column {widening.column} changed from "
                    f"{widening.from_type} to {widening.to_type} at row "
                    f"{widening.at_row + 1:,}"
                )
            return line
        kept = job.rows_kept or 0
        if job.on_error == "keep" and kept:
            outcome = f"; {where} has the first {_plural(kept, 'row')}"
        elif job.on_error == "drop" and job.rows:
            outcome = f"; the {_plural(job.rows, 'row')} imported were removed"
        else:
            outcome = ""
        if job.state == "failed":
            return f"Failed to import {job.title} into {where}: {job.error}{outcome}"
        datasette = job.registry.datasette
        if datasette is not None and getattr(datasette, "_shutdown_invoked", False):
            return (
                f"Stopped importing {job.title} into {where}, because Datasette "
                f"is shutting down{outcome}"
            )
        return f"Cancelled import of {job.title} into {where}{outcome}"


__all__ = [
    "ACTIVE_STATES",
    "FINISHED_STATES",
    "ImportFailed",
    "ImportRegistry",
    "TableImport",
    "TerminalReporter",
    "existing_registry",
    "import_registry",
]
