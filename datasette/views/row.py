from datasette.utils.asgi import NotFound, Forbidden, Response
from datasette.database import QueryInterrupted
from datasette.events import UpdateRowEvent, DeleteRowEvent
from datasette.resources import TableResource
from .base import DataView, BaseView, _error
from datasette.utils import (
    await_me_maybe,
    CustomRow,
    make_slot_function,
    path_from_row_pks,
    to_css_class,
    escape_sqlite,
)
from datasette.plugins import pm
import json
import markupsafe
import sqlite_utils
from datasette.extras import extra_names_from_request
from .table import (
    display_columns_and_rows,
    _table_page_data,
    row_label_from_label_column,
)
from .table_extras import RowExtraContext, resolve_row_extras, table_extra_registry


class RowView(DataView):
    name = "row"

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
            is_table = await db.table_exists(table)
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

            label_column = await db.label_column_for_table(table) if is_table else None
            row_path = path_from_row_pks(rows[0], pks, False)
            pk_path = path_from_row_pks(rows[0], pks, False, False)
            row_label = row_label_from_label_column(expanded_rows[0], label_column)
            for display_row in display_rows:
                display_row.pk_path = pk_path
                display_row.row_path = row_path
                display_row.row_label = row_label

            row_action_label = pk_path
            if row_label and row_label != pk_path:
                row_action_label = "{} {}".format(pk_path, row_label)

            row_action_permissions = {}
            if is_table and db.is_mutable:
                row_action_permissions = await self.ds.allowed_many(
                    actions=["update-row", "delete-row"],
                    resource=TableResource(database=database, table=table),
                    actor=request.actor,
                )

            row_actions = []
            if row_action_permissions.get("update-row"):
                attrs = {
                    "aria-label": "Edit row {}".format(row_action_label),
                    "data-row": row_path,
                    "data-row-action": "edit",
                }
                if row_label:
                    attrs["data-row-label"] = row_label
                row_actions.append(
                    {
                        "type": "button",
                        "label": "Edit row",
                        "description": "Open a dialog to edit this row.",
                        "attrs": attrs,
                    }
                )
            if row_action_permissions.get("delete-row"):
                attrs = {
                    "aria-label": "Delete row {}".format(row_action_label),
                    "data-row": row_path,
                    "data-row-action": "delete",
                }
                if row_label:
                    attrs["data-row-label"] = row_label
                row_actions.append(
                    {
                        "type": "button",
                        "label": "Delete row",
                        "description": "Open a confirmation dialog to delete this row.",
                        "attrs": attrs,
                    }
                )
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
                "row_mutation_ui": any(row_action_permissions.values()),
                "table_page_data": await _table_page_data(
                    self.ds,
                    request,
                    db,
                    database,
                    table,
                    not is_table,
                    None,
                    None,
                ),
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


ROW_FLASH_LABEL_MAX_LENGTH = 80


def _truncated_row_flash_label(label):
    label = " ".join(str(label).split())
    if len(label) <= ROW_FLASH_LABEL_MAX_LENGTH:
        return label
    return label[: ROW_FLASH_LABEL_MAX_LENGTH - 1] + "\u2026"


async def _row_flash_message(db, action, resolved, row=None):
    pk_label = ", ".join(resolved.pk_values)
    label_column = await db.label_column_for_table(resolved.table)
    label = row_label_from_label_column(row or resolved.row, label_column)
    if label:
        label = _truncated_row_flash_label(label)
    if label and label != pk_label:
        return "{} row {} ({})".format(action, pk_label, label)
    return "{} row {}".format(action, pk_label)


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

        if request.args.get("_redirect_to_table"):
            table_url = self.ds.urls.table(resolved.db.name, resolved.table)
            self.ds.add_message(
                request,
                await _row_flash_message(resolved.db, "Deleted", resolved),
                self.ds.INFO,
            )
            return Response.json({"ok": True, "redirect": str(table_url)}, status=200)

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

        try:
            data = await request.json()
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
        returned_row = None
        if data.get("return"):
            results = await resolved.db.execute(
                resolved.sql, resolved.params, truncate=True
            )
            returned_row = results.dicts()[0]
            result["row"] = returned_row

        await self.ds.track_event(
            UpdateRowEvent(
                actor=request.actor,
                database=resolved.db.name,
                table=resolved.table,
                pks=resolved.pk_values,
            )
        )

        if request.args.get("_message"):
            message_row = returned_row
            if message_row is None:
                results = await resolved.db.execute(
                    resolved.sql, resolved.params, truncate=True
                )
                message_row = results.first()
            self.ds.add_message(
                request,
                await _row_flash_message(
                    resolved.db, "Updated", resolved, row=message_row
                ),
                self.ds.INFO,
            )

        return Response.json(result, status=200)
