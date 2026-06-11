import asyncio
import itertools
import json
import urllib

import markupsafe

from datasette.extras import extra_names_from_request
from datasette.plugins import pm
from datasette.events import (
    AlterTableEvent,
    DropTableEvent,
    InsertRowsEvent,
    UpsertRowsEvent,
)
from datasette import tracer
from datasette.resources import DatabaseResource, TableResource
from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
    call_with_supported_arguments,
    CustomRow,
    append_querystring,
    compound_keys_after_sql,
    format_bytes,
    make_slot_function,
    tilde_encode,
    escape_sqlite,
    filters_should_redirect,
    is_url,
    path_from_row_pks,
    path_with_added_args,
    path_with_format,
    path_with_removed_args,
    path_with_replaced_args,
    to_css_class,
    truncate_url,
    urlsafe_components,
    value_as_boolean,
    InvalidSql,
    sqlite3,
)
from datasette.utils.asgi import BadRequest, Forbidden, NotFound, Response
from datasette.filters import Filters
import sqlite_utils
from dataclasses import dataclass, field, fields

from datasette.extras import ExtraScope
from . import Context, extra_field
from .base import BaseView, DatasetteError, _error, stream_csv
from .database import QueryView
from .table_extras import (
    TABLE_EXTRA_BUNDLES,
    TableExtraContext,
    resolve_table_extras,
    table_extra_registry,
)


@dataclass
class TableContext(Context):
    "The page showing the rows in a table or SQL view, e.g. /fixtures/facetable."

    template = "table.html"
    extras_scope = ExtraScope.TABLE

    # Fields resolved by registered extras - their documentation comes
    # from the description on each Extra class in table_extras.py
    actions: callable = extra_field()
    all_columns: list = extra_field()
    columns: list = extra_field()
    count: int = extra_field()
    count_sql: str = extra_field()
    custom_table_templates: list = extra_field()
    database: str = extra_field()
    database_color: str = extra_field()
    display_columns: list = extra_field()
    display_rows: list = extra_field()
    expandable_columns: list = extra_field()
    facet_results: dict = extra_field()
    facets_timed_out: list = extra_field()
    filters: Filters = extra_field()
    form_hidden_args: list = extra_field()
    human_description_en: str = extra_field()
    is_view: bool = extra_field()
    metadata: dict = extra_field()
    next_url: str = extra_field()
    primary_keys: list = extra_field()
    private: bool = extra_field()
    query: dict = extra_field()
    renderers: dict = extra_field()
    set_column_type_ui: dict = extra_field()
    sorted_facet_results: list = extra_field()
    suggested_facets: list = extra_field()
    table: str = extra_field()
    table_definition: str = extra_field()
    view_definition: str = extra_field()

    # Fields added by the view code
    ok: bool = field(
        metadata={"help": "True if the data for this page was retrieved without errors"}
    )
    next: str = field(metadata={"help": "Pagination token for the next page, or None"})
    rows: list = field(
        metadata={
            "help": "The rows for this page, as a list of dictionaries mapping column name to value"
        }
    )
    filter_columns: list = field(
        metadata={"help": "List of columns offered by the filter interface"}
    )
    supports_search: bool = field(
        metadata={"help": "True if this table has full-text search configured"}
    )
    extra_wheres_for_ui: list = field(
        metadata={
            "help": "Extra where clauses from ?_where=, with links to remove them"
        }
    )
    url_csv: str = field(metadata={"help": "URL for the CSV export of this page"})
    url_csv_path: str = field(metadata={"help": "Path portion of the CSV export URL"})
    url_csv_hidden_args: list = field(
        metadata={
            "help": "(name, value) pairs for hidden form fields used by the CSV export form"
        }
    )
    sort: str = field(metadata={"help": "Column the page is sorted by, or None"})
    sort_desc: str = field(
        metadata={"help": "Column the page is sorted by in descending order, or None"}
    )
    append_querystring: callable = field(
        metadata={
            "help": "Function that appends additional querystring arguments to a URL"
        }
    )
    path_with_replaced_args: callable = field(
        metadata={
            "help": "Function for building the current path with modified querystring arguments"
        }
    )
    fix_path: callable = field(
        metadata={"help": "Function that applies the base_url prefix to a path"}
    )
    settings: dict = field(
        metadata={"help": "Dictionary of Datasette's current settings"}
    )
    alternate_url_json: str = field(
        metadata={"help": "URL for the JSON version of this page"}
    )
    datasette_allow_facet: str = field(
        metadata={
            "help": 'The string "true" or "false" reflecting the allow_facet setting'
        }
    )
    is_sortable: bool = field(
        metadata={"help": "True if any of the displayed columns can be used to sort"}
    )
    allow_execute_sql: bool = field(
        metadata={
            "help": "True if the current actor can execute custom SQL against this database"
        }
    )
    query_ms: float = field(
        metadata={
            "help": "Time taken by the SQL queries for this page, in milliseconds"
        }
    )
    select_templates: list = field(
        metadata={
            "help": "List of template names that were considered for this page, the one used marked with an asterisk"
        }
    )
    top_table: callable = field(
        metadata={"help": "Async function rendering the top_table plugin slot"}
    )
    count_limit: int = field(
        metadata={
            "help": "The maximum number of rows Datasette will count before showing an approximation"
        }
    )


LINK_WITH_LABEL = (
    '<a href="{base_url}{database}/{table}/{link_id}">{label}</a>&nbsp;<em>{id}</em>'
)
LINK_WITH_VALUE = '<a href="{base_url}{database}/{table}/{link_id}">{id}</a>'


class Row:
    def __init__(self, cells):
        self.cells = cells

    def __iter__(self):
        return iter(self.cells)

    def __getitem__(self, key):
        for cell in self.cells:
            if cell["column"] == key:
                return cell["raw"]
        raise KeyError

    def display(self, key):
        for cell in self.cells:
            if cell["column"] == key:
                return cell["value"]
        return None

    def __str__(self):
        d = {
            key: self[key]
            for key in [
                c["column"] for c in self.cells if not c.get("is_special_link_column")
            ]
        }
        return json.dumps(d, default=repr, indent=2)


async def run_sequential(*args):
    # This used to be swappable for asyncio.gather() to run things in
    # parallel, but this lead to hard-to-debug locking issues with
    # in-memory databases: https://github.com/simonw/datasette/issues/2189
    results = []
    for fn in args:
        results.append(await fn)
    return results


def _redirect(datasette, request, path, forward_querystring=True, remove_args=None):
    if request.query_string and "?" not in path and forward_querystring:
        path = f"{path}?{request.query_string}"
    if remove_args:
        path = path_with_removed_args(request, remove_args, path=path)
    r = Response.redirect(path)
    r.headers["Link"] = f"<{path}>; rel=preload"
    if datasette.cors:
        add_cors_headers(r.headers)
    return r


async def _redirect_if_needed(datasette, request, resolved):
    # Handle ?_filter_column
    redirect_params = filters_should_redirect(request.args)
    if redirect_params:
        return _redirect(
            datasette,
            request,
            datasette.urls.path(path_with_added_args(request, redirect_params)),
            forward_querystring=False,
        )

    # If ?_sort_by_desc=on (from checkbox) redirect to _sort_desc=(_sort)
    if "_sort_by_desc" in request.args:
        return _redirect(
            datasette,
            request,
            datasette.urls.path(
                path_with_added_args(
                    request,
                    {
                        "_sort_desc": request.args.get("_sort"),
                        "_sort_by_desc": None,
                        "_sort": None,
                    },
                )
            ),
            forward_querystring=False,
        )


async def _validate_column_types(datasette, database_name, table_name, rows):
    """Validate row values against assigned column types. Returns list of error strings."""
    ct_map = await datasette.get_column_types(database_name, table_name)
    if not ct_map:
        return []
    errors = []
    for row in rows:
        for col_name, ct in ct_map.items():
            if col_name not in row:
                continue
            error = await ct.validate(row[col_name], datasette)
            if error:
                errors.append(f"{col_name}: {error}")
    return errors


async def display_columns_and_rows(
    datasette,
    database_name,
    table_name,
    description,
    rows,
    link_column=False,
    truncate_cells=0,
    sortable_columns=None,
    request=None,
):
    """Returns columns, rows for specified table - including fancy foreign key treatment"""
    sortable_columns = sortable_columns or set()
    db = datasette.databases[database_name]
    column_descriptions = dict(
        await datasette.get_internal_database().execute(
            """
          SELECT
            column_name,
            value
          FROM metadata_columns
          WHERE database_name = ?
            AND resource_name = ?
            AND key = 'description'
        """,
            [database_name, table_name],
        )
    )

    # Look up column types for this table
    column_types_map = await datasette.get_column_types(database_name, table_name)

    column_details = {
        col.name: col for col in await db.table_column_details(table_name)
    }
    pks = await db.primary_keys(table_name)
    pks_for_display = pks
    if not pks_for_display:
        pks_for_display = ["rowid"]

    columns = []
    for r in description:
        if r[0] == "rowid" and "rowid" not in column_details:
            type_ = "integer"
            notnull = 0
        else:
            type_ = column_details[r[0]].type
            notnull = column_details[r[0]].notnull
        col_dict = {
            "name": r[0],
            "sortable": r[0] in sortable_columns,
            "is_pk": r[0] in pks_for_display,
            "type": type_,
            "notnull": notnull,
            "description": column_descriptions.get(r[0]),
            "column_type": None,
            "column_type_config": None,
        }
        ct = column_types_map.get(r[0])
        if ct:
            col_dict["column_type"] = ct.name
            col_dict["column_type_config"] = ct.config
        columns.append(col_dict)

    column_to_foreign_key_table = {
        fk["column"]: fk["other_table"]
        for fk in await db.foreign_keys_for_table(table_name)
    }

    cell_rows = []
    base_url = datasette.setting("base_url")
    for row in rows:
        cells = []
        # Unless we are a view, the first column is a link - either to the rowid
        # or to the simple or compound primary key
        if link_column:
            is_special_link_column = len(pks) != 1
            pk_path = path_from_row_pks(row, pks, not pks, False)
            cells.append(
                {
                    "column": pks[0] if len(pks) == 1 else "Link",
                    "value_type": "pk",
                    "is_special_link_column": is_special_link_column,
                    "raw": pk_path,
                    "value": markupsafe.Markup(
                        '<a href="{table_path}/{flat_pks_quoted}">{flat_pks}</a>'.format(
                            table_path=datasette.urls.table(database_name, table_name),
                            flat_pks=str(markupsafe.escape(pk_path)),
                            flat_pks_quoted=path_from_row_pks(row, pks, not pks),
                        )
                    ),
                }
            )

        for value, column_dict in zip(row, columns):
            column = column_dict["name"]
            if link_column and len(pks) == 1 and column == pks[0]:
                # If there's a simple primary key, don't repeat the value as it's
                # already shown in the link column.
                continue

            # First try column type render_cell, then plugins
            # pylint: disable=no-member
            plugin_display_value = None
            ct = column_types_map.get(column)
            if ct:
                candidate = await ct.render_cell(
                    value=value,
                    column=column,
                    table=table_name,
                    database=database_name,
                    datasette=datasette,
                    request=request,
                )
                if candidate is not None:
                    plugin_display_value = candidate
            if plugin_display_value is None:
                for candidate in pm.hook.render_cell(
                    row=row,
                    value=value,
                    column=column,
                    table=table_name,
                    pks=pks_for_display,
                    database=database_name,
                    datasette=datasette,
                    request=request,
                    column_type=ct,
                ):
                    candidate = await await_me_maybe(candidate)
                    if candidate is not None:
                        plugin_display_value = candidate
                        break
            if plugin_display_value:
                display_value = plugin_display_value
            elif isinstance(value, bytes):
                formatted = format_bytes(len(value))
                display_value = markupsafe.Markup(
                    '<a class="blob-download" href="{}"{}>&lt;Binary:&nbsp;{:,}&nbsp;byte{}&gt;</a>'.format(
                        datasette.urls.row_blob(
                            database_name,
                            table_name,
                            path_from_row_pks(row, pks, not pks),
                            column,
                        ),
                        (
                            ' title="{}"'.format(formatted)
                            if "bytes" not in formatted
                            else ""
                        ),
                        len(value),
                        "" if len(value) == 1 else "s",
                    )
                )
            elif isinstance(value, dict):
                # It's an expanded foreign key - display link to other row
                label = value["label"]
                value = value["value"]
                # The table we link to depends on the column
                other_table = column_to_foreign_key_table[column]
                link_template = LINK_WITH_LABEL if (label != value) else LINK_WITH_VALUE
                display_value = markupsafe.Markup(
                    link_template.format(
                        database=tilde_encode(database_name),
                        base_url=base_url,
                        table=tilde_encode(other_table),
                        link_id=tilde_encode(str(value)),
                        id=str(markupsafe.escape(value)),
                        label=str(markupsafe.escape(label)) or "-",
                    )
                )
            elif value in ("", None):
                display_value = markupsafe.Markup("&nbsp;")
            elif is_url(str(value).strip()):
                display_value = markupsafe.Markup(
                    '<a href="{url}">{truncated_url}</a>'.format(
                        url=markupsafe.escape(value.strip()),
                        truncated_url=markupsafe.escape(
                            truncate_url(value.strip(), truncate_cells)
                        ),
                    )
                )
            else:
                display_value = str(value)
                if truncate_cells and len(display_value) > truncate_cells:
                    display_value = display_value[:truncate_cells] + "\u2026"

            cells.append(
                {
                    "column": column,
                    "value": display_value,
                    "raw": value,
                    "value_type": (
                        "none" if value is None else str(type(value).__name__)
                    ),
                }
            )
        cell_rows.append(Row(cells))

    if link_column:
        # Add the link column header.
        # If it's a simple primary key, we have to remove and re-add that column name at
        # the beginning of the header row.
        first_column = None
        if len(pks) == 1:
            columns = [col for col in columns if col["name"] != pks[0]]
            first_column = {
                "name": pks[0],
                "sortable": len(pks) == 1,
                "is_pk": True,
                "type": column_details[pks[0]].type,
                "notnull": column_details[pks[0]].notnull,
            }
        else:
            first_column = {
                "name": "Link",
                "sortable": False,
                "is_pk": False,
                "type": "",
                "notnull": 0,
                "is_special_link_column": True,
            }
        columns = [first_column] + columns
    return columns, cell_rows


class TableInsertView(BaseView):
    name = "table-insert"

    def __init__(self, datasette):
        self.ds = datasette

    async def _validate_data(self, request, db, table_name, pks, upsert):
        errors = []

        pks_list = []
        if isinstance(pks, str):
            pks_list = [pks]
        else:
            pks_list = list(pks)

        if not pks_list:
            pks_list = ["rowid"]

        def _errors(errors):
            return None, errors, {}

        if not request.headers.get("content-type").startswith("application/json"):
            # TODO: handle form-encoded data
            return _errors(["Invalid content-type, must be application/json"])
        body = await request.post_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return _errors(["Invalid JSON: {}".format(e)])
        if not isinstance(data, dict):
            return _errors(["JSON must be a dictionary"])
        keys = data.keys()

        # keys must contain "row" or "rows"
        if "row" not in keys and "rows" not in keys:
            return _errors(['JSON must have one or other of "row" or "rows"'])
        rows = []
        if "row" in keys:
            if "rows" in keys:
                return _errors(['Cannot use "row" and "rows" at the same time'])
            row = data["row"]
            if not isinstance(row, dict):
                return _errors(['"row" must be a dictionary'])
            rows = [row]
            data["return"] = True
        else:
            rows = data["rows"]
        if not isinstance(rows, list):
            return _errors(['"rows" must be a list'])
        for row in rows:
            if not isinstance(row, dict):
                return _errors(['"rows" must be a list of dictionaries'])

        # Does this exceed max_insert_rows?
        max_insert_rows = self.ds.setting("max_insert_rows")
        if len(rows) > max_insert_rows:
            return _errors(
                ["Too many rows, maximum allowed is {}".format(max_insert_rows)]
            )

        # Validate other parameters
        extras = {
            key: value for key, value in data.items() if key not in ("row", "rows")
        }
        valid_extras = {"return", "ignore", "replace", "alter"}
        invalid_extras = extras.keys() - valid_extras
        if invalid_extras:
            return _errors(
                ['Invalid parameter: "{}"'.format('", "'.join(sorted(invalid_extras)))]
            )
        if extras.get("ignore") and extras.get("replace"):
            return _errors(['Cannot use "ignore" and "replace" at the same time'])

        columns = set(await db.table_columns(table_name))
        columns.update(pks_list)

        for i, row in enumerate(rows):
            if upsert:
                # It MUST have the primary key
                missing_pks = [pk for pk in pks_list if pk not in row]
                if missing_pks:
                    errors.append(
                        'Row {} is missing primary key column(s): "{}"'.format(
                            i, '", "'.join(missing_pks)
                        )
                    )
                null_pks = [pk for pk in pks_list if pk in row and row[pk] is None]
                if null_pks:
                    errors.append(
                        'Row {} has null primary key column(s): "{}"'.format(
                            i, '", "'.join(null_pks)
                        )
                    )
            invalid_columns = set(row.keys()) - columns
            if invalid_columns and not extras.get("alter"):
                errors.append(
                    "Row {} has invalid columns: {}".format(
                        i, ", ".join(sorted(invalid_columns))
                    )
                )
        if errors:
            return _errors(errors)
        return rows, errors, extras

    async def post(self, request, upsert=False):
        try:
            resolved = await self.ds.resolve_table(request)
        except NotFound as e:
            return _error([e.args[0]], 404)
        db = resolved.db
        database_name = db.name
        table_name = resolved.table

        # Table must exist (may handle table creation in the future)
        db = self.ds.get_database(database_name)
        if not await db.table_exists(table_name):
            return _error(["Table not found: {}".format(table_name)], 404)

        if upsert:
            # Must have insert-row AND upsert-row permissions
            if not (
                await self.ds.allowed(
                    action="insert-row",
                    resource=TableResource(database=database_name, table=table_name),
                    actor=request.actor,
                )
                and await self.ds.allowed(
                    action="update-row",
                    resource=TableResource(database=database_name, table=table_name),
                    actor=request.actor,
                )
            ):
                return _error(
                    ["Permission denied: need both insert-row and update-row"], 403
                )
        else:
            # Must have insert-row permission
            if not await self.ds.allowed(
                action="insert-row",
                resource=TableResource(database=database_name, table=table_name),
                actor=request.actor,
            ):
                return _error(["Permission denied"], 403)

        if not db.is_mutable:
            return _error(["Database is immutable"], 403)

        pks = await db.primary_keys(table_name)

        rows, errors, extras = await self._validate_data(
            request, db, table_name, pks, upsert
        )
        if errors:
            return _error(errors, 400)

        # Validate column types
        ct_errors = await _validate_column_types(
            self.ds, database_name, table_name, rows
        )
        if ct_errors:
            return _error(ct_errors, 400)

        num_rows = len(rows)

        # No that we've passed pks to _validate_data it's safe to
        # fix the rowids case:
        if not pks:
            pks = ["rowid"]

        ignore = extras.get("ignore")
        replace = extras.get("replace")
        alter = extras.get("alter")

        if upsert and (ignore or replace):
            return _error(["Upsert does not support ignore or replace"], 400)

        if replace and not await self.ds.allowed(
            action="update-row",
            resource=TableResource(database=database_name, table=table_name),
            actor=request.actor,
        ):
            return _error(['Permission denied: need update-row to use "replace"'], 403)

        initial_schema = None
        if alter:
            # Must have alter-table permission
            if not await self.ds.allowed(
                action="alter-table",
                resource=TableResource(database=database_name, table=table_name),
                actor=request.actor,
            ):
                return _error(["Permission denied for alter-table"], 403)
            # Track initial schema to check if it changed later
            initial_schema = await db.execute_fn(
                lambda conn: sqlite_utils.Database(conn)[table_name].schema
            )

        should_return = bool(extras.get("return", False))
        row_pk_values_for_later = []
        if should_return and upsert:
            row_pk_values_for_later = [tuple(row[pk] for pk in pks) for row in rows]

        def insert_or_upsert_rows(conn):
            table = sqlite_utils.Database(conn)[table_name]
            kwargs = {}
            if upsert:
                kwargs = {
                    "pk": pks[0] if len(pks) == 1 else pks,
                    "alter": alter,
                }
            else:
                # Insert
                kwargs = {"ignore": ignore, "replace": replace, "alter": alter}
            if should_return and not upsert:
                rowids = []
                method = table.upsert if upsert else table.insert
                for row in rows:
                    rowids.append(method(row, **kwargs).last_rowid)
                return list(
                    table.rows_where(
                        "rowid in ({})".format(",".join("?" for _ in rowids)),
                        rowids,
                    )
                )
            else:
                method_all = table.upsert_all if upsert else table.insert_all
                method_all(rows, **kwargs)

        try:
            rows = await db.execute_write_fn(insert_or_upsert_rows, request=request)
        except Exception as e:
            return _error([str(e)])
        result = {"ok": True}
        if should_return:
            if upsert:
                # Fetch based on initial input IDs
                where_clause = " OR ".join(
                    ["({})".format(" AND ".join("{} = ?".format(pk) for pk in pks))]
                    * len(row_pk_values_for_later)
                )
                args = list(itertools.chain.from_iterable(row_pk_values_for_later))
                fetched_rows = await db.execute(
                    "select {}* from [{}] where {}".format(
                        "rowid, " if pks == ["rowid"] else "", table_name, where_clause
                    ),
                    args,
                )
                result["rows"] = fetched_rows.dicts()
            else:
                result["rows"] = rows
        # We track the number of rows requested, but do not attempt to show which were actually
        # inserted or upserted v.s. ignored
        if upsert:
            await self.ds.track_event(
                UpsertRowsEvent(
                    actor=request.actor,
                    database=database_name,
                    table=table_name,
                    num_rows=num_rows,
                )
            )
        else:
            await self.ds.track_event(
                InsertRowsEvent(
                    actor=request.actor,
                    database=database_name,
                    table=table_name,
                    num_rows=num_rows,
                    ignore=bool(ignore),
                    replace=bool(replace),
                )
            )

        if initial_schema is not None:
            after_schema = await db.execute_fn(
                lambda conn: sqlite_utils.Database(conn)[table_name].schema
            )
            if initial_schema != after_schema:
                await self.ds.track_event(
                    AlterTableEvent(
                        request.actor,
                        database=database_name,
                        table=table_name,
                        before_schema=initial_schema,
                        after_schema=after_schema,
                    )
                )

        return Response.json(result, status=200 if upsert else 201)


class TableUpsertView(TableInsertView):
    name = "table-upsert"

    async def post(self, request):
        return await super().post(request, upsert=True)


class TableSetColumnTypeView(BaseView):
    name = "table-set-column-type"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        try:
            resolved = await self.ds.resolve_table(request)
        except NotFound as e:
            return _error([e.args[0]], 404)

        database_name = resolved.db.name
        table_name = resolved.table

        if not await self.ds.allowed(
            action="set-column-type",
            resource=TableResource(database=database_name, table=table_name),
            actor=request.actor,
        ):
            return _error(["Permission denied"], 403)

        content_type = request.headers.get("content-type") or ""
        if not content_type.startswith("application/json"):
            return _error(["Invalid content-type, must be application/json"], 400)

        try:
            data = json.loads(await request.post_body())
        except json.JSONDecodeError as e:
            return _error(["Invalid JSON: {}".format(e)], 400)

        if not isinstance(data, dict):
            return _error(["JSON must be a dictionary"], 400)

        invalid_keys = set(data.keys()) - {"column", "column_type"}
        if invalid_keys:
            return _error(
                ['Invalid parameter: "{}"'.format('", "'.join(sorted(invalid_keys)))],
                400,
            )

        if "column" not in data:
            return _error(['"column" is required'], 400)
        column = data["column"]
        if not isinstance(column, str):
            return _error(['"column" must be a string'], 400)

        if "column_type" not in data:
            return _error(['"column_type" is required'], 400)

        column_details = await self.ds._get_resource_column_details(
            database_name, table_name
        )
        if column not in column_details:
            return _error(["Column not found: {}".format(column)], 400)

        column_type_data = data["column_type"]
        if column_type_data is None:
            await self.ds.remove_column_type(database_name, table_name, column)
            return Response.json(
                {
                    "ok": True,
                    "database": database_name,
                    "table": table_name,
                    "column": column,
                    "column_type": None,
                },
                status=200,
            )

        if not isinstance(column_type_data, dict):
            return _error(['"column_type" must be an object or null'], 400)

        invalid_column_type_keys = set(column_type_data.keys()) - {"type", "config"}
        if invalid_column_type_keys:
            return _error(
                [
                    'Invalid column_type parameter: "{}"'.format(
                        '", "'.join(sorted(invalid_column_type_keys))
                    )
                ],
                400,
            )

        if "type" not in column_type_data:
            return _error(['"column_type.type" is required'], 400)
        column_type = column_type_data["type"]
        if not isinstance(column_type, str):
            return _error(['"column_type.type" must be a string'], 400)

        config = column_type_data.get("config")
        if config is not None and not isinstance(config, dict):
            return _error(['"column_type.config" must be a dictionary'], 400)

        if column_type not in self.ds._column_types:
            return _error(["Unknown column type: {}".format(column_type)], 400)

        try:
            await self.ds.set_column_type(
                database_name, table_name, column, column_type, config
            )
        except ValueError as e:
            return _error([str(e)], 400)

        return Response.json(
            {
                "ok": True,
                "database": database_name,
                "table": table_name,
                "column": column,
                "column_type": {"type": column_type, "config": config},
            },
            status=200,
        )


class TableDropView(BaseView):
    name = "table-drop"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        try:
            resolved = await self.ds.resolve_table(request)
        except NotFound as e:
            return _error([e.args[0]], 404)
        db = resolved.db
        database_name = db.name
        table_name = resolved.table
        # Table must exist
        db = self.ds.get_database(database_name)
        if not await db.table_exists(table_name):
            return _error(["Table not found: {}".format(table_name)], 404)
        if not await self.ds.allowed(
            action="drop-table",
            resource=TableResource(database=database_name, table=table_name),
            actor=request.actor,
        ):
            return _error(["Permission denied"], 403)
        if not db.is_mutable:
            return _error(["Database is immutable"], 403)
        confirm = False
        try:
            data = json.loads(await request.post_body())
            confirm = data.get("confirm")
        except json.JSONDecodeError:
            pass

        if not confirm:
            return Response.json(
                {
                    "ok": True,
                    "database": database_name,
                    "table": table_name,
                    "row_count": (
                        await db.execute("select count(*) from [{}]".format(table_name))
                    ).single_value(),
                    "message": 'Pass "confirm": true to confirm',
                },
                status=200,
            )

        # Drop table
        def drop_table(conn):
            sqlite_utils.Database(conn)[table_name].drop()

        await db.execute_write_fn(drop_table, request=request)
        await self.ds.track_event(
            DropTableEvent(
                actor=request.actor, database=database_name, table=table_name
            )
        )
        return Response.json({"ok": True}, status=200)


async def _columns_to_select(table_columns, pks, request):
    columns = list(table_columns)
    if "_col" in request.args:
        columns = list(pks)
        _cols = request.args.getlist("_col")
        bad_columns = [column for column in _cols if column not in table_columns]
        if bad_columns:
            raise DatasetteError(
                "_col={} - invalid columns".format(", ".join(bad_columns)),
                status=400,
            )
        # De-duplicate maintaining order:
        columns.extend(dict.fromkeys(_cols))
    if "_nocol" in request.args:
        # Return all columns EXCEPT these
        bad_columns = [
            column
            for column in request.args.getlist("_nocol")
            if (column not in table_columns) or (column in pks)
        ]
        if bad_columns:
            raise DatasetteError(
                "_nocol={} - invalid columns".format(", ".join(bad_columns)),
                status=400,
            )
        tmp_columns = [
            column for column in columns if column not in request.args.getlist("_nocol")
        ]
        columns = tmp_columns
    return columns


async def _sortable_columns_for_table(datasette, database_name, table_name, use_rowid):
    db = datasette.databases[database_name]
    table_metadata = await datasette.table_config(database_name, table_name)
    if "sortable_columns" in table_metadata:
        sortable_columns = set(table_metadata["sortable_columns"])
    else:
        sortable_columns = set(await db.table_columns(table_name))
    if use_rowid:
        sortable_columns.add("rowid")
    return sortable_columns


async def _sort_order(table_metadata, sortable_columns, request, order_by):
    sort = request.args.get("_sort")
    sort_desc = request.args.get("_sort_desc")

    if not sort and not sort_desc:
        sort = table_metadata.get("sort")
        sort_desc = table_metadata.get("sort_desc")

    if sort and sort_desc:
        raise DatasetteError(
            "Cannot use _sort and _sort_desc at the same time", status=400
        )

    if sort:
        if sort not in sortable_columns:
            raise DatasetteError(f"Cannot sort table by {sort}", status=400)

        order_by = escape_sqlite(sort)

    if sort_desc:
        if sort_desc not in sortable_columns:
            raise DatasetteError(f"Cannot sort table by {sort_desc}", status=400)

        order_by = f"{escape_sqlite(sort_desc)} desc"

    return sort, sort_desc, order_by


async def table_view(datasette, request):
    await datasette.refresh_schemas()
    with tracer.trace_child_tasks():
        response = await table_view_traced(datasette, request)

    # CORS
    if datasette.cors:
        add_cors_headers(response.headers)

    # Cache TTL header
    ttl = request.args.get("_ttl", None)
    if ttl is None or not ttl.isdigit():
        ttl = datasette.setting("default_cache_ttl")

    if datasette.cache_headers and response.status == 200:
        ttl = int(ttl)
        if ttl == 0:
            ttl_header = "no-cache"
        else:
            ttl_header = f"max-age={ttl}"
        response.headers["Cache-Control"] = ttl_header

    # Referrer policy
    response.headers["Referrer-Policy"] = "no-referrer"

    return response


async def table_view_traced(datasette, request):
    from datasette.app import TableNotFound

    try:
        resolved = await datasette.resolve_table(request)
    except TableNotFound as not_found:
        # Was this actually a stored query?
        stored_query = await datasette.get_query(
            not_found.database_name, not_found.table
        )
        # If this is a stored query, not a table, then dispatch to QueryView instead
        if stored_query:
            return await QueryView()(request, datasette)
        else:
            raise

    if request.method == "POST":
        return Response.text("Method not allowed", status=405)

    format_ = request.url_vars.get("format") or "html"
    extra_extras = None
    context_for_html_hack = False
    default_labels = False
    if format_ == "html":
        extra_extras = {"_html"}
        context_for_html_hack = True
        default_labels = True

    view_data = await table_view_data(
        datasette,
        request,
        resolved,
        extra_extras=extra_extras,
        context_for_html_hack=context_for_html_hack,
        default_labels=default_labels,
    )
    if isinstance(view_data, Response):
        return view_data
    data, rows, columns, expanded_columns, sql, next_url = view_data

    # Handle formats from plugins
    if format_ == "csv":

        async def fetch_data(request, _next=None):
            (
                data,
                rows,
                columns,
                expanded_columns,
                sql,
                next_url,
            ) = await table_view_data(
                datasette,
                request,
                resolved,
                extra_extras=extra_extras,
                context_for_html_hack=context_for_html_hack,
                default_labels=default_labels,
                _next=_next,
            )
            data["rows"] = rows
            data["table"] = resolved.table
            data["columns"] = columns
            data["expanded_columns"] = expanded_columns
            return data, None, None

        return await stream_csv(datasette, fetch_data, request, resolved.db.name)
    elif format_ in datasette.renderers.keys():
        # Dispatch request to the correct output format renderer
        # (CSV is not handled here due to streaming)
        result = call_with_supported_arguments(
            datasette.renderers[format_][0],
            datasette=datasette,
            columns=columns,
            rows=rows,
            sql=sql,
            query_name=None,
            database=resolved.db.name,
            table=resolved.table,
            request=request,
            view_name="table",
            truncated=False,
            error=None,
            # These will be deprecated in Datasette 1.0:
            args=request.args,
            data=data,
        )
        if asyncio.iscoroutine(result):
            result = await result
        if result is None:
            raise NotFound("No data")
        if isinstance(result, dict):
            r = Response(
                body=result.get("body"),
                status=result.get("status_code") or 200,
                content_type=result.get("content_type", "text/plain"),
                headers=result.get("headers"),
            )
        elif isinstance(result, Response):
            r = result
            # if status_code is not None:
            #     # Over-ride the status code
            #     r.status = status_code
        else:
            assert False, f"{result} should be dict or Response"
    elif format_ == "html":
        headers = {}
        templates = [
            f"table-{to_css_class(resolved.db.name)}-{to_css_class(resolved.table)}.html",
            "table.html",
        ]
        environment = datasette.get_jinja_environment(request)
        template = environment.select_template(templates)
        alternate_url_json = datasette.absolute_url(
            request,
            datasette.urls.path(
                path_with_format(
                    request=request,
                    path=request.scope.get("route_path"),
                    format="json",
                )
            ),
        )
        headers.update(
            {
                "Link": '<{}>; rel="alternate"; type="application/json+datasette"'.format(
                    alternate_url_json
                )
            }
        )
        # Only keys declared on TableContext are part of the documented
        # template contract - anything else in data (e.g. extras requested
        # with ?_extra= on the HTML page, or extra filter context added by
        # filters_from_request plugins) is dropped here
        declared_fields = {f.name for f in fields(TableContext)}
        r = Response.html(
            await datasette.render_template(
                template,
                TableContext(
                    **{k: v for k, v in data.items() if k in declared_fields},
                    append_querystring=append_querystring,
                    path_with_replaced_args=path_with_replaced_args,
                    fix_path=datasette.urls.path,
                    settings=datasette.settings_dict(),
                    # TODO: review up all of these hacks:
                    alternate_url_json=alternate_url_json,
                    datasette_allow_facet=(
                        "true" if datasette.setting("allow_facet") else "false"
                    ),
                    is_sortable=any(c["sortable"] for c in data["display_columns"]),
                    allow_execute_sql=await datasette.allowed(
                        action="execute-sql",
                        resource=DatabaseResource(database=resolved.db.name),
                        actor=request.actor,
                    ),
                    query_ms=1.2,
                    select_templates=[
                        f"{'*' if template_name == template.name else ''}{template_name}"
                        for template_name in templates
                    ],
                    top_table=make_slot_function(
                        "top_table",
                        datasette,
                        request,
                        database=resolved.db.name,
                        table=resolved.table,
                    ),
                    count_limit=resolved.db.count_limit,
                ),
                request=request,
                view_name="table",
            ),
            headers=headers,
        )
    else:
        assert False, "Invalid format: {}".format(format_)
    if next_url:
        r.headers["link"] = f'<{next_url}>; rel="next"'
    return r


async def table_view_data(
    datasette,
    request,
    resolved,
    extra_extras=None,
    context_for_html_hack=False,
    default_labels=False,
    _next=None,
):
    extra_extras = extra_extras or set()
    # We have a table or view
    db = resolved.db
    database_name = resolved.db.name
    table_name = resolved.table
    is_view = resolved.is_view

    # Can this user view it?
    visible, private = await datasette.check_visibility(
        request.actor,
        action="view-table",
        resource=TableResource(database=database_name, table=table_name),
    )
    if not visible:
        raise Forbidden("You do not have permission to view this table")

    # Redirect based on request.args, if necessary
    redirect_response = await _redirect_if_needed(datasette, request, resolved)
    if redirect_response:
        return redirect_response

    # Introspect columns and primary keys for table
    pks = await db.primary_keys(table_name)
    table_columns = await db.table_columns(table_name)

    # Take ?_col= and ?_nocol= into account
    specified_columns = await _columns_to_select(table_columns, pks, request)
    select_specified_columns = ", ".join(escape_sqlite(t) for t in specified_columns)
    select_all_columns = ", ".join(escape_sqlite(t) for t in table_columns)

    # rowid tables (no specified primary key) need a different SELECT
    use_rowid = not pks and not is_view
    order_by = ""
    if use_rowid:
        select_specified_columns = f"rowid, {select_specified_columns}"
        select_all_columns = f"rowid, {select_all_columns}"
        order_by = "rowid"
        order_by_pks = "rowid"
    else:
        order_by_pks = ", ".join([escape_sqlite(pk) for pk in pks])
        order_by = order_by_pks

    if is_view:
        order_by = ""

    # TODO: This logic should turn into logic about which ?_extras get
    # executed instead:
    nocount = request.args.get("_nocount")
    nofacet = request.args.get("_nofacet")
    nosuggest = request.args.get("_nosuggest")
    if request.args.get("_shape") in ("array", "object"):
        nocount = True
        nofacet = True

    table_metadata = await datasette.table_config(database_name, table_name)

    # Arguments that start with _ and don't contain a __ are
    # special - things like ?_search= - and should not be
    # treated as filters.
    filter_args = []
    for key in request.args:
        if not (key.startswith("_") and "__" not in key):
            for v in request.args.getlist(key):
                filter_args.append((key, v))

    # Build where clauses from query string arguments
    filters = Filters(sorted(filter_args))
    where_clauses, params = filters.build_where_clauses(table_name)

    # Execute filters_from_request plugin hooks - including the default
    # ones that live in datasette/filters.py
    extra_context_from_filters = {}
    extra_human_descriptions = []

    for hook in pm.hook.filters_from_request(
        request=request,
        table=table_name,
        database=database_name,
        datasette=datasette,
    ):
        filter_arguments = await await_me_maybe(hook)
        if filter_arguments:
            where_clauses.extend(filter_arguments.where_clauses)
            params.update(filter_arguments.params)
            extra_human_descriptions.extend(filter_arguments.human_descriptions)
            extra_context_from_filters.update(filter_arguments.extra_context)

    # Deal with custom sort orders
    sortable_columns = await _sortable_columns_for_table(
        datasette, database_name, table_name, use_rowid
    )

    sort, sort_desc, order_by = await _sort_order(
        table_metadata, sortable_columns, request, order_by
    )

    from_sql = "from {table_name} {where}".format(
        table_name=escape_sqlite(table_name),
        where=(
            ("where {} ".format(" and ".join(where_clauses))) if where_clauses else ""
        ),
    )
    # Copy of params so we can mutate them later:
    from_sql_params = dict(**params)

    count_sql = f"select count(*) {from_sql}"

    # Handle pagination driven by ?_next=
    _next = _next or request.args.get("_next")

    offset = ""
    if _next:
        sort_value = None
        if is_view:
            # _next is an offset
            offset = f" offset {int(_next)}"
        else:
            components = urlsafe_components(_next)
            # If a sort order is applied and there are multiple components,
            # the first of these is the sort value
            if (sort or sort_desc) and (len(components) > 1):
                sort_value = components[0]
                # Special case for if non-urlencoded first token was $null
                if _next.split(",")[0] == "$null":
                    sort_value = None
                components = components[1:]

            # Figure out the SQL for next-based-on-primary-key first
            next_by_pk_clauses = []
            if use_rowid:
                next_by_pk_clauses.append(f"rowid > :p{len(params)}")
                params[f"p{len(params)}"] = components[0]
            else:
                # Apply the tie-breaker based on primary keys
                if len(components) == len(pks):
                    param_len = len(params)
                    next_by_pk_clauses.append(compound_keys_after_sql(pks, param_len))
                    for i, pk_value in enumerate(components):
                        params[f"p{param_len + i}"] = pk_value

            # Now add the sort SQL, which may incorporate next_by_pk_clauses
            if sort or sort_desc:
                if sort_value is None:
                    if sort_desc:
                        # Just items where column is null ordered by pk
                        where_clauses.append(
                            "({column} is null and {next_clauses})".format(
                                column=escape_sqlite(sort_desc),
                                next_clauses=" and ".join(next_by_pk_clauses),
                            )
                        )
                    else:
                        where_clauses.append(
                            "({column} is not null or ({column} is null and {next_clauses}))".format(
                                column=escape_sqlite(sort),
                                next_clauses=" and ".join(next_by_pk_clauses),
                            )
                        )
                else:
                    where_clauses.append(
                        "({column} {op} :p{p}{extra_desc_only} or ({column} = :p{p} and {next_clauses}))".format(
                            column=escape_sqlite(sort or sort_desc),
                            op=">" if sort else "<",
                            p=len(params),
                            extra_desc_only=(
                                ""
                                if sort
                                else " or {column2} is null".format(
                                    column2=escape_sqlite(sort or sort_desc)
                                )
                            ),
                            next_clauses=" and ".join(next_by_pk_clauses),
                        )
                    )
                    params[f"p{len(params)}"] = sort_value
                order_by = f"{order_by}, {order_by_pks}"
            else:
                where_clauses.extend(next_by_pk_clauses)

    where_clause = ""
    if where_clauses:
        where_clause = f"where {' and '.join(where_clauses)} "

    if order_by:
        order_by = f"order by {order_by}"

    extra_args = {}
    # Handle ?_size=500
    # TODO: This was:
    # page_size = _size or request.args.get("_size") or table_metadata.get("size")
    page_size = request.args.get("_size") or table_metadata.get("size")
    if page_size:
        if page_size == "max":
            page_size = datasette.max_returned_rows
        try:
            page_size = int(page_size)
            if page_size < 0:
                raise ValueError

        except ValueError:
            raise BadRequest("_size must be a positive integer")

        if page_size > datasette.max_returned_rows:
            raise BadRequest(f"_size must be <= {datasette.max_returned_rows}")

        extra_args["page_size"] = page_size
    else:
        page_size = datasette.page_size

    # Facets are calculated against SQL without order by or limit
    sql_no_order_no_limit = (
        "select {select_all_columns} from {table_name} {where}".format(
            select_all_columns=select_all_columns,
            table_name=escape_sqlite(table_name),
            where=where_clause,
        )
    )

    # This is the SQL that populates the main table on the page
    sql = "select {select_specified_columns} from {table_name} {where}{order_by} limit {page_size}{offset}".format(
        select_specified_columns=select_specified_columns,
        table_name=escape_sqlite(table_name),
        where=where_clause,
        order_by=order_by,
        page_size=page_size + 1,
        offset=offset,
    )

    if request.args.get("_timelimit"):
        extra_args["custom_time_limit"] = int(request.args.get("_timelimit"))

    # Execute the main query!
    try:
        results = await db.execute(sql, params, truncate=True, **extra_args)
    except (sqlite3.OperationalError, InvalidSql) as e:
        raise DatasetteError(str(e), title="Invalid SQL", status=400)

    except sqlite3.OperationalError as e:
        raise DatasetteError(str(e))

    columns = [r[0] for r in results.description]
    rows = list(results.rows)

    # Expand labeled columns if requested
    expanded_columns = []
    # List of (fk_dict, label_column-or-None) pairs for that table
    expandable_columns = []
    for fk in await db.foreign_keys_for_table(table_name):
        label_column = await db.label_column_for_table(fk["other_table"])
        expandable_columns.append((fk, label_column))

    columns_to_expand = None
    try:
        all_labels = value_as_boolean(request.args.get("_labels", ""))
    except ValueError:
        all_labels = default_labels
    # Check for explicit _label=
    if "_label" in request.args:
        columns_to_expand = request.args.getlist("_label")
    if columns_to_expand is None and all_labels:
        # expand all columns with foreign keys
        columns_to_expand = [fk["column"] for fk, _ in expandable_columns]

    if columns_to_expand:
        expanded_labels = {}
        for fk, _ in expandable_columns:
            column = fk["column"]
            if column not in columns_to_expand:
                continue
            if column not in columns:
                continue
            expanded_columns.append(column)
            # Gather the values
            column_index = columns.index(column)
            values = [row[column_index] for row in rows]
            # Expand them
            expanded_labels.update(
                await datasette.expand_foreign_keys(
                    request.actor, database_name, table_name, column, values
                )
            )
        if expanded_labels:
            # Rewrite the rows
            new_rows = []
            for row in rows:
                new_row = CustomRow(columns)
                for column in row.keys():
                    value = row[column]
                    if (column, value) in expanded_labels and value is not None:
                        new_row[column] = {
                            "value": value,
                            "label": expanded_labels[(column, value)],
                        }
                    else:
                        new_row[column] = value
                new_rows.append(new_row)
            rows = new_rows

    _next = request.args.get("_next")

    # Pagination next link
    next_value, next_url = await _next_value_and_url(
        datasette,
        db,
        request,
        table_name,
        _next,
        rows,
        pks,
        use_rowid,
        sort,
        sort_desc,
        page_size,
        is_view,
    )
    rows = rows[:page_size]

    # Resolve extras
    extras = extra_names_from_request(request)
    if any(k for k in request.args.keys() if k == "_facet" or k.startswith("_facet_")):
        extras.add("facet_results")
    if request.args.get("_shape") == "object":
        extras.add("primary_keys")
    if extra_extras:
        extras.update(extra_extras)

    # Faceting
    if not datasette.setting("allow_facet") and any(
        arg.startswith("_facet") for arg in request.args
    ):
        raise BadRequest("_facet= is not allowed")

    for key, values in TABLE_EXTRA_BUNDLES.items():
        if f"_{key}" in extras:
            extras.update(values)
        extras.discard(f"_{key}")

    table_extra_context = TableExtraContext(
        datasette=datasette,
        request=request,
        resolved=resolved,
        db=db,
        database_name=database_name,
        table_name=table_name,
        is_view=is_view,
        private=private,
        rows=rows,
        columns=columns,
        results_description=results.description,
        table_columns=table_columns,
        pks=pks,
        count_sql=count_sql,
        from_sql=from_sql,
        from_sql_params=from_sql_params,
        nocount=nocount,
        nofacet=nofacet,
        nosuggest=nosuggest,
        next_arg=request.args.get("_next"),
        next_url=next_url,
        sql=sql,
        sql_no_order_no_limit=sql_no_order_no_limit,
        params=params,
        table_metadata=table_metadata,
        filters=filters,
        extra_human_descriptions=extra_human_descriptions,
        sort=sort,
        sort_desc=sort_desc,
        sortable_columns=sortable_columns,
        extras=extras,
        extra_registry=table_extra_registry,
        display_columns_and_rows=display_columns_and_rows,
        run_sequential=run_sequential,
    )

    data = {
        "ok": True,
        "next": next_value and str(next_value) or None,
    }
    data.update(
        await resolve_table_extras(
            extras,
            table_extra_context,
            # The HTML view needs extras that are not JSON serializable
            include_internal=bool(extra_extras),
        )
    )
    raw_sqlite_rows = rows[:page_size]
    # Apply transform_value for columns with assigned types
    ct_map = await datasette.get_column_types(database_name, table_name)
    transformed_rows = []
    for r in raw_sqlite_rows:
        row_dict = dict(r)
        for col_name, ct in ct_map.items():
            if col_name in row_dict:
                row_dict[col_name] = await ct.transform_value(
                    row_dict[col_name], datasette
                )
        transformed_rows.append(row_dict)
    data["rows"] = transformed_rows

    if context_for_html_hack:
        data.update(extra_context_from_filters)
        # filter_columns combine the columns we know are available
        # in the table with any additional columns (such as rowid)
        # which are available in the query
        data["filter_columns"] = list(columns) + [
            table_column
            for table_column in table_columns
            if table_column not in columns
        ]
        url_labels_extra = {}
        if data.get("expandable_columns"):
            url_labels_extra = {"_labels": "on"}
        url_csv_args = {"_size": "max", **url_labels_extra}
        url_csv = datasette.urls.path(
            path_with_format(
                request=request,
                path=request.scope.get("route_path"),
                format="csv",
                extra_qs=url_csv_args,
            )
        )
        url_csv_path = url_csv.split("?")[0]
        data.update(
            {
                "url_csv": url_csv,
                "url_csv_path": url_csv_path,
                "url_csv_hidden_args": [
                    (key, value)
                    for key, value in urllib.parse.parse_qsl(request.query_string)
                    if key not in ("_labels", "_facet", "_size")
                ]
                + [("_size", "max")],
            }
        )
        # if no sort specified AND table has a single primary key,
        # set sort to that so arrow is displayed
        if not sort and not sort_desc:
            if 1 == len(pks):
                sort = pks[0]
            elif use_rowid:
                sort = "rowid"
        data["sort"] = sort
        data["sort_desc"] = sort_desc

    return data, rows[:page_size], columns, expanded_columns, sql, next_url


async def _next_value_and_url(
    datasette,
    db,
    request,
    table_name,
    _next,
    rows,
    pks,
    use_rowid,
    sort,
    sort_desc,
    page_size,
    is_view,
):
    next_value = None
    next_url = None
    if 0 < page_size < len(rows):
        if is_view:
            next_value = int(_next or 0) + page_size
        else:
            next_value = path_from_row_pks(rows[-2], pks, use_rowid)
        # If there's a sort or sort_desc, add that value as a prefix
        if (sort or sort_desc) and not is_view:
            try:
                prefix = rows[-2][sort or sort_desc]
            except IndexError:
                # sort/sort_desc column missing from SELECT - look up value by PK instead
                prefix_where_clause = " and ".join(
                    "[{}] = :pk{}".format(pk, i) for i, pk in enumerate(pks)
                )
                prefix_lookup_sql = "select [{}] from [{}] where {}".format(
                    sort or sort_desc, table_name, prefix_where_clause
                )
                prefix = (
                    await db.execute(
                        prefix_lookup_sql,
                        {
                            **{
                                "pk{}".format(i): rows[-2][pk]
                                for i, pk in enumerate(pks)
                            }
                        },
                    )
                ).single_value()
            if isinstance(prefix, dict) and "value" in prefix:
                prefix = prefix["value"]
            if prefix is None:
                prefix = "$null"
            else:
                prefix = tilde_encode(str(prefix))
            next_value = f"{prefix},{next_value}"
            added_args = {"_next": next_value}
            if sort:
                added_args["_sort"] = sort
            else:
                added_args["_sort_desc"] = sort_desc
        else:
            added_args = {"_next": next_value}
        next_url = datasette.absolute_url(
            request, datasette.urls.path(path_with_replaced_args(request, added_args))
        )
    return next_value, next_url
