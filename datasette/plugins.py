import importlib
import pluggy
import sys
from . import hookspecs

DEFAULT_PLUGINS = (
    "datasette.publish.heroku",
    "datasette.publish.now",
    "datasette.publish.cloudrun",
    "datasette.facets",
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
