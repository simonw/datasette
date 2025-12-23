"""
Shared extras functionality for table and row views.
"""

from datasette.plugins import pm
from datasette.resources import TableResource
from datasette.utils import await_me_maybe


def _get_extras(request):
    """Parse ?_extra= parameters from request into a set of extra names."""
    extra_bits = request.args.getlist("_extra")
    extras = set()
    for bit in extra_bits:
        extras.update(bit.split(","))
    return extras


async def render_cells_for_rows(
    datasette, database_name, table_name, rows, columns, request
):
    """
    Call render_cell plugin hook for each cell.
    Returns a list of dicts, one per row, containing only cells modified by plugins.
    """
    rendered_rows = []
    for row in rows:
        rendered_row = {}
        for value, column in zip(row, columns):
            plugin_display_value = None
            for candidate in pm.hook.render_cell(
                row=row,
                value=value,
                column=column,
                table=table_name,
                database=database_name,
                datasette=datasette,
                request=request,
            ):
                candidate = await await_me_maybe(candidate)
                if candidate is not None:
                    plugin_display_value = candidate
                    break
            if plugin_display_value:
                rendered_row[column] = str(plugin_display_value)
        rendered_rows.append(rendered_row)
    return rendered_rows


class SharedExtras:
    """
    Extras that are shared between table and row views.

    Initialize with context, then call get_extras() to process requested extras.
    Subclass to add view-specific extras.
    """

    # Extras that this class can provide
    available_extras = {
        "columns",
        "primary_keys",
        "database",
        "table",
        "database_color",
        "query",
        "render_cell",
        "table_definition",
        "view_definition",
        "is_view",
        "private",
        "metadata",
    }

    def __init__(
        self,
        datasette,
        db,
        database_name,
        table_name,
        request,
        rows,
        columns,
        pks,
        sql=None,
        params=None,
    ):
        self.datasette = datasette
        self.db = db
        self.database_name = database_name
        self.table_name = table_name
        self.request = request
        self.rows = rows
        self.columns = columns
        self.pks = pks
        self.sql = sql
        self.params = params

    async def get_extras(self, extras):
        """
        Process a set of extra names and return a dict of results.
        Only processes extras that this class knows how to handle.
        """
        results = {}
        for extra in extras:
            method = getattr(self, f"extra_{extra}", None)
            if method:
                results[extra] = await method()
        return results

    async def extra_columns(self):
        """Column names returned by this query"""
        return self.columns

    async def extra_primary_keys(self):
        """Primary keys for this table"""
        return self.pks

    async def extra_database(self):
        """Database name"""
        return self.database_name

    async def extra_table(self):
        """Table name"""
        return self.table_name

    async def extra_database_color(self):
        """Database color"""
        return self.db.color

    async def extra_query(self):
        """Details of the underlying SQL query"""
        return {
            "sql": self.sql,
            "params": self.params,
        }

    async def extra_render_cell(self):
        """Rendered HTML for each cell using the render_cell plugin hook"""
        return await render_cells_for_rows(
            self.datasette,
            self.database_name,
            self.table_name,
            self.rows,
            self.columns,
            self.request,
        )

    async def extra_table_definition(self):
        """SQL schema for this table"""
        return await self.db.get_table_definition(self.table_name)

    async def extra_view_definition(self):
        """SQL schema for this view (if it is a view)"""
        return await self.db.get_view_definition(self.table_name)

    async def extra_is_view(self):
        """Is this a view rather than a table?"""
        return await self.db.view_exists(self.table_name)

    async def extra_private(self):
        """Is this table private?"""
        visible, _ = await self.datasette.check_visibility(
            self.request.actor,
            action="view-table",
            resource=TableResource(database=self.database_name, table=self.table_name),
        )
        return not visible

    async def extra_metadata(self):
        """Metadata about the table and database"""
        tablemetadata = await self.datasette.get_resource_metadata(
            self.database_name, self.table_name
        )
        rows = await self.datasette.get_internal_database().execute(
            """
            SELECT column_name, value
            FROM metadata_columns
            WHERE database_name = ?
              AND resource_name = ?
              AND key = 'description'
            """,
            [self.database_name, self.table_name],
        )
        tablemetadata["columns"] = dict(rows)
        return tablemetadata
