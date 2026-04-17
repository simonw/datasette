"""
Regression test for https://github.com/simonw/datasette/issues/2692 —
confirm that creating and closing Datasette instances in a loop does not
leak open file descriptors.

Each Datasette() with is_temp_disk internal DB opens a temp file and a
write thread with its own SQLite connection. Without Datasette.close()
nothing unwinds this state, and a large pytest run exhausts the process
FD limit.
"""

import asyncio
import threading

import pytest

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from datasette.app import Datasette


def _count_open_files():
    return len(psutil.Process().open_files())


def _count_threads():
    return threading.active_count()


@pytest.mark.skipif(psutil is None, reason="psutil not installed")
def test_close_releases_file_descriptors():
    # Warm-up so Python/library caches don't skew the baseline
    ds = Datasette(memory=True)
    asyncio.run(ds.invoke_startup())
    ds.close()

    baseline_fds = _count_open_files()
    baseline_threads = _count_threads()

    for _ in range(50):
        ds = Datasette(memory=True)
        asyncio.run(ds.invoke_startup())
        ds.close()

    after_fds = _count_open_files()
    after_threads = _count_threads()

    assert (
        after_fds - baseline_fds <= 2
    ), f"Leaked FDs: baseline={baseline_fds}, after=50 iterations={after_fds}"
    assert (
        after_threads - baseline_threads <= 2
    ), f"Leaked threads: baseline={baseline_threads}, after={after_threads}"
