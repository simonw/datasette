using_pysqlite3 = False
try:
    import pysqlite3 as sqlite3

    using_pysqlite3 = True
except ImportError:
    import sqlite3

if hasattr(sqlite3, "enable_callback_tracebacks"):
    sqlite3.enable_callback_tracebacks(True)

_cached_sqlite_version = None


def sqlite_version():
    global _cached_sqlite_version
    if _cached_sqlite_version is None:
        _cached_sqlite_version = _sqlite_version()
    return _cached_sqlite_version


def _sqlite_version():
    return tuple(
        map(
            int,
            sqlite3.connect(":memory:")
            .execute("select sqlite_version()")
            .fetchone()[0]
            .split("."),
        )
    )


def supports_table_xinfo():
    return sqlite_version() >= (3, 26, 0)


def supports_generated_columns():
    return sqlite_version() >= (3, 31, 0)
