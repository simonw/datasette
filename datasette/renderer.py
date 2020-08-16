import json
from datasette.utils import (
    value_as_boolean,
    remove_infinites,
    CustomJSONEncoder,
    path_from_row_pks,
)


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
                    print(e)
                    pass
            new_row.append(value)
        new_rows.append(new_row)
    return new_rows


def json_renderer(args, data, view_name):
    """ Render a response as JSON """
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
    shape = args.get("_shape", "arrays")
    if shape == "arrayfirst":
        data = [row[0] for row in data["rows"]]
    elif shape in ("objects", "object", "array"):
        columns = data.get("columns")
        rows = data.get("rows")
        if rows and columns:
            data["rows"] = [dict(zip(columns, row)) for row in rows]
        if shape == "object":
            error = None
            if "primary_keys" not in data:
                error = "_shape=object is only available on tables"
            else:
                pks = data["primary_keys"]
                if not pks:
                    error = (
                        "_shape=object not available for tables with no primary keys"
                    )
                else:
                    object_rows = {}
                    for row in data["rows"]:
                        pk_string = path_from_row_pks(row, pks, not pks)
                        object_rows[pk_string] = row
                    data = object_rows
            if error:
                data = {"ok": False, "error": error}
        elif shape == "array":
            data = data["rows"]
    elif shape == "arrays":
        pass
    else:
        status_code = 400
        data = {
            "ok": False,
            "error": "Invalid _shape: {}".format(shape),
            "status": 400,
            "title": None,
        }
    # Handle _nl option for _shape=array
    nl = args.get("_nl", "")
    if nl and shape == "array":
        body = "\n".join(json.dumps(item, cls=CustomJSONEncoder) for item in data)
        content_type = "text/plain"
    else:
        body = json.dumps(data, cls=CustomJSONEncoder)
        content_type = "application/json; charset=utf-8"
    return {"body": body, "status_code": status_code, "content_type": content_type}
