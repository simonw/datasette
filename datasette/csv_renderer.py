import csv
import hashlib
import io

import markupsafe

from datasette import hookimpl
from datasette.utils import path_from_row_pks
from datasette.utils.asgi import Response
from datasette.views.base import DatasetteError

# TSV uses Excel's tab-separated dialect, so it pastes cleanly into Excel and
# Google Sheets: values containing tabs, newlines or double quotes are wrapped
# in double quotes, with any double quotes inside them doubled.
FORMATS = {
    # extension: (csv module dialect, content type used for downloads)
    "csv": ("excel", "text/csv; charset=utf-8"),
    "tsv": ("excel-tab", "text/tab-separated-values; charset=utf-8"),
}


def _blob_url(datasette, request, format_, database, table, pks, columns, row, column):
    "URL that serves the binary content of this cell"
    cell = row[columns.index(column)]
    # If this is a table page with a way to identify the row, use
    # .urls.row_blob(). Views usually have no rowid, so they use the
    # content hash.
    if table and (pks or "rowid" in columns):
        return datasette.absolute_url(
            request,
            datasette.urls.row_blob(
                database, table, path_from_row_pks(row, pks, not pks), column
            ),
        )
    # Otherwise generate URL for this query
    return datasette.absolute_url(
        request,
        datasette.urls.modify(
            request,
            format="blob",
            add={
                "_blob_column": column,
                "_blob_hash": hashlib.sha256(cell).hexdigest(),
            },
        ),
    )


def render_delimited(
    format_, datasette, request, columns, row_stream, database, table, page, error
):
    if error:
        raise DatasetteError(error, title="Invalid SQL", status=400)

    dialect, download_content_type = FORMATS[format_]
    pks = page.get("primary_keys") or []
    # Columns expanded using ?_labels= get an additional column_label heading
    expanded_columns = set(page.get("expanded_columns") or [])
    headings = []
    for column in columns:
        headings.append(column)
        if column in expanded_columns:
            headings.append(f"{column}_label")

    def cells(row):
        labels = getattr(row, "labels", {})
        for column, cell in zip(columns, row):
            if isinstance(cell, bytes):
                cell = _blob_url(
                    datasette,
                    request,
                    format_,
                    database,
                    table,
                    pks,
                    columns,
                    row,
                    column,
                )
            if column not in expanded_columns:
                yield cell
            elif cell is None:
                yield ""
                yield ""
            elif column in labels:
                yield cell
                yield labels[column]
            else:
                yield cell
                yield ""

    # ?_trace=1 wraps the output in HTML so the trace can be shown below it
    trace = request.args.get("_trace")

    async def body():
        buffer = io.StringIO()
        writer = csv.writer(buffer, dialect=dialect)

        def written():
            value = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
            return str(markupsafe.escape(value)) if trace else value

        if trace:
            yield (
                f"<html><head><title>{format_.upper()} debug</title></head>"
                '<body><textarea style="width: 90%; height: 70vh">'
            )
        if request.args.get("_header") != "off":
            writer.writerow(headings)
        # Send one chunk for each page of rows
        async for rows in row_stream.pages():
            for row in rows:
                writer.writerow(cells(row))
            yield written()
        if trace:
            yield "</textarea></body></html>"

    if trace:
        content_type = "text/html; charset=utf-8"
    elif request.args.get("_dl"):
        content_type = download_content_type
    else:
        content_type = "text/plain; charset=utf-8"
    return Response.stream(body(), content_type=content_type)


def render_csv(datasette, request, columns, row_stream, database, table, page, error):
    return render_delimited(
        "csv", datasette, request, columns, row_stream, database, table, page, error
    )


def render_tsv(datasette, request, columns, row_stream, database, table, page, error):
    return render_delimited(
        "tsv", datasette, request, columns, row_stream, database, table, page, error
    )


# tryfirst so that a plugin registering one of these extensions replaces it
@hookimpl(tryfirst=True)
def register_output_renderer():
    return [
        {
            "extension": "tsv",
            "render": render_tsv,
            "extras": ["expanded_columns"],
            "label": "TSV",
            "description": "Tab-separated values, for pasting into Excel or Google Sheets",
        },
        {
            "extension": "csv",
            "render": render_csv,
            "extras": ["expanded_columns"],
            "label": "CSV",
            "description": "Comma-separated values, for spreadsheets and other tools",
        },
    ]
