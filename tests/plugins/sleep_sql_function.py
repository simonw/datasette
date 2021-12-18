from datasette import hookimpl
import time


@hookimpl
def prepare_connection(conn):
    conn.create_function("sleep", 1, lambda n: time.sleep(float(n)))
