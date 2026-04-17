"""
Pytest plugin that automatically closes any Datasette instances constructed
during a pytest test — both in the test body and in function-scoped
fixtures. Instances constructed by session-, module-, class- or package-
scoped fixtures are left alone, because other tests in the session will
still want to use them.

Registered as a pytest11 entry point in pyproject.toml so that downstream
projects using Datasette get the same FD-safety net for their own tests.

Opt out by setting ``datasette_autoclose = false`` in pytest.ini (or the
equivalent ini file).
"""

from __future__ import annotations

import contextvars
import weakref

import pytest

from datasette.app import Datasette

_active_instances: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "datasette_active_instances", default=None
)

_original_init = Datasette.__init__


def _tracking_init(self, *args, **kwargs):
    _original_init(self, *args, **kwargs)
    instances = _active_instances.get()
    if instances is not None:
        instances.append(weakref.ref(self))


Datasette.__init__ = _tracking_init


def pytest_addoption(parser):
    parser.addini(
        "datasette_autoclose",
        help=(
            "Automatically close Datasette instances created inside test "
            "bodies and function-scoped fixtures (default: true)."
        ),
        default="true",
    )


def _enabled(config) -> bool:
    value = config.getini("datasette_autoclose")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Track Datasette instances across setup, call and teardown; close at end."""
    if not _enabled(item.config):
        yield
        return
    refs: list[weakref.ref] = []
    token = _active_instances.set(refs)
    try:
        yield
    finally:
        _active_instances.reset(token)
        for ref in reversed(refs):
            ds = ref()
            if ds is None:
                continue
            try:
                ds.close()
            except Exception as e:
                item.warn(
                    pytest.PytestUnraisableExceptionWarning(
                        f"Error closing Datasette instance: {e!r}"
                    )
                )


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    """Exempt instances created by non-function-scoped fixtures.

    Session-, module-, class- and package-scoped fixtures produce Datasette
    instances that must survive beyond the current test — other tests in
    the session will still use them. When such a fixture creates one or
    more Datasette instances during its setup, we snapshot the tracking
    list before the fixture runs and subtract off any instances that were
    added during its setup, so they don't get closed at test teardown.
    """
    refs = _active_instances.get()
    if refs is None:
        yield
        return
    before_ids = {id(ref) for ref in refs}
    yield
    if fixturedef.scope != "function":
        new_refs = [ref for ref in refs if id(ref) not in before_ids]
        for new_ref in new_refs:
            try:
                refs.remove(new_ref)
            except ValueError:
                pass
