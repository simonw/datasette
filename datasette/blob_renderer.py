from datasette.utils import call_with_supported_arguments
from datasette import hookimpl
from datasette.utils.asgi import Response
from datasette.utils import to_css_class

COLUMN = "_blob_column"


async def render_blob(datasette, database, rows, request, table, view_name):
    if COLUMN not in request.args:
        return Response.html("?_blob_column= is required", status=400)
    blob_column = request.args[COLUMN]
    row = rows[0]
    if blob_column not in row.keys():
        return Response.html("_blob_column is not valid", status=400)
    value = row[blob_column]
    filename_bits = []
    if table:
        filename_bits.append(to_css_class(table))
    if "pk_path" in request.url_vars:
        filename_bits.append(request.url_vars["pk_path"])
    filename_bits.append(to_css_class(blob_column))
    filename = "-".join(filename_bits) + ".blob"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'attachment; filename="{}"'.format(filename),
    }
    return Response(
        body=value,
        status=200,
        headers=headers,
        content_type="application/binary",
    )


@hookimpl
def register_output_renderer():
    return {
        "extension": "blob",
        "render": render_blob,
        "can_render": lambda: False,
    }
