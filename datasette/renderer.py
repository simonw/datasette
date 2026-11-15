import json

from datasette.utils import (
    CustomJSONEncoder,
    error_body,
    path_from_row_pks,
    remove_infinites,
    value_as_boolean,
)
from datasette.utils.asgi import Response


class RowStream:
    """
    The row_stream argument passed to output renderers: an async iterator
    over the rows to render.

    This is the rows on the current page, or every row - fetched a page at
    a time - if the request asked for them all using ?_stream=1.

    Use ``async for row in row_stream`` to loop through individual rows, or
    ``async for rows in row_stream.pages()`` for a list of rows at a time.
    """

    def __init__(self, rows, fetch_next_page=None):
        self._rows = rows
        self._fetch_next_page = fetch_next_page

    async def pages(self):
        yield self._rows
        while self._fetch_next_page is not None:
            rows = await self._fetch_next_page()
            if rows is None:
                break
            yield rows

    async def __aiter__(self):
        async for rows in self.pages():
            for row in rows:
                yield row


def _load_json_value(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def convert_specific_columns_to_json(rows, columns, json_cols):
    if not rows:
        return rows
    if columns is None:
        if not isinstance(rows[0], dict):
            return rows
        columns = rows[0].keys()
    json_cols = set(json_cols)
    if not json_cols.intersection(columns):
        return rows
    new_rows = []
    for row in rows:
        if isinstance(row, dict):
            new_row = dict(row)
            for column in json_cols.intersection(row):
                new_row[column] = _load_json_value(row[column])
        else:
            new_row = [
                _load_json_value(value) if column in json_cols else value
                for value, column in zip(row, columns)
            ]
        new_rows.append(new_row)
    return new_rows


async def apply_column_type_transforms(datasette, database, table, rows):
    """
    Return rows as dictionaries, with transform_value() applied for any
    columns that have been assigned a column type.
    """
    ct_map = await datasette.get_column_types(database, table)
    transformed_rows = []
    for row in rows:
        row_dict = dict(row)
        for col_name, ct in ct_map.items():
            if col_name in row_dict:
                row_dict[col_name] = await ct.transform_value(
                    row_dict[col_name], datasette
                )
        transformed_rows.append(row_dict)
    return transformed_rows


async def json_renderer(
    datasette,
    request,
    rows,
    columns,
    page,
    error,
    database,
    table,
    view_name,
    truncated=None,
):
    """Render a response as JSON"""
    args = request.args
    status_code = 200

    if view_name == "table" and table and not error:
        # Table pages apply transform_value() from any column types
        rows = await apply_column_type_transforms(datasette, database, table, rows)

    # Everything other than the rows comes from the page dictionary
    data = {"ok": True, **page, "rows": rows}

    # Handle the _json= parameter which may modify data["rows"]
    json_cols = []
    if "_json" in args:
        json_cols = args.getlist("_json")
    object_keys = None
    pks = data.get("primary_keys")
    if (
        not error
        and args.get("_shape") == "object"
        and pks
        and any(pk in json_cols for pk in pks)
    ):
        object_keys = []
        for row in data["rows"]:
            if columns and not isinstance(row, dict):
                row = dict(zip(columns, row))
            object_keys.append(path_from_row_pks(row, pks, False))
    if json_cols:
        data["rows"] = convert_specific_columns_to_json(
            data["rows"], columns, json_cols
        )

    # unless _json_infinity=1 requested, replace infinity with None
    if not value_as_boolean(args.get("_json_infinity", "0")):
        data["rows"] = [remove_infinites(row) for row in data["rows"]]

    # Deal with the _shape option
    shape = args.get("_shape", "objects")
    # Row pages return {"row": {...}} unless a ?_shape= is requested
    single_row = view_name == "row" and "_shape" not in args
    # if there's an error, ignore the shape entirely
    if error:
        shape = "objects"
        single_row = False
        status_code = 400
        data.update(error_body(error, status_code))

    # A single row cannot be truncated
    if truncated is not None and not single_row:
        data["truncated"] = truncated
    if shape == "arrayfirst":
        if not data["rows"]:
            data = []
        elif isinstance(data["rows"][0], dict):
            data = [next(iter(row.values())) for row in data["rows"]]
        else:
            data = [row[0] for row in data["rows"]]
    elif shape in ("objects", "object", "array"):
        rows = data.get("rows")
        if rows and columns and not isinstance(rows[0], dict):
            data["rows"] = [dict(zip(columns, row)) for row in rows]
        if single_row:
            # Swap "rows" for "row", keeping its position in the output
            data = {
                ("row" if key == "rows" else key): (
                    (value[0] if value else None) if key == "rows" else value
                )
                for key, value in data.items()
            }
            # Likewise the render_cell extra becomes a single object
            if "render_cell" in data:
                cells = data["render_cell"]
                data["render_cell"] = cells[0] if cells else {}
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
                    for index, row in enumerate(data["rows"]):
                        pk_string = (
                            object_keys[index]
                            if object_keys is not None
                            else path_from_row_pks(row, pks, not pks)
                        )
                        object_rows[pk_string] = row
                    data = object_rows
            if shape_error:
                status_code = 400
                data = error_body(shape_error, status_code)
        elif shape == "array":
            data = data["rows"]

    elif shape == "arrays":
        if not data["rows"]:
            pass
        elif isinstance(data["rows"][0], dict):
            data["rows"] = [list(row.values()) for row in data["rows"]]
        else:
            data["rows"] = [list(row) for row in data["rows"]]
    else:
        status_code = 400
        data = error_body(f"Invalid _shape: {shape}", status_code)

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
