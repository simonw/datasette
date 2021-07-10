from datasette import hookimpl
from datasette.utils.asgi import Response, BadRequest
from datasette.utils import to_css_class
import hashlib

_BLOB_COLUMN = "_blob_column"
_BLOB_HASH = "_blob_hash"


async def render_blob(datasette, database, rows, columns, request, table, view_name):
    if _BLOB_COLUMN not in request.args:
        raise BadRequest(f"?{_BLOB_COLUMN}= is required")
    blob_column = request.args[_BLOB_COLUMN]
    if blob_column not in columns:
        raise BadRequest(f"{blob_column} is not a valid column")

    # If ?_blob_hash= provided, use that to select the row - otherwise use first row
    blob_hash = None
    if _BLOB_HASH in request.args:
        blob_hash = request.args[_BLOB_HASH]
        for row in rows:
            value = row[blob_column]
            if hashlib.sha256(value).hexdigest() == blob_hash:
                break
        else:
            # Loop did not break
            raise BadRequest(
                "Link has expired - the requested binary content has changed or could not be found."
            )
    else:
        row = rows[0]

    value = row[blob_column]
    filename_bits = []
    if table:
        filename_bits.append(to_css_class(table))
    if "pk_path" in request.url_vars:
        filename_bits.append(request.url_vars["pk_path"])
    filename_bits.append(to_css_class(blob_column))
    if blob_hash:
        filename_bits.append(blob_hash[:6])
    filename = "-".join(filename_bits) + ".blob"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    return Response(
        body=value or b"",
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
