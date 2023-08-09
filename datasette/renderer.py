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


def json_renderer(args, rows, columns, internal_data, error, truncated=None):
    """Render a response as JSON"""
    status_code = 200

    # Turn rows into a list of lists
    row_lists = [list(row) for row in rows]
    row_dicts = None

    # Handle the _json= parameter which may modify the rows
    json_cols = []
    if "_json" in args:
        json_cols = args.getlist("_json")
    if json_cols:
        row_lists = convert_specific_columns_to_json(row_lists, columns, json_cols)

    # unless _json_infinity=1 requested, replace infinity with None
    if not value_as_boolean(args.get("_json_infinity", "0")):
        row_lists = [remove_infinites(row) for row in row_lists]

    nl = args.get("_nl", "")

    if internal_data:
        return_data = internal_data
    else:
        return_data = {"ok": True}

    # Deal with the _shape option
    shape = args.get("_shape", "objects")
    # if there's an error, ignore the shape entirely
    if error:
        shape = "objects"
        status_code = 400
        return_data["ok"] = False
        return_data["error"] = error

    # return_data["rows"] is either lists or dicts
    if shape in ("objects", "object", "array"):
        row_dicts = [dict(zip(columns, row)) for row in row_lists]
        return_data["rows"] = row_dicts
    else:
        return_data["rows"] = row_lists

    if truncated is not None:
        return_data["truncated"] = truncated

    if shape == "objects":
        pass
    elif shape == "arrayfirst":
        # Special case, return array as root object
        return_data = [next(iter(row)) for row in row_lists]
    elif shape == "object":
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
                object_row = {}
                for row in return_data["rows"]:
                    pk_string = path_from_row_pks(row, pks, not pks)
                    object_row[pk_string] = row
                return_data = object_row
        if shape_error:
            return_data = {"ok": False, "error": shape_error}
    elif shape == "array":
        # Return an array of objects
        if nl:
            body = "\n".join(
                json.dumps(item, cls=CustomJSONEncoder) for item in row_dicts
            )
            content_type = "text/plain"
        else:
            body = json.dumps(row_dicts, cls=CustomJSONEncoder)
            content_type = "application/json; charset=utf-8"
        return Response(body, status=status_code, content_type=content_type)

    elif shape == "arrays":
        return_data["rows"] = row_lists
    else:
        status_code = 400
        data = {
            "ok": False,
            "error": f"Invalid _shape: {shape}",
            "status": 400,
            "title": None,
        }
    return Response(
        json.dumps(return_data, cls=CustomJSONEncoder),
        status=status_code,
        content_type="application/json; charset=utf-8",
    )
