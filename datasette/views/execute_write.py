import re
from urllib.parse import urlencode

from datasette.resources import DatabaseResource
from datasette.utils import UNSTABLE_API_MESSAGE, sqlite3
from datasette.utils.asgi import Response

from .base import BaseView, _error
from .database import display_rows as display_query_rows
from .query_helpers import (
    QueryValidationError,
    SQL_PARAMETER_FORM_PREFIX,
    _analysis_is_write,
    _analysis_rows,
    _analysis_rows_with_permissions,
    _block_framing,
    _coerce_execute_write_payload,
    _derived_query_parameters,
    _execute_write_analysis_data,
    _execute_write_disabled_reason,
    _inserted_row_url,
    _json_or_form_payload,
    _prepare_execute_write,
    _table_columns,
    _wants_json,
)

WRITE_TEMPLATE_LABELS = {
    "insert": "Insert row",
    "update": "Update rows",
    "delete": "Delete rows",
}
WRITE_TEMPLATE_OPERATIONS = tuple(WRITE_TEMPLATE_LABELS)
CREATE_TABLE_TEMPLATE_SQL = "\n".join(
    (
        "create table new_table (",
        "  id integer primary key,",
        "  name text",
        "  -- created text default (datetime('now'))",
        ")",
    )
)


def _parameter_names(columns):
    seen = set()
    names = {}
    for column in columns:
        base = re.sub(r"[^a-z0-9_]+", "_", column.lower())
        base = base.strip("_") or "value"
        if base[0].isdigit():
            base = "p_{}".format(base)
        name = base
        index = 2
        while name in seen:
            name = "{}_{}".format(base, index)
            index += 1
        seen.add(name)
        names[column] = name
    return names


def _quote_identifier(identifier):
    return '"{}"'.format(identifier.replace('"', '""'))


def _preferred_where_column(table, columns):
    lower_table_id = "{}_id".format(table.lower())
    return (
        next((column for column in columns if column.lower() == "id"), None)
        or next(
            (column for column in columns if column.lower() == lower_table_id), None
        )
        or columns[0]
    )


def _auto_incrementing_primary_key(columns):
    primary_keys = [column for column in columns if column.is_pk]
    if len(primary_keys) != 1:
        return None
    primary_key = primary_keys[0]
    if primary_key.type and primary_key.type.lower() == "integer":
        return primary_key.name
    return None


def _insert_template_sql(table, columns):
    column_names = [column.name for column in columns]
    auto_pk = _auto_incrementing_primary_key(columns)
    insert_columns = [column for column in column_names if column != auto_pk]
    if not insert_columns:
        return "insert into {}\ndefault values".format(_quote_identifier(table))
    names = _parameter_names(insert_columns)
    return "\n".join(
        (
            "insert into {} (".format(_quote_identifier(table)),
            ",\n".join(
                "  {}".format(_quote_identifier(column)) for column in insert_columns
            ),
            ")",
            "values (",
            ",\n".join("  :{}".format(names[column]) for column in insert_columns),
            ")",
        )
    )


def _update_template_sql(table, columns):
    column_names = [column.name for column in columns]
    names = _parameter_names(column_names)
    where_column = _preferred_where_column(table, column_names)
    set_columns = [column for column in column_names if column != where_column]
    if not set_columns:
        return "\n".join(
            (
                "update {}".format(_quote_identifier(table)),
                "set {} = :new_{}".format(
                    _quote_identifier(where_column), names[where_column]
                ),
                "where {} = :{}".format(
                    _quote_identifier(where_column), names[where_column]
                ),
            )
        )
    return "\n".join(
        (
            "update {}".format(_quote_identifier(table)),
            "set "
            + ",\n".join(
                "{}{} = :{}".format(
                    "    " if index else "",
                    _quote_identifier(column),
                    names[column],
                )
                for index, column in enumerate(set_columns)
            ),
            "where {} = :{}".format(
                _quote_identifier(where_column), names[where_column]
            ),
        )
    )


def _delete_template_sql(table, columns):
    column_names = [column.name for column in columns]
    names = _parameter_names(column_names)
    where_column = _preferred_where_column(table, column_names)
    return "\n".join(
        (
            "delete from {}".format(_quote_identifier(table)),
            "where {} = :{}".format(
                _quote_identifier(where_column), names[where_column]
            ),
        )
    )


def _template_sqls_for_table(table, columns):
    return {
        "insert": _insert_template_sql(table, columns),
        "update": _update_template_sql(table, columns),
        "delete": _delete_template_sql(table, columns),
    }


async def _template_sql_allowed(datasette, db, sql, actor):
    params = {parameter: "" for parameter in _derived_query_parameters(sql)}
    try:
        analysis = await db.analyze_sql(sql, params)
    except sqlite3.DatabaseError:
        return False
    if not _analysis_is_write(analysis):
        return False
    analysis_rows = await _analysis_rows_with_permissions(datasette, analysis, actor)
    return _execute_write_disabled_reason(sql, None, analysis_rows) is None


async def _write_template_tables(
    datasette, db, table_columns, hidden_table_names, actor
):
    write_template_tables = {}
    for table in table_columns:
        if table in hidden_table_names or not table_columns[table]:
            continue
        column_details = [
            column
            for column in await db.table_column_details(table)
            if not column.hidden
        ]
        if not column_details:
            continue
        templates = {}
        for operation, sql in _template_sqls_for_table(table, column_details).items():
            if await _template_sql_allowed(datasette, db, sql, actor):
                templates[operation] = sql
        if templates:
            write_template_tables[table] = {
                "templates": templates,
            }
    return write_template_tables


def _write_template_operations(write_template_tables):
    operations = []
    for operation in WRITE_TEMPLATE_OPERATIONS:
        if any(
            operation in table["templates"] for table in write_template_tables.values()
        ):
            operations.append(
                {
                    "name": operation,
                    "label": WRITE_TEMPLATE_LABELS[operation],
                }
            )
    return operations


async def _create_table_template_sql(datasette, db, actor):
    if await datasette.allowed(
        action="create-table",
        resource=DatabaseResource(db.name),
        actor=actor,
    ):
        return CREATE_TABLE_TEMPLATE_SQL
    return None


def _analysis_changes_schema(analysis):
    return any(
        operation.operation in {"create", "alter", "drop"}
        for operation in analysis.operations
    )


class ExecuteWriteView(BaseView):
    name = "execute-write"
    has_json_alternate = False

    async def _render_form(
        self,
        request,
        db,
        *,
        sql="",
        parameter_values=None,
        analysis=None,
        analysis_error=None,
        execution_message=None,
        execution_links=None,
        execution_ok=None,
        execute_write_returns_rows=False,
        execute_write_columns=None,
        execute_write_display_rows=None,
        execute_write_truncated=False,
        status=200,
    ):
        parameter_values = parameter_values or {}
        execution_links = execution_links or []
        execute_write_columns = execute_write_columns or []
        execute_write_display_rows = execute_write_display_rows or []
        parameter_names = []
        analysis_rows = []
        table_columns = await _table_columns(self.ds, db.name)
        hidden_table_names = set(await db.hidden_table_names())
        write_template_tables = await _write_template_tables(
            self.ds, db, table_columns, hidden_table_names, request.actor
        )
        write_template_operations = _write_template_operations(write_template_tables)
        write_create_table_template_sql = await _create_table_template_sql(
            self.ds, db, request.actor
        )
        if sql and analysis_error is None:
            try:
                parameter_names = _derived_query_parameters(sql)
                if analysis is None:
                    params = {parameter: "" for parameter in parameter_names}
                    analysis = await db.analyze_sql(sql, params)
                if _analysis_is_write(analysis):
                    analysis_rows = await _analysis_rows_with_permissions(
                        self.ds, analysis, request.actor
                    )
                else:
                    analysis_error = (
                        "Use /-/query for read-only SQL; "
                        "this endpoint only executes writes"
                    )
            except (QueryValidationError, sqlite3.DatabaseError) as ex:
                analysis_error = getattr(ex, "message", str(ex))

        allow_save_query = await self.ds.allowed(
            action="execute-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ) and await self.ds.allowed(
            action="store-query",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        )
        save_query_base_url = None
        save_query_url = None
        execute_disabled_reason = _execute_write_disabled_reason(
            sql, analysis_error, analysis_rows
        )
        if allow_save_query:
            save_query_base_url = self.ds.urls.database(db.name) + "/-/queries/store"
            if not execute_disabled_reason:
                save_query_url = save_query_base_url + "?" + urlencode({"sql": sql})

        response = await self.render(
            ["execute_write.html"],
            request,
            {
                "database": db.name,
                "database_color": db.color,
                "sql": sql,
                "parameter_names": parameter_names,
                "parameter_values": parameter_values,
                "analysis_error": analysis_error,
                "analysis_rows": analysis_rows,
                "execution_message": execution_message,
                "execution_links": execution_links,
                "execution_ok": execution_ok,
                "execute_write_returns_rows": execute_write_returns_rows,
                "execute_write_columns": execute_write_columns,
                "execute_write_display_rows": execute_write_display_rows,
                "execute_write_truncated": execute_write_truncated,
                "sql_parameter_name_prefix": SQL_PARAMETER_FORM_PREFIX,
                "execute_disabled": bool(execute_disabled_reason),
                "execute_disabled_reason": execute_disabled_reason,
                "table_columns": table_columns,
                "write_template_tables": write_template_tables,
                "write_template_operations": write_template_operations,
                "write_create_table_template_sql": write_create_table_template_sql,
                "save_query_url": save_query_url,
                "save_query_base_url": save_query_base_url,
            },
        )
        response.status = status
        return _block_framing(response)

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        await self.ds.ensure_permission(
            action="execute-write-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        )
        if not db.is_mutable:
            return _block_framing(
                _error(
                    ["Cannot execute write SQL because this database is immutable."],
                    403,
                )
            )
        return await self._render_form(
            request,
            db,
            sql=request.args.get("sql") or "",
        )

    async def post(self, request):
        db = await self.ds.resolve_database(request)
        if not await self.ds.allowed(
            action="execute-write-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            return _block_framing(
                _error(["Permission denied: need execute-write-sql"], 403)
            )
        if not db.is_mutable:
            return _block_framing(_error(["Database is immutable"], 403))

        data = {}
        is_json = request.headers.get("content-type", "").startswith("application/json")
        sql = ""
        provided_params = {}
        try:
            data, is_json = await _json_or_form_payload(request)
            sql, provided_params = _coerce_execute_write_payload(data, is_json)
            parameter_names, params, analysis = await _prepare_execute_write(
                self.ds, db, sql, provided_params, request.actor
            )
        except QueryValidationError as ex:
            if _wants_json(request, is_json, data):
                return _block_framing(_error([ex.message], ex.status))
            if ex.flash:
                self.ds.add_message(request, ex.message, self.ds.ERROR)
            return await self._render_form(
                request,
                db,
                sql=sql or "",
                parameter_values=provided_params,
                analysis_error=None if ex.flash else ex.message,
                execution_message=None if ex.flash else ex.message,
                execution_ok=False,
                status=ex.status,
            )

        wants_json = _wants_json(request, is_json, data)
        try:
            execute_write_kwargs = {"request": request}
            cursor = await db.execute_write(sql, params, **execute_write_kwargs)
        except sqlite3.DatabaseError as ex:
            message = str(ex)
            if wants_json:
                return _block_framing(_error([message], 400))
            return await self._render_form(
                request,
                db,
                sql=sql,
                parameter_values=params,
                analysis=analysis,
                execution_message=message,
                execution_ok=False,
                status=400,
            )

        if _analysis_changes_schema(analysis):
            await self.ds.refresh_schemas(force=True)

        if cursor.rowcount == -1:
            message = "Query executed"
        else:
            message = "Query executed, {} row{} affected".format(
                cursor.rowcount, "" if cursor.rowcount == 1 else "s"
            )
        if wants_json:
            data = {
                "ok": True,
                "message": message,
                "rowcount": cursor.rowcount,
                "rows": [],
                "truncated": False,
                "analysis": _analysis_rows(analysis),
            }
            if cursor.description is not None:
                data["rows"] = [dict(row) for row in cursor.fetchall()]
                data["truncated"] = cursor.truncated
            return _block_framing(Response.json(data))

        inserted_row_url = await _inserted_row_url(self.ds, db, analysis, cursor)
        execution_links = (
            [{"href": inserted_row_url, "label": "View row"}]
            if inserted_row_url
            else []
        )
        execute_write_returns_rows = cursor.description is not None
        execute_write_columns = []
        execute_write_display_rows = []
        if execute_write_returns_rows:
            execute_write_columns = [
                description[0] for description in cursor.description
            ]
            execute_write_display_rows = await display_query_rows(
                self.ds,
                db.name,
                request,
                cursor.fetchall(),
                execute_write_columns,
            )
        return await self._render_form(
            request,
            db,
            sql=sql,
            parameter_values={name: params.get(name, "") for name in parameter_names},
            analysis=analysis,
            execution_message=message,
            execution_links=execution_links,
            execution_ok=True,
            execute_write_returns_rows=execute_write_returns_rows,
            execute_write_columns=execute_write_columns,
            execute_write_display_rows=execute_write_display_rows,
            execute_write_truncated=cursor.truncated,
        )


class ExecuteWriteAnalyzeView(BaseView):
    name = "execute-write-analyze"
    has_json_alternate = False

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        if not await self.ds.allowed(
            action="execute-write-sql",
            resource=DatabaseResource(db.name),
            actor=request.actor,
        ):
            return _block_framing(
                _error(["Permission denied: need execute-write-sql"], 403)
            )

        invalid_keys = set(request.args) - {"sql"}
        if invalid_keys:
            return _block_framing(
                _error(
                    ["Invalid keys: {}".format(", ".join(sorted(invalid_keys)))],
                    400,
                )
            )
        sql = request.args.get("sql") or ""
        analysis = await _execute_write_analysis_data(self.ds, db, sql, request.actor)
        analysis["unstable"] = UNSTABLE_API_MESSAGE
        return _block_framing(Response.json(analysis))
