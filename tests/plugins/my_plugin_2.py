from datasette import hookimpl
from datasette.utils.asgi import Response
from functools import wraps
import markupsafe
import json


@hookimpl
def extra_js_urls():
    return [
        {
            "url": "https://plugin-example.datasette.io/jquery.js",
            "sri": "SRIHASH",
        },
        "https://plugin-example.datasette.io/plugin2.js",
    ]


@hookimpl
def render_cell(value, database):
    # Render {"href": "...", "label": "..."} as link
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped.startswith("{") and stripped.endswith("}"):
        return None
    try:
        data = json.loads(value)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if set(data.keys()) != {"href", "label"}:
        return None
    href = data["href"]
    if not (
        href.startswith("/")
        or href.startswith("http://")
        or href.startswith("https://")
    ):
        return None
    return markupsafe.Markup(
        '<a data-database="{database}" href="{href}">{label}</a>'.format(
            database=database,
            href=markupsafe.escape(data["href"]),
            label=markupsafe.escape(data["label"] or "") or "&nbsp;",
        )
    )


@hookimpl
def extra_template_vars(template, database, table, view_name, request, datasette):
    # This helps unit tests that want to run assertions against the request object:
    datasette._last_request = request

    async def query_database(sql):
        first_db = list(datasette.databases.keys())[0]
        return (await datasette.execute(first_db, sql)).rows[0][0]

    async def inner():
        return {
            "extra_template_vars_from_awaitable": json.dumps(
                {
                    "template": template,
                    "scope_path": request.scope["path"] if request else None,
                    "awaitable": True,
                },
                default=lambda b: b.decode("utf8"),
            ),
            "query_database": query_database,
        }

    return inner


@hookimpl
def asgi_wrapper(datasette):
    def wrap_with_databases_header(app):
        @wraps(app)
        async def add_x_databases_header(scope, receive, send):
            async def wrapped_send(event):
                if event["type"] == "http.response.start":
                    original_headers = event.get("headers") or []
                    event = {
                        "type": event["type"],
                        "status": event["status"],
                        "headers": original_headers
                        + [
                            [
                                b"x-databases",
                                ", ".join(datasette.databases.keys()).encode("utf-8"),
                            ]
                        ],
                    }
                await send(event)

            await app(scope, receive, wrapped_send)

        return add_x_databases_header

    return wrap_with_databases_header


@hookimpl
def actor_from_request(datasette, request):
    async def inner():
        if request.args.get("_bot2"):
            result = await datasette.get_database().execute("select 1 + 1")
            return {"id": "bot2", "1+1": result.first()[0]}
        else:
            return None

    return inner


@hookimpl
def prepare_jinja2_environment(env, datasette):
    env.filters["format_numeric"] = lambda s: f"{float(s):,.0f}"
    env.filters["to_hello"] = lambda s: datasette._HELLO


@hookimpl
def startup(datasette):
    async def inner():
        # Run against _internal so tests that use the ds_client fixture
        # (which has no databases yet on startup) do not fail:
        internal_db = datasette.get_internal_database()
        result = await internal_db.execute("select 1 + 1")
        datasette._startup_hook_calculation = result.first()[0]

    return inner


@hookimpl
def canned_queries(datasette, database):
    async def inner():
        return {
            "from_async_hook": "select {}".format(
                (
                    await datasette.get_database(database).execute("select 1 + 1")
                ).first()[0]
            )
        }

    return inner


@hookimpl(trylast=True)
def menu_links(datasette, actor):
    async def inner():
        if actor:
            return [{"href": datasette.urls.instance(), "label": "Hello 2"}]

    return inner


@hookimpl
def table_actions(datasette, database, table, actor, request):
    async def inner():
        if actor:
            label = "From async"
            if request.args.get("_hello"):
                label += " " + request.args["_hello"]
            return [{"href": datasette.urls.instance(), "label": label}]

    return inner


@hookimpl
def register_routes(datasette):
    config = datasette.plugin_config("register-route-demo")
    if not config:
        return
    path = config["path"]

    def new_table(request):
        return Response.text("/db/table: {}".format(sorted(request.url_vars.items())))

    return [
        (r"/{}/$".format(path), lambda: Response.text(path.upper())),
        # Also serves to demonstrate over-ride of default paths:
        (r"/(?P<db_name>[^/]+)/(?P<table_and_format>[^/]+?$)", new_table),
    ]


@hookimpl
def handle_exception(datasette, request, exception):
    datasette._exception_hook_fired = (request, exception)
    if request.args.get("_custom_error"):
        return Response.text("_custom_error")
    elif request.args.get("_custom_error_async"):

        async def inner():
            return Response.text("_custom_error_async")

        return inner


@hookimpl(specname="register_routes")
def register_triger_error():
    return ((r"/trigger-error", lambda: 1 / 0),)
