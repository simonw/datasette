from pluggy import HookimplMarker
from pluggy import HookspecMarker

hookspec = HookspecMarker('datasette')
hookimpl = HookimplMarker('datasette')


@hookspec
def prepare_connection(conn):
    "Modify SQLite connection in some way e.g. register custom SQL functions"


@hookspec
def prepare_jinja2_environment(env):
    "Modify Jinja2 template environment e.g. register custom template tags"
