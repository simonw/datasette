import importlib
import os
import pluggy
from pprint import pprint
import sys
from . import hookspecs

if sys.version_info >= (3, 9):
    import importlib.resources as importlib_resources
else:
    import importlib_resources
if sys.version_info >= (3, 10):
    import importlib.metadata as importlib_metadata
else:
    import importlib_metadata


DEFAULT_PLUGINS = (
    "datasette.publish.heroku",
    "datasette.publish.cloudrun",
    "datasette.facets",
    "datasette.filters",
    "datasette.sql_functions",
    "datasette.actor_auth_cookie",
    "datasette.default_permissions",
    "datasette.default_magic_parameters",
    "datasette.blob_renderer",
    "datasette.default_menu_links",
    "datasette.handle_exception",
    "datasette.forbidden",
    "datasette.events",
)

pm = pluggy.PluginManager("datasette")
pm.add_hookspecs(hookspecs)

DATASETTE_TRACE_PLUGINS = os.environ.get("DATASETTE_TRACE_PLUGINS", None)


def before(hook_name, hook_impls, kwargs):
    print(file=sys.stderr)
    print(f"{hook_name}:", file=sys.stderr)
    pprint(kwargs, width=40, indent=4, stream=sys.stderr)
    print("Hook implementations:", file=sys.stderr)
    pprint(hook_impls, width=40, indent=4, stream=sys.stderr)


def after(outcome, hook_name, hook_impls, kwargs):
    results = outcome.get_result()
    if not isinstance(results, list):
        results = [results]
    print(f"Results:", file=sys.stderr)
    pprint(results, width=40, indent=4, stream=sys.stderr)


if DATASETTE_TRACE_PLUGINS:
    pm.add_hookcall_monitoring(before, after)


DATASETTE_LOAD_PLUGINS = os.environ.get("DATASETTE_LOAD_PLUGINS", None)

if not hasattr(sys, "_called_from_test") and DATASETTE_LOAD_PLUGINS is None:
    # Only load plugins if not running tests
    pm.load_setuptools_entrypoints("datasette")

# Load any plugins specified in DATASETTE_LOAD_PLUGINS")
if DATASETTE_LOAD_PLUGINS is not None:
    for package_name in [
        name for name in DATASETTE_LOAD_PLUGINS.split(",") if name.strip()
    ]:
        try:
            distribution = importlib_metadata.distribution(package_name)
            entry_points = distribution.entry_points
            for entry_point in entry_points:
                if entry_point.group == "datasette":
                    mod = entry_point.load()
                    pm.register(mod, name=entry_point.name)
                    # Ensure name can be found in plugin_to_distinfo later:
                    pm._plugin_distinfo.append((mod, distribution))
        except importlib_metadata.PackageNotFoundError:
            sys.stderr.write("Plugin {} could not be found\n".format(package_name))


# Load default plugins
for plugin in DEFAULT_PLUGINS:
    mod = importlib.import_module(plugin)
    pm.register(mod, plugin)


def get_plugins():
    plugins = []
    plugin_to_distinfo = dict(pm.list_plugin_distinfo())
    for plugin in pm.get_plugins():
        static_path = None
        templates_path = None
        if plugin.__name__ not in DEFAULT_PLUGINS:
            try:
                if (importlib_resources.files(plugin.__name__) / "static").is_dir():
                    static_path = str(
                        importlib_resources.files(plugin.__name__) / "static"
                    )
                if (importlib_resources.files(plugin.__name__) / "templates").is_dir():
                    templates_path = str(
                        importlib_resources.files(plugin.__name__) / "templates"
                    )
            except (TypeError, ModuleNotFoundError):
                # Caused by --plugins_dir= plugins
                pass
        plugin_info = {
            "name": plugin.__name__,
            "static_path": static_path,
            "templates_path": templates_path,
            "hooks": [h.name for h in pm.get_hookcallers(plugin)],
        }
        distinfo = plugin_to_distinfo.get(plugin)
        if distinfo:
            plugin_info["version"] = distinfo.version
            plugin_info["name"] = distinfo.name or distinfo.project_name
        plugins.append(plugin_info)
    return plugins
