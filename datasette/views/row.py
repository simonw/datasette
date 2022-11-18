from datasette.utils.asgi import NotFound, Forbidden, Response
from datasette.database import QueryInterrupted
from .base import DataView, BaseView, _error
from datasette.utils import (
    tilde_decode,
    urlsafe_components,
    to_css_class,
    escape_sqlite,
    row_sql_params_pks,
)
import json
import sqlite_utils
from .table import display_columns_and_rows


class RowView(DataView):
    name = "row"

    async def data(self, request, default_labels=False):
        resolved = await self.ds.resolve_row(request)
        database = resolved.db.name
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
            )
            for column in display_columns:
                column["sortable"] = False
            return {
                "private": private,
                "foreign_key_tables": await self.foreign_key_tables(
                    database, table, pk_values
                ),
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
            }

        data = {
            "database": database,
            "table": table,
            "rows": rows,
            "columns": columns,
            "primary_keys": resolved.pks,
            "primary_key_values": pk_values,
            "units": self.ds.table_metadata(database, table).get("units", {}),
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


class RowDeleteView(BaseView):
    name = "row-delete"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        from datasette.app import DatabaseNotFound, TableNotFound, RowNotFound

        try:
            resolved = await self.ds.resolve_row(request)
        except DatabaseNotFound as e:
            return _error(["Database not found: {}".format(e.database_name)], 404)
        except TableNotFound as e:
            return _error(["Table not found: {}".format(e.table)], 404)
        except RowNotFound as e:
            return _error(["Record not found: {}".format(e.pk_values)], 404)
        db = resolved.db
        database_name = db.name
        table = resolved.table
        pk_values = resolved.pk_values

        # Ensure user has permission to delete this row
        if not await self.ds.permission_allowed(
            request.actor, "delete-row", resource=(database_name, table)
        ):
            return _error(["Permission denied"], 403)

        # Delete table
        def delete_row(conn):
            sqlite_utils.Database(conn)[table].delete(pk_values)

        await db.execute_write_fn(delete_row)
        return Response.json({"ok": True}, status=200)
