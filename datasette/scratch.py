"""Scratch databases: SQLite files that plugins create, fill, change and
throw away, and that survive a restart.

The scratch directory looks like this::

    <scratch dir>/
        .datasette-scratch.db     the registry: one row per scratch database
        .datasette-scratch.lock   flock()ed by the Datasette instance using it
        <name>.db                 one SQLite file per scratch database
        <name>.db-wal, -shm       while the database is open (WAL mode)

Why a registry file next to the databases (and not metadata inside each
file, or rows in the internal database): see "Scratch databases" in
docs/internals.rst. In short:

* Each registry change is one SQLite transaction. create, delete and rename
  first record an intent (``state`` = creating / deleting / renaming), then
  change the files, then finish the row. ``_reconcile()`` at startup rolls
  back an interrupted create and finishes an interrupted delete or rename,
  so a crash at any point leaves either the old or the new state, never a
  database without its metadata or a stray ``-wal`` file.
* Startup reads one small file and lists one directory. No scratch database
  is opened, and no thread is started, until something uses it.
* The registry lives with the files, so it survives a temporary internal
  database, and moving or copying the directory keeps the metadata.
* ``last_used`` is tracked in memory on every read and write and written to
  the registry at most once per ``LAST_USED_FLUSH_INTERVAL_S`` (and on
  close, and with any other registry change) - never a write per read.

The directory is the source of truth for which databases exist: a ``.db``
file copied in by hand is adopted at the next startup (without owner or
metadata), and a row whose file was deleted by hand is dropped.
"""

import asyncio
import dataclasses
import errno
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import weakref
from pathlib import Path

from .database import Database, DatasetteClosedError
from .utils import StartupError, sqlite3

logger = logging.getLogger("datasette.scratch")

REGISTRY_FILENAME = ".datasette-scratch.db"
LOCK_FILENAME = ".datasette-scratch.lock"
SUFFIX = ".db"
# Removal order: -wal first. A -wal left next to a missing main file would be
# replayed into the next database file of that name (SQLite says "database
# disk image is malformed")
SIDECAR_SUFFIXES = ("-wal", "-journal", "-shm")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
# Characters that would change the meaning of the file: URI Datasette opens
# databases with
UNSAFE_PATH_CHARS = ("?", "#", "%")
LAST_USED_FLUSH_INTERVAL_S = 60.0
SQLITE_HEADER = b"SQLite format 3\x00"

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS scratch_databases (
    name TEXT PRIMARY KEY,
    created REAL NOT NULL,
    last_used REAL,
    owner TEXT,
    metadata TEXT,
    -- ready | creating | deleting | renaming
    state TEXT NOT NULL DEFAULT 'ready',
    rename_to TEXT
)
"""


# Registry statements. create, delete and rename record their intent first
# (state = creating / deleting / renaming), then change the files, then
# finish the row - see _reconcile()
SQL_INSERT_CREATING = (
    "insert into scratch_databases "
    "(name, created, last_used, owner, metadata, state) "
    "values (?, ?, ?, ?, ?, 'creating')"
)
SQL_MARK_READY = "update scratch_databases set state = 'ready' where name = ?"
SQL_MARK_DELETING = "update scratch_databases set state = 'deleting' where name = ?"
SQL_MARK_RENAMING = (
    "update scratch_databases set state = 'renaming', rename_to = ? where name = ?"
)
SQL_FINISH_RENAME = (
    "update scratch_databases set name = ?, state = 'ready', rename_to = null "
    "where name = ?"
)
SQL_CANCEL_RENAME = (
    "update scratch_databases set state = 'ready', rename_to = null where name = ?"
)
SQL_DELETE = "delete from scratch_databases where name = ?"
SQL_INSERT_ADOPTED = "insert into scratch_databases (name, created) values (?, ?)"


class ScratchDatabaseError(Exception):
    """A scratch database operation could not be carried out."""


class ScratchDatabaseExists(ScratchDatabaseError):
    """A database with that name already exists."""


class ScratchDatabaseNotFound(ScratchDatabaseError):
    """There is no scratch database with that name."""


class ScratchDatabaseDeleted(DatasetteClosedError):
    """Raised to callers using a scratch database that has been deleted or
    renamed, including calls that were queued when that happened."""


@dataclasses.dataclass(frozen=True)
class ScratchDatabaseInfo:
    name: str
    path: str
    created: float
    last_used: float | None
    size: int
    owner: str | None
    metadata: dict
    attached: bool


class _Entry:
    __slots__ = (
        "created",
        "db",
        "dirty",
        "last_used",
        "metadata",
        "name",
        "owner",
        "state",
    )

    def __init__(self, name, created, owner=None, metadata=None, state="ready"):
        self.name = name
        self.created = created
        self.last_used = None
        self.owner = owner
        self.metadata = metadata or {}
        self.state = state
        self.db = None
        self.dirty = False


class ScratchDatabase(Database):
    """A database in the scratch directory, created with
    ``datasette.create_scratch_database()``.

    Behaves like any other mutable file database. Its catalog is kept current
    by its own writes (schema watch mode ``owned``). Once it has been deleted
    or renamed every call - including reads and writes that were already
    queued - raises :class:`ScratchDatabaseDeleted`.
    """

    is_scratch = True

    def __init__(self, ds, path, *, entry, manager):
        super().__init__(ds, path=str(path), is_mutable=True)
        self._scratch_entry = entry
        self._scratch_manager = manager
        # None while usable, otherwise why it is not ("was deleted")
        self._scratch_gone = None
        # Datasette created this file or found it in the scratch directory:
        # writes use mode=rw and never recreate it if it is deleted by hand
        self._file_seen = True

    @property
    def owner(self):
        return self._scratch_entry.owner

    @property
    def created(self):
        return self._scratch_entry.created

    @property
    def last_used(self):
        return self._scratch_entry.last_used

    @property
    def scratch_metadata(self):
        return dict(self._scratch_entry.metadata)

    @property
    def size(self):
        # 0 once deleted, rather than FileNotFoundError (repr() uses this)
        try:
            return super().size
        except FileNotFoundError:
            return 0

    def _gone_error(self):
        return ScratchDatabaseDeleted(
            f"Scratch database {self.name!r} {self._scratch_gone}"
        )

    def _check_not_closed(self):
        if self._scratch_gone is not None:
            raise self._gone_error()
        super()._check_not_closed()

    def _guard(self, fn):
        # Runs when the queued call is picked up by a thread: anything queued
        # before a delete fails here instead of touching the file
        def guarded(conn):
            if self._scratch_gone is not None:
                raise self._gone_error()
            return fn(conn)

        return guarded

    def _translate(self, e):
        if self._scratch_gone is not None and not isinstance(e, ScratchDatabaseDeleted):
            return self._gone_error()
        return None

    async def _execute_fn(self, fn):
        self._check_not_closed()
        self._scratch_manager._touch(self)
        try:
            return await super()._execute_fn(self._guard(fn))
        except DatasetteClosedError as e:
            raise (self._translate(e) or e) from None

    async def _execute_write_fn(self, fn, block=True, transaction=True, request=None):
        self._check_not_closed()
        self._scratch_manager._touch(self)
        return await super()._execute_write_fn(
            fn, block=block, transaction=transaction, request=request
        )

    async def execute_isolated_fn(self, fn):
        self._check_not_closed()
        self._scratch_manager._touch(self)
        return await super().execute_isolated_fn(fn)

    async def _send_to_write_thread(self, fn, **kwargs):
        try:
            return await super()._send_to_write_thread(self._guard(fn), **kwargs)
        except DatasetteClosedError as e:
            raise (self._translate(e) or e) from None


def _owner_id(actor):
    if actor is None:
        return None
    if isinstance(actor, dict):
        actor_id = actor.get("id")
        return None if actor_id is None else str(actor_id)
    return str(actor)


def _fsync_dir(directory):
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _looks_like_sqlite(path):
    try:
        with open(path, "rb") as fp:
            header = fp.read(16)
    except OSError:
        return False
    # An empty file is a valid, empty SQLite database
    return header == b"" or header == SQLITE_HEADER


class ScratchDatabases:
    """Owns the scratch directory and its registry. One per Datasette,
    available as ``datasette._scratch``; plugins use the Datasette methods
    (``create_scratch_database()`` etc.)."""

    def __init__(self, ds, directory=None):
        self.ds = ds
        self.configured_dir = (
            Path(directory).expanduser().resolve() if directory else None
        )
        # The directory in use: configured_dir, or a temporary directory
        # created by the first create_scratch_database() call
        self.directory = None
        self._entries = {}
        # Protects _entries and _dirty. Never held across a blocking call
        self._lock = threading.Lock()
        # Serializes registry transactions (they run on worker threads)
        self._registry_lock = threading.Lock()
        self._registry_initialized = False
        self._dirty = set()
        self._last_flush = time.monotonic()
        self._flush_scheduled = False
        self._closed = False
        self._finalizers = []
        self.counters = {
            "registry_transactions": 0,
            "last_used_flushes": 0,
            "adopted": 0,
            "dropped_missing": 0,
            "rolled_back_creates": 0,
            "finished_deletes": 0,
            "finished_renames": 0,
            "stray_files_removed": 0,
        }

    @property
    def persistent(self):
        return self.configured_dir is not None

    # ------------------------------------------------------------------
    # startup
    # ------------------------------------------------------------------
    def load(self):
        """Called from Datasette.__init__. Attaches every scratch database
        in the configured directory without opening any of them."""
        if self.configured_dir is None:
            return
        self._open_directory(self.configured_dir)
        for entry in self._reconcile():
            self._attach(entry)

    def _open_directory(self, directory):
        directory = Path(directory)
        if any(c in str(directory) for c in UNSAFE_PATH_CHARS):
            raise StartupError(
                f"Scratch directory path {str(directory)!r} must not contain any of "
                f"{' '.join(UNSAFE_PATH_CHARS)}"
            )
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise StartupError(f"Cannot create scratch directory {directory}: {e}")
        self._acquire_directory_lock(directory)
        self.directory = str(directory)

    def _acquire_directory_lock(self, directory):
        try:
            import fcntl
        except ImportError:  # Windows, Pyodide: no advisory lock
            return
        fd = os.open(os.path.join(directory, LOCK_FILENAME), os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise StartupError(
                    f"Scratch directory {directory} is in use by another Datasette instance"
                )
            # flock() not supported here (some network file systems,
            # Emscripten): carry on without the lock
            logger.warning("Could not lock scratch directory %s: %s", directory, e)
            return
        # Released by close(), or when this object is garbage collected
        self._finalizers.append(weakref.finalize(self, os.close, fd))

    def _ensure_directory(self):
        if self.directory is not None:
            return
        with self._lock:
            if self.directory is not None:
                return
            path = tempfile.mkdtemp(prefix="datasette_scratch_")
            # Removed by close(), or at exit. Holds only the path, so it
            # does not keep this Datasette alive (unlike atexit.register of a
            # bound method)
            self._finalizers.append(
                weakref.finalize(self, shutil.rmtree, path, ignore_errors=True)
            )
            self.directory = path

    def _reconcile(self):
        """Bring the registry and the directory into agreement. Returns the
        entries to attach. Runs once, at startup, before anything else can
        use the directory (the directory lock is held)."""
        directory = self.directory
        files = set(os.listdir(directory))
        on_disk = set()
        for filename in files:
            if not filename.endswith(SUFFIX) or filename.startswith("."):
                continue
            name = filename[: -len(SUFFIX)]
            if NAME_RE.fullmatch(name):
                on_disk.add(name)
            else:
                logger.warning(
                    "Ignoring %s in scratch directory %s: not a valid scratch database name",
                    filename,
                    directory,
                )
        statements = []
        entries = {}
        with self._registry_lock:
            conn = self._registry_connection()
            try:
                rows = conn.execute(
                    "select name, created, last_used, owner, metadata, state, rename_to "
                    "from scratch_databases"
                ).fetchall()
            finally:
                conn.close()
        for name, created, last_used, owner, metadata, state, rename_to in rows:
            if state == "creating":
                # The create never returned to its caller: roll it back
                self._unlink_files(name)
                on_disk.discard(name)
                statements.append((SQL_DELETE, [name]))
                self.counters["rolled_back_creates"] += 1
                continue
            if state == "deleting":
                self._unlink_files(name)
                on_disk.discard(name)
                statements.append((SQL_DELETE, [name]))
                self.counters["finished_deletes"] += 1
                continue
            final_name = name
            if state == "renaming":
                old_exists, new_exists = name in on_disk, rename_to in on_disk
                if old_exists and new_exists and self._same_file(name, rename_to):
                    # Crashed between link() and unlink()
                    self._unlink_files(name)
                    old_exists = False
                    on_disk.discard(name)
                if new_exists and not old_exists:
                    final_name = rename_to
                    statements.append((SQL_FINISH_RENAME, [rename_to, name]))
                    self.counters["finished_renames"] += 1
                else:
                    statements.append((SQL_CANCEL_RENAME, [name]))
            if final_name not in on_disk:
                # Deleted by hand
                statements.append((SQL_DELETE, [name]))
                self.counters["dropped_missing"] += 1
                continue
            on_disk.discard(final_name)
            entry = _Entry(
                final_name, created, owner, json.loads(metadata) if metadata else {}
            )
            entry.last_used = last_used
            entries[final_name] = entry
        for name in sorted(on_disk):
            path = self._path(name)
            if not _looks_like_sqlite(path):
                logger.warning("Ignoring %s: not a SQLite database", path)
                continue
            # Copied in by hand: adopt it, with no owner or metadata
            created = os.stat(path).st_mtime
            statements.append((SQL_INSERT_ADOPTED, [name, created]))
            entries[name] = _Entry(name, created)
            self.counters["adopted"] += 1
        # -wal/-shm/-journal files whose database is gone would be replayed
        # into the next database created under that name
        files = set(os.listdir(directory))
        for filename in files:
            for suffix in SIDECAR_SUFFIXES:
                if (
                    filename.endswith(SUFFIX + suffix)
                    and not filename.startswith(".")
                    and filename[: -len(suffix)] not in files
                ):
                    try:
                        os.unlink(os.path.join(directory, filename))
                        self.counters["stray_files_removed"] += 1
                    except OSError:
                        pass
        if statements:
            self._registry_write(statements, flush_dirty=False)
        with self._lock:
            self._entries.update(entries)
        return list(entries.values())

    def _same_file(self, a, b):
        try:
            return os.path.samefile(self._path(a), self._path(b))
        except OSError:
            return False

    # ------------------------------------------------------------------
    # registry
    # ------------------------------------------------------------------
    def _registry_connection(self):
        # Short-lived, plain connection: the registry is never held open
        conn = sqlite3.connect(
            os.path.join(self.directory, REGISTRY_FILENAME),
            isolation_level=None,
            check_same_thread=False,
        )
        if not self._registry_initialized:
            conn.execute(REGISTRY_SCHEMA)
            self._registry_initialized = True
        return conn

    def _registry_write(self, statements, flush_dirty=True):
        """Run statements in one transaction, plus any pending last_used
        updates. Blocking: call from a worker thread (or inline when there
        are no threads)."""
        with self._registry_lock:
            dirty = self._take_dirty() if flush_dirty else []
            if not statements and not dirty:
                return
            conn = self._registry_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for sql, params in statements:
                    conn.execute(sql, params)
                if dirty:
                    conn.executemany(
                        "update scratch_databases set last_used = ? where name = ?",
                        [(entry.last_used, entry.name) for entry in dirty],
                    )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                self._restore_dirty(dirty)
                raise
            finally:
                conn.close()
            self.counters["registry_transactions"] += 1
            if dirty:
                self.counters["last_used_flushes"] += 1

    def _take_dirty(self):
        with self._lock:
            dirty = [e for e in self._dirty if e.last_used is not None]
            for entry in self._dirty:
                entry.dirty = False
            self._dirty.clear()
            self._last_flush = time.monotonic()
        return dirty

    def _restore_dirty(self, dirty):
        with self._lock:
            for entry in dirty:
                entry.dirty = True
                self._dirty.add(entry)

    def flush(self):
        """Write in-memory last_used times to the registry (blocking)."""
        if self.directory is None:
            return
        self._registry_write([])

    def _touch(self, db):
        # On every read and write: a clock read and two attribute stores.
        # The registry is written at most once per LAST_USED_FLUSH_INTERVAL_S
        entry = db._scratch_entry
        entry.last_used = time.time()
        if not entry.dirty:
            with self._lock:
                entry.dirty = True
                self._dirty.add(entry)
        if (
            not self._flush_scheduled
            and time.monotonic() - self._last_flush >= LAST_USED_FLUSH_INTERVAL_S
        ):
            self._schedule_flush()

    def _schedule_flush(self):
        with self._lock:
            if self._flush_scheduled or self._closed:
                return
            self._flush_scheduled = True
        executor = self.ds.executor
        if executor is None:
            # No threads (Pyodide): one small transaction, inline
            self._flush_quietly()
            return
        try:
            executor.submit(self._flush_quietly)
        except RuntimeError:
            # Executor already shut down: close() flushes
            self._flush_scheduled = False

    def _flush_quietly(self):
        try:
            self.flush()
        except Exception:
            logger.exception("Could not record scratch database last_used times")
        finally:
            self._last_flush = time.monotonic()
            self._flush_scheduled = False

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _path(self, name):
        return os.path.join(self.directory, name + SUFFIX)

    def _unlink_files(self, name, main=True):
        path = self._path(name)
        for suffix in SIDECAR_SUFFIXES + (("",) if main else ()):
            try:
                os.unlink(path + suffix)
            except FileNotFoundError:
                pass

    async def _run_blocking(self, fn, *args):
        """Blocking file and registry work: on a worker thread - not
        ds.executor, whose threads may be the very ones a close() waits for
        - or inline when there are no threads (num_sql_threads=0)."""
        if self.ds.executor is None:
            return fn(*args)
        return await asyncio.to_thread(fn, *args)

    def _check_open(self):
        if self._closed or self.ds._closed:
            raise DatasetteClosedError("Datasette has been closed")

    @staticmethod
    def validate_name(name):
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ValueError(
                f"Invalid scratch database name {name!r}: use 1-64 letters, digits, "
                "'_' or '-', starting with a letter or digit"
            )

    def _check_available_locked(self, name, ignore=None):
        if name in self.ds.databases and name != ignore:
            raise ScratchDatabaseExists(f"A database called {name!r} already exists")
        lowered = name.lower()
        for other in self._entries:
            # Case-insensitive: Foo.db and foo.db are one file on macOS and
            # Windows
            if other.lower() == lowered and other != ignore:
                raise ScratchDatabaseExists(
                    f"A scratch database called {other!r} already exists"
                )

    def _generate_name_locked(self):
        while True:
            name = f"scratch_{secrets.token_hex(4)}"
            try:
                self._check_available_locked(name)
            except ScratchDatabaseExists:
                continue
            return name

    def _attach(self, entry):
        db = ScratchDatabase(self.ds, self._path(entry.name), entry=entry, manager=self)
        entry.db = db
        if entry.name in self.ds.databases:
            logger.warning(
                "Scratch database %r not attached: a database with that name already exists",
                entry.name,
            )
            return db
        self.ds.add_database(db, name=entry.name, schema_watch="owned")
        return db

    def _is_attached(self, entry):
        return entry.db is not None and self.ds.databases.get(entry.name) is entry.db

    def _get_entry_locked(self, name):
        entry = self._entries.get(name)
        if entry is None or entry.state != "ready":
            raise ScratchDatabaseNotFound(f"No scratch database called {name!r}")
        return entry

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------
    async def create(self, name=None, *, actor=None, metadata=None):
        self._check_open()
        if name is not None:
            self.validate_name(name)
        owner = _owner_id(actor)
        metadata = dict(metadata or {})
        metadata_json = json.dumps(metadata)  # fail early if not JSON
        if self.directory is None:
            await self._run_blocking(self._ensure_directory)
        with self._lock:
            if name is None:
                name = self._generate_name_locked()
            else:
                self._check_available_locked(name)
            # Reserves the name until the create finishes or fails
            entry = _Entry(name, time.time(), owner, metadata, state="creating")
            self._entries[name] = entry
        try:
            await self._run_blocking(self._create_files, entry, metadata_json)
        except BaseException:
            with self._lock:
                self._entries.pop(name, None)
            raise
        with self._lock:
            # add_database() may have taken the name while we were creating
            # the file - it does not know about scratch reservations
            taken = name in self.ds.databases
            if not taken:
                entry.state = "ready"
                entry.last_used = entry.created
        if taken:
            await self._run_blocking(self._delete_files, entry)
            with self._lock:
                self._entries.pop(name, None)
            raise ScratchDatabaseExists(f"A database called {name!r} already exists")
        db = self._attach(entry)
        # Read-your-writes for the catalog: catalog_databases has a row for
        # the new database when this returns
        await db._after_write()
        return db

    def _create_files(self, entry, metadata_json):
        name = entry.name
        path = self._path(name)
        # Sidecars left by a crash would be replayed into the new file
        self._unlink_files(name, main=False)
        self._registry_write(
            [
                (
                    SQL_INSERT_CREATING,
                    [name, entry.created, entry.created, entry.owner, metadata_json],
                )
            ]
        )
        created_file = False
        try:
            # O_EXCL: never adopt (and later delete) a file that is
            # already there
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            created_file = True
            conn = sqlite3.connect(path)
            try:
                if self.ds.nolock:
                    # nolock=1 readers cannot open WAL databases
                    conn.execute("VACUUM")
                else:
                    # Readers never wait for writers (copying data in while
                    # the database is being browsed)
                    conn.execute("PRAGMA journal_mode=WAL").fetchone()
            finally:
                conn.close()
            _fsync_dir(self.directory)
            self._registry_write([(SQL_MARK_READY, [name])])
        except BaseException:
            if created_file:
                self._unlink_files(name)
            try:
                self._registry_write(
                    [(SQL_DELETE, [name])],
                    flush_dirty=False,
                )
            except Exception:
                # Left as 'creating': rolled back at the next startup
                logger.exception("Could not roll back scratch database %r", name)
            raise

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------
    async def delete(self, name):
        self._check_open()
        with self._lock:
            entry = self._get_entry_locked(name)
            entry.state = "deleting"
        try:
            await self._run_blocking(
                self._registry_write,
                [(SQL_MARK_DELETING, [name])],
            )
        except BaseException:
            with self._lock:
                entry.state = "ready"
            raise
        # From here on the delete always completes: if this process dies,
        # the 'deleting' row makes the next startup finish it
        await self._detach_and_close(entry, "was deleted")
        await self._run_blocking(self._delete_files, entry)
        await self._delete_catalog(name)
        with self._lock:
            if self._entries.get(name) is entry:
                del self._entries[name]

    async def _detach_and_close(self, entry, reason):
        db = entry.db
        db._scratch_gone = reason
        state = db._watch_state
        if state is not None:
            fut = state.scan_future
            if fut is not None and not fut.done():
                try:
                    same_loop = fut.get_loop() is asyncio.get_running_loop()
                except RuntimeError:
                    same_loop = False
                if same_loop:
                    # A catalog scan in flight: let it finish, its rows are
                    # deleted below
                    await asyncio.wait([fut])
        if self.ds.databases.get(entry.name) is db:
            self.ds._detach_database(entry.name)
        await self._run_blocking(self._close_and_wait, db)

    @staticmethod
    def _close_and_wait(db):
        """Close db and wait until nothing has its files open: close() waits
        for queued and running reads and writes, but gives up on a write
        thread after 10s; the files must not be removed while it runs."""
        db.close()
        with db._write_thread_lock:
            threads = [db._write_thread, *db._retiring_write_threads]
        for thread in threads:
            if thread is not None and thread is not threading.current_thread():
                thread.join()
        # The SchemaWatcher's short-lived scan connections
        db._wait_for_untracked_connections()

    def _delete_files(self, entry):
        self._unlink_files(entry.name)
        _fsync_dir(self.directory)
        self._registry_write([(SQL_DELETE, [entry.name])])

    async def _delete_catalog(self, name):
        if self.ds.internal_db_created:
            # unregister() already spawned this as a task; awaiting it here
            # means the rows are gone when delete returns (it is idempotent)
            await self.ds._schema_watcher._delete_catalog([name])

    # ------------------------------------------------------------------
    # rename
    # ------------------------------------------------------------------
    async def rename(self, name, new_name):
        self._check_open()
        self.validate_name(new_name)
        with self._lock:
            entry = self._get_entry_locked(name)
            if new_name == name:
                return entry.db
            # ignore=name allows a case-only rename (foo -> Foo)
            self._check_available_locked(new_name, ignore=name)
            entry.state = "renaming"
            # Reserve the new name
            target = _Entry(
                new_name, entry.created, entry.owner, entry.metadata, "creating"
            )
            target.last_used = entry.last_used
            self._entries[new_name] = target
        try:
            await self._run_blocking(
                self._registry_write,
                [(SQL_MARK_RENAMING, [new_name, name])],
            )
        except BaseException:
            with self._lock:
                entry.state = "ready"
                if self._entries.get(new_name) is target:
                    del self._entries[new_name]
            raise
        await self._detach_and_close(entry, f"was renamed to {new_name!r}")
        try:
            await self._run_blocking(self._rename_files, name, new_name)
        except BaseException:
            # Nothing was renamed: re-attach under the old name
            with self._lock:
                if self._entries.get(new_name) is target:
                    del self._entries[new_name]
                entry.state = "ready"
            await self._run_blocking(
                self._registry_write,
                [(SQL_CANCEL_RENAME, [name])],
            )
            self._attach(entry)
            raise
        await self._delete_catalog(name)
        with self._lock:
            if self._entries.get(name) is entry:
                del self._entries[name]
            target.state = "ready"
            self._entries[new_name] = target
        db = self._attach(target)
        await db._after_write()
        return db

    def _rename_files(self, name, new_name):
        old, new = self._path(name), self._path(new_name)
        # Datasette's last connection to close is often a read-only pooled
        # one, which cannot checkpoint: fold the -wal back into the main
        # file so the rename moves a single, complete file
        conn = sqlite3.connect(old)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        finally:
            conn.close()
        for suffix in ("-wal", "-journal"):
            try:
                if os.path.getsize(old + suffix):
                    # After a clean close there is none: another process has
                    # this database open, or it needs crash recovery
                    raise ScratchDatabaseError(
                        f"Cannot rename scratch database {name!r}: {old + suffix} "
                        "is not empty (is another process using it?)"
                    )
            except FileNotFoundError:
                pass
        self._unlink_files(name, main=False)
        self._unlink_files(new_name, main=False)
        if name.lower() == new_name.lower() and name != new_name:
            # Case-only change: link() would fail on case-insensitive file
            # systems
            os.rename(old, new)
        else:
            try:
                # link + unlink never replaces an existing file (rename does)
                os.link(old, new)
            except FileExistsError:
                raise ScratchDatabaseExists(f"{new} already exists")
            except OSError:
                # No hard links on this file system
                if os.path.exists(new):
                    raise ScratchDatabaseExists(f"{new} already exists")
                os.rename(old, new)
            else:
                os.unlink(old)
        _fsync_dir(self.directory)
        self._registry_write([(SQL_FINISH_RENAME, [new_name, name])])

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------
    async def list(self):
        with self._lock:
            entries = sorted(
                (e for e in self._entries.values() if e.state == "ready"),
                key=lambda e: e.name,
            )
        if not entries:
            return []
        sizes = await self._run_blocking(self._sizes, [e.name for e in entries])
        return [
            ScratchDatabaseInfo(
                name=entry.name,
                path=self._path(entry.name),
                created=entry.created,
                last_used=entry.last_used,
                size=size,
                owner=entry.owner,
                metadata=dict(entry.metadata),
                attached=self._is_attached(entry),
            )
            for entry, size in zip(entries, sizes)
        ]

    def _sizes(self, names):
        sizes = []
        for name in names:
            size = 0
            for suffix in ("", "-wal"):
                try:
                    size += os.stat(self._path(name) + suffix).st_size
                except OSError:
                    pass
            sizes.append(size)
        return sizes

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------
    def close(self):
        """Called by Datasette.close() after every database has been closed
        and the executor shut down: records last_used, releases the
        directory lock, removes a temporary directory."""
        if self._closed:
            return
        self._closed = True
        with self._lock:
            entries = list(self._entries.values())
        for entry in entries:
            # Detached ones (name clash, remove_database()) are not closed
            # by Datasette.close()
            if entry.db is not None and not entry.db._closed:
                try:
                    entry.db.close()
                except Exception:
                    logger.exception("Closing scratch database %r failed", entry.name)
        if self.directory is not None and self.persistent:
            try:
                self.flush()
            except Exception:
                logger.exception("Could not record scratch database last_used times")
        for finalizer in self._finalizers:
            finalizer()
