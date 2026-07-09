import json
import re
import time
from typing import Annotated, Any, Literal, Union

from datasette.database import QueryInterrupted
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError
import sqlite_utils
from sqlite_utils.db import DEFAULT as SQLITE_UTILS_DEFAULT

from datasette.column_types import SQLiteType
from datasette.events import AlterTableEvent, CreateTableEvent, InsertRowsEvent
from datasette.resources import DatabaseResource, TableResource
from datasette.utils import (
    decode_write_json_rows,
    escape_sqlite,
    get_outbound_foreign_keys,
    table_column_details,
    WriteJsonValueError,
)
from datasette.utils.asgi import NotFound, PayloadTooLarge, Response
from datasette.utils.sqlite import sqlite_hidden_table_names

from .base import BaseView

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
TABLE_NAME_RE = re.compile(r"^(?!sqlite_)[^\n]+$")
ALTER_TABLE_COLUMN_TYPES = CREATE_TABLE_COLUMN_TYPES
ALTER_TABLE_TYPE_FOR_SQLITE_TYPE = {
    SQLiteType.TEXT: "text",
    SQLiteType.INTEGER: "integer",
    SQLiteType.REAL: "float",
    SQLiteType.BLOB: "blob",
}
FOREIGN_KEY_SUGGESTION_ROW_LIMIT = 500
FOREIGN_KEY_SUGGESTION_TIME_LIMIT_MS = 50
FOREIGN_KEY_SUGGESTION_TOTAL_TIME_LIMIT_MS = 200
FOREIGN_KEY_TARGETS_SQL = """
select
  m.name as fk_table,
  p.name as fk_column,
  case
    when upper(coalesce(p.type, '')) like '%INT%' then 'integer'
    when upper(coalesce(p.type, '')) like '%CHAR%'
      or upper(coalesce(p.type, '')) like '%CLOB%'
      or upper(coalesce(p.type, '')) like '%TEXT%' then 'text'
    when upper(coalesce(p.type, '')) like '%BLOB%'
      or coalesce(p.type, '') = '' then 'blob'
    when upper(coalesce(p.type, '')) like '%REAL%'
      or upper(coalesce(p.type, '')) like '%FLOA%'
      or upper(coalesce(p.type, '')) like '%' || 'DOU' || 'B' || '%' then 'real'
    else 'numeric'
  end as type
from sqlite_master as m
cross join pragma_table_info(m.name) as p
where m.type = 'table'
  and m.name not like 'sqlite_%'
  and p.pk > 0
  and (
    select count(*)
    from pragma_table_info(m.name) as p2
    where p2.pk > 0
  ) = 1
order by m.name
"""


class ForeignKeySuggestionTimedOut(Exception):
    pass


def _sqlite_type_affinity(type_name):
    type_name = (type_name or "").upper()
    if "INT" in type_name:
        return "integer"
    if any(token in type_name for token in ("CHAR", "CLOB", "TEXT")):
        return "text"
    if "BLOB" in type_name or not type_name:
        return "blob"
    if any(
        token in type_name
        for token in ("REAL", "FLOA", "DOUB")  # codespell:ignore doub
    ):
        return "real"
    return "numeric"


def _foreign_key_type_compatible(source_affinity, target_affinity):
    if source_affinity == target_affinity:
        return True
    numeric_affinities = {"integer", "real", "numeric"}
    if source_affinity == "numeric":
        return target_affinity in numeric_affinities
    if target_affinity == "numeric":
        return source_affinity in numeric_affinities
    return False


def _public_foreign_key_target(target):
    return {
        "fk_table": target["fk_table"],
        "fk_column": target["fk_column"],
        "type": target["type"],
    }


def _singular(name):
    if name.endswith("ies") and len(name) > 3:
        return name[:-3] + "y"
    if name.endswith("s") and len(name) > 1:
        return name[:-1]
    return name


def _foreign_key_name_reasons(source_column, target):
    source = source_column.lower()
    table = target["fk_table"].lower()
    singular_table = _singular(table)
    column = target["fk_column"].lower()
    possible_names = {
        "{}_{}".format(table, column),
        "{}_{}".format(singular_table, column),
    }
    if column == "id":
        possible_names.update(
            {
                "{}_id".format(table),
                "{}_id".format(singular_table),
            }
        )
    return ["name_match"] if source in possible_names else []


def _foreign_key_option_sort_key(source_column, target):
    has_name_match = bool(_foreign_key_name_reasons(source_column, target))
    return (
        0 if has_name_match else 1,
        target["fk_table"],
        target["fk_column"],
    )


def _foreign_key_suggestion_metadata(conn, table_name):
    hidden_tables = set(sqlite_hidden_table_names(conn))
    source_columns = [
        {
            "column": column.name,
            "type": (column.type or "").upper(),
            "affinity": _sqlite_type_affinity(column.type),
        }
        for column in table_column_details(conn, table_name)
        if not column.hidden
    ]
    current_by_column = {
        fk["column"]: {
            "fk_table": fk["other_table"],
            "fk_column": fk["other_column"],
        }
        for fk in get_outbound_foreign_keys(conn, table_name)
    }
    table_names = [
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type = 'table' order by name"
        ).fetchall()
        if not row[0].startswith("sqlite_")
    ]
    targets = []
    for candidate_table in table_names:
        if candidate_table == table_name or candidate_table in hidden_tables:
            continue
        columns = [column for column in table_column_details(conn, candidate_table)]
        pks = [column for column in columns if column.is_pk and not column.hidden]
        pks.sort(key=lambda column: column.is_pk)
        if len(pks) != 1:
            continue
        pk = pks[0]
        targets.append(
            {
                "fk_table": candidate_table,
                "fk_column": pk.name,
                "type": (pk.type or "").upper(),
                "affinity": _sqlite_type_affinity(pk.type),
            }
        )
    return source_columns, targets, current_by_column


async def _foreign_key_suggestion_samples(db, table_name, columns):
    if not columns:
        return 0, {}
    sql = "select {} from {} limit {}".format(
        ", ".join(escape_sqlite(column) for column in columns),
        escape_sqlite(table_name),
        FOREIGN_KEY_SUGGESTION_ROW_LIMIT,
    )
    try:
        results = await db.execute(
            sql,
            custom_time_limit=FOREIGN_KEY_SUGGESTION_TIME_LIMIT_MS,
            log_sql_errors=False,
        )
    except QueryInterrupted as e:
        raise ForeignKeySuggestionTimedOut from e
    values_by_column = {column: [] for column in columns}
    seen_by_column = {column: set() for column in columns}
    for row in results.rows:
        for column in columns:
            value = row[column]
            if value is None or value in seen_by_column[column]:
                continue
            seen_by_column[column].add(value)
            values_by_column[column].append(value)
    return len(results.rows), values_by_column


async def _foreign_key_suggestion_values_exist(db, target, values, time_limit_ms):
    if not values:
        return False
    sql = "select {} from {} where {} in ({})".format(
        escape_sqlite(target["fk_column"]),
        escape_sqlite(target["fk_table"]),
        escape_sqlite(target["fk_column"]),
        ", ".join("?" for _ in values),
    )
    try:
        results = await db.execute(
            sql,
            params=values,
            custom_time_limit=time_limit_ms,
            log_sql_errors=False,
        )
    except QueryInterrupted as e:
        raise ForeignKeySuggestionTimedOut from e
    found = {row[0] for row in results.rows}
    return all(value in found for value in values)


async def _create_table_ui_context(
    datasette, request, db, database_name, database_action_permissions
):
    if not db.is_mutable:
        return None
    if not database_action_permissions.get("create-table"):
        return None
    data = {
        "path": "{}/-/create".format(datasette.urls.database(database_name)),
        "foreignKeyTargetsPath": "{}/-/foreign-key-targets".format(
            datasette.urls.database(database_name)
        ),
        "databaseName": database_name,
        "columnTypes": CREATE_TABLE_COLUMN_TYPES,
        "defaultExpressions": default_expression_options(),
        "canInsertRows": await datasette.allowed(
            action="insert-row",
            resource=DatabaseResource(database=database_name),
            actor=request.actor,
        ),
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
DEFAULT_EXPRESSIONS = {
    "current_timestamp": {
        "sql": "CURRENT_TIMESTAMP",
        "label": "Current timestamp in UTC, e.g. 2026-05-01 13:34:00",
        "sqliteType": "text",
    },
    "current_date": {
        "sql": "CURRENT_DATE",
        "label": "Current date in UTC, e.g. 2026-05-01",
        "sqliteType": "text",
    },
    "current_time": {
        "sql": "CURRENT_TIME",
        "label": "Current time in UTC, e.g. 13:34:00",
        "sqliteType": "text",
    },
    "current_unixtime": {
        "sql": "(CAST(strftime('%s', 'now') AS INTEGER))",
        "label": "Current Unix time, integer seconds since the epoch",
        "sqliteType": "integer",
    },
    "current_unixtime_ms": {
        "sql": "(CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER))",
        "label": "Current Unix time, integer milliseconds since the epoch",
        "sqliteType": "integer",
    },
}
DefaultExpr = str
DEFAULT_EXPR_SQL = {
    name: metadata["sql"] for name, metadata in DEFAULT_EXPRESSIONS.items()
}


def _strip_wrapping_parentheses(expression):
    expression = expression.strip()
    while expression.startswith("(") and expression.endswith(")"):
        depth = 0
        in_single_quote = False
        wraps_whole_expression = True
        i = 0
        while i < len(expression):
            char = expression[i]
            if char == "'":
                if (
                    in_single_quote
                    and i + 1 < len(expression)
                    and expression[i + 1] == "'"
                ):
                    i += 2
                    continue
                in_single_quote = not in_single_quote
            elif not in_single_quote:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0 and i != len(expression) - 1:
                        wraps_whole_expression = False
                        break
            i += 1
        if not wraps_whole_expression or depth != 0 or in_single_quote:
            break
        expression = expression[1:-1].strip()
    return expression


def _default_expression_lookup_key(expression):
    return re.sub(r"\s+", " ", _strip_wrapping_parentheses(expression)).lower()


DEFAULT_EXPR_BY_SQL = {
    _default_expression_lookup_key(sql): name for name, sql in DEFAULT_EXPR_SQL.items()
}


def default_expr_for_sql(expression):
    if expression is None:
        return None
    return DEFAULT_EXPR_BY_SQL.get(_default_expression_lookup_key(expression))


def _quoted_options(options):
    if len(options) == 1:
        return "'{}'".format(options[0])
    return "{} or '{}'".format(
        ", ".join("'{}'".format(option) for option in options[:-1]),
        options[-1],
    )


def _default_expr_error_message():
    return "Input should be {}".format(_quoted_options(list(DEFAULT_EXPRESSIONS)))


def default_expression_options():
    return [
        {
            "value": value,
            "label": metadata["label"],
            "sqliteType": metadata["sqliteType"],
        }
        for value, metadata in DEFAULT_EXPRESSIONS.items()
    ]


class _StrictPydanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _DefaultArgsMixin(_StrictPydanticModel):
    default: Any | None = None
    default_expr: DefaultExpr | None = None

    @field_validator("default_expr")
    @classmethod
    def validate_default_expr_value(cls, value):
        if value is not None and value not in DEFAULT_EXPRESSIONS:
            raise PydanticCustomError("default_expr", _default_expr_error_message())
        return value

    @model_validator(mode="after")
    def validate_default_fields(self):
        has_default = "default" in self.model_fields_set
        has_default_expr = "default_expr" in self.model_fields_set
        if has_default and has_default_expr:
            raise ValueError("default and default_expr cannot both be provided")
        if has_default_expr and self.default_expr is None:
            raise ValueError("default_expr cannot be null")
        return self


class CreateTableColumn(_DefaultArgsMixin):

    name: Any = None
    type: Any = "text"
    fk_table: str | None = None
    fk_column: str | None = None
    not_null: bool = False

    @model_validator(mode="after")
    def validate_column(self):
        if not self.name or not isinstance(self.name, str):
            raise PydanticCustomError("create_table", "Column name is required")
        if not self.type:
            self.type = "text"
        elif self.type not in CREATE_TABLE_COLUMN_TYPES:
            raise PydanticCustomError(
                "create_table", "Unsupported column type: {type}", {"type": self.type}
            )
        if self.fk_column and not self.fk_table:
            raise PydanticCustomError(
                "create_table_with_location",
                "fk_column requires fk_table",
            )
        return self


class CreateTableRequest(_StrictPydanticModel):
    table: Any = None
    rows: Any = None
    row: Any = None
    columns: list[CreateTableColumn] | None = None
    pk: Any = None
    pks: Any = None
    ignore: bool | None = None
    replace: bool | None = None
    alter: bool | None = None

    @field_validator("columns", mode="before")
    @classmethod
    def validate_columns_list(cls, value):
        if value is None:
            return value
        if not isinstance(value, list):
            raise PydanticCustomError("create_table", "columns must be a list")
        if not all(isinstance(column, dict) for column in value):
            raise PydanticCustomError(
                "create_table", "columns must be a list of objects"
            )
        return value

    @model_validator(mode="after")
    def validate_request(self):
        if not self.table:
            raise PydanticCustomError("create_table", "Table is required")
        if not isinstance(self.table, str) or not TABLE_NAME_RE.match(self.table):
            raise PydanticCustomError("create_table", "Invalid table name")
        if not self.columns and not self.rows and not self.row:
            raise PydanticCustomError(
                "create_table", "columns, rows or row is required"
            )
        if self.rows and self.row:
            raise PydanticCustomError(
                "create_table", "Cannot specify both rows and row"
            )
        if self.columns and (self.rows or self.row):
            raise PydanticCustomError(
                "create_table", "Cannot specify columns with rows or row"
            )
        if self.columns is not None:
            seen = set()
            duplicates = []
            for column in self.columns:
                if column.name in seen and column.name not in duplicates:
                    duplicates.append(column.name)
                seen.add(column.name)
            if duplicates:
                raise PydanticCustomError(
                    "create_table",
                    "Duplicate column name: {names}",
                    {"names": ", ".join(duplicates)},
                )
        if self.rows is not None:
            if not isinstance(self.rows, list):
                raise PydanticCustomError("create_table", "rows must be a list")
            if not all(isinstance(row, dict) for row in self.rows):
                raise PydanticCustomError(
                    "create_table", "rows must be a list of objects"
                )
        if self.pk is not None and not isinstance(self.pk, str):
            raise PydanticCustomError("create_table", "pk must be a string")
        if self.pk and self.pks:
            raise PydanticCustomError("create_table", "Cannot specify both pk and pks")
        if self.pks is not None:
            if not isinstance(self.pks, list):
                raise PydanticCustomError("create_table", "pks must be a list")
            if not all(isinstance(pk, str) for pk in self.pks):
                raise PydanticCustomError(
                    "create_table", "pks must be a list of strings"
                )
        if self.ignore and self.replace:
            raise PydanticCustomError(
                "create_table", "ignore and replace are mutually exclusive"
            )
        if {"ignore", "replace"} & self.model_fields_set:
            if not self.row and not self.rows:
                raise PydanticCustomError(
                    "create_table", "ignore and replace require row or rows"
                )
            if not self.pk and not self.pks:
                raise PydanticCustomError(
                    "create_table", "ignore and replace require pk or pks"
                )
        return self

    @property
    def rows_list(self):
        return [self.row] if self.row else self.rows

    @property
    def foreign_keys(self):
        if not self.columns:
            return None
        foreign_keys = []
        for column in self.columns:
            if column.fk_table and column.fk_column:
                foreign_keys.append((column.name, column.fk_table, column.fk_column))
            elif column.fk_table:
                foreign_keys.append((column.name, column.fk_table))
        return foreign_keys or None


class AddColumnArgs(_DefaultArgsMixin):
    name: str
    type: SqliteApiType = "text"
    not_null: bool = False


class RenameColumnArgs(_StrictPydanticModel):
    name: str
    to: str


class RenameTableArgs(_StrictPydanticModel):
    to: str

    @field_validator("to")
    @classmethod
    def validate_table_name(cls, v):
        if not TABLE_NAME_RE.match(v):
            raise PydanticCustomError(
                "alter_table_rename_table",
                "Invalid table name",
            )
        return v


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


class ForeignKeyArgs(_StrictPydanticModel):
    column: str
    fk_table: str | None = None
    fk_column: str | None = None

    @model_validator(mode="after")
    def validate_foreign_key(self):
        if self.fk_column and not self.fk_table:
            raise PydanticCustomError(
                "alter_table_foreign_key",
                "fk_column requires fk_table",
            )
        if not self.fk_table:
            raise PydanticCustomError(
                "alter_table_foreign_key",
                "fk_table is required",
            )
        return self

    @property
    def tuple(self):
        if self.fk_column:
            return (self.column, self.fk_table, self.fk_column)
        return (self.column, self.fk_table)


class DropForeignKeyArgs(_StrictPydanticModel):
    column: str


class SetForeignKeysArgs(_StrictPydanticModel):
    foreign_keys: list[ForeignKeyArgs]


class AddColumnOperation(_StrictPydanticModel):
    op: Literal["add_column"]
    args: AddColumnArgs


class RenameColumnOperation(_StrictPydanticModel):
    op: Literal["rename_column"]
    args: RenameColumnArgs


class RenameTableOperation(_StrictPydanticModel):
    op: Literal["rename_table"]
    args: RenameTableArgs


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


class AddForeignKeyOperation(_StrictPydanticModel):
    op: Literal["add_foreign_key"]
    args: ForeignKeyArgs


class DropForeignKeyOperation(_StrictPydanticModel):
    op: Literal["drop_foreign_key"]
    args: DropForeignKeyArgs


class SetForeignKeysOperation(_StrictPydanticModel):
    op: Literal["set_foreign_keys"]
    args: SetForeignKeysArgs


AlterTableOperation = Annotated[
    Union[
        AddColumnOperation,
        RenameColumnOperation,
        RenameTableOperation,
        AlterColumnOperation,
        DropColumnOperation,
        SetPrimaryKeyOperation,
        ReorderColumnsOperation,
        AddForeignKeyOperation,
        DropForeignKeyOperation,
        SetForeignKeysOperation,
    ],
    Field(discriminator="op"),
]


class AlterTableRequest(_StrictPydanticModel):
    operations: list[AlterTableOperation] = Field(min_length=1)


def _pydantic_errors(validation_error):
    errors = []
    for error in validation_error.errors():
        location = ".".join(str(item) for item in error["loc"])
        message = error["msg"]
        errors.append("{}: {}".format(location, message) if location else message)
    return errors


def _create_table_pydantic_errors(validation_error):
    errors = validation_error.errors()
    invalid_keys = sorted(
        str(error["loc"][0])
        for error in errors
        if error["type"] == "extra_forbidden" and len(error["loc"]) == 1
    )
    if invalid_keys:
        return ["Invalid keys: {}".format(", ".join(invalid_keys))]

    output = []
    for error in errors:
        message = error["msg"]
        if error["type"] == "create_table":
            output.append(message)
            continue
        location = ".".join(str(item) for item in error["loc"])
        output.append("{}: {}".format(location, message) if location else message)
    return output


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
            return Response.error(["Permission denied"], 403)

        try:
            data = await request.json()
        except json.JSONDecodeError as e:
            return Response.error(["Invalid JSON: {}".format(e)])
        except PayloadTooLarge as e:
            return Response.error([str(e)], 413)

        if not isinstance(data, dict):
            return Response.error(["JSON must be an object"])

        try:
            create_request = CreateTableRequest.model_validate(data)
        except ValidationError as e:
            return Response.error(_create_table_pydantic_errors(e))

        ignore = create_request.ignore
        replace = create_request.replace

        if replace:
            # Must have update-row permission
            if not await self.ds.allowed(
                action="update-row",
                resource=DatabaseResource(database=database_name),
                actor=request.actor,
            ):
                return Response.error(["Permission denied: need update-row"], 403)

        table_name = create_request.table
        table_exists = await db.table_exists(table_name)
        columns = create_request.columns
        rows = create_request.rows_list

        if rows:
            # Must have insert-row permission
            if not await self.ds.allowed(
                action="insert-row",
                resource=DatabaseResource(database=database_name),
                actor=request.actor,
            ):
                return Response.error(["Permission denied: need insert-row"], 403)
            try:
                rows = decode_write_json_rows(rows)
            except WriteJsonValueError as e:
                return Response.error([str(e)], 400)

        alter = False
        if rows:
            if not table_exists:
                # if table is being created for the first time, alter=True
                alter = True
            else:
                # alter=True only if they request it AND they have permission
                if create_request.alter:
                    if not await self.ds.allowed(
                        action="alter-table",
                        resource=DatabaseResource(database=database_name),
                        actor=request.actor,
                    ):
                        return Response.error(
                            ["Permission denied: need alter-table"], 403
                        )
                    alter = True

        pk = create_request.pk
        pks = create_request.pks

        # If table exists already, read pks from that instead
        if table_exists:
            actual_pks = await db.primary_keys(table_name)
            # if pk passed and table already exists check it does not change
            bad_pks = False
            if len(actual_pks) == 1 and pk and pk != actual_pks[0]:
                bad_pks = True
            elif len(actual_pks) > 1 and pks and set(pks) != set(actual_pks):
                bad_pks = True
            if bad_pks:
                return Response.error(["pk cannot be changed for existing table"])
            pks = actual_pks

        initial_schema = None
        if table_exists:
            initial_schema = await db.execute_fn(
                lambda conn: sqlite_utils.Database(conn, execute_plugins=False)[
                    table_name
                ].schema
            )

        def create_table(conn):
            db_for_write = sqlite_utils.Database(conn, execute_plugins=False)
            table = db_for_write[table_name]
            if rows:
                table.insert_all(
                    rows, pk=pks or pk, ignore=ignore, replace=replace, alter=alter
                )
            else:
                not_null = [column.name for column in columns if column.not_null]
                defaults = {}
                for column in columns:
                    if "default_expr" in column.model_fields_set:
                        defaults[column.name] = _default_expression_sql(
                            column.default_expr
                        )
                    elif (
                        "default" in column.model_fields_set
                        and column.default is not None
                    ):
                        defaults[column.name] = _literal_default(
                            db_for_write, column.default
                        )
                table.create(
                    {column.name: column.type for column in columns},
                    pk=pks or pk,
                    foreign_keys=create_request.foreign_keys,
                    not_null=not_null or None,
                    defaults=defaults or None,
                )
            return table.schema

        try:
            schema = await db.execute_write_fn(create_table, request=request)
        except Exception as e:
            return Response.error([str(e)])

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


class DatabaseForeignKeyTargetsView(BaseView):
    name = "database-foreign-key-targets"

    def __init__(self, datasette):
        self.ds = datasette

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        database_name = db.name

        table_name = request.args.get("table")
        can_create_table = await self.ds.allowed(
            action="create-table",
            resource=DatabaseResource(database=database_name),
            actor=request.actor,
        )
        can_alter_table = False
        if table_name and await db.table_exists(table_name):
            can_alter_table = await self.ds.allowed(
                action="alter-table",
                resource=TableResource(database=database_name, table=table_name),
                actor=request.actor,
            )
        if not (can_create_table or can_alter_table):
            return Response.error(["Permission denied: need create-table"], 403)

        hidden_tables = await db.execute_fn(
            lambda conn: set(sqlite_hidden_table_names(conn))
        )
        targets = [
            target
            for target in (await db.execute(FOREIGN_KEY_TARGETS_SQL)).dicts()
            if target["fk_table"] not in hidden_tables
        ]
        return Response.json(
            {
                "ok": True,
                "database": database_name,
                "targets": targets,
            }
        )


class TableForeignKeySuggestionsView(BaseView):
    name = "table-foreign-key-suggestions"

    def __init__(self, datasette):
        self.ds = datasette

    async def get(self, request):
        try:
            resolved = await self.ds.resolve_table(request)
        except NotFound as e:
            return Response.error([e.args[0]], 404)

        db = resolved.db
        database_name = db.name
        table_name = resolved.table

        if resolved.is_view:
            return Response.error(["Cannot suggest foreign keys for a view"], 400)

        if not await self.ds.allowed(
            action="alter-table",
            resource=TableResource(database=database_name, table=table_name),
            actor=request.actor,
        ):
            return Response.error(["Permission denied: need alter-table"], 403)

        source_columns, targets, current_by_column = await db.execute_fn(
            lambda conn: _foreign_key_suggestion_metadata(conn, table_name)
        )

        columns = []
        options_by_column = {}
        for source_column in source_columns:
            options = sorted(
                [
                    target
                    for target in targets
                    if _foreign_key_type_compatible(
                        source_column["affinity"], target["affinity"]
                    )
                ],
                key=lambda target: _foreign_key_option_sort_key(
                    source_column["column"], target
                ),
            )
            options_by_column[source_column["column"]] = options
            columns.append(
                {
                    "column": source_column["column"],
                    "type": source_column["type"],
                    "affinity": source_column["affinity"],
                    "current": current_by_column.get(source_column["column"]),
                    "suggestions": [],
                    "options": [
                        _public_foreign_key_target(option) for option in options
                    ],
                }
            )

        columns_to_sample = [
            column["column"]
            for column in columns
            if options_by_column[column["column"]]
        ]
        row_check = {
            "attempted": bool(columns_to_sample),
            "status": "completed" if columns_to_sample else "skipped",
            "row_limit": FOREIGN_KEY_SUGGESTION_ROW_LIMIT,
            "sampled_rows": 0,
            "checked_options": 0,
        }

        try:
            sampled_rows, values_by_column = await _foreign_key_suggestion_samples(
                db, table_name, columns_to_sample
            )
            row_check["sampled_rows"] = sampled_rows
            deadline = time.perf_counter() + (
                FOREIGN_KEY_SUGGESTION_TOTAL_TIME_LIMIT_MS / 1000
            )
            for column_info in columns:
                values = values_by_column.get(column_info["column"]) or []
                if not values:
                    continue
                for option in options_by_column[column_info["column"]]:
                    remaining_ms = int((deadline - time.perf_counter()) * 1000)
                    if remaining_ms <= 0:
                        raise ForeignKeySuggestionTimedOut
                    if await _foreign_key_suggestion_values_exist(
                        db,
                        option,
                        values,
                        min(FOREIGN_KEY_SUGGESTION_TIME_LIMIT_MS, remaining_ms),
                    ):
                        reasons = [
                            "type_match",
                            "sample_values_exist",
                        ] + _foreign_key_name_reasons(column_info["column"], option)
                        column_info["suggestions"].append(
                            {
                                "fk_table": option["fk_table"],
                                "fk_column": option["fk_column"],
                                "confidence": "sampled",
                                "sampled_values": len(values),
                                "reasons": reasons,
                            }
                        )
                    row_check["checked_options"] += 1
        except ForeignKeySuggestionTimedOut:
            row_check["status"] = "timed_out"

        return Response.json(
            {
                "ok": True,
                "database": database_name,
                "table": table_name,
                "row_check": row_check,
                "columns": columns,
            }
        )


class TableAlterView(BaseView):
    name = "table-alter"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        try:
            resolved = await self.ds.resolve_table(request)
        except NotFound as e:
            return Response.error([e.args[0]], 404)

        db = resolved.db
        database_name = db.name
        table_name = resolved.table

        if not await self.ds.allowed(
            action="alter-table",
            resource=TableResource(database=database_name, table=table_name),
            actor=request.actor,
        ):
            return Response.error(["Permission denied: need alter-table"], 403)

        if not db.is_mutable:
            return Response.error(["Database is immutable"], 403)

        try:
            data = await request.json()
        except json.JSONDecodeError as e:
            return Response.error(["Invalid JSON: {}".format(e)], 400)
        except PayloadTooLarge as e:
            return Response.error([str(e)], 413)

        if not isinstance(data, dict):
            return Response.error(["JSON must be a dictionary"], 400)

        try:
            alter_request = AlterTableRequest.model_validate(data)
        except ValidationError as e:
            return Response.error(_pydantic_errors(e), 400)

        def alter_table(conn):
            before_schema = _table_schema_from_conn(conn, table_name)

            def apply_operations(operation_conn):
                db_for_write = sqlite_utils.Database(
                    operation_conn, execute_plugins=False
                )
                table = db_for_write[table_name]
                current_table_name = table_name

                add_columns = []
                types = {}
                rename = {}
                rename_table_to = None
                drop = set()
                not_null = {}
                defaults = {}
                column_order = None
                pk = SQLITE_UTILS_DEFAULT
                add_foreign_keys = []
                drop_foreign_keys = []
                foreign_keys = None

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
                    elif operation.op == "rename_table":
                        rename_table_to = args.to
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
                    elif operation.op == "add_foreign_key":
                        add_foreign_keys.append(args.tuple)
                    elif operation.op == "drop_foreign_key":
                        drop_foreign_keys.append(args.column)
                    elif operation.op == "set_foreign_keys":
                        foreign_keys = [fk.tuple for fk in args.foreign_keys]

                # The write task transaction makes these operations atomic
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
                        add_foreign_keys,
                        drop_foreign_keys,
                        foreign_keys is not None,
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
                        add_foreign_keys=add_foreign_keys or None,
                        drop_foreign_keys=drop_foreign_keys or None,
                        foreign_keys=foreign_keys,
                    )
                if (
                    rename_table_to is not None
                    and rename_table_to != current_table_name
                ):
                    operation_conn.execute(
                        "alter table {} rename to {}".format(
                            escape_sqlite(current_table_name),
                            escape_sqlite(rename_table_to),
                        )
                    )
                    current_table_name = rename_table_to

                return current_table_name, _table_schema_from_conn(
                    operation_conn, current_table_name
                )

            after_table_name, after_schema = apply_operations(conn)
            return before_schema, after_schema, after_table_name

        try:
            before_schema, after_schema, after_table_name = await db.execute_write_fn(
                alter_table, request=request
            )
        except Exception as e:
            return Response.error([str(e)], 400)

        altered = before_schema != after_schema
        if altered:
            await self.ds.track_event(
                AlterTableEvent(
                    request.actor,
                    database=database_name,
                    table=after_table_name,
                    before_schema=before_schema,
                    after_schema=after_schema,
                )
            )

        table_url = self.ds.absolute_url(
            request, self.ds.urls.table(database_name, after_table_name)
        )
        table_api_url = self.ds.absolute_url(
            request, self.ds.urls.table(database_name, after_table_name, format="json")
        )
        return Response.json(
            {
                "ok": True,
                "database": database_name,
                "table": after_table_name,
                "table_url": table_url,
                "table_api_url": table_api_url,
                "altered": altered,
                "schema": after_schema,
                "before_schema": before_schema,
                "operations_applied": len(alter_request.operations),
            },
            status=200,
        )
