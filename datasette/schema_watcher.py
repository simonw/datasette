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

Defaults: files passed to ``Datasette(files=...)`` / the CLI are
``external``, immutable files are ``immutable``, anything added later with
``datasette.add_database()`` is ``owned``. Override per database with
``databases: {name: {schema_watch: owned|external|immutable}}`` in
``datasette.yaml`` or ``add_database(..., schema_watch=...)``.

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
import json
import logging
import os
import time

from .utils import sqlite3
from .utils.internal_db import CATALOG_TABLES, collect_schema, write_catalog_entries

logger = logging.getLogger("datasette.schema_watcher")

MODES = ("owned", "external", "immutable")
# Timestamps closer than this to the time of the stat are treated as racy.
# 2s covers coarse filesystems (FAT: 2s, HFS+: 1s) and clock skew on
# network filesystems; ext4/xfs/tmpfs on Linux are far finer than this.
RACY_WINDOW_NS = 2_000_000_000
# Databases per thread hop / per internal-DB transaction for bulk scans
SCAN_CHUNK = 64
SCAN_CONCURRENCY = 3
# Directories with at least this many watched files are listed once per
# sweep with os.scandir() instead of probing -wal/-journal with stat()
SCANDIR_THRESHOLD = 8
PRAGMA_BUSY_TIMEOUT_MS = 200


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
        self.scan_future = None
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
            parts.append(_fp_side(os.stat(path + suffix)))
        except FileNotFoundError:
            parts.append(None)
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


def _fp_to_json(fp, t_ns):
    return json.dumps({"fp": fp, "t": t_ns})


def _fp_from_json(value):
    try:
        data = json.loads(value)
        fp = tuple(tuple(p) if p is not None else None for p in data["fp"])
        return fp, int(data["t"])
    except Exception:  # noqa: BLE001
        return None, None


class SchemaWatcher:
    def __init__(self, ds):
        self.ds = ds
        self.states = {}
        self._pending = set()  # states registered after startup, not yet scanned
        self._pending_removals = set()  # names whose catalog rows must go
        self._task = None
        self._spawned = set()
        self._last_sweep = 0.0
        self.counters = {
            "sweeps": 0,
            "sweep_seconds": 0.0,
            "stats": 0,
            "pragma_checks": 0,
            "scans": 0,
            "write_detections": 0,
            "replaced": 0,
            "missing": 0,
            "busy": 0,
            "restored_from_persisted": 0,
        }

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

    def _resolve_mode(self, state):
        db = state.db
        mode = self._config_mode(state.name) or state.requested_mode
        if mode is None:
            mode = "owned"
        if mode not in MODES:
            from .utils import StartupError

            raise StartupError(
                f"Invalid schema_watch mode {mode!r} for database {state.name!r}, "
                f"expected one of {', '.join(MODES)}"
            )
        if not db.is_mutable:
            # Nothing can change an immutable database
            mode = "immutable"
        elif mode == "external" and not state.is_file:
            # There is no file to stat: rely on the write path
            mode = "owned"
        return mode

    def configure(self):
        """Resolve modes once ds.config is available (end of __init__)."""
        for state in self.states.values():
            state.mode = self._resolve_mode(state)

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def register(self, db, requested_mode=None):
        if requested_mode is not None and requested_mode not in MODES:
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
            self._pending.add(state)
            self._spawn(self.refresh([state]))
        return state

    def unregister(self, name):
        state = self.states.pop(name, None)
        if state is None:
            return
        state.removed = True
        state.db._watch_state = None
        self._pending.discard(state)
        if self.ds.internal_db_created:
            self._pending_removals.add(name)
            self._spawn(self._delete_catalog([name]))

    # ------------------------------------------------------------------
    # task helpers
    # ------------------------------------------------------------------
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

    def loop_running(self):
        task = self._task
        if task is None or task.done():
            return False
        try:
            return task.get_loop() is asyncio.get_running_loop()
        except RuntimeError:
            return False

    async def start(self):
        """Start the polling loop (idempotent). Runs one sweep first so the
        catalog is current when the first request is served."""
        if self.interval_s <= 0 or self.ds._closed or self.loop_running():
            return
        loop = asyncio.get_running_loop()
        # Claim the slot before awaiting so concurrent start() calls no-op
        self._task = loop.create_task(self._run(), name="datasette-schema-watcher")
        if self.ds.internal_db_created:
            try:
                await self.ds._refresh_schemas(background=True)
            except Exception:
                logger.exception("schema watcher initial sweep failed")

    async def _run(self):
        # Wait one interval first: start() has just swept
        while not self.ds._closed:
            await asyncio.sleep(self.interval_s)
            if self.ds._closed:
                return
            try:
                await self.ds._refresh_schemas(background=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("schema watcher sweep failed")

    def stop(self):
        tasks = [t for t in [self._task, *self._spawned] if t is not None]
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
        tasks = [t for t in [self._task, *self._spawned] if t is not None]
        self.stop()
        loop = asyncio.get_running_loop()
        mine = [t for t in tasks if t.get_loop() is loop and not t.done()]
        if mine:
            await asyncio.wait(mine, timeout=5)

    # ------------------------------------------------------------------
    # request path: O(1) unless add/remove_database left work pending
    # ------------------------------------------------------------------
    async def on_request(self):
        if (self._pending or self._pending_removals) and self.ds.internal_db_created:
            await self.flush_pending()
        if (
            not self.loop_running()
            and self.interval_s > 0
            and self.ds.internal_db_created
        ):
            await self.start()

    async def flush_pending(self):
        if self._pending_removals:
            await self._delete_catalog(list(self._pending_removals))
        if self._pending:
            await self.refresh(list(self._pending))

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
        if version == state.catalog_version or version == state.notified_version:
            return
        state.notified_version = version
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
        state.needs_scan = True
        state.stats["write_detections"] += 1
        self.counters["write_detections"] += 1
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
            outcomes = await asyncio.to_thread(self._sweep_sync, file_candidates)
        for state in candidates:
            if not state.is_file and state.db.memory_name and not state.removed:
                outcomes.append(await self._memory_pragma_check(state))
        to_scan = [s for s in self._pending if not s.removed]
        to_clear = []
        for state, kind, fp, t_ns, version in outcomes:
            if state.removed:
                continue
            self.counters["stats"] += 1
            if fp is not None:
                state.fp = fp
                state.fp_racy = is_racy(fp, t_ns)
            if kind == "clean":
                pass
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
            elif kind == "checked":
                self.counters["pragma_checks"] += 1
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

    def _sweep_sync(self, states):
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
                if fp[0] is None:
                    out.append((state, "missing", fp, t_ns, None))
                    continue
                if prev is None or prev[0] is None or prev[0][:2] != fp[0][:2]:
                    # New inode/device (atomic replace, delete+recreate) or
                    # first sight: the schema_version alone cannot be trusted
                    out.append((state, "replaced", fp, t_ns, None))
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
        conn = db.connect()
        try:
            if prepare:
                self.ds._prepare_connection(conn, db.name)
            else:
                conn.row_factory = None
        except Exception:
            self._close(db, conn)
            raise
        return conn

    @staticmethod
    def _close(db, conn):
        try:
            conn.close()
        finally:
            try:
                db._all_connections.remove(conn)
            except ValueError:
                pass

    def _scan_sync(self, state):
        """Read schema_version + full schema in one read transaction."""
        db = state.db
        fp = t_ns = None
        if state.is_file:
            t_ns = time.time_ns()
            fp = fingerprint(db.path)
            if fp[0] is None:
                return {"state": state, "missing": True, "fp": fp, "t": t_ns}
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
        scans already in flight for the same database."""
        loop = asyncio.get_running_loop()
        pending = list(states)
        for _ in range(10):
            mine = []
            waits = []
            for state in pending:
                fut = state.scan_future
                if fut is not None and not fut.done():
                    if fut.get_loop() is loop:
                        waits.append(fut)
                        continue
                    # Scan started on an event loop that has gone away
                    state.scan_future = None
                    state.needs_scan = True
                if state.needs_scan and not state.removed:
                    state.needs_scan = False
                    state.scan_future = loop.create_future()
                    mine.append(state)
            if mine:
                try:
                    await self._scan_and_store(mine)
                finally:
                    for state in mine:
                        fut = state.scan_future
                        if fut is not None and not fut.done():
                            fut.set_result(None)
                        state.scan_future = None
            if waits:
                await asyncio.wait(waits)
            pending = [
                s
                for s in pending
                if not s.removed
                and (
                    s.needs_scan
                    or (s.scan_future is not None and not s.scan_future.done())
                )
                and s.error is None
            ]
            if not pending:
                break
        for state in states:
            # Failed scans are retried by sweeps, not by every request
            if not state.needs_scan or state.error is not None:
                self._pending.discard(state)

    async def _scan_and_store(self, states):
        memory = [s for s in states if not s.is_file and s.db.memory_name]
        if memory:
            results = []
            for state in memory:
                results.append(await self._scan_on_write_connection(state))
            await self._store(results)
            states = [s for s in states if s not in memory]
            if not states:
                return
        chunks = [states[i : i + SCAN_CHUNK] for i in range(0, len(states), SCAN_CHUNK)]
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def one(chunk):
            async with semaphore:
                results = await asyncio.to_thread(self._scan_chunk_sync, chunk)
            await self._store(results)

        if len(chunks) == 1:
            await one(chunks[0])
        else:
            await asyncio.gather(*(one(c) for c in chunks))

    async def _store(self, results):
        entries = []
        missing = []
        stored = []
        for r in results:
            state = r["state"]
            if state.removed:
                continue
            if "error" in r:
                state.error = r["error"]
                state.needs_scan = True
                logger.warning(
                    "Could not read schema of database %r: %s", state.name, r["error"]
                )
                continue
            state.error = None
            if r.get("missing"):
                missing.append(state)
                state.fp = r["fp"]
                state.fp_racy = True
                continue
            db = state.db
            fp_json = _fp_to_json(r["fp"], r["t"]) if r["fp"] is not None else None
            entries.append(
                (
                    state.name,
                    str(db.path) if db.path is not None else None,
                    db.is_memory,
                    r["version"],
                    fp_json,
                    r["schema"],
                )
            )
            stored.append(r)
        if entries:
            watcher = self

            def _write(conn):
                # Skip databases removed while we were scanning
                live = [e for e in entries if e[0] in watcher.states]
                write_catalog_entries(conn, live)

            await self.ds.get_internal_database().execute_write_fn(_write)
            for r in stored:
                state = r["state"]
                state.catalog_version = r["version"]
                state.missing = False
                if r["fp"] is not None:
                    state.fp = r["fp"]
                    state.fp_racy = is_racy(r["fp"], r["t"])
                state.stats["scans"] += 1
                self.counters["scans"] += 1
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
            reuse = await asyncio.to_thread(self._reusable_sync, states, persisted)
            for state in states:
                hit = reuse.get(state.name)
                if hit is None:
                    to_scan.append(state)
                    continue
                fp, t_ns, version = hit
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
        self._pending.clear()

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
            stored_fp, stored_t = _fp_from_json(row["fingerprint"])
            if stored_fp is None or is_racy(stored_fp, stored_t):
                continue
            t_ns = time.time_ns()
            fp = fingerprint(state.db.path)
            if fp == stored_fp:
                out[state.name] = (fp, t_ns, row["schema_version"])
        return out

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
