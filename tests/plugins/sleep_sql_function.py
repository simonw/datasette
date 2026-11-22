import time

from datasette import hookimpl


@hookimpl
def prepare_connection(conn):
    conn.create_function("sleep", 1, lambda n: time.sleep(float(n)))
