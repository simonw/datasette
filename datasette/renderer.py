import json
from datasette.utils import (
    value_as_boolean,
    remove_infinites,
    CustomJSONEncoder,
    path_from_row_pks,
    sqlite3,
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
                except (TypeError, ValueError) as e:
                    pass
            new_row.append(value)
        new_rows.append(new_row)
    return new_rows


def json_renderer(request, args, data, error, truncated=None):
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
    # if there's an error, ignore the shape entirely
    data["ok"] = True
    if error:
        shape = "objects"
        status_code = 400
        data["error"] = error
        data["ok"] = False

    if truncated is not None:
        data["truncated"] = truncated

    if shape == "arrayfirst":
        if not data["rows"]:
            data = []
        elif isinstance(data["rows"][0], sqlite3.Row):
            data = [row[0] for row in data["rows"]]
        else:
            assert isinstance(data["rows"][0], dict)
            data = [next(iter(row.values())) for row in data["rows"]]
    elif shape in ("objects", "object", "array"):
        columns = data.get("columns")
        rows = data.get("rows")
        if rows and columns:
            data["rows"] = [dict(zip(columns, row)) for row in rows]
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
                data = {"ok": False, "error": shape_error}
        elif shape == "array":
            data = data["rows"]

    elif shape == "arrays":
        if not data["rows"]:
            pass
        elif isinstance(data["rows"][0], sqlite3.Row):
            data["rows"] = [list(row) for row in data["rows"]]
        else:
            data["rows"] = [list(row.values()) for row in data["rows"]]
    else:
        status_code = 400
        data = {
            "ok": False,
            "error": f"Invalid _shape: {shape}",
            "status": 400,
            "title": None,
        }

    # Don't include "columns" in output
    # https://github.com/simonw/datasette/issues/2136
    if isinstance(data, dict) and "columns" not in request.args.getlist("_extra"):
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
