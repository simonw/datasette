import asyncio
import json
import textwrap
import time
import urllib.parse
from dataclasses import dataclass, field

import markupsafe
import sqlite_utils

from datasette.utils.asgi import NotFound, Forbidden, Response
from datasette.database import QueryInterrupted
from datasette.events import UpdateRowEvent, DeleteRowEvent
from datasette.resources import TableResource
from .base import BaseView, DatasetteError, _error, stream_csv
from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
    call_with_supported_arguments,
    CustomRow,
    InvalidSql,
    make_slot_function,
    path_from_row_pks,
    path_with_added_args,
    path_with_format,
    path_with_removed_args,
    to_css_class,
    escape_sqlite,
    sqlite3,
)
from datasette.plugins import pm
from datasette.extras import extra_names_from_request, ExtraScope
from . import Context, from_extra
from .table import (
    display_columns_and_rows,
    _table_page_data,
    row_label_from_label_column,
)
from .table_extras import RowExtraContext, resolve_row_extras, table_extra_registry


@dataclass
class RowContext(Context):
    "The page showing an individual row, e.g. /fixtures/facetable/1."

    documented_template = "row.html"
    extras_scope = ExtraScope.ROW

    # Fields resolved by registered extras - their documentation comes
    # from the description on each Extra class in table_extras.py
    columns: list = from_extra()
    database: str = from_extra()
    database_color: str = from_extra()
    foreign_key_tables: list = from_extra()
    metadata: dict = from_extra()
    primary_keys: list = from_extra()
    private: bool = from_extra()
    table: str = from_extra()

    # Fields added by the view code
    ok: bool = field(
        metadata={"help": "True if the data for this page was retrieved without errors"}
    )
    rows: list = field(
        metadata={
            "help": "A single-item list containing this row as a dictionary mapping column name to raw value."
        }
    )
    primary_key_values: list = field(
        metadata={"help": "Values of the primary keys for this row, from the URL"}
    )
    query_ms: float = field(
        metadata={
            "help": "Time taken by the SQL queries for this page, in milliseconds"
        }
    )
    display_columns: list = field(
        metadata={
            "help": "Column metadata used by the HTML table display. Each item includes ``name``, ``sortable``, ``is_pk``, ``type``, ``notnull``, ``description``, ``column_type`` and ``column_type_config`` keys."
        }
    )
    display_rows: list = field(
        metadata={
            "help": "Rows formatted for the HTML table display. Each row is iterable and contains cell dictionaries with ``column``, ``value``, ``raw`` and ``value_type`` keys."
        }
    )
    custom_table_templates: list = field(
        metadata={
            "help": "Custom template names that were considered for displaying this row's table, in lookup order."
        }
    )
    row_actions: list = field(
        metadata={
            "help": 'Row actions made available by core and plugin hooks. Each item is either a link with ``href``, ``label`` and optional ``description`` keys, or a button with ``type: "button"``, ``label``, optional ``description`` and optional ``attrs``. See :ref:`plugin_actions` and :ref:`plugin_hook_row_actions`.'
        }
    )
    row_mutation_ui: bool = field(
        metadata={"help": "True if the row edit/delete JavaScript UI should be enabled"}
    )
    table_page_data: dict = field(
        metadata={
            "help": "JSON data used by JavaScript on the row page. Includes ``database``, ``table`` and ``tableUrl``, plus optional ``foreignKeys`` mapping column names to autocomplete URLs."
        }
    )
    top_row: callable = field(
        metadata={
            "help": "Async callable that renders the ``top_row`` plugin slot for this row and returns HTML."
        }
    )
    renderers: dict = field(
        metadata={
            "help": "Dictionary mapping output format names such as ``json`` to URLs for this row in that format."
        }
    )
    url_csv: str = field(metadata={"help": "URL for the CSV export of this page"})
    url_csv_path: str = field(metadata={"help": "Path portion of the CSV export URL"})
    url_csv_hidden_args: list = field(
        metadata={
            "help": "List of ``(name, value)`` pairs for hidden form fields used by the CSV export form, preserving current options while forcing ``_size=max``."
        }
    )
    settings: dict = field(
        metadata={
            "help": "Dictionary of Datasette's current settings, keyed by setting name."
        }
    )
    select_templates: list = field(
        metadata={
            "help": "List of template names that were considered for this page, with the selected template prefixed by ``*``."
        }
    )
    alternate_url_json: str = field(
        metadata={"help": "URL for the JSON version of this page"}
    )


class RowView(BaseView):
    name = "row"

    def redirect(self, request, path, forward_querystring=True, remove_args=None):
        if request.query_string and "?" not in path and forward_querystring:
            path = f"{path}?{request.query_string}"
        if remove_args:
            path = path_with_removed_args(request, remove_args, path=path)
        response = Response.redirect(path)
        response.headers["Link"] = f"<{path}>; rel=preload"
        if self.ds.cors:
            add_cors_headers(response.headers)
        return response

    async def as_csv(self, request, database):
        return await stream_csv(self.ds, self.data, request, database)

    async def get(self, request):
        db = await self.ds.resolve_database(request)
        database = db.name
        database_route = db.route
        format_ = request.url_vars.get("format") or "html"
        data_kwargs = {}

        if format_ == "csv":
            return await self.as_csv(request, database_route)

        if format_ == "html":
            # HTML views default to expanding all foreign key labels
            data_kwargs["default_labels"] = True

        extra_template_data = {}
        start = time.perf_counter()
        status_code = None
        templates = ()
        try:
            response_or_template_contexts = await self.data(request, **data_kwargs)
            if isinstance(response_or_template_contexts, Response):
                return response_or_template_contexts
            # If it has four items, it includes an HTTP status code
            if len(response_or_template_contexts) == 4:
                (
                    data,
                    extra_template_data,
                    templates,
                    status_code,
                ) = response_or_template_contexts
            else:
                data, extra_template_data, templates = response_or_template_contexts
        except QueryInterrupted as ex:
            raise DatasetteError(
                textwrap.dedent("""
                <p>SQL query took too long. The time limit is controlled by the
                <a href="https://docs.datasette.io/en/stable/settings.html#sql-time-limit-ms">sql_time_limit_ms</a>
                configuration option.</p>
                <textarea style="width: 90%">{}</textarea>
                <script>
                let ta = document.querySelector("textarea");
                ta.style.height = ta.scrollHeight + "px";
                </script>
            """.format(markupsafe.escape(ex.sql))).strip(),
                title="SQL Interrupted",
                status=400,
                message_is_html=True,
            )
        except (sqlite3.OperationalError, InvalidSql) as e:
            raise DatasetteError(str(e), title="Invalid SQL", status=400)
        except sqlite3.OperationalError as e:
            raise DatasetteError(str(e))
        except DatasetteError:
            raise

        end = time.perf_counter()
        data["query_ms"] = (end - start) * 1000

        # Special case for .jsono extension - redirect to _shape=objects
        if format_ == "jsono":
            return self.redirect(
                request,
                path_with_added_args(
                    request,
                    {"_shape": "objects"},
                    path=request.path.rsplit(".jsono", 1)[0] + ".json",
                ),
                forward_querystring=False,
            )

        if format_ in self.ds.renderers.keys():
            # Dispatch request to the correct output format renderer
            # (CSV is not handled here due to streaming)
            result = call_with_supported_arguments(
                self.ds.renderers[format_][0],
                datasette=self.ds,
                columns=data.get("columns") or [],
                rows=data.get("rows") or [],
                sql=data.get("query", {}).get("sql", None),
                query_name=data.get("query_name"),
                database=database,
                table=data.get("table"),
                request=request,
                view_name=self.name,
                truncated=False,  # TODO: support this
                error=data.get("error"),
                # These will be deprecated in Datasette 1.0:
                args=request.args,
                data=data,
            )
            if asyncio.iscoroutine(result):
                result = await result
            if result is None:
                raise NotFound("No data")
            if isinstance(result, dict):
                response = Response(
                    body=result.get("body"),
                    status=result.get("status_code", status_code or 200),
                    content_type=result.get("content_type", "text/plain"),
                    headers=result.get("headers"),
                )
            elif isinstance(result, Response):
                response = result
                if status_code is not None:
                    # Over-ride the status code
                    response.status = status_code
            else:
                assert False, f"{result} should be dict or Response"
        elif format_ == "html":
            response = await self.html(request, data, extra_template_data, templates)
            if status_code is not None:
                response.status = status_code
        else:
            raise NotFound("Invalid format: {}".format(format_))

        ttl = request.args.get("_ttl", None)
        if ttl is None or not ttl.isdigit():
            ttl = self.ds.setting("default_cache_ttl")

        return self.set_response_headers(response, ttl)

    async def html(self, request, data, extra_template_data, templates):
        extras = {}
        if callable(extra_template_data):
            extras = extra_template_data()
            if asyncio.iscoroutine(extras):
                extras = await extras
        else:
            extras = extra_template_data

        url_labels_extra = {}
        if data.get("expandable_columns"):
            url_labels_extra = {"_labels": "on"}

        renderers = {}
        for key, (_, can_render) in self.ds.renderers.items():
            it_can_render = call_with_supported_arguments(
                can_render,
                datasette=self.ds,
                columns=data.get("columns") or [],
                rows=data.get("rows") or [],
                sql=data.get("query", {}).get("sql", None),
                query_name=data.get("query_name"),
                database=data.get("database"),
                table=data.get("table"),
                request=request,
                view_name=self.name,
            )
            it_can_render = await await_me_maybe(it_can_render)
            if it_can_render:
                renderers[key] = self.ds.urls.path(
                    path_with_format(
                        request=request,
                        path=request.scope.get("route_path"),
                        format=key,
                        extra_qs={**url_labels_extra},
                    )
                )

        url_csv_args = {"_size": "max", **url_labels_extra}
        url_csv = self.ds.urls.path(
            path_with_format(
                request=request,
                path=request.scope.get("route_path"),
                format="csv",
                extra_qs=url_csv_args,
            )
        )
        url_csv_path = url_csv.split("?")[0]
        context = {**data, **extras}
        if "metadata" not in context:
            context["metadata"] = await self.ds.get_instance_metadata()

        environment = self.ds.get_jinja_environment(request)
        template = environment.select_template(templates)
        alternate_url_json = self.ds.absolute_url(
            request,
            self.ds.urls.path(
                path_with_format(
                    request=request,
                    path=request.scope.get("route_path"),
                    format="json",
                )
            ),
        )
        return Response.html(
            await self.ds.render_template(
                template,
                RowContext(
                    columns=context["columns"],
                    database=context["database"],
                    database_color=context["database_color"],
                    foreign_key_tables=context["foreign_key_tables"],
                    metadata=context["metadata"],
                    primary_keys=context["primary_keys"],
                    private=context["private"],
                    table=context["table"],
                    ok=context["ok"],
                    rows=context["rows"],
                    primary_key_values=context["primary_key_values"],
                    query_ms=context["query_ms"],
                    display_columns=context["display_columns"],
                    display_rows=context["display_rows"],
                    custom_table_templates=context["custom_table_templates"],
                    row_actions=context["row_actions"],
                    row_mutation_ui=context["row_mutation_ui"],
                    table_page_data=context["table_page_data"],
                    top_row=context["top_row"],
                    renderers=renderers,
                    url_csv=url_csv,
                    url_csv_path=url_csv_path,
                    url_csv_hidden_args=[
                        (key, value)
                        for key, value in urllib.parse.parse_qsl(request.query_string)
                        if key not in ("_labels", "_facet", "_size")
                    ]
                    + [("_size", "max")],
                    settings=self.ds.settings_dict(),
                    select_templates=[
                        f"{'*' if template_name == template.name else ''}{template_name}"
                        for template_name in templates
                    ],
                    alternate_url_json=alternate_url_json,
                ),
                request=request,
                view_name=self.name,
            ),
            headers={
                "Link": '<{}>; rel="alternate"; type="application/json+datasette"'.format(
                    alternate_url_json
                )
            },
        )

    def set_response_headers(self, response, ttl):
        # Set far-future cache expiry
        if self.ds.cache_headers and response.status == 200:
            ttl = int(ttl)
            if ttl == 0:
                ttl_header = "no-cache"
            else:
                ttl_header = f"max-age={ttl}"
            response.headers["Cache-Control"] = ttl_header
        response.headers["Referrer-Policy"] = "no-referrer"
        if self.ds.cors:
            add_cors_headers(response.headers)
        return response

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
                    datasette=self.ds,
                    request=request,
                    db=db,
                    database_name=database,
                    table_name=table,
                    is_view=not is_table,
                    table_insert_ui=None,
                    table_alter_ui=None,
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
