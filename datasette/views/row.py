from datasette.utils.asgi import NotFound, Forbidden, Response
from datasette.database import QueryInterrupted
from datasette.events import UpdateRowEvent, DeleteRowEvent
from datasette.resources import TableResource
from .base import DataView, BaseView, _error
from datasette.utils import (
    await_me_maybe,
    CustomRow,
    make_slot_function,
    to_css_class,
    escape_sqlite,
)
from datasette.plugins import pm
from dataclasses import dataclass, field
import json
import markupsafe
import sqlite_utils
from datasette.extras import extra_names_from_request, ExtraScope
from . import Context, extra_field
from .table import display_columns_and_rows
from .table_extras import RowExtraContext, resolve_row_extras, table_extra_registry


@dataclass
class RowContext(Context):
    "The page showing an individual row, e.g. /fixtures/facetable/1"

    extras_scope = ExtraScope.ROW

    # Fields resolved by registered extras - their documentation comes
    # from the description on each Extra class in table_extras.py
    columns: list = extra_field()
    database: str = extra_field()
    database_color: str = extra_field()
    foreign_key_tables: list = extra_field()
    metadata: dict = extra_field()
    primary_keys: list = extra_field()
    private: bool = extra_field()
    table: str = extra_field()

    # Fields added by the view code
    ok: bool = field(
        metadata={"help": "True if the data for this page was retrieved without errors"}
    )
    rows: list = field(
        metadata={
            "help": "The rows for this page, as a list of dictionaries mapping column name to value"
        }
    )
    primary_key_values: list = field(
        metadata={"help": "Values of the primary keys for this row, from the URL"}
    )
    query_ms: float = field(
        metadata={
            "help": "Time taken by the SQL queries for this page, in milliseconds"
        }
    )
    display_columns: list = field(
        metadata={"help": "Column objects formatted for the HTML table display"}
    )
    display_rows: list = field(
        metadata={"help": "Row data formatted for the HTML table display"}
    )
    custom_table_templates: list = field(
        metadata={
            "help": "Custom template names that were considered for displaying this table"
        }
    )
    row_actions: list = field(
        metadata={"help": "Row actions made available by plugin hooks"}
    )
    top_row: callable = field(
        metadata={"help": "Async function rendering the top_row plugin slot"}
    )
    renderers: dict = field(
        metadata={
            "help": "Dictionary mapping output format names (e.g. json) to their URLs for this page"
        }
    )
    url_csv: str = field(metadata={"help": "URL for the CSV export of this page"})
    url_csv_path: str = field(metadata={"help": "Path portion of the CSV export URL"})
    url_csv_hidden_args: list = field(
        metadata={
            "help": "(name, value) pairs for hidden form fields used by the CSV export form"
        }
    )
    settings: dict = field(
        metadata={"help": "Dictionary of Datasette's current settings"}
    )
    select_templates: list = field(
        metadata={
            "help": "List of template names that were considered for this page, the one used marked with an asterisk"
        }
    )
    alternate_url_json: str = field(
        metadata={"help": "URL for the JSON version of this page"}
    )


class RowView(DataView):
    name = "row"
    context_class = RowContext

    async def data(self, request, default_labels=False):
        resolved = await self.ds.resolve_row(request)
        db = resolved.db
        database = db.name
        table = resolved.table
        pk_values = resolved.pk_values

        # Ensure user has permission to view this row
        visible, private = await self.ds.check_visibility(
            request.actor,
            action="view-table",
            resource=TableResource(database=database, table=table),
        )
        if not visible:
            raise Forbidden("You do not have permission to view this table")

        results = await resolved.db.execute(
            resolved.sql, resolved.params, truncate=True
        )
        columns = [r[0] for r in results.description]
        rows = list(results.rows)
        if not rows:
            raise NotFound(f"Record not found: {pk_values}")

        pks = resolved.pks

        async def template_data():
            # Reorder columns so primary keys come first
            pk_set = set(pks)
            pk_cols = [d for d in results.description if d[0] in pk_set]
            non_pk_cols = [d for d in results.description if d[0] not in pk_set]
            reordered_description = pk_cols + non_pk_cols
            reordered_columns = [d[0] for d in reordered_description]

            # Reorder row data to match
            reordered_rows = []
            for row in rows:
                new_row = CustomRow(reordered_columns)
                for col in reordered_columns:
                    new_row[col] = row[col]
                reordered_rows.append(new_row)

            # Expand foreign key columns into dicts so display_columns_and_rows
            # renders them as hyperlinks, matching the table view behavior
            expanded_rows = reordered_rows
            for fk in await db.foreign_keys_for_table(table):
                column = fk["column"]
                if column not in reordered_columns:
                    continue
                column_index = reordered_columns.index(column)
                values = [row[column_index] for row in expanded_rows]
                expanded_labels = await self.ds.expand_foreign_keys(
                    request.actor, database, table, column, values
                )
                if expanded_labels:
                    new_rows = []
                    for row in expanded_rows:
                        new_row = CustomRow(reordered_columns)
                        for col in reordered_columns:
                            value = row[col]
                            if (
                                col == column
                                and (col, value) in expanded_labels
                                and value is not None
                            ):
                                new_row[col] = {
                                    "value": value,
                                    "label": expanded_labels[(col, value)],
                                }
                            else:
                                new_row[col] = value
                        new_rows.append(new_row)
                    expanded_rows = new_rows

            display_columns, display_rows = await display_columns_and_rows(
                self.ds,
                database,
                table,
                reordered_description,
                expanded_rows,
                link_column=False,
                truncate_cells=0,
                request=request,
            )
            for column in display_columns:
                column["sortable"] = False

            # Bold primary key cell values
            for row in display_rows:
                for cell in row:
                    if cell["column"] in pk_set:
                        cell["value"] = markupsafe.Markup(
                            "<strong>{}</strong>".format(cell["value"])
                        )

            row_actions = []
            for hook in pm.hook.row_actions(
                datasette=self.ds,
                actor=request.actor,
                request=request,
                database=database,
                table=table,
                row=rows[0],
            ):
                extra_links = await await_me_maybe(hook)
                if extra_links:
                    row_actions.extend(extra_links)

            return {
                "private": private,
                "columns": reordered_columns,
                "foreign_key_tables": await self.foreign_key_tables(
                    database, table, pk_values
                ),
                "database_color": db.color,
                "display_columns": display_columns,
                "display_rows": display_rows,
                "custom_table_templates": [
                    f"_table-{to_css_class(database)}-{to_css_class(table)}.html",
                    f"_table-row-{to_css_class(database)}-{to_css_class(table)}.html",
                    "_table.html",
                ],
                "row_actions": row_actions,
                "top_row": make_slot_function(
                    "top_row",
                    self.ds,
                    request,
                    database=resolved.db.name,
                    table=resolved.table,
                    row=rows[0],
                ),
                "metadata": {},
            }

        data = {
            "ok": True,
            "database": database,
            "table": table,
            "rows": rows,
            "columns": columns,
            "primary_keys": resolved.pks,
            "primary_key_values": pk_values,
        }

        extras = extra_names_from_request(request)

        # Process extras
        row_extra_context = RowExtraContext(
            datasette=self.ds,
            request=request,
            db=db,
            database_name=database,
            table_name=table,
            private=private,
            rows=rows,
            columns=columns,
            pks=pks,
            pk_values=pk_values,
            sql=resolved.sql,
            params=resolved.params,
            extras=extras,
            extra_registry=table_extra_registry,
            foreign_key_tables=self.foreign_key_tables,
        )
        data.update(await resolve_row_extras(extras, row_extra_context))

        return (
            data,
            template_data,
            (
                f"row-{to_css_class(database)}-{to_css_class(table)}.html",
                "row.html",
            ),
        )

    async def foreign_key_tables(self, database, table, pk_values):
        if len(pk_values) != 1:
            return []
        db = self.ds.databases[database]
        all_foreign_keys = await db.get_all_foreign_keys()
        foreign_keys = all_foreign_keys[table]["incoming"]
        if len(foreign_keys) == 0:
            return []

        sql = "select " + ", ".join(
            [
                "(select count(*) from {table} where {column}=:id)".format(
                    table=escape_sqlite(fk["other_table"]),
                    column=escape_sqlite(fk["other_column"]),
                )
                for fk in foreign_keys
            ]
        )
        try:
            rows = list(await db.execute(sql, {"id": pk_values[0]}))
        except QueryInterrupted:
            # Almost certainly hit the timeout
            return []

        foreign_table_counts = dict(
            zip(
                [(fk["other_table"], fk["other_column"]) for fk in foreign_keys],
                list(rows[0]),
            )
        )
        foreign_key_tables = []
        for fk in foreign_keys:
            count = (
                foreign_table_counts.get((fk["other_table"], fk["other_column"])) or 0
            )
            key = fk["other_column"]
            if key.startswith("_"):
                key += "__exact"
            link = "{}?{}={}".format(
                self.ds.urls.table(database, fk["other_table"]),
                key,
                ",".join(pk_values),
            )
            foreign_key_tables.append({**fk, **{"count": count, "link": link}})
        return foreign_key_tables


class RowError(Exception):
    def __init__(self, error):
        self.error = error


async def _resolve_row_and_check_permission(datasette, request, permission):
    from datasette.app import DatabaseNotFound, TableNotFound, RowNotFound

    try:
        resolved = await datasette.resolve_row(request)
    except DatabaseNotFound as e:
        return False, _error(["Database not found: {}".format(e.database_name)], 404)
    except TableNotFound as e:
        return False, _error(["Table not found: {}".format(e.table)], 404)
    except RowNotFound as e:
        return False, _error(["Record not found: {}".format(e.pk_values)], 404)

    # Ensure user has permission to delete this row
    if not await datasette.allowed(
        action=permission,
        resource=TableResource(database=resolved.db.name, table=resolved.table),
        actor=request.actor,
    ):
        return False, _error(["Permission denied"], 403)

    return True, resolved


class RowDeleteView(BaseView):
    name = "row-delete"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        ok, resolved = await _resolve_row_and_check_permission(
            self.ds, request, "delete-row"
        )
        if not ok:
            return resolved

        # Delete table
        def delete_row(conn):
            sqlite_utils.Database(conn)[resolved.table].delete(resolved.pk_values)

        try:
            await resolved.db.execute_write_fn(delete_row, request=request)
        except Exception as e:
            return _error([str(e)], 500)

        await self.ds.track_event(
            DeleteRowEvent(
                actor=request.actor,
                database=resolved.db.name,
                table=resolved.table,
                pks=resolved.pk_values,
            )
        )

        return Response.json({"ok": True}, status=200)


class RowUpdateView(BaseView):
    name = "row-update"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        ok, resolved = await _resolve_row_and_check_permission(
            self.ds, request, "update-row"
        )
        if not ok:
            return resolved

        body = await request.post_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return _error(["Invalid JSON: {}".format(e)])

        if not isinstance(data, dict):
            return _error(["JSON must be a dictionary"])
        if "update" not in data or not isinstance(data["update"], dict):
            return _error(["JSON must contain an update dictionary"])

        invalid_keys = set(data.keys()) - {"update", "return", "alter"}
        if invalid_keys:
            return _error(["Invalid keys: {}".format(", ".join(invalid_keys))])

        update = data["update"]

        # Validate column types
        from datasette.views.table import _validate_column_types

        ct_errors = await _validate_column_types(
            self.ds, resolved.db.name, resolved.table, [update]
        )
        if ct_errors:
            return _error(ct_errors, 400)

        alter = data.get("alter")
        if alter and not await self.ds.allowed(
            action="alter-table",
            resource=TableResource(database=resolved.db.name, table=resolved.table),
            actor=request.actor,
        ):
            return _error(["Permission denied for alter-table"], 403)

        def update_row(conn):
            sqlite_utils.Database(conn)[resolved.table].update(
                resolved.pk_values, update, alter=alter
            )

        try:
            await resolved.db.execute_write_fn(update_row, request=request)
        except Exception as e:
            return _error([str(e)], 400)

        result = {"ok": True}
        if data.get("return"):
            results = await resolved.db.execute(
                resolved.sql, resolved.params, truncate=True
            )
            result["row"] = results.dicts()[0]

        await self.ds.track_event(
            UpdateRowEvent(
                actor=request.actor,
                database=resolved.db.name,
                table=resolved.table,
                pks=resolved.pk_values,
            )
        )

        return Response.json(result, status=200)
