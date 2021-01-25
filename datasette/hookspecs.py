from pluggy import HookimplMarker
from pluggy import HookspecMarker

hookspec = HookspecMarker("datasette")
hookimpl = HookimplMarker("datasette")


@hookspec
def startup(datasette):
    """Fires directly after Datasette first starts running"""


@hookspec
def asgi_wrapper(datasette):
    """Returns an ASGI middleware callable to wrap our ASGI application with"""


@hookspec
def prepare_connection(conn, database, datasette):
    """Modify SQLite connection in some way e.g. register custom SQL functions"""


@hookspec
def prepare_jinja2_environment(env):
    """Modify Jinja2 template environment e.g. register custom template tags"""


@hookspec
def extra_css_urls(template, database, table, columns, view_name, request, datasette):
    """Extra CSS URLs added by this plugin"""


@hookspec
def extra_js_urls(template, database, table, columns, view_name, request, datasette):
    """Extra JavaScript URLs added by this plugin"""


@hookspec
def extra_body_script(
    template, database, table, columns, view_name, request, datasette
):
    """Extra JavaScript code to be included in <script> at bottom of body"""


@hookspec
def extra_template_vars(
    template, database, table, columns, view_name, request, datasette
):
    """Extra template variables to be made available to the template - can return dict or callable or awaitable"""


@hookspec
def publish_subcommand(publish):
    """Subcommands for 'datasette publish'"""


@hookspec(firstresult=True)
def render_cell(value, column, table, database, datasette):
    """Customize rendering of HTML table cell values"""


@hookspec
def register_output_renderer(datasette):
    """Register a renderer to output data in a different format"""


@hookspec
def register_facet_classes():
    """Register Facet subclasses"""


@hookspec
def register_routes():
    """Register URL routes: return a list of (regex, view_function) pairs"""


@hookspec
def actor_from_request(datasette, request):
    """Return an actor dictionary based on the incoming request"""


@hookspec
def permission_allowed(datasette, actor, action, resource):
    """Check if actor is allowed to perfom this action - return True, False or None"""


@hookspec
def canned_queries(datasette, database, actor):
    """Return a dictonary of canned query definitions or an awaitable function that returns them"""


@hookspec
def register_magic_parameters(datasette):
    """Return a list of (name, function) magic parameter functions"""


@hookspec
def forbidden(datasette, request, message):
    """Custom response for a 403 forbidden error"""


@hookspec
def menu_links(datasette, actor):
    """Links for the navigation menu"""


@hookspec
def table_actions(datasette, actor, database, table):
    """Links for the table actions menu"""


@hookspec
def database_actions(datasette, actor, database):
    """Links for the database actions menu"""
