from datasette.utils.asgi import NotFound, Forbidden, Response
from datasette.database import QueryInterrupted
from datasette.events import UpdateRowEvent, DeleteRowEvent
from .base import DataView, BaseView, _error
from datasette.utils import (
    make_slot_function,
    to_css_class,
    escape_sqlite,
)
import json
import sqlite_utils
from .table import display_columns_and_rows


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
            permissions=[
                ("view-table", (database, table)),
                ("view-database", database),
                "view-instance",
            ],
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

        async def template_data():
            display_columns, display_rows = await display_columns_and_rows(
                self.ds,
                database,
                table,
                results.description,
                rows,
                link_column=False,
                truncate_cells=0,
                request=request,
            )
            for column in display_columns:
                column["sortable"] = False
            return {
                "private": private,
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
                "metadata": (self.ds.metadata("databases") or {})
                .get(database, {})
                .get("tables", {})
                .get(table, {}),
                "top_row": make_slot_function(
                    "top_row",
                    self.ds,
                    request,
                    database=resolved.db.name,
                    table=resolved.table,
                    row=rows[0],
                ),
            }

        data = {
            "database": database,
            "table": table,
            "rows": rows,
            "columns": columns,
            "primary_keys": resolved.pks,
            "primary_key_values": pk_values,
            "units": (await self.ds.table_config(database, table)).get("units", {}),
        }

        if "foreign_key_tables" in (request.args.get("_extras") or "").split(","):
            data["foreign_key_tables"] = await self.foreign_key_tables(
                database, table, pk_values
            )

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
    if not await datasette.permission_allowed(
        request.actor, permission, resource=(resolved.db.name, resolved.table)
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
            await resolved.db.execute_write_fn(delete_row)
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
        if not "update" in data or not isinstance(data["update"], dict):
            return _error(["JSON must contain an update dictionary"])

        invalid_keys = set(data.keys()) - {"update", "return", "alter"}
        if invalid_keys:
            return _error(["Invalid keys: {}".format(", ".join(invalid_keys))])

        update = data["update"]

        alter = data.get("alter")
        if alter and not await self.ds.permission_allowed(
            request.actor, "alter-table", resource=(resolved.db.name, resolved.table)
        ):
            return _error(["Permission denied for alter-table"], 403)

        def update_row(conn):
            sqlite_utils.Database(conn)[resolved.table].update(
                resolved.pk_values, update, alter=alter
            )

        try:
            await resolved.db.execute_write_fn(update_row)
        except Exception as e:
            return _error([str(e)], 400)

        result = {"ok": True}
        if data.get("return"):
            results = await resolved.db.execute(
                resolved.sql, resolved.params, truncate=True
            )
            rows = list(results.rows)
            result["row"] = dict(rows[0])

        await self.ds.track_event(
            UpdateRowEvent(
                actor=request.actor,
                database=resolved.db.name,
                table=resolved.table,
                pks=resolved.pk_values,
            )
        )

        return Response.json(result, status=200)
