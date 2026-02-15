"""
Public API for displaying interactive pages of SQL query results.

This module provides :class:`QueryPage`, a class-based view (CBV) that
executes a SQL query and renders an interactive page showing the results.
It supports HTML, JSON, and CSV output formats.

This is the shared foundation used by both the ``/db/-/query`` and
``/db/table`` views internally. Plugins can import and use this class
directly, or subclass it for customization.

Simple usage in a plugin::

    from datasette import hookimpl
    from datasette.query_page import QueryPage

    @hookimpl
    def register_routes(datasette):
        return [
            (r"/my-query", my_query_view),
        ]

    async def my_query_view(datasette, request):
        page = QueryPage(
            datasette,
            request,
            database="_internal",
            sql="select * from catalog_tables",
        )
        return await page.response()

Subclass for customization::

    class MyQueryPage(QueryPage):
        async def title(self):
            return "My Custom Results"

        async def extra_context(self):
            return {"custom_key": "value"}

    async def my_view(datasette, request):
        return await MyQueryPage(
            datasette, request,
            database="_internal",
            sql="select * from catalog_tables",
        ).response()
"""
import asyncio
import hashlib
import markupsafe
import textwrap

from datasette.database import QueryInterrupted
from datasette.plugins import pm
from datasette.resources import DatabaseResource
from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
    call_with_supported_arguments,
    format_bytes,
    is_url,
    make_slot_function,
    named_parameters as derive_named_parameters,
    path_with_added_args,
    path_with_format,
    path_with_removed_args,
    to_css_class,
    truncate_url,
    validate_sql_select,
    InvalidSql,
    sqlite3,
)
from datasette.utils.asgi import NotFound, Response
from datasette.views.base import DatasetteError, stream_csv


class QueryPage:
    """Render an interactive page displaying SQL query results.

    This class encapsulates the logic for executing a SQL query, formatting
    the result rows for display, and rendering an HTML page (or JSON/CSV
    response) with those results.

    It is designed in the style of a class-based view: instantiate with
    the necessary parameters, then call :meth:`response` to get back a
    :class:`Response`. Override methods on a subclass for customization.

    Args:
        datasette: The :class:`~datasette.Datasette` instance.
        request: The incoming :class:`~datasette.utils.asgi.Request`.
        database: Name of the database to query.
        sql: The SQL query string.
        params: Optional dict of query parameters for named placeholders.
        editable: Whether to show an editable SQL editor (default True).
        canned_query: Optional canned query dict (used internally).
        private: Whether this is a private/restricted resource (default False).
        templates: Optional list of Jinja template names to try.
        extra_template_context: Optional dict merged into the template context.
    """

    def __init__(
        self,
        datasette,
        request,
        database,
        sql,
        params=None,
        *,
        editable=True,
        canned_query=None,
        private=False,
        templates=None,
        extra_template_context=None,
    ):
        self.datasette = datasette
        self.request = request
        self.database = database
        self.sql = sql
        self.params = params or {}
        self.editable = editable
        self.canned_query = canned_query
        self.private = private
        self._templates = templates
        self._extra_template_context = extra_template_context or {}
        #: Set to True by :meth:`execute_query` if results were truncated.
        self.truncated = False

    # ------------------------------------------------------------------
    # Override points – subclass and override these for customisation
    # ------------------------------------------------------------------

    async def title(self):
        """Return the page title. Override for custom titles."""
        if self.canned_query and self.canned_query.get("title"):
            return self.canned_query["title"]
        return self.database

    async def execute_query(self):
        """Execute the SQL query and return ``(columns, rows, error)``.

        *columns* is a list of column name strings.
        *rows* is a list of row tuples/dicts.
        *error* is a string error message or ``None``.

        Override this to customise query execution – for example to add
        pagination, modify the SQL, or query a different source entirely.

        .. note::

            Pagination for arbitrary queries requires knowing which column(s)
            can serve as a reliable sort key for keyset pagination. Since this
            cannot be guessed from arbitrary SQL, a future implementation
            could accept sort column hints via query string parameters
            (e.g. ``?_sort=id``). The table view already implements keyset
            pagination using the table's primary keys.
        """
        query_error = None
        columns = []
        rows = []

        if not self.sql:
            return columns, rows, query_error

        canned_query_write = bool(
            self.canned_query and self.canned_query.get("write")
        )
        if canned_query_write:
            # Write queries don't execute on GET
            return columns, rows, query_error

        extra_args = {}
        if self.params.get("_timelimit"):
            extra_args["custom_time_limit"] = int(self.params["_timelimit"])

        try:
            if not self.canned_query:
                validate_sql_select(self.sql)
            else:
                # Canned queries can use magic parameters
                from datasette.views.database import MagicParameters

                self.params = MagicParameters(
                    self.sql, self.params, self.request, self.datasette
                )
                await self.params.execute_params()

            results = await self.datasette.execute(
                self.database, self.sql, self.params, truncate=True, **extra_args
            )
            columns = results.columns
            rows = results.rows
            self.truncated = results.truncated
        except QueryInterrupted as ex:
            raise DatasetteError(
                textwrap.dedent(
                    """
                <p>SQL query took too long. The time limit is controlled by the
                <a href="https://docs.datasette.io/en/stable/settings.html#sql-time-limit-ms">sql_time_limit_ms</a>
                configuration option.</p>
                <textarea style="width: 90%">{}</textarea>
                <script>
                let ta = document.querySelector("textarea");
                ta.style.height = ta.scrollHeight + "px";
                </script>
            """.format(
                        markupsafe.escape(ex.sql)
                    )
                ).strip(),
                title="SQL Interrupted",
                status=400,
                message_is_html=True,
            )
        except sqlite3.DatabaseError as ex:
            query_error = str(ex)
        except (sqlite3.OperationalError, InvalidSql) as ex:
            raise DatasetteError(str(ex), title="Invalid SQL", status=400)

        return columns, rows, query_error

    async def display_rows(self, rows, columns):
        """Format raw result rows into display-ready values.

        Returns a list of lists of display values (strings or Markup).
        Override to customise how cell values are rendered.
        """
        return await _display_rows(
            self.datasette, self.database, self.request, rows, columns
        )

    async def get_templates(self):
        """Return ordered list of Jinja template names to try.

        Override to use a custom template for your page.
        """
        if self._templates:
            return list(self._templates)
        templates = [
            "query-{}.html".format(to_css_class(self.database)),
            "query.html",
        ]
        if self.canned_query:
            templates.insert(
                0,
                "query-{}-{}.html".format(
                    to_css_class(self.database),
                    to_css_class(self.canned_query["name"]),
                ),
            )
        return templates

    async def extra_context(self):
        """Return a dict of extra template context variables.

        Override to inject additional context into the template. This is
        the simplest customisation point for adding data to the page.
        """
        return {}

    async def query_actions(self):
        """Return list of action links for the query action menu.

        Override to add custom action links.
        """
        links = []
        for hook in pm.hook.query_actions(
            datasette=self.datasette,
            actor=self.request.actor,
            database=self.database,
            query_name=(
                self.canned_query["name"] if self.canned_query else None
            ),
            request=self.request,
            sql=self.sql,
            params=self.params,
        ):
            extra_links = await await_me_maybe(hook)
            if extra_links:
                links.extend(extra_links)
        return links

    # ------------------------------------------------------------------
    # Core response method
    # ------------------------------------------------------------------

    async def response(self):
        """Execute the query and return the appropriate :class:`Response`.

        Dispatches to HTML, JSON, CSV, or a plugin renderer based on the
        requested format (derived from the URL extension or query params).

        Returns:
            :class:`~datasette.utils.asgi.Response`
        """
        format_ = self.request.url_vars.get("format") or "html"

        columns, rows, error = await self.execute_query()

        if format_ == "csv":
            return await self._csv_response()

        if format_ in self.datasette.renderers.keys():
            return await self._renderer_response(
                format_, columns, rows, error
            )

        if format_ == "html":
            return await self._html_response(columns, rows, error)

        raise NotFound("Invalid format: {}".format(format_))

    # ------------------------------------------------------------------
    # Format-specific response builders
    # ------------------------------------------------------------------

    async def _csv_response(self):
        """Return a streaming CSV response."""
        sql = self.sql
        params = self.params

        async def fetch_data_for_csv(request, _next=None):
            db = self.datasette.get_database(self.database)
            results = await db.execute(sql, params, truncate=True)
            data = {"rows": results.rows, "columns": results.columns}
            return data, None, None

        return await stream_csv(
            self.datasette, fetch_data_for_csv, self.request, self.database
        )

    async def _renderer_response(self, format_, columns, rows, error):
        """Dispatch to a plugin output renderer."""
        result = call_with_supported_arguments(
            self.datasette.renderers[format_][0],
            datasette=self.datasette,
            columns=columns,
            rows=rows,
            sql=self.sql,
            query_name=(
                self.canned_query["name"] if self.canned_query else None
            ),
            database=self.database,
            table=None,
            request=self.request,
            view_name="query",
            truncated=self.truncated,
            error=error,
            # Deprecated but kept for backwards compat:
            args=self.request.args,
            data={"ok": True, "rows": rows, "columns": columns},
        )
        if asyncio.iscoroutine(result):
            result = await result
        if result is None:
            raise NotFound("No data")

        if isinstance(result, dict):
            r = Response(
                body=result.get("body"),
                status=result.get("status_code") or 200,
                content_type=result.get("content_type", "text/plain"),
                headers=result.get("headers"),
            )
        elif isinstance(result, Response):
            r = result
        else:
            raise AssertionError(
                "{} should be dict or Response".format(result)
            )
        if self.datasette.cors:
            add_cors_headers(r.headers)
        return r

    async def _html_response(self, columns, rows, error):
        """Build and return the HTML response."""
        datasette = self.datasette
        request = self.request
        database = self.database
        db = datasette.get_database(database)

        templates = await self.get_templates()
        environment = datasette.get_jinja_environment(request)
        template = environment.select_template(templates)

        alternate_url_json = datasette.absolute_url(
            request,
            datasette.urls.path(
                path_with_format(request=request, format="json")
            ),
        )
        headers = {
            "Link": '<{}>; rel="alternate"; type="application/json+datasette"'.format(
                alternate_url_json
            )
        }

        metadata = await datasette.get_database_metadata(database)
        display_rows = await self.display_rows(rows, columns)

        # Named parameters
        named_parameters = []
        if self.canned_query and self.canned_query.get("params"):
            named_parameters = self.canned_query["params"]
        if not named_parameters and self.sql:
            named_parameters = derive_named_parameters(self.sql)
        named_parameter_values = {
            p: self.params.get(p) or ""
            for p in named_parameters
            if not p.startswith("_")
        }

        # Renderers
        renderers = await self._available_renderers(columns, rows)

        allow_execute_sql = await datasette.allowed(
            action="execute-sql",
            resource=DatabaseResource(database=database),
            actor=request.actor,
        )

        # Show/hide SQL controls
        canned_query_write = bool(
            self.canned_query and self.canned_query.get("write")
        )
        show_hide_hidden, hide_sql, show_hide_link, show_hide_text = (
            self._show_hide_sql_controls()
        )

        # Edit SQL URL
        edit_sql_url = self._edit_sql_url(
            allow_execute_sql, named_parameter_values
        )

        # Tables for autocomplete
        from datasette.views.database import (
            get_tables,
            _table_columns,
        )

        allowed_tables_page = await datasette.allowed_resources(
            "view-table",
            request.actor,
            parent=database,
            include_is_private=True,
            limit=1000,
        )
        allowed_dict = {r.child: r for r in allowed_tables_page.resources}

        context = {
            "database": database,
            "database_color": db.color,
            "query": {"sql": self.sql, "params": self.params},
            "canned_query": (
                self.canned_query["name"] if self.canned_query else None
            ),
            "private": self.private,
            "canned_query_write": canned_query_write,
            "db_is_immutable": not db.is_mutable,
            "error": error,
            "hide_sql": hide_sql,
            "show_hide_link": datasette.urls.path(show_hide_link),
            "show_hide_text": show_hide_text,
            "editable": self.editable and not self.canned_query,
            "allow_execute_sql": allow_execute_sql,
            "tables": await get_tables(
                datasette, request, db, allowed_dict
            ),
            "named_parameter_values": named_parameter_values,
            "edit_sql_url": edit_sql_url,
            "display_rows": display_rows,
            "table_columns": (
                await _table_columns(datasette, database)
                if allow_execute_sql
                else {}
            ),
            "columns": columns,
            "renderers": renderers,
            "url_csv": datasette.urls.path(
                path_with_format(
                    request=request,
                    format="csv",
                    extra_qs={"_size": "max"},
                )
            ),
            "show_hide_hidden": markupsafe.Markup(show_hide_hidden),
            "metadata": self.canned_query or metadata,
            "alternate_url_json": alternate_url_json,
            "select_templates": [
                "{}{}".format(
                    "*" if t == template.name else "", t
                )
                for t in templates
            ],
            "top_query": make_slot_function(
                "top_query",
                datasette,
                request,
                database=database,
                sql=self.sql,
            ),
            "top_canned_query": make_slot_function(
                "top_canned_query",
                datasette,
                request,
                database=database,
                query_name=(
                    self.canned_query["name"]
                    if self.canned_query
                    else None
                ),
            ),
            "query_actions": self.query_actions,
        }

        # Merge extra context from subclass
        extra_ctx = await self.extra_context()
        if extra_ctx:
            context.update(extra_ctx)

        # Merge extra context from constructor
        if self._extra_template_context:
            context.update(self._extra_template_context)

        r = Response.html(
            await datasette.render_template(
                template,
                context,
                request=request,
                view_name="database",
            ),
            headers=headers,
        )
        if datasette.cors:
            add_cors_headers(r.headers)
        return r

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _show_hide_sql_controls(self):
        """Compute the show/hide SQL toggle state.

        Returns (show_hide_hidden, hide_sql, show_hide_link, show_hide_text).
        """
        params = {key: self.request.args.get(key) for key in self.request.args}
        show_hide_hidden = ""

        if self.canned_query and self.canned_query.get("hide_sql"):
            if bool(params.get("_show_sql")):
                show_hide_link = path_with_removed_args(
                    self.request, {"_show_sql"}
                )
                show_hide_text = "hide"
                show_hide_hidden = (
                    '<input type="hidden" name="_show_sql" value="1">'
                )
            else:
                show_hide_link = path_with_added_args(
                    self.request, {"_show_sql": 1}
                )
                show_hide_text = "show"
        else:
            if bool(params.get("_hide_sql")):
                show_hide_link = path_with_removed_args(
                    self.request, {"_hide_sql"}
                )
                show_hide_text = "show"
                show_hide_hidden = (
                    '<input type="hidden" name="_hide_sql" value="1">'
                )
            else:
                show_hide_link = path_with_added_args(
                    self.request, {"_hide_sql": 1}
                )
                show_hide_text = "hide"

        hide_sql = show_hide_text == "show"
        return show_hide_hidden, hide_sql, show_hide_link, show_hide_text

    def _edit_sql_url(self, allow_execute_sql, named_parameter_values):
        """Build the 'Edit SQL' URL for canned queries, or None."""
        if not self.canned_query:
            return None

        is_validated = False
        try:
            validate_sql_select(self.sql)
            is_validated = True
        except InvalidSql:
            pass

        if allow_execute_sql and is_validated and ":_" not in self.sql:
            from urllib.parse import urlencode

            return (
                self.datasette.urls.database(self.database)
                + "/-/query"
                + "?"
                + urlencode({"sql": self.sql, **named_parameter_values})
            )
        return None

    async def _available_renderers(self, columns, rows):
        """Build dict of {renderer_name: url} for available output formats."""
        renderers = {}
        for key, (_, can_render) in self.datasette.renderers.items():
            it_can_render = call_with_supported_arguments(
                can_render,
                datasette=self.datasette,
                columns=columns or [],
                rows=rows or [],
                sql=self.sql,
                query_name=(
                    self.canned_query["name"] if self.canned_query else None
                ),
                database=self.database,
                table=None,
                request=self.request,
                view_name="database",
            )
            it_can_render = await await_me_maybe(it_can_render)
            if it_can_render:
                renderers[key] = self.datasette.urls.path(
                    path_with_format(request=self.request, format=key)
                )
        return renderers

    # ------------------------------------------------------------------
    # Class-level convenience for use as a route handler
    # ------------------------------------------------------------------

    @classmethod
    def view(cls, datasette, database, sql, **kwargs):
        """Return an async view function suitable for use with register_routes.

        Usage::

            @hookimpl
            def register_routes(datasette):
                return [
                    (r"/my-query", QueryPage.view(
                        datasette,
                        database="_internal",
                        sql="select * from catalog_tables",
                    )),
                ]
        """

        async def _view(datasette_arg, request):
            page = cls(
                datasette_arg,
                request,
                database=database,
                sql=sql,
                **kwargs,
            )
            return await page.response()

        return _view


# ------------------------------------------------------------------
# Shared utility: format rows for display (used by both query and table views)
# ------------------------------------------------------------------


async def _display_rows(datasette, database, request, rows, columns):
    """Format raw query result rows into display-ready values.

    Used by both the query page and the table page for rendering cell
    values in HTML. Calls the ``render_cell`` plugin hook for each cell.

    Args:
        datasette: Datasette instance
        database: Database name string
        request: Request object
        rows: List of row tuples
        columns: List of column name strings

    Returns:
        List of lists of display values (strings or :class:`markupsafe.Markup`)
    """
    display_rows = []
    truncate_cells = datasette.setting("truncate_cells_html")
    for row in rows:
        display_row = []
        for column, value in zip(columns, row):
            display_value = value
            # Let plugins have a go
            plugin_display_value = None
            for candidate in pm.hook.render_cell(
                row=row,
                value=value,
                column=column,
                table=None,
                database=database,
                datasette=datasette,
                request=request,
            ):
                candidate = await await_me_maybe(candidate)
                if candidate is not None:
                    plugin_display_value = candidate
                    break
            if plugin_display_value is not None:
                display_value = plugin_display_value
            else:
                if value in ("", None):
                    display_value = markupsafe.Markup("&nbsp;")
                elif is_url(str(display_value).strip()):
                    display_value = markupsafe.Markup(
                        '<a href="{url}">{truncated_url}</a>'.format(
                            url=markupsafe.escape(value.strip()),
                            truncated_url=markupsafe.escape(
                                truncate_url(value.strip(), truncate_cells)
                            ),
                        )
                    )
                elif isinstance(display_value, bytes):
                    blob_url = path_with_format(
                        request=request,
                        format="blob",
                        extra_qs={
                            "_blob_column": column,
                            "_blob_hash": hashlib.sha256(
                                display_value
                            ).hexdigest(),
                        },
                    )
                    formatted = format_bytes(len(value))
                    display_value = markupsafe.Markup(
                        '<a class="blob-download" href="{}"{}>'
                        "&lt;Binary:&nbsp;{:,}&nbsp;byte{}&gt;</a>".format(
                            blob_url,
                            (
                                ' title="{}"'.format(formatted)
                                if "bytes" not in formatted
                                else ""
                            ),
                            len(value),
                            "" if len(value) == 1 else "s",
                        )
                    )
                else:
                    display_value = str(value)
                    if truncate_cells and len(display_value) > truncate_cells:
                        display_value = (
                            display_value[:truncate_cells] + "\u2026"
                        )
            display_row.append(display_value)
        display_rows.append(display_row)
    return display_rows


# ------------------------------------------------------------------
# Shared format dispatch helper (used by table_view too)
# ------------------------------------------------------------------


async def dispatch_renderer(
    datasette,
    request,
    format_,
    columns,
    rows,
    sql,
    *,
    database,
    table=None,
    query_name=None,
    truncated=False,
    error=None,
    view_name="table",
    data=None,
):
    """Dispatch a request to a plugin output renderer.

    This is the shared code used by both the query view and the table
    view for handling non-HTML, non-CSV output formats registered by
    plugins via the ``register_output_renderer`` hook.

    Args:
        datasette: Datasette instance
        request: Request object
        format_: The format string (e.g. "json")
        columns: List of column names
        rows: List of result rows
        sql: The SQL query string
        database: Database name
        table: Optional table name
        query_name: Optional canned query name
        truncated: Whether results were truncated
        error: Error message or None
        view_name: View name string for plugin hooks
        data: Optional full data dict for backwards compat

    Returns:
        :class:`Response` object
    """
    result = call_with_supported_arguments(
        datasette.renderers[format_][0],
        datasette=datasette,
        columns=columns,
        rows=rows,
        sql=sql,
        query_name=query_name,
        database=database,
        table=table,
        request=request,
        view_name=view_name,
        truncated=truncated,
        error=error,
        # Deprecated but kept for backwards compat:
        args=request.args,
        data=data or {"ok": True, "rows": rows, "columns": columns},
    )
    if asyncio.iscoroutine(result):
        result = await result
    if result is None:
        raise NotFound("No data")

    if isinstance(result, dict):
        r = Response(
            body=result.get("body"),
            status=result.get("status_code") or 200,
            content_type=result.get("content_type", "text/plain"),
            headers=result.get("headers"),
        )
    elif isinstance(result, Response):
        r = result
    else:
        raise AssertionError("{} should be dict or Response".format(result))

    if datasette.cors:
        add_cors_headers(r.headers)
    return r
