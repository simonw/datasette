import urllib
import itertools

import jinja2
from sanic.exceptions import NotFound
from sanic.request import RequestParameters

from datasette.facets import load_facet_configs
from datasette.plugins import pm
from datasette.utils import (
    CustomRow,
    InterruptedError,
    append_querystring,
    compound_keys_after_sql,
    detect_fts,
    detect_primary_keys,
    escape_sqlite,
    filters_should_redirect,
    get_all_foreign_keys,
    is_url,
    path_from_row_pks,
    path_with_added_args,
    path_with_removed_args,
    path_with_replaced_args,
    sqlite3,
    table_columns,
    to_css_class,
    urlsafe_components,
    value_as_boolean,
)
from datasette.filters import Filters
from .base import BaseView, DatasetteError, ureg

LINK_WITH_LABEL = (
    '<a href="/{database}/{table}/{link_id}">{label}</a>&nbsp;<em>{id}</em>'
)
LINK_WITH_VALUE = '<a href="/{database}/{table}/{link_id}">{id}</a>'


class RowTableShared(BaseView):
    async def sortable_columns_for_table(self, database, table, use_rowid):
        table_metadata = self.ds.table_metadata(database, table)
        if "sortable_columns" in table_metadata:
            sortable_columns = set(table_metadata["sortable_columns"])
        else:
            sortable_columns = set(await self.ds.table_columns(database, table))
        if use_rowid:
            sortable_columns.add("rowid")
        return sortable_columns

    async def expandable_columns(self, database, table):
        # Returns list of (fk_dict, label_column-or-None) pairs for that table
        expandables = []
        for fk in await self.ds.foreign_keys_for_table(database, table):
            label_column = await self.ds.label_column_for_table(
                database, fk["other_table"]
            )
            expandables.append((fk, label_column))
        return expandables

    async def display_columns_and_rows(
        self, database, table, description, rows, link_column=False, truncate_cells=0
    ):
        "Returns columns, rows for specified table - including fancy foreign key treatment"
        table_metadata = self.ds.table_metadata(database, table)
        sortable_columns = await self.sortable_columns_for_table(database, table, True)
        columns = [
            {"name": r[0], "sortable": r[0] in sortable_columns} for r in description
        ]
        pks = await self.ds.execute_against_connection_in_thread(
            database, lambda conn: detect_primary_keys(conn, table)
        )
        column_to_foreign_key_table = {
            fk["column"]: fk["other_table"]
            for fk in await self.ds.foreign_keys_for_table(database, table)
        }

        cell_rows = []
        for row in rows:
            cells = []
            # Unless we are a view, the first column is a link - either to the rowid
            # or to the simple or compound primary key
            if link_column:
                cells.append(
                    {
                        "column": pks[0] if len(pks) == 1 else "Link",
                        "value": jinja2.Markup(
                            '<a href="/{database}/{table}/{flat_pks_quoted}">{flat_pks}</a>'.format(
                                database=database,
                                table=urllib.parse.quote_plus(table),
                                flat_pks=str(
                                    jinja2.escape(
                                        path_from_row_pks(row, pks, not pks, False)
                                    )
                                ),
                                flat_pks_quoted=path_from_row_pks(row, pks, not pks),
                            )
                        ),
                    }
                )

            for value, column_dict in zip(row, columns):
                column = column_dict["name"]
                if link_column and len(pks) == 1 and column == pks[0]:
                    # If there's a simple primary key, don't repeat the value as it's
                    # already shown in the link column.
                    continue

                # First let the plugins have a go
                # pylint: disable=no-member
                plugin_display_value = pm.hook.render_cell(
                    value=value,
                    column=column,
                    table=table,
                    database=database,
                    datasette=self.ds,
                )
                if plugin_display_value is not None:
                    display_value = plugin_display_value
                elif isinstance(value, bytes):
                    display_value = jinja2.Markup(
                        "&lt;Binary&nbsp;data:&nbsp;{}&nbsp;byte{}&gt;".format(
                            len(value), "" if len(value) == 1 else "s"
                        )
                    )
                elif isinstance(value, dict):
                    # It's an expanded foreign key - display link to other row
                    label = value["label"]
                    value = value["value"]
                    # The table we link to depends on the column
                    other_table = column_to_foreign_key_table[column]
                    link_template = (
                        LINK_WITH_LABEL if (label != value) else LINK_WITH_VALUE
                    )
                    display_value = jinja2.Markup(
                        link_template.format(
                            database=database,
                            table=urllib.parse.quote_plus(other_table),
                            link_id=urllib.parse.quote_plus(str(value)),
                            id=str(jinja2.escape(value)),
                            label=str(jinja2.escape(label)),
                        )
                    )
                elif value in ("", None):
                    display_value = jinja2.Markup("&nbsp;")
                elif is_url(str(value).strip()):
                    display_value = jinja2.Markup(
                        '<a href="{url}">{url}</a>'.format(
                            url=jinja2.escape(value.strip())
                        )
                    )
                elif column in table_metadata.get("units", {}) and value != "":
                    # Interpret units using pint
                    value = value * ureg(table_metadata["units"][column])
                    # Pint uses floating point which sometimes introduces errors in the compact
                    # representation, which we have to round off to avoid ugliness. In the vast
                    # majority of cases this rounding will be inconsequential. I hope.
                    value = round(value.to_compact(), 6)
                    display_value = jinja2.Markup(
                        "{:~P}".format(value).replace(" ", "&nbsp;")
                    )
                else:
                    display_value = str(value)
                    if truncate_cells and len(display_value) > truncate_cells:
                        display_value = display_value[:truncate_cells] + u"\u2026"

                cells.append({"column": column, "value": display_value})
            cell_rows.append(cells)

        if link_column:
            # Add the link column header.
            # If it's a simple primary key, we have to remove and re-add that column name at
            # the beginning of the header row.
            if len(pks) == 1:
                columns = [col for col in columns if col["name"] != pks[0]]

            columns = [
                {"name": pks[0] if len(pks) == 1 else "Link", "sortable": len(pks) == 1}
            ] + columns
        return columns, cell_rows


class TableView(RowTableShared):
    name = "table"

    async def data(
        self,
        request,
        database,
        hash,
        table,
        default_labels=False,
        _next=None,
        _size=None,
    ):
        canned_query = self.ds.get_canned_query(database, table)
        if canned_query is not None:
            return await self.custom_sql(
                request,
                database,
                hash,
                canned_query["sql"],
                metadata=canned_query,
                editable=False,
                canned_query=table,
            )

        is_view = bool(await self.ds.get_view_definition(database, table))
        table_exists = bool(await self.ds.table_exists(database, table))
        if not is_view and not table_exists:
            raise NotFound("Table not found: {}".format(table))
        pks = await self.ds.execute_against_connection_in_thread(
            database, lambda conn: detect_primary_keys(conn, table)
        )
        use_rowid = not pks and not is_view
        if use_rowid:
            select = "rowid, *"
            order_by = "rowid"
            order_by_pks = "rowid"
        else:
            select = "*"
            order_by_pks = ", ".join([escape_sqlite(pk) for pk in pks])
            order_by = order_by_pks

        if is_view:
            order_by = ""

        # We roll our own query_string decoder because by default Sanic
        # drops anything with an empty value e.g. ?name__exact=
        args = RequestParameters(
            urllib.parse.parse_qs(request.query_string, keep_blank_values=True)
        )

        # Special args start with _ and do not contain a __
        # That's so if there is a column that starts with _
        # it can still be queried using ?_col__exact=blah
        special_args = {}
        special_args_lists = {}
        other_args = []
        for key, value in args.items():
            if key.startswith("_") and "__" not in key:
                special_args[key] = value[0]
                special_args_lists[key] = value
            else:
                for v in value:
                    other_args.append((key, v))

        # Handle ?_filter_column and redirect, if present
        redirect_params = filters_should_redirect(special_args)
        if redirect_params:
            return self.redirect(
                request,
                path_with_added_args(request, redirect_params),
                forward_querystring=False,
            )

        # Spot ?_sort_by_desc and redirect to _sort_desc=(_sort)
        if "_sort_by_desc" in special_args:
            return self.redirect(
                request,
                path_with_added_args(
                    request,
                    {
                        "_sort_desc": special_args.get("_sort"),
                        "_sort_by_desc": None,
                        "_sort": None,
                    },
                ),
                forward_querystring=False,
            )

        table_metadata = self.ds.table_metadata(database, table)
        units = table_metadata.get("units", {})
        filters = Filters(sorted(other_args), units, ureg)
        where_clauses, params = filters.build_where_clauses(table)

        extra_wheres_for_ui = []
        # Add _where= from querystring
        if "_where" in request.args:
            if not self.ds.config("allow_sql"):
                raise DatasetteError("_where= is not allowed", status=400)
            else:
                where_clauses.extend(request.args["_where"])
                extra_wheres_for_ui = [
                    {
                        "text": text,
                        "remove_url": path_with_removed_args(request, {"_where": text}),
                    }
                    for text in request.args["_where"]
                ]

        # _search support:
        fts_table = special_args.get("_fts_table")
        fts_table = fts_table or table_metadata.get("fts_table")
        fts_table = fts_table or await self.ds.execute_against_connection_in_thread(
            database, lambda conn: detect_fts(conn, table)
        )
        fts_pk = special_args.get("_fts_pk", table_metadata.get("fts_pk", "rowid"))
        search_args = dict(
            pair for pair in special_args.items() if pair[0].startswith("_search")
        )
        search_descriptions = []
        search = ""
        if fts_table and search_args:
            if "_search" in search_args:
                # Simple ?_search=xxx
                search = search_args["_search"]
                where_clauses.append(
                    "{fts_pk} in (select rowid from {fts_table} where {fts_table} match :search)".format(
                        fts_table=escape_sqlite(fts_table), fts_pk=escape_sqlite(fts_pk)
                    )
                )
                search_descriptions.append('search matches "{}"'.format(search))
                params["search"] = search
            else:
                # More complex: search against specific columns
                for i, (key, search_text) in enumerate(search_args.items()):
                    search_col = key.split("_search_", 1)[1]
                    if search_col not in await self.ds.table_columns(
                        database, fts_table
                    ):
                        raise DatasetteError("Cannot search by that column", status=400)

                    where_clauses.append(
                        "rowid in (select rowid from {fts_table} where {search_col} match :search_{i})".format(
                            fts_table=escape_sqlite(fts_table),
                            search_col=escape_sqlite(search_col),
                            i=i,
                        )
                    )
                    search_descriptions.append(
                        'search column "{}" matches "{}"'.format(
                            search_col, search_text
                        )
                    )
                    params["search_{}".format(i)] = search_text

        sortable_columns = set()

        sortable_columns = await self.sortable_columns_for_table(
            database, table, use_rowid
        )

        # Allow for custom sort order
        sort = special_args.get("_sort")
        if sort:
            if sort not in sortable_columns:
                raise DatasetteError("Cannot sort table by {}".format(sort))

            order_by = escape_sqlite(sort)
        sort_desc = special_args.get("_sort_desc")
        if sort_desc:
            if sort_desc not in sortable_columns:
                raise DatasetteError("Cannot sort table by {}".format(sort_desc))

            if sort:
                raise DatasetteError("Cannot use _sort and _sort_desc at the same time")

            order_by = "{} desc".format(escape_sqlite(sort_desc))

        from_sql = "from {table_name} {where}".format(
            table_name=escape_sqlite(table),
            where=("where {} ".format(" and ".join(where_clauses)))
            if where_clauses
            else "",
        )
        # Copy of params so we can mutate them later:
        from_sql_params = dict(**params)

        count_sql = "select count(*) {}".format(from_sql)

        _next = _next or special_args.get("_next")
        offset = ""
        if _next:
            if is_view:
                # _next is an offset
                offset = " offset {}".format(int(_next))
            else:
                components = urlsafe_components(_next)
                # If a sort order is applied, the first of these is the sort value
                if sort or sort_desc:
                    sort_value = components[0]
                    # Special case for if non-urlencoded first token was $null
                    if _next.split(",")[0] == "$null":
                        sort_value = None
                    components = components[1:]

                # Figure out the SQL for next-based-on-primary-key first
                next_by_pk_clauses = []
                if use_rowid:
                    next_by_pk_clauses.append("rowid > :p{}".format(len(params)))
                    params["p{}".format(len(params))] = components[0]
                else:
                    # Apply the tie-breaker based on primary keys
                    if len(components) == len(pks):
                        param_len = len(params)
                        next_by_pk_clauses.append(
                            compound_keys_after_sql(pks, param_len)
                        )
                        for i, pk_value in enumerate(components):
                            params["p{}".format(param_len + i)] = pk_value

                # Now add the sort SQL, which may incorporate next_by_pk_clauses
                if sort or sort_desc:
                    if sort_value is None:
                        if sort_desc:
                            # Just items where column is null ordered by pk
                            where_clauses.append(
                                "({column} is null and {next_clauses})".format(
                                    column=escape_sqlite(sort_desc),
                                    next_clauses=" and ".join(next_by_pk_clauses),
                                )
                            )
                        else:
                            where_clauses.append(
                                "({column} is not null or ({column} is null and {next_clauses}))".format(
                                    column=escape_sqlite(sort),
                                    next_clauses=" and ".join(next_by_pk_clauses),
                                )
                            )
                    else:
                        where_clauses.append(
                            "({column} {op} :p{p}{extra_desc_only} or ({column} = :p{p} and {next_clauses}))".format(
                                column=escape_sqlite(sort or sort_desc),
                                op=">" if sort else "<",
                                p=len(params),
                                extra_desc_only=""
                                if sort
                                else " or {column2} is null".format(
                                    column2=escape_sqlite(sort or sort_desc)
                                ),
                                next_clauses=" and ".join(next_by_pk_clauses),
                            )
                        )
                        params["p{}".format(len(params))] = sort_value
                    order_by = "{}, {}".format(order_by, order_by_pks)
                else:
                    where_clauses.extend(next_by_pk_clauses)

        where_clause = ""
        if where_clauses:
            where_clause = "where {} ".format(" and ".join(where_clauses))

        if order_by:
            order_by = "order by {} ".format(order_by)

        # _group_count=col1&_group_count=col2
        group_count = special_args_lists.get("_group_count") or []
        if group_count:
            sql = 'select {group_cols}, count(*) as "count" from {table_name} {where} group by {group_cols} order by "count" desc limit 100'.format(
                group_cols=", ".join(
                    '"{}"'.format(group_count_col) for group_count_col in group_count
                ),
                table_name=escape_sqlite(table),
                where=where_clause,
            )
            return await self.custom_sql(request, database, hash, sql, editable=True)

        extra_args = {}
        # Handle ?_size=500
        page_size = _size or request.raw_args.get("_size")
        if page_size:
            if page_size == "max":
                page_size = self.ds.max_returned_rows
            try:
                page_size = int(page_size)
                if page_size < 0:
                    raise ValueError

            except ValueError:
                raise DatasetteError("_size must be a positive integer", status=400)

            if page_size > self.ds.max_returned_rows:
                raise DatasetteError(
                    "_size must be <= {}".format(self.ds.max_returned_rows), status=400
                )

            extra_args["page_size"] = page_size
        else:
            page_size = self.ds.page_size

        sql_no_limit = "select {select} from {table_name} {where}{order_by}".format(
            select=select,
            table_name=escape_sqlite(table),
            where=where_clause,
            order_by=order_by,
        )
        sql = "{sql_no_limit} limit {limit}{offset}".format(
            sql_no_limit=sql_no_limit.rstrip(), limit=page_size + 1, offset=offset
        )

        if request.raw_args.get("_timelimit"):
            extra_args["custom_time_limit"] = int(request.raw_args["_timelimit"])

        results = await self.ds.execute(
            database, sql, params, truncate=True, **extra_args
        )

        # Number of filtered rows in whole set:
        filtered_table_rows_count = None
        if count_sql:
            try:
                count_rows = list(
                    await self.ds.execute(database, count_sql, from_sql_params)
                )
                filtered_table_rows_count = count_rows[0][0]
            except InterruptedError:
                pass

        # facets support
        if not self.ds.config("allow_facet") and any(
            arg.startswith("_facet") for arg in request.args
        ):
            raise DatasetteError("_facet= is not allowed", status=400)

        # pylint: disable=no-member
        facet_classes = list(
            itertools.chain.from_iterable(pm.hook.register_facet_classes())
        )
        facet_results = {}
        facets_timed_out = []
        facet_instances = []
        for klass in facet_classes:
            facet_instances.append(
                klass(
                    self.ds,
                    request,
                    database,
                    sql=sql_no_limit,
                    params=params,
                    table=table,
                    metadata=table_metadata,
                    row_count=filtered_table_rows_count,
                )
            )

        for facet in facet_instances:
            instance_facet_results, instance_facets_timed_out = (
                await facet.facet_results()
            )
            facet_results.update(instance_facet_results)
            facets_timed_out.extend(instance_facets_timed_out)

        # Figure out columns and rows for the query
        columns = [r[0] for r in results.description]
        rows = list(results.rows)

        filter_columns = columns[:]
        if use_rowid and filter_columns[0] == "rowid":
            filter_columns = filter_columns[1:]

        # Expand labeled columns if requested
        expanded_columns = []
        expandable_columns = await self.expandable_columns(database, table)
        columns_to_expand = None
        try:
            all_labels = value_as_boolean(special_args.get("_labels", ""))
        except ValueError:
            all_labels = default_labels
        # Check for explicit _label=
        if "_label" in request.args:
            columns_to_expand = request.args["_label"]
        if columns_to_expand is None and all_labels:
            # expand all columns with foreign keys
            columns_to_expand = [fk["column"] for fk, _ in expandable_columns]

        if columns_to_expand:
            expanded_labels = {}
            for fk, _ in expandable_columns:
                column = fk["column"]
                if column not in columns_to_expand:
                    continue
                expanded_columns.append(column)
                # Gather the values
                column_index = columns.index(column)
                values = [row[column_index] for row in rows]
                # Expand them
                expanded_labels.update(
                    await self.ds.expand_foreign_keys(database, table, column, values)
                )
            if expanded_labels:
                # Rewrite the rows
                new_rows = []
                for row in rows:
                    new_row = CustomRow(columns)
                    for column in row.keys():
                        value = row[column]
                        if (column, value) in expanded_labels:
                            new_row[column] = {
                                "value": value,
                                "label": expanded_labels[(column, value)],
                            }
                        else:
                            new_row[column] = value
                    new_rows.append(new_row)
                rows = new_rows

        # Pagination next link
        next_value = None
        next_url = None
        if len(rows) > page_size and page_size > 0:
            if is_view:
                next_value = int(_next or 0) + page_size
            else:
                next_value = path_from_row_pks(rows[-2], pks, use_rowid)
            # If there's a sort or sort_desc, add that value as a prefix
            if (sort or sort_desc) and not is_view:
                prefix = rows[-2][sort or sort_desc]
                if prefix is None:
                    prefix = "$null"
                else:
                    prefix = urllib.parse.quote_plus(str(prefix))
                next_value = "{},{}".format(prefix, next_value)
                added_args = {"_next": next_value}
                if sort:
                    added_args["_sort"] = sort
                else:
                    added_args["_sort_desc"] = sort_desc
            else:
                added_args = {"_next": next_value}
            next_url = self.ds.absolute_url(
                request, path_with_replaced_args(request, added_args)
            )
            rows = rows[:page_size]

        # Detect suggested facets
        suggested_facets = []

        if (
            self.ds.config("suggest_facets")
            and self.ds.config("allow_facet")
            and not _next
        ):
            for facet in facet_instances:
                # TODO: ensure facet is not suggested if it is already active
                # used to use 'if facet_column in facets' for this
                suggested_facets.extend(await facet.suggest())

        # human_description_en combines filters AND search, if provided
        human_description_en = filters.human_description_en(extra=search_descriptions)

        if sort or sort_desc:
            sorted_by = "sorted by {}{}".format(
                (sort or sort_desc), " descending" if sort_desc else ""
            )
            human_description_en = " ".join(
                [b for b in [human_description_en, sorted_by] if b]
            )

        async def extra_template():
            display_columns, display_rows = await self.display_columns_and_rows(
                database,
                table,
                results.description,
                rows,
                link_column=not is_view,
                truncate_cells=self.ds.config("truncate_cells_html"),
            )
            metadata = (
                (self.ds.metadata("databases") or {})
                .get(database, {})
                .get("tables", {})
                .get(table, {})
            )
            self.ds.update_with_inherited_metadata(metadata)
            form_hidden_args = []
            for arg in ("_fts_table", "_fts_pk"):
                if arg in special_args:
                    form_hidden_args.append((arg, special_args[arg]))
            return {
                "supports_search": bool(fts_table),
                "search": search or "",
                "use_rowid": use_rowid,
                "filters": filters,
                "display_columns": display_columns,
                "filter_columns": filter_columns,
                "display_rows": display_rows,
                "facets_timed_out": facets_timed_out,
                "sorted_facet_results": sorted(
                    facet_results.values(),
                    key=lambda f: (len(f["results"]), f["name"]),
                    reverse=True,
                ),
                "extra_wheres_for_ui": extra_wheres_for_ui,
                "form_hidden_args": form_hidden_args,
                "is_sortable": any(c["sortable"] for c in display_columns),
                "path_with_replaced_args": path_with_replaced_args,
                "path_with_removed_args": path_with_removed_args,
                "append_querystring": append_querystring,
                "request": request,
                "sort": sort,
                "sort_desc": sort_desc,
                "disable_sort": is_view,
                "custom_rows_and_columns_templates": [
                    "_rows_and_columns-{}-{}.html".format(
                        to_css_class(database), to_css_class(table)
                    ),
                    "_rows_and_columns-table-{}-{}.html".format(
                        to_css_class(database), to_css_class(table)
                    ),
                    "_rows_and_columns.html",
                ],
                "metadata": metadata,
                "view_definition": await self.ds.get_view_definition(database, table),
                "table_definition": await self.ds.get_table_definition(database, table),
            }

        return (
            {
                "database": database,
                "table": table,
                "is_view": is_view,
                "human_description_en": human_description_en,
                "rows": rows[:page_size],
                "truncated": results.truncated,
                "filtered_table_rows_count": filtered_table_rows_count,
                "expanded_columns": expanded_columns,
                "expandable_columns": expandable_columns,
                "columns": columns,
                "primary_keys": pks,
                "units": units,
                "query": {"sql": sql, "params": params},
                "facet_results": facet_results,
                "suggested_facets": suggested_facets,
                "next": next_value and str(next_value) or None,
                "next_url": next_url,
            },
            extra_template,
            (
                "table-{}-{}.html".format(to_css_class(database), to_css_class(table)),
                "table.html",
            ),
        )


class RowView(RowTableShared):
    name = "row"

    async def data(self, request, database, hash, table, pk_path, default_labels=False):
        pk_values = urlsafe_components(pk_path)
        pks = await self.ds.execute_against_connection_in_thread(
            database, lambda conn: detect_primary_keys(conn, table)
        )
        use_rowid = not pks
        select = "*"
        if use_rowid:
            select = "rowid, *"
            pks = ["rowid"]
        wheres = ['"{}"=:p{}'.format(pk, i) for i, pk in enumerate(pks)]
        sql = "select {} from {} where {}".format(
            select, escape_sqlite(table), " AND ".join(wheres)
        )
        params = {}
        for i, pk_value in enumerate(pk_values):
            params["p{}".format(i)] = pk_value
        results = await self.ds.execute(database, sql, params, truncate=True)
        columns = [r[0] for r in results.description]
        rows = list(results.rows)
        if not rows:
            raise NotFound("Record not found: {}".format(pk_values))

        async def template_data():
            display_columns, display_rows = await self.display_columns_and_rows(
                database,
                table,
                results.description,
                rows,
                link_column=False,
                truncate_cells=0,
            )
            for column in display_columns:
                column["sortable"] = False
            return {
                "foreign_key_tables": await self.foreign_key_tables(
                    database, table, pk_values
                ),
                "display_columns": display_columns,
                "display_rows": display_rows,
                "custom_rows_and_columns_templates": [
                    "_rows_and_columns-{}-{}.html".format(
                        to_css_class(database), to_css_class(table)
                    ),
                    "_rows_and_columns-row-{}-{}.html".format(
                        to_css_class(database), to_css_class(table)
                    ),
                    "_rows_and_columns.html",
                ],
                "metadata": (self.ds.metadata("databases") or {})
                .get(database, {})
                .get("tables", {})
                .get(table, {}),
            }

        data = {
            "database": database,
            "table": table,
            "rows": rows,
            "columns": columns,
            "primary_keys": pks,
            "primary_key_values": pk_values,
            "units": self.ds.table_metadata(database, table).get("units", {}),
        }

        if "foreign_key_tables" in (request.raw_args.get("_extras") or "").split(","):
            data["foreign_key_tables"] = await self.foreign_key_tables(
                database, table, pk_values
            )

        return (
            data,
            template_data,
            (
                "row-{}-{}.html".format(to_css_class(database), to_css_class(table)),
                "row.html",
            ),
        )

    async def foreign_key_tables(self, database, table, pk_values):
        if len(pk_values) != 1:
            return []

        all_foreign_keys = await self.ds.execute_against_connection_in_thread(
            database, get_all_foreign_keys
        )
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
            rows = list(await self.ds.execute(database, sql, {"id": pk_values[0]}))
        except sqlite3.OperationalError:
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
            foreign_key_tables.append({**fk, **{"count": count}})
        return foreign_key_tables
