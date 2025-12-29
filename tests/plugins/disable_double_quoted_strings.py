from datasette import hookimpl
from datasette.utils.sqlite import sqlite3


@hookimpl
def prepare_connection(conn):
    if hasattr(conn, "setconfig") and sqlite3.sqlite_version_info >= (3, 29):
        # Available only since Python 3.12 and SQLite 3.29.0
        conn.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DDL, False)
        conn.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DML, False)
