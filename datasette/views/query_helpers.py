import json
import re

from datasette.resources import DatabaseResource
from datasette.stored_queries import (
    StoredQuery,
)
from datasette.write_sql import (
    IgnoreWriteSqlOperation,
    QueryWriteRejected,
    RequireWriteSqlPermissions,
    decision_for_write_sql_operation,
    operation_is_write,
)
from datasette.utils import (
    parse_size_limit,
    named_parameters as derive_named_parameters,
    escape_sqlite,
    path_from_row_pks,
    sqlite3,
    validate_sql_select,
    InvalidSql,
)
from datasette.utils.asgi import Forbidden
from datasette.utils.sql_analysis import Operation, SQLAnalysis

_query_name_re = re.compile(r"^[^/\.\n]+$")

_query_fields = {
    "sql",
    "title",
    "description",
    "hide_sql",
    "fragment",
    "parameters",
    "is_private",
    "on_success_message",
    "on_success_redirect",
    "on_error_message",
    "on_error_redirect",
}

_query_create_fields = _query_fields | {"name", "mode", "csrftoken"}
_query_update_fields = _query_fields
_query_write_fields = {
    "on_success_message",
    "on_success_redirect",
    "on_error_message",
    "on_error_redirect",
}

SQL_PARAMETER_FORM_PREFIX = "_sql_param_"


class QueryValidationError(Exception):
    def __init__(self, message, status=400, *, flash=False):
        self.message = message
        self.status = status
        self.flash = flash
        super().__init__(message)


def _actor_id(actor):
    if isinstance(actor, dict):
        return actor.get("id")
    return None


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in {"1", "true", "t", "yes", "on"}
    return bool(value)


def _as_optional_bool(value, name):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"1", "true", "t", "yes", "on"}:
            return True
        if lowered in {"0", "false", "f", "no", "off"}:
            return False
    raise QueryValidationError("{} must be 0 or 1".format(name))


def _query_list_limit(value, default, maximum):
    try:
        return parse_size_limit(value, default, maximum)
    except ValueError as ex:
        raise QueryValidationError(str(ex)) from ex


def _derived_query_parameters(sql):
    parameters = []
    seen = set()
    for parameter in derive_named_parameters(sql):
        if parameter.startswith("_"):
            raise QueryValidationError("Magic parameters are not allowed")
        if parameter not in seen:
            parameters.append(parameter)
            seen.add(parameter)
    return parameters


def _coerce_query_parameters(value, derived):
    if value is None:
        return derived
    if isinstance(value, str):
        parameters = [
            parameter.strip()
            for parameter in re.split(r"[\s,]+", value)
            if parameter.strip()
        ]
    elif isinstance(value, list):
        parameters = value
    else:
        raise QueryValidationError("parameters must be a list of strings")
    if not all(isinstance(parameter, str) for parameter in parameters):
        raise QueryValidationError("parameters must be a list of strings")
    if any(parameter.startswith("_") for parameter in parameters):
        raise QueryValidationError("Magic parameters are not allowed")
    if set(parameters) != set(derived):
        raise QueryValidationError("parameters must match SQL named parameters")
    return parameters


def _analysis_is_write(analysis: SQLAnalysis) -> bool:
    return any(operation_is_write(operation) for operation in analysis.operations)


def _block_framing(response):
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _wants_json(request, is_json, data):
    return (
        is_json
        or request.headers.get("accept") == "application/json"
        or (isinstance(data, dict) and data.get("_json"))
    )


def _query_create_form_error_message(message):
    return {
        "Query name is required": "URL is required",
        "Invalid query name": "Invalid URL",
        "Query name conflicts with a table or view": (
            "URL conflicts with an existing table or view"
        ),
        "Query already exists": "A query already exists at that URL",
    }.get(message, message)


async def _json_or_form_payload(request):
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        body = await request.post_body()
        try:
            return json.loads(body or b"{}"), True
        except json.JSONDecodeError as e:
            raise QueryValidationError("Invalid JSON: {}".format(e))
    return await request.post_vars(), False


async def _check_query_name(db, name, *, existing=False):
    if not name or not isinstance(name, str):
        raise QueryValidationError("Query name is required")
    if not _query_name_re.match(name):
        raise QueryValidationError("Invalid query name")
    if not existing and (await db.table_exists(name) or await db.view_exists(name)):
        raise QueryValidationError("Query name conflicts with a table or view")


async def _analyze_user_query(datasette, db, sql, *, actor):
    if not sql or not isinstance(sql, str):
        raise QueryValidationError("SQL is required")
    derived = _derived_query_parameters(sql)
    params = {parameter: "" for parameter in derived}
    try:
        analysis = await db.analyze_sql(sql, params)
    except sqlite3.DatabaseError as ex:
        raise QueryValidationError("Could not analyze query: {}".format(ex)) from ex

    is_write = _analysis_is_write(analysis)
    if is_write:
        try:
            await datasette.ensure_query_write_permissions(
                db.name, sql, actor=actor, analysis=analysis
            )
        except QueryWriteRejected as ex:
            raise QueryValidationError(ex.message, status=403, flash=True) from ex
        except Forbidden as ex:
            raise QueryValidationError(str(ex), status=403) from ex
    else:
        try:
            validate_sql_select(sql)
        except InvalidSql as ex:
            raise QueryValidationError(str(ex)) from ex
    return is_write, derived, analysis


def _display_operations(analysis: SQLAnalysis) -> list[Operation]:
    operations = []
    for operation in analysis.operations:
        if isinstance(
            decision_for_write_sql_operation(operation), IgnoreWriteSqlOperation
        ):
            continue
        operations.append(operation)
    return operations


def _analysis_rows(analysis: SQLAnalysis) -> list[dict[str, object]]:
    rows = []
    for operation in _display_operations(analysis):
        decision = decision_for_write_sql_operation(operation)
        required_permission = (
            ", ".join(permission.action for permission in decision.permissions)
            if isinstance(decision, RequireWriteSqlPermissions)
            else ""
        )
        rows.append(
            {
                "operation": operation.operation,
                "database": operation.database,
                "table": operation.table or operation.target,
                "required_permission": required_permission,
                "source": operation.source,
            }
        )
    return rows


async def _analysis_rows_with_permissions(
    datasette, analysis: SQLAnalysis, actor
) -> list[dict[str, object]]:
    rows = _analysis_rows(analysis)
    is_write = _analysis_is_write(analysis)
    for row, operation in zip(rows, _display_operations(analysis)):
        decision = decision_for_write_sql_operation(operation)
        if isinstance(decision, RequireWriteSqlPermissions):
            row["allowed"] = True
            for permission in decision.permissions:
                if not await datasette.allowed(
                    action=permission.action,
                    resource=permission.resource,
                    actor=actor,
                ):
                    row["allowed"] = False
                    break
        elif is_write:
            row["allowed"] = False
        else:
            row["allowed"] = None
    return rows


def _execute_write_disabled_reason(sql, analysis_error, analysis_rows):
    if not (sql and sql.strip()):
        return "Enter writable SQL before executing."
    if analysis_error:
        return analysis_error
    if any(row.get("allowed") is False for row in analysis_rows):
        return "You do not have permission for every operation listed above."
    return None


def _coerce_execute_write_payload(data, is_json):
    if not isinstance(data, dict):
        raise QueryValidationError("JSON must be a dictionary")
    if is_json:
        invalid_keys = set(data) - {"sql", "params"}
        if invalid_keys:
            raise QueryValidationError(
                "Invalid keys: {}".format(", ".join(sorted(invalid_keys)))
            )
        params = data.get("params") or {}
    else:
        params = {}
        for key, value in data.items():
            if key in {"sql", "csrftoken", "_json"}:
                continue
            if key.startswith(SQL_PARAMETER_FORM_PREFIX):
                key = key[len(SQL_PARAMETER_FORM_PREFIX) :]
            params[key] = value
    if not isinstance(params, dict):
        raise QueryValidationError("params must be a dictionary")
    return data.get("sql"), params


async def _prepare_execute_write(datasette, db, sql, params, actor):
    if not sql or not isinstance(sql, str):
        raise QueryValidationError("SQL is required")
    parameter_names = _derived_query_parameters(sql)
    extra_params = set(params) - set(parameter_names)
    if extra_params:
        raise QueryValidationError(
            "Unknown parameters: {}".format(", ".join(sorted(extra_params)))
        )
    params = {name: params.get(name, "") for name in parameter_names}
    try:
        analysis = await db.analyze_sql(sql, params)
    except sqlite3.DatabaseError as ex:
        raise QueryValidationError("Could not analyze query: {}".format(ex)) from ex
    if not _analysis_is_write(analysis):
        raise QueryValidationError(
            "Use /-/query for read-only SQL; this endpoint only executes writes"
        )
    try:
        await datasette.ensure_query_write_permissions(
            db.name, sql, actor=actor, analysis=analysis
        )
    except QueryWriteRejected as ex:
        raise QueryValidationError(ex.message, status=403, flash=True) from ex
    except Forbidden as ex:
        raise QueryValidationError(str(ex), status=403) from ex
    return parameter_names, params, analysis


async def _ensure_stored_query_execution_permissions(
    datasette, db, query: StoredQuery, actor
):
    if query.is_trusted:
        return
    if query.is_write:
        await datasette.ensure_permission(
            action="execute-write-sql",
            resource=DatabaseResource(db.name),
            actor=actor,
        )
        await datasette.ensure_query_write_permissions(db.name, query.sql, actor=actor)
    else:
        await datasette.ensure_permission(
            action="execute-sql",
            resource=DatabaseResource(db.name),
            actor=actor,
        )


async def _execute_write_analysis_data(datasette, db, sql, actor):
    parameter_names = []
    analysis_rows = []
    analysis_error = None
    if sql:
        try:
            parameter_names = _derived_query_parameters(sql)
            params = {parameter: "" for parameter in parameter_names}
            analysis = await db.analyze_sql(sql, params)
            if _analysis_is_write(analysis):
                analysis_rows = await _analysis_rows_with_permissions(
                    datasette, analysis, actor
                )
            else:
                analysis_error = (
                    "Use /-/query for read-only SQL; "
                    "this endpoint only executes writes"
                )
        except (QueryValidationError, sqlite3.DatabaseError) as ex:
            analysis_error = getattr(ex, "message", str(ex))
    execute_disabled_reason = _execute_write_disabled_reason(
        sql, analysis_error, analysis_rows
    )
    return {
        "ok": analysis_error is None,
        "parameters": parameter_names,
        "analysis_error": analysis_error,
        "analysis_rows": analysis_rows,
        "execute_disabled": bool(execute_disabled_reason),
        "execute_disabled_reason": execute_disabled_reason,
    }


async def _query_create_analysis_data(datasette, db, sql, actor):
    has_sql = bool(sql and sql.strip())
    parameter_names = []
    analysis_rows = []
    analysis_error = None
    analysis: SQLAnalysis | None = None
    if has_sql:
        try:
            parameter_names = _derived_query_parameters(sql)
            params = {parameter: "" for parameter in parameter_names}
            analysis = await db.analyze_sql(sql, params)
            analysis_rows = await _analysis_rows_with_permissions(
                datasette, analysis, actor
            )
        except (QueryValidationError, sqlite3.DatabaseError) as ex:
            analysis_error = getattr(ex, "message", str(ex))
    return {
        "ok": analysis_error is None,
        "parameters": parameter_names,
        "analysis_error": analysis_error,
        "analysis_rows": analysis_rows,
        "has_sql": has_sql,
        "analysis_is_write": _analysis_is_write(analysis) if analysis else False,
        "save_disabled": bool(
            (not has_sql)
            or analysis_error
            or any(row["allowed"] is False for row in analysis_rows)
        ),
    }


async def _query_create_form_context(
    datasette,
    request,
    db,
    *,
    sql="",
    name="",
    title="",
    description="",
    is_private=True,
):
    analysis_data = await _query_create_analysis_data(datasette, db, sql, request.actor)
    return {
        "database": db.name,
        "database_color": db.color,
        "sql": sql,
        "name": name,
        "title": title,
        "description": description,
        "is_private": is_private,
        **analysis_data,
    }


async def _query_edit_form_context(
    datasette,
    request,
    db,
    existing: StoredQuery,
    *,
    sql=None,
    title=None,
    description=None,
    is_private=None,
):
    sql = existing.sql if sql is None else sql
    title = existing.title if title is None else title
    description = existing.description if description is None else description
    is_private = existing.is_private if is_private is None else is_private
    analysis_data = await _query_create_analysis_data(datasette, db, sql, request.actor)
    return {
        "database": db.name,
        "database_color": db.color,
        "name": existing.name,
        "sql": sql,
        "title": title or "",
        "description": description or "",
        "is_private": is_private,
        "query_url": datasette.urls.table(db.name, existing.name),
        **analysis_data,
    }


async def _inserted_row_url(datasette, db, analysis, cursor):
    if cursor.rowcount != 1:
        return None
    lastrowid = getattr(cursor, "lastrowid", None)
    if lastrowid is None:
        return None
    direct_inserts = [
        operation
        for operation in analysis.operations
        if operation.operation == "insert"
        and operation.target_type == "table"
        and not operation.internal
        and operation.source is None
        and operation.database == db.name
    ]
    if len(direct_inserts) != 1:
        return None
    table = direct_inserts[0].table
    if table is None:
        return None
    pks = await db.primary_keys(table)
    use_rowid = not pks
    select = (
        "rowid"
        if use_rowid
        else ", ".join(escape_sqlite(primary_key) for primary_key in pks)
    )
    try:
        result = await db.execute(
            "select {} from {} where rowid = ?".format(select, escape_sqlite(table)),
            [lastrowid],
        )
    except sqlite3.DatabaseError:
        return None
    row = result.first()
    if row is None:
        return None
    row_path = path_from_row_pks(row, pks, use_rowid)
    return datasette.urls.row(db.name, table, row_path)


def _apply_query_data_types(data):
    typed = dict(data)
    for key in ("hide_sql", "is_private"):
        if key in typed:
            typed[key] = _as_bool(typed[key])
    return typed


async def _prepare_query_create(datasette, request, db, data):
    invalid_keys = set(data) - _query_create_fields
    if invalid_keys:
        raise QueryValidationError(
            "Invalid keys: {}".format(", ".join(sorted(invalid_keys)))
        )

    data = _apply_query_data_types(data)
    name = data.get("name")
    await _check_query_name(db, name)
    if await datasette.get_query(db.name, name) is not None:
        raise QueryValidationError("Query already exists")

    is_write, derived, analysis = await _analyze_user_query(
        datasette,
        db,
        data.get("sql"),
        actor=request.actor,
    )
    if not is_write and any(data.get(field) for field in _query_write_fields):
        raise QueryValidationError("Writable query fields require writable SQL")

    parameters = _coerce_query_parameters(
        data.get("parameters"),
        derived,
    )
    return {
        "name": name,
        "sql": data["sql"],
        "title": data.get("title"),
        "description": data.get("description"),
        "hide_sql": _as_bool(data.get("hide_sql")),
        "fragment": data.get("fragment"),
        "parameters": parameters,
        "is_write": is_write,
        "is_private": _as_bool(data.get("is_private", True)),
        "is_trusted": False,
        "source": "user",
        "owner_id": _actor_id(request.actor),
        "on_success_message": data.get("on_success_message"),
        "on_success_redirect": data.get("on_success_redirect"),
        "on_error_message": data.get("on_error_message"),
        "on_error_redirect": data.get("on_error_redirect"),
        "analysis": analysis,
    }


async def _prepare_query_update(datasette, request, db, existing: StoredQuery, update):
    invalid_keys = set(update) - _query_update_fields
    if invalid_keys:
        raise QueryValidationError(
            "Invalid keys: {}".format(", ".join(sorted(invalid_keys)))
        )

    update = _apply_query_data_types(update)
    sql = update.get("sql", existing.sql)
    query_is_write = existing.is_write
    derived = _derived_query_parameters(sql)
    parameters = None

    if "sql" in update:
        query_is_write, derived, _ = await _analyze_user_query(
            datasette,
            db,
            sql,
            actor=request.actor,
        )

    if "parameters" in update:
        parameters = _coerce_query_parameters(
            update.get("parameters"),
            derived,
        )
    elif "sql" in update:
        parameters = derived

    if not query_is_write and any(update.get(field) for field in _query_write_fields):
        raise QueryValidationError("Writable query fields require writable SQL")

    field_values = {
        "sql": sql,
        "title": update.get("title"),
        "description": update.get("description"),
        "hide_sql": update.get("hide_sql"),
        "fragment": update.get("fragment"),
        "parameters": parameters,
        "is_write": query_is_write,
        "is_private": update.get("is_private"),
        "on_success_message": update.get("on_success_message"),
        "on_success_redirect": update.get("on_success_redirect"),
        "on_error_message": update.get("on_error_message"),
        "on_error_redirect": update.get("on_error_redirect"),
    }
    update_kwargs = {}
    for field_name, value in field_values.items():
        if field_name in update:
            update_kwargs[field_name] = value
    if parameters is not None:
        update_kwargs["parameters"] = parameters
    if "sql" in update:
        update_kwargs["is_write"] = query_is_write
    return update_kwargs


async def _table_columns(datasette, database_name):
    internal_db = datasette.get_internal_database()
    result = await internal_db.execute(
        "select table_name, name from catalog_columns where database_name = ?",
        [database_name],
    )
    table_columns = {}
    for row in result.rows:
        table_columns.setdefault(row["table_name"], []).append(row["name"])
    # Add views
    db = datasette.get_database(database_name)
    for view_name in await db.view_names():
        table_columns[view_name] = []
    return table_columns


def _column_completion(name, type_):
    # A @codemirror/lang-sql Completion object for a single column. boost keeps
    # columns ranked above bare SQL keywords in the autocomplete popup.
    completion = {
        "label": name,
        "type": "property",
        "boost": 10,
    }
    if type_:
        completion["detail"] = type_
    return completion


async def _editor_schema(datasette, database_name):
    """
    Build a lang-sql SQLNamespace for the CodeMirror SQL editor autocomplete.

    Returns a dict keyed by table or view name. Table values are lists of
    Completion objects (one per column, carrying the column's SQLite type as
    ``detail``). Views are wrapped in a ``{"self": Completion, "children": [...]}``
    container so the popup can label them as views while still completing their
    real columns. See @codemirror/lang-sql >= 6.6 SQLNamespace / Completion.
    """
    internal_db = datasette.get_internal_database()
    result = await internal_db.execute(
        "select table_name, name, type from catalog_columns where database_name = ?",
        [database_name],
    )
    schema = {}
    for row in result.rows:
        schema.setdefault(row["table_name"], []).append(
            _column_completion(row["name"], row["type"])
        )
    # Views are not represented in catalog_columns, so pull their real columns
    # directly (PRAGMA table_xinfo works against views too).
    db = datasette.get_database(database_name)
    for view_name in await db.view_names():
        columns = await db.table_column_details(view_name)
        schema[view_name] = {
            "self": {
                "label": view_name,
                "type": "class",
                "detail": "view",
            },
            "children": [
                _column_completion(column.name, column.type) for column in columns
            ],
        }
    return schema
