import asyncio
from datasette import hookimpl
from datasette.facets import Facet
from datasette import tracer
from datasette.permissions import Action
from datasette.resources import DatabaseResource
from datasette.utils import path_with_added_args
from datasette.utils.asgi import asgi_send_json, Response
import base64
import json
import urllib.parse


@hookimpl
def prepare_connection(conn, database, datasette):
    def convert_units(amount, from_, to_):
        """select convert_units(100, 'm', 'ft');"""
        # Convert meters to feet
        if from_ == "m" and to_ == "ft":
            return amount * 3.28084
        # Convert feet to meters
        if from_ == "ft" and to_ == "m":
            return amount / 3.28084
        assert False, "Unsupported conversion"

    conn.create_function("convert_units", 3, convert_units)

    def prepare_connection_args():
        return 'database={}, datasette.plugin_config("name-of-plugin")={}'.format(
            database, datasette.plugin_config("name-of-plugin")
        )

    conn.create_function("prepare_connection_args", 0, prepare_connection_args)


@hookimpl
def extra_css_urls(template, database, table, view_name, columns, request, datasette):
    async def inner():
        return [
            "https://plugin-example.datasette.io/{}/extra-css-urls-demo.css".format(
                base64.b64encode(
                    json.dumps(
                        {
                            "template": template,
                            "database": database,
                            "table": table,
                            "view_name": view_name,
                            "request_path": (
                                request.path if request is not None else None
                            ),
                            "added": (
                                await datasette.get_database().execute("select 3 * 5")
                            ).first()[0],
                            "columns": columns,
                        }
                    ).encode("utf8")
                ).decode("utf8")
            )
        ]

    return inner


@hookimpl
def extra_js_urls():
    return [
        {
            "url": "https://plugin-example.datasette.io/jquery.js",
            "sri": "SRIHASH",
        },
        "https://plugin-example.datasette.io/plugin1.js",
        {"url": "https://plugin-example.datasette.io/plugin.module.js", "module": True},
    ]


@hookimpl
def extra_body_script(
    template, database, table, view_name, columns, request, datasette
):
    async def inner():
        script = "var extra_body_script = {};".format(
            json.dumps(
                {
                    "template": template,
                    "database": database,
                    "table": table,
                    "config": datasette.plugin_config(
                        "name-of-plugin",
                        database=database,
                        table=table,
                    ),
                    "view_name": view_name,
                    "request_path": request.path if request is not None else None,
                    "added": (
                        await datasette.get_database().execute("select 3 * 5")
                    ).first()[0],
                    "columns": columns,
                }
            )
        )
        return {"script": script, "module": True}

    return inner


@hookimpl
def render_cell(row, value, column, table, database, datasette, request):
    async def inner():
        # Render some debug output in cell with value RENDER_CELL_DEMO
        if value == "RENDER_CELL_DEMO":
            data = {
                "row": dict(row),
                "column": column,
                "table": table,
                "database": database,
                "config": datasette.plugin_config(
                    "name-of-plugin",
                    database=database,
                    table=table,
                ),
            }
            if request.args.get("_render_cell_extra"):
                data["render_cell_extra"] = 1
            return json.dumps(data)
        elif value == "RENDER_CELL_ASYNC":
            return (
                await datasette.get_database(database).execute(
                    "select 'RENDER_CELL_ASYNC_RESULT'"
                )
            ).single_value()

    return inner


@hookimpl
def extra_template_vars(
    template, database, table, view_name, columns, request, datasette
):
    return {
        "extra_template_vars": json.dumps(
            {
                "template": template,
                "scope_path": request.scope["path"] if request else None,
                "columns": columns,
            },
            default=lambda b: b.decode("utf8"),
        )
    }


@hookimpl
def prepare_jinja2_environment(env, datasette):
    async def select_times_three(s):
        db = datasette.get_database()
        return (await db.execute("select 3 * ?", [int(s)])).first()[0]

    async def inner():
        env.filters["select_times_three"] = select_times_three

    return inner


@hookimpl
def register_facet_classes():
    return [DummyFacet]


class DummyFacet(Facet):
    type = "dummy"

    async def suggest(self):
        columns = await self.get_columns(self.sql, self.params)
        return (
            [
                {
                    "name": column,
                    "toggle_url": self.ds.absolute_url(
                        self.request,
                        path_with_added_args(self.request, {"_facet_dummy": column}),
                    ),
                    "type": "dummy",
                }
                for column in columns
            ]
            if self.request.args.get("_dummy_facet")
            else []
        )

    async def facet_results(self):
        facet_results = {}
        facets_timed_out = []
        return facet_results, facets_timed_out


@hookimpl
def actor_from_request(datasette, request):
    if request.args.get("_bot"):
        return {"id": "bot"}
    else:
        return None


@hookimpl
def asgi_wrapper():
    def wrap(app):
        async def maybe_set_actor_in_scope(scope, receive, send):
            if b"_actor_in_scope" in scope.get("query_string", b""):
                scope = dict(scope, actor={"id": "from-scope"})
                print(scope)
            await app(scope, receive, send)

        return maybe_set_actor_in_scope

    return wrap


@hookimpl
def register_routes():
    async def one(datasette):
        return Response.text(
            (await datasette.get_database().execute("select 1 + 1")).first()[0]
        )

    async def two(request):
        name = request.url_vars["name"]
        greeting = request.args.get("greeting")
        return Response.text(f"{greeting} {name}")

    async def three(scope, send):
        await asgi_send_json(
            send, {"hello": "world"}, status=200, headers={"x-three": "1"}
        )

    async def post(request):
        if request.method == "GET":
            return Response.html(request.scope["csrftoken"]())
        else:
            return Response.json(await request.post_vars())

    async def csrftoken_form(request, datasette):
        return Response.html(
            await datasette.render_template("csrftoken_form.html", request=request)
        )

    def not_async():
        return Response.html("This was not async")

    def add_message(datasette, request):
        datasette.add_message(request, "Hello from messages")
        return Response.html("Added message")

    async def render_message(datasette, request):
        return Response.html(
            await datasette.render_template("render_message.html", request=request)
        )

    def login_as_root(datasette, request):
        # Mainly for the latest.datasette.io demo
        if request.method == "POST":
            response = Response.redirect("/")
            datasette.set_actor_cookie(response, {"id": "root"})
            return response
        return Response.html(
            """
            <form action="{}" method="POST">
                <p>
                    <input type="hidden" name="csrftoken" value="{}">
                    <input type="submit"
                      value="Sign in as root user"
                      style="font-size: 2em; padding: 0.1em 0.5em;">
                </p>
            </form>
        """.format(
                request.path, request.scope["csrftoken"]()
            )
        )

    def asgi_scope(scope):
        return Response.json(scope, default=repr)

    async def parallel_queries(datasette):
        db = datasette.get_database()
        with tracer.trace_child_tasks():
            one, two = await asyncio.gather(
                db.execute("select coalesce(sleep(0.1), 1)"),
                db.execute("select coalesce(sleep(0.1), 2)"),
            )
        return Response.json({"one": one.single_value(), "two": two.single_value()})

    return [
        (r"/one/$", one),
        (r"/two/(?P<name>.*)$", two),
        (r"/three/$", three),
        (r"/post/$", post),
        (r"/csrftoken-form/$", csrftoken_form),
        (r"/login-as-root$", login_as_root),
        (r"/not-async/$", not_async),
        (r"/add-message/$", add_message),
        (r"/render-message/$", render_message),
        (r"/asgi-scope$", asgi_scope),
        (r"/parallel-queries$", parallel_queries),
    ]


@hookimpl
def startup(datasette):
    datasette._startup_hook_fired = True

    # And test some import shortcuts too
    from datasette import Response
    from datasette import Forbidden
    from datasette import NotFound
    from datasette import hookimpl
    from datasette import actor_matches_allow

    _ = (Response, Forbidden, NotFound, hookimpl, actor_matches_allow)


@hookimpl
def canned_queries(datasette, database, actor):
    return {"from_hook": f"select 1, '{actor['id'] if actor else 'null'}' as actor_id"}


@hookimpl
def register_magic_parameters():
    from uuid import uuid4

    def uuid(key, request):
        if key == "new":
            return str(uuid4())
        else:
            raise KeyError

    def request(key, request):
        if key == "http_version":
            return request.scope["http_version"]
        else:
            raise KeyError

    async def asyncrequest(key, request):
        return key

    return [
        ("request", request),
        ("uuid", uuid),
        ("asyncrequest", asyncrequest),
    ]


@hookimpl
def forbidden(datasette, request, message):
    datasette._last_forbidden_message = message
    if request.path == "/data2":
        return Response.redirect("/login?message=" + message)


@hookimpl
def menu_links(datasette, actor, request):
    if actor:
        label = "Hello"
        if request.args.get("_hello"):
            label += ", " + request.args["_hello"]
        return [{"href": datasette.urls.instance(), "label": label}]


@hookimpl
def table_actions(datasette, database, table, actor):
    if actor:
        return [
            {
                "href": datasette.urls.instance(),
                "label": f"Database: {database}",
            },
            {"href": datasette.urls.instance(), "label": f"Table: {table}"},
        ]


@hookimpl
def view_actions(datasette, database, view, actor):
    if actor:
        return [
            {
                "href": datasette.urls.instance(),
                "label": f"Database: {database}",
            },
            {"href": datasette.urls.instance(), "label": f"View: {view}"},
        ]


@hookimpl
def query_actions(datasette, database, query_name, sql):
    # Don't explain an explain
    if sql.lower().startswith("explain"):
        return
    return [
        {
            "href": datasette.urls.database(database)
            + "/-/query"
            + "?"
            + urllib.parse.urlencode(
                {
                    "sql": "explain " + sql,
                }
            ),
            "label": "Explain this query",
            "description": "Runs a SQLite explain",
        },
    ]


@hookimpl
def row_actions(datasette, database, table, actor, row):
    if actor:
        return [
            {
                "href": datasette.urls.instance(),
                "label": f"Row details for {actor['id']}",
                "description": json.dumps(dict(row), default=repr),
            },
        ]


@hookimpl
def database_actions(datasette, database, actor, request):
    if actor:
        label = f"Database: {database}"
        if request.args.get("_hello"):
            label += " - " + request.args["_hello"]
        return [
            {
                "href": datasette.urls.instance(),
                "label": label,
            }
        ]


@hookimpl
def homepage_actions(datasette, actor, request):
    if actor:
        label = f"Custom homepage for: {actor['id']}"
        return [
            {
                "href": datasette.urls.path("/-/custom-homepage"),
                "label": label,
            }
        ]


@hookimpl
def skip_csrf(scope):
    return scope["path"] == "/skip-csrf"


@hookimpl
def register_actions(datasette):
    extras_old = datasette.plugin_config("datasette-register-permissions") or {}
    extras_new = datasette.plugin_config("datasette-register-actions") or {}

    actions = [
        Action(
            name="action-from-plugin",
            abbr="ap",
            description="New action added by a plugin",
            resource_class=DatabaseResource,
        ),
        Action(
            name="view-collection",
            abbr="vc",
            description="View a collection",
            resource_class=DatabaseResource,
        ),
        # Test actions for test_hook_custom_allowed (global actions - no resource_class)
        Action(
            name="this_is_allowed",
            abbr=None,
            description=None,
        ),
        Action(
            name="this_is_denied",
            abbr=None,
            description=None,
        ),
        Action(
            name="this_is_allowed_async",
            abbr=None,
            description=None,
        ),
        Action(
            name="this_is_denied_async",
            abbr=None,
            description=None,
        ),
    ]

    # Support old-style config for backwards compatibility
    if extras_old:
        for p in extras_old["permissions"]:
            # Map old takes_database/takes_resource to new global/resource_class
            if p.get("takes_database"):
                # Has database -> DatabaseResource
                actions.append(
                    Action(
                        name=p["name"],
                        abbr=p["abbr"],
                        description=p["description"],
                        resource_class=DatabaseResource,
                    )
                )
            else:
                # No database -> global action (no resource_class)
                actions.append(
                    Action(
                        name=p["name"],
                        abbr=p["abbr"],
                        description=p["description"],
                    )
                )

    # Support new-style config
    if extras_new:
        for a in extras_new["actions"]:
            # Check if this is a global action (no resource_class specified)
            if not a.get("resource_class"):
                actions.append(
                    Action(
                        name=a["name"],
                        abbr=a["abbr"],
                        description=a["description"],
                    )
                )
            else:
                # Map string resource_class to actual class
                resource_class_map = {
                    "DatabaseResource": DatabaseResource,
                }
                resource_class = resource_class_map.get(
                    a.get("resource_class", "DatabaseResource"), DatabaseResource
                )

                actions.append(
                    Action(
                        name=a["name"],
                        abbr=a["abbr"],
                        description=a["description"],
                        resource_class=resource_class,
                    )
                )

    return actions


@hookimpl
def permission_resources_sql(datasette, actor, action):
    from datasette.permissions import PermissionSQL

    # Handle test actions used in test_hook_custom_allowed
    if action == "this_is_allowed":
        return PermissionSQL.allow(reason="test plugin allows this_is_allowed")
    elif action == "this_is_denied":
        return PermissionSQL.deny(reason="test plugin denies this_is_denied")
    elif action == "this_is_allowed_async":
        return PermissionSQL.allow(reason="test plugin allows this_is_allowed_async")
    elif action == "this_is_denied_async":
        return PermissionSQL.deny(reason="test plugin denies this_is_denied_async")
    elif action == "view-database-download":
        # Return rule based on actor's can_download permission
        if actor and actor.get("can_download"):
            return PermissionSQL.allow(reason="actor has can_download")
        else:
            return None  # No opinion
    elif action == "view-database":
        # Also grant view-database if actor has can_download (needed for download to work)
        if actor and actor.get("can_download"):
            return PermissionSQL.allow(
                reason="actor has can_download, grants view-database"
            )
        else:
            return None
    elif action in (
        "insert-row",
        "create-table",
        "drop-table",
        "delete-row",
        "update-row",
    ):
        # Special permissions for latest.datasette.io demos
        actor_id = actor.get("id") if actor else None
        if actor_id == "todomvc":
            return PermissionSQL.allow(reason=f"todomvc actor allowed for {action}")

    return None
