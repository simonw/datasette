import importlib
import pluggy
from . import hookspecs

default_plugins = (
    "datasette.publish.heroku",
    "datasette.publish.now",
)

pm = pluggy.PluginManager("datasette")
pm.add_hookspecs(hookspecs)
pm.load_setuptools_entrypoints("datasette")

# Load default plugins
for plugin in default_plugins:
    mod = importlib.import_module(plugin)
    pm.register(mod, plugin)
