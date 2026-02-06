# perm_utils.py
from __future__ import annotations

import json
from typing import Any, Iterable, List

from datasette.permissions import PermissionSQL
from datasette.plugins import pm
from datasette.utils import await_me_maybe


# Sentinel object to indicate permission checks should be skipped
SKIP_PERMISSION_CHECKS = object()


async def gather_permission_sql_from_hooks(
    *, datasette, actor: dict | None, action: str
) -> List[PermissionSQL] | object:
    """Collect PermissionSQL objects from the permission_resources_sql hook.

    Ensures that each returned PermissionSQL has a populated ``source``.

    Returns SKIP_PERMISSION_CHECKS sentinel if skip_permission_checks context variable
    is set, signaling that all permission checks should be bypassed.
    """
    from datasette.permissions import _skip_permission_checks

    # Check if we should skip permission checks BEFORE calling hooks
    # This avoids creating unawaited coroutines
    if _skip_permission_checks.get():
        return SKIP_PERMISSION_CHECKS

    hook_caller = pm.hook.permission_resources_sql
    hookimpls = hook_caller.get_hookimpls()
    hook_results = list(hook_caller(datasette=datasette, actor=actor, action=action))

    collected: List[PermissionSQL] = []
    actor_json = json.dumps(actor) if actor is not None else None
    actor_id = actor.get("id") if isinstance(actor, dict) else None

    for index, result in enumerate(hook_results):
        hookimpl = hookimpls[index]
        resolved = await await_me_maybe(result)
        default_source = _plugin_name_from_hookimpl(hookimpl)
        for permission_sql in _iter_permission_sql_from_result(resolved, action=action):
            if not permission_sql.source:
                permission_sql.source = default_source
            if permission_sql.params is None:
                permission_sql.params = {}
            permission_sql.params.setdefault("action", action)
            permission_sql.params.setdefault("actor", actor_json)
            permission_sql.params.setdefault("actor_id", actor_id)
            collected.append(permission_sql)

    return collected


def _plugin_name_from_hookimpl(hookimpl) -> str:
    if getattr(hookimpl, "plugin_name", None):
        return hookimpl.plugin_name
    plugin = getattr(hookimpl, "plugin", None)
    if hasattr(plugin, "__name__"):
        return plugin.__name__
    return repr(plugin)


def _iter_permission_sql_from_result(
    result: Any, *, action: str
) -> Iterable[PermissionSQL]:
    if result is None:
        return []
    if isinstance(result, PermissionSQL):
        return [result]
    if isinstance(result, (list, tuple)):
        collected: List[PermissionSQL] = []
        for item in result:
            collected.extend(_iter_permission_sql_from_result(item, action=action))
        return collected
    if callable(result):
        permission_sql = result(action)  # type: ignore[call-arg]
        return _iter_permission_sql_from_result(permission_sql, action=action)
    raise TypeError(
        "Plugin providers must return PermissionSQL instances, sequences, or callables"
    )
