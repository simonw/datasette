"""Leased read-connection pool.

Read connections are no longer cached per worker thread. Instead every
``execute_fn()`` / ``execute()`` call checks a connection out of a pool for
the duration of one callback and returns it afterwards.

* One ``ReadConnectionPool`` per ``Datasette`` instance. It enforces a global
  cap (``max_open_connections``) on open pooled read connections across all
  file-backed databases. When a new connection is needed and the cap has been
  reached, the least recently used *idle* connection (from any database) is
  closed first.
* Each database keeps a LIFO stack of its idle connections, so the most
  recently used (warmest) connection is reused and surplus ones age out.
* Idle connections are closed after ``connection_idle_timeout`` seconds. This
  is done opportunistically on every checkout and by one process-wide daemon
  reaper thread, so it works with or without a running event loop
  (non-threaded mode, sync callers, closed loops in tests).
* When the cap has been reached and every connection is checked out the pool
  waits up to ``connection_pool_wait_ms`` for one to come back and then opens
  a connection anyway (the cap is soft). Each checked-out connection occupies
  one executor thread, so the overflow can never exceed ``num_sql_threads``.
* Callbacks receive a ``LeasedConnection`` proxy. Once the callback returns
  the proxy is invalidated and any further use raises
  ``ConnectionLeaseError``.
* In-memory databases are leased the same way but do not count against the
  cap and are never reaped: they use no file descriptors, and closing every
  connection to a named in-memory database would destroy its contents.
"""

import collections
import inspect
import threading
import time
import weakref

from .utils import sqlite3


class ConnectionLeaseError(RuntimeError):
    """A pooled read connection was used outside the callback it was lent to."""


class LeasedConnection:
    """Forwarding proxy handed to read callbacks.

    Every attribute access, method call and attribute assignment is forwarded
    to the underlying ``sqlite3.Connection`` while the lease is active. After
    the callback returns the proxy is expired and any use raises
    ``ConnectionLeaseError``. Each lease gets a fresh proxy, so a stale
    reference fails even if the same underlying connection has since been
    lent to another callback.

    ``isinstance(proxy, sqlite3.Connection)`` is True (via ``__class__``), but
    C functions that require a real connection object (for example the
    *target* argument of ``Connection.backup()``) reject the proxy.
    """

    __slots__ = ("_conn", "_db_name")

    def __init__(self, conn, db_name):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_db_name", db_name)

    def _live(self):
        conn = self._conn
        if conn is None:
            raise ConnectionLeaseError(
                f"A read connection to database {self._db_name!r} was used after the "
                "execute_fn() callback it was passed to had returned. Read "
                "connections are pooled and only valid inside the callback: do "
                "not store the connection (or a cursor) for later use"
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
        return self._live().__enter__()

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
        # Closing a pooled connection is allowed; the pool notices on release
        # and discards it instead of returning it to the idle stack.
        self._live().close()

    def __repr__(self):
        state = "expired" if self._conn is None else "active"
        return f"<LeasedConnection database={self._db_name!r} {state}>"


class _Entry:
    __slots__ = ("conn", "last_used", "state")

    def __init__(self, conn, state):
        self.conn = conn
        self.state = state
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
        self.counted = not db.is_memory


class ReadConnectionPool:
    def __init__(
        self,
        prepare_connection,
        max_open=128,
        idle_timeout=60.0,
        wait_ms=0,
        enabled=True,
    ):
        self._prepare_connection = prepare_connection
        self.max_open = max_open  # 0 = no limit
        self.idle_timeout = idle_timeout  # seconds, 0 = never
        self.wait_s = wait_ms / 1000.0
        self.enabled = enabled
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        # Idle, counted entries, least recently used first
        self._lru = collections.OrderedDict()
        self._open = 0  # counted open connections, idle + leased
        self._waiters = 0
        self._closed = False
        self.stats = collections.Counter()

    # -- public API ---------------------------------------------------------

    def run(self, db, fn):
        """Call fn(conn) with a pooled connection leased for the duration."""
        entry = self.acquire(db)
        lease = LeasedConnection(entry.conn, db.name)
        try:
            result = fn(lease)
        finally:
            lease._expire()
            self.release(entry)
        if isinstance(result, sqlite3.Cursor) or inspect.isgenerator(result):
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
            now = time.monotonic()
            self._collect_expired_locked(now, to_close)
            deadline = None
            while True:
                if deadline is not None:
                    self.stats["wait_ms_max"] = max(
                        self.stats["wait_ms_max"],
                        int((time.monotonic() - now) * 1000),
                    )
                if state.idle:
                    entry = state.idle.pop()
                    if state.counted:
                        del self._lru[entry]
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
                # Cap reached and every connection is checked out
                if self.wait_s > 0:
                    if deadline is None:
                        deadline = now + self.wait_s
                        self.stats["waits"] += 1
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        self._waiters += 1
                        t0 = time.monotonic()
                        try:
                            self._cond.wait(remaining)
                        finally:
                            self._waiters -= 1
                            waited_ms = int((time.monotonic() - t0) * 1000)
                            self.stats["wait_ms_total"] += waited_ms
                        continue
                    self.stats["wait_timeouts"] += 1
                if deadline is not None:
                    self.stats["wait_ms_max"] = max(
                        self.stats["wait_ms_max"],
                        int((time.monotonic() - now) * 1000),
                    )
                # Soft cap: open one more. Bounded by the number of threads
                # that can hold a lease at once (num_sql_threads).
                self.stats["exceeded_cap"] += 1
                break
            if entry is None:
                state.open += 1
                state.leased += 1
                self.stats["opened"] += 1
                if state.counted:
                    self._open += 1
                    self.stats["peak_open"] = max(self.stats["peak_open"], self._open)
        self._close_entries(to_close)
        if entry is not None:
            return entry
        # Open and prepare outside the lock
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
                if self._waiters:
                    self._cond.notify()
            if conn is not None:
                self._close_conn(state, conn)
            raise
        return _Entry(conn, state)

    def release(self, entry):
        state = entry.state
        conn = entry.conn
        discard = False
        try:
            # Hygiene: a callback that began a transaction and did not end it
            # must not hand that transaction to the next borrower
            rolled_back = False
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
            over_cap = state.counted and self.max_open and self._open > self.max_open
            if discard or over_cap or self._closed or state.closed or not self.enabled:
                state.open -= 1
                if state.counted:
                    self._open -= 1
                discard = True
                if over_cap:
                    self.stats["closed_over_cap"] += 1
            else:
                entry.last_used = time.monotonic()
                state.idle.append(entry)
                if state.counted:
                    self._lru[entry] = None
                    if self.idle_timeout > 0:
                        notify_reaper = entry.last_used + self.idle_timeout
            if self._waiters:
                self._cond.notify()
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
                    self._open -= 1
                state.open -= 1
        self._close_entries(to_close)

    def close(self):
        with self._lock:
            self._closed = True
            to_close = list(self._lru)
            self._lru.clear()
            for entry in to_close:
                entry.state.idle.remove(entry)
                entry.state.open -= 1
                self._open -= 1
            self._cond.notify_all()
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
            head.state.open -= 1
            self._open -= 1
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
        # monotonic time of the next planned scan, inf while sleeping
        # indefinitely or while scanning
        self.wake_at = float("inf")

    def wake(self, pool, expiry):
        # Unlocked pre-check: most releases do not need to wake the reaper
        if expiry >= self.wake_at and pool in self._pools:
            return
        with self._cond:
            self._pools.add(pool)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="datasette-read-pool-reaper", daemon=True
                )
                self._thread.start()
            if expiry < self.wake_at:
                self._dirty = True
                self._cond.notify()

    def _run(self):
        while True:
            with self._cond:
                self.wake_at = float("inf")
                self._dirty = False
                pools = list(self._pools)
            next_wake = None
            for pool in pools:
                try:
                    expiry = pool.reap_expired()
                except Exception:  # noqa: BLE001
                    expiry = None
                if expiry is not None and (next_wake is None or expiry < next_wake):
                    next_wake = expiry
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


_reaper = _Reaper()
