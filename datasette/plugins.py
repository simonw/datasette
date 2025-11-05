import importlib
import importlib.metadata
import importlib.resources
import pluggy
import sys
from . import hookspecs

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
)

pm = pluggy.PluginManager("datasette")
pm.add_hookspecs(hookspecs)

if not hasattr(sys, "_called_from_test"):
    # Only load plugins if not running tests
    pm.load_setuptools_entrypoints("datasette")

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
                if (importlib.resources.files(plugin.__name__) / "static").is_dir():
                    static_path = str(
                        importlib.resources.files(plugin.__name__) / "static"
                    )
                if (importlib.resources.files(plugin.__name__) / "templates").is_dir():
                    templates_path = str(
                        importlib.resources.files(plugin.__name__) / "templates"
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
            plugin_info["name"] = distinfo.project_name
        plugins.append(plugin_info)
    return plugins
