import threading
import time

import pytest

from datasette.database import Pool


def test_lock_connection():
    pool = Pool({"one": ":memory:"})
    with pool.connection("one") as conn:
        assert conn.lock.locked()
    assert not conn.lock.locked()


def test_connect_if_one_connection_is_locked():
    pool = Pool({"one": ":memory:"})
    connections = pool.connection_groups["one"].connections
    assert 3 == len(connections)
    # They should all start unlocked:
    assert all(not c.lock.locked() for c in connections)
    # Now lock one for the duration of this test
    first_connection = connections[0]
    try:
        first_connection.lock.acquire()
        # This should give us a different connection
        with pool.connection("one") as conn:
            assert conn is not first_connection
            assert conn.lock.locked()
            # There should be only one UNLOCKED connection now
            assert 1 == len([c for c in connections if not c.lock.locked()])
    finally:
        first_connection.lock.release()
    # At this point, all connections should be unlocked
    assert 3 == len([c for c in connections if not c.lock.locked()])


def test_block_until_connection_is_released():
    # If all connections are already in use, block until one is released
    pool = Pool({"one": ":memory:"}, max_connections_per_database=1)
    connections = pool.connection_groups["one"].connections
    assert 1 == len(connections)

    def block_connection(pool):
        with pool.connection("one"):
            time.sleep(0.05)

    t = threading.Thread(target=block_connection, args=[pool])
    t.start()
    # Give thread time to grab the connection:
    time.sleep(0.01)
    # Thread should now have grabbed and locked a connection:
    assert 1 == len([c for c in connections if c.lock.locked()])

    start = time.time()
    # Now we attempt to use the connection. This should block.
    with pool.connection("one") as conn:
        # At this point, over 0.02 seconds should have passed
        assert (time.time() - start) > 0.02
        assert conn.lock.locked()

    # Ensure thread has run to completion before ending test:
    t.join()
    # Connections should all be unlocked at the end
    assert all(not c.lock.locked() for c in connections)
