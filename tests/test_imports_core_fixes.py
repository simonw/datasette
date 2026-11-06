"""
Regression tests for the core import library as the rest of Datasette uses
it: datasette/imports/inserter.py under the worker import in
datasette/imports/subprocess_import.py.
"""

import asyncio
import os
import time

import pytest

from datasette.app import Datasette
from datasette.database import Database
from datasette.hookspecs import hookimpl
from datasette.imports import inserter as inserter_module
from datasette.imports.subprocess_import import import_file_via_worker
from datasette.plugins import pm


class _SlowWrites:
    "A write_wrapper plugin that holds every write for a moment."

    __name__ = "_SlowWrites"

    def __init__(self, seconds):
        self.seconds = seconds

    @hookimpl
    def write_wrapper(self, datasette, database, request, transaction):
        def wrapper(conn):
            time.sleep(self.seconds)
            yield

        return wrapper


@pytest.fixture
def slow_writes():
    plugin = _SlowWrites(0.01)
    pm.register(plugin, name="test_core_fixes_slow_writes")
    try:
        yield plugin
    finally:
        pm.unregister(name="test_core_fixes_slow_writes")


@pytest.fixture
def db():
    ds = Datasette()
    db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
    yield db
    db.close()
    ds.close()


def _code(i):
    if i % 7 == 0:
        return ""
    return f"{i}.50" if i % 2 else str(i)


def _late_widening_csv(path, rows, text_at):
    with open(path, "w") as fp:
        fp.write("id,code\n")
        fp.writelines(
            f"{i},{'late' if i == text_at else _code(i)}\n" for i in range(rows)
        )
    return path


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@pytest.mark.asyncio
async def test_cancelling_a_worker_import_during_the_backlog(
    db, tmp_path, monkeypatch, slow_writes
):
    # A late widening to text leaves the stored numbers of every row before
    # it to convert, a slice per write. Cancelling the import meanwhile used
    # to convert all of them before import_rows() raised - seconds on a big
    # table - with the worker left running until then. Now the call raises
    # once the write in progress is done, the worker is killed, and the
    # conversion finishes in the background: with the original text of the
    # numbers kept, although subprocess_import's clean up drops the TEMP
    # tables it saw import_rows() create under the name they used to have
    monkeypatch.setattr(inserter_module, "CONVERT_SECONDS", 1e-6)
    monkeypatch.setattr(inserter_module, "_MIN_CONVERT_ROWS", 100)
    path = _late_widening_csv(tmp_path / "late.csv", 20_000, text_at=15_000)
    state = {}
    task = None

    def progress(p):
        state["pid"] = p.pid
        if p.rows_written >= 16_000 and "cancelled" not in state:
            state["cancelled"] = time.perf_counter()
            task.cancel()

    task = asyncio.ensure_future(
        import_file_via_worker(db, "t", path, progress=progress, stop_timeout=0.5)
    )
    with pytest.raises(asyncio.CancelledError):
        await task
    raised = time.perf_counter() - state["cancelled"]
    # 140 slices of 10ms were left; at most one write, the worker's exit and
    # the clean up's own write or two go before the call raises
    assert raised < 0.5, raised
    assert not _alive(state["pid"])

    def declared(conn):
        return dict(
            conn.execute("select name, type from pragma_table_info('t')").fetchall()
        )

    # Still converting
    assert (await db.execute_fn(declared))["code"] == "BLOB"
    for _ in range(300):
        if (await db.execute_fn(declared))["code"] == "TEXT":
            break
        await asyncio.sleep(0.05)
    assert await db.execute_fn(declared) == {"id": "INTEGER", "code": "TEXT"}
    rows = [tuple(r) for r in await db.execute("select id, code from t order by id")]
    assert len(rows) >= 16_000
    expected = [(i, "late" if i == 15_000 else _code(i)) for i in range(len(rows))]
    assert rows == expected
    temp = await db.execute_write_fn(
        lambda conn: conn.execute("select name from sqlite_temp_master").fetchall()
    )
    assert temp == []
