import os
import jinja2

from datasette.utils import (
    to_css_class,
    validate_sql_select,
    is_url,
    path_with_added_args,
    path_with_removed_args,
)
from datasette.utils.asgi import AsgiFileDownload
from datasette.plugins import pm

from .base import DatasetteError, DataView


class DatabaseView(DataView):
    name = "database"

    async def data(self, request, database, hash, default_labels=False, _size=None):
        metadata = (self.ds.metadata("databases") or {}).get(database, {})
        self.ds.update_with_inherited_metadata(metadata)

        if request.args.get("sql"):
            if not self.ds.config("allow_sql"):
                raise DatasetteError("sql= is not allowed", status=400)
            sql = request.raw_args.pop("sql")
            validate_sql_select(sql)
            return await QueryView(self.ds).data(
                request, database, hash, sql, _size=_size, metadata=metadata
            )

        db = self.ds.databases[database]

        table_counts = await db.table_counts(5)
        views = await db.view_names()
        hidden_table_names = set(await db.hidden_table_names())
        all_foreign_keys = await db.get_all_foreign_keys()

        tables = []
        for table in table_counts:
            table_columns = await db.table_columns(table)
            tables.append(
                {
                    "name": table,
                    "columns": table_columns,
                    "primary_keys": await db.primary_keys(table),
                    "count": table_counts[table],
                    "hidden": table in hidden_table_names,
                    "fts_table": await db.fts_table(table),
                    "foreign_keys": all_foreign_keys[table],
                }
            )

        tables.sort(key=lambda t: (t["hidden"], t["name"]))
        return (
            {
                "database": database,
                "size": db.size,
                "tables": tables,
                "hidden_count": len([t for t in tables if t["hidden"]]),
                "views": views,
                "queries": self.ds.get_canned_queries(database),
            },
            {
                "show_hidden": request.args.get("_show_hidden"),
                "editable": True,
                "metadata": metadata,
                "allow_download": self.ds.config("allow_download")
                and not db.is_mutable
                and database != ":memory:",
            },
            ("database-{}.html".format(to_css_class(database)), "database.html"),
        )


class DatabaseDownload(DataView):
    name = "database_download"

    async def view_get(self, request, database, hash, correct_hash_present, **kwargs):
        if database not in self.ds.databases:
            raise DatasetteError("Invalid database", status=404)
        db = self.ds.databases[database]
        if db.is_memory:
            raise DatasetteError("Cannot download :memory: database", status=404)
        if not self.ds.config("allow_download") or db.is_mutable:
            raise DatasetteError("Database download is forbidden", status=403)
        if not db.path:
            raise DatasetteError("Cannot download database", status=404)
        filepath = db.path
        return AsgiFileDownload(
            filepath,
            filename=os.path.basename(filepath),
            content_type="application/octet-stream",
        )


class QueryView(DataView):
    async def data(
        self,
        request,
        database,
        hash,
        sql,
        editable=True,
        canned_query=None,
        metadata=None,
        _size=None,
    ):
        params = request.raw_args
        if "sql" in params:
            params.pop("sql")
        if "_shape" in params:
            params.pop("_shape")
        # Extract any :named parameters
        named_parameters = self.re_named_parameter.findall(sql)
        named_parameter_values = {
            named_parameter: params.get(named_parameter) or ""
            for named_parameter in named_parameters
        }

        # Set to blank string if missing from params
        for named_parameter in named_parameters:
            if named_parameter not in params:
                params[named_parameter] = ""

        extra_args = {}
        if params.get("_timelimit"):
            extra_args["custom_time_limit"] = int(params["_timelimit"])
        if _size:
            extra_args["page_size"] = _size
        results = await self.ds.execute(
            database, sql, params, truncate=True, **extra_args
        )
        columns = [r[0] for r in results.description]

        templates = ["query-{}.html".format(to_css_class(database)), "query.html"]
        if canned_query:
            templates.insert(
                0,
                "query-{}-{}.html".format(
                    to_css_class(database), to_css_class(canned_query)
                ),
            )

        async def extra_template():
            display_rows = []
            for row in results.rows:
                display_row = []
                for column, value in zip(results.columns, row):
                    display_value = value
                    # Let the plugins have a go
                    # pylint: disable=no-member
                    plugin_value = pm.hook.render_cell(
                        value=value,
                        column=column,
                        table=None,
                        database=database,
                        datasette=self.ds,
                    )
                    if plugin_value is not None:
                        display_value = plugin_value
                    else:
                        if value in ("", None):
                            display_value = jinja2.Markup("&nbsp;")
                        elif is_url(str(display_value).strip()):
                            display_value = jinja2.Markup(
                                '<a href="{url}">{url}</a>'.format(
                                    url=jinja2.escape(value.strip())
                                )
                            )
                    display_row.append(display_value)
                display_rows.append(display_row)
            return {
                "display_rows": display_rows,
                "custom_sql": True,
                "named_parameter_values": named_parameter_values,
                "editable": editable,
                "canned_query": canned_query,
                "metadata": metadata,
                "config": self.ds.config_dict(),
                "request": request,
                "path_with_added_args": path_with_added_args,
                "path_with_removed_args": path_with_removed_args,
                "hide_sql": "_hide_sql" in params,
            }

        return (
            {
                "database": database,
                "rows": results.rows,
                "truncated": results.truncated,
                "columns": columns,
                "query": {"sql": sql, "params": params},
            },
            extra_template,
            templates,
        )
