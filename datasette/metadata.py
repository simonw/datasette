import re
from marshmallow import Schema, fields, validate
from marshmallow_union import Union


# these fields can be used at instance, database or table-level
STANDARD_FIELDS = {
    "title": fields.String(),
    "description": fields.String(),
    "description_html": fields.String(),
    "license": fields.String(),
    "license_url": fields.String(),
    "source": fields.String(),
    "source_url": fields.String(),
    "about": fields.String(),
    "about_url": fields.String(),
}


def _get_units_schema(columns):
    return Schema.from_dict({col: fields.String() for col in columns})


def _get_table_schema(columns, tables):
    UnitsSchema = _get_units_schema(columns)
    columns_regex = f"^({'|'.join([re.escape(c) for c in columns])})$"
    tables_regex = f"^({'|'.join([re.escape(t) for t in tables])})$"
    TableSchema = Schema.from_dict(
        {
            **STANDARD_FIELDS,
            **{
                "units": fields.Nested(UnitsSchema()),
                "sortable_columns": fields.List(
                    fields.String(
                        required=True,
                        validate=validate.Regexp(columns_regex, error="Invalid column"),
                    )
                ),
                "label_column": fields.String(
                    validate=validate.Regexp(columns_regex, error="Invalid column")
                ),
                "hidden": fields.Boolean(),
                "plugins": fields.Dict(),
                "fts_table": fields.String(
                    validate=validate.Regexp(tables_regex, error="Invalid table")
                ),
                "fts_pk": fields.String(
                    validate=validate.Regexp(columns_regex, error="Invalid column")
                ),
            },
        }
    )

    return TableSchema


def _get_tables_schema(tables):
    tables_dict = {}
    for table, columns in tables.items():
        TableSchema = _get_table_schema(columns, tables.keys())
        tables_dict[table] = fields.Nested(TableSchema())

    return Schema.from_dict(tables_dict)


def _get_queries_schema(queries):
    QuerySchema = Schema.from_dict(
        {
            "sql": fields.String(required=True),
            "title": fields.String(),
            "description": fields.String(),
            "description_html": fields.String(),
        }
    )

    return Schema.from_dict(
        {
            q: Union([fields.Nested(QuerySchema()), fields.String(required=True)])
            for q in queries
        }
    )


def _get_database_schema(database):
    TablesSchema = _get_tables_schema({**database["tables"], **database["views"]})
    QueriesSchema = _get_queries_schema(database["queries"])
    DatabaseSchema = Schema.from_dict(
        {
            **STANDARD_FIELDS,
            **{
                "plugins": fields.Dict(),
                "tables": fields.Nested(TablesSchema()),
                "queries": fields.Nested(QueriesSchema()),
            },
        }
    )

    return DatabaseSchema


def _get_databases_schema(databases):
    schema_dict = {}
    for database in databases:
        DatabaseSchema = _get_database_schema(databases[database])
        schema_dict[database] = fields.Nested(DatabaseSchema())

    return Schema.from_dict(schema_dict)


async def get_metadata_schema(app):
    db_structure = {}

    for name, db in app.databases.items():
        table_names = await db.table_names()
        view_names = await db.view_names()
        tables_structure = {t: await db.table_columns(t) for t in table_names}
        views_structure = {v: await db.table_columns(v) for v in view_names}

        db_structure[name] = {"tables": tables_structure, "views": views_structure}

        try:
            db_structure[name]["queries"] = app._metadata["databases"]["fixtures"][
                "queries"
            ].keys()
        except KeyError:
            db_structure[name]["queries"] = []

    DatabasesSchema = _get_databases_schema(db_structure)
    MetadataSchema = Schema.from_dict(
        {
            **STANDARD_FIELDS,
            **{
                "plugins": fields.Dict(),
                "custom_units": fields.List(fields.String()),
                "databases": fields.Nested(DatabasesSchema()),
            },
        }
    )

    return MetadataSchema
