"""
Loading command-line sources into the ``scratch`` database (or a ``--save``
file).

``scratch`` is a SQLite file in WAL mode in the temporary directory, deleted
when Datasette exits. WAL rather than a shared-cache in-memory database
because shared-cache memory fails with "database table is locked" as soon
as anything reads while an import writes (see prototypes/data_csv). It is
read-only for everyone except the import itself: see
``datasette/imports/default_hooks.py``.

:func:`load_source` loads one source into one new table through
``db.execute_write_fn()`` (so ``write_wrapper`` plugins see every chunk) and
is the unit a background task can run. :func:`load_sources` loads several,
all or nothing, for the command line.
"""

import asyncio
import atexit
import contextlib
import contextvars
import dataclasses
import json
import os
import sqlite3
import tempfile
import threading
import time

from datasette.database import Database
from datasette.imports import loaders as _loaders
from datasette.imports.inserter import import_rows, insert_rows
from datasette.imports.sources import (
    SQLITE_HEADER,
    Source,
    SourceError,
    remove_tree,
)

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


class MemoryScratchDatabase(Database):
    """
    A shared-cache in-memory scratch database. Not used by default: it
    breaks with "database table is locked" when queried during an import.
    Kept so the two can be compared (``DATASETTE_SCRATCH_STORAGE=memory``).
    """

    is_scratch = True

    def __init__(self, ds):
        super().__init__(ds, memory_name=f"datasette_scratch_{os.getpid()}_{id(self)}")
        self.loaded = []
        self.save_command = None
        self.from_stdin = False
        self.keep = True

    def suggest_name(self):
        return SCRATCH_NAME


def is_scratch(db) -> bool:
    return bool(getattr(db, "is_scratch", False))


def check_save_file(path: str) -> None:
    "Refuse a --save path that exists and is not a SQLite database."
    if os.path.isdir(path):
        raise SourceError(f"--save {path}: this is a directory")
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, "rb") as fp:
            if fp.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                raise SourceError(
                    f"--save {path}: this file exists and is not a SQLite database"
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
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type in ('table', 'view')"
            )
        ]
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


def sniff(source: Source, loaders=None):
    """
    Pick the loader for ``source.path`` (or the one its ``:format`` names)
    and store it on ``source.loader``. Raises :class:`ImportFailed` with a
    message naming the source for empty and unrecognized files.
    """
    loaders = _loaders.get_loaders() if loaders is None else list(loaders)
    try:
        if source.format:
            source.loader = _loaders.get_loader(source.format, loaders)
            return source.loader
        with open(source.path, "rb") as fp:
            if fp.read(len(SQLITE_HEADER)) == SQLITE_HEADER:
                raise ImportFailed(
                    f"{source.label} is a SQLite database: save it to a file "
                    "and run datasette FILE.db instead"
                )
        first_bytes, _scores = _loaders.sniff_path(source.path, loaders)
        source.loader = _loaders.pick_loader(first_bytes, loaders)
        return source.loader
    except _loaders.EmptyFile:
        raise ImportFailed(f"{source.label} is empty") from None
    except _loaders.UnrecognizedFormat as ex:
        names = ", ".join(sorted(loader.name for loader in loaders))
        raise ImportFailed(
            f"Could not load {source.label}: it does not look like any format "
            f"Datasette can read ({names}). {ex}"
        ) from None
    except _loaders.LoaderError as ex:
        raise ImportFailed(f"Could not load {source.label}: {ex}") from None


async def drop_table(db, table: str) -> None:
    "Drop ``table`` from ``db`` if it exists, as the import process."

    def _drop(conn):
        conn.execute(f"drop table if exists [{table.replace(']', ']]')}]")

    with import_writes():
        await db.execute_write_fn(_drop)


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
) -> LoadedTable:
    """
    Load one source into a new table ``source.table`` in ``db``.

    Fetches the source first if :func:`~datasette.imports.sources.fetch`
    has not run yet (into ``workdir``), and sniffs it if no loader has been
    picked. The table must not exist. If loading fails or is cancelled the
    partly loaded table is dropped and :class:`ImportFailed` (or the
    cancellation) is raised. ``progress(rows_written)`` is called after
    every chunk.

    By default every chunk is its own ``db.execute_write_fn()`` call while
    the loader runs in an executor thread, so other writes and reads can
    happen in between: this is the unit of work a background import task
    runs. ``exclusive=True`` instead parses and writes on the write thread
    in one ``execute_write_fn()`` call (still committing every chunk).
    That is about 30% faster - the two threads contending for the GIL cost
    more than they overlap - but holds up every other write to ``db`` until
    it finishes, so it is only for loading before the server starts.
    ``write_wrapper`` plugins then see one call for the whole load.
    """
    from datasette.imports.sources import fetch

    loop = asyncio.get_running_loop()
    if source.path is None:
        await fetch(source, workdir or tempfile.gettempdir())
    if source.loader is None:
        await loop.run_in_executor(None, sniff, source, loaders)
    loader = source.loader
    # Checked here as well as in plan_loads(): on failure the table is
    # dropped, which must never hit a table this import did not create
    taken = await db.execute(
        "select name from sqlite_master where type in ('table', 'view') "
        "and name = ? collate nocase",
        [source.table],
    )
    if taken.first():
        raise TableExists(f"Table {source.table!r} already exists in {db.name}")
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
                    source.table,
                    result.rows,
                    schema,
                    sample_size,
                    batch_size,
                    progress,
                )
            else:
                imported = await import_rows(
                    db,
                    source.table,
                    result.rows,
                    schema=schema,
                    sample_size=sample_size,
                    batch_size=batch_size,
                    progress=progress,
                )
    except BaseException as ex:
        with contextlib.suppress(Exception):
            await drop_table(db, source.table)
        if isinstance(ex, _loaders.LoaderError):
            raise ImportFailed(f"Error loading {source.label}: {ex}") from ex
        if isinstance(ex, sqlite3.Error):
            raise ImportFailed(
                f"Error loading {source.label} into table {source.table!r}: {ex}"
            ) from ex
        raise
    return LoadedTable(
        source=source,
        table=source.table,
        loader=loader.name,
        rows=imported.rows,
        seconds=time.perf_counter() - start,
        created=imported.created,
        types=imported.types,
        widenings=list(imported.widenings),
    )


class _Cancelled(Exception):
    pass


async def _load_exclusive(db, table, rows, schema, sample_size, batch_size, progress):
    # insert_rows() on the write thread, committing every chunk. The write
    # thread cannot be interrupted, so cancellation sets a flag that the
    # per-chunk progress callback checks
    cancelled = threading.Event()

    def on_chunk(rows_written):
        if cancelled.is_set():
            raise _Cancelled
        if progress is not None:
            progress(rows_written)

    def load(conn):
        return insert_rows(
            conn,
            table,
            rows,
            schema=schema,
            sample_size=sample_size,
            batch_size=batch_size,
            progress=on_chunk,
        )

    try:
        return await db.execute_write_fn(load, transaction=False)
    except asyncio.CancelledError:
        cancelled.set()
        raise


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
            raise TableExists(
                f"Table {tables[key]!r} already exists in {target_path}, so "
                f"{source.spec} cannot be loaded into it. Choose another table "
                f"name with NAME:{source.spec}, use a different --save file "
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
    too, so a failed ``--save`` leaves the file as it was. Returns a
    :class:`LoadedTable` per source, in order.

    ``on_start(source)``, ``on_progress(source, rows)`` and
    ``on_loaded(loaded_table)`` report progress. ``exclusive`` is passed to
    :func:`load_source` (``on_progress`` is then called from the write
    thread).
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
            if on_progress:

                def progress(rows, source=source):
                    on_progress(source, rows)

            result = await load_source(
                db,
                source,
                workdir=workdir,
                batch_size=batch_size,
                progress=progress,
                exclusive=exclusive,
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
