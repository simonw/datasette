from pluggy import HookimplMarker
from pluggy import HookspecMarker

hookspec = HookspecMarker("datasette")
hookimpl = HookimplMarker("datasette")


@hookspec
def startup(datasette):
    """Fires directly after Datasette first starts running"""


@hookspec
def get_metadata(datasette, key, database, table):
    """Return metadata to be merged into Datasette's metadata dictionary"""


@hookspec
def asgi_wrapper(datasette):
    """Returns an ASGI middleware callable to wrap our ASGI application with"""


@hookspec
def prepare_connection(conn, database, datasette):
    """Modify SQLite connection in some way e.g. register custom SQL functions"""


@hookspec
def prepare_jinja2_environment(env, datasette):
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


@hookspec
def render_cell(row, value, column, table, database, datasette, request):
    """Customize rendering of HTML table cell values"""


@hookspec
def register_output_renderer(datasette):
    """Register a renderer to output data in a different format"""


@hookspec
def register_facet_classes():
    """Register Facet subclasses"""


@hookspec
def register_permissions(datasette):
    """Register permissions: returns a list of datasette.permission.Permission named tuples"""


@hookspec
def register_routes(datasette):
    """Register URL routes: return a list of (regex, view_function) pairs"""


@hookspec
def register_commands(cli):
    """Register additional CLI commands, e.g. 'datasette mycommand ...'"""


@hookspec
def actor_from_request(datasette, request):
    """Return an actor dictionary based on the incoming request"""


@hookspec(firstresult=True)
def actors_from_ids(datasette, actor_ids):
    """Returns a dictionary mapping those IDs to actor dictionaries"""


@hookspec
def jinja2_environment_from_request(datasette, request, env):
    """Return a Jinja2 environment based on the incoming request"""


@hookspec
def filters_from_request(request, database, table, datasette):
    """
    Return datasette.filters.FilterArguments(
        where_clauses=[str, str, str],
        params={},
        human_descriptions=[str, str, str],
        extra_context={}
    ) based on the request"""


@hookspec
def permission_allowed(datasette, actor, action, resource):
    """Check if actor is allowed to perform this action - return True, False or None"""


@hookspec
def canned_queries(datasette, database, actor):
    """Return a dictionary of canned query definitions or an awaitable function that returns them"""


@hookspec
def register_magic_parameters(datasette):
    """Return a list of (name, function) magic parameter functions"""


@hookspec
def forbidden(datasette, request, message):
    """Custom response for a 403 forbidden error"""


@hookspec
def menu_links(datasette, actor, request):
    """Links for the navigation menu"""


@hookspec
def table_actions(datasette, actor, database, table, request):
    """Links for the table actions menu"""


@hookspec
def database_actions(datasette, actor, database, request):
    """Links for the database actions menu"""


@hookspec
def skip_csrf(datasette, scope):
    """Mechanism for skipping CSRF checks for certain requests"""


@hookspec
def handle_exception(datasette, request, exception):
    """Handle an uncaught exception. Can return a Response or None."""


@hookspec
def track_event(datasette, event):
    """Respond to an event tracked by Datasette"""


@hookspec
def register_events(datasette):
    """Return a list of Event subclasses to use with track_event()"""


@hookspec
def top_homepage(datasette, request):
    """HTML to include at the top of the homepage"""


@hookspec
def top_database(datasette, request, database):
    """HTML to include at the top of the database page"""


@hookspec
def top_table(datasette, request, database, table):
    """HTML to include at the top of the table page"""


@hookspec
def top_row(datasette, request, database, table, row):
    """HTML to include at the top of the row page"""


@hookspec
def top_query(datasette, request, database, sql):
    """HTML to include at the top of the query results page"""


@hookspec
def top_canned_query(datasette, request, database, query_name):
    """HTML to include at the top of the canned query page"""
