import json
import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
import sqlite_utils
from sqlite_utils.db import DEFAULT as SQLITE_UTILS_DEFAULT

from datasette.column_types import SQLiteType
from datasette.events import AlterTableEvent, CreateTableEvent, InsertRowsEvent
from datasette.resources import DatabaseResource, TableResource
from datasette.utils import sqlite3
from datasette.utils.asgi import NotFound, Response

from .base import BaseView, _error

CREATE_TABLE_COLUMN_TYPES = ["text", "integer", "float", "blob"]
CREATE_TABLE_SQLITE_TYPES = {
    "text": SQLiteType.TEXT,
    "integer": SQLiteType.INTEGER,
    "float": SQLiteType.REAL,
    "blob": SQLiteType.BLOB,
}
CREATE_TABLE_TYPE_FOR_SQLITE_TYPE = {
    sqlite_type: column_type
    for column_type, sqlite_type in CREATE_TABLE_SQLITE_TYPES.items()
}
ALTER_TABLE_COLUMN_TYPES = CREATE_TABLE_COLUMN_TYPES
ALTER_TABLE_TYPE_FOR_SQLITE_TYPE = {
    SQLiteType.TEXT: "text",
    SQLiteType.INTEGER: "integer",
    SQLiteType.REAL: "float",
    SQLiteType.BLOB: "blob",
}


async def _create_table_ui_context(
    datasette, request, db, database_name, database_action_permissions
):
    if not db.is_mutable:
        return None
    if not database_action_permissions.get("create-table"):
        return None
    data = {
        "path": "{}/-/create".format(datasette.urls.database(database_name)),
        "databaseName": database_name,
        "columnTypes": CREATE_TABLE_COLUMN_TYPES,
    }
    can_set_column_type = await datasette.allowed(
        action="set-column-type",
        resource=TableResource(database=database_name, table="__new_table__"),
        actor=request.actor,
    )
    if can_set_column_type:
        data["customColumnTypes"] = _custom_column_type_options_for_create_table(
            datasette
        )
    return data


def _custom_column_type_options_for_create_table(datasette):
    options = []
    for name, ct_cls in sorted(datasette._column_types.items()):
        sqlite_types = getattr(ct_cls, "sqlite_types", None)
        if sqlite_types is None:
            option_sqlite_types = CREATE_TABLE_COLUMN_TYPES[:]
        else:
            option_sqlite_types = [
                create_table_type
                for create_table_type, sqlite_type in CREATE_TABLE_SQLITE_TYPES.items()
                if sqlite_type in sqlite_types
            ]
        if not option_sqlite_types:
            continue
        option = {
            "name": name,
            "description": ct_cls.description,
            "sqliteTypes": option_sqlite_types,
        }
        if sqlite_types is not None and len(sqlite_types) == 1:
            fixed_sqlite_type = CREATE_TABLE_TYPE_FOR_SQLITE_TYPE.get(sqlite_types[0])
            if fixed_sqlite_type is not None:
                option["fixedSqliteType"] = fixed_sqlite_type
        options.append(option)
    return options


SqliteApiType = Literal["text", "integer", "float", "blob"]
DefaultExpr = Literal["current_timestamp", "current_date", "current_time"]
DEFAULT_EXPR_SQL = {
    "current_timestamp": "CURRENT_TIMESTAMP",
    "current_date": "CURRENT_DATE",
    "current_time": "CURRENT_TIME",
}


class _StrictPydanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _DefaultArgsMixin(_StrictPydanticModel):
    default: Any | None = None
    default_expr: DefaultExpr | None = None

    @model_validator(mode="after")
    def validate_default_fields(self):
        has_default = "default" in self.model_fields_set
        has_default_expr = "default_expr" in self.model_fields_set
        if has_default and has_default_expr:
            raise ValueError("default and default_expr cannot both be provided")
        if has_default_expr and self.default_expr is None:
            raise ValueError("default_expr cannot be null")
        return self


class AddColumnArgs(_DefaultArgsMixin):
    name: str
    type: SqliteApiType = "text"
    not_null: bool = False


class RenameColumnArgs(_StrictPydanticModel):
    name: str
    to: str


class AlterColumnArgs(_DefaultArgsMixin):
    name: str
    type: SqliteApiType | None = None
    not_null: bool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if not (
            {"type", "not_null", "default", "default_expr"} & self.model_fields_set
        ):
            raise ValueError(
                "At least one of type, not_null, default or default_expr must be provided"
            )
        return self


class DropColumnArgs(_StrictPydanticModel):
    name: str


class SetPrimaryKeyArgs(_StrictPydanticModel):
    columns: list[str] = Field(min_length=1)


class ReorderColumnsArgs(_StrictPydanticModel):
    columns: list[str] = Field(min_length=1)


class AddColumnOperation(_StrictPydanticModel):
    op: Literal["add_column"]
    args: AddColumnArgs


class RenameColumnOperation(_StrictPydanticModel):
    op: Literal["rename_column"]
    args: RenameColumnArgs


class AlterColumnOperation(_StrictPydanticModel):
    op: Literal["alter_column"]
    args: AlterColumnArgs


class DropColumnOperation(_StrictPydanticModel):
    op: Literal["drop_column"]
    args: DropColumnArgs


class SetPrimaryKeyOperation(_StrictPydanticModel):
    op: Literal["set_primary_key"]
    args: SetPrimaryKeyArgs


class ReorderColumnsOperation(_StrictPydanticModel):
    op: Literal["reorder_columns"]
    args: ReorderColumnsArgs


AlterTableOperation = Annotated[
    Union[
        AddColumnOperation,
        RenameColumnOperation,
        AlterColumnOperation,
        DropColumnOperation,
        SetPrimaryKeyOperation,
        ReorderColumnsOperation,
    ],
    Field(discriminator="op"),
]


class AlterTableRequest(_StrictPydanticModel):
    operations: list[AlterTableOperation] = Field(min_length=1)
    dry_run: bool = False


def _pydantic_errors(validation_error):
    errors = []
    for error in validation_error.errors():
        location = ".".join(str(item) for item in error["loc"])
        message = error["msg"]
        errors.append("{}: {}".format(location, message) if location else message)
    return errors


def _table_schema_from_conn(conn, table_name):
    row = conn.execute(
        "select sql from sqlite_master where type = 'table' and name = ?",
        [table_name],
    ).fetchone()
    return row[0] if row else None


def _primary_key_value(columns):
    if len(columns) == 1:
        return columns[0]
    return tuple(columns)


def _default_expression_sql(default_expr):
    return DEFAULT_EXPR_SQL[default_expr]


def _literal_default(db, value):
    if isinstance(value, str):
        return db.quote(value)
    return value


class TableCreateView(BaseView):
    name = "table-create"

    _valid_keys = {
        "table",
        "rows",
        "row",
        "columns",
        "pk",
        "pks",
        "ignore",
        "replace",
        "alter",
    }
    _supported_column_types = set(CREATE_TABLE_COLUMN_TYPES)
    # Any string that does not contain a newline or start with sqlite_
    _table_name_re = re.compile(r"^(?!sqlite_)[^\n]+$")

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        db = await self.ds.resolve_database(request)
        database_name = db.name

        # Must have create-table permission
        if not await self.ds.allowed(
            action="create-table",
            resource=DatabaseResource(database=database_name),
            actor=request.actor,
        ):
            return _error(["Permission denied"], 403)

        try:
            data = await request.json()
        except json.JSONDecodeError as e:
            return _error(["Invalid JSON: {}".format(e)])

        if not isinstance(data, dict):
            return _error(["JSON must be an object"])

        invalid_keys = set(data.keys()) - self._valid_keys
        if invalid_keys:
            return _error(["Invalid keys: {}".format(", ".join(invalid_keys))])

        # ignore and replace are mutually exclusive
        if data.get("ignore") and data.get("replace"):
            return _error(["ignore and replace are mutually exclusive"])

        # ignore and replace only allowed with row or rows
        if "ignore" in data or "replace" in data:
            if not data.get("row") and not data.get("rows"):
                return _error(["ignore and replace require row or rows"])

        # ignore and replace require pk or pks
        if "ignore" in data or "replace" in data:
            if not data.get("pk") and not data.get("pks"):
                return _error(["ignore and replace require pk or pks"])

        ignore = data.get("ignore")
        replace = data.get("replace")

        if replace:
            # Must have update-row permission
            if not await self.ds.allowed(
                action="update-row",
                resource=DatabaseResource(database=database_name),
                actor=request.actor,
            ):
                return _error(["Permission denied: need update-row"], 403)

        table_name = data.get("table")
        if not table_name:
            return _error(["Table is required"])

        if not self._table_name_re.match(table_name):
            return _error(["Invalid table name"])

        table_exists = await db.table_exists(data["table"])
        columns = data.get("columns")
        rows = data.get("rows")
        row = data.get("row")
        if not columns and not rows and not row:
            return _error(["columns, rows or row is required"])

        if rows and row:
            return _error(["Cannot specify both rows and row"])

        if rows or row:
            # Must have insert-row permission
            if not await self.ds.allowed(
                action="insert-row",
                resource=DatabaseResource(database=database_name),
                actor=request.actor,
            ):
                return _error(["Permission denied: need insert-row"], 403)

        alter = False
        if rows or row:
            if not table_exists:
                # if table is being created for the first time, alter=True
                alter = True
            else:
                # alter=True only if they request it AND they have permission
                if data.get("alter"):
                    if not await self.ds.allowed(
                        action="alter-table",
                        resource=DatabaseResource(database=database_name),
                        actor=request.actor,
                    ):
                        return _error(["Permission denied: need alter-table"], 403)
                    alter = True

        if columns:
            if rows or row:
                return _error(["Cannot specify columns with rows or row"])
            if not isinstance(columns, list):
                return _error(["columns must be a list"])
            for column in columns:
                if not isinstance(column, dict):
                    return _error(["columns must be a list of objects"])
                if not column.get("name") or not isinstance(column.get("name"), str):
                    return _error(["Column name is required"])
                if not column.get("type"):
                    column["type"] = "text"
                if column["type"] not in self._supported_column_types:
                    return _error(
                        ["Unsupported column type: {}".format(column["type"])]
                    )
            # No duplicate column names
            dupes = {c["name"] for c in columns if columns.count(c) > 1}
            if dupes:
                return _error(["Duplicate column name: {}".format(", ".join(dupes))])

        if row:
            rows = [row]

        if rows:
            if not isinstance(rows, list):
                return _error(["rows must be a list"])
            for row in rows:
                if not isinstance(row, dict):
                    return _error(["rows must be a list of objects"])

        pk = data.get("pk")
        pks = data.get("pks")

        if pk and pks:
            return _error(["Cannot specify both pk and pks"])
        if pk:
            if not isinstance(pk, str):
                return _error(["pk must be a string"])
        if pks:
            if not isinstance(pks, list):
                return _error(["pks must be a list"])
            for pk in pks:
                if not isinstance(pk, str):
                    return _error(["pks must be a list of strings"])

        # If table exists already, read pks from that instead
        if table_exists:
            actual_pks = await db.primary_keys(table_name)
            # if pk passed and table already exists check it does not change
            bad_pks = False
            if len(actual_pks) == 1 and data.get("pk") and data["pk"] != actual_pks[0]:
                bad_pks = True
            elif (
                len(actual_pks) > 1
                and data.get("pks")
                and set(data["pks"]) != set(actual_pks)
            ):
                bad_pks = True
            if bad_pks:
                return _error(["pk cannot be changed for existing table"])
            pks = actual_pks

        initial_schema = None
        if table_exists:
            initial_schema = await db.execute_fn(
                lambda conn: sqlite_utils.Database(conn)[table_name].schema
            )

        def create_table(conn):
            table = sqlite_utils.Database(conn)[table_name]
            if rows:
                table.insert_all(
                    rows, pk=pks or pk, ignore=ignore, replace=replace, alter=alter
                )
            else:
                table.create(
                    {c["name"]: c["type"] for c in columns},
                    pk=pks or pk,
                )
            return table.schema

        try:
            schema = await db.execute_write_fn(create_table, request=request)
        except Exception as e:
            return _error([str(e)])

        if initial_schema is not None and initial_schema != schema:
            await self.ds.track_event(
                AlterTableEvent(
                    request.actor,
                    database=database_name,
                    table=table_name,
                    before_schema=initial_schema,
                    after_schema=schema,
                )
            )

        table_url = self.ds.absolute_url(
            request, self.ds.urls.table(db.name, table_name)
        )
        table_api_url = self.ds.absolute_url(
            request, self.ds.urls.table(db.name, table_name, format="json")
        )
        details = {
            "ok": True,
            "database": db.name,
            "table": table_name,
            "table_url": table_url,
            "table_api_url": table_api_url,
            "schema": schema,
        }
        if rows:
            details["row_count"] = len(rows)

        if not table_exists:
            # Only log creation if we created a table
            await self.ds.track_event(
                CreateTableEvent(
                    request.actor, database=db.name, table=table_name, schema=schema
                )
            )
        if rows:
            await self.ds.track_event(
                InsertRowsEvent(
                    request.actor,
                    database=db.name,
                    table=table_name,
                    num_rows=len(rows),
                    ignore=ignore,
                    replace=replace,
                )
            )
        return Response.json(details, status=201)


class TableAlterView(BaseView):
    name = "table-alter"

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

        if not await self.ds.allowed(
            action="alter-table",
            resource=TableResource(database=database_name, table=table_name),
            actor=request.actor,
        ):
            return _error(["Permission denied: need alter-table"], 403)

        if not db.is_mutable:
            return _error(["Database is immutable"], 403)

        content_type = request.headers.get("content-type") or ""
        if not content_type.startswith("application/json"):
            return _error(["Invalid content-type, must be application/json"], 400)

        try:
            data = await request.json()
        except json.JSONDecodeError as e:
            return _error(["Invalid JSON: {}".format(e)], 400)

        if not isinstance(data, dict):
            return _error(["JSON must be a dictionary"], 400)

        try:
            alter_request = AlterTableRequest.model_validate(data)
        except ValidationError as e:
            return _error(_pydantic_errors(e), 400)

        def alter_table(conn):
            before_schema = _table_schema_from_conn(conn, table_name)

            def apply_operations(operation_conn):
                db_for_write = sqlite_utils.Database(operation_conn)
                table = db_for_write[table_name]

                add_columns = []
                types = {}
                rename = {}
                drop = set()
                not_null = {}
                defaults = {}
                column_order = None
                pk = SQLITE_UTILS_DEFAULT

                for operation in alter_request.operations:
                    args = operation.args
                    if operation.op == "add_column":
                        if args.not_null and not (
                            (
                                "default" in args.model_fields_set
                                and args.default is not None
                            )
                            or "default_expr" in args.model_fields_set
                        ):
                            raise ValueError(
                                "add_column args.default or args.default_expr is required when not_null is true"
                            )
                        add_columns.append(args)
                        if "default" in args.model_fields_set and not args.not_null:
                            defaults[args.name] = _literal_default(
                                db_for_write, args.default
                            )
                        if (
                            "default_expr" in args.model_fields_set
                            and not args.not_null
                        ):
                            defaults[args.name] = _default_expression_sql(
                                args.default_expr
                            )
                    elif operation.op == "rename_column":
                        rename[args.name] = args.to
                    elif operation.op == "alter_column":
                        if args.type is not None:
                            types[args.name] = args.type
                        if args.not_null is not None:
                            not_null[args.name] = args.not_null
                        if "default" in args.model_fields_set:
                            defaults[args.name] = (
                                None
                                if args.default is None
                                else _literal_default(db_for_write, args.default)
                            )
                        if "default_expr" in args.model_fields_set:
                            defaults[args.name] = _default_expression_sql(
                                args.default_expr
                            )
                    elif operation.op == "drop_column":
                        drop.add(args.name)
                    elif operation.op == "set_primary_key":
                        pk = _primary_key_value(args.columns)
                    elif operation.op == "reorder_columns":
                        column_order = args.columns

                with operation_conn:
                    for column in add_columns:
                        not_null_default = None
                        if column.not_null:
                            if "default_expr" in column.model_fields_set:
                                not_null_default = _default_expression_sql(
                                    column.default_expr
                                )
                            else:
                                not_null_default = _literal_default(
                                    db_for_write, column.default
                                )
                        table.add_column(
                            column.name,
                            column.type,
                            not_null_default=not_null_default,
                        )

                    should_transform = any(
                        (
                            types,
                            rename,
                            drop,
                            not_null,
                            defaults,
                            column_order is not None,
                            pk is not SQLITE_UTILS_DEFAULT,
                        )
                    )
                    if should_transform:
                        table.transform(
                            types=types or None,
                            rename=rename or None,
                            drop=drop or None,
                            pk=pk,
                            not_null=not_null or None,
                            defaults=defaults or None,
                            column_order=column_order,
                        )

                return _table_schema_from_conn(operation_conn, table_name)

            if alter_request.dry_run:
                memory_conn = sqlite3.connect(":memory:")
                try:
                    conn.backup(memory_conn)
                    return before_schema, apply_operations(memory_conn)
                finally:
                    memory_conn.close()

            after_schema = apply_operations(conn)
            return before_schema, after_schema

        try:
            before_schema, after_schema = await db.execute_write_fn(
                alter_table, request=request
            )
        except Exception as e:
            return _error([str(e)], 400)

        altered = before_schema != after_schema
        if altered and not alter_request.dry_run:
            await self.ds.track_event(
                AlterTableEvent(
                    request.actor,
                    database=database_name,
                    table=table_name,
                    before_schema=before_schema,
                    after_schema=after_schema,
                )
            )

        table_url = self.ds.absolute_url(
            request, self.ds.urls.table(database_name, table_name)
        )
        table_api_url = self.ds.absolute_url(
            request, self.ds.urls.table(database_name, table_name, format="json")
        )
        return Response.json(
            {
                "ok": True,
                "database": database_name,
                "table": table_name,
                "table_url": table_url,
                "table_api_url": table_api_url,
                "altered": altered,
                "schema": after_schema,
                "before_schema": before_schema,
                "operations_applied": (
                    0 if alter_request.dry_run else len(alter_request.operations)
                ),
                "dry_run": alter_request.dry_run,
            },
            status=200,
        )
