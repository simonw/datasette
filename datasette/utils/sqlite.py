try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

if hasattr(sqlite3, "enable_callback_tracebacks"):
    sqlite3.enable_callback_tracebacks(True)


def sqlite_version():
    return tuple(
        map(
            int,
            sqlite3.connect(":memory:")
            .execute("select sqlite_version()")
            .fetchone()[0]
            .split("."),
        )
    )
