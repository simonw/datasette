"""Record which databases a block of code touches, and assert a budget.

With a capped, idle-reaped connection pool, anything that loops over every
attached database opens (and evicts) a connection per database at a 0% hit
rate. These helpers make "this request only touches the databases it is
about" a testable property::

    async with connection_budget() as budget:
        await ds.client.get("/db1/t1.json")
    budget.assert_only("db1")  # plus _internal, always allowed

What counts as touching a database:

* ``opened``: ``Database.connect()`` calls - pooled read connections, write
  connections, isolated connections and the SchemaWatcher's short-lived
  scan/pragma connections (``track=False``)
* ``reads``: ``Database._execute_fn()`` calls (every ``execute()``,
  ``execute_fn()`` and introspection helper), whether or not the pool had an
  idle connection - so a warm pool does not hide a loop over every database
* ``writes``: ``execute_write*()``, ``execute_isolated_fn()`` and work run on
  the write connection
* ``attached``: databases ATTACHed to a connection while it was prepared
  (``--crossdb``)
* ``probes``: ``sqlite3.connect()`` calls that bypass ``Database.connect()``
  (``:memory:`` feature probes and the like), keyed by filename

The patches are installed process-wide for the duration of the block, so
background work (the SchemaWatcher's polling task, the read pool's reaper)
that runs during the block is counted too. Tests that want to measure one
request should turn polling off (``schema_watch_interval_ms=0``) or measure
it separately.
"""

import collections
import contextlib
import threading

from datasette import app as app_module
from datasette.database import Database
from datasette.utils import sqlite3

INTERNAL = "__INTERNAL__"

_local = threading.local()


class ConnectionBudget:
    def __init__(self):
        self._lock = threading.Lock()
        self.opened = collections.Counter()
        self.reads = collections.Counter()
        self.writes = collections.Counter()
        self.attached = collections.Counter()
        self.probes = collections.Counter()

    def _add(self, counter, key):
        with self._lock:
            counter[key] += 1

    def touched(self):
        """Names of every database opened, read, written or ATTACHed."""
        names = set()
        for counter in (self.opened, self.reads, self.writes, self.attached):
            names.update(counter)
        return names

    def summary(self):
        return {
            "opened": dict(self.opened),
            "reads": dict(self.reads),
            "writes": dict(self.writes),
            "attached": dict(self.attached),
            "probes": dict(self.probes),
        }

    def assert_only(self, *names, internal=True, probes=0):
        """Assert that nothing outside ``names`` was touched.

        ``_internal`` is allowed unless ``internal=False``. ``probes`` is the
        number of direct ``sqlite3.connect()`` calls allowed.
        """
        allowed = set(names)
        if internal:
            allowed.add(INTERNAL)
        extra = self.touched() - allowed
        assert not extra, (
            f"touched {len(extra)} database(s) outside the budget "
            f"{sorted(allowed)}: {sorted(extra)[:10]}"
            f"{' ...' if len(extra) > 10 else ''}\n{self.summary()}"
        )
        total_probes = sum(self.probes.values())
        assert (
            total_probes <= probes
        ), f"{total_probes} direct sqlite3.connect() call(s): {dict(self.probes)}"

    def assert_at_most(self, n, *, internal=True):
        """Assert that at most ``n`` user databases were touched."""
        touched = self.touched()
        if internal:
            touched.discard(INTERNAL)
        assert len(touched) <= n, (
            f"touched {len(touched)} databases, budget {n}: "
            f"{sorted(touched)[:10]}\n{self.summary()}"
        )

    def assert_no_connections(self, internal=True):
        """Assert that no connection was opened at all (``_internal`` aside)."""
        opened = {
            k: v for k, v in self.opened.items() if not (internal and k == INTERNAL)
        }
        assert not opened, f"opened connections: {opened}"
        assert not self.attached, f"attached: {dict(self.attached)}"
        assert not self.probes, f"probes: {dict(self.probes)}"


@contextlib.contextmanager
def _patched(budget):
    original_connect = Database.connect
    original_execute_fn = Database._execute_fn
    original_execute_write_fn = Database._execute_write_fn
    original_isolated = Database.execute_isolated_fn
    original_on_write_conn = Database._execute_on_write_connection
    original_prepare = app_module.Datasette._prepare_connection
    original_sqlite_connect = sqlite3.connect

    def connect(self, *args, **kwargs):
        budget._add(budget.opened, self.name)
        _local.depth = getattr(_local, "depth", 0) + 1
        try:
            return original_connect(self, *args, **kwargs)
        finally:
            _local.depth -= 1

    async def _execute_fn(self, fn):
        budget._add(budget.reads, self.name)
        return await original_execute_fn(self, fn)

    async def _execute_write_fn(self, fn, *args, **kwargs):
        budget._add(budget.writes, self.name)
        return await original_execute_write_fn(self, fn, *args, **kwargs)

    async def execute_isolated_fn(self, fn):
        budget._add(budget.writes, self.name)
        return await original_isolated(self, fn)

    async def _execute_on_write_connection(self, fn):
        budget._add(budget.writes, self.name)
        return await original_on_write_conn(self, fn)

    def _prepare_connection(self, conn, database, **kwargs):
        result = original_prepare(self, conn, database, **kwargs)
        try:
            rows = conn.execute("PRAGMA database_list").fetchall()
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows:
            name = row[1]
            if name not in ("main", "temp"):
                budget._add(budget.attached, name)
        return result

    def sqlite_connect(database, *args, **kwargs):
        if not getattr(_local, "depth", 0):
            budget._add(budget.probes, str(database))
        return original_sqlite_connect(database, *args, **kwargs)

    Database.connect = connect
    Database._execute_fn = _execute_fn
    Database._execute_write_fn = _execute_write_fn
    Database.execute_isolated_fn = execute_isolated_fn
    Database._execute_on_write_connection = _execute_on_write_connection
    app_module.Datasette._prepare_connection = _prepare_connection
    sqlite3.connect = sqlite_connect
    try:
        yield budget
    finally:
        Database.connect = original_connect
        Database._execute_fn = original_execute_fn
        Database._execute_write_fn = original_execute_write_fn
        Database.execute_isolated_fn = original_isolated
        Database._execute_on_write_connection = original_on_write_conn
        app_module.Datasette._prepare_connection = original_prepare
        sqlite3.connect = original_sqlite_connect


class connection_budget:
    """Context manager (sync or async) yielding a ConnectionBudget."""

    def __init__(self):
        self.budget = ConnectionBudget()
        self._cm = None

    def __enter__(self):
        self._cm = _patched(self.budget)
        return self._cm.__enter__()

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *exc):
        return self.__exit__(*exc)
