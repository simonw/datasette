import sqlite3
import urllib

import jinja2
from sanic.exceptions import NotFound
from sanic.request import RequestParameters

from datasette.utils import (
    Filters,
    InterruptedError,
    compound_keys_after_sql,
    escape_sqlite,
    filters_should_redirect,
    is_url,
    path_from_row_pks,
    path_with_added_args,
    path_with_removed_args,
    path_with_replaced_args,
    to_css_class,
    urlsafe_components,
)

from .base import BaseView, DatasetteError, ureg


class RowTableShared(BaseView):

    def sortable_columns_for_table(self, name, table, use_rowid):
        table_metadata = self.table_metadata(name, table)
        if "sortable_columns" in table_metadata:
            sortable_columns = set(table_metadata["sortable_columns"])
        else:
            table_info = self.ds.inspect()[name]["tables"].get(table) or {}
            sortable_columns = set(table_info.get("columns", []))
        if use_rowid:
            sortable_columns.add("rowid")
        return sortable_columns

    async def expand_foreign_keys(self, database, table, column, values):
        "Returns dict mapping (column, value) -> label"
        labeled_fks = {}
        tables_info = self.ds.inspect()[database]["tables"]
        table_info = tables_info.get(table) or {}
        if not table_info:
            return {}
        foreign_keys = table_info["foreign_keys"]["outgoing"]
        # Find the foreign_key for this column
        try:
            fk = [
                foreign_key for foreign_key in foreign_keys
                if foreign_key["column"] == column
            ][0]
        except IndexError:
            return {}
        label_column = (
            # First look in metadata.json for this foreign key table:
            self.table_metadata(
                database, fk["other_table"]
            ).get("label_column")
            or tables_info.get(fk["other_table"], {}).get("label_column")
        )
        if not label_column:
            return {}
        labeled_fks = {}
        sql = '''
            select {other_column}, {label_column}
            from {other_table}
            where {other_column} in ({placeholders})
        '''.format(
            other_column=escape_sqlite(fk["other_column"]),
            label_column=escape_sqlite(label_column),
            other_table=escape_sqlite(fk["other_table"]),
            placeholders=", ".join(["?"] * len(set(values))),
        )
        try:
            results = await self.execute(
                database, sql, list(set(values))
            )
        except InterruptedError:
            pass
        else:
            for id, value in results:
                labeled_fks[(fk["column"], id)] = value
        return labeled_fks

    async def display_columns_and_rows(
        self,
        database,
        table,
        description,
        rows,
        link_column=False,
        expand_foreign_keys=True,
    ):
        "Returns columns, rows for specified table - including fancy foreign key treatment"
        table_metadata = self.table_metadata(database, table)
        info = self.ds.inspect()[database]
        sortable_columns = self.sortable_columns_for_table(database, table, True)
        columns = [
            {"name": r[0], "sortable": r[0] in sortable_columns} for r in description
        ]
        tables = info["tables"]
        table_info = tables.get(table) or {}
        pks = table_info.get("primary_keys") or []

        # Prefetch foreign key resolutions for later expansion:
        fks = {}
        labeled_fks = {}
        if table_info and expand_foreign_keys:
            foreign_keys = table_info["foreign_keys"]["outgoing"]
            for fk in foreign_keys:
                label_column = (
                    # First look in metadata.json definition for this foreign key table:
                    self.table_metadata(database, fk["other_table"]).get("label_column")
                    # Fall back to label_column from .inspect() detection:
                    or tables.get(fk["other_table"], {}).get("label_column")
                )
                if not label_column:
                    # No label for this FK
                    fks[fk["column"]] = fk["other_table"]
                    continue

                ids_to_lookup = set([row[fk["column"]] for row in rows])
                sql = '''
                    select {other_column}, {label_column}
                    from {other_table}
                    where {other_column} in ({placeholders})
                '''.format(
                    other_column=escape_sqlite(fk["other_column"]),
                    label_column=escape_sqlite(label_column),
                    other_table=escape_sqlite(fk["other_table"]),
                    placeholders=", ".join(["?"] * len(ids_to_lookup)),
                )
                try:
                    results = await self.execute(
                        database, sql, list(set(ids_to_lookup))
                    )
                except InterruptedError:
                    pass
                else:
                    for id, value in results:
                        labeled_fks[(fk["column"], id)] = (fk["other_table"], value)

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

                if (column, value) in labeled_fks:
                    other_table, label = labeled_fks[(column, value)]
                    display_value = jinja2.Markup(
                        '<a href="/{database}/{table}/{link_id}">{label}</a>&nbsp;<em>{id}</em>'.format(
                            database=database,
                            table=urllib.parse.quote_plus(other_table),
                            link_id=urllib.parse.quote_plus(str(value)),
                            id=str(jinja2.escape(value)),
                            label=str(jinja2.escape(label)),
                        )
                    )
                elif column in fks:
                    display_value = jinja2.Markup(
                        '<a href="/{database}/{table}/{link_id}">{id}</a>'.format(
                            database=database,
                            table=urllib.parse.quote_plus(fks[column]),
                            link_id=urllib.parse.quote_plus(str(value)),
                            id=str(jinja2.escape(value)),
                        )
                    )
                elif value is None:
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

    async def data(self, request, name, hash, table):
        table = urllib.parse.unquote_plus(table)
        canned_query = self.ds.get_canned_query(name, table)
        if canned_query is not None:
            return await self.custom_sql(
                request,
                name,
                hash,
                canned_query["sql"],
                editable=False,
                canned_query=table,
            )

        is_view = bool(
            list(
                await self.execute(
                    name,
                    "SELECT count(*) from sqlite_master WHERE type = 'view' and name=:n",
                    {"n": table},
                )
            )[0][0]
        )
        view_definition = None
        table_definition = None
        if is_view:
            view_definition = list(
                await self.execute(
                    name,
                    'select sql from sqlite_master where name = :n and type="view"',
                    {"n": table},
                )
            )[0][0]
        else:
            table_definition_rows = list(
                await self.execute(
                    name,
                    'select sql from sqlite_master where name = :n and type="table"',
                    {"n": table},
                )
            )
            if not table_definition_rows:
                raise NotFound("Table not found: {}".format(table))

            table_definition = table_definition_rows[0][0]
        info = self.ds.inspect()
        table_info = info[name]["tables"].get(table) or {}
        pks = table_info.get("primary_keys") or []
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
        other_args = {}
        for key, value in args.items():
            if key.startswith("_") and "__" not in key:
                special_args[key] = value[0]
                special_args_lists[key] = value
            else:
                other_args[key] = value[0]

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

        table_metadata = self.table_metadata(name, table)
        units = table_metadata.get("units", {})
        filters = Filters(sorted(other_args.items()), units, ureg)
        where_clauses, params = filters.build_where_clauses()

        # _search support:
        fts_table = info[name]["tables"].get(table, {}).get("fts_table")
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
                    "rowid in (select rowid from {fts_table} where {fts_table} match :search)".format(
                        fts_table=escape_sqlite(fts_table),
                    )
                )
                search_descriptions.append('search matches "{}"'.format(search))
                params["search"] = search
            else:
                # More complex: search against specific columns
                valid_columns = set(info[name]["tables"][fts_table]["columns"])
                for i, (key, search_text) in enumerate(search_args.items()):
                    search_col = key.split("_search_", 1)[1]
                    if search_col not in valid_columns:
                        raise DatasetteError("Cannot search by that column", status=400)

                    where_clauses.append(
                        "rowid in (select rowid from {fts_table} where {search_col} match :search_{i})".format(
                            fts_table=escape_sqlite(fts_table),
                            search_col=escape_sqlite(search_col),
                            i=i
                        )
                    )
                    search_descriptions.append(
                        'search column "{}" matches "{}"'.format(
                            search_col, search_text
                        )
                    )
                    params["search_{}".format(i)] = search_text

        table_rows_count = None
        sortable_columns = set()
        if not is_view:
            table_rows_count = table_info["count"]
            sortable_columns = self.sortable_columns_for_table(name, table, use_rowid)

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
            where=(
                "where {} ".format(" and ".join(where_clauses))
            ) if where_clauses else "",
        )
        # Store current params and where_clauses for later:
        from_sql_params = dict(**params)
        from_sql_where_clauses = where_clauses[:]

        count_sql = "select count(*) {}".format(from_sql)

        _next = special_args.get("_next")
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
                                extra_desc_only="" if sort else " or {column2} is null".format(
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
            return await self.custom_sql(request, name, hash, sql, editable=True)

        extra_args = {}
        # Handle ?_size=500
        page_size = request.raw_args.get("_size")
        if page_size:
            if page_size == "max":
                page_size = self.max_returned_rows
            try:
                page_size = int(page_size)
                if page_size < 0:
                    raise ValueError

            except ValueError:
                raise DatasetteError("_size must be a positive integer", status=400)

            if page_size > self.max_returned_rows:
                raise DatasetteError(
                    "_size must be <= {}".format(self.max_returned_rows), status=400
                )

            extra_args["page_size"] = page_size
        else:
            page_size = self.page_size

        sql = "select {select} from {table_name} {where}{order_by}limit {limit}{offset}".format(
            select=select,
            table_name=escape_sqlite(table),
            where=where_clause,
            order_by=order_by,
            limit=page_size + 1,
            offset=offset,
        )

        if request.raw_args.get("_timelimit"):
            extra_args["custom_time_limit"] = int(request.raw_args["_timelimit"])

        rows, truncated, description = await self.execute(
            name, sql, params, truncate=True, **extra_args
        )

        # facets support
        facet_size = self.ds.config["default_facet_size"]
        metadata_facets = table_metadata.get("facets", [])
        facets = metadata_facets[:]
        try:
            facets.extend(request.args["_facet"])
        except KeyError:
            pass
        facet_results = {}
        facets_timed_out = []
        for column in facets:
            facet_sql = """
                select {col} as value, count(*) as count
                {from_sql} {and_or_where} {col} is not null
                group by {col} order by count desc limit {limit}
            """.format(
                col=escape_sqlite(column),
                from_sql=from_sql,
                and_or_where='and' if where_clause else 'where',
                limit=facet_size+1,
            )
            try:
                facet_rows = await self.execute(
                    name, facet_sql, params,
                    truncate=False,
                    custom_time_limit=self.ds.config["facet_time_limit_ms"],
                )
                facet_results_values = []
                facet_results[column] = {
                    "name": column,
                    "results": facet_results_values,
                    "truncated": len(facet_rows) > facet_size,
                }
                facet_rows = facet_rows[:facet_size]
                # Attempt to expand foreign keys into labels
                values = [row["value"] for row in facet_rows]
                expanded = (await self.expand_foreign_keys(
                    name, table, column, values
                ))
                for row in facet_rows:
                    selected = str(other_args.get(column)) == str(row["value"])
                    if selected:
                        toggle_path = path_with_removed_args(
                            request, {column: str(row["value"])}
                        )
                    else:
                        toggle_path = path_with_added_args(
                            request, {column: row["value"]}
                        )
                    facet_results_values.append({
                        "value": row["value"],
                        "label": expanded.get(
                            (column, row["value"]),
                            row["value"]
                        ),
                        "count": row["count"],
                        "toggle_url": urllib.parse.urljoin(
                            request.url, toggle_path
                        ),
                        "selected": selected,
                    })
            except InterruptedError:
                facets_timed_out.append(column)

        columns = [r[0] for r in description]
        rows = list(rows)

        filter_columns = columns[:]
        if use_rowid and filter_columns[0] == "rowid":
            filter_columns = filter_columns[1:]

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
            next_url = urllib.parse.urljoin(
                request.url, path_with_replaced_args(request, added_args)
            )
            rows = rows[:page_size]

        # Number of filtered rows in whole set:
        filtered_table_rows_count = None
        if count_sql:
            try:
                count_rows = list(await self.execute(
                    name, count_sql, from_sql_params
                ))
                filtered_table_rows_count = count_rows[0][0]
            except InterruptedError:
                pass

            # Detect suggested facets
            suggested_facets = []
            for facet_column in columns:
                if facet_column in facets:
                    continue
                suggested_facet_sql = '''
                    select distinct {column} {from_sql}
                    {and_or_where} {column} is not null
                    limit {limit}
                '''.format(
                    column=escape_sqlite(facet_column),
                    from_sql=from_sql,
                    and_or_where='and' if from_sql_where_clauses else 'where',
                    limit=facet_size+1
                )
                distinct_values = None
                try:
                    distinct_values = await self.execute(
                        name, suggested_facet_sql, from_sql_params,
                        truncate=False,
                        custom_time_limit=self.ds.config["facet_suggest_time_limit_ms"],
                    )
                    num_distinct_values = len(distinct_values)
                    if (
                        num_distinct_values and
                        num_distinct_values > 1 and
                        num_distinct_values <= facet_size and
                        num_distinct_values < filtered_table_rows_count
                    ):
                        suggested_facets.append({
                            'name': facet_column,
                            'toggle_url': path_with_added_args(
                                request, {'_facet': facet_column}
                            ),
                        })
                except InterruptedError:
                    pass

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
                name,
                table,
                description,
                rows,
                link_column=not is_view,
                expand_foreign_keys=True,
            )
            metadata = self.ds.metadata.get("databases", {}).get(name, {}).get(
                "tables", {}
            ).get(
                table, {}
            )
            self.ds.update_with_inherited_metadata(metadata)
            return {
                "database_hash": hash,
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
                    reverse=True
                ),
                "facet_hideable": lambda facet: facet not in metadata_facets,
                "is_sortable": any(c["sortable"] for c in display_columns),
                "path_with_replaced_args": path_with_replaced_args,
                "path_with_removed_args": path_with_removed_args,
                "request": request,
                "sort": sort,
                "sort_desc": sort_desc,
                "disable_sort": is_view,
                "custom_rows_and_columns_templates": [
                    "_rows_and_columns-{}-{}.html".format(
                        to_css_class(name), to_css_class(table)
                    ),
                    "_rows_and_columns-table-{}-{}.html".format(
                        to_css_class(name), to_css_class(table)
                    ),
                    "_rows_and_columns.html",
                ],
                "metadata": metadata,
            }

        return {
            "database": name,
            "table": table,
            "is_view": is_view,
            "view_definition": view_definition,
            "table_definition": table_definition,
            "human_description_en": human_description_en,
            "rows": rows[:page_size],
            "truncated": truncated,
            "table_rows_count": table_rows_count,
            "filtered_table_rows_count": filtered_table_rows_count,
            "columns": columns,
            "primary_keys": pks,
            "units": units,
            "query": {"sql": sql, "params": params},
            "facet_results": facet_results,
            "suggested_facets": suggested_facets,
            "next": next_value and str(next_value) or None,
            "next_url": next_url,
        }, extra_template, (
            "table-{}-{}.html".format(to_css_class(name), to_css_class(table)),
            "table.html",
        )


class RowView(RowTableShared):

    async def data(self, request, name, hash, table, pk_path):
        table = urllib.parse.unquote_plus(table)
        pk_values = urlsafe_components(pk_path)
        info = self.ds.inspect()[name]
        table_info = info["tables"].get(table) or {}
        pks = table_info.get("primary_keys") or []
        use_rowid = not pks
        select = "*"
        if use_rowid:
            select = "rowid, *"
            pks = ["rowid"]
        wheres = ['"{}"=:p{}'.format(pk, i) for i, pk in enumerate(pks)]
        sql = 'select {} from "{}" where {}'.format(select, table, " AND ".join(wheres))
        params = {}
        for i, pk_value in enumerate(pk_values):
            params["p{}".format(i)] = pk_value
        # rows, truncated, description = await self.execute(name, sql, params, truncate=True)
        rows, truncated, description = await self.execute(
            name, sql, params, truncate=True
        )
        columns = [r[0] for r in description]
        rows = list(rows)
        if not rows:
            raise NotFound("Record not found: {}".format(pk_values))

        async def template_data():
            display_columns, display_rows = await self.display_columns_and_rows(
                name,
                table,
                description,
                rows,
                link_column=False,
                expand_foreign_keys=True,
            )
            for column in display_columns:
                column["sortable"] = False
            return {
                "database_hash": hash,
                "foreign_key_tables": await self.foreign_key_tables(
                    name, table, pk_values
                ),
                "display_columns": display_columns,
                "display_rows": display_rows,
                "custom_rows_and_columns_templates": [
                    "_rows_and_columns-{}-{}.html".format(
                        to_css_class(name), to_css_class(table)
                    ),
                    "_rows_and_columns-row-{}-{}.html".format(
                        to_css_class(name), to_css_class(table)
                    ),
                    "_rows_and_columns.html",
                ],
                "metadata": self.ds.metadata.get("databases", {}).get(name, {}).get(
                    "tables", {}
                ).get(
                    table, {}
                ),
            }

        data = {
            "database": name,
            "table": table,
            "rows": rows,
            "columns": columns,
            "primary_keys": pks,
            "primary_key_values": pk_values,
            "units": self.table_metadata(name, table).get("units", {}),
        }

        if "foreign_key_tables" in (request.raw_args.get("_extras") or "").split(","):
            data["foreign_key_tables"] = await self.foreign_key_tables(
                name, table, pk_values
            )

        return data, template_data, (
            "row-{}-{}.html".format(to_css_class(name), to_css_class(table)), "row.html"
        )

    async def foreign_key_tables(self, name, table, pk_values):
        if len(pk_values) != 1:
            return []

        table_info = self.ds.inspect()[name]["tables"].get(table)
        if not table_info:
            return []

        foreign_keys = table_info["foreign_keys"]["incoming"]
        if len(foreign_keys) == 0:
            return []

        sql = "select " + ", ".join(
            [
                '(select count(*) from {table} where {column}=:id)'.format(
                    table=escape_sqlite(fk["other_table"]),
                    column=escape_sqlite(fk["other_column"]),
                )
                for fk in foreign_keys
            ]
        )
        try:
            rows = list(await self.execute(name, sql, {"id": pk_values[0]}))
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
            count = foreign_table_counts.get(
                (fk["other_table"], fk["other_column"])
            ) or 0
            foreign_key_tables.append({**fk, **{"count": count}})
        return foreign_key_tables
