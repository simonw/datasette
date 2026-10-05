import json

from datasette.extras import extra_names_from_request
from datasette.utils import (
    CustomJSONEncoder,
    error_body,
    path_from_row_pks,
    remove_infinites,
    value_as_boolean,
)
from datasette.utils.asgi import Response


def convert_specific_columns_to_json(rows, columns, json_cols):
    json_cols = set(json_cols)
    if not json_cols.intersection(columns):
        return rows
    new_rows = []
    for row in rows:
        new_row = []
        for value, column in zip(row, columns):
            if column in json_cols:
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    pass
            new_row.append(value)
        new_rows.append(new_row)
    return new_rows


def json_renderer(request, args, data, error, truncated=None, view_name=None):
    """Render a response as JSON"""
    status_code = 200

    # Handle the _json= parameter which may modify data["rows"]
    json_cols = []
    if "_json" in args:
        json_cols = args.getlist("_json")
    if json_cols and "rows" in data and "columns" in data:
        data["rows"] = convert_specific_columns_to_json(
            data["rows"], data["columns"], json_cols
        )

    # unless _json_infinity=1 requested, replace infinity with None
    if "rows" in data and not value_as_boolean(args.get("_json_infinity", "0")):
        data["rows"] = [remove_infinites(row) for row in data["rows"]]

    # Deal with the _shape option
    shape = args.get("_shape", "objects")
    # Row pages return {"row": {...}} unless a _shape is requested
    single_row = view_name == "row" and "_shape" not in args
    # if there's an error, ignore the shape entirely
    data["ok"] = True
    if error:
        shape = "objects"
        single_row = False
        status_code = 400
        data.update(error_body(error, status_code))

    if truncated is not None:
        data["truncated"] = truncated
    if shape == "arrayfirst":
        # Rows can be dicts, sqlite3.Row or lists (from remove_infinites)
        data = [
            next(iter(row.values())) if isinstance(row, dict) else row[0]
            for row in data["rows"]
        ]
    elif shape in ("objects", "object", "array"):
        columns = data.get("columns")
        rows = data.get("rows")
        if rows and columns and not isinstance(rows[0], dict):
            data["rows"] = [dict(zip(columns, row)) for row in rows]
        if single_row and "rows" in data:
            # Swap "rows" for "row", keeping its position in the output
            row = data["rows"][0] if data["rows"] else None
            data = dict(
                ("row", row) if key == "rows" else (key, value)
                for key, value in data.items()
            )
        if shape == "object":
            shape_error = None
            if "primary_keys" not in data:
                shape_error = "_shape=object is only available on tables"
            else:
                pks = data["primary_keys"]
                if not pks:
                    shape_error = (
                        "_shape=object not available for tables with no primary keys"
                    )
                else:
                    object_rows = {}
                    for row in data["rows"]:
                        pk_string = path_from_row_pks(row, pks, not pks)
                        object_rows[pk_string] = row
                    data = object_rows
            if shape_error:
                status_code = 400
                data = error_body(shape_error, status_code)
        elif shape == "array":
            data = data["rows"]

    elif shape == "arrays":
        data["rows"] = [
            list(row.values()) if isinstance(row, dict) else list(row)
            for row in data["rows"]
        ]
    else:
        status_code = 400
        data = error_body(f"Invalid _shape: {shape}", status_code)

    # Don't include "columns" in output
    # https://github.com/simonw/datasette/issues/2136
    if isinstance(data, dict) and "columns" not in extra_names_from_request(request):
        data.pop("columns", None)

    # Handle _nl option for _shape=array
    nl = args.get("_nl", "")
    if nl and shape == "array":
        body = "\n".join(json.dumps(item, cls=CustomJSONEncoder) for item in data)
        content_type = "text/plain"
    else:
        body = json.dumps(data, cls=CustomJSONEncoder)
        content_type = "application/json; charset=utf-8"
    headers = {}
    return Response(
        body, status=status_code, headers=headers, content_type=content_type
    )
