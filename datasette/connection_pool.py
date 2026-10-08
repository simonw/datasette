"""Leased read-connection pool, and the lease proxy every callback receives.

Read connections are not cached per worker thread. Every ``execute_fn()`` /
``execute()`` call checks a connection out of a pool for the duration of one
callback and returns it afterwards.

* One ``ReadConnectionPool`` per ``Datasette`` instance. It enforces a global
  cap (the ``max_open_connections`` setting) on open pooled read connections
  across all file-backed databases. When a new connection is needed and the
  cap has been reached, the least recently used *idle* connection (from any
  database) is closed first.
* Each database keeps a LIFO stack of its idle connections, so the most
  recently used (warmest) connection is reused and surplus ones age out.
* Idle connections are closed after ``connection_idle_timeout_ms``. This is
  done opportunistically on every checkout and by one process-wide daemon
  reaper thread, so it works with or without a running event loop
  (non-threaded mode, sync callers, closed loops in tests). Where threads
  cannot be started at all (Pyodide) only the opportunistic reaping runs.
* Reusable connections are opened with ``Database.connect()`` and prepared
  with ``Datasette._prepare_connection()`` exactly once, outside the pool
  lock. Immutable isolated callbacks use fresh, unprepared connections
  counted against the same cap and closed when the callback finishes.
* Each pooled connection remembers the database's ``_conn_generation`` when
  it was opened. The SchemaWatcher bumps that generation when a file is
  replaced or deleted and calls ``invalidate_database()``: idle connections
  are closed at once, and leased ones are discarded by their own thread when
  they are returned. A connection is never closed while another thread may
  be stepping it (that segfaults).
* The cap is hard. When every connection is leased, admission waits for
  release instead of exceeding it. Retiring connections remain counted
  until they have actually closed.
* Callbacks receive a ``LeasedConnection`` proxy. Once the callback returns
  the proxy is expired and any further use raises ``ConnectionLeaseError``.
  Write callbacks get the same proxy (see ``Database._execute_writes``).
* In-memory databases are leased the same way but do not count against the
  cap and are never reaped: they use no file descriptors, and closing every
  connection to a named in-memory database would destroy its contents. The
  exception is ``_memory`` under ``--crossdb``: each of its connections
  ATTACHes up to ten database files, so those are counted and reaped like
  file connections.
"""

import collections
import inspect
import threading
import time
import weakref

from .utils import sqlite3
from .write_budget import DatabaseResourceError, callback_scope


class ConnectionLeaseError(RuntimeError):
    """A connection was used outside the callback it was lent to."""


_CALLBACK_NAMES = {
    "read": "execute_fn()",
    "write": "execute_write_fn()",
    "isolated": "execute_isolated_fn()",
}


class LeasedConnection:
    """Forwarding proxy handed to read, write and isolated callbacks.

    Every attribute access, method call and attribute assignment is forwarded
    to the underlying ``sqlite3.Connection`` while the lease is active. After
    the callback returns the proxy is expired and any use raises
    ``ConnectionLeaseError``. Each lease gets a fresh proxy, so a stale
    reference fails even if the same underlying connection has since been
    lent to another callback.

    ``isinstance(proxy, sqlite3.Connection)`` is True (via ``__class__``), but
    C functions that require a real connection object (for example the
    *target* argument of ``Connection.backup()``) reject the proxy.

    Cursors, blobs, dump iterators and saved bound methods share the lease.
    Resources are closed on the owning thread before the connection is
    returned to the pool or the write transaction commits.
    """

    __slots__ = ("_blobs", "_conn", "_cursors", "_db_name", "_iterators", "_kind")

    def __init__(self, conn, db_name, kind="read"):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_db_name", db_name)
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_cursors", [])
        object.__setattr__(self, "_blobs", [])
        object.__setattr__(self, "_iterators", [])

    def _live(self):
        conn = self._conn
        if conn is None:
            raise ConnectionLeaseError(
                f"A {self._kind} connection to database {self._db_name!r} was used "
                f"after the {_CALLBACK_NAMES.get(self._kind, 'callback')} callback "
                "it was passed to had returned. Connections are only valid inside "
                "the callback: do not store the connection or its resources for "
                "later use"
            )
        return conn

    def _expire(self):
        for iterator in self._iterators:
            close_cursors(iterator)
        self._iterators.clear()
        for blob in self._blobs:
            _close_cursor(blob)
        self._blobs.clear()
        for cursor in self._cursors:
            _close_cursor(cursor)
        self._cursors.clear()
        object.__setattr__(self, "_conn", None)

    def _wrap(self, result):
        if isinstance(result, sqlite3.Cursor):
            self._cursors.append(result)
            return LeasedCursor(result, self)
        # Incremental BLOB access was added in Python 3.11.
        blob_type = getattr(sqlite3, "Blob", None)
        if blob_type is not None and isinstance(result, blob_type):
            self._blobs.append(result)
            return LeasedBlob(result, self)
        if inspect.isgenerator(result):
            # iterdump() holds a raw connection, then a raw cursor once it
            # starts. Track even unstarted or stashed iterators so neither
            # the iterator nor its read lock can outlive this callback.
            self._iterators.append(result)
            return LeasedIterator(result, self)
        return result

    # isinstance(conn, sqlite3.Connection) checks keep working
    @property
    def __class__(self):
        return sqlite3.Connection

    def __getattr__(self, name):
        value = getattr(self._live(), name)
        if getattr(value, "__self__", None) is self._conn:
            # Resolve the method again when called: caching a bound method
            # must not bypass expiry or keep the raw connection alive.
            def call(*args, **kwargs):
                return self._wrap(getattr(self._live(), name)(*args, **kwargs))

            return call
        return value

    def __setattr__(self, name, value):
        setattr(self._live(), name, value)

    def __delattr__(self, name):
        delattr(self._live(), name)

    # Dunder methods are looked up on the type, so forward them explicitly
    def __enter__(self):
        self._live().__enter__()
        # "with conn as c" must not hand out the raw connection
        return self

    def __exit__(self, *args):
        return self._live().__exit__(*args)

    def __call__(self, *args, **kwargs):
        return self._live()(*args, **kwargs)

    # Hot paths, to skip __getattr__
    def execute(self, *args, **kwargs):
        return self._wrap(self._live().execute(*args, **kwargs))

    def cursor(self, *args, **kwargs):
        return self._wrap(self._live().cursor(*args, **kwargs))

    def close(self):
        conn = self._live()
        if self._kind == "write":
            # The write connection belongs to the database's write thread,
            # which keeps using it for every later write
            raise ConnectionLeaseError(
                f"Cannot close the write connection to database "
                f"{self._db_name!r} from inside an execute_write_fn() "
                "callback: Datasette owns it and closes it when it is idle"
            )
        # Closing a pooled read connection is allowed; the pool notices on
        # release and discards it instead of returning it to the idle stack.
        # An isolated connection is closed afterwards anyway.
        conn.close()

    def __repr__(self):
        state = "expired" if self._conn is None else "active"
        return f"<LeasedConnection {self._kind} database={self._db_name!r} {state}>"


class LeasedCursor:
    """A cursor cannot outlive the callback that owns its connection.

    Metadata remains readable after closure for execute_write_many() and
    callbacks returning insertion cursors. cursor.connection never exposes
    the raw SQLite connection.
    """

    __slots__ = ("_cursor", "_lease")

    def __init__(self, cursor, lease):
        object.__setattr__(self, "_cursor", cursor)
        object.__setattr__(self, "_lease", lease)

    @property
    def __class__(self):
        return sqlite3.Cursor

    @property
    def connection(self):
        return self._lease

    def _live(self):
        self._lease._live()
        return self._cursor

    def __getattr__(self, name):
        if name in ("rowcount", "lastrowid", "description"):
            return getattr(self._cursor, name)
        value = getattr(self._live(), name)
        if getattr(value, "__self__", None) is self._cursor:

            def call(*args, **kwargs):
                result = getattr(self._live(), name)(*args, **kwargs)
                return self if result is self._cursor else result

            return call
        return value

    def __setattr__(self, name, value):
        setattr(self._live(), name, value)

    def __iter__(self):
        self._live()
        return self

    def __next__(self):
        return next(self._live())

    def close(self):
        self._live().close()


class LeasedBlob:
    """Incremental BLOB access lasts only as long as its connection lease."""

    __slots__ = ("_blob", "_lease")

    def __init__(self, blob, lease):
        self._blob = blob
        self._lease = lease

    @property
    def __class__(self):
        return sqlite3.Blob

    def _live(self):
        self._lease._live()
        return self._blob

    def __getattr__(self, name):
        value = getattr(self._live(), name)
        if getattr(value, "__self__", None) is self._blob:

            def call(*args, **kwargs):
                return getattr(self._live(), name)(*args, **kwargs)

            return call
        return value

    def __len__(self):
        return len(self._live())

    def __getitem__(self, key):
        return self._live()[key]

    def __setitem__(self, key, value):
        self._live()[key] = value

    def __enter__(self):
        self._live().__enter__()
        return self

    def __exit__(self, *args):
        return self._live().__exit__(*args)


class LeasedIterator:
    """An iterator returned by SQLite cannot resume after its lease ends."""

    __slots__ = ("_iterator", "_lease")

    def __init__(self, iterator, lease):
        self._iterator = iterator
        self._lease = lease

    def _live(self):
        self._lease._live()
        return self._iterator

    def __iter__(self):
        self._live()
        return self

    def __next__(self):
        return next(self._live())

    def send(self, value):
        return self._live().send(value)

    def throw(self, *args):
        return self._live().throw(*args)

    def close(self):
        return self._live().close()


def _contains_database_resource(value, seen=None):
    """Inspect ordinary containers without invoking arbitrary user iterators."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, (sqlite3.Cursor, LeasedBlob, LeasedIterator)):
        return True
    if isinstance(value, dict):
        return any(
            _contains_database_resource(v, seen) for pair in value.items() for v in pair
        )
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_database_resource(v, seen) for v in value)
    return False


def close_cursors(value):
    """Close the cursors in a callback's result (a cursor, or a tuple or
    list holding one), in a generator's locals, or - for an exception - in
    the locals of the frames it was raised through.

    Call it on the thread that owns the connection, before the connection
    can be used by anyone else. A cursor keeps a reference to the
    connection's cached statement for its SQL; on Python before 3.12,
    deallocating the cursor resets that statement. If the cursor dies on
    another thread (the caller's event loop) while the connection is running
    the same SQL again - the write thread's next execute_write_many(), or
    another worker that has leased the connection - the statement is reset
    mid-step and that query fails with "bad parameter or other API misuse".
    A closed cursor keeps its rowcount, lastrowid and description.
    """
    if isinstance(value, BaseException):
        tb = value.__traceback__
        while tb is not None:
            try:
                values = list(tb.tb_frame.f_locals.values())
            except Exception:  # noqa: BLE001
                values = []
            for item in values:
                if isinstance(item, sqlite3.Cursor):
                    _close_cursor(item)
            tb = tb.tb_next
        return
    if isinstance(value, sqlite3.Cursor):
        _close_cursor(value)
    elif isinstance(value, (tuple, list)):
        for item in value:
            if isinstance(item, sqlite3.Cursor):
                _close_cursor(item)
    elif inspect.isgenerator(value):
        try:
            values = list(inspect.getgeneratorlocals(value).values())
        except Exception:  # noqa: BLE001
            values = []
        for item in values:
            if isinstance(item, sqlite3.Cursor):
                _close_cursor(item)
        value.close()


def _close_cursor(cursor):
    try:
        cursor.close()
    except Exception:  # noqa: BLE001, S110
        # Its connection is closed already
        pass


def _generator_uses_connection(result):
    """True for a generator that would step the leased connection after
    the callback returned: one holding the connection (a closure over
    ``conn``) or a live cursor (``(r for r in conn.execute(...))``). A
    generator over rows already fetched inside the callback is fine."""
    if not inspect.isgenerator(result):
        return False
    try:
        values = inspect.getgeneratorlocals(result).values()
    except Exception:  # noqa: BLE001
        return False
    return any(
        isinstance(v, (sqlite3.Connection, sqlite3.Cursor, LeasedBlob, LeasedIterator))
        for v in values
    )


class _Entry:
    __slots__ = ("conn", "generation", "last_used", "state")

    def __init__(self, conn, state, generation):
        self.conn = conn
        self.state = state
        self.generation = generation
        self.last_used = 0.0


class _DatabaseState:
    """Per-database bookkeeping, stored on the Database as _read_pool_state."""

    __slots__ = ("closed", "counted", "db", "idle", "leased", "open")

    def __init__(self, db):
        self.db = db
        self.idle = []  # LIFO stack of _Entry
        self.open = 0  # idle + leased
        self.leased = 0
        self.closed = False
        # In-memory databases use no file descriptors and are never evicted
        # (closing every connection to a named one would destroy it) -
        # except _memory under --crossdb, whose connections each ATTACH up
        # to ten database files and are private, so closing one loses
        # nothing
        self.counted = not db.is_memory or (
            db.name == "_memory"
            and not db.memory_name
            and bool(getattr(db.ds, "crossdb", False))
        )


class ReadConnectionPool:
    def __init__(self, prepare_connection, max_open=32, idle_timeout=30.0):
        self._prepare_connection = prepare_connection
        self.max_open = max_open  # 0 = no limit
        self.idle_timeout = idle_timeout  # seconds, 0 = never
        self._lock = threading.Condition()
        # Idle, counted entries, least recently used first
        self._lru = collections.OrderedDict()
        self._open = 0  # counted open connections, idle + leased
        self._closed = False
        self.stats = collections.Counter()

    # -- public API ---------------------------------------------------------

    def run(self, db, fn, isolated=False):
        """Call fn(conn) with a pooled connection leased for the duration."""
        entry = self.acquire(db, fresh=isolated)
        lease = LeasedConnection(
            entry.conn, db.name, "isolated" if isolated else "read"
        )
        rejected = False
        discard = isolated
        try:
            with callback_scope():
                result = fn(lease)
            if _contains_database_resource(result) or _generator_uses_connection(
                result
            ):
                rejected = True
                close_cursors(result)
        except BaseException as e:
            close_cursors(e)
            discard = True
            raise
        finally:
            lease._expire()
            self.release(entry, discard=discard)
        if rejected:
            raise ConnectionLeaseError(
                f"An execute_fn() callback for database {db.name!r} returned a {result.__class__.__name__} containing database resources, which "
                "would keep using the pooled connection after the callback "
                "returned. Fetch the rows inside the callback instead"
            )
        return result

    def acquire(self, db, fresh=False):
        state = self._state(db)
        deadline = time.monotonic() + 5
        while True:
            to_close = []
            entry = None
            reserved = False
            with self._lock:
                if self._closed or state.closed or db._closed:
                    from .database import DatasetteClosedError

                    raise DatasetteClosedError(f"Database {db.name!r} has been closed")
                self._collect_expired_locked(time.monotonic(), to_close)
                if to_close:
                    pass  # Close before spending the capacity it releases.
                elif state.idle and not fresh:
                    entry = state.idle.pop()
                    if state.counted:
                        del self._lru[entry]
                    if entry.generation != db._conn_generation:
                        to_close.append(entry)
                        self.stats["discarded_stale"] += 1
                        entry = None
                    else:
                        state.leased += 1
                        self.stats["reused"] += 1
                elif state.counted and self.max_open and self._open >= self.max_open:
                    if self._lru:
                        victim, _ = self._lru.popitem(last=False)
                        victim.state.idle.remove(victim)
                        to_close.append(victim)
                        self.stats["evicted_lru"] += 1
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise DatabaseResourceError(
                                "Timed out waiting for a read connection"
                            )
                        self._lock.wait(min(remaining, 0.1))
                else:
                    generation = db._conn_generation
                    state.open += 1
                    state.leased += 1
                    self.stats["opened"] += 1
                    if state.counted:
                        self._open += 1
                        self.stats["peak_open"] = max(
                            self.stats["peak_open"], self._open
                        )
                    reserved = True
            if to_close:
                self._close_entries(to_close)
            if entry is not None:
                return entry
            if reserved:
                break
        conn = None
        try:
            conn = db.connect()
            if not fresh:
                with callback_scope():
                    self._prepare_connection(conn, db.name)
        except BaseException:
            if conn is not None:
                self._close_conn(state, conn)
            with self._lock:
                if conn is None:
                    state.open -= 1
                    if state.counted:
                        self._open -= 1
                state.leased -= 1
                self.stats["opened"] -= 1
                self.stats["open_failed"] += 1
                self._lock.notify_all()
            raise
        return _Entry(conn, state, generation)

    def evict_idle(self):
        """Reclaim idle file connections after external descriptor pressure."""
        with self._lock:
            entries = list(self._lru)
            self._lru.clear()
            for entry in entries:
                entry.state.idle.remove(entry)
        self._close_entries(entries)
        return len(entries)

    def release(self, entry, discard=False):
        state = entry.state
        conn = entry.conn
        rolled_back = False
        try:
            # Hygiene: a callback that began a transaction and did not end it
            # must not hand that transaction to the next borrower
            if conn.in_transaction:
                conn.rollback()
                rolled_back = True
        except Exception:  # noqa: BLE001
            # e.g. ProgrammingError: the callback closed the connection
            discard = True
        notify_reaper = None
        with self._lock:
            if discard:
                self.stats["discarded_broken"] += 1
            elif rolled_back:
                self.stats["rolled_back"] += 1
            state.leased -= 1
            stale = entry.generation != state.db._conn_generation
            over_cap = state.counted and self.max_open and self._open > self.max_open
            if discard or stale or over_cap or self._closed or state.closed:
                discard = True
                if over_cap:
                    self.stats["closed_over_cap"] += 1
                if stale:
                    self.stats["discarded_stale"] += 1
            else:
                entry.last_used = time.monotonic()
                state.idle.append(entry)
                if state.counted:
                    self._lru[entry] = None
                    if self.idle_timeout > 0:
                        notify_reaper = entry.last_used + self.idle_timeout
            self._lock.notify_all()
        if discard:
            self._close_conn(state, conn)
        if notify_reaper is not None:
            _reaper.wake(self, notify_reaper)

    def reap_expired(self):
        """Close expired idle connections. Returns the next expiry time
        (time.monotonic() based) or None if nothing is idle."""
        to_close = []
        with self._lock:
            self._collect_expired_locked(time.monotonic(), to_close)
            next_expiry = None
            if self._lru and self.idle_timeout > 0:
                head = next(iter(self._lru))
                next_expiry = head.last_used + self.idle_timeout
        self._close_entries(to_close)
        return next_expiry

    def invalidate_database(self, db):
        """db's file was replaced or deleted and its ``_conn_generation`` has
        been bumped: close its idle connections now. Leased connections are
        closed by the thread using them, when they are released."""
        state = getattr(db, "_read_pool_state", None)
        if state is None:
            return
        with self._lock:
            stale = [e for e in state.idle if e.generation != db._conn_generation]
            for entry in stale:
                state.idle.remove(entry)
                if state.counted:
                    self._lru.pop(entry, None)
                self.stats["discarded_stale"] += 1
        self._close_entries(stale)

    def close_database(self, db):
        """Close idle connections for db; leased ones are closed on release."""
        state = getattr(db, "_read_pool_state", None)
        if state is None:
            return
        with self._lock:
            state.closed = True
            to_close = state.idle
            state.idle = []
            for entry in to_close:
                if state.counted:
                    self._lru.pop(entry, None)
        self._close_entries(to_close)

    def close(self):
        with self._lock:
            self._closed = True
            to_close = list(self._lru)
            self._lru.clear()
            for entry in to_close:
                entry.state.idle.remove(entry)
        self._close_entries(to_close)

    def snapshot(self):
        with self._lock:
            return {
                "open": self._open,
                "idle": len(self._lru),
                "max_open": self.max_open,
                "idle_timeout": self.idle_timeout,
                "stats": dict(self.stats),
            }

    # -- internals ----------------------------------------------------------

    def _state(self, db):
        state = db._read_pool_state
        if state is None:
            with self._lock:
                state = db._read_pool_state
                if state is None:
                    state = db._read_pool_state = _DatabaseState(db)
        return state

    def _collect_expired_locked(self, now, to_close):
        if self.idle_timeout <= 0 or not self._lru:
            return
        cutoff = now - self.idle_timeout
        lru = self._lru
        while lru:
            head = next(iter(lru))
            if head.last_used > cutoff:
                break
            lru.popitem(last=False)
            head.state.idle.remove(head)
            to_close.append(head)
            self.stats["expired"] += 1

    def _close_entries(self, entries):
        for entry in entries:
            self._close_conn(entry.state, entry.conn)

    def _close_conn(self, state, conn):
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110
            pass
        with self._lock:
            state.open -= 1
            if state.counted:
                self._open -= 1
            self.stats["closed"] += 1
            self._lock.notify_all()
        try:
            state.db._all_connections.remove(conn)
        except ValueError:
            # May already have been cleared by Database.close()
            pass


class _Reaper:
    """One daemon thread per process that closes expired idle connections
    for every live pool. Pools are held by weak reference."""

    def __init__(self):
        self._cond = threading.Condition()
        self._pools = weakref.WeakSet()
        self._thread = None
        self._dirty = False
        # Set when this platform cannot start threads (Pyodide): pools then
        # rely on reaping during checkout only
        self.unavailable = False
        # monotonic time of the next planned scan, inf while sleeping
        # indefinitely or while scanning
        self.wake_at = float("inf")

    def wake(self, pool, expiry):
        # Unlocked pre-check: most releases do not need to wake the reaper
        if self.unavailable or (expiry >= self.wake_at and pool in self._pools):
            return
        with self._cond:
            self._pools.add(pool)
            if self._thread is None or not self._thread.is_alive():
                thread = threading.Thread(
                    target=self._run, name="datasette-read-pool-reaper", daemon=True
                )
                try:
                    thread.start()
                except RuntimeError:
                    # "can't start new thread": no threads on this platform
                    self.unavailable = True
                    return
                self._thread = thread
            if expiry < self.wake_at:
                self._dirty = True
                self._cond.notify()

    def _run(self):
        while True:
            with self._cond:
                self.wake_at = float("inf")
                self._dirty = False
                pools = list(self._pools)
            # In a helper so that no reference to a pool (and through its
            # _prepare_connection, the whole Datasette) survives in this
            # long-lived frame while it waits
            next_wake = self._reap(pools)
            del pools
            with self._cond:
                if self._dirty:
                    continue
                if next_wake is None:
                    self.wake_at = float("inf")
                    self._cond.wait()
                else:
                    self.wake_at = next_wake
                    # Small slack so a batch of connections that expire close
                    # together is handled in one pass
                    self._cond.wait(max(0.0, next_wake - time.monotonic()) + 0.05)

    @staticmethod
    def _reap(pools):
        next_wake = None
        for pool in pools:
            try:
                expiry = pool.reap_expired()
            except Exception:  # noqa: BLE001
                expiry = None
            if expiry is not None and (next_wake is None or expiry < next_wake):
                next_wake = expiry
        return next_wake


_reaper = _Reaper()
