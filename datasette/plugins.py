import importlib
import pluggy
from . import hookspecs

DEFAULT_PLUGINS = (
    "datasette.publish.heroku",
    "datasette.publish.now",
)

pm = pluggy.PluginManager("datasette")
pm.add_hookspecs(hookspecs)
pm.load_setuptools_entrypoints("datasette")

# Load default plugins
for plugin in DEFAULT_PLUGINS:
    mod = importlib.import_module(plugin)
    pm.register(mod, plugin)
