"""
Loading command-line sources into the ``scratch`` database (or a ``--save``
file).

``scratch`` is a SQLite file in WAL mode in the temporary directory, deleted
when Datasette exits. WAL rather than a shared-cache in-memory database
because shared-cache memory fails with "database table is locked" as soon
as anything reads while an import writes. It is read-only for everyone
except the import itself: see ``datasette/imports/default_hooks.py``.

:func:`load_sources` and :func:`load_source` are the ``--get`` path, which
loads every input before answering: all or nothing, on the write thread
(``exclusive=True``, one ``db.execute_write_fn()`` call per input). While
Datasette serves, inputs and uploads load as background imports instead,
through :mod:`datasette.imports.background` and the worker subprocess -
not through these. (``exclusive=False``, a write per chunk while parsing
runs in an executor thread, is only used by tests.)
"""

import asyncio
import atexit
import contextlib
import contextvars
import dataclasses
import json
import logging
import os
import pathlib
import sqlite3
import tempfile
import threading
import time
import weakref

from datasette.database import Database
from datasette.imports import loaders as _loaders
from datasette.imports.inserter import (
    SQLITE_ERRORS,
    ImportStatus,
    import_rows,
    insert_rows,
)
from datasette.imports.sources import (
    SQLITE_HEADER,
    Source,
    SourceError,
    remove_tree,
)
from datasette.utils import escape_sqlite

logger = logging.getLogger(__name__)

SCRATCH_NAME = "scratch"
DEFAULT_BATCH_SIZE = 1000
# A --save file keeps SQLite's default journal and synchronous=FULL, so each
# commit waits for the disk: 6,400 commits cost ~18s of a 6.4M row load,
# 640 commits ~5s. Nothing reads while the command line loads, so commit
# less often there (it costs ~17MB more memory)
SAVE_BATCH_SIZE = 10000

# Set while the import process itself is writing - every other write to a
# scratch database is refused by the write_wrapper hook in default_hooks
_import_writes = contextvars.ContextVar("datasette_import_writes", default=False)


@contextlib.contextmanager
def import_writes():
    "Mark writes made inside this block (in this task) as the import's own."
    token = _import_writes.set(True)
    try:
        yield
    finally:
        _import_writes.reset(token)


def import_writes_allowed() -> bool:
    return _import_writes.get()


class ImportFailed(SourceError):
    """A source could not be loaded. The message names the source."""


class TableExists(SourceError):
    """A --save file already has a table with the name a source needs."""


# Databases --------------------------------------------------------------------


class ScratchDatabase(Database):
    """
    The read-only database that command-line sources load into: a SQLite
    file in WAL mode, created in the temporary directory unless ``path`` is
    given, and deleted when it is closed or the process exits unless
    ``keep=True``.
    """

    is_scratch = True

    def __init__(self, ds, path=None, *, keep=False):
        if path is None:
            fd, path = tempfile.mkstemp(prefix="datasette-scratch-", suffix=".db")
            os.close(fd)
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        finally:
            conn.close()
        super().__init__(ds, path=path, is_mutable=True)
        self.keep = keep
        #: LoadedTable records, shown on the database page
        self.loaded = []
        #: The ``datasette ... --save data.db`` command for these sources
        self.save_command = None
        #: The tables those sources load into: the command is only shown to
        #: actors who may view all of them
        self.save_tables = []
        self.from_stdin = False
        if not keep:
            atexit.register(self.delete_files)

    def suggest_name(self):
        return SCRATCH_NAME

    def connect(self, write=False):
        conn = super().connect(write=write)
        if write:
            # The data is thrown away at exit, so skip waiting for the disk
            conn.execute("PRAGMA synchronous=OFF")
        return conn

    def close(self):
        super().close()
        if not self.keep:
            self.delete_files()

    def delete_files(self):
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                os.unlink(self.path + suffix)


class CreatedSaveDatabase(Database):
    """
    A ``--save`` file that this run created, loaded in the background while
    Datasette serves. If nothing ended up in it - every input failed or was
    cancelled - it is deleted again when it is closed (when Datasette shuts
    down, after the imports have been cancelled), as it is when loading
    before serving fails, instead of leaving an empty database behind.
    ``on_removed(path)`` is called if it was.

    It is in WAL mode, so that pages can be read while rows are written.
    Closing it checkpoints it, so the data is all in the file itself and
    the -wal and -shm files are gone: Datasette's read connections are
    read-only, and when one of them is the last to close, SQLite cannot
    do that itself - copying just the ``.db`` file would lose every row.
    """

    def __init__(self, ds, path, *, on_removed=None):
        super().__init__(ds, path=path, is_mutable=True)
        self.on_removed = on_removed
        self.removed = False

    def close(self):
        already_closed = self._closed
        super().close()
        if already_closed or self.removed:
            return
        if remove_if_empty(self.path):
            self.removed = True
            if self.on_removed is not None:
                self.on_removed(self.path)
            return
        # After remove_if_empty(), whose read-only connection leaves -wal and
        # -shm files behind if there were none
        checkpoint(self.path)


def checkpoint(path: str) -> None:
    """
    Copy everything in the -wal file of the WAL mode database at ``path``
    into the database file, and remove the -wal and -shm files. Call it
    once every connection to the file is closed: a connection that can
    write, closing last, removes them.
    """
    if not os.path.exists(path + "-wal"):
        return
    try:
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning("Could not checkpoint %s", path, exc_info=True)


def remove_if_empty(path: str) -> bool:
    """
    Delete the SQLite database ``path`` (and its -wal, -shm and -journal
    files) if it has no tables, views, indexes or triggers. Returns True if
    it did.
    """
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            count = conn.execute("select count(*) from sqlite_master").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    if count:
        return False
    for suffix in ("", "-wal", "-shm", "-journal"):
        with contextlib.suppress(OSError):
            os.unlink(path + suffix)
    return True


def is_scratch(db) -> bool:
    return bool(getattr(db, "is_scratch", False))


def check_save_file(path: str) -> None:
    """
    Refuse a --save path that cannot work, before anything is downloaded or
    read: a directory, a file that is not a SQLite database, a missing or
    read-only directory, or a name that Datasette's ``file:`` URIs would
    misread.
    """
    if os.path.isdir(path):
        raise SourceError(f"--save {path}: this is a directory")
    # Database.connect() opens "file:{path}?mode=...": a ? or # would end
    # the path early and %XX would be decoded, so the data would go to a
    # different file
    bad = sorted({c for c in path if c in "?#%"})
    if bad:
        raise SourceError(
            f"--save {path}: Datasette cannot open a database whose path "
            f"contains {' or '.join(repr(c) for c in bad)}"
        )
    if os.path.exists(path):
        if os.path.getsize(path):
            with open(path, "rb") as fp:
                if fp.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                    raise SourceError(
                        f"--save {path}: this file exists and is not a SQLite "
                        "database"
                    )
        if not os.access(path, os.W_OK):
            raise SourceError(f"--save {path}: this file is not writable")
        return
    directory = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(directory):
        raise SourceError(f"--save {path}: directory {directory} does not exist")
    if not os.access(directory, os.W_OK):
        raise SourceError(
            f"--save {path}: cannot create a file in {directory}, " "it is not writable"
        )


def create_save_file(path: str) -> bool:
    """
    Create an empty SQLite database at ``path`` if there is no file there.
    Returns True if it created one. Refuses to touch a file that is not a
    SQLite database.
    """
    check_save_file(path)
    if os.path.exists(path):
        return False
    conn = sqlite3.connect(path)
    try:
        conn.execute("vacuum")
    finally:
        conn.close()
    return True


def existing_tables(path: str) -> list[str]:
    "Names of the tables and views in the SQLite file at ``path``."
    if path is None or not os.path.exists(path) or not os.path.getsize(path):
        return []
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        return [
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type in ('table', 'view')"
            )
        ]
    finally:
        conn.close()


def free_bytes(path: str) -> int:
    """
    Bytes of free pages in the SQLite file at ``path``: space that dropped
    tables leave behind until ``VACUUM`` (SQLite reuses it for new data).
    """
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        pages = conn.execute("pragma freelist_count").fetchone()[0]
        return pages * conn.execute("pragma page_size").fetchone()[0]
    finally:
        conn.close()


# Loading ------------------------------------------------------------------------


@dataclasses.dataclass
class LoadedTable:
    "What happened to one source."

    source: Source
    table: str
    loader: str
    rows: int
    seconds: float
    #: False if there were no rows and no columns, so no table was created
    created: bool = True
    types: dict = dataclasses.field(default_factory=dict)
    widenings: list = dataclasses.field(default_factory=list)
    #: Kept from an earlier load in this --reload session, not loaded again
    reused: bool = False
    #: The loader's notes about how it read the file (``LoadResult.notes``)
    notes: list = dataclasses.field(default_factory=list)


def sniff(source: Source, loaders=None):
    """
    Pick the loader for ``source.path`` (or the one its ``:format`` names)
    and store it on ``source.loader``. Raises :class:`ImportFailed` with a
    message naming the source for empty and unrecognized files.
    """
    loaders = _loaders.get_loaders() if loaders is None else list(loaders)
    try:
        with open(source.path, "rb") as fp:
            if fp.read(len(SQLITE_HEADER)) == SQLITE_HEADER:
                if source.kind == "file" and not source.needs_copy:
                    raise ImportFailed(
                        f"{source.spec}: {source.location} is a SQLite "
                        "database, not data to load into a table. Pass it on "
                        f"its own to serve it: datasette {source.location}"
                    )
                raise ImportFailed(
                    f"{source.label} is a SQLite database: save it to a file "
                    "and run datasette FILE.db instead"
                )
        if source.format:
            source.loader = _loaders.get_loader(source.format, loaders)
            return source.loader
        first_bytes, _scores = _loaders.sniff_path(source.path, loaders)
        source.loader = _loaders.pick_loader(first_bytes, loaders)
        return source.loader
    except _loaders.EmptyFile:
        raise ImportFailed(f"{source.label} is empty") from None
    except _loaders.UnrecognizedFormat as ex:
        # The loaders' scores are for whoever debugs a loader, not the reader
        logger.debug("Could not recognize %s: %s", source.label, ex)
        names = ", ".join(sorted(loader.name for loader in loaders))
        raise ImportFailed(
            f"Could not load {source.label}: it does not look like any format "
            f"Datasette can read ({names}). To load it as one of these, add "
            "the format to the argument: FILE:csv, or stdin:csv for standard "
            "input"
        ) from None
    except _loaders.LoaderError as ex:
        raise ImportFailed(f"Could not load {source.label}: {ex}") from None


async def drop_table(db, table: str) -> None:
    "Drop ``table`` from ``db`` if it exists, as the import process."

    def _drop(conn):
        conn.execute(f"drop table if exists {escape_sqlite(table)}")

    with import_writes():
        await db.execute_write_fn(_drop)


# Tables that are being loaded right now, per database - by load_source()
# or a background import (datasette/imports/background.py). A name is
# reserved (synchronously, on the event loop) before anything else happens
# and released when the load ends, so two imports in this process can never
# both decide that they are the one creating a table
_loading = weakref.WeakKeyDictionary()


def reserve_table(db, table: str) -> None:
    """
    Claim ``table`` in ``db`` for an import in this process, or raise
    :class:`TableExists` if another import has claimed it. Call
    :func:`release_table` when the import ends, however it ends.
    """
    names = _loading.setdefault(db, set())
    # SQLite compares table names case-insensitively (ASCII only, so this is
    # if anything stricter)
    key = table.lower()
    if key in names:
        raise TableExists(f"Table {table!r} is already being loaded into {db.name}")
    names.add(key)


def release_table(db, table: str) -> None:
    "Give up a claim made with :func:`reserve_table`."
    names = _loading.get(db)
    if names is not None:
        names.discard(table.lower())


def is_reserved(db, table: str) -> bool:
    return table.lower() in _loading.get(db, ())


# Is there a table or view of this name, in any case? (Tables and views
# share one namespace in SQLite)
_TAKEN_SQL = (
    "select 1 from sqlite_master where type in ('table', 'view') "
    "and name = ? collate nocase"
)


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(_TAKEN_SQL, [table]).fetchone())


async def table_taken(db, table: str) -> bool:
    "Whether ``db`` has a table or view called ``table``, in any case."
    return (await db.execute(_TAKEN_SQL, [table])).first() is not None


class TableClaim:
    """
    ``table`` in ``db``, reserved for one import in this process until
    :meth:`release`: no other import here can create it meanwhile. Taking
    one raises :class:`TableExists` if another import holds that name (see
    :func:`reserve_table`).
    """

    def __init__(self, db, table: str):
        reserve_table(db, table)
        self.db = db
        self.table = table
        self.held = True

    async def check_free(self) -> None:
        "Raise :class:`TableExists` if a table or view of that name is there."
        if await table_taken(self.db, self.table):
            raise TableExists(f"Table {self.table!r} already exists in {self.db.name}")

    def release(self) -> None:
        "Give up the name. Safe to call more than once."
        if self.held:
            self.held = False
            release_table(self.db, self.table)


async def load_source(
    db,
    source: Source,
    *,
    workdir: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sample_size: int = 1000,
    progress=None,
    loaders=None,
    exclusive: bool = False,
    status: ImportStatus | None = None,
) -> LoadedTable:
    """
    Load one source into a new table ``source.table`` in ``db``.

    Fetches the source first if :func:`~datasette.imports.sources.fetch`
    has not run yet, and sniffs it if no loader has been picked. A copy
    that this call downloads or spools itself (into ``workdir``, or a
    private temporary directory) is deleted again when it returns, loaded
    or not; copies made beforehand belong to the caller.

    The table must not exist, and no other ``load_source()`` call in this
    process may be loading a table of the same name (compared
    case-insensitively): either raises :class:`TableExists` before
    anything is fetched. If loading fails or is cancelled, the partly
    loaded table is dropped - only if this call created it - and
    :class:`ImportFailed` (or the cancellation) is raised.
    ``progress(rows_written)`` is called after every chunk (and after each
    slice of converting stored numbers to text after the last one, see
    :func:`~datasette.imports.inserter.import_rows`). Pass an
    :class:`~datasette.imports.inserter.ImportStatus` as ``status`` to see
    when that conversion is under way (``status.finishing``) and how much
    of it is left (``status.backlog``).

    This is how ``--get`` loads its inputs, through :func:`load_sources`,
    with ``exclusive=True``: parsing and writing on the write thread in
    one ``execute_write_fn()`` call (still committing every chunk), which
    holds up every other write to ``db`` until it finishes, so it is only
    for loading before the server starts. ``write_wrapper`` plugins see
    one call for the whole load. Background imports - while Datasette
    serves - use :mod:`datasette.imports.background` and the worker
    subprocess instead.

    With ``exclusive=False`` (only used by tests) every chunk is its own
    ``db.execute_write_fn()`` call while the loader runs in an executor
    thread, so other writes and reads can happen in between. That is
    about 30% slower: the parsing and writing threads never actually run
    in parallel (CPU time equals wall time) and splitting the work across
    them costs extra CPU in every thread.
    """
    from datasette.imports.sources import fetch, remove_temporary

    if not source.table:
        raise ValueError("source.table is not set: see assign_table_names()")
    loop = asyncio.get_running_loop()
    claim = TableClaim(db, source.table)
    fetched_here = source.path is None
    private_dir = None
    try:
        if fetched_here:
            if workdir is None:
                private_dir = workdir = tempfile.mkdtemp(prefix="datasette-import-")
            await fetch(source, workdir)
        if source.loader is None:
            await loop.run_in_executor(None, sniff, source, loaders)
        # Fail early, before parsing anything. The write that creates the
        # table checks again: see _load_new_table()
        await claim.check_free()
        return await _load_new_table(
            db, source, batch_size, sample_size, progress, exclusive, status
        )
    finally:
        claim.release()
        if fetched_here and source.temporary:
            # The rows are in SQLite now (or never will be)
            remove_temporary([source])
        if private_dir is not None:
            remove_tree(private_dir)


async def _load_new_table(
    db, source, batch_size, sample_size, progress, exclusive, status
):
    loop = asyncio.get_running_loop()
    loader = source.loader
    table = source.table
    start = time.perf_counter()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: _loaders.load_path(source.path, [loader], format=loader.name),
        )
        schema = dict(result.schema.columns) if result.schema else None
        with import_writes():
            if exclusive:
                imported = await _load_exclusive(
                    db,
                    table,
                    result.rows,
                    schema,
                    sample_size,
                    batch_size,
                    progress,
                    status,
                )
            else:
                # on_error="drop": if loading fails or is cancelled, the table
                # is dropped - if this import created it - without first
                # converting what a late widening left to convert
                imported = await import_rows(
                    db,
                    table,
                    result.rows,
                    schema=schema,
                    sample_size=sample_size,
                    batch_size=batch_size,
                    progress=progress,
                    on_error="drop",
                    status=status,
                )
    except _loaders.LoaderError as ex:
        raise ImportFailed(f"Error loading {source.label}: {ex}") from ex
    except SQLITE_ERRORS as ex:
        raise ImportFailed(
            f"Error loading {source.label} into table {table!r}: {ex}"
        ) from ex
    return LoadedTable(
        source=source,
        table=table,
        loader=loader.name,
        rows=imported.rows,
        seconds=time.perf_counter() - start,
        created=imported.created,
        types=imported.types,
        widenings=list(imported.widenings),
        notes=list(result.notes),
    )


class _Cancelled(Exception):
    pass


async def _load_exclusive(
    db, table, rows, schema, sample_size, batch_size, progress, status
):
    # insert_rows() on the write thread, committing every chunk. The write
    # thread cannot be interrupted, so cancellation sets a flag that the
    # progress callback checks after every chunk (and every slice of the
    # conversion after the last one).
    #
    # The table is dropped if anything goes wrong: insert_rows() drops it
    # itself (on_error="drop") if it created it, without completing it
    # first; load() drops it if the cancellation arrives once it is
    # complete. load() returns errors rather than raising them, because
    # the write thread prints every exception it sees to stderr and the
    # command line would show each error twice
    cancelled = threading.Event()

    def load(conn):
        if _table_exists(conn, table):
            return None, TableExists(f"Table {table!r} already exists in {db.name}")

        def on_chunk(rows_written):
            if cancelled.is_set():
                raise _Cancelled
            if progress is not None:
                progress(rows_written)

        try:
            imported = insert_rows(
                conn,
                table,
                rows,
                schema=schema,
                sample_size=sample_size,
                batch_size=batch_size,
                progress=on_chunk,
                on_error="drop",
                status=status,
            )
        except Exception as ex:  # noqa: BLE001 - returned, re-raised below
            return None, ex
        if cancelled.is_set():
            if imported.created:
                with contextlib.suppress(Exception):
                    conn.execute(f"drop table if exists {escape_sqlite(table)}")
            return None, _Cancelled()
        return imported, None

    try:
        imported, error = await db.execute_write_fn(load, transaction=False)
    except asyncio.CancelledError:
        cancelled.set()
        # Wait until load() has seen the flag and dropped its table, so the
        # caller can rely on the table being gone (a write queues behind it)
        with contextlib.suppress(Exception):
            await db.execute_write_fn(lambda conn: None, transaction=False)
        raise
    if error is not None:
        raise error
    return imported


@dataclasses.dataclass
class LoadPlan:
    "What :func:`plan_loads` decided for a target database."

    #: Sources that need loading
    load: list
    #: Sources whose table is kept from earlier in this --reload session
    reuse: list
    #: Tables this --reload session created that must be dropped first
    drop: list


def plan_loads(target_path, sources, *, session=None) -> LoadPlan:
    """
    Decide which sources to load into the SQLite file at ``target_path``.

    A table that already exists is an error (:class:`TableExists`) - unless
    this ``--reload`` session created it, in which case it is kept if its
    input has not changed and dropped and loaded again if it has.
    """
    tables = {name.lower(): name for name in existing_tables(target_path)}
    manifest = session.manifest(target_path) if session else {}
    wanted = {source.table.lower(): source for source in sources}
    plan = LoadPlan(load=[], reuse=[], drop=[])
    for table, entry in manifest.items():
        source = wanted.get(table.lower())
        unchanged = (
            source is not None
            and entry.get("complete")
            and entry.get("fingerprint") == source.fingerprint()
            and table.lower() in tables
        )
        if unchanged:
            plan.reuse.append(source)
        elif table.lower() in tables:
            plan.drop.append(tables[table.lower()])
    owned = {table.lower() for table in manifest}
    reused = {source.table.lower() for source in plan.reuse}
    for source in sources:
        key = source.table.lower()
        if key in reused:
            continue
        if key in tables and key not in owned:
            # NAME:source, without the name the source was given
            unnamed = source.spec.split(":", 1)[1] if source.name else source.spec
            raise TableExists(
                f"Table {tables[key]!r} already exists in {target_path}, so "
                f"{source.spec} cannot be loaded into it. Choose another table "
                f"name with NAME:{unnamed}, use a different --save file "
                "or drop that table first."
            )
        plan.load.append(source)
    return plan


async def load_sources(
    db,
    plan: LoadPlan,
    *,
    session=None,
    target_path=None,
    workdir=None,
    batch_size=DEFAULT_BATCH_SIZE,
    on_start=None,
    on_progress=None,
    on_loaded=None,
    exclusive=False,
) -> list:
    """
    Carry out a :class:`LoadPlan`: drop, then load each source in turn.
    All or nothing - if one fails, the tables loaded before it are dropped
    too, so a failed ``--save`` leaves the file with the tables and data it
    had before. (Not byte for byte: the pages the dropped tables used stay
    in the file as free pages, which SQLite reuses, until ``VACUUM``; see
    :func:`free_bytes`.) Returns a :class:`LoadedTable` per source, in
    order.

    ``on_start(source)``, ``on_progress(source, rows, status)`` and
    ``on_loaded(loaded_table)`` report progress: ``status`` is the
    source's :class:`~datasette.imports.inserter.ImportStatus`, which says
    when the rows are all in and stored numbers are being converted to
    text (``status.finishing``, ``status.backlog``). ``exclusive`` is
    passed to :func:`load_source` (``on_progress`` is then called from the
    write thread).
    """
    for table in plan.drop:
        await drop_table(db, table)
        if session:
            session.forget(target_path, table)
    manifest = session.manifest(target_path) if session else {}
    results = [
        LoadedTable(
            source=source,
            table=source.table,
            loader=(manifest.get(source.table) or {}).get("loader") or "?",
            rows=0,
            seconds=0.0,
            reused=True,
        )
        for source in plan.reuse
    ]
    for result in results:
        if on_loaded:
            on_loaded(result)
    created = []
    try:
        for source in plan.load:
            if on_start:
                on_start(source)
            if session:
                session.remember(target_path, source, complete=False)
            progress = None
            status = ImportStatus()
            if on_progress:

                def progress(rows, source=source, status=status):
                    on_progress(source, rows, status)

            result = await load_source(
                db,
                source,
                workdir=workdir,
                batch_size=batch_size,
                progress=progress,
                exclusive=exclusive,
                status=status,
            )
            created.append(source.table)
            if session:
                session.remember(
                    target_path, source, complete=True, loader=result.loader
                )
            results.append(result)
            if on_loaded:
                on_loaded(result)
    except BaseException:
        for table in created:
            with contextlib.suppress(Exception):
                await drop_table(db, table)
            if session:
                session.forget(target_path, table)
        raise
    return results


# --reload sessions ------------------------------------------------------------


class ReloadSession:
    """
    State shared by the successive worker processes of one ``--reload``
    run, in a directory that the reloader process removes when it exits.

    - Downloads and standard input are fetched once, by the first worker,
      and every later worker loads the same copy: stdin cannot be read
      twice, and code changes should not download a URL again.
    - The scratch database lives here, and a manifest records which tables
      each worker loaded from which input (path, size, modification time).
      A worker started because a template or plugin changed keeps those
      tables; one started because an input file changed drops and loads
      just that table again. The same goes for tables a session created in
      a ``--save`` file.
    """

    ENV = "DATASETTE_IMPORT_SESSION"

    def __init__(self, directory):
        self.directory = directory

    @classmethod
    def start(cls):
        "Called by the reloader process before it starts the first worker."
        directory = tempfile.mkdtemp(prefix="datasette-reload-")
        os.environ[cls.ENV] = directory
        atexit.register(remove_tree, directory)
        return cls(directory)

    @classmethod
    def current(cls):
        directory = os.environ.get(cls.ENV)
        if directory and os.path.isdir(directory):
            return cls(directory)
        return None

    @property
    def scratch_path(self):
        return os.path.join(self.directory, "scratch.db")

    def _read(self):
        try:
            with open(os.path.join(self.directory, "session.json")) as fp:
                return json.load(fp)
        except (OSError, ValueError):
            return {"copies": {}, "manifests": {}}

    def _write(self, data):
        path = os.path.join(self.directory, "session.json")
        with open(path + ".tmp", "w") as fp:
            json.dump(data, fp)
        os.replace(path + ".tmp", path)

    @staticmethod
    def copy_key(source):
        return f"{source.kind}:{source.location}"

    def cached_copy(self, source):
        path = self._read()["copies"].get(self.copy_key(source))
        return path if path and os.path.exists(path) else None

    def remember_copy(self, source):
        data = self._read()
        data["copies"][self.copy_key(source)] = source.path
        self._write(data)

    def manifest(self, target_path):
        return self._read()["manifests"].get(os.path.realpath(target_path), {})

    def remember(self, target_path, source, *, complete, loader=None):
        data = self._read()
        manifest = data["manifests"].setdefault(os.path.realpath(target_path), {})
        manifest[source.table] = {
            "fingerprint": source.fingerprint(),
            "complete": complete,
            "loader": loader,
        }
        self._write(data)

    def forget(self, target_path, table):
        data = self._read()
        data["manifests"].get(os.path.realpath(target_path), {}).pop(table, None)
        self._write(data)
