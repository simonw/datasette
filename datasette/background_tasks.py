"""
Supervised background-task registration for Datasette core.

Plugins that need long-lived background work (a polling loop, a queue
consumer, a scheduled job runner) register it with
``datasette.add_background_task(func, name=None)`` - typically from a
``startup`` plugin hook - instead of fire-and-forgetting their own
``asyncio.create_task()``. Core owns:

- **references**: every launched ``asyncio.Task`` is kept alive on a
  :class:`BackgroundTaskSupervisor`, so it can never be silently garbage
  collected the way an unreferenced ``create_task()`` call can be;
- **launch timing**: registered work is buffered until
  :meth:`BackgroundTaskSupervisor.launch_all` runs, which core arranges to
  happen only after *every* plugin's ``startup`` hook has finished - so
  a task that depends on another plugin having registered something first
  doesn't need ``tryfirst=True`` ordering tricks;
- **crash surfacing**: an unhandled exception in a background task is
  logged with its full traceback to the ``datasette.background_tasks``
  logger and recorded on the handle, instead of becoming an "Task
  exception was never retrieved" warning nobody sees;
- **cancellation**: :meth:`BackgroundTaskSupervisor.cancel_all` cancels
  every task still running and waits (with a grace period) for them to
  actually stop.
"""

from __future__ import annotations

import asyncio
import datetime
import functools
import inspect
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("datasette.background_tasks")


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _resolve_plugin_name(func: Callable) -> str | None:
    """Best-effort, cheap attempt to work out which registered plugin a
    background-task function belongs to, for the ``.plugin`` field on
    :class:`BackgroundTask` (used by ``/-/tasks`` and logs).

    This matches ``func``'s module against every currently-registered
    pluggy plugin's module - the same module a plugin's ``startup`` hook
    implementation lives in, in the overwhelmingly common case where
    ``add_background_task`` is called directly from (or a couple of
    frames below) that hook. It deliberately does *not* walk the call
    stack or otherwise try harder: this is a nice-to-have for
    introspection, not something worth building heavy machinery for, and
    returning ``None`` when it can't tell is a fine fallback.
    """
    try:
        from .plugins import pm

        module = inspect.getmodule(func)
        if module is None:
            return None
        module_name = getattr(module, "__name__", None)
        if not module_name:
            return None
        for plugin in pm.get_plugins():
            plugin_module = (
                plugin if inspect.ismodule(plugin) else inspect.getmodule(plugin)
            )
            if plugin_module is None:
                continue
            plugin_module_name = getattr(plugin_module, "__name__", None)
            if not plugin_module_name:
                continue
            if module_name == plugin_module_name or module_name.startswith(
                plugin_module_name + "."
            ):
                return pm.get_name(plugin)
    except Exception:  # noqa: BLE001
        # Never let plugin-name resolution break task registration.
        return None
    return None


class BackgroundTask:
    """A handle to a single piece of supervised background work.

    States: ``registered`` (added but not yet launched) -> ``running`` ->
    one of ``completed`` (returned cleanly), ``crashed`` (raised an
    exception other than ``CancelledError`` - see ``.exception``), or
    ``cancelled`` (``.cancel()`` was called, or it was still running at
    shutdown).
    """

    def __init__(
        self,
        name: str,
        func: Callable[[object], Awaitable[None]],
        plugin: str | None = None,
    ):
        self.name = name
        self.state = "registered"
        self.task: asyncio.Task | None = None
        self.exception: BaseException | None = None
        self.started_at: str | None = None
        self.plugin = plugin
        self._func = func
        self._supervisor: BackgroundTaskSupervisor | None = None

    def cancel(self) -> None:
        """Cancel this task.

        If it has already been launched, cancels the underlying
        ``asyncio.Task`` - its state becomes ``cancelled`` once the
        cancellation is observed (asynchronously, via the task's done
        callback). If it has not been launched yet, this is a no-op as
        far as asyncio is concerned (there's no task to cancel) but it
        deregisters the handle from its supervisor so it never runs.
        """
        if self.task is not None:
            self.task.cancel()
        elif self._supervisor is not None:
            self._supervisor._deregister(self)

    def __repr__(self) -> str:
        return f"<BackgroundTask name={self.name!r} state={self.state!r}>"


class BackgroundTaskSupervisor:
    """Owns registration and launch of every :class:`BackgroundTask` for a
    single ``Datasette`` instance.

    Registration (:meth:`add`) is separate from launch
    (:meth:`launch_all`): plugins register work whenever convenient
    (typically from a ``startup`` hook, but request handlers can register
    dynamic per-job work too), and it either sits buffered until
    :meth:`launch_all` runs, or - if :meth:`launch_all` has already run -
    starts immediately.

    Strong references to every :class:`BackgroundTask` (and its
    ``asyncio.Task``) are kept for the life of the instance, by design -
    that's what makes the enrichments-style "fire-and-forget task gets
    garbage collected mid-flight" bug impossible here. There is currently
    no pruning of completed/crashed/cancelled tasks, so a plugin that
    dynamically registers many short-lived tasks over a long process
    lifetime (a per-job registration pattern, e.g. one task per queued
    job) will grow this list without bound. That's an accepted v1
    trade-off in favour of full introspection (``/-/tasks``); revisit
    with a pruning or capping policy if unbounded growth is reported in
    practice.
    """

    def __init__(self, datasette):
        self._datasette = datasette
        self._tasks: list[BackgroundTask] = []
        self._names = set()
        self._launched = False
        self._lock = asyncio.Lock()

    def add(self, func, name=None) -> BackgroundTask:
        base_name = name or getattr(func, "__qualname__", None) or repr(func)
        actual_name = self._unique_name(base_name)
        plugin = _resolve_plugin_name(func)
        handle = BackgroundTask(actual_name, func, plugin=plugin)
        handle._supervisor = self
        self._tasks.append(handle)
        self._names.add(actual_name)
        if self._launched:
            self._launch_one(handle)
        return handle

    def _unique_name(self, base_name: str) -> str:
        if base_name not in self._names:
            return base_name
        n = 2
        while f"{base_name}-{n}" in self._names:
            n += 1
        return f"{base_name}-{n}"

    def _deregister(self, handle: BackgroundTask) -> None:
        try:
            self._tasks.remove(handle)
        except ValueError:
            pass
        self._names.discard(handle.name)

    def _launch_one(self, handle: BackgroundTask) -> None:
        handle.state = "running"
        handle.started_at = _utcnow_iso()
        handle.task = asyncio.create_task(
            handle._func(self._datasette), name=handle.name
        )
        handle.task.add_done_callback(functools.partial(_on_task_done, handle))

    async def launch_all(self) -> None:
        """Launch every currently-registered task that hasn't launched
        yet. Idempotent and safe to call concurrently: subsequent (or
        racing) calls are no-ops once the first has set ``self._launched``.
        """
        if self._launched:
            return
        async with self._lock:
            if self._launched:
                return
            self._launched = True
            for handle in list(self._tasks):
                if handle.task is None:
                    self._launch_one(handle)

    async def cancel_all(self, grace: float = 5.0) -> None:
        """Cancel every task that isn't already done, then wait up to
        ``grace`` seconds for them to actually finish. Stragglers still
        running after that are logged by name (but left to finish or not
        on their own - this does not forcibly kill them, asyncio has no
        mechanism for that).
        """
        handles_by_task = {
            handle.task: handle for handle in self._tasks if handle.task is not None
        }
        pending = [task for task in handles_by_task if not task.done()]
        for task in pending:
            task.cancel()
        if not pending:
            return
        _done, not_done = await asyncio.wait(pending, timeout=grace)
        if not_done:
            names = sorted(handles_by_task[task].name for task in not_done)
            logger.warning(
                "%d background task(s) did not finish within the %.1fs grace "
                "period after cancellation: %s",
                len(names),
                grace,
                ", ".join(names),
            )

    def tasks(self) -> list[BackgroundTask]:
        """Return every registered :class:`BackgroundTask`, launched or
        not, in registration order. Used by the ``/-/tasks`` debug
        endpoint.
        """
        return list(self._tasks)


def _on_task_done(handle: BackgroundTask, task: asyncio.Task) -> None:
    if task.cancelled():
        handle.state = "cancelled"
        return
    exc = task.exception()
    if exc is not None:
        handle.state = "crashed"
        handle.exception = exc
        logger.error("Background task %r crashed", handle.name, exc_info=exc)
        return
    handle.state = "completed"
