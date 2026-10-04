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
* Every connection is opened with ``Database.connect()`` and prepared with
  ``Datasette._prepare_connection()`` exactly once, outside the pool lock.
* Each pooled connection remembers the database's ``_conn_generation`` when
  it was opened. The SchemaWatcher bumps that generation when a file is
  replaced or deleted and calls ``invalidate_database()``: idle connections
  are closed at once, and leased ones are discarded by their own thread when
  they are returned. A connection is never closed while another thread may
  be stepping it (that segfaults).
* The cap is soft. Each lease occupies one executor thread (or the event
  loop in non-threaded mode), and Datasette clamps the cap to at least
  ``4 x num_sql_threads``, so "cap reached and every connection leased"
  cannot happen with the public settings. If it does (a cap lowered through
  the private attribute) the pool opens one more connection and closes it
  on release.
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

    Not enforceable: a cursor created inside the callback and used after it
    returns, and the raw connection reached through ``cursor.connection``.
    """

    __slots__ = ("_conn", "_db_name", "_kind")

    def __init__(self, conn, db_name, kind="read"):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_db_name", db_name)
        object.__setattr__(self, "_kind", kind)

    def _live(self):
        conn = self._conn
        if conn is None:
            raise ConnectionLeaseError(
                f"A {self._kind} connection to database {self._db_name!r} was used "
                f"after the {_CALLBACK_NAMES.get(self._kind, 'callback')} callback "
                "it was passed to had returned. Connections are only valid inside "
                "the callback: do not store the connection (or a cursor) for "
                "later use"
            )
        return conn

    def _expire(self):
        object.__setattr__(self, "_conn", None)

    # isinstance(conn, sqlite3.Connection) checks keep working
    @property
    def __class__(self):
        return sqlite3.Connection

    def __getattr__(self, name):
        return getattr(self._live(), name)

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
        return self._live().execute(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        return self._live().cursor(*args, **kwargs)

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
    return any(isinstance(v, (sqlite3.Connection, sqlite3.Cursor)) for v in values)


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
    def __init__(self, prepare_connection, max_open=128, idle_timeout=30.0):
        self._prepare_connection = prepare_connection
        self.max_open = max_open  # 0 = no limit
        self.idle_timeout = idle_timeout  # seconds, 0 = never
        self._lock = threading.Lock()
        # Idle, counted entries, least recently used first
        self._lru = collections.OrderedDict()
        self._open = 0  # counted open connections, idle + leased
        self._closed = False
        self.stats = collections.Counter()

    # -- public API ---------------------------------------------------------

    def run(self, db, fn):
        """Call fn(conn) with a pooled connection leased for the duration."""
        entry = self.acquire(db)
        lease = LeasedConnection(entry.conn, db.name, "read")
        try:
            result = fn(lease)
        finally:
            lease._expire()
            self.release(entry)
        if isinstance(result, sqlite3.Cursor) or _generator_uses_connection(result):
            raise ConnectionLeaseError(
                f"An execute_fn() callback for database {db.name!r} returned a {type(result).__name__}, which "
                "would keep using the pooled connection after the callback "
                "returned. Fetch the rows inside the callback instead"
            )
        return result

    def acquire(self, db):
        state = self._state(db)
        to_close = []
        entry = None
        with self._lock:
            if self._closed or state.closed:
                from .database import DatasetteClosedError

                raise DatasetteClosedError(f"Database {db.name!r} has been closed")
            self._collect_expired_locked(time.monotonic(), to_close)
            while True:
                if state.idle:
                    entry = state.idle.pop()
                    if state.counted:
                        del self._lru[entry]
                    if entry.generation != db._conn_generation:
                        # Opened before the file was replaced or deleted
                        self._forget_locked(entry)
                        to_close.append(entry)
                        self.stats["discarded_stale"] += 1
                        entry = None
                        continue
                    state.leased += 1
                    self.stats["reused"] += 1
                    break
                if not state.counted or not self.max_open or self._open < self.max_open:
                    break
                if self._lru:
                    # Make room by closing the least recently used idle
                    # connection, which may belong to any database
                    victim, _ = self._lru.popitem(last=False)
                    victim.state.idle.remove(victim)
                    victim.state.open -= 1
                    self._open -= 1
                    to_close.append(victim)
                    self.stats["evicted_lru"] += 1
                    continue
                # Cap reached and every connection is leased: open one more
                # (soft cap). Bounded by the number of threads that can hold
                # a lease at once, and closed again on release.
                self.stats["exceeded_cap"] += 1
                break
            if entry is None:
                # Read before connecting: if the generation moves while we
                # connect, the connection is discarded on release - one
                # connection too many, never a stale one handed out twice
                generation = db._conn_generation
                state.open += 1
                state.leased += 1
                self.stats["opened"] += 1
                if state.counted:
                    self._open += 1
                    self.stats["peak_open"] = max(self.stats["peak_open"], self._open)
        self._close_entries(to_close)
        if entry is not None:
            return entry
        # Open and prepare outside the lock, exactly once per connection
        conn = None
        try:
            conn = db.connect()
            self._prepare_connection(conn, db.name)
        except BaseException:
            with self._lock:
                state.open -= 1
                state.leased -= 1
                self.stats["opened"] -= 1
                self.stats["open_failed"] += 1
                if state.counted:
                    self._open -= 1
            if conn is not None:
                self._close_conn(state, conn)
            raise
        return _Entry(conn, state, generation)

    def release(self, entry):
        state = entry.state
        conn = entry.conn
        discard = False
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
                state.open -= 1
                if state.counted:
                    self._open -= 1
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
                self._forget_locked(entry)
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
                self._forget_locked(entry)
        self._close_entries(to_close)

    def close(self):
        with self._lock:
            self._closed = True
            to_close = list(self._lru)
            self._lru.clear()
            for entry in to_close:
                entry.state.idle.remove(entry)
                self._forget_locked(entry)
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

    def _forget_locked(self, entry):
        # Caller holds the lock and has already removed entry from the idle
        # stack and the LRU
        entry.state.open -= 1
        if entry.state.counted:
            self._open -= 1

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
            self._forget_locked(head)
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
            self.stats["closed"] += 1
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
