from pluggy import HookimplMarker
from pluggy import HookspecMarker

hookspec = HookspecMarker("datasette")
hookimpl = HookimplMarker("datasette")


@hookspec
def prepare_connection(conn):
    "Modify SQLite connection in some way e.g. register custom SQL functions"


@hookspec
def prepare_jinja2_environment(env):
    "Modify Jinja2 template environment e.g. register custom template tags"


@hookspec
def extra_css_urls(template, database, table, datasette):
    "Extra CSS URLs added by this plugin"


@hookspec
def extra_js_urls(template, database, table, datasette):
    "Extra JavaScript URLs added by this plugin"


@hookspec
def extra_body_script(template, database, table, datasette):
    "Extra JavaScript code to be included in <script> at bottom of body"


@hookspec
def inspect(database, conn):
    "Inspect the database for plugin-specific information"


@hookspec
def prepare_sanic(app, datasette):
    "Add additional web routes"


@hookspec
def publish_subcommand(publish):
    "Subcommands for 'datasette publish'"


@hookspec(firstresult=True)
def render_cell(value, column, table, database, datasette):
    "Customize rendering of HTML table cell values"
