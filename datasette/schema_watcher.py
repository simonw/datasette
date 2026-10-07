"""
Keeps the ``_internal`` catalog (``catalog_databases``, ``catalog_tables``,
``catalog_columns`` ...) current without checking every attached database on
the request path.

Every attached database has a *schema watch mode*:

``owned``
    Every schema change goes through Datasette. After each write task the
    write thread reads ``PRAGMA schema_version`` on the connection it just
    used (microseconds, same thread). If the value moved, the catalog for
    that one database is rebuilt before the write call returns, so callers
    get read-your-writes on the catalog. Owned databases are never polled.

``external``
    The file can also be changed by other processes (sqlite-utils, cron
    jobs...). On top of the write-path check, a background task stats the
    database file, its ``-wal`` and its ``-journal`` every
    ``schema_watch_interval_ms``. Only a database whose fingerprint
    ``(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)`` changed - or
    whose previous fingerprint was *racily clean* - gets a
    ``PRAGMA schema_version`` check on a short-lived connection, and only a
    changed ``schema_version`` (or a replaced or deleted file) triggers a
    catalog rebuild for that database.

``immutable``
    Scanned once at startup and never again.

Defaults: database files and named in-memory databases use the
``default_schema_watch`` setting (``external`` unless set to ``owned``),
whether they were passed to ``Datasette(files=...)`` / the CLI or added
later with ``datasette.add_database()``. Immutable files are ``immutable``
and private ``:memory:`` databases ``owned``. Override per database with
``databases: {name: {schema_watch: owned|external|immutable}}`` in
``datasette.yaml`` or ``add_database(..., schema_watch=...)``. A named
in-memory database in ``external`` mode is polled with ``PRAGMA
schema_version`` on its write connection (shared-cache table locks do not
wait, so reading it from another connection could fail Datasette's writes).

Several event loops
-------------------
One Datasette can be driven by several event loops at once, each in its own
thread. Scans in flight are tracked with ``concurrent.futures`` futures that
any loop can wait for; a scan is only taken over when the loop running it
has closed or stopped. There is at most one live polling task. State that a
write thread marks (``needs_scan``, ``_pending``) is set under a lock, before
the write's result is delivered, so nothing depends on a notification
reaching a loop that may have gone away.

Replaced and deleted files
--------------------------
A sweep that sees a new inode or a missing file calls
``Database._invalidate_connections()``: the database's connection generation
is bumped, idle pooled read connections are closed, leased ones are
discarded when their callback returns, and the write thread reopens its
connection before the next write. Once Datasette has seen a file exist,
write connections open it with ``mode=rw``, so a deleted file is never
silently recreated.

Racily clean fingerprints
-------------------------
Like git's index: a fingerprint whose newest timestamp is within
``RACY_WINDOW_NS`` of the moment it was taken could be followed by another
write inside the same timestamp tick that leaves size, inode and mtime
identical. Such a fingerprint is flagged ``racy`` and the next sweep runs
the ``PRAGMA schema_version`` check even though the fingerprint looks
unchanged. The stat is always taken *before* the pragma, so a write landing
between the two either shows up in the pragma or changes the next stat.

All catalog reads use one short-lived connection per database inside a
single read transaction, so ``schema_version`` and the rows written to the
catalog describe the same snapshot. The watcher never keeps a connection
open.
"""

import asyncio
import errno
import json
import logging
import os
import threading
import time

from .utils import sqlite3
from .utils.catalog import remember_derived_table_dependencies
from .utils.inflight import InFlight, wait_for_concurrent
from .utils.internal_db import CATALOG_TABLES, collect_schema, write_catalog_entries

logger = logging.getLogger("datasette.schema_watcher")

MODES = ("owned", "external", "immutable")
# Valid values for the default_schema_watch setting
DEFAULT_MODES = ("external", "owned")
# Passed by Datasette.__init__ for files=/CLI databases: resolved to the
# default_schema_watch setting once settings are available
FILES_DEFAULT = "_files_default"
# Timestamps closer than this to the time of the stat are treated as racy.
# 2s covers coarse filesystems (FAT: 2s, HFS+: 1s) and clock skew on
# network filesystems; ext4/xfs/tmpfs on Linux are far finer than this.
RACY_WINDOW_NS = 2_000_000_000
# Databases per thread hop / per internal-DB transaction for bulk scans
SCAN_CHUNK = 64
SCAN_CONCURRENCY = 1
# Directories with at least this many watched files are listed once per
# sweep with os.scandir() instead of probing -wal/-journal with stat()
SCANDIR_THRESHOLD = 8
PRAGMA_BUSY_TIMEOUT_MS = 200
# Read the schema cookie from the file header instead of opening a
# connection when no -wal/-journal has content
USE_HEADER_CHECK = True
# Seconds before a database whose catalog scan or catalog write failed for a
# transient reason (internal database locked, cancelled) is scanned again by
# a request or sweep; doubled after each consecutive failure up to the max
RETRY_DELAY_S = 1.0
RETRY_DELAY_MAX_S = 30.0


class WatchState:
    __slots__ = (
        "catalog_version",
        "db",
        "error",
        "fp",
        "fp_racy",
        "missing",
        "mode",
        "name",
        "needs_scan",
        "notified_version",
        "removed",
        "requested_mode",
        "retry_at",
        "retry_delay",
        "scan_future",
        "stats",
    )

    def __init__(self, db, name, requested_mode):
        self.db = db
        self.name = name
        self.requested_mode = requested_mode
        self.mode = None
        self.fp = None
        self.fp_racy = True
        self.catalog_version = None
        self.notified_version = None
        self.needs_scan = True
        # An InFlight (datasette.utils.inflight) while a catalog scan of
        # this database runs
        self.scan_future = None
        # time.monotonic() before which a failed scan is not retried, and
        # the delay used for the next failure (doubles, see RETRY_DELAY_S)
        self.retry_at = 0.0
        self.retry_delay = RETRY_DELAY_S
        self.missing = False
        self.removed = False
        self.error = None
        self.stats = {"checks": 0, "scans": 0, "write_detections": 0}

    @property
    def is_file(self):
        db = self.db
        return bool(db.path) and not db.is_memory

    def __repr__(self):
        return f"<WatchState {self.name!r} mode={self.mode} version={self.catalog_version}>"


def _fp_main(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _fp_side(st):
    # No ctime for -wal/-journal: when running as root SQLite fchown()s
    # these files to match the database every time a connection opens
    # them, so their ctime moves on every open - including our own checks
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def fingerprint(path, present=None):
    """(main, wal, journal) stat tuples; None for a file that does not exist.

    ``present`` is an optional set of file names in the directory (from
    scandir) used to skip stat() calls for -wal/-journal files that are not
    there.
    """
    path = os.fspath(path)
    try:
        main = _fp_main(os.stat(path))
    except FileNotFoundError:
        main = None
    parts = [main]
    base = os.path.basename(path) if present is not None else None
    for suffix in ("-wal", "-journal"):
        if present is not None and (base + suffix) not in present:
            parts.append(None)
            continue
        try:
            st = os.stat(path + suffix)
        except FileNotFoundError:
            parts.append(None)
            continue
        # An empty -wal holds no frames (and an empty -journal nothing to
        # roll back): the database is exactly the main file, same as when
        # the file is absent. Read-only opens of a WAL database create an
        # empty -wal, which must not look like a change.
        parts.append(_fp_side(st) if st.st_size else None)
    return tuple(parts)


def newest_timestamp(fp):
    main = fp[0]
    newest = max(main[3], main[4]) if main is not None else 0
    for part in fp[1:]:
        if part is not None:
            newest = max(newest, part[2])
    return newest


def is_racy(fp, t_ns):
    return newest_timestamp(fp) >= t_ns - RACY_WINDOW_NS


SQLITE_HEADER = b"SQLite format 3\x00"


def header_schema_version(path):
    """The schema cookie (offset 40 of the database header) read straight
    from the file - what PRAGMA schema_version returns - without opening a
    SQLite connection or taking any lock.

    Only meaningful when there is no non-empty -wal or -journal: then the
    main file is the whole database. The caller must have taken the
    fingerprint *before* calling this; a commit in flight at that moment has
    already bumped the mtime it saw, so the fingerprint is racy and the next
    sweep looks again.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        header = os.pread(fd, 100, 0)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(header) < 100 or not header.startswith(SQLITE_HEADER):
        return None
    return int.from_bytes(header[40:44], "big")


def _is_transient(error):
    if isinstance(error, OSError):
        return error.errno in (errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EAGAIN)
    if isinstance(error, sqlite3.OperationalError):
        code = getattr(error, "sqlite_errorcode", 0) & 0xFF
        if code in (sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_NOMEM):
            return True
        message = str(error).lower()
        return any(
            text in message
            for text in (
                "locked",
                "busy",
                "unable to open database file",
                "too many open files",
            )
        )
    return False


def _fp_to_json(fp, t_ns, closed=False):
    data = {"fp": fp, "t": t_ns}
    if closed:
        # Taken by Datasette.close() after every connection to an owned
        # database had been closed: nothing in this process can change the
        # file after the stat, so the racily-clean rule does not apply
        data["closed"] = True
    return json.dumps(data)


def _fp_from_json(value):
    """(fp, t_ns, closed) - (None, None, False) if unreadable."""
    try:
        data = json.loads(value)
        fp = tuple(tuple(p) if p is not None else None for p in data["fp"])
        return fp, int(data["t"]), bool(data.get("closed"))
    except Exception:  # noqa: BLE001
        return None, None, False


def store_closing_fingerprints(internal_path, rows):
    """Write SchemaWatcher.closing_fingerprints() to a persistent internal
    database that has already been closed (Datasette.close() is synchronous
    and the internal database's write thread is gone by then). Only rows
    whose stored schema_version still matches are updated."""
    conn = sqlite3.connect(internal_path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "UPDATE catalog_databases SET fingerprint = ? "
            "WHERE database_name = ? AND schema_version = ?",
            [(fp_json, name, version) for name, version, fp_json in rows],
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


class SchemaWatcher:
    def __init__(self, ds):
        self.ds = ds
        self.states = {}
        # States whose catalog must be (re)built: registered after startup,
        # changed by a write whose notification may never run, or whose last
        # scan failed for a transient reason. Flushed by the next request or
        # sweep.
        self._pending = set()
        self._pending_removals = set()  # names whose catalog rows must go
        # Guards claiming scans (WatchState.needs_scan / scan_future) and
        # _pending: refresh() runs on several event loops in different
        # threads, and write threads mark states from their own thread
        self._lock = threading.Lock()
        # The current polling task. At most one is live: a poller whose loop
        # has closed or stopped is replaced, and a replaced poller exits at
        # its next wake-up (it is no longer self._task)
        self._task = None
        self._pollers = set()
        self._spawned = set()
        self._last_sweep = 0.0
        # Bumped after every write to the catalog tables, so readers can
        # cache things derived from the catalog (see datasette.utils.catalog).
        # Locked: several event loops (threads) can store scans at once, and
        # a lost increment would leave a stale cache in place
        self.catalog_generation = 0
        self._generation_lock = threading.Lock()
        self.counters = {
            "sweeps": 0,
            "sweep_seconds": 0.0,
            "stats": 0,
            "pragma_checks": 0,
            "header_checks": 0,
            "scans": 0,
            "write_detections": 0,
            "replaced": 0,
            "missing": 0,
            "busy": 0,
            "restored_from_persisted": 0,
        }

    def _bump_catalog_generation(self):
        with self._generation_lock:
            self.catalog_generation += 1

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------
    @property
    def interval_s(self):
        ms = self.ds.setting("schema_watch_interval_ms")
        return (ms or 0) / 1000.0

    def _config_mode(self, name):
        config = getattr(self.ds, "config", None) or {}
        db_config = (config.get("databases") or {}).get(name) or {}
        return db_config.get("schema_watch")

    def _default_mode_for_files(self):
        from .utils import StartupError

        mode = self.ds.setting("default_schema_watch") or "external"
        if mode not in DEFAULT_MODES:
            raise StartupError(
                f"Invalid default_schema_watch setting {mode!r}, expected one of "
                f"{', '.join(DEFAULT_MODES)}"
            )
        return mode

    def _resolve_mode(self, state):
        db = state.db
        mode = self._config_mode(state.name) or state.requested_mode
        if mode is None:
            if state.is_file or db.memory_name:
                # Other code may change it: a plugin's own connection, a
                # backup() into a named in-memory database, another process
                mode = self._default_mode_for_files()
            else:
                # A private ":memory:" database: nothing else can see it
                mode = "owned"
        elif mode == FILES_DEFAULT:
            mode = self._default_mode_for_files()
        if mode not in MODES:
            from .utils import StartupError

            raise StartupError(
                f"Invalid schema_watch mode {mode!r} for database {state.name!r}, "
                f"expected one of {', '.join(MODES)}"
            )
        if not db.is_mutable:
            # Nothing can change an immutable database
            mode = "immutable"
        elif mode == "external" and not state.is_file and not db.memory_name:
            # A private ":memory:" database: there is nothing to poll.
            # Named in-memory databases are polled with PRAGMA
            # schema_version on their write connection.
            mode = "owned"
        return mode

    def configure(self):
        """Resolve modes once ds.config is available (end of __init__)."""
        # Validate the setting even when no database uses it
        self._default_mode_for_files()
        for state in self.states.values():
            state.mode = self._resolve_mode(state)

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def register(self, db, requested_mode=None):
        if requested_mode not in (None, FILES_DEFAULT) and requested_mode not in MODES:
            raise ValueError(
                f"schema_watch must be one of {', '.join(MODES)}, not {requested_mode!r}"
            )
        state = WatchState(db, db.name, requested_mode)
        if hasattr(self.ds, "config"):
            state.mode = self._resolve_mode(state)
        old = self.states.get(db.name)
        if old is not None:
            old.removed = True
        self.states[db.name] = state
        db._watch_state = state
        self._pending_removals.discard(db.name)
        if self.ds.internal_db_created:
            with self._lock:
                self._pending.add(state)
            if not db.memory_name:
                self._spawn(self.refresh([state]))
            # A shared-cache in-memory database is scanned by the next
            # request or sweep instead: plugins typically fill one right
            # after adding it (VACUUM INTO, backup()) from their own
            # connection, and shared-cache table locks do not wait - a scan
            # reading sqlite_master at that moment makes their writes fail
            # with "database table is locked"
        return state

    def unregister(self, name):
        state = self.states.pop(name, None)
        if state is None:
            return
        state.removed = True
        state.db._watch_state = None
        with self._lock:
            self._pending.discard(state)
        if self.ds.internal_db_created:
            self._pending_removals.add(name)
            self._spawn(self._delete_catalog([name]))

    # ------------------------------------------------------------------
    # task helpers
    # ------------------------------------------------------------------
    async def _off_loop(self, fn, *args):
        """Run blocking stat()/scan work in a worker thread - or inline with
        num_sql_threads=0, which must work where threads cannot be started
        at all (Pyodide)."""
        if self.ds.executor is None:
            return fn(*args)
        return await asyncio.to_thread(fn, *args)

    def _spawn(self, coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop (sync caller): the work stays pending and is picked
            # up by the next flush/sweep
            coro.close()
            return None
        task = loop.create_task(coro)
        self._spawned.add(task)

        def _done(t):
            self._spawned.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.error("schema watcher task failed", exc_info=t.exception())

        task.add_done_callback(_done)
        return task

    def _poller_alive(self):
        task = self._task
        if task is None or task.done():
            return False
        loop = task.get_loop()
        return not loop.is_closed() and loop.is_running()

    def loop_running(self):
        """True while a polling task is alive, on any event loop."""
        return self._poller_alive()

    async def start(self):
        """Start the polling loop unless one is already alive on any event
        loop. Runs one sweep first so the catalog is current when the first
        request is served."""
        if self.interval_s <= 0 or self.ds._closed:
            return
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._poller_alive():
                return
            # Claim the slot before awaiting so concurrent start() calls,
            # on this loop or another, no-op
            task = loop.create_task(self._run(), name="datasette-schema-watcher")
            self._task = task
            self._pollers.add(task)
        task.add_done_callback(self._poller_done)
        if self.ds.internal_db_created:
            try:
                await self.ds._refresh_schemas(background=True)
            except Exception:
                logger.exception("schema watcher initial sweep failed")

    def _poller_done(self, task):
        with self._lock:
            self._pollers.discard(task)

    async def _run(self):
        me = asyncio.current_task()
        # Wait one interval first: start() has just swept. A poller that
        # has been replaced (its loop stopped for a while and another loop
        # started a new one) exits instead of sweeping alongside it.
        while not self.ds._closed and self._task is me:
            await asyncio.sleep(self.interval_s)
            if self.ds._closed or self._task is not me:
                return
            try:
                await self.ds._refresh_schemas(background=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("schema watcher sweep failed")

    def _all_tasks(self):
        with self._lock:
            return [t for t in [self._task, *self._pollers, *self._spawned] if t]

    def stop(self):
        tasks = self._all_tasks()
        self._task = None
        for task in tasks:
            if task.done():
                continue
            loop = task.get_loop()
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                task.cancel()
            elif not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass

    async def astop(self):
        tasks = self._all_tasks()
        self.stop()
        loop = asyncio.get_running_loop()
        mine = [t for t in tasks if t.get_loop() is loop and not t.done()]
        if mine:
            await asyncio.wait(mine, timeout=5)

    # ------------------------------------------------------------------
    # request path: O(1) unless add/remove_database or a write left work
    # pending
    # ------------------------------------------------------------------
    async def on_request(self):
        if (self._pending or self._pending_removals) and self.ds.internal_db_created:
            try:
                await self.flush_pending()
            except Exception:
                # Never fail the request: what is still pending is retried
                # (with back-off) by later requests and sweeps
                logger.warning("Could not update the catalog", exc_info=True)
        if (
            self.interval_s > 0
            and self.ds.internal_db_created
            and not self._poller_alive()
        ):
            await self.start()

    def _ready_pending(self):
        now = time.monotonic()
        with self._lock:
            return [s for s in self._pending if not s.removed and s.retry_at <= now]

    async def flush_pending(self):
        if self._pending_removals:
            await self._delete_catalog(list(self._pending_removals))
        ready = self._ready_pending()
        if ready:
            await self.refresh(ready)

    # ------------------------------------------------------------------
    # write path
    # ------------------------------------------------------------------
    def check_after_write(self, state, conn, loop=None):
        """Called on the thread that owns ``conn`` right after a write task.

        ``loop`` is the event loop to notify (None when already on it)."""
        try:
            version = conn.execute("PRAGMA schema_version").fetchone()[0]
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            if (
                state.removed
                or version == state.catalog_version
                or version == state.notified_version
            ):
                return
            state.notified_version = version
            # Marked here, on the write thread, before the write's result is
            # delivered: a blocking writer always finds needs_scan set in
            # Database._after_write(). The notification below only starts a
            # scan sooner; if it never runs (a block=False write from an
            # event loop that has since closed or stopped) the next request
            # or sweep picks the state up from _pending.
            state.needs_scan = True
            state.retry_at = 0.0
            self._pending.add(state)
            state.stats["write_detections"] += 1
            self.counters["write_detections"] += 1
        if loop is None:
            self._on_write_schema_change(state)
        else:
            try:
                loop.call_soon_threadsafe(self._on_write_schema_change, state)
            except RuntimeError:
                pass

    def _on_write_schema_change(self, state):
        if state.removed:
            return
        if self.ds.internal_db_created:
            self._spawn(self.refresh([state]))

    async def after_write(self, state):
        """Awaited by blocking writes: gives read-your-writes on the catalog."""
        if not self.ds.internal_db_created or state.removed:
            return
        try:
            await self.refresh([state])
        except Exception:
            logger.exception("catalog refresh after write to %r failed", state.name)

    # ------------------------------------------------------------------
    # sweeps (stat prefilter)
    # ------------------------------------------------------------------
    async def sweep(self, *, background):
        """Check databases for changes made outside Datasette.

        background=True (the polling loop): external-mode files only.
        background=False (explicit refresh_schemas()): every mutable
        database - files via the stat prefilter, named in-memory databases
        via a PRAGMA schema_version check.
        """
        t0 = time.perf_counter()
        if self._pending_removals:
            await self._delete_catalog(list(self._pending_removals))
        candidates = []
        for state in list(self.states.values()):
            if state.mode == "immutable" or state.mode is None:
                if state.needs_scan:
                    candidates.append(state)
                continue
            if background and state.mode != "external":
                continue
            candidates.append(state)
        outcomes = []
        file_candidates = [s for s in candidates if s.is_file]
        if file_candidates:
            outcomes = await self._off_loop(
                self._sweep_sync, file_candidates, background
            )
        for state in candidates:
            if not state.is_file and state.db.memory_name and not state.removed:
                outcomes.append(await self._memory_pragma_check(state))
        to_scan = self._ready_pending()
        to_clear = []
        for state, kind, fp, t_ns, version in outcomes:
            if state.removed:
                continue
            self.counters["stats"] += 1
            if fp is not None:
                state.fp = fp
                state.fp_racy = is_racy(fp, t_ns)
                if fp[0] is not None:
                    state.db._file_seen = True
            if kind == "clean":
                pass
            elif kind == "failed_unchanged":
                continue
            elif kind == "missing":
                self.counters["missing"] += 1
                if not state.missing:
                    state.missing = True
                    state.db._invalidate_connections()
                    to_clear.append(state)
            elif kind == "replaced":
                self.counters["replaced"] += 1
                state.missing = False
                state.db._invalidate_connections()
                state.needs_scan = True
            elif kind == "busy":
                self.counters["busy"] += 1
                state.fp_racy = True  # check again next sweep
            elif kind in ("checked", "header"):
                self.counters[
                    "pragma_checks" if kind == "checked" else "header_checks"
                ] += 1
                state.stats["checks"] += 1
                if version != state.catalog_version:
                    state.needs_scan = True
            elif kind == "error":
                state.fp_racy = True
            if state.needs_scan:
                to_scan.append(state)
        if to_clear:
            await self._clear_missing(to_clear)
        if to_scan:
            await self.refresh(to_scan)
        self.counters["sweeps"] += 1
        self.counters["sweep_seconds"] += time.perf_counter() - t0
        self._last_sweep = time.monotonic()

    def _sweep_sync(self, states, background=False):
        """Runs in a worker thread. Reads state, never writes it."""
        by_dir = {}
        for state in states:
            if state.is_file:
                by_dir.setdefault(
                    os.path.dirname(os.path.abspath(state.db.path)), []
                ).append(state)
        out = []
        for directory, dir_states in by_dir.items():
            present = None
            if len(dir_states) >= SCANDIR_THRESHOLD:
                try:
                    with os.scandir(directory) as it:
                        present = {entry.name for entry in it}
                except OSError:
                    present = None
            for state in dir_states:
                t_ns = time.time_ns()
                fp = fingerprint(state.db.path, present)
                prev = state.fp
                if fp == prev and not state.fp_racy and not state.needs_scan:
                    out.append((state, "clean", fp, t_ns, None))
                    continue
                if (
                    background
                    and state.error is not None
                    and fp == prev
                    # Not state.fp_racy: failed checks set that flag to
                    # force a recheck, which would retry forever
                    and not is_racy(fp, t_ns)
                ):
                    # The last scan failed (missing extension module, not
                    # a database, ...) and the file has not changed since:
                    # retrying would fail again. An explicit
                    # refresh_schemas() still retries.
                    out.append((state, "failed_unchanged", fp, t_ns, None))
                    continue
                if fp[0] is None:
                    out.append((state, "missing", fp, t_ns, None))
                    continue
                if prev is None or prev[0] is None or prev[0][:2] != fp[0][:2]:
                    # New inode/device (atomic replace, delete+recreate) or
                    # first sight: the schema_version alone cannot be trusted
                    out.append((state, "replaced", fp, t_ns, None))
                    continue
                if USE_HEADER_CHECK and fp[1] is None and fp[2] is None:
                    # No -wal/-journal content: read the cookie from the
                    # header. No connection, no SHARED lock that could make
                    # a concurrent writer back off.
                    version = header_schema_version(os.fspath(state.db.path))
                    if version is not None:
                        out.append((state, "header", fp, t_ns, version))
                        continue
                out.append(self._pragma_check(state, fp, t_ns))
        return out

    async def _memory_pragma_check(self, state):
        def _version(conn):
            return conn.execute("PRAGMA schema_version").fetchone()[0]

        try:
            version = await state.db._execute_on_write_connection(_version)
        except Exception:  # noqa: BLE001
            return (state, "error", None, None, None)
        return (state, "checked", None, None, version)

    def _pragma_check(self, state, fp, t_ns):
        db = state.db
        try:
            conn = self._connect(db, prepare=False)
        except Exception:  # noqa: BLE001
            return (state, "error", fp, t_ns, None)
        try:
            conn.execute(f"PRAGMA busy_timeout={PRAGMA_BUSY_TIMEOUT_MS}")
            version = conn.execute("PRAGMA schema_version").fetchone()[0]
            return (state, "checked", fp, t_ns, version)
        except sqlite3.OperationalError as e:
            msg = str(e)
            if "locked" in msg or "busy" in msg:
                return (state, "busy", fp, t_ns, None)
            return (state, "error", fp, t_ns, None)
        except Exception:  # noqa: BLE001
            return (state, "error", fp, t_ns, None)
        finally:
            self._close(db, conn)

    # ------------------------------------------------------------------
    # scanning + storing
    # ------------------------------------------------------------------
    def _connect(self, db, prepare):
        # Untracked: Database.close() on the event loop thread must not close
        # this connection while a worker thread is using it (that segfaults).
        # It is always closed by _close() in the same thread that opened it.
        # Counted separately so database lifecycle operations can wait for
        # scans without closing a connection while its thread is using it.
        db._untracked_connection_opened()
        try:
            conn = db.connect(track=False)
        except BaseException:
            db._untracked_connection_closed()
            raise
        try:
            if prepare:
                # crossdb=False: a scan of _memory reads its own schema only,
                # it must not ATTACH (open) up to ten other database files
                self.ds._prepare_connection(conn, db.name, crossdb=False)
            else:
                conn.row_factory = None
        except BaseException:
            self._close(db, conn)
            raise
        return conn

    @staticmethod
    def _close(db, conn):
        try:
            conn.close()
        finally:
            db._untracked_connection_closed()

    def _scan_sync(self, state):
        """Read schema_version + full schema in one read transaction."""
        db = state.db
        fp = t_ns = None
        if state.is_file:
            t_ns = time.time_ns()
            fp = fingerprint(db.path)
            if fp[0] is None:
                return {"state": state, "missing": True, "fp": fp, "t": t_ns}
        try:
            conn = self._connect(db, prepare=True)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN")
                try:
                    version = conn.execute("PRAGMA schema_version").fetchone()[0]
                    schema = collect_schema(conn, db.name)
                finally:
                    conn.rollback()
            finally:
                self._close(db, conn)
        except Exception as e:  # noqa: BLE001
            # Keep the fingerprint: a background sweep retries a failed scan
            # only once the file changes, instead of every interval
            return {"state": state, "error": e, "fp": fp, "t": t_ns}
        return {
            "state": state,
            "fp": fp,
            "t": t_ns,
            "version": version,
            "schema": schema,
        }

    async def _scan_on_write_connection(self, state):
        name = state.db.name

        def _scan(conn):
            version = conn.execute("PRAGMA schema_version").fetchone()[0]
            # The write thread's post-task schema check must not re-notify
            # for the version this scan is about to store
            state.notified_version = version
            return version, collect_schema(conn, name)

        try:
            version, schema = await state.db._execute_on_write_connection(_scan)
        except Exception as e:  # noqa: BLE001
            return {"state": state, "error": e}
        return {
            "state": state,
            "fp": None,
            "t": None,
            "version": version,
            "schema": schema,
        }

    def _scan_chunk_sync(self, states):
        results = []
        for state in states:
            try:
                results.append(self._scan_sync(state))
            except Exception as e:  # noqa: BLE001
                results.append({"state": state, "error": e})
        return results

    async def refresh(self, states):
        """Rebuild the catalog for every state that needs it, coalescing with
        scans already in flight for the same database - on this event loop
        or any other."""
        loop = asyncio.get_running_loop()
        pending = list(states)
        # States whose scan this call ran and that failed transiently: left
        # in _pending for a later request or sweep, not retried here
        failed = set()
        for _ in range(10):
            mine = []
            waits = []
            with self._lock:
                for state in pending:
                    inflight = state.scan_future
                    if inflight is not None and not inflight.done():
                        if not inflight.abandoned():
                            waits.append(inflight.future)
                            continue
                        # Its event loop has closed or stopped, so nothing
                        # will finish that scan. Take over: its results are
                        # discarded (_store() checks scan_future) and its
                        # waiters are woken to wait for this one instead
                        state.scan_future = None
                        state.needs_scan = True
                        inflight.finish()
                    if state.needs_scan and not state.removed and state not in failed:
                        state.needs_scan = False
                        state.scan_future = InFlight(loop)
                        mine.append(state)
            if mine:
                records = {state: state.scan_future for state in mine}
                handled = set()
                try:
                    await self._scan_and_store(mine, records, handled)
                finally:
                    now = time.monotonic()
                    with self._lock:
                        for state, record in records.items():
                            if state.scan_future is not record:
                                # Taken over by another loop (see above)
                                continue
                            state.scan_future = None
                            if state not in handled and not state.removed:
                                # The scan or the catalog write failed
                                # transiently (internal database locked, a
                                # shared-cache lock) or was cancelled: the
                                # catalog is not current, so make sure a
                                # later request or sweep tries again
                                state.needs_scan = True
                                state.retry_at = now + state.retry_delay
                                state.retry_delay = min(
                                    state.retry_delay * 2, RETRY_DELAY_MAX_S
                                )
                                self._pending.add(state)
                                failed.add(state)
                    for record in records.values():
                        record.finish()
            if waits:
                await asyncio.wait([wait_for_concurrent(f) for f in waits])
            pending = [
                s
                for s in pending
                if not s.removed
                and s not in failed
                and (
                    s.needs_scan
                    or (s.scan_future is not None and not s.scan_future.done())
                )
                and s.error is None
            ]
            if not pending:
                break
        with self._lock:
            for state in states:
                # Failed scans are retried by sweeps, not by every request
                if not state.needs_scan or state.error is not None:
                    self._pending.discard(state)

    async def wait_for_scan(self, state):
        """Wait for a catalog scan of state that is in flight, if any."""
        inflight = state.scan_future
        if inflight is not None and not inflight.done() and not inflight.abandoned():
            await wait_for_concurrent(inflight.future)

    async def _scan_and_store(self, states, records, handled):
        memory = [s for s in states if not s.is_file and s.db.memory_name]
        if memory:
            results = []
            for state in memory:
                results.append(await self._scan_on_write_connection(state))
            await self._store(results, records, handled)
            states = [s for s in states if s not in memory]
            if not states:
                return
        chunks = [states[i : i + SCAN_CHUNK] for i in range(0, len(states), SCAN_CHUNK)]
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def one(chunk):
            async with semaphore:
                results = await self._off_loop(self._scan_chunk_sync, chunk)
            await self._store(results, records, handled)

        if len(chunks) == 1:
            await one(chunks[0])
        else:
            await asyncio.gather(*(one(c) for c in chunks))

    async def _store(self, results, records=None, handled=None):
        """Write scan results to the catalog. ``records`` maps each state to
        the InFlight this scan was started as: results for a state whose
        scan has since been taken over by another loop are dropped. States
        whose outcome was recorded are added to ``handled``; the others
        (transient failures) are retried later by refresh()."""
        records = records or {}
        if handled is None:
            handled = set()

        def current(state):
            record = records.get(state)
            return record is None or state.scan_future is record

        entries = []
        missing = []
        stored = []
        for r in results:
            state = r["state"]
            if state.removed or not current(state):
                handled.add(state)
                continue
            if "error" in r:
                if state.db._closed:
                    # Closed (or deleted) while the scan was running
                    handled.add(state)
                    continue
                if _is_transient(r["error"]):
                    state.error = None
                    # "database is locked" / "database table is locked" (a
                    # shared-cache memory database being filled from another
                    # connection): not a property of the database, retry
                    logger.info(
                        "Transient error reading schema of database %r, will retry: %s",
                        state.name,
                        r["error"],
                    )
                    continue
                state.error = r["error"]
                state.needs_scan = True
                if r.get("fp") is not None and r["fp"][0] is not None:
                    state.fp = r["fp"]
                    state.fp_racy = is_racy(r["fp"], r["t"])
                logger.warning(
                    "Could not read schema of database %r: %s", state.name, r["error"]
                )
                handled.add(state)
                continue
            state.error = None
            if r.get("missing"):
                missing.append(state)
                state.fp = r["fp"]
                state.fp_racy = True
                handled.add(state)
                continue
            db = state.db
            fp_json = _fp_to_json(r["fp"], r["t"]) if r["fp"] is not None else None
            entries.append(
                (
                    state,
                    (
                        state.name,
                        str(db.path) if db.path is not None else None,
                        db.is_memory,
                        r["version"],
                        fp_json,
                        r["schema"],
                    ),
                )
            )
            stored.append(r)
        if entries:
            watcher = self

            def _write(conn):
                # Runs on the internal database's write thread, which
                # serializes catalog writes: skip databases removed (or
                # re-added) while we were scanning, and scans another loop
                # has taken over - its newer rows must not be overwritten
                live = [
                    entry
                    for state, entry in entries
                    if watcher.states.get(entry[0]) is state and current(state)
                ]
                write_catalog_entries(conn, live)

            await self.ds.get_internal_database().execute_write_fn(_write)
            for r in stored:
                state = r["state"]
                handled.add(state)
                if state.removed or not current(state):
                    continue
                state.catalog_version = r["version"]
                state.missing = False
                state.retry_at = 0.0
                state.retry_delay = RETRY_DELAY_S
                if r["fp"] is not None:
                    state.fp = r["fp"]
                    state.fp_racy = is_racy(r["fp"], r["t"])
                    if r["fp"][0] is not None:
                        state.db._file_seen = True
                state.stats["scans"] += 1
                self.counters["scans"] += 1
                remember_derived_table_dependencies(
                    state, [(t[1], t[3]) for t in r["schema"]["tables"]]
                )
            # After the states: a reader that sees the new generation must
            # also see their new catalog versions
            self._bump_catalog_generation()
        if missing:
            for state in missing:
                if not state.missing:
                    state.missing = True
                    state.db._invalidate_connections()
            await self._clear_missing(missing)

    async def _clear_missing(self, states):
        names = [s.name for s in states]

        def _clear(conn):
            for name in names:
                for table in CATALOG_TABLES[:-1]:
                    conn.execute(f"DELETE FROM {table} WHERE database_name = ?", [name])
                conn.execute(
                    "UPDATE catalog_databases SET schema_version = NULL, fingerprint = NULL "
                    "WHERE database_name = ?",
                    [name],
                )

        await self.ds.get_internal_database().execute_write_fn(_clear)
        for state in states:
            state.catalog_version = None
        self._bump_catalog_generation()

    async def _delete_catalog(self, names):
        watcher = self

        def _delete(conn):
            for name in names:
                if name in watcher.states:
                    # Re-added under the same name; its own scan replaces rows
                    continue
                for table in CATALOG_TABLES:
                    conn.execute(f"DELETE FROM {table} WHERE database_name = ?", [name])

        await self.ds.get_internal_database().execute_write_fn(_delete)
        self._bump_catalog_generation()
        for name in names:
            self._pending_removals.discard(name)

    # ------------------------------------------------------------------
    # startup
    # ------------------------------------------------------------------
    async def initial_scan(self):
        """Full catalog build at startup.

        Deletes catalog rows for databases that are not attached (a
        persistent --internal database can carry rows from a previous run),
        then scans every database - except those whose persisted fingerprint
        proves the file is unchanged since the catalog was written.
        """
        internal = self.ds.get_internal_database()
        names = list(self.states.keys())
        names_json = json.dumps(names)

        def _delete_stale(conn):
            for table in CATALOG_TABLES:
                conn.execute(
                    f"DELETE FROM {table} WHERE database_name IS NULL OR database_name NOT IN "
                    "(SELECT value FROM json_each(?))",
                    [names_json],
                )

        await internal.execute_write_fn(_delete_stale)
        self._bump_catalog_generation()
        self._pending_removals.clear()
        states = [s for s in self.states.values() if not s.removed]
        persisted = {}
        if not internal.is_temp_disk:
            rows = await internal.execute(
                "select database_name, path, schema_version, fingerprint from catalog_databases"
            )
            persisted = {row["database_name"]: row for row in rows.rows}
        to_scan = []
        if persisted:
            reuse = await self._off_loop(self._reusable_sync, states, persisted)
            for state in states:
                hit = reuse.get(state.name)
                if hit is None:
                    to_scan.append(state)
                    continue
                fp, t_ns, version = hit
                state.db._file_seen = True
                state.fp = fp
                state.fp_racy = is_racy(fp, t_ns)
                state.catalog_version = version
                state.needs_scan = False
                self.counters["restored_from_persisted"] += 1
        else:
            to_scan = states
        for state in to_scan:
            state.needs_scan = True
        if to_scan:
            await self.refresh(to_scan)
        with self._lock:
            # Keep states that a write marked during the scan, or whose scan
            # failed transiently
            self._pending = {s for s in self._pending if s.needs_scan and not s.removed}

    def _reusable_sync(self, states, persisted):
        """Databases whose stored fingerprint matches the file right now and
        was not racy when it was stored. Stat calls only - no connections."""
        out = {}
        for state in states:
            row = persisted.get(state.name)
            if row is None or not state.is_file or row["fingerprint"] is None:
                continue
            if row["path"] != str(state.db.path) or row["schema_version"] is None:
                continue
            stored_fp, stored_t, closed = _fp_from_json(row["fingerprint"])
            if stored_fp is None or (is_racy(stored_fp, stored_t) and not closed):
                continue
            t_ns = time.time_ns()
            fp = fingerprint(state.db.path)
            if fp == stored_fp:
                out[state.name] = (fp, t_ns, row["schema_version"])
        return out

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------
    def closing_fingerprints(self):
        """Called by Datasette.close() once every attached database has been
        closed. For each owned file database whose catalog is current,
        returns ``(name, schema_version, fingerprint_json)`` for the file as
        it is now. Owned databases are only changed through Datasette, so a
        restart with a persistent internal database can reuse their catalog
        rows without opening them - even if data was written (or the -wal
        checkpointed by the final close) after the catalog was last built.
        Stat calls only."""
        out = []
        for state in list(self.states.values()):
            if (
                state.mode != "owned"
                or not state.is_file
                or state.removed
                or state.missing
                or state.needs_scan
                or state.scan_future is not None
                or state.error is not None
                or state.catalog_version is None
                or state.notified_version not in (None, state.catalog_version)
            ):
                continue
            t_ns = time.time_ns()
            try:
                fp = fingerprint(state.db.path)
            except OSError:
                continue
            if fp[0] is None or fp[1] is not None or fp[2] is not None:
                # Missing, or a -wal/-journal with content: the header
                # check below would not describe the whole database
                continue
            if state.fp is None or state.fp[0] is None or state.fp[0][:2] != fp[0][:2]:
                # Replaced since the catalog was built
                continue
            if header_schema_version(os.fspath(state.db.path)) != state.catalog_version:
                # Changed by something other than Datasette while it ran:
                # the catalog is stale and must not be restored next time
                continue
            out.append(
                (state.name, state.catalog_version, _fp_to_json(fp, t_ns, closed=True))
            )
        return out

    # ------------------------------------------------------------------
    # single-database freshness checks (permission decisions)
    # ------------------------------------------------------------------
    def catalog_is_current(self, state):
        """True if this database's catalog rows provably describe it as it
        is right now - checked without opening a SQLite connection.

        Used before trusting the catalog for a decision about one database
        (the derived-table permission rule), where a catalog that lags an
        out-of-band change could expose a table. Immutable databases cannot
        change. A file database is current if it is the same file the
        catalog was built from, has no -wal/-journal content, and the schema
        cookie in its header equals the catalog's version. Anything else
        (in-memory databases, live WAL content) returns False and the caller
        asks the database itself.
        """
        if state.mode == "immutable":
            return True
        if (
            not state.is_file
            or state.catalog_version is None
            or state.needs_scan
            or state.missing
        ):
            return False
        path = os.fspath(state.db.path)
        try:
            fp = fingerprint(path)
        except OSError:
            return False
        if fp[0] is None or fp[1] is not None or fp[2] is not None:
            return False
        prev = state.fp
        if prev is None or prev[0] is None or prev[0][:2] != fp[0][:2]:
            return False
        return header_schema_version(path) == state.catalog_version

    def note_live_version(self, state, version):
        """A caller read ``PRAGMA schema_version`` from the database itself.
        If the catalog is behind, schedule a rescan instead of waiting for
        the next write (owned) or poll (external)."""
        with self._lock:
            if (
                state.removed
                or state.error is not None
                or state.needs_scan
                or state.scan_future is not None
                or version == state.catalog_version
            ):
                return
            state.needs_scan = True
            self._pending.add(state)
        if self.ds.internal_db_created:
            self._spawn(self.refresh([state]))

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------
    def status(self):
        modes = {}
        for state in self.states.values():
            modes[state.mode] = modes.get(state.mode, 0) + 1
        return {
            "interval_s": self.interval_s,
            "loop_running": self.loop_running(),
            "modes": modes,
            "pending": len(self._pending),
            "pending_removals": len(self._pending_removals),
            **self.counters,
        }
