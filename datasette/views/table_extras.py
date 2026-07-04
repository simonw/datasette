import itertools
from dataclasses import dataclass

from datasette.column_types import SQLiteType
from datasette.database import QueryInterrupted
from datasette.extras import Extra, ExtraExample, ExtraRegistry, ExtraScope, Provider
from datasette.plugins import pm
from datasette.resources import DatabaseResource, TableResource
from datasette.utils import (
    await_me_maybe,
    call_with_supported_arguments,
    path_with_added_args,
    path_with_format,
    path_with_removed_args,
    to_css_class,
)


@dataclass(frozen=True)
class TableExtraContext:
    datasette: object
    request: object
    resolved: object
    db: object
    database_name: str
    table_name: str
    is_view: bool
    private: bool
    rows: list
    columns: list
    results_description: list
    table_columns: list
    pks: list
    count_sql: str
    from_sql: str
    from_sql_params: dict
    nocount: object
    nofacet: object
    nosuggest: object
    next_arg: object
    next_url: str | None
    sql: str
    sql_no_order_no_limit: str
    params: dict
    table_metadata: dict
    filters: object
    extra_human_descriptions: list
    sort: str | None
    sort_desc: str | None
    sortable_columns: set
    extras: set
    extra_registry: ExtraRegistry
    display_columns_and_rows: object
    run_sequential: object
    query_name: str | None = None
    scope: ExtraScope = ExtraScope.TABLE


@dataclass(frozen=True)
class RowExtraContext:
    datasette: object
    request: object
    db: object
    database_name: str
    table_name: str
    private: bool
    rows: list
    columns: list
    pks: list
    pk_values: list
    sql: str
    params: dict
    extras: set
    extra_registry: ExtraRegistry
    foreign_key_tables: object
    is_view: bool = False
    scope: ExtraScope = ExtraScope.ROW


@dataclass(frozen=True)
class QueryExtraContext:
    datasette: object
    request: object
    db: object
    database_name: str
    private: bool
    rows: list
    columns: list
    sql: str | None
    params: dict
    query_name: str | None
    metadata: dict
    extras: set
    extra_registry: ExtraRegistry
    table_name: str | None = None
    is_view: bool = False
    pks: list | None = None
    scope: ExtraScope = ExtraScope.QUERY


class CountSqlExtra(Extra):
    description = "SQL query string used to calculate the total count for the current table view, including active filters."
    example = ExtraExample("/fixtures/facetable.json?_size=0&_extra=count_sql")
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        return context.count_sql


class CountExtra(Extra):
    description = "Total count of rows matching these filters"
    example = ExtraExample("/fixtures/facetable.json?_extra=count")
    scopes = {ExtraScope.TABLE}
    expensive = True

    async def resolve(self, context):
        count = None
        if (
            not context.db.is_mutable
            and context.datasette.inspect_data
            and context.count_sql == f"select count(*) from {context.table_name} "
        ):
            try:
                count = context.datasette.inspect_data[context.database_name]["tables"][
                    context.table_name
                ]["count"]
            except KeyError:
                pass

        if context.count_sql and count is None and not context.nocount:
            count_sql_limited = "select count(*) from (select * {} limit {})".format(
                context.from_sql, context.db.count_limit + 1
            )
            try:
                count_rows = list(
                    await context.db.execute(count_sql_limited, context.from_sql_params)
                )
                count = count_rows[0][0]
            except QueryInterrupted:
                pass
        return count


class FacetInstancesProvider(Provider):
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context, count):
        facet_instances = []
        facet_classes = list(
            itertools.chain.from_iterable(pm.hook.register_facet_classes())
        )
        for facet_class in facet_classes:
            facet_instances.append(
                facet_class(
                    context.datasette,
                    context.request,
                    context.database_name,
                    sql=context.sql_no_order_no_limit,
                    params=context.params,
                    table=context.table_name,
                    table_config=context.table_metadata,
                    row_count=count,
                )
            )
        return facet_instances


class FacetResultsExtra(Extra):
    description = "Results of facets calculated against this data. A dictionary with ``results`` and ``timed_out`` keys: ``results`` maps facet names to facet dictionaries with ``name``, ``type``, ``results`` and URL keys, and each facet result item includes ``value``, ``label``, ``count`` and ``toggle_url``."
    example = ExtraExample(
        value={
            "results": {
                "state": {
                    "name": "state",
                    "type": "column",
                    "results": [
                        {"value": "CA", "label": "CA", "count": 10},
                        {"value": "MI", "label": "MI", "count": 4},
                    ],
                }
            },
            "timed_out": [],
        },
        note="Shape abbreviated from /fixtures/facetable.json?_facet=state&_extra=facet_results.",
    )
    scopes = {ExtraScope.TABLE}
    expensive = True
    docs_note = "See :ref:`facets` for details of how facets work."

    async def resolve(self, context, facet_instances):
        facet_results = {}
        facets_timed_out = []

        if not context.nofacet:
            facet_awaitables = [facet.facet_results() for facet in facet_instances]
            facet_awaitable_results = await context.run_sequential(*facet_awaitables)
            for (
                instance_facet_results,
                instance_facets_timed_out,
            ) in facet_awaitable_results:
                for facet_info in instance_facet_results:
                    base_key = facet_info["name"]
                    key = base_key
                    i = 1
                    while key in facet_results:
                        i += 1
                        key = f"{base_key}_{i}"
                    facet_results[key] = facet_info
                facets_timed_out.extend(instance_facets_timed_out)

        return {
            "results": facet_results,
            "timed_out": facets_timed_out,
        }


class FacetsTimedOutExtra(Extra):
    description = (
        "List of names of facet calculations that exceeded the facet time limit."
    )
    example = ExtraExample(
        "/fixtures/facetable.json?_facet=state&_extra=facets_timed_out",
        note=(
            "A list of the names of any facets that exceeded the "
            ":ref:`setting_facet_time_limit_ms` time limit - an empty list "
            "if every facet calculation completed."
        ),
    )
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context, facet_results):
        return facet_results["timed_out"]


class SuggestedFacetsExtra(Extra):
    description = "Suggestions for facets that might return interesting results. Each item is a dictionary with ``name`` and ``toggle_url`` keys, and may include extra keys such as ``type`` or ``label`` depending on the facet class."
    example = ExtraExample(
        value=[
            {
                "name": "state",
                "toggle_url": "http://localhost/fixtures/facetable.json?_extra=suggested_facets&_facet=state",
            }
        ],
        note="Shape abbreviated from /fixtures/facetable.json?_extra=suggested_facets.",
    )
    scopes = {ExtraScope.TABLE}
    expensive = True
    docs_note = (
        "Suggestions are controlled by the :ref:`setting_suggest_facets` setting."
    )

    async def resolve(self, context, facet_instances):
        suggested_facets = []
        if (
            context.datasette.setting("suggest_facets")
            and context.datasette.setting("allow_facet")
            and not context.next_arg
            and not context.nofacet
            and not context.nosuggest
        ):
            facet_suggest_awaitables = [facet.suggest() for facet in facet_instances]
            for suggest_result in await context.run_sequential(
                *facet_suggest_awaitables
            ):
                suggested_facets.extend(suggest_result)
        return suggested_facets


class HumanDescriptionEnExtra(Extra):
    description = "Human-readable description of the filters"
    example = ExtraExample(
        "/fixtures/facetable.json?state=CA&_sort=pk&_extra=human_description_en"
    )
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        human_description_en = context.filters.human_description_en(
            extra=context.extra_human_descriptions
        )
        if context.sort or context.sort_desc:
            sorted_by = "sorted by {}{}".format(
                (context.sort or context.sort_desc),
                " descending" if context.sort_desc else "",
            )
            human_description_en = " ".join(
                [b for b in [human_description_en, sorted_by] if b]
            )
        return human_description_en


class NextUrlExtra(Extra):
    description = "Full URL for the next page of results"
    example = ExtraExample(
        "/fixtures/facetable.json?_size=1&_extra=next_url",
        note=(
            "``null`` if there are no more pages of results. "
            "See :ref:`json_api_pagination`."
        ),
    )
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        return context.next_url


class ColumnsExtra(Extra):
    description = "List of column names returned by this table, row or query."
    example = ExtraExample("/fixtures/facetable.json?_extra=columns")
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/simple_primary_key/1.json?_extra=columns"
        ),
        ExtraScope.QUERY: ExtraExample(
            "/fixtures/-/query.json?sql=select+1+as+one&_extra=columns"
        ),
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW, ExtraScope.QUERY}

    async def resolve(self, context):
        return context.columns


class AllColumnsExtra(Extra):
    description = "List of all column names in the table, regardless of ``_col=`` or ``_nocol=`` filtering."
    example = ExtraExample("/fixtures/facetable.json?_col=pk&_extra=all_columns")
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        return list(context.table_columns)


class PrimaryKeysExtra(Extra):
    description = "List of primary key column names for this table, or an empty list if the table has no explicit primary key."
    example = ExtraExample("/fixtures/facetable.json?_extra=primary_keys")
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/simple_primary_key/1.json?_extra=primary_keys"
        )
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW}

    async def resolve(self, context):
        return context.pks


def column_detail_as_json(column):
    return {
        "type": column.type,
        "sqlite_type": SQLiteType.from_declared_type(column.type).value,
        "notnull": bool(column.notnull),
        "default": column.default_value,
        "is_pk": bool(column.is_pk),
        "pk_position": column.is_pk,
        "hidden": column.hidden,
    }


class ColumnDetailsExtra(Extra):
    description = (
        "SQLite schema details for columns in this table. The dictionary maps "
        "column names to objects describing the schema for each column."
    )
    docs_note = (
        "Each object has ``type`` as the declared type string returned by "
        'SQLite, or ``""`` if no type was declared; ``sqlite_type`` as the '
        "normalized SQLite affinity, one of ``TEXT``, ``INTEGER``, ``REAL``, "
        "``BLOB`` or ``NUMERIC``; ``notnull`` as a boolean; ``default`` "
        'as the raw SQL default expression string, such as ``"42"``, '
        "``\"'hello'\"`` or ``\"datetime('now')\"``, or ``null`` if there is "
        "no default; ``is_pk`` as a boolean; ``pk_position`` as the integer "
        "primary key position reported by SQLite, or ``0`` for columns that "
        "are not part of the primary key; and ``hidden`` as the integer value "
        "reported by SQLite's ``PRAGMA table_xinfo``. ``hidden`` is ``0`` for "
        "normal columns, ``1`` for hidden virtual table columns, ``2`` for "
        "virtual generated columns and ``3`` for stored generated columns."
    )
    example = ExtraExample("/fixtures/binary_data.json?_size=0&_extra=column_details")
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/binary_data/1.json?_extra=column_details"
        )
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW}

    async def resolve(self, context):
        column_details = await context.datasette._get_resource_column_details(
            context.database_name, context.table_name
        )
        return {
            column_name: column_detail_as_json(column)
            for column_name, column in column_details.items()
        }


class ActionsExtra(Extra):
    description = 'Async callable returning table or view actions made available by core and plugin hooks. Each item is either a link with ``href``, ``label`` and optional ``description`` keys, or a button with ``type: "button"``, ``label``, optional ``description`` and optional ``attrs``. See :ref:`plugin_actions`, :ref:`plugin_hook_table_actions` and :ref:`plugin_hook_view_actions`.'
    scopes = {ExtraScope.TABLE}
    # Returns an async function for the HTML templates - not JSON serializable
    public = False

    async def resolve(self, context):
        async def actions():
            links = []
            kwargs = {
                "datasette": context.datasette,
                "database": context.database_name,
                "actor": context.request.actor,
                "request": context.request,
            }
            if context.is_view:
                kwargs["view"] = context.table_name
                method = pm.hook.view_actions
            else:
                kwargs["table"] = context.table_name
                method = pm.hook.table_actions
                # Resolve the registered table-level actions for this table
                # and the database-level actions for its database in two
                # batched queries, seeding the request permission cache so
                # that allowed() calls made inside the plugin hooks below
                # are served from the cache
                datasette = context.datasette
                await precompute_table_action_permissions(
                    datasette,
                    context.request.actor,
                    context.database_name,
                    context.table_name,
                )
                await precompute_database_action_permissions(
                    datasette, context.request.actor, context.database_name
                )
            for hook in method(**kwargs):
                extra_links = await await_me_maybe(hook)
                if extra_links:
                    links.extend(extra_links)
            return links

        return actions


async def precompute_table_action_permissions(
    datasette, actor, database_name, table_name
):
    await datasette.allowed_many(
        actions=[
            name
            for name, action in datasette.actions.items()
            if action.resource_class is TableResource
        ],
        resource=TableResource(database_name, table_name),
        actor=actor,
    )


async def precompute_database_action_permissions(datasette, actor, database_name):
    await datasette.allowed_many(
        actions=[
            name
            for name, action in datasette.actions.items()
            if action.resource_class is DatabaseResource
        ],
        resource=DatabaseResource(database_name),
        actor=actor,
    )


class IsViewExtra(Extra):
    description = "Whether this resource is a view instead of a table"
    example = ExtraExample("/fixtures/simple_view.json?_extra=is_view")
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        return context.is_view


class DebugExtra(Extra):
    description = "Extra debug information dictionary. This is intended for development only and its shape is not part of the stable template contract."
    docs_note = (
        "The contents of this block are not a stable part of the Datasette "
        "API and may change without warning."
    )
    example = ExtraExample("/fixtures/facetable.json?_extra=debug")
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/simple_primary_key/1.json?_extra=debug"
        ),
        ExtraScope.QUERY: ExtraExample(
            "/fixtures/-/query.json?sql=select+1+as+one&_extra=debug"
        ),
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW, ExtraScope.QUERY}

    async def resolve(self, context):
        debug = {
            "url_vars": context.request.url_vars,
        }
        if context.scope == ExtraScope.TABLE:
            debug["resolved"] = repr(context.resolved)
            debug["nofacet"] = context.nofacet
            debug["nosuggest"] = context.nosuggest
        elif context.scope == ExtraScope.ROW:
            debug["resolved"] = {
                "table": context.table_name,
                "sql": context.sql,
                "params": context.params,
                "pks": context.pks,
                "pk_values": context.pk_values,
            }
        return debug


class RequestExtra(Extra):
    description = "Dictionary with request details: ``url``, ``path``, ``full_path``, ``host`` and ``args`` where ``args`` maps query string parameter names to their values."
    example = ExtraExample("/fixtures/facetable.json?_extra=request")
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/simple_primary_key/1.json?_extra=request"
        ),
        ExtraScope.QUERY: ExtraExample(
            "/fixtures/-/query.json?sql=select+1+as+one&_extra=request"
        ),
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW, ExtraScope.QUERY}

    async def resolve(self, context):
        return {
            "url": context.request.url,
            "path": context.request.path,
            "full_path": context.request.full_path,
            "host": context.request.host,
            "args": context.request.args._data,
        }


class DisplayColumnsAndRowsProvider(Provider):
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        display_columns, display_rows = await context.display_columns_and_rows(
            context.datasette,
            context.database_name,
            context.table_name,
            context.results_description,
            context.rows,
            link_column=not context.is_view,
            truncate_cells=context.datasette.setting("truncate_cells_html"),
            sortable_columns=context.sortable_columns,
            request=context.request,
        )
        return {
            "columns": display_columns,
            "rows": display_rows,
        }


class DisplayColumnsExtra(Extra):
    description = "Column metadata used by the HTML table display. Each item includes ``name``, ``sortable``, ``is_pk``, ``type``, ``notnull``, ``description``, ``column_type`` and ``column_type_config`` keys."
    example = ExtraExample(
        value=[
            {
                "name": "pk",
                "sortable": True,
                "is_pk": True,
                "type": "INTEGER",
                "notnull": 0,
            },
            {
                "name": "created",
                "sortable": True,
                "is_pk": False,
                "type": "TEXT",
                "notnull": 0,
                "description": None,
                "column_type": None,
                "column_type_config": None,
            },
        ],
        note="Shape abbreviated from /fixtures/facetable.json?_size=1&_extra=display_columns.",
    )
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context, display_columns_and_rows):
        return display_columns_and_rows["columns"]


class DisplayRowsExtra(Extra):
    description = "Rows formatted for the HTML table display. Each row is iterable and contains cell dictionaries with ``column``, ``value``, ``raw`` and ``value_type`` keys; table pages may also provide ``pk_path``, ``row_path`` and ``row_label`` attributes on each row object."
    scopes = {ExtraScope.TABLE}
    # Contains markupsafe/sqlite3.Row values - not JSON serializable
    public = False

    async def resolve(self, context, display_columns_and_rows):
        return display_columns_and_rows["rows"]


class RenderCellExtra(Extra):
    description = "Rendered HTML for each cell using the render_cell plugin hook"
    docs_note = (
        "See the :ref:`render_cell() plugin hook <plugin_hook_render_cell>` "
        "documentation."
    )
    example = ExtraExample(
        value={
            "rows": [
                {"id": 1, "content": "hello"},
                {"id": 4, "content": "RENDER_CELL_DEMO"},
            ],
            "render_cell": [
                {},
                {"content": "<strong>Custom rendered HTML</strong>"},
            ],
        },
        note=(
            "The ``render_cell`` array has one item per row, in the same order as "
            "the ``rows`` array. Each object is keyed by column name. Only columns "
            "whose rendered value differs from the default are included."
        ),
    )
    examples = {
        ExtraScope.ROW: ExtraExample(
            value={
                "rows": [{"id": 4, "content": "RENDER_CELL_DEMO"}],
                "render_cell": [{"content": "<strong>Custom rendered HTML</strong>"}],
            },
            note=(
                "The ``render_cell`` array has one item for the requested row. "
                "The object is keyed by column name. Only columns whose rendered "
                "value differs from the default are included."
            ),
        ),
        ExtraScope.QUERY: ExtraExample(
            value={
                "rows": [{"content": "RENDER_CELL_DEMO"}],
                "render_cell": [{"content": "<strong>Custom rendered HTML</strong>"}],
            },
            note=(
                "The ``render_cell`` array has one item per query result row, in "
                "the same order as the ``rows`` array. Each object is keyed by "
                "column name. Only columns whose rendered value differs from the "
                "default are included."
            ),
        ),
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW, ExtraScope.QUERY}

    async def resolve(self, context):
        table_name = context.table_name
        pks_for_display = context.pks or (
            ["rowid"] if table_name and not context.is_view else []
        )
        ct_map = (
            await context.datasette.get_column_types(context.database_name, table_name)
            if table_name
            else {}
        )
        rendered_rows = []
        for row in context.rows:
            rendered_row = {}
            for value, column in zip(row, context.columns):
                ct = ct_map.get(column)
                plugin_display_value = None
                if ct:
                    candidate = await ct.render_cell(
                        value=value,
                        column=column,
                        table=table_name,
                        database=context.database_name,
                        datasette=context.datasette,
                        request=context.request,
                    )
                    if candidate is not None:
                        plugin_display_value = candidate
                if plugin_display_value is None:
                    for candidate in pm.hook.render_cell(
                        row=row,
                        value=value,
                        column=column,
                        table=table_name,
                        pks=pks_for_display,
                        database=context.database_name,
                        datasette=context.datasette,
                        request=context.request,
                        column_type=ct,
                    ):
                        candidate = await await_me_maybe(candidate)
                        if candidate is not None:
                            plugin_display_value = candidate
                            break
                if plugin_display_value:
                    rendered_row[column] = str(plugin_display_value)
            rendered_rows.append(rendered_row)
        return rendered_rows


class QueryExtra(Extra):
    description = "Details of the underlying SQL query as a dictionary with ``sql`` and ``params`` keys."
    example = ExtraExample("/fixtures/facetable.json?_size=1&_extra=query")
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/simple_primary_key/1.json?_extra=query"
        ),
        ExtraScope.QUERY: [
            ExtraExample("/fixtures/-/query.json?sql=select+1+as+one&_extra=query"),
            ExtraExample("/fixtures/neighborhood_search.json?text=town&_extra=query"),
        ],
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW, ExtraScope.QUERY}

    async def resolve(self, context):
        return {
            "sql": context.sql,
            "params": context.params,
        }


class ColumnTypesExtra(Extra):
    description = 'Column type assignments for this table. A dictionary mapping column names to ``{"type": type_name, "config": config}`` dictionaries.'
    docs_note = (
        "An empty object if no column types have been assigned. Column types "
        "can be assigned in :ref:`configuration "
        "<table_configuration_column_types>` or using the :ref:`set column "
        "type API <TableSetColumnTypeView>`."
    )
    example = ExtraExample(
        "/fixtures/facetable.json?_size=0&_extra=column_types",
        note=(
            "This example is from an instance where the ``tags`` column has "
            "been assigned the ``json`` column type."
        ),
    )
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/facetable/1.json?_extra=column_types",
            note=(
                "This example is from an instance where the ``tags`` column "
                "has been assigned the ``json`` column type."
            ),
        )
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW}

    async def resolve(self, context):
        ct_map = await context.datasette.get_column_types(
            context.database_name, context.table_name
        )
        return {
            col_name: {
                "type": ct.name,
                "config": ct.config,
            }
            for col_name, ct in ct_map.items()
        }


class SetColumnTypeUiExtra(Extra):
    description = "Information needed to build an interface for assigning column types, or ``None`` if unavailable. When present it has ``path`` and ``columns`` keys; ``columns`` maps column names to ``current`` and ``options`` values."
    docs_note = (
        "``null`` unless the current actor is allowed to use the :ref:`set "
        "column type API <TableSetColumnTypeView>` for this table."
    )
    example = ExtraExample(
        value={
            "path": "/fixtures/facetable/-/set-column-type",
            "columns": {
                "created": {
                    "current": None,
                    "options": [
                        {"name": "email", "description": "Email address"},
                        {"name": "json", "description": "JSON data"},
                        {"name": "url", "description": "URL"},
                    ],
                },
                "tags": {
                    "current": {"type": "json", "config": None},
                    "options": [
                        {"name": "email", "description": "Email address"},
                        {"name": "json", "description": "JSON data"},
                        {"name": "url", "description": "URL"},
                    ],
                },
            },
        },
        note=(
            "Shape abbreviated to two columns, as seen by an actor with "
            "``set-column-type`` permission. ``current`` is the column type "
            "currently assigned to each column and ``options`` lists the "
            "types that could be assigned to it."
        ),
    )
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        if context.is_view:
            return None

        if not await context.datasette.allowed(
            action="set-column-type",
            resource=TableResource(
                database=context.database_name, table=context.table_name
            ),
            actor=context.request.actor,
        ):
            return None

        column_details = await context.datasette._get_resource_column_details(
            context.database_name, context.table_name
        )
        ct_map = await context.datasette.get_column_types(
            context.database_name, context.table_name
        )
        columns = {}
        for column_name, column_detail in column_details.items():
            current = ct_map.get(column_name)
            columns[column_name] = {
                "current": (
                    {"type": current.name, "config": current.config}
                    if current is not None
                    else None
                ),
                "options": [
                    {
                        "name": name,
                        "description": ct_cls.description,
                    }
                    for name, ct_cls in sorted(context.datasette._column_types.items())
                    if context.datasette._column_type_is_applicable(
                        ct_cls, column_detail
                    )
                ],
            }
        return {
            "path": "{}/-/set-column-type".format(
                context.datasette.urls.table(context.database_name, context.table_name)
            ),
            "columns": columns,
        }


class MetadataExtra(Extra):
    description = "Metadata dictionary for the table, database or stored query. Table and row metadata include a ``columns`` dictionary mapping column names to descriptions; stored query metadata returns the stored query configuration."
    docs_note = "See :ref:`metadata` for how to attach metadata to tables."
    example = ExtraExample(
        "/fixtures/facetable.json?_extra=metadata",
        note=(
            "This example is from an instance where the ``facetable`` table "
            "has a metadata ``description`` and a :ref:`column description "
            "<metadata_column_descriptions>` for its ``state`` column. The "
            "``columns`` object is empty for tables with no column "
            "descriptions."
        ),
    )
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/simple_primary_key/1.json?_extra=metadata",
            note=(
                "This table has no metadata, so only an empty ``columns`` "
                "object is returned."
            ),
        ),
        ExtraScope.QUERY: ExtraExample(
            "/fixtures/neighborhood_search.json?text=town&_extra=metadata",
            note=(
                "For stored queries this returns the full configuration of "
                "the query, including the :ref:`stored query options "
                "<queries_options>`. For ``?sql=`` queries it returns an "
                "empty object."
            ),
        ),
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW, ExtraScope.QUERY}

    async def resolve(self, context):
        if context.scope == ExtraScope.QUERY:
            return context.metadata

        tablemetadata = await context.datasette.get_resource_metadata(
            context.database_name, context.table_name
        )

        rows = await context.datasette.get_internal_database().execute(
            """
              SELECT
                column_name,
                value
              FROM metadata_columns
              WHERE database_name = ?
                AND resource_name = ?
                AND key = 'description'
            """,
            [context.database_name, context.table_name],
        )
        tablemetadata["columns"] = dict(rows)
        return tablemetadata


class DatabaseExtra(Extra):
    description = "Database name"
    example = ExtraExample("/fixtures/facetable.json?_extra=database")
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/simple_primary_key/1.json?_extra=database"
        ),
        ExtraScope.QUERY: ExtraExample(
            "/fixtures/-/query.json?sql=select+1+as+one&_extra=database"
        ),
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW, ExtraScope.QUERY}

    async def resolve(self, context):
        return context.database_name


class TableExtra(Extra):
    description = "Table name"
    example = ExtraExample("/fixtures/facetable.json?_extra=table")
    examples = {
        ExtraScope.ROW: ExtraExample("/fixtures/simple_primary_key/1.json?_extra=table")
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW}

    async def resolve(self, context):
        return context.table_name


class DatabaseColorExtra(Extra):
    description = "Color assigned to the database"
    docs_note = (
        "A six character hex color, without the leading ``#``, derived from "
        "a hash of the database name and used in the Datasette interface."
    )
    example = ExtraExample("/fixtures/facetable.json?_extra=database_color")
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/simple_primary_key/1.json?_extra=database_color"
        ),
        ExtraScope.QUERY: ExtraExample(
            "/fixtures/-/query.json?sql=select+1+as+one&_extra=database_color"
        ),
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW, ExtraScope.QUERY}

    async def resolve(self, context):
        return context.db.color


class FormHiddenArgsExtra(Extra):
    description = "List of ``(name, value)`` pairs for hidden form fields used by the HTML table interface to preserve current query string options."
    example = ExtraExample(
        "/fixtures/facetable.json?_facet=state&_size=1&_extra=form_hidden_args"
    )
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        form_hidden_args = []
        for key in context.request.args:
            if (
                key.startswith("_")
                and key not in ("_sort", "_sort_desc", "_search", "_next")
                and "__" not in key
            ):
                for value in context.request.args.getlist(key):
                    form_hidden_args.append((key, value))
        return form_hidden_args


class FiltersExtra(Extra):
    description = "``Filters`` object used by the HTML table interface. Useful methods include ``filters.human_description_en()``; this is not JSON serializable."
    scopes = {ExtraScope.TABLE}
    # Returns a Filters instance for the HTML templates - not JSON serializable
    public = False

    async def resolve(self, context):
        return context.filters


class CustomTableTemplatesExtra(Extra):
    description = "List of custom template names considered for rendering table rows, in lookup order."
    docs_note = (
        "The first template in this list that exists will be used to render "
        "the table on the HTML version of this page. See "
        ":ref:`customization_custom_templates`."
    )
    example = ExtraExample("/fixtures/facetable.json?_extra=custom_table_templates")
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        return [
            f"_table-{to_css_class(context.database_name)}-{to_css_class(context.table_name)}.html",
            f"_table-table-{to_css_class(context.database_name)}-{to_css_class(context.table_name)}.html",
            "_table.html",
        ]


class SortedFacetResultsExtra(Extra):
    description = "Facet result dictionaries sorted for display. Each item has the same shape as an entry from ``facet_results['results']``."
    docs_note = (
        "The same data as ``facet_results``, as a list in the order used by "
        "the HTML interface: facets from :ref:`facet configuration "
        "<facets_metadata>` first, then other facets ordered by their number "
        "of results."
    )
    example = ExtraExample(
        "/fixtures/facetable.json?_facet=state&_extra=sorted_facet_results"
    )
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context, facet_results):
        facet_configs = context.table_metadata.get("facets", [])
        if facet_configs:
            metadata_facet_names = []
            for fc in facet_configs:
                if isinstance(fc, str):
                    metadata_facet_names.append(fc)
                elif isinstance(fc, dict):
                    metadata_facet_names.append(list(fc.values())[0])
            metadata_order = {name: i for i, name in enumerate(metadata_facet_names)}
            metadata_facets = []
            request_facets = []
            for f in facet_results["results"].values():
                if f["name"] in metadata_order:
                    metadata_facets.append(f)
                else:
                    request_facets.append(f)
            metadata_facets.sort(key=lambda f: metadata_order[f["name"]])
            request_facets.sort(
                key=lambda f: (len(f["results"]), f["name"]),
                reverse=True,
            )
            return metadata_facets + request_facets
        else:
            return sorted(
                facet_results["results"].values(),
                key=lambda f: (len(f["results"]), f["name"]),
                reverse=True,
            )


class TableDefinitionExtra(Extra):
    description = "SQL definition for this table"
    example = ExtraExample("/fixtures/facetable.json?_extra=table_definition")
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        return await context.db.get_table_definition(context.table_name)


class ViewDefinitionExtra(Extra):
    description = "SQL definition for this view"
    example = ExtraExample("/fixtures/simple_view.json?_extra=view_definition")
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        return await context.db.get_view_definition(context.table_name)


class RenderersExtra(Extra):
    description = "Dictionary mapping output format names such as ``json`` or plugin-provided renderer names to URLs for this data in that format."
    example = ExtraExample(
        "/fixtures/facetable.json?_extra=renderers",
        note=(
            "Each key is the name of an output format, each value the URL "
            "for this data in that format. Plugins can add additional "
            "formats using the :ref:`register_output_renderer() plugin hook "
            "<plugin_register_output_renderer>`."
        ),
    )
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context, expandable_columns, query):
        renderers = {}
        url_labels_extra = {}
        if expandable_columns:
            url_labels_extra = {"_labels": "on"}
        table_name = context.table_name
        view_name = "table" if context.scope == ExtraScope.TABLE else "database"
        for key, (_, can_render) in context.datasette.renderers.items():
            it_can_render = call_with_supported_arguments(
                can_render,
                datasette=context.datasette,
                columns=context.columns or [],
                rows=context.rows or [],
                sql=query.get("sql", None),
                query_name=context.query_name,
                database=context.database_name,
                table=table_name,
                request=context.request,
                view_name=view_name,
            )
            it_can_render = await await_me_maybe(it_can_render)
            if it_can_render:
                renderers[key] = context.datasette.urls.path(
                    path_with_format(
                        request=context.request,
                        path=context.request.scope.get("route_path"),
                        format=key,
                        extra_qs={**url_labels_extra},
                    )
                )
        return renderers


class PrivateExtra(Extra):
    description = "Whether this resource is private to the current actor"
    docs_note = (
        "``true`` if the current actor can see this resource but an "
        "anonymous user could not. See :ref:`authentication_permissions`."
    )
    example = ExtraExample("/fixtures/facetable.json?_extra=private")
    examples = {
        ExtraScope.ROW: ExtraExample(
            "/fixtures/simple_primary_key/1.json?_extra=private"
        ),
        ExtraScope.QUERY: ExtraExample(
            "/fixtures/-/query.json?sql=select+1+as+one&_extra=private"
        ),
    }
    scopes = {ExtraScope.TABLE, ExtraScope.ROW, ExtraScope.QUERY}

    async def resolve(self, context):
        return context.private


class ExpandableColumnsExtra(Extra):
    description = "List of foreign key columns that can be expanded with labels. Each item is a ``(foreign_key, label_column)`` pair where ``foreign_key`` is the SQLite foreign key dictionary and ``label_column`` is the label column in the referenced table, or ``None``."
    docs_note = "See :ref:`expand_foreign_keys` for how to expand these labels."
    example = ExtraExample(
        "/fixtures/facetable.json?_extra=expandable_columns",
        note=(
            "Each item is a ``[foreign_key, label_column]`` pair: the "
            "foreign key relationship, then the column in the other table "
            "that would be used as the label for each expanded value."
        ),
    )
    scopes = {ExtraScope.TABLE}

    async def resolve(self, context):
        expandables = []
        db = context.datasette.databases[context.database_name]
        for fk in await db.foreign_keys_for_table(context.table_name):
            label_column = await db.label_column_for_table(fk["other_table"])
            expandables.append((fk, label_column))
        return expandables


class ForeignKeyTablesExtra(Extra):
    description = "List of tables that link to this row using foreign keys. Each item includes the foreign key fields plus ``count`` for matching rows and ``link`` for the filtered table URL."
    example = ExtraExample(
        "/fixtures/simple_primary_key/1.json?_extra=foreign_key_tables",
        note=(
            "``count`` is the number of rows in the other table that "
            "reference this row, and ``link`` is a URL to browse those rows."
        ),
    )
    scopes = {ExtraScope.ROW}
    expensive = True

    async def resolve(self, context):
        return await context.foreign_key_tables(
            context.database_name, context.table_name, context.pk_values
        )


class ExtrasExtra(Extra):
    description = "List of ``?_extra=`` blocks that can be used on this page. Each item has ``name``, ``description``, ``toggle_url`` and ``selected`` keys."
    example = ExtraExample(
        value=[
            {
                "name": "count",
                "description": "Total count of rows matching these filters",
                "toggle_url": "http://localhost/fixtures/facetable.json?_extra=extras&_extra=count",
                "selected": False,
            },
            {
                "name": "extras",
                "description": "List of ?_extra= blocks that can be used on this page",
                "toggle_url": "http://localhost/fixtures/facetable.json",
                "selected": True,
            },
        ],
        note=(
            "Shape abbreviated from /fixtures/facetable.json?_extra=extras - "
            "the full response lists every extra described on this page. "
            "``toggle_url`` is the current URL with that extra added or "
            "removed, and ``selected`` is ``true`` for extras included in "
            "the current request."
        ),
    )
    scopes = {ExtraScope.TABLE, ExtraScope.ROW, ExtraScope.QUERY}

    async def resolve(self, context):
        all_extras = [
            (cls.key(), cls.description)
            for cls in context.extra_registry.public_classes_for_scope(context.scope)
        ]
        return [
            {
                "name": name,
                "description": description,
                "toggle_url": context.datasette.absolute_url(
                    context.request,
                    context.datasette.urls.path(
                        path_with_added_args(context.request, {"_extra": name})
                        if name not in context.extras
                        else path_with_removed_args(context.request, {"_extra": name})
                    ),
                ),
                "selected": name in context.extras,
            }
            for name, description in all_extras
        ]


TABLE_EXTRA_BUNDLES = {
    "html": [
        "suggested_facets",
        "facet_results",
        "facets_timed_out",
        "count",
        "count_sql",
        "human_description_en",
        "next_url",
        "metadata",
        "query",
        "columns",
        "display_columns",
        "display_rows",
        "database",
        "table",
        "database_color",
        "actions",
        "filters",
        "renderers",
        "custom_table_templates",
        "sorted_facet_results",
        "table_definition",
        "view_definition",
        "is_view",
        "private",
        "primary_keys",
        "all_columns",
        "expandable_columns",
        "form_hidden_args",
        "set_column_type_ui",
    ]
}


TABLE_EXTRA_CLASSES = [
    CountExtra,
    CountSqlExtra,
    FacetResultsExtra,
    FacetsTimedOutExtra,
    SuggestedFacetsExtra,
    FacetInstancesProvider,
    HumanDescriptionEnExtra,
    NextUrlExtra,
    ColumnsExtra,
    AllColumnsExtra,
    PrimaryKeysExtra,
    ColumnDetailsExtra,
    DisplayColumnsAndRowsProvider,
    DisplayColumnsExtra,
    DisplayRowsExtra,
    RenderCellExtra,
    DebugExtra,
    RequestExtra,
    QueryExtra,
    ColumnTypesExtra,
    SetColumnTypeUiExtra,
    MetadataExtra,
    ExtrasExtra,
    DatabaseExtra,
    TableExtra,
    DatabaseColorExtra,
    ActionsExtra,
    FiltersExtra,
    RenderersExtra,
    CustomTableTemplatesExtra,
    SortedFacetResultsExtra,
    TableDefinitionExtra,
    ViewDefinitionExtra,
    IsViewExtra,
    PrivateExtra,
    ExpandableColumnsExtra,
    ForeignKeyTablesExtra,
    FormHiddenArgsExtra,
]


table_extra_registry = ExtraRegistry(TABLE_EXTRA_CLASSES)


async def resolve_table_extras(extras, context, include_internal=False):
    return await table_extra_registry.resolve(
        extras, context, ExtraScope.TABLE, include_internal=include_internal
    )


async def resolve_row_extras(extras, context):
    return await table_extra_registry.resolve(extras, context, ExtraScope.ROW)


async def resolve_query_extras(extras, context):
    return await table_extra_registry.resolve(extras, context, ExtraScope.QUERY)
