from datasette import hookimpl
from datasette.utils.asgi import Response
import json


async def can_render(
    datasette, columns, rows, sql, query_name, database, table, request, view_name
):
    # We stash this on datasette so the calling unit test can see it
    datasette._can_render_saw = {
        "datasette": datasette,
        "columns": columns,
        "rows": rows,
        "sql": sql,
        "query_name": query_name,
        "database": database,
        "table": table,
        "request": request,
        "view_name": view_name,
    }
    if request.args.get("_no_can_render"):
        return False
    return True


async def render_test_all_parameters(
    datasette, columns, rows, sql, query_name, database, table, request, view_name, data
):
    headers = {}
    for custom_header in request.args.getlist("header"):
        key, value = custom_header.split(":")
        headers[key] = value
    result = await datasette.databases["fixtures"].execute("select 1 + 1")
    return {
        "body": json.dumps(
            {
                "datasette": datasette,
                "columns": columns,
                "rows": rows,
                "sql": sql,
                "query_name": query_name,
                "database": database,
                "table": table,
                "request": request,
                "view_name": view_name,
                "1+1": result.first()[0],
            },
            default=repr,
        ),
        "content_type": request.args.get("content_type", "text/plain"),
        "status_code": int(request.args.get("status_code", 200)),
        "headers": headers,
    }


def render_test_no_parameters():
    return {"body": "Hello"}


async def render_response(request):
    if request.args.get("_broken"):
        return "this should break"
    return Response.json({"this_is": "json"})


@hookimpl
def register_output_renderer(datasette):
    return [
        {
            "extension": "testall",
            "render": render_test_all_parameters,
            "can_render": can_render,
        },
        {"extension": "testnone", "callback": render_test_no_parameters},
        {"extension": "testresponse", "render": render_response},
    ]
