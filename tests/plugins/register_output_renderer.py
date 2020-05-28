from datasette import hookimpl
import json


async def render_test_all_parameters(
    datasette, columns, rows, sql, query_name, database, table, request, view_name, data
):
    headers = {}
    for custom_header in request.args.getlist("header") or []:
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


@hookimpl
def register_output_renderer(datasette):
    return [
        {"extension": "testall", "render": render_test_all_parameters},
        {"extension": "testnone", "callback": render_test_no_parameters},
    ]
