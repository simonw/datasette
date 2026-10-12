"""Waiting for work started on another event loop.

One Datasette can be driven by several event loops at once, each in its own
thread (``datasette --get``, TestClient, pytest-asyncio, an embedding app).
asyncio primitives (``asyncio.Lock``, ``asyncio.Future``) belong to one loop,
so "one call does the work, concurrent callers wait for it" is built from a
``concurrent.futures.Future`` that any loop can wait for, and a
``threading.Lock`` held only for the moment it takes to claim the work.
"""

import asyncio
import concurrent.futures


class InFlight:
    """One piece of work in progress, started by ``task`` on ``loop``."""

    __slots__ = ("future", "loop", "task")

    def __init__(self, loop, task=None):
        self.future = concurrent.futures.Future()
        self.loop = loop
        self.task = task

    def done(self):
        return self.future.done()

    def abandoned(self):
        """True if nothing will ever finish this work: the loop running it
        has closed, or has stopped (``run_until_complete()`` returned while
        the task doing it was still pending)."""
        loop = self.loop
        return loop.is_closed() or not loop.is_running()

    def finish(self):
        """Wake every waiter (they check for themselves whether the work
        succeeded). Safe to call more than once, from any thread."""
        try:
            self.future.set_result(None)
        except concurrent.futures.InvalidStateError:
            pass


def wait_for_concurrent(cf_future):
    """An asyncio future on the running loop that completes when
    ``cf_future`` does. Cancelling it does not cancel ``cf_future``, which
    other waiters - maybe on other loops - share."""
    loop = asyncio.get_running_loop()
    waiter = loop.create_future()

    def _set():
        if not waiter.done():
            waiter.set_result(None)

    def _done(_):
        try:
            loop.call_soon_threadsafe(_set)
        except RuntimeError:
            # That loop has closed; nobody is waiting any more
            pass

    cf_future.add_done_callback(_done)
    return waiter
