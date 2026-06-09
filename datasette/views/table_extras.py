import itertools
from dataclasses import dataclass

from datasette.database import QueryInterrupted
from datasette.extras import Extra, ExtraRegistry, ExtraScope, Provider
from datasette.plugins import pm
from datasette.resources import TableResource
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
    next_value: object
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


class CountSqlExtra(Extra):
    description = "SQL query used to calculate the total count"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return context.count_sql


class CountExtra(Extra):
    description = "Total count of rows matching these filters"
    scopes = frozenset({ExtraScope.TABLE})
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
            count_sql_limited = (
                f"select count(*) from (select * {context.from_sql} limit 10001)"
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
    scopes = frozenset({ExtraScope.TABLE})

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
    description = "Results of facets calculated against this data"
    scopes = frozenset({ExtraScope.TABLE})
    expensive = True

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
    description = "Facet calculations that timed out"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context, facet_results):
        return facet_results["timed_out"]


class SuggestedFacetsExtra(Extra):
    description = "Suggestions for facets that might return interesting results"
    scopes = frozenset({ExtraScope.TABLE})
    expensive = True

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
    scopes = frozenset({ExtraScope.TABLE})

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
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return context.next_url


class ColumnsExtra(Extra):
    description = "Column names returned by this query"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return context.columns


class AllColumnsExtra(Extra):
    description = "All columns in the table, regardless of _col/_nocol filtering"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return list(context.table_columns)


class PrimaryKeysExtra(Extra):
    description = "Primary keys for this table"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return context.pks


class ActionsExtra(Extra):
    description = "Table or view actions made available by plugin hooks"
    scopes = frozenset({ExtraScope.TABLE})

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
            for hook in method(**kwargs):
                extra_links = await await_me_maybe(hook)
                if extra_links:
                    links.extend(extra_links)
            return links

        return actions


class IsViewExtra(Extra):
    description = "Whether this resource is a view instead of a table"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return context.is_view


class DebugExtra(Extra):
    description = "Extra debug information"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return {
            "resolved": repr(context.resolved),
            "url_vars": context.request.url_vars,
            "nofacet": context.nofacet,
            "nosuggest": context.nosuggest,
        }


class RequestExtra(Extra):
    description = "Full information about the request"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return {
            "url": context.request.url,
            "path": context.request.path,
            "full_path": context.request.full_path,
            "host": context.request.host,
            "args": context.request.args._data,
        }


class DisplayColumnsAndRowsProvider(Provider):
    scopes = frozenset({ExtraScope.TABLE})

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
    description = "Column metadata used by the HTML table display"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context, display_columns_and_rows):
        return display_columns_and_rows["columns"]


class DisplayRowsExtra(Extra):
    description = "Row data formatted for the HTML table display"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context, display_columns_and_rows):
        return display_columns_and_rows["rows"]


class RenderCellExtra(Extra):
    description = "Rendered HTML for each cell using the render_cell plugin hook"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        pks_for_display = (
            context.pks if context.pks else (["rowid"] if not context.is_view else [])
        )
        col_names = [col[0] for col in context.results_description]
        ct_map = await context.datasette.get_column_types(
            context.database_name, context.table_name
        )
        rendered_rows = []
        for row in context.rows:
            rendered_row = {}
            for value, column in zip(row, col_names):
                ct = ct_map.get(column)
                plugin_display_value = None
                if ct:
                    candidate = await ct.render_cell(
                        value=value,
                        column=column,
                        table=context.table_name,
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
                        table=context.table_name,
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
    description = "Details of the underlying SQL query"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return {
            "sql": context.sql,
            "params": context.params,
        }


class ColumnTypesExtra(Extra):
    description = "Column type assignments for this table"
    scopes = frozenset({ExtraScope.TABLE})

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
    description = "Column type UI metadata for this table"
    scopes = frozenset({ExtraScope.TABLE})

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
    description = "Metadata about the table and database"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
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
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return context.database_name


class TableExtra(Extra):
    description = "Table name"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return context.table_name


class DatabaseColorExtra(Extra):
    description = "Color assigned to the database"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return context.db.color


class FormHiddenArgsExtra(Extra):
    description = "Hidden form arguments used by the HTML table interface"
    scopes = frozenset({ExtraScope.TABLE})

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
    description = "Filters object used by the HTML table interface"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return context.filters


class CustomTableTemplatesExtra(Extra):
    description = "Custom template names considered for this table"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return [
            f"_table-{to_css_class(context.database_name)}-{to_css_class(context.table_name)}.html",
            f"_table-table-{to_css_class(context.database_name)}-{to_css_class(context.table_name)}.html",
            "_table.html",
        ]


class SortedFacetResultsExtra(Extra):
    description = "Facet results sorted for display"
    scopes = frozenset({ExtraScope.TABLE})

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
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return await context.db.get_table_definition(context.table_name)


class ViewDefinitionExtra(Extra):
    description = "SQL definition for this view"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return await context.db.get_view_definition(context.table_name)


class RenderersExtra(Extra):
    description = "Alternative output renderers available for this table"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context, expandable_columns, query):
        renderers = {}
        url_labels_extra = {}
        if expandable_columns:
            url_labels_extra = {"_labels": "on"}
        for key, (_, can_render) in context.datasette.renderers.items():
            it_can_render = call_with_supported_arguments(
                can_render,
                datasette=context.datasette,
                columns=context.columns or [],
                rows=context.rows or [],
                sql=query.get("sql", None),
                query_name=None,
                database=context.database_name,
                table=context.table_name,
                request=context.request,
                view_name="table",
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
    description = "Whether this table is private to the current actor"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        return context.private


class ExpandableColumnsExtra(Extra):
    description = "Foreign key columns that can be expanded with labels"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        expandables = []
        db = context.datasette.databases[context.database_name]
        for fk in await db.foreign_keys_for_table(context.table_name):
            label_column = await db.label_column_for_table(fk["other_table"])
            expandables.append((fk, label_column))
        return expandables


class ExtrasExtra(Extra):
    description = "Available ?_extra= blocks"
    scopes = frozenset({ExtraScope.TABLE})

    async def resolve(self, context):
        all_extras = [
            (cls.key(), cls.description)
            for cls in context.extra_registry.public_classes_for_scope(ExtraScope.TABLE)
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
    FormHiddenArgsExtra,
]


table_extra_registry = ExtraRegistry(TABLE_EXTRA_CLASSES)


async def resolve_table_extras(extras, context):
    return await table_extra_registry.resolve(extras, context, ExtraScope.TABLE)
