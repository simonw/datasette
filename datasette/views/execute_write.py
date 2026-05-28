from urllib.parse import urlencode

from datasette.resources import DatabaseResource
from datasette.utils import sqlite3
from datasette.utils.asgi import Response

from .base import BaseView, _error
from .query_helpers import (
    QueryValidationError,
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
        status=200,
    ):
        parameter_values = parameter_values or {}
        execution_links = execution_links or []
        parameter_names = []
        analysis_rows = []
        table_columns = await _table_columns(self.ds, db.name)
        hidden_table_names = set(await db.hidden_table_names())
        write_template_tables = {
            table: columns
            for table, columns in table_columns.items()
            if columns and table not in hidden_table_names
        }
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
                "execute_disabled": bool(execute_disabled_reason),
                "execute_disabled_reason": execute_disabled_reason,
                "table_columns": table_columns,
                "write_template_tables": write_template_tables,
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

        try:
            cursor = await db.execute_write(sql, params, request=request)
        except sqlite3.DatabaseError as ex:
            message = str(ex)
            if _wants_json(request, is_json, data):
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

        if cursor.rowcount == -1:
            message = "Query executed"
        else:
            message = "Query executed, {} row{} affected".format(
                cursor.rowcount, "" if cursor.rowcount == 1 else "s"
            )
        if _wants_json(request, is_json, data):
            return _block_framing(
                Response.json(
                    {
                        "ok": True,
                        "message": message,
                        "rowcount": cursor.rowcount,
                        "analysis": _analysis_rows(analysis),
                    }
                )
            )

        inserted_row_url = await _inserted_row_url(self.ds, db, analysis, cursor)
        execution_links = (
            [{"href": inserted_row_url, "label": "View row"}]
            if inserted_row_url
            else []
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
        return _block_framing(
            Response.json(
                await _execute_write_analysis_data(self.ds, db, sql, request.actor)
            )
        )
