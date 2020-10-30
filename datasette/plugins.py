import importlib
import pluggy
import pkg_resources
import sys
from . import hookspecs

DEFAULT_PLUGINS = (
    "datasette.publish.heroku",
    "datasette.publish.cloudrun",
    "datasette.facets",
    "datasette.sql_functions",
    "datasette.actor_auth_cookie",
    "datasette.default_permissions",
    "datasette.default_magic_parameters",
    "datasette.blob_renderer",
    "datasette.default_menu_links",
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
                if pkg_resources.resource_isdir(plugin.__name__, "static"):
                    static_path = pkg_resources.resource_filename(
                        plugin.__name__, "static"
                    )
                if pkg_resources.resource_isdir(plugin.__name__, "templates"):
                    templates_path = pkg_resources.resource_filename(
                        plugin.__name__, "templates"
                    )
            except (KeyError, ImportError):
                # Caused by --plugins_dir= plugins - KeyError/ImportError thrown in Py3.5
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
