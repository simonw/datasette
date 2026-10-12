"""Error recovery must also work without Python 3.11's SQLite error metadata."""

import errno
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from datasette.schema_watcher import _is_transient
from datasette.utils import sqlite3
from datasette.write_budget import DatabaseResourceError, connect_with_retry


@pytest.fixture
def old_sqlite_errors(monkeypatch):
    for name in ("SQLITE_CANTOPEN", "SQLITE_NOMEM"):
        monkeypatch.delattr(sqlite3, name, raising=False)


@pytest.mark.parametrize(
    "error,expected",
    [
        (sqlite3.OperationalError("unable to open database file"), True),
        (sqlite3.OperationalError("out of memory"), True),
        (sqlite3.OperationalError("database is locked"), True),
        (sqlite3.OperationalError("no such table: missing"), False),
        (OSError(errno.EMFILE, "too many open files"), True),
    ],
)
def test_transient_errors_without_sqlite_constants(old_sqlite_errors, error, expected):
    assert _is_transient(error) is expected


@pytest.mark.parametrize("os_error", [False, True])
def test_open_recovers_without_sqlite_constants(old_sqlite_errors, tmp_path, os_error):
    path = tmp_path / "db.sqlite"
    path.touch()
    pool = Mock()
    db = SimpleNamespace(path=str(path), ds=SimpleNamespace(_read_pool_or_none=pool))
    error = (
        OSError(errno.EMFILE, "too many open files")
        if os_error
        else sqlite3.OperationalError("unable to open database file")
    )
    connection = object()
    connect = Mock(side_effect=[error, connection])
    assert connect_with_retry(db, connect) is connection
    assert connect.call_count == 2
    pool.evict_idle.assert_called_once_with()


@pytest.mark.parametrize("os_error", [False, True])
def test_open_failure_preserved_without_sqlite_constants(
    old_sqlite_errors, tmp_path, os_error
):
    path = tmp_path / "db.sqlite"
    path.touch()
    db = SimpleNamespace(path=str(path), ds=SimpleNamespace(_read_pool_or_none=None))
    error = (
        OSError(errno.EMFILE, "too many open files")
        if os_error
        else sqlite3.OperationalError("unable to open database file")
    )
    connect = Mock(side_effect=error)
    with pytest.raises(
        DatabaseResourceError if os_error else sqlite3.OperationalError
    ) as exc:
        connect_with_retry(db, connect)
    assert (exc.value.__cause__ if os_error else exc.value) is error
    assert connect.call_count == 2
