"""Bound retained writers and queued writes without changing per-database FIFO.

The coordinator only admits work and retires idle workers. SQLite connections
are opened, used and closed by their owning writer. A retiring thread keeps
its slot until it has actually exited. Internal catalog writes use their own
single writer, so user saturation cannot prevent catalog progress.
"""

import collections
import contextlib
import contextvars
import errno
import os
import threading
import time

in_database_callback = contextvars.ContextVar("in_database_callback", default=False)
RETIRE_WRITER = object()
_STARTING = object()


class DatabaseResourceError(RuntimeError):
    code = "database_resource_unavailable"
    execution_started = False


class DatabaseQueueFull(DatabaseResourceError):
    code = "database_queue_full"


class DatabaseAdmissionTimeout(DatabaseResourceError):
    code = "database_admission_timeout"


class DatabaseReentrancyError(RuntimeError):
    code = "database_reentrant_operation"


def check_reentrancy():
    if in_database_callback.get():
        raise DatabaseReentrancyError(
            "Schedule dependent database work after the current callback returns"
        )


@contextlib.contextmanager
def callback_scope():
    token = in_database_callback.set(True)
    try:
        yield
    finally:
        in_database_callback.reset(token)


def connect_with_retry(db, connect):
    """Retry only opening a connection, never preparation or a callback."""
    from .utils import sqlite3

    for attempt in range(2):
        try:
            return connect()
        except (OSError, sqlite3.OperationalError) as error:
            pressure = isinstance(error, OSError) and error.errno in (
                errno.EMFILE,
                errno.ENFILE,
                errno.ENOMEM,
            )
            # Python 3.10 exposes neither SQLite result constants nor
            # sqlite_errorcode. Keep its open-error recovery path working.
            code = getattr(error, "sqlite_errorcode", None)
            cantopen_code = getattr(sqlite3, "SQLITE_CANTOPEN", None)
            cantopen = isinstance(error, sqlite3.OperationalError) and (
                (
                    code is not None
                    and cantopen_code is not None
                    and code & 0xFF == cantopen_code
                )
                or "unable to open database file" in str(error).lower()
            )
            if not pressure and (not cantopen or not os.path.exists(db.path)):
                raise
            if attempt:
                if pressure:
                    raise DatabaseResourceError(
                        "Unable to open a database connection; try again later"
                    ) from error
                raise
            pool = db.ds._read_pool_or_none
            if pool is not None:
                pool.evict_idle()
            # Writers own their connections: ask them to retire rather
            # than closing SQLite objects from this thread.
            budget = db.ds.__dict__.get("_write_budget_instance")
            if budget is not None:
                budget.retire_idle()


class WriteBudget:
    def __init__(self, max_writers, max_pending, timeout_ms):
        self.max_writers = max_writers
        self.max_pending = max_pending
        self.timeout = timeout_ms / 1000
        self._condition = threading.Condition()
        self._active = {}  # db -> starting marker or thread (including retiring)
        self._pending = {}  # task -> (db, monotonic deadline)
        self._ready = collections.OrderedDict()
        self._busy = set()
        self._retirement_requested = set()
        self._thread = None
        self._inline_lock = threading.RLock()
        self._inline = collections.OrderedDict()
        self.stats = collections.Counter()

    def submit(self, db, task):
        # Caller holds db._write_thread_lock. Never take a database lock
        # while holding _condition in the coordinator.
        with self._condition:
            if len(self._pending) >= self.max_pending:
                self.stats["rejected"] += 1
                raise DatabaseQueueFull(
                    "Too many queued database writes; try again later"
                )
            self._pending[task] = (db, time.monotonic() + self.timeout)
            db._write_queue.put(task)
            self._ready.setdefault(db, None)
            self.stats["peak_pending"] = max(
                self.stats["peak_pending"], len(self._pending)
            )
            if self._thread is None:
                thread = threading.Thread(
                    target=self._run, name="datasette-write-scheduler", daemon=True
                )
                try:
                    thread.start()
                except BaseException:
                    self._pending.pop(task)
                    self._remove_queued(db, task)
                    self._ready.pop(db, None)
                    raise
                self._thread = thread
            self._condition.notify_all()

    @staticmethod
    def _remove_queued(db, task):
        # Remove expired entries instead of retaining unbounded tombstones.
        q = db._write_queue
        with q.mutex:
            try:
                q.queue.remove(task)
            except ValueError:
                pass  # A worker took it; take() will reject the expired task.
            q.not_full.notify()

    @staticmethod
    def _fail(task, error):
        from .database import _deliver_write_result

        _deliver_write_result(task, None, error)

    def take(self, db, task):
        with self._condition:
            item = self._pending.pop(task, None)
            self._condition.notify_all()
            if item is None:
                return False
            if time.monotonic() >= item[1]:
                self.stats["expired"] += 1
                expired = True
            else:
                self._busy.add(db)
                expired = False
        if expired:
            self._fail(
                task, DatabaseAdmissionTimeout("Write expired before execution started")
            )
        return not expired

    def completed(self, db):
        with self._condition:
            self._busy.discard(db)
            self._condition.notify_all()

    def should_yield(self, db):
        # Named in-memory databases need a keeper connection. Until a
        # separate keeper is introduced their writer slot is pinned.
        if db.is_memory or db._closed:
            return False
        with self._condition:
            return any(
                other is not db and other not in self._active
                for other, _ in self._pending.values()
            )

    def wake(self):
        with self._condition:
            self._condition.notify_all()

    def drain_database(self, db, timeout):
        """Wait for accepted work and its owning threads, including admission.

        The caller has stopped submissions and queued the shutdown sentinel.
        Normal admission deadlines still apply. If shutdown itself times out,
        only callbacks that have not started are cancelled; running callbacks
        keep ownership of their connections until they return.
        """
        with self._condition:
            drained = self._condition.wait_for(
                lambda: db not in self._active
                and not any(owner is db for owner, _ in self._pending.values()),
                timeout=timeout,
            )
        if not drained:
            self.cancel_database(db)
        else:
            with self._inline_lock:
                self._inline.pop(db, None)
        return drained

    def cancel_database(self, db):
        from .database import DatasetteClosedError

        cancelled = []
        with self._condition:
            for task, (owner, _) in list(self._pending.items()):
                if owner is db:
                    self._pending.pop(task)
                    self._remove_queued(db, task)
                    cancelled.append(task)
            self._ready.pop(db, None)
            self._condition.notify_all()
        for task in cancelled:
            self._fail(
                task, DatasetteClosedError("Database closed before write started")
            )
        with self._inline_lock:
            self._inline.pop(db, None)

    def _run(self):
        while True:
            expired = []
            start = []
            retire = []
            with self._condition:
                for db, thread in list(self._active.items()):
                    if thread is not _STARTING and not thread.is_alive():
                        del self._active[db]
                        self._retirement_requested.discard(db)
                        self._busy.discard(db)
                        self._ready.pop(db, None)
                        self._ready[db] = None
                now = time.monotonic()
                for task, (db, deadline) in list(self._pending.items()):
                    if now >= deadline:
                        self._pending.pop(task)
                        self._remove_queued(db, task)
                        self.stats["expired"] += 1
                        expired.append(task)
                pending_dbs = {db for db, _ in self._pending.values()}
                for db in list(self._ready):
                    if db not in pending_dbs:
                        del self._ready[db]
                    elif (
                        db not in self._active and len(self._active) < self.max_writers
                    ):
                        del self._ready[db]
                        self._active[db] = _STARTING
                        start.append(db)
                        self.stats["peak_writers"] = max(
                            self.stats["peak_writers"], len(self._active)
                        )
                if any(db not in self._active for db in pending_dbs):
                    for db in self._active:
                        if (
                            db not in self._busy
                            and not db.is_memory
                            and db not in self._retirement_requested
                        ):
                            self._retirement_requested.add(db)
                            retire.append(db)
                if not self._pending and not self._active:
                    self._ready.clear()
                    self._thread = None
                    stop = True
                else:
                    stop = False
                # close() may be waiting for a starting or retiring worker,
                # not just for callbacks to complete.
                self._condition.notify_all()
            for task in expired:
                self._fail(
                    task,
                    DatabaseAdmissionTimeout("Write expired before execution started"),
                )
            if stop:
                return
            for db in start:
                try:
                    with db._write_thread_lock:
                        # close() rejects new submissions, but accepted work
                        # remains eligible for this normally budgeted slot.
                        # Its shutdown sentinel follows the accepted tasks.
                        db._start_write_thread()
                        thread = db._write_thread
                    with self._condition:
                        if thread is None:
                            self._active.pop(db, None)
                        else:
                            self._active[db] = thread
                except Exception as error:  # noqa: BLE001
                    failures = []
                    with self._condition:
                        self._active.pop(db, None)
                        for task, (owner, _) in list(self._pending.items()):
                            if owner is db:
                                self._pending.pop(task)
                                self._remove_queued(db, task)
                                failures.append(task)
                    for task in failures:
                        self._fail(task, error)
            for db in retire:
                with db._write_thread_lock:
                    if db._write_thread is not None and not db._closed:
                        db._write_queue.put(RETIRE_WRITER)
            # Timed wake also observes completed thread retirement and
            # expires queued work while all slots are pinned or busy.
            with self._condition:
                self._condition.wait(0.01)
            # No completed database is retained in this long-lived frame.
            del start, retire, expired, pending_dbs
            db = thread = task = owner = failures = None

    @contextlib.contextmanager
    def inline(self, db):
        """Non-threaded mode: bounded idle connections, no helper threads."""
        check_reentrancy()
        with self._inline_lock:
            for other in list(self._inline):
                if other._write_connection is None or other._closed:
                    self._inline.pop(other)
            if db not in self._inline and len(self._inline) >= self.max_writers:
                victim = next((d for d in self._inline if not d.is_memory), None)
                if victim is None:
                    raise DatabaseResourceError(
                        "Writer capacity is held by in-memory databases"
                    )
                self._inline.pop(victim)
                conn, victim._write_connection = victim._write_connection, None
                victim._forget_connection(conn)
            self._inline.pop(db, None)
            self._inline[db] = None
            yield

    def retire_idle(self):
        with self._condition:
            candidates = [
                db
                for db in self._active
                if db not in self._busy
                and not db.is_memory
                and db not in self._retirement_requested
            ]
            self._retirement_requested.update(candidates)
        for db in candidates:
            with db._write_thread_lock:
                if db._write_thread is not None:
                    db._write_queue.put(RETIRE_WRITER)

    def snapshot(self):
        with self._condition:
            return {
                "writers": len(self._active),
                "pending": len(self._pending),
                "max_writers": self.max_writers,
                "max_pending": self.max_pending,
                "stats": dict(self.stats),
            }
