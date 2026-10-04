"""
Randomized torture harness for Datasette's connection management.

Run one scenario per process, so that a segfault or a hang is a result rather
than the end of the test run::

    python tests/connection_torture.py SCENARIO --seed N [--duration S]

Several event loops, each in its own thread (and each running several
tasks), drive one Datasette with extreme settings while a separate process
changes some of the database files. The last line of output is a JSON
summary. Exit status: 0 every check passed, 1 a check failed, 3 something
hung (an operation or a thread did not finish in time); a crash shows up as
death by a signal (faulthandler prints the stacks).

Checks:

* no operation hangs, no crash;
* only documented exceptions (see ``Torture.expected()``);
* per database, each writer's rows are in the order it wrote them, and
  every acknowledged blocking write (and every non-blocking one, once the
  queue has drained) is in the file;
* reads see the reader's own earlier blocking writes;
* the catalog lists a table as soon as a blocking DDL write returns (when
  the checkout has a SchemaWatcher), and matches the files after a forced
  refresh;
* after ``Datasette.close()`` and ``gc.collect()``: no file descriptor open
  on any database file, no write or SQL thread left, the instance itself
  collected.

Works against older checkouts too: features they lack (settings, scratch
databases, schema watch modes) are detected and skipped.
"""

import argparse
import asyncio
import collections
import faulthandler
import gc
import inspect
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import weakref

from datasette.app import Datasette
from datasette.database import Database, QueryInterrupted

try:
    from datasette.app import DEFAULT_SETTINGS
except ImportError:  # pragma: no cover
    DEFAULT_SETTINGS = {}
try:
    from datasette.database import DatasetteClosedError
except ImportError:  # pragma: no cover
    DatasetteClosedError = RuntimeError
try:
    from datasette.connection_pool import ConnectionLeaseError
except ImportError:
    ConnectionLeaseError = None
try:
    from datasette import scratch as scratch_module
except ImportError:
    scratch_module = None

OP_TIMEOUT = 30.0
SLOW_SQL = (
    "with recursive c(x) as (select 1 union all select x + 1 from c "
    "where x < 5000000) select count(*) from c"
)

SCENARIOS = {
    # Reads, writes of every kind, DDL, isolated functions, analyze_sql,
    # cancellation, external schema changes - on databases that stay put
    "mixed": {
        "loops": 3,
        "tasks": 4,
        "threads": 3,
        "duration": 6.0,
        "stable": 4,
        "ext": 2,
        "external_process": True,
        "ops": {
            "read": 6,
            "execute_fn": 3,
            "http": 3,
            "write": 6,
            "write_nonblocking": 3,
            "write_many": 2,
            "write_script": 1,
            "ddl": 2,
            "isolated": 1,
            "analyze": 1,
            "cancel_read": 1,
            "cancel_write": 1,
        },
    },
    # Databases come and go while they are being used
    "lifecycle": {
        "loops": 3,
        "tasks": 3,
        "threads": 3,
        "duration": 6.0,
        "stable": 3,
        "ext": 1,
        "churn": 3,
        "external_process": True,
        "ops": {
            "read": 4,
            "write": 4,
            "write_nonblocking": 1,
            "http": 2,
            "isolated": 1,
            "churn_read": 4,
            "churn_write": 3,
            "churn_slow": 1,
            "add_remove": 2,
            "replace_delete": 1,
            "db_close": 1,
            "scratch": 3,
            "cancel_read": 1,
        },
    },
    # Datasette.close() from one loop while the others keep going
    "close": {
        "loops": 3,
        "tasks": 4,
        "threads": 3,
        "duration": 4.0,
        "stable": 3,
        "ext": 1,
        "churn": 2,
        "external_process": True,
        "close_midflight": True,
        "ops": {
            "read": 4,
            "write": 4,
            "write_nonblocking": 2,
            "http": 2,
            "ddl": 1,
            "isolated": 1,
            "churn_slow": 1,
            "scratch": 1,
            "cancel_read": 1,
        },
    },
    # Slow queries with a progress handler, cancelled or with their database
    # closed underneath them (the close-while-stepping segfault)
    "cancel": {
        "loops": 2,
        "tasks": 6,
        "threads": 2,
        "duration": 5.0,
        "stable": 2,
        "churn": 3,
        "ops": {
            "cancel_read": 4,
            "cancel_write": 2,
            "churn_slow": 4,
            "churn_isolated_slow": 3,
            "add_remove": 3,
            "write": 2,
            "read": 2,
        },
    },
    # Databases removed (closed) while isolated functions with a progress
    # handler step them - on main, immutable isolated functions were not
    # tracked as pending work, and close() could free a connection mid-step
    # (segfault)
    "closerace": {
        "loops": 3,
        "tasks": 6,
        "threads": 3,
        "duration": 6.0,
        "stable": 1,
        "churn": 4,
        "churn_mutable_every": 1,
        "isolated_rows": 20000,
        "ops": {
            "churn_isolated_slow": 8,
            "churn_slow": 2,
            "churn_read": 2,
            "add_remove": 6,
        },
    },
    # Write-heavy: execute_write_many() and execute_write() from several
    # loops at once (a returned cursor freed on the caller's thread used to
    # reset the write thread's statement: "bad parameter or other API
    # misuse" on Python < 3.12)
    "writes": {
        "loops": 3,
        "tasks": 6,
        "threads": 3,
        "duration": 6.0,
        "stable": 2,
        "ext": 1,
        "external_process": True,
        "ops": {
            "write_many": 6,
            "write": 4,
            "write_nonblocking": 3,
            "cancel_write": 2,
            "write_script": 1,
            "ddl": 1,
            "read": 3,
            "isolated": 1,
        },
    },
    # Many databases, the smallest pool, 1ms idle timeout, crossdb
    "pool": {
        "loops": 3,
        "tasks": 6,
        "threads": 3,
        "duration": 5.0,
        "stable": 12,
        "crossdb": True,
        "private_cap": 1,
        "ops": {
            "read": 6,
            "execute_fn": 3,
            "http": 3,
            "crossdb_read": 2,
            "write": 3,
        },
    },
    # num_sql_threads=0: one thread, a new event loop every round (the
    # TestClient pattern), every kind of operation
    "nothreads": {
        "loops": 1,
        "rounds": True,
        "tasks": 6,
        "threads": 0,
        "duration": 5.0,
        "stable": 3,
        "ext": 1,
        "churn": 2,
        "external_process": True,
        "ops": {
            "read": 4,
            "execute_fn": 2,
            "http": 2,
            "write": 4,
            "write_nonblocking": 2,
            "write_many": 1,
            "write_script": 1,
            "ddl": 2,
            "isolated": 1,
            "analyze": 1,
            "churn_read": 2,
            "add_remove": 1,
            "replace_delete": 1,
            "scratch": 2,
        },
    },
}

EXTERNAL_WRITER = r"""
import random, sqlite3, sys, time
paths = sys.argv[2:]
rng = random.Random(int(sys.argv[1]))
n = 0
while True:
    path = rng.choice(paths)
    try:
        conn = sqlite3.connect(path, timeout=1, isolation_level=None)
        n += 1
        kind = rng.random()
        if kind < 0.4:
            conn.execute(f"create table if not exists ext_{n % 7} (id integer, v text)")
        elif kind < 0.6:
            conn.execute(f"drop table if exists ext_{rng.randrange(7)}")
        elif kind < 0.8:
            try:
                conn.execute(f"alter table ext_{rng.randrange(7)} add column c{n} text")
            except sqlite3.OperationalError:
                pass
        else:
            conn.execute("insert into data (v) values ('external')")
        conn.close()
    except sqlite3.Error:
        pass
    time.sleep(rng.uniform(0.005, 0.05))
"""


class Hang(Exception):
    pass


def make_file(path, wal=False, extra_sql=""):
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=wal")
    conn.executescript(
        "create table if not exists log (id integer primary key, writer text, "
        "seq integer, kind text);"
        "create index if not exists log_writer on log (writer, seq);"
        "create table if not exists data (id integer primary key, v text);"
        "insert into data (v) values ('one'), ('two');" + extra_sql
    )
    conn.commit()
    conn.close()


def describe(exc):
    return f"{type(exc).__module__}.{type(exc).__name__}: {exc}"[:300]


class Writer:
    """One task's identity: its own sequence numbers per database."""

    def __init__(self, name, rng):
        self.name = name
        self.rng = rng
        self.seq = collections.Counter()
        self.ddl = collections.Counter()


class Torture:
    def __init__(self, scenario, seed, duration=None):
        self.name = scenario
        self.spec = dict(SCENARIOS[scenario])
        if duration is not None:
            self.spec["duration"] = duration
        self.seed = seed
        self.rng = random.Random(seed)
        self.tmp = tempfile.mkdtemp(prefix="datasette_torture_")
        self.lock = threading.Lock()
        self.problems = []
        self.counts = collections.Counter()
        self.expected_errors = collections.Counter()
        # (db name, writer) -> list of seqs acknowledged by blocking writes
        self.acked = collections.defaultdict(list)
        # (db name, writer) -> seqs issued as non-blocking writes, and seqs
        # whose write was cancelled (may or may not have committed)
        self.nonblocking = collections.defaultdict(list)
        self.maybe = collections.defaultdict(list)
        self.closing = False
        self.closed_at = None
        self.stop_at = None
        self.churn_seq = 0
        self.scratch_seq = 0
        self.features = {}
        self.stable_names = []
        self.ext_names = []
        self.churn_paths = {}
        self.ds = None
        self.external = None
        self.writers = {}

    # -- bookkeeping -----------------------------------------------------

    def problem(self, kind, message):
        with self.lock:
            self.problems.append({"kind": kind, "message": message[:2000]})

    def count(self, key):
        with self.lock:
            self.counts[key] += 1

    def expected(self, op, group, exc):
        """Is exc a documented outcome of op on a database in group?
        Returns a label for the counts, or None for a problem."""
        message = str(exc)
        if isinstance(exc, QueryInterrupted):
            return "QueryInterrupted"
        if isinstance(exc, DatasetteClosedError) and (
            group in ("churn", "scratch") or self.closing
        ):
            return type(exc).__name__
        if self.closing and isinstance(exc, RuntimeError):
            # Work submitted to the executor after close() shut it down
            return "RuntimeError after close"
        if scratch_module is not None and isinstance(
            exc,
            (
                scratch_module.ScratchDatabaseNotFound,
                scratch_module.ScratchDatabaseExists,
            ),
        ):
            return type(exc).__name__
        if isinstance(exc, KeyError) and group in ("churn", "scratch"):
            return "KeyError (database removed)"
        if isinstance(exc, sqlite3.Error):
            if group in ("churn", "scratch"):
                # Files replaced, deleted and closed underneath
                return f"sqlite3 on {group}: {message[:40]}"
            if group == "mem" and "is locked" in message:
                # Shared-cache in-memory databases do not wait for table or
                # schema locks (documented)
                return "mem: shared-cache lock"
            if group == "ext" and (
                "no such table" in message
                or "no such column" in message
                or "database is locked" in message
                or "schema has changed" in message
            ):
                return f"ext: {message[:30]}"
            if self.closing and "closed database" in message:
                return "closed database after close"
        return None

    async def call(self, op, group, awaitable, timeout=OP_TIMEOUT):
        """Await with a timeout; classify exceptions. Returns (ok, value)."""
        try:
            value = await asyncio.wait_for(awaitable, timeout)
        except asyncio.TimeoutError:
            self.problem("hang", f"{op} on {group} did not finish in {timeout}s")
            return False, None
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            label = self.expected(op, group, e)
            if label is None:
                if ConnectionLeaseError is not None and isinstance(
                    e, ConnectionLeaseError
                ):
                    kind = "lease"
                else:
                    kind = "unexpected"
                self.problem(
                    kind,
                    f"{op} on {group}: {describe(e)}\n"
                    + "".join(traceback.format_exception(e)[-6:]),
                )
            else:
                with self.lock:
                    self.expected_errors[label] += 1
            return False, None
        self.count(op)
        return True, value

    # -- setup -----------------------------------------------------------

    def settings(self):
        wanted = {
            "num_sql_threads": self.spec["threads"],
            "max_open_connections": 1,
            "connection_idle_timeout_ms": 1,
            "schema_watch_interval_ms": 10,
        }
        return {k: v for k, v in wanted.items() if k in DEFAULT_SETTINGS}

    def build(self):
        spec = self.spec
        files = []
        for i in range(spec.get("stable", 0)):
            path = os.path.join(self.tmp, f"stable_{i}.db")
            make_file(path, wal=i % 2 == 0)
            files.append(path)
        ext_paths = []
        for i in range(spec.get("ext", 0)):
            path = os.path.join(self.tmp, f"ext_{i}.db")
            make_file(path, wal=i % 2 == 1)
            ext_paths.append(path)
        imm = os.path.join(self.tmp, "imm.db")
        make_file(imm)
        self.template = os.path.join(self.tmp, "template.db")
        make_file(self.template)
        kwargs = {}
        if spec.get("crossdb"):
            kwargs["crossdb"] = True
        scratch_dir = os.path.join(self.tmp, "scratch")
        self.features["scratch"] = (
            "scratch_dir" in inspect.signature(Datasette.__init__).parameters
        )
        if self.features["scratch"]:
            kwargs["scratch_dir"] = scratch_dir
        self.features["schema_watch"] = (
            "schema_watch" in inspect.signature(Datasette.add_database).parameters
        )
        self.ds = Datasette(
            files + ext_paths, immutables=[imm], settings=self.settings(), **kwargs
        )
        ds = self.ds
        self.features["watcher"] = hasattr(ds, "_schema_watcher")
        self.stable_names = [os.path.basename(p)[:-3] for p in files]
        self.ext_names = [os.path.basename(p)[:-3] for p in ext_paths]
        # Half the stable databases owned (when the checkout has modes):
        # added with add_database() rather than on the "command line"
        for i in range(spec.get("stable", 0)):
            if i % 2 == 1 and self.features["schema_watch"]:
                name = f"stable_{i}"
                ds.remove_database(name)
                ds.add_database(
                    Database(ds, path=files[i]), name=name, schema_watch="owned"
                )
        ds.add_memory_database(f"torture_mem_{os.getpid()}_{self.seed}", name="mem")
        self.groups = {name: "stable" for name in self.stable_names}
        self.groups.update({name: "ext" for name in self.ext_names})
        self.groups["mem"] = "mem"
        self.groups["imm"] = "imm"
        for i in range(spec.get("churn", 0)):
            self.add_churn()
        if spec.get("external_process") and ext_paths:
            self.external = subprocess.Popen(
                [sys.executable, "-c", EXTERNAL_WRITER, str(self.seed), *ext_paths],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def add_churn(self):
        with self.lock:
            self.churn_seq += 1
            n = self.churn_seq
        name = f"churn_{n}"
        path = os.path.join(self.tmp, f"{name}.db")
        shutil.copy(self.template, path)
        # Every nth churn database is immutable (all of them with 1)
        mutable = n % self.spec.get("churn_mutable_every", 4) != 0
        db = Database(self.ds, path=path, is_mutable=mutable)
        kwargs = {"schema_watch": "external"} if self.features["schema_watch"] else {}
        self.ds.add_database(db, name=name, **kwargs)
        with self.lock:
            self.churn_paths[name] = path
            self.groups[name] = "churn"
        return name

    # -- operations ------------------------------------------------------

    def pick(self, rng, groups):
        names = [n for n, g in list(self.groups.items()) if g in groups]
        if not names:
            return None, None
        name = rng.choice(names)
        return name, self.groups.get(name)

    def db(self, name):
        return self.ds.databases.get(name)

    async def op_read(self, w):
        name, group = self.pick(w.rng, ("stable", "ext", "mem", "imm"))
        db = self.db(name)
        if db is None:
            return
        if group == "stable" and w.seq[name] and w.rng.random() < 0.5:
            # Read-your-writes: this writer's last acknowledged seq
            last = self.acked.get((name, w.name))
            ok, results = await self.call(
                "read",
                group,
                db.execute("select max(seq) from log where writer = ?", [w.name]),
            )
            if ok and last:
                got = results.first()[0]
                if got is None or got < last[-1]:
                    self.problem(
                        "read-your-writes",
                        f"{name}: writer {w.name} read max(seq)={got} after "
                        f"its write {last[-1]} was acknowledged",
                    )
            return
        sql = w.rng.choice(
            [
                "select count(*) from data",
                "select * from data order by id desc limit 3",
                "select name from sqlite_master",
                "PRAGMA schema_version",
            ]
        )
        await self.call("read", group, db.execute(sql))

    async def op_execute_fn(self, w):
        name, group = self.pick(w.rng, ("stable", "ext", "mem", "imm"))
        db = self.db(name)
        if db is None:
            return

        def fn(conn):
            n = conn.execute("select count(*) from data").fetchone()[0]
            tables = [r[0] for r in conn.execute("select name from sqlite_master")]
            return n, tables

        await self.call("execute_fn", group, db.execute_fn(fn))

    async def op_http(self, w):
        name, group = self.pick(w.rng, ("stable", "ext", "imm", "mem"))
        path = w.rng.choice(
            [
                f"/{name}.json",
                f"/{name}/data.json?_size=2",
                "/.json",
                "/-/databases.json",
            ]
        )
        ok, response = await self.call("http", group, self.ds.client.get(path))
        if ok and response.status_code != 200:
            if self.closing or (group == "ext" and response.status_code == 404):
                # 404: data or the database page during an external change
                self.count(f"http {response.status_code} (expected)")
                return
            if response.status_code == 500 and (
                "schema is locked" in response.text
                or "table is locked" in response.text
            ):
                # SQLITE_LOCKED from the shared-cache in-memory database: it
                # does not wait for locks (documented, the same on main)
                with self.lock:
                    self.expected_errors["http 500: shared-cache lock"] += 1
                return
            self.problem(
                "http", f"GET {path} -> {response.status_code}: {response.text[:300]}"
            )

    def _next_seq(self, w, name):
        w.seq[name] += 1
        return w.seq[name]

    async def _write(self, w, name, group, block):
        db = self.db(name)
        if db is None:
            return
        seq = self._next_seq(w, name)
        ok, _ = await self.call(
            "write" if block else "write_nonblocking",
            group,
            db.execute_write(
                "insert into log (writer, seq, kind) values (?, ?, ?)",
                [w.name, seq, "write"],
                block=block,
            ),
        )
        # Recorded once the call has returned: a block=False write is in the
        # queue by then, so a later drain write is behind it
        if ok and block:
            self.acked[(name, w.name)].append(seq)
        elif ok:
            self.nonblocking[(name, w.name)].append(seq)
        elif not block:
            # Failed to queue, or maybe queued then failed: either way it
            # may or may not be in the file
            self.maybe[(name, w.name)].append(seq)

    async def op_write(self, w):
        name, group = self.pick(w.rng, ("stable",))
        if name:
            await self._write(w, name, group, True)

    async def op_write_nonblocking(self, w):
        name, group = self.pick(w.rng, ("stable",))
        if name:
            await self._write(w, name, group, False)

    async def op_write_many(self, w):
        name, group = self.pick(w.rng, ("stable",))
        db = self.db(name)
        if db is None:
            return
        seqs = [self._next_seq(w, name) for _ in range(w.rng.randint(1, 4))]
        ok, _ = await self.call(
            "write_many",
            group,
            db.execute_write_many(
                "insert into log (writer, seq, kind) values (?, ?, 'many')",
                [(w.name, s) for s in seqs],
            ),
        )
        if ok:
            self.acked[(name, w.name)].extend(seqs)

    async def op_write_script(self, w):
        name, group = self.pick(w.rng, ("stable",))
        db = self.db(name)
        if db is None:
            return
        seqs = [self._next_seq(w, name) for _ in range(2)]
        script = "".join(
            f"insert into log (writer, seq, kind) values ('{w.name}', {s}, 'script');"
            for s in seqs
        )
        ok, _ = await self.call("write_script", group, db.execute_write_script(script))
        if ok:
            self.acked[(name, w.name)].extend(seqs)

    async def op_ddl(self, w):
        name, group = self.pick(w.rng, ("stable", "mem"))
        db = self.db(name)
        if db is None:
            return
        w.ddl[name] += 1
        n = w.ddl[name]
        if n % 3 == 0:
            sql = f"drop table if exists ddl_{w.name}_{n - 1}"
            table = None
        else:
            table = f"ddl_{w.name}_{n}"
            sql = f"create table if not exists {table} (id integer primary key)"
        ok, _ = await self.call("ddl", group, db.execute_write(sql))
        if ok and table and self.features["watcher"] and not self.closing:
            # The catalog lists a table once a blocking DDL write returns
            ok2, rows = await self.call(
                "ddl_catalog",
                group,
                self.ds.get_internal_database().execute(
                    "select 1 from catalog_tables where database_name = ? "
                    "and table_name = ?",
                    [name, table],
                ),
            )
            if ok2 and not rows.rows and not self.closing:
                self.problem(
                    "catalog", f"{name}: {table} not in the catalog after its DDL"
                )

    async def op_isolated(self, w):
        name, group = self.pick(w.rng, ("stable", "imm"))
        db = self.db(name)
        if db is None:
            return

        def fn(conn):
            return conn.execute("select count(*) from data").fetchone()[0]

        await self.call("isolated", group, db.execute_isolated_fn(fn))

    async def op_analyze(self, w):
        name, group = self.pick(w.rng, ("stable",))
        db = self.db(name)
        if db is None or not hasattr(db, "analyze_sql"):
            return
        await self.call(
            "analyze", group, db.analyze_sql("select * from log where writer = 'x'")
        )

    async def op_cancel_read(self, w):
        name, group = self.pick(w.rng, ("stable", "imm"))
        db = self.db(name)
        if db is None:
            return
        task = asyncio.ensure_future(db.execute(SLOW_SQL, custom_time_limit=300))
        await asyncio.sleep(w.rng.uniform(0, 0.05))
        task.cancel()
        try:
            await asyncio.wait_for(task, OP_TIMEOUT)
            self.count("cancel_read finished")
        except asyncio.CancelledError:
            self.count("cancel_read cancelled")
        except asyncio.TimeoutError:
            self.problem("hang", f"cancelled read on {name} did not finish")
        except Exception as e:  # noqa: BLE001
            if self.expected("cancel_read", group, e) is None:
                self.problem("unexpected", f"cancel_read on {name}: {describe(e)}")

    async def op_cancel_write(self, w):
        name, group = self.pick(w.rng, ("stable",))
        db = self.db(name)
        if db is None:
            return
        seq = self._next_seq(w, name)
        self.maybe[(name, w.name)].append(seq)
        task = asyncio.ensure_future(
            db.execute_write(
                "insert into log (writer, seq, kind) values (?, ?, 'cancelled')",
                [w.name, seq],
            )
        )
        await asyncio.sleep(w.rng.uniform(0, 0.005))
        task.cancel()
        try:
            await asyncio.wait_for(task, OP_TIMEOUT)
            self.count("cancel_write finished")
        except asyncio.CancelledError:
            self.count("cancel_write cancelled")
        except asyncio.TimeoutError:
            self.problem("hang", f"cancelled write on {name} did not finish")
        except Exception as e:  # noqa: BLE001
            if self.expected("cancel_write", group, e) is None:
                self.problem("unexpected", f"cancel_write on {name}: {describe(e)}")

    async def op_crossdb_read(self, w):
        db = self.db("_memory")
        name, group = self.pick(w.rng, ("stable",))
        if db is None or name is None:
            return
        attached = {r.name for r in await db.attached_databases()}
        if name not in attached:
            return
        await self.call(
            "crossdb_read", group, db.execute(f"select count(*) from [{name}].data")
        )

    async def op_churn_read(self, w):
        name, group = self.pick(w.rng, ("churn",))
        db = self.db(name)
        if db is None:
            return
        if w.rng.random() < 0.5:
            await self.call(
                "churn_read", group, db.execute("select count(*) from data")
            )
        else:
            ok, response = await self.call(
                "churn_http", group, self.ds.client.get(f"/{name}/data.json?_size=1")
            )
            # 400: SQL errors on a replaced or deleted file are reported as
            # "Invalid SQL" by the table page
            if ok and response.status_code not in (200, 400, 404, 500):
                self.problem("http", f"GET /{name}/data.json -> {response.status_code}")

    async def op_churn_write(self, w):
        name, group = self.pick(w.rng, ("churn",))
        db = self.db(name)
        if db is None or not db.is_mutable:
            return
        await self.call(
            "churn_write",
            group,
            db.execute_write(
                "insert into data (v) values ('churn')", block=w.rng.random() < 0.7
            ),
        )

    async def op_churn_slow(self, w):
        # A slow query with Datasette's progress-handler time limit, on a
        # database that another task may close or remove meanwhile
        name, group = self.pick(w.rng, ("churn",))
        db = self.db(name)
        if db is None:
            return
        await self.call(
            "churn_slow", group, db.execute(SLOW_SQL, custom_time_limit=200)
        )

    async def op_churn_isolated_slow(self, w):
        # The close-while-stepping crash on main: an isolated function on an
        # immutable database (not tracked as pending work there) with a
        # progress handler installed, while the database is removed
        name, group = self.pick(w.rng, ("churn",))
        db = self.db(name)
        if db is None:
            return

        rows = self.spec.get("isolated_rows", 300000)

        def fn(conn):
            conn.set_progress_handler(lambda: 0, 100)
            try:
                return conn.execute(
                    "with recursive c(x) as (select 1 union all select x + 1 "
                    f"from c where x < {rows}) select count(*) from c"
                ).fetchone()[0]
            finally:
                conn.set_progress_handler(None, 0)

        await self.call("churn_isolated_slow", group, db.execute_isolated_fn(fn))

    async def op_add_remove(self, w):
        churn = [n for n, g in list(self.groups.items()) if g == "churn"]
        if len(churn) < 2 or (len(churn) < 6 and w.rng.random() < 0.5):
            if not self.closing:
                self.add_churn()
                self.count("add_database")
            return
        name = w.rng.choice(churn)
        with self.lock:
            if self.groups.get(name) != "churn":
                return
            del self.groups[name]
        try:
            self.ds.remove_database(name)
            self.count("remove_database")
        except KeyError:
            pass
        except Exception as e:  # noqa: BLE001
            if self.expected("remove_database", "churn", e) is None:
                self.problem("unexpected", f"remove_database({name}): {describe(e)}")

    async def op_replace_delete(self, w):
        name, _group = self.pick(w.rng, ("churn",))
        path = self.churn_paths.get(name)
        db = self.db(name)
        if path is None or db is None or not db.is_mutable:
            return
        if w.rng.random() < 0.6:
            new = f"{path}.{w.name}.{w.rng.randrange(10**9)}.new"
            make_file(new, extra_sql="create table replaced (id integer);")
            os.replace(new, path)
            self.count("file replaced")
        else:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    os.unlink(path + suffix)
                except OSError:
                    pass
            self.count("file deleted")

    async def op_db_close(self, w):
        name, _group = self.pick(w.rng, ("churn",))
        db = self.db(name)
        if db is None or not hasattr(db, "close"):
            return
        with self.lock:
            if self.groups.get(name) != "churn":
                return
            self.groups.pop(name, None)
        db.close()
        self.count("Database.close")

    async def op_scratch(self, w):
        ds = self.ds
        if not self.features["scratch"] or self.closing:
            return
        scratch = [n for n, g in list(self.groups.items()) if g == "scratch"]
        roll = w.rng.random()
        if not scratch or (roll < 0.3 and len(scratch) < 6):
            with self.lock:
                self.scratch_seq += 1
                name = f"s{self.seed}_{self.scratch_seq}"
            ok, db = await self.call(
                "scratch_create", "scratch", ds.create_scratch_database(name)
            )
            if ok:
                with self.lock:
                    self.groups[db.name] = "scratch"
                await self.call(
                    "scratch_write",
                    "scratch",
                    db.execute_write("create table t (id integer primary key, v text)"),
                )
            return
        name = w.rng.choice(scratch)
        db = self.db(name)
        if db is None:
            return
        if roll < 0.75:
            if w.rng.random() < 0.5:
                await self.call(
                    "scratch_write",
                    "scratch",
                    db.execute_write("insert into t (v) values ('x')"),
                )
            else:
                await self.call(
                    "scratch_read", "scratch", db.execute("select count(*) from t")
                )
        elif roll < 0.9:
            with self.lock:
                self.scratch_seq += 1
                new_name = f"s{self.seed}_{self.scratch_seq}"
            ok, new_db = await self.call(
                "scratch_rename", "scratch", ds.rename_scratch_database(name, new_name)
            )
            if ok:
                with self.lock:
                    self.groups.pop(name, None)
                    self.groups[new_db.name] = "scratch"
        else:
            ok, _ = await self.call(
                "scratch_delete", "scratch", ds.delete_scratch_database(name)
            )
            if ok:
                with self.lock:
                    self.groups.pop(name, None)

    # -- driving ---------------------------------------------------------

    async def worker(self, w, deadline):
        ops = self.spec["ops"]
        names = list(ops)
        weights = [ops[n] for n in names]
        while time.monotonic() < deadline:
            op = w.rng.choices(names, weights)[0]
            if self.closing and op in ("add_remove", "scratch", "replace_delete"):
                op = "read"
            try:
                await getattr(self, f"op_{op}")(w)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.problem(
                    "harness",
                    f"{op}: {describe(e)}\n"
                    + "".join(traceback.format_exception(e)[-5:]),
                )
            await asyncio.sleep(0)

    def writer(self, loop_index, t):
        # One Writer per task slot for the whole run: in "rounds" mode each
        # round's event loop reuses the same writers (and their sequences)
        key = (loop_index, t)
        with self.lock:
            w = self.writers.get(key)
            if w is None:
                rng = random.Random(f"{self.seed}-{loop_index}-{t}")
                w = self.writers[key] = Writer(f"w{loop_index}_{t}", rng)
        return w

    async def loop_main(self, loop_index, deadline):
        tasks = []
        for t in range(self.spec["tasks"]):
            tasks.append(self.worker(self.writer(loop_index, t), deadline))
        if self.spec.get("close_midflight") and loop_index == 0:
            tasks.append(self.close_midflight(deadline))
        await asyncio.gather(*tasks)

    async def close_midflight(self, deadline):
        await asyncio.sleep(
            self.rng.uniform(0.3, max(0.4, deadline - time.monotonic() - 1))
        )
        await self.drain_and_verify()
        self.closing = True
        self.closed_at = time.monotonic()
        t0 = time.monotonic()
        self.ds.close()
        self.counts["close seconds x100"] = int((time.monotonic() - t0) * 100)

    def run_loops(self):
        duration = self.spec["duration"]
        deadline = time.monotonic() + duration
        if self.spec.get("rounds"):
            # One thread, a fresh event loop per round
            while time.monotonic() < deadline:
                round_end = min(deadline, time.monotonic() + 0.4)
                asyncio.run(self.loop_main(0, round_end))
                self.count("rounds")
            return
        errors = []

        def runner(i):
            try:
                asyncio.run(self.loop_main(i, deadline))
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [
            threading.Thread(
                target=runner, args=(i,), name=f"torture-loop-{i}", daemon=True
            )
            for i in range(self.spec["loops"])
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(duration + OP_TIMEOUT + 15)
            if thread.is_alive():
                raise Hang(f"{thread.name} still running")
        for e in errors:
            self.problem("harness", f"loop died: {describe(e)}")

    # -- checks ----------------------------------------------------------

    async def drain_and_verify(self):
        """Wait for every write queue to drain, then check ordering and that
        nothing acknowledged is missing. Runs before close()."""
        ds = self.ds
        # Other loops may still be writing (close_midflight): only writes
        # acknowledged or queued before the drain must be visible to it
        with self.lock:
            acked = {k: list(v) for k, v in self.acked.items()}
            nonblocking = {k: list(v) for k, v in self.nonblocking.items()}
        for name in self.stable_names:
            db = ds.databases.get(name)
            await self.call("drain", "stable", db.execute_write("select 1"))
        for name in self.stable_names:
            db = ds.databases.get(name)
            ok, results = await self.call(
                "verify",
                "stable",
                db.execute(
                    "select writer, seq from log order by id", custom_time_limit=None
                ),
            )
            if ok:
                self.check_log(
                    name,
                    [(r[0], r[1]) for r in results.rows],
                    "before close",
                    acked,
                    nonblocking,
                )

    def check_log(self, name, rows, phase, acked=None, nonblocking=None):
        if acked is None:
            acked = self.acked
        if nonblocking is None:
            nonblocking = self.nonblocking
        by_writer = collections.defaultdict(list)
        for writer, seq in rows:
            by_writer[writer].append(seq)
        for (db_name, writer), seqs in list(acked.items()):
            if db_name != name:
                continue
            present = set(by_writer.get(writer, []))
            missing = [s for s in seqs if s not in present]
            if missing:
                self.problem(
                    "lost-write",
                    f"{name} ({phase}): {len(missing)} acknowledged writes by "
                    f"{writer} missing, e.g. seq {missing[:5]}",
                )
        for (db_name, writer), seqs in list(nonblocking.items()):
            if db_name != name:
                continue
            present = set(by_writer.get(writer, []))
            missing = [s for s in seqs if s not in present]
            if missing:
                self.problem(
                    "lost-write",
                    f"{name} ({phase}): {len(missing)} block=False writes by "
                    f"{writer} missing after the queue drained, e.g. seq {missing[:5]}",
                )
        for writer, seqs in by_writer.items():
            if seqs != sorted(seqs) or len(seqs) != len(set(seqs)):
                self.problem(
                    "write-order",
                    f"{name} ({phase}): writer {writer} rows out of order: {seqs[:20]}",
                )
        self.count("verified databases")

    async def check_catalog(self):
        """After a forced refresh, the catalog matches each stable and
        external database file."""
        ds = self.ds
        ok, _ = await self.call("refresh", "stable", ds.refresh_schemas(force=True))
        if not ok:
            return
        for name in self.stable_names + self.ext_names:
            db = ds.databases.get(name)
            internal = ds.get_internal_database()
            ok1, catalog = await self.call(
                "catalog",
                "stable",
                internal.execute(
                    "select table_name from catalog_tables where database_name = ?",
                    [name],
                ),
            )
            ok2, live = await self.call(
                "catalog",
                self.groups.get(name, "stable"),
                db.execute("select name from sqlite_master where type = 'table'"),
            )
            if ok1 and ok2:
                catalog_set = {r[0] for r in catalog.rows}
                live_set = {r[0] for r in live.rows}
                if catalog_set != live_set:
                    if name in self.ext_names:
                        # The external process may have changed it since;
                        # one more forced refresh must converge
                        await ds.refresh_schemas(force=True)
                        catalog = await internal.execute(
                            "select table_name from catalog_tables where database_name = ?",
                            [name],
                        )
                        live = await db.execute(
                            "select name from sqlite_master where type = 'table'"
                        )
                        catalog_set = {r[0] for r in catalog.rows}
                        live_set = {r[0] for r in live.rows}
                        if catalog_set == live_set or self.external is not None:
                            continue
                    self.problem(
                        "catalog",
                        f"{name}: catalog {sorted(catalog_set ^ live_set)} differ "
                        "from the file after refresh_schemas(force=True)",
                    )

    def check_files_after_close(self):
        for name in self.stable_names:
            path = os.path.join(self.tmp, f"{name}.db")
            conn = sqlite3.connect(path)
            try:
                rows = conn.execute(
                    "select writer, seq from log order by id"
                ).fetchall()
            finally:
                conn.close()
            self.check_log(name, rows, "file after close")

    def check_resources(self, baseline_threads):
        deadline = time.monotonic() + 15
        while True:
            gc.collect()
            leaked_fds = []
            for fd in os.listdir("/proc/self/fd"):
                try:
                    target = os.readlink(f"/proc/self/fd/{fd}")
                except OSError:
                    continue
                if target.startswith(self.tmp) or "datasette_temp_" in target:
                    leaked_fds.append(target)
            threads = [
                t.name
                for t in threading.enumerate()
                if t.name.startswith("_execute_writes")
                or t.name.startswith("ThreadPoolExecutor")
                or t.name.startswith("datasette-")
                and t.name != "datasette-read-pool-reaper"
            ]
            if (not leaked_fds and not threads) or time.monotonic() > deadline:
                break
            time.sleep(0.1)
        if leaked_fds:
            self.problem(
                "fd-leak",
                f"{len(leaked_fds)} fds still open after close: "
                + ", ".join(sorted({os.path.basename(p) for p in leaked_fds}))[:500],
            )
        if threads:
            self.problem("thread-leak", f"threads after close: {sorted(set(threads))}")
        extra = threading.active_count() - baseline_threads
        self.counts["threads above baseline after close"] = max(extra, 0)

    def run(self):
        baseline_threads = threading.active_count()
        self.build()
        ds = self.ds

        async def start():
            await ds.invoke_startup()
            await ds.get_database("mem").execute_write_script(
                "create table data (id integer primary key, v text);"
                "insert into data (v) values ('one'), ('two');"
            )
            if self.spec.get("private_cap") and hasattr(ds, "_read_pool"):
                # Below the public minimum: the pool's soft-cap path
                ds._read_pool.max_open = self.spec["private_cap"]

        asyncio.run(start())
        hung = None
        try:
            self.run_loops()
        except Hang as e:
            hung = e
            self.problem("hang", str(e))
        if hung is None and not self.closing:

            async def finish():
                await self.drain_and_verify()
                await self.check_catalog()

            asyncio.run(finish())
        if self.external is not None:
            self.external.kill()
            self.external.wait()
        if hung is None:
            if not self.closing:
                ds.close()
            ref = weakref.ref(ds)
            self.ds = ds = None
            self.check_files_after_close()
            self.check_resources(baseline_threads)
            gc.collect()
            if ref() is not None:
                self.problem(
                    "instance-leak",
                    "Datasette still alive after close() + gc.collect()",
                )
        shutil.rmtree(self.tmp, ignore_errors=True)
        return hung is None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--duration", type=float, default=None)
    args = parser.parse_args()
    faulthandler.enable()
    spec = SCENARIOS[args.scenario]
    duration = args.duration if args.duration is not None else spec["duration"]
    # A last resort: dump every thread's stack and exit if the whole run
    # takes far longer than it should
    faulthandler.dump_traceback_later(duration + OP_TIMEOUT + 60, exit=True)
    torture = Torture(args.scenario, args.seed, args.duration)
    t0 = time.monotonic()
    finished = torture.run()
    summary = {
        "scenario": args.scenario,
        "seed": args.seed,
        "python": sys.version.split()[0],
        "gil": getattr(sys, "_is_gil_enabled", lambda: True)(),
        "seconds": round(time.monotonic() - t0, 2),
        "features": torture.features,
        "ok": finished and not torture.problems,
        "problems": torture.problems[:50],
        "problem_kinds": dict(collections.Counter(p["kind"] for p in torture.problems)),
        "counts": dict(torture.counts),
        "expected_errors": dict(torture.expected_errors),
    }
    print(json.dumps(summary), flush=True)
    faulthandler.cancel_dump_traceback_later()
    if not finished or any(p["kind"] == "hang" for p in torture.problems):
        os._exit(3)
    os._exit(0 if summary["ok"] else 1)


if __name__ == "__main__":
    main()
