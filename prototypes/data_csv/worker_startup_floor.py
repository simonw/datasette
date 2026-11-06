"""
Run the import worker the way it could start if importing it did not
import the whole datasette package - to measure what that would save.
Used by bench_worker_startup.py; not something to ship.

    python prototypes/data_csv/worker_startup_floor.py [--no-sqlite-utils]
        [--no-entrypoints] PATH [worker options]

It stands in for two changes:

1. ``datasette/__init__.py`` importing its re-exports lazily (PEP 562
   ``__getattr__``), so ``import datasette.imports.loaders`` does not import
   ``datasette.app``'s dependencies (``datasette.utils.asgi``,
   ``background_tasks``, ``permissions``, ``views``...). Here: an empty
   ``datasette`` package module with the real ``__path__``.
2. The worker using a plugin manager that only registers the import
   loaders hook implementations plus entry point plugins, instead of
   ``datasette.plugins``, which imports all of DEFAULT_PLUGINS (facets,
   filters, publish, permissions...). Here: a stub ``datasette.plugins``.

``--no-sqlite-utils`` also stubs ``sqlite_utils.utils`` (loaders.py only
needs ``maximize_csv_field_size_limit`` from it, and importing it imports
all of ``sqlite_utils.db``). ``--no-entrypoints`` skips scanning installed
packages for plugins.
"""

import importlib.util
import sys
import types


def stub(no_sqlite_utils, no_entrypoints):
    spec = importlib.util.find_spec("datasette")
    package = types.ModuleType("datasette")
    package.__path__ = list(spec.submodule_search_locations)
    package.__spec__ = spec
    sys.modules["datasette"] = package
    import pluggy

    from datasette import hookspecs

    # What plugins import from the package
    package.hookimpl = hookspecs.hookimpl
    package.hookspec = hookspecs.hookspec
    pm = pluggy.PluginManager("datasette")
    pm.add_hookspecs(hookspecs)
    plugins = types.ModuleType("datasette.plugins")
    plugins.pm = pm
    sys.modules["datasette.plugins"] = plugins
    package.plugins = plugins
    if no_sqlite_utils:
        import csv

        sqlite_utils = types.ModuleType("sqlite_utils")
        utils = types.ModuleType("sqlite_utils.utils")

        def maximize_csv_field_size_limit():
            field_size_limit = sys.maxsize
            while True:
                try:
                    csv.field_size_limit(field_size_limit)
                    break
                except OverflowError:
                    field_size_limit = int(field_size_limit / 10)

        utils.maximize_csv_field_size_limit = maximize_csv_field_size_limit
        sqlite_utils.utils = utils
        sys.modules["sqlite_utils"] = sqlite_utils
        sys.modules["sqlite_utils.utils"] = utils
    from datasette.imports import loaders

    pm.register(loaders, "datasette.imports.loaders")
    if not no_entrypoints:
        pm.load_setuptools_entrypoints("datasette")


def main():
    argv = sys.argv[1:]
    no_sqlite_utils = "--no-sqlite-utils" in argv
    no_entrypoints = "--no-entrypoints" in argv
    argv = [a for a in argv if a not in ("--no-sqlite-utils", "--no-entrypoints")]
    stub(no_sqlite_utils, no_entrypoints)
    from datasette.imports import worker

    return worker.main(argv)


if __name__ == "__main__":
    sys.exit(main())
