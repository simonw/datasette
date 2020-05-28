from datasette import hookimpl
from functools import wraps
import jinja2
import json


@hookimpl
def extra_js_urls():
    return [
        {"url": "https://plugin-example.com/jquery.js", "sri": "SRIHASH",},
        "https://plugin-example.com/plugin2.js",
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
    return jinja2.Markup(
        '<a data-database="{database}" href="{href}">{label}</a>'.format(
            database=database,
            href=jinja2.escape(data["href"]),
            label=jinja2.escape(data["label"] or "") or "&nbsp;",
        )
    )


@hookimpl
def extra_template_vars(template, database, table, view_name, request, datasette):
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
        async def add_x_databases_header(scope, recieve, send):
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

            await app(scope, recieve, wrapped_send)

        return add_x_databases_header

    return wrap_with_databases_header
