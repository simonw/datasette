"""
Background imports: loading a file, a URL or standard input into a new table
while Datasette serves requests.

Every import is a :class:`TableImport` in the Datasette instance's
:class:`ImportRegistry` (:func:`import_registry`). The TableImport is the
record of what the import is doing or did, and the handle to cancel it or
wait for it: the progress notices on the homepage, database and table
pages, ``/-/imports.json``, the lines the command line prints and the
registry's listeners all read it. It runs as a task of its own under
``datasette.add_background_task()``, called ``import DATABASE.TABLE``, so
it is listed in ``/-/tasks``. That task is an :class:`ImportRun`, which
once :meth:`ImportRegistry.start` has returned is the only thing that
changes the TableImport. The registry keeps every import in the order they
were started, and :meth:`ImportRegistry.get` looks one up by database and
table: the import that says what state that table is in.

The command line starts one import per input. An upload endpoint does the
same with the file it saved, in a directory of its own::

    source = Source(name, "file", path, path=path, temporary=True, table=table)
    job = import_registry(datasette).start(
        db,
        source,
        title=name,
        on_error="drop",
        remove_after=(directory,),
        listeners=[on_event],
    )

:meth:`ImportRegistry.start` takes an :class:`ImportOptions`, or any of its
fields by keyword as here, and checks it at once, so a mistake fails there
rather than in the task. The options say how the rows get in (an
:class:`~datasette.imports.subprocess_import.Engine`, by default a worker
subprocess, see that module), what happens to them if the import fails
(``on_error``), where a URL or standard input is copied to, which
directories the import owns, and which :class:`ImportHooks` to call at
three points of it. ``listeners`` hear that import's events alone;
:meth:`ImportRegistry.add_listener` hears every import's.

Only background imports are in the registry. ``--get``, which loads its
inputs before answering, uses :func:`~datasette.imports.scratch.load_sources`
directly, so ``/-/imports.json`` is empty there: there is nothing to show
progress to.

The life of an import
---------------------

::

    start() -> queued -> [downloading] -> queued -> importing -> done
               waiting_for                waiting_for
               ="start"                   ="worker"

Any of the four active states can end as ``failed`` or ``cancelled``
instead, and an import only ever ends in :meth:`ImportRun.finish`. The
event is what listeners hear on entering the state.

=====================  ===========  ========================================
state (waiting_for)    event        entered in, and what is true then
=====================  ===========  ========================================
queued ("start")       queued       :meth:`ImportRegistry.start`. The table
                                    name is claimed and nothing is fetched.
                                    The task may not have run yet:
                                    ``waiting_for`` is None instead if
                                    background tasks were running already,
                                    as they are for an upload
downloading            downloading  ``ImportRun._steps()``, for a source
                                    with no ``path`` yet: a URL, standard
                                    input or a pipe is copied into
                                    ``ImportOptions.fetch_dir`` or a private
                                    directory, with byte progress
queued ("worker")      waiting      ``ImportRun._steps()``: queued for one
                                    of the :func:`worker_slots`, which go
                                    in the order asked. ``waited`` counts
                                    from here
importing              importing    ``ImportRun._steps()``, once the check
                                    for a table of that name found none:
                                    ``reached_table`` is True, and from now
                                    on ``import_status`` alone says whether
                                    the table is this import's. The engine
                                    inserts the rows a chunk per
                                    ``execute_write_fn()`` call, so they can
                                    be queried as they arrive, with a
                                    ``progress`` event after each
done/failed/cancelled  the state's  :meth:`ImportRun.finish` only. The name
                       name         is free, the files the import owned are
                                    gone, and :meth:`TableImport.wait`
                                    returns
=====================  ===========  ========================================

While importing, ``fraction`` is how far the worker has read into the file.
Once every row is in, ``finishing`` is True: if a column changed to text
late in the file, the numbers already stored in it are still being turned
into text, a slice per write, and ``backlog`` says how many rows are left
("finishing events: 1,200,000 rows to convert").

Who writes what, once :meth:`ImportRegistry.start` has returned:

============================  =============================================
writer                        fields of the TableImport
============================  =============================================
``ImportRun._set_state()``    ``state``, ``waiting_for``
``ImportRun.run()``           ``started_at``
``ImportRun._steps()``        ``loader``, ``waited``, ``reached_table``,
                              and ``existing_table`` if the check found a
                              table
``ImportRun._on_download()``  ``downloaded``, ``download_size``
``ImportRun._on_progress()``  ``rows``, ``bytes``, ``size``, ``pid``,
                              ``finishing``, ``backlog``, ``notes``,
                              ``loader``, while it runs
``ImportRun.finish()``        ``state``, ``waiting_for``, ``error``,
                              ``complete``, ``finished_at``,
                              ``cancel_reason``, ``waited``, and the final
                              ``rows``, ``bytes``, ``size``, ``pid``,
                              ``finishing``, ``notes``, ``widenings`` and
                              ``existing_table`` (from the probe for a
                              table in the way, before the check)
``import_rows()``             ``import_status``, which ``table_exists`` and
                              ``created_table`` read: they are properties.
                              The one other write to it is
                              ``ImportRun._settle_table()`` setting
                              ``dropped`` after a drop of its own
``TableImport.cancel()``      ``cancelled_by``, and the flag that makes a
                              second request change nothing
============================  =============================================

There is one write after :meth:`ImportRun.finish`. After a cancellation
(or a failed write) the import looks at its table: if it never got as far
as checking for one, it looks for a table of that name in the way;
otherwise it waits for the write in flight to settle, then counts or drops
its own table, if there is one. An import cancelled again during that look
finishes at once with what it knows, and when the look ends,
``ImportRun._settled_late()`` records the ``rows`` and ``existing_table``
it found. A second cancellation is enough for the finished record to
change then. Cancelled while queued for a worker, and again while it looks
for a table in the way, an import first says that no rows were imported,
then that the table that was there already was left alone; with
``on_error="drop"``, cancelled again while it drops its table, its
``table_exists`` stays True until the drop is over. After a third
cancellation - the second having cut import_rows()'s clean up short, so
that the write in flight had not settled - what that write committed, and
whether the table is there, only show once it has. Listeners are not told
again.

Failures, cancellation and the table
------------------------------------

When an import fails or is cancelled, ``on_error`` decides what happens to
the rows imported so far, as it does for
:func:`~datasette.imports.subprocess_import.import_file`: ``"keep"`` (the
default, and what the command line uses for ``scratch``) keeps them - the
table holds exactly the first ``rows_kept`` rows of the input - and the
import stays in the registry as failed or cancelled, which the pages show as
an "incomplete table" notice. ``"drop"`` drops the table: the command line
uses it for a ``--save`` file, which has nowhere to record that a table is
incomplete, and uploads use it too. Either way only a table this import
created is touched: a table that was there before the import checked for
it is never dropped, counted or labelled incomplete, even if the import is
cancelled while it waits for a worker. :func:`ending_for` maps the
exception an import ended with to its state, and to whether to look at the
table first.

An import into a table that exists already fails (``existing_table`` is
then True) and leaves that table alone; it does not replace the import that
created the table as the one :meth:`~ImportRegistry.get` returns, so a
second upload of the same file cannot make a complete table look
incomplete.

The names of the tables being imported are claimed in this process with a
:class:`~datasette.imports.scratch.TableClaim`, from
:meth:`ImportRegistry.start` until the import ends - the same claim that
:func:`~datasette.imports.scratch.load_source` takes - so two imports can
never both create a table. An import is active exactly while it holds its
claim, with one exception: one cancelled while it looks at its table keeps
the claim until that look is over, so that nothing else can create the
table before it has been dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import itertools
import logging
import os
import tempfile
import time
import weakref
from collections.abc import Callable

from datasette.imports import ACTIVE_STATES, FINISHED_STATES
from datasette.imports import loaders as _loaders
from datasette.imports.inserter import ON_ERROR, SQLITE_ERRORS, ImportStatus
from datasette.imports.scratch import (
    DEFAULT_BATCH_SIZE,
    ImportFailed,
    TableClaim,
    TableExists,
    drop_table,
    import_writes,
    sniff,
    table_taken,
)
from datasette.imports.sources import (
    Source,
    SourceError,
    display_spec,
    fetch,
    human_size,
    remove_temporary,
    remove_tree,
)
from datasette.imports.subprocess_import import (
    Engine,
    WorkerImportError,
    default_engine,
)

# TerminalReporter is re-exported: it was defined here
from datasette.imports.terminal import TerminalReporter, plural
from datasette.utils import escape_sqlite

logger = logging.getLogger(__name__)

# Finished imports kept in the registry, beyond which the oldest are
# forgotten (an upload endpoint could otherwise grow it without bound)
MAX_FINISHED = 200
# Seconds a cancelled import waits for the clean up of its rows to say what
# it left, if it was cancelled again during that clean up: well within the
# grace period Datasette gives background tasks at shutdown
SETTLE_SECONDS = 3.0
# Default number of workers that may run at once for one Datasette instance.
# Measured on 4 CPUs: 32 files of 1,000 rows
# took 8.1-8.6s with 1, 4.1-4.2s with 2, 2.4-2.5s with 4; 8 files of 100,000
# rows went 81,000 -> 84,000-89,000 -> 91,000 rows/s (the parent's loop is
# the limit) while loop lag p99 went 4.5-4.9 -> 5.6-5.7 -> 8.7-9.2ms
DEFAULT_MAX_WORKERS = 2

_WORKER_SLOTS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def worker_slots(ds, max_workers=None) -> asyncio.Semaphore:
    """
    The :class:`asyncio.Semaphore` that limits how many import workers run
    at once for the Datasette instance ``ds`` (``DEFAULT_MAX_WORKERS``
    unless ``max_workers`` is given the first time this is called for it):
    :attr:`ImportRegistry.slots`. A background import waits for a slot
    before it sniffs its file and starts its worker, and holds it until
    its rows are in and its table is finished. Waiters get a slot in the
    order they asked.
    """
    slots = _WORKER_SLOTS.get(ds)
    if slots is None:
        slots = asyncio.Semaphore(max_workers or DEFAULT_MAX_WORKERS)
        _WORKER_SLOTS[ds] = slots
    return slots


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class ImportHooks:
    """
    What the code that starts an import needs done at three points of it,
    as :attr:`ImportOptions.hooks`: subclass it and override any of them.
    The command line keeps its ``--reload`` session up to date with them.
    """

    async def before(self, job: TableImport) -> None:
        "Awaited as the import's task starts, before anything else."

    def fetched(self, job: TableImport) -> None:
        """
        Called once the import has fetched a copy of its input (a URL
        downloaded, standard input or a pipe copied). Setting
        ``job.source.temporary = False`` keeps the copy when the import ends.
        """

    def finished(self, job: TableImport) -> None:
        """
        Called when the import has finished, however it finished: by then
        its table's name is free and the files it owned are gone, as they
        are when listeners hear the ``"done"``, ``"failed"`` or
        ``"cancelled"`` event that follows.
        """


@dataclasses.dataclass(frozen=True)
class ImportOptions:
    """
    How an import runs, decided by whoever starts it and checked by
    :meth:`ImportRegistry.start`, so a mistake fails there rather than
    later in the import's task.
    """

    #: How to refer to the input. By default the argument as it was typed,
    #: with any credentials in a URL redacted
    title: str | None = None
    #: ``"keep"`` or ``"drop"`` the rows imported before a failure or
    #: cancellation: see the module documentation
    on_error: str = "keep"
    sample_size: int = 1000
    batch_size: int = DEFAULT_BATCH_SIZE
    #: How the rows get into the table: a
    #: :class:`~datasette.imports.subprocess_import.WorkerEngine` (a worker
    #: subprocess, started as its ``WorkerOptions`` say) or an
    #: :class:`~datasette.imports.subprocess_import.InProcessEngine` (a
    #: thread of this process). ``None``:
    #: :func:`~datasette.imports.subprocess_import.default_engine`, the
    #: worker where subprocesses can be started
    engine: Engine | None = None
    #: Where a source with no ``path`` yet is fetched to (a URL downloaded,
    #: standard input or a pipe copied). ``None``: a private temporary
    #: directory, removed when the import ends
    fetch_dir: str | None = None
    #: Directories this import owns, removed when it ends however it ends:
    #: the one an upload saved its file in
    remove_after: tuple[str, ...] = ()
    hooks: ImportHooks = dataclasses.field(default_factory=ImportHooks)

    def __post_init__(self):
        if self.on_error not in ON_ERROR:
            raise ValueError(f"on_error must be one of {ON_ERROR}")
        if isinstance(self.remove_after, (str, bytes, os.PathLike)):
            # A path on its own would be removed a character at a time
            raise TypeError("remove_after must be a tuple of directories")
        object.__setattr__(self, "remove_after", tuple(self.remove_after))
        if not isinstance(self.hooks, ImportHooks):
            raise TypeError("hooks must be an ImportHooks")


@dataclasses.dataclass(frozen=True)
class Ending:
    "How an import ends: see :func:`ending_for`."

    #: "done", "failed" or "cancelled"
    state: str
    #: The error message, for "failed"
    error: str | None
    #: Look at what it left first: a table that was in the way, or its own
    #: table to count the rows of (or drop, with on_error="drop")
    check_table: bool
    #: Raise the exception again once it has ended: cancellations, so its
    #: task reads "cancelled", and bugs, so the background task supervisor
    #: logs their traceback
    reraise: bool


def ending_for(error: BaseException | None) -> Ending:
    "How an import that raised ``error`` ends (``None``: it succeeded)."
    if error is None:
        return Ending("done", None, False, False)
    if isinstance(error, asyncio.CancelledError):
        return Ending("cancelled", None, True, True)
    if isinstance(error, GeneratorExit):
        # The task's coroutine is being closed: the task was garbage
        # collected unfinished, after its loop had gone. Nothing can be
        # awaited now, so it ends at once, with what is known
        return Ending("cancelled", None, False, True)
    if isinstance(error, WorkerImportError):
        # The worker or its loader failed: the import kept or dropped the
        # table, and the result attached to the error says what is there
        return Ending("failed", str(error), False, False)
    if isinstance(error, (SourceError, _loaders.LoaderError)):
        # Before any rows were written: a download, sniffing, or a table
        # that was there already (TableExists)
        return Ending("failed", str(error), False, False)
    if isinstance(error, (*SQLITE_ERRORS, OSError)):
        # Inserting failed: a full disk, a write_wrapper plugin refusing. The
        # rows committed before it stay, as for a worker failure
        return Ending("failed", str(error), True, False)
    if isinstance(error, Exception):
        # A bug
        return Ending("failed", f"{type(error).__name__}: {error}", True, True)
    # KeyboardInterrupt, SystemExit
    return Ending("cancelled", None, True, True)


@dataclasses.dataclass(frozen=True)
class Found:
    "What :meth:`ImportRun._settle_table` found, for the import's outcome."

    #: A table of that name was there before the import checked for one
    existing_table: bool = False
    #: The rows counted in the import's own table, if it was kept
    rows: int | None = None


class TableImport:
    """
    One import into one table: its progress and outcome, and a handle to
    cancel it. Created by :meth:`ImportRegistry.start`; read the attributes,
    :attr:`fraction`, :meth:`describe` or :meth:`to_dict`. Once it has
    started, only its :class:`ImportRun` changes it.
    """

    def __init__(self, registry, id: str, db, source: Source, *, title, on_error):
        self.registry = registry
        self.id = id
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
        #: Rows committed to the table so far. Once it has finished, what
        #: :attr:`import_status` says were committed - after a cancellation,
        #: with the chunk that was being written then, which import_rows()
        #: lets commit before it stops - or, where a cancellation or a
        #: failed write left its table, a count of the rows in it. With
        #: ``on_error="drop"`` that is how many rows went with the table
        self.rows = 0
        #: True once every row is in the table and the import is finishing:
        #: converting the :attr:`backlog`, if any, then completing the table
        self.finishing = False
        #: Rows (by rowid range) whose stored numbers are still to be turned
        #: into text after a column changed to text
        self.backlog = 0
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
        self._finished = None
        #: Seconds spent queued for a worker slot (not counted in seconds)
        self.waited = 0.0
        self._waiting_since = None
        #: The error message, for a failed import
        self.error = None
        #: What this import's rows have done to its table, kept up to date by
        #: :func:`~datasette.imports.inserter.import_rows`: whether it created
        #: the table (and dropped it again), and the rows committed. Only
        #: what it says is this import's is ever dropped, counted or
        #: labelled incomplete
        self.import_status = ImportStatus()
        #: Whether a table of that name was there before this import
        #: checked: the import then failed (or was cancelled) and left that
        #: table alone, so it says nothing about the table's state
        self.existing_table = False
        #: Whether the import got as far as the table: it checked that no
        #: table of that name was there. From then on :attr:`import_status`
        #: says whether a table of that name is this import's
        self.reached_table = False
        #: True once every row of the input is in the table
        self.complete = False
        self.widenings = []
        #: Things the person importing the file should know about how it
        #: was read, from the loader (``Note`` messages): "Importing the 2
        #: objects in 'results', ignoring 'total'". Shown with the import
        self.notes = []
        #: The worker's process ID while it runs
        self.pid = None
        #: The :class:`~datasette.background_tasks.BackgroundTask`
        self.task = None
        self._cancel_requested = False
        #: The actor who cancelled the import with :meth:`cancel`, if one did
        #: (not when Datasette shut down, or the caller passed none)
        self.cancelled_by = None
        #: Why a cancelled import was cancelled: "shutdown" (Datasette was
        #: shutting down), "request" (:meth:`cancel` was called) or "task"
        #: (something else cancelled its task)
        self.cancel_reason = None
        self._done = asyncio.Event()

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
    def table_exists(self) -> bool:
        "Whether the table exists: created by this import and not dropped."
        return self.import_status.table_exists

    @property
    def created_table(self) -> bool:
        """
        Whether this import created the table (it may have dropped it again:
        on_error="drop").
        """
        return self.import_status.created

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
        """
        Seconds the import has been working, or took: downloading and
        importing, not counting time queued for a worker slot
        (:attr:`waited_seconds`).
        """
        if self._started is None:
            return None
        end = self._finished if self._finished is not None else time.monotonic()
        return max(end - self._started - self.waited_seconds, 0.0)

    @property
    def waited_seconds(self) -> float:
        "Seconds spent queued for a worker slot so far."
        waited = self.waited
        if self._waiting_since is not None:
            end = self._finished if self._finished is not None else time.monotonic()
            waited += end - self._waiting_since
        return waited

    def _outcome(self) -> str:
        kept = self.rows_kept or 0
        if self.existing_table:
            return "the table that was there already was left alone"
        if self.on_error == "drop":
            return "the table was removed" if self.rows else "no table was created"
        if kept:
            return f"the table has the first {plural(kept, 'row')}"
        return "no rows were imported"

    @property
    def converting(self) -> bool:
        "Whether every row is in and stored numbers are being turned into text."
        return self.state == "importing" and self.finishing and self.backlog > 0

    def status(self, table: str | None = None) -> str:
        """
        One line of text for people saying what is happening. With the
        ``table`` name (what :meth:`describe` gives): ``importing events:
        40% (2,560,000 rows)``, ``finishing events: 1,200,000 rows to
        convert``, ``import of events failed: ...``. Without it:
        ``importing 40% (2,560,000 rows)``, ``queued, waiting for an import
        worker``, ``import failed: ... (the table has the first 3,000
        rows)``.
        """

        def line(verb, details, joiner=": "):
            # "importing events: 40% (4 rows)" with the table name. Without
            # it, joiner says how the details follow on from the verb:
            # "importing 40% (4 rows)", "importing: 4 rows so far"
            if table:
                return f"{verb} {table}: {details}"
            return f"{verb}{joiner}{details}"

        if self.state == "queued":
            if self.waiting_for == "worker":
                return line("queued", "waiting for an import worker", ", ")
            return f"queued {table}" if table else "queued"
        if self.state == "downloading":
            verb = "downloading"
            if self.kind == "stdin":
                verb = (
                    "reading standard input for" if table else "reading standard input"
                )
            done = human_size(self.downloaded or 0)
            if self.download_size:
                total = human_size(self.download_size)
                return line(verb, f"{self.percent}% ({done} of {total})", " ")
            return line(verb, done)
        if self.converting:
            return line("finishing", f"{plural(self.backlog, 'row')} to convert")
        if self.state == "importing":
            rows = plural(self.rows, "row")
            if self.percent is None:
                return line("importing", f"{rows} so far")
            return line("importing", f"{self.percent}% ({rows})", " ")
        if self.state == "done":
            if not self.table_exists:
                return line("imported", "no rows, so no table was created")
            return line("imported", plural(self.rows, "row"), " ")
        if self.state == "failed":
            name = f" of {table}" if table else ""
            return f"import{name} failed: {self.error} ({self._outcome()})"
        if table:
            return f"import of {table} was cancelled ({self._outcome()})"
        return f"import cancelled ({self._outcome()})"

    def describe(self) -> str:
        "What is happening, with the table name: :meth:`status` for :attr:`table`."
        return self.status(self.table)

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
            "finishing": self.finishing,
            "backlog": self.backlog,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "seconds": (round(self.seconds, 3) if self.seconds is not None else None),
            "waited_seconds": round(self.waited_seconds, 3),
            "error": self.error,
            "on_error": self.on_error,
            "complete": self.complete,
            "table_exists": self.table_exists,
            "created_table": self.created_table,
            "existing_table": self.existing_table,
            "rows_kept": self.rows_kept,
            "notes": list(self.notes),
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

    def cancel(self, actor=None) -> bool:
        """
        Cancel the import if it is still running: its worker is killed and,
        with ``on_error="keep"``, the rows already imported stay. Returns
        False if it had already finished. Use :meth:`wait` to wait for the
        cancellation to finish. ``actor`` is who asked, recorded as
        :attr:`cancelled_by`. Asking again while it is being cancelled
        returns True and changes nothing - it must not interrupt the clean
        up - so :attr:`cancelled_by` is whoever asked first.
        """
        if not self.active:
            return False
        if not self._cancel_requested:
            self._cancel_requested = True
            self.cancelled_by = actor
            # Launched: the task unwinds through ImportRun.run(), or -
            # cancelled before its first step - ends without running it, and
            # its done callback finishes the import. Not launched: it is
            # taken off the list of tasks to start, and its done callback
            # finishes the import now
            self.task.cancel()
        return True

    async def wait(self, timeout: float | None = None) -> bool:
        "Wait until the import has finished. False if ``timeout`` ran out."
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._done.wait(), timeout)
        return self._done.is_set()


class ImportRun:
    """
    The background task of one :class:`TableImport`, and once it has
    started the only thing that changes it: :meth:`run` is the task, and
    :meth:`finish` the one way the import ends.
    """

    def __init__(
        self,
        job: TableImport,
        claim: TableClaim,
        options: ImportOptions,
        notify,
    ):
        self.job = job
        #: The table's name, held until the import ends
        self.claim = claim
        #: How it runs; ``options.engine`` is set
        self.options = options
        # ImportRegistry._notify(job, event)
        self._notify = notify
        self._private_dir = None
        self._catalog_task = None
        # The _settle_table() task, once the import is looking at its table
        # (not ImportStatus._settling: the future that task may wait for)
        self._settle_task = None

    def task_ended(self, handle):
        """
        The done callback of every import's task. run() finishes the import
        however it ends, so this finishes only one whose task ended without
        running run(): cancelled before its first step (by Datasette
        shutting down, say) or before background tasks started.
        """
        self.finish(Ending("cancelled", None, False, False))

    def _set_state(self, state, waiting_for=None):
        job = self.job
        job.state = state
        job.waiting_for = waiting_for
        event = "waiting" if state == "queued" else state
        self._notify(job, event)

    def _on_download(self, done, total):
        job = self.job
        job.downloaded = done
        job.download_size = total
        self._notify(job, "progress")

    def _on_progress(self, progress):
        job = self.job
        job.rows = progress.rows_written
        job.bytes = progress.bytes
        job.size = progress.size
        job.pid = progress.pid
        job.finishing = progress.finishing
        job.backlog = progress.backlog
        if progress.notes:
            job.notes = list(progress.notes)
        if progress.loader:
            job.loader = progress.loader
        self._notify(job, "progress")
        if job.table_exists and self._catalog_task is None:
            # The first chunk has created the table. Table pages find it at
            # once (resolve_table() looks in sqlite_master), but the lists
            # of tables on the homepage and the database page come from
            # Datasette's catalog, refreshed at most once a second as
            # requests arrive. Refresh it now, without holding up the rows
            self._catalog_task = asyncio.ensure_future(self._refresh_catalog())

    async def _refresh_catalog(self, attempts=20, delay=0.05):
        """
        Refresh Datasette's catalog until it lists this table, trying at
        most ``attempts`` times: ``refresh_schemas(force=True)`` returns at
        once, without refreshing, while another refresh runs - and that one
        may have read the schema before the table was created.
        """
        job = self.job
        datasette = job.registry.datasette
        if datasette is None:
            return
        try:
            internal = datasette.get_internal_database()
            for _ in range(attempts):
                await datasette.refresh_schemas(force=True)
                listed = (
                    await internal.execute(
                        "select 1 from catalog_tables where database_name = ? "
                        "and table_name = ?",
                        [job.database, job.table],
                    )
                ).first()
                if listed is not None:
                    return
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not refresh the schema for %r", job)

    def finish(self, ending, result=None, found=None):
        """
        The one way an import ends, however it ends: record the outcome,
        let go of what the import held, then tell everyone - so whoever
        hears that it has finished finds its table's name free and its
        temporary files gone. Does nothing if it has finished already.
        ``result`` is the worker's, if there is one: a worker that failed
        attaches it to its error. ``found`` is what :meth:`_settle_table`
        found, if it looked.
        """
        job = self.job
        if not job.active:
            return
        if found is None:
            found = Found()
        now = time.monotonic()
        if job._waiting_since is not None:
            # Ended while queued for a worker slot
            job.waited += now - job._waiting_since
            job._waiting_since = None
        self._take_result(result)
        self._take_found(found)
        job.state = ending.state
        job.waiting_for = None
        job.error = ending.error
        job.finished_at = _now_iso()
        job._finished = now
        job.pid = None
        job.finishing = False
        if ending.state == "done":
            job.complete = True
            job.bytes = job.size
        elif ending.state == "cancelled":
            datasette = job.registry.datasette
            if datasette is not None and getattr(datasette, "_shutdown_invoked", False):
                job.cancel_reason = "shutdown"
            elif job._cancel_requested:
                job.cancel_reason = "request"
            else:
                job.cancel_reason = "task"
        self._release()
        job._done.set()
        try:
            self.options.hooks.finished(job)
        except Exception:
            logger.exception("The finished() hook of %r failed", job)
        self._notify(job, ending.state)

    def _release(self):
        """
        Let go of the table's name, the catalog refresh and the files and
        directories it owns.
        """
        if self._settle_task is not None and not self._settle_task.done():
            # Cancelled again while it looked at its table: keep the name
            # until it has finished with it - dropping it, say - so that no
            # other import can create a table of that name meanwhile
            self._settle_task.add_done_callback(self._settled_late)
        else:
            self.claim.release()
        if self._catalog_task is not None and not self._catalog_task.done():
            self._catalog_task.cancel()
        source = self.job.source
        if source.temporary:
            # An uploaded file, or a copy this import fetched that nobody
            # kept (the fetched() hook can set source.temporary = False)
            remove_temporary([source])
        if self._private_dir is not None:
            remove_tree(self._private_dir)
            self._private_dir = None
        for directory in self.options.remove_after:
            remove_tree(directory)

    def _fetch_dir(self):
        if self.options.fetch_dir is not None:
            return self.options.fetch_dir
        self._private_dir = tempfile.mkdtemp(prefix="datasette-import-")
        return self._private_dir

    async def run(self, datasette):
        "The import's background task: its steps, then however it ended."
        # datasette.add_background_task() runs every task as func(datasette)
        # (BackgroundTaskSupervisor._launch_one()); the import reaches the
        # Datasette instance through its registry instead
        job = self.job
        job.started_at = _now_iso()
        job._started = time.monotonic()
        result = error = None
        try:
            result = await self._steps()
        except BaseException as ex:  # noqa: BLE001 - ending_for() says if it re-raises
            error = ex
            if isinstance(ex, WorkerImportError):
                result = ex.result
        ending = ending_for(error)
        raise_after = error if ending.reraise else None
        found = Found()
        try:
            if ending.check_table:
                self._settle_task = asyncio.ensure_future(self._settle_table())
                found = await asyncio.shield(self._settle_task)
        except asyncio.CancelledError as again:
            # Cancelled again while it looked: finish with what is known
            raise_after = again
        finally:
            self.finish(ending, result, found)
        if raise_after is not None:
            raise raise_after

    async def _steps(self):
        "What the import does, in order. Returns the worker's result."
        job, options = self.job, self.options
        source = job.source
        await options.hooks.before(job)
        if source.path is None:
            copy = source.needs_copy
            if copy:
                self._set_state("downloading")
            await fetch(
                source,
                self._fetch_dir() if copy else None,
                progress=self._on_download,
            )
            if copy:
                options.hooks.fetched(job)
        job._waiting_since = time.monotonic()
        self._set_state("queued", "worker")
        # The one place a worker slot is taken. Waiters get one in the order
        # they asked, so local files load in the order they were given:
        # nothing above waits for one. Sniffing and the check for an
        # existing table wait for a thread, so they come after
        async with job.registry.slots:
            job.waited += time.monotonic() - job._waiting_since
            job._waiting_since = None
            if source.loader is None:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, sniff, source)
            job.loader = source.loader.name
            try:
                await self.claim.check_free()
            except TableExists:
                job.existing_table = True
                raise
            job.reached_table = True
            self._set_state("importing")
            with import_writes():
                return await options.engine.load(
                    job.db,
                    job.table,
                    source.path,
                    loader=job.loader,
                    status=job.import_status,
                    progress=self._on_progress,
                    on_error=job.on_error,
                    sample_size=options.sample_size,
                    batch_size=options.batch_size,
                )

    def _take_result(self, result):
        "What the worker's result says that import_status does not."
        if result is None:
            return
        job = self.job
        if result.notes:
            job.notes = list(result.notes)
        if result.import_result is not None:
            job.widenings = list(result.import_result.widenings)
        if result.size is not None:
            job.size = result.size

    def _take_found(self, found):
        "What :meth:`_settle_table` found, and the rows the import left."
        job = self.job
        job.existing_table = job.existing_table or found.existing_table
        # A count of the rows kept, or what import_rows() committed
        job.rows = found.rows if found.rows is not None else job.import_status.rows

    def _settled_late(self, task):
        """
        The done callback of a :meth:`_settle_table` task that another
        cancellation cut short: a second one, while it looked for a table
        in the way or counted or dropped its own, or a third, while it
        waited for the write in flight to settle. :meth:`finish` recorded
        what was known then, so record what the task found - a table that
        was in the way, the rows in its own - so that the finished import
        says what it left, as its ``table_exists`` already does.
        Listeners have heard that it was cancelled; pages,
        ``/-/imports.json`` and ``/-/tasks`` read the record. Then let go
        of the table's name.
        """
        try:
            if not task.cancelled() and task.exception() is None:
                self._take_found(task.result())
                job = self.job
                if job.task is not None:
                    job.task.status = job.describe()
        finally:
            self.claim.release()

    async def _settle_table(self) -> Found:
        """
        After a cancellation or an insert that failed: is a table of this
        name in the way, or is this import's own table still there, to drop
        or count?

        A table that is there although this import never checked for one
        (it was cancelled while queued for a worker, say) is somebody
        else's: it is left alone, and ``existing_table`` says so. After the
        check, :attr:`TableImport.import_status` alone says whether a table
        of that name is this import's. It is final once import_rows() has
        raised - its clean up settles the write it had running first -
        unless the import was cancelled again during that clean up (a
        cancel request, then Datasette shutting down), which then carries
        on in the background: wait for it to settle
        (:meth:`~datasette.imports.inserter.ImportStatus.wait_settled`).
        """
        job = self.job
        db, status = job.db, job.import_status
        if not job.reached_table:
            try:
                return Found(existing_table=await table_taken(db, job.table))
            except Exception:
                logger.warning(
                    "Could not check for the table of %r", job, exc_info=True
                )
                return Found()
        if not await status.wait_settled(timeout=SETTLE_SECONDS):
            logger.warning(
                "The clean up of %r had not settled after %ss", job, SETTLE_SECONDS
            )
        if not status.table_exists:
            # Never created, or import_rows() dropped it
            return Found()
        if job.on_error == "drop":
            # It failed once import_rows() had finished the table: while
            # the worker was being stopped, say
            try:
                await drop_table(db, job.table)
            except Exception:
                logger.exception("Could not drop the partial table of %r", job)
            else:
                status.dropped = True
            return Found()
        try:
            count = (
                await db.execute(f"select count(*) from {escape_sqlite(job.table)}")
            ).first()[0]
        except Exception:
            logger.warning("Could not count the rows of %r", job, exc_info=True)
            return Found()
        return Found(rows=count)


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
    Every background import for one Datasette instance: get it with
    :func:`import_registry`. Keeps the imports in the order they were
    started - every running one, and up to ``MAX_FINISHED`` finished ones -
    and finds the one that says what state a table is in with :meth:`get`.
    """

    def __init__(self, datasette):
        self._datasette = weakref.ref(datasette)
        # By id, in the order they were started
        self._jobs: dict[str, TableImport] = {}
        self._listeners: list[Callable] = []
        # By job id: the listeners given to start() for that import alone,
        # until it has finished
        self._job_listeners: dict[str, list[Callable]] = {}
        self._ids = itertools.count(1)

    @property
    def datasette(self):
        return self._datasette()

    @property
    def slots(self) -> asyncio.Semaphore:
        "The import worker slots: :func:`worker_slots` of the Datasette instance."
        return worker_slots(self.datasette)

    @staticmethod
    def _key(database, table):
        # SQLite table names are case-insensitive
        return (database, table.lower())

    @staticmethod
    def _current(jobs: list[TableImport]) -> TableImport | None:
        """
        Of the imports into one table (oldest first), the one that says
        what state it is in:

        1. the one running;
        2. else the latest that got as far as the table - past the check
           that it did not exist - if its rows are still there;
        3. else the latest that did not find the table there already. An
           import that failed to download, or was cancelled while queued,
           never touched the table, so it only says something about the
           table if no earlier import's rows are in it;
        4. else the latest.
        """
        if not jobs:
            return None
        for job in reversed(jobs):
            if job.active:
                return job
        reached = next(
            (job for job in reversed(jobs) if job.reached_table or job.created_table),
            None,
        )
        if reached is not None and reached.table_exists:
            return reached
        for job in reversed(jobs):
            if not job.existing_table:
                return job
        return jobs[-1]

    def get(self, database: str, table: str) -> TableImport | None:
        """
        The import that says what state ``table`` (case-insensitive) is in:
        the import running into it, if there is one; otherwise the latest
        to finish - except that an import that never touched the table (it
        failed because the table was there already, failed to download, or
        was cancelled while queued) does not replace an earlier import
        whose rows are in the table. ``None`` if no import into that table
        is known.
        """
        return self._current(self.imports(database, table))

    def imports(
        self, database: str | None = None, table: str | None = None
    ) -> list[TableImport]:
        """
        Every import in the order they were started, optionally only those
        into one database or one table - including imports that failed
        because their table existed already.
        """
        return [
            job
            for job in self._jobs.values()
            if (database is None or job.database == database)
            and (table is None or job.table.lower() == table.lower())
        ]

    def tables(self, database: str | None = None) -> list[TableImport]:
        """
        One import per table, as :meth:`get` would return it, in the order
        those imports were started: what the progress notices show.
        """
        by_table: dict[tuple, list[TableImport]] = {}
        for job in self.imports(database):
            by_table.setdefault(self._key(job.database, job.table), []).append(job)
        current = [self._current(jobs) for jobs in by_table.values()]
        return sorted(current, key=lambda job: int(job.id))

    def active(
        self, database: str | None = None, table: str | None = None
    ) -> list[TableImport]:
        """
        The imports still running - queued, downloading or importing - of
        those :meth:`imports` returns. :meth:`start` refuses an import into
        a table that one of them is importing into: by the database's name,
        as :meth:`get` finds imports, since a database removed and added
        again under its name is a new Database object, whose names no
        import has claimed, but its tables are still being imported.
        """
        return [job for job in self.imports(database, table) if job.active]

    def add_listener(self, listener: Callable) -> None:
        """
        Call ``listener(job, event)`` on every change to every import:
        ``event`` is ``"queued"`` (started), ``"waiting"`` (queued for a
        worker), ``"downloading"``, ``"importing"``, ``"progress"`` (often -
        after each chunk of rows and each progress report from the worker or
        the download), ``"done"``, ``"failed"`` or ``"cancelled"``. For the
        changes to one import only, pass ``listeners=`` to :meth:`start`.
        """
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def _notify(self, job, event):
        if job.task is not None:
            job.task.status = job.describe()
        for listener in [*self._listeners, *self._job_listeners.get(job.id, ())]:
            try:
                listener(job, event)
            except Exception:
                logger.exception("Import listener %r failed", listener)
        if event in FINISHED_STATES:
            # Nothing happens to an import after it has finished
            self._job_listeners.pop(job.id, None)

    def start(
        self,
        db,
        source: Source,
        options: ImportOptions | None = None,
        *,
        listeners=(),
        **changes,
    ) -> TableImport:
        """
        Import ``source`` into a new table ``source.table`` in ``db``, as a
        background task, as ``options`` say - with any of its fields
        changed by keyword, ``start(db, source, title=name)``. Returns the
        :class:`TableImport` at once.

        Raises :class:`TypeError` or :class:`ValueError` for an option that
        does not exist or a value it cannot have, and
        :class:`~datasette.imports.scratch.TableExists` if that table is
        being imported already. A table that exists already is found when
        the task runs: the import then fails with ``existing_table`` set,
        and leaves the table - and what :meth:`get` says about it - alone.

        ``source.path`` is the file to load; if it is ``None`` the source is
        fetched first (a URL downloaded, standard input or a pipe copied)
        into :attr:`ImportOptions.fetch_dir`. The file is deleted when the
        import ends if ``source.temporary`` is set, as it is for an uploaded
        file and for a copy this import fetches itself (unless the
        ``fetched()`` hook sets ``source.temporary = False``).

        ``listeners`` are called as ``listener(job, event)`` for this
        import's changes alone, as :meth:`add_listener`'s are for every
        import's, starting with the ``"queued"`` event before ``start()``
        returns, and ending with the ``"done"``, ``"failed"`` or
        ``"cancelled"`` event.
        """
        options = dataclasses.replace(
            options if options is not None else ImportOptions(), **changes
        )
        if options.engine is None:
            options = dataclasses.replace(options, engine=default_engine())
        if not source.table:
            raise ValueError("source.table is not set: see assign_table_names()")
        datasette = self.datasette
        # An import that is still running holds its table's name until it
        # ends, so a second one is refused - by the name of its database
        # too, see active()
        if self.active(db.name, source.table):
            raise TableExists(
                f"Table {source.table!r} is already being imported into {db.name}"
            )
        claim = TableClaim(db, source.table)
        job = TableImport(
            self,
            str(next(self._ids)),
            db,
            source,
            title=options.title or _title(source),
            on_error=options.on_error,
        )
        run = ImportRun(job, claim, options, self._notify)
        self._jobs[job.id] = job
        self._job_listeners[job.id] = list(listeners)
        self._prune()
        try:
            job.task = datasette.add_background_task(
                run.run, name=f"import {db.name}.{source.table}"
            )
        except BaseException:
            self._jobs.pop(job.id, None)
            self._job_listeners.pop(job.id, None)
            run._release()
            raise
        # However its task ends, even before its first step
        job.task.add_done_callback(run.task_ended)
        if job.task.task is not None:
            # Background tasks are running already, so this one started
            job.waiting_for = None
        self._notify(job, "queued")
        return job

    def _prune(self):
        """
        Forget the oldest finished imports beyond ``MAX_FINISHED``: first
        those that no longer say what state their table is in (a later
        import into it did), then the others.
        """
        finished = [job for job in self._jobs.values() if not job.active]
        excess = len(finished) - MAX_FINISHED
        if excess <= 0:
            return
        current = {job.id for job in self.tables()}
        order = [job for job in finished if job.id not in current] + [
            job for job in finished if job.id in current
        ]
        for job in order[:excess]:
            self._jobs.pop(job.id, None)

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


__all__ = [
    "ACTIVE_STATES",
    "DEFAULT_MAX_WORKERS",
    "FINISHED_STATES",
    "ImportFailed",
    "ImportHooks",
    "ImportOptions",
    "ImportRegistry",
    "TableImport",
    "TerminalReporter",
    "existing_registry",
    "import_registry",
    "worker_slots",
]
