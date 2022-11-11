import asyncio
import itertools
import json

import markupsafe

from datasette.plugins import pm
from datasette.database import QueryInterrupted
from datasette import tracer
from datasette.utils import (
    await_me_maybe,
    CustomRow,
    append_querystring,
    compound_keys_after_sql,
    format_bytes,
    tilde_decode,
    tilde_encode,
    escape_sqlite,
    filters_should_redirect,
    format_bytes,
    is_url,
    path_from_row_pks,
    path_with_added_args,
    path_with_removed_args,
    path_with_replaced_args,
    to_css_class,
    truncate_url,
    urlsafe_components,
    value_as_boolean,
)
from datasette.utils.asgi import BadRequest, Forbidden, NotFound
from datasette.filters import Filters
from .base import DataView, DatasetteError, ureg
from .database import QueryView

LINK_WITH_LABEL = (
    '<a href="{base_url}{database}/{table}/{link_id}">{label}</a>&nbsp;<em>{id}</em>'
)
LINK_WITH_VALUE = '<a href="{base_url}{database}/{table}/{link_id}">{id}</a>'


class Row:
    def __init__(self, cells):
        self.cells = cells

    def __iter__(self):
        return iter(self.cells)

    def __getitem__(self, key):
        for cell in self.cells:
            if cell["column"] == key:
                return cell["raw"]
        raise KeyError

    def display(self, key):
        for cell in self.cells:
            if cell["column"] == key:
                return cell["value"]
        return None

    def __str__(self):
        d = {
            key: self[key]
            for key in [
                c["column"] for c in self.cells if not c.get("is_special_link_column")
            ]
        }
        return json.dumps(d, default=repr, indent=2)


class TableView(DataView):
    name = "table"

    async def sortable_columns_for_table(self, database_name, table_name, use_rowid):
        db = self.ds.databases[database_name]
        table_metadata = self.ds.table_metadata(database_name, table_name)
        if "sortable_columns" in table_metadata:
            sortable_columns = set(table_metadata["sortable_columns"])
        else:
            sortable_columns = set(await db.table_columns(table_name))
        if use_rowid:
            sortable_columns.add("rowid")
        return sortable_columns

    async def expandable_columns(self, database_name, table_name):
        # Returns list of (fk_dict, label_column-or-None) pairs for that table
        expandables = []
        db = self.ds.databases[database_name]
        for fk in await db.foreign_keys_for_table(table_name):
            label_column = await db.label_column_for_table(fk["other_table"])
            expandables.append((fk, label_column))
        return expandables

    async def post(self, request):
        database_route = tilde_decode(request.url_vars["database"])
        try:
            db = self.ds.get_database(route=database_route)
        except KeyError:
            raise NotFound("Database not found: {}".format(database_route))
        database_name = db.name
        table_name = tilde_decode(request.url_vars["table"])
        # Handle POST to a canned query
        canned_query = await self.ds.get_canned_query(
            database_name, table_name, request.actor
        )
        assert canned_query, "You may only POST to a canned query"
        return await QueryView(self.ds).data(
            request,
            canned_query["sql"],
            metadata=canned_query,
            editable=False,
            canned_query=table_name,
            named_parameters=canned_query.get("params"),
            write=bool(canned_query.get("write")),
        )

    async def columns_to_select(self, table_columns, pks, request):
        columns = list(table_columns)
        if "_col" in request.args:
            columns = list(pks)
            _cols = request.args.getlist("_col")
            bad_columns = [column for column in _cols if column not in table_columns]
            if bad_columns:
                raise DatasetteError(
                    "_col={} - invalid columns".format(", ".join(bad_columns)),
                    status=400,
                )
            # De-duplicate maintaining order:
            columns.extend(dict.fromkeys(_cols))
        if "_nocol" in request.args:
            # Return all columns EXCEPT these
            bad_columns = [
                column
                for column in request.args.getlist("_nocol")
                if (column not in table_columns) or (column in pks)
            ]
            if bad_columns:
                raise DatasetteError(
                    "_nocol={} - invalid columns".format(", ".join(bad_columns)),
                    status=400,
                )
            tmp_columns = [
                column
                for column in columns
                if column not in request.args.getlist("_nocol")
            ]
            columns = tmp_columns
        return columns

    async def data(
        self,
        request,
        default_labels=False,
        _next=None,
        _size=None,
    ):
        with tracer.trace_child_tasks():
            return await self._data_traced(request, default_labels, _next, _size)

    async def _data_traced(
        self,
        request,
        default_labels=False,
        _next=None,
        _size=None,
    ):
        database_route = tilde_decode(request.url_vars["database"])
        table_name = tilde_decode(request.url_vars["table"])
        try:
            db = self.ds.get_database(route=database_route)
        except KeyError:
            raise NotFound("Database not found: {}".format(database_route))
        database_name = db.name

        # For performance profiling purposes, ?_noparallel=1 turns off asyncio.gather
        async def _gather_parallel(*args):
            return await asyncio.gather(*args)

        async def _gather_sequential(*args):
            results = []
            for fn in args:
                results.append(await fn)
            return results

        gather = (
            _gather_sequential if request.args.get("_noparallel") else _gather_parallel
        )

        # If this is a canned query, not a table, then dispatch to QueryView instead
        canned_query = await self.ds.get_canned_query(
            database_name, table_name, request.actor
        )
        if canned_query:
            return await QueryView(self.ds).data(
                request,
                canned_query["sql"],
                metadata=canned_query,
                editable=False,
                canned_query=table_name,
                named_parameters=canned_query.get("params"),
                write=bool(canned_query.get("write")),
            )

        is_view, table_exists = map(
            bool,
            await gather(
                db.get_view_definition(table_name), db.table_exists(table_name)
            ),
        )

        # If table or view not found, return 404
        if not is_view and not table_exists:
            raise NotFound(f"Table not found: {table_name}")

        # Ensure user has permission to view this table
        visible, private = await self.ds.check_visibility(
            request.actor,
            permissions=[
                ("view-table", (database_name, table_name)),
                ("view-database", database_name),
                "view-instance",
            ],
        )
        if not visible:
            raise Forbidden("You do not have permission to view this table")

        # Handle ?_filter_column and redirect, if present
        redirect_params = filters_should_redirect(request.args)
        if redirect_params:
            return self.redirect(
                request,
                self.ds.urls.path(path_with_added_args(request, redirect_params)),
                forward_querystring=False,
            )

        # If ?_sort_by_desc=on (from checkbox) redirect to _sort_desc=(_sort)
        if "_sort_by_desc" in request.args:
            return self.redirect(
                request,
                self.ds.urls.path(
                    path_with_added_args(
                        request,
                        {
                            "_sort_desc": request.args.get("_sort"),
                            "_sort_by_desc": None,
                            "_sort": None,
                        },
                    )
                ),
                forward_querystring=False,
            )

        # Introspect columns and primary keys for table
        pks = await db.primary_keys(table_name)
        table_columns = await db.table_columns(table_name)

        # Take ?_col= and ?_nocol= into account
        specified_columns = await self.columns_to_select(table_columns, pks, request)
        select_specified_columns = ", ".join(
            escape_sqlite(t) for t in specified_columns
        )
        select_all_columns = ", ".join(escape_sqlite(t) for t in table_columns)

        # rowid tables (no specified primary key) need a different SELECT
        use_rowid = not pks and not is_view
        if use_rowid:
            select_specified_columns = f"rowid, {select_specified_columns}"
            select_all_columns = f"rowid, {select_all_columns}"
            order_by = "rowid"
            order_by_pks = "rowid"
        else:
            order_by_pks = ", ".join([escape_sqlite(pk) for pk in pks])
            order_by = order_by_pks

        if is_view:
            order_by = ""

        nocount = request.args.get("_nocount")
        nofacet = request.args.get("_nofacet")
        nosuggest = request.args.get("_nosuggest")

        if request.args.get("_shape") in ("array", "object"):
            nocount = True
            nofacet = True

        table_metadata = self.ds.table_metadata(database_name, table_name)
        units = table_metadata.get("units", {})

        # Arguments that start with _ and don't contain a __ are
        # special - things like ?_search= - and should not be
        # treated as filters.
        filter_args = []
        for key in request.args:
            if not (key.startswith("_") and "__" not in key):
                for v in request.args.getlist(key):
                    filter_args.append((key, v))

        # Build where clauses from query string arguments
        filters = Filters(sorted(filter_args), units, ureg)
        where_clauses, params = filters.build_where_clauses(table_name)

        # Execute filters_from_request plugin hooks - including the default
        # ones that live in datasette/filters.py
        extra_context_from_filters = {}
        extra_human_descriptions = []

        for hook in pm.hook.filters_from_request(
            request=request,
            table=table_name,
            database=database_name,
            datasette=self.ds,
        ):
            filter_arguments = await await_me_maybe(hook)
            if filter_arguments:
                where_clauses.extend(filter_arguments.where_clauses)
                params.update(filter_arguments.params)
                extra_human_descriptions.extend(filter_arguments.human_descriptions)
                extra_context_from_filters.update(filter_arguments.extra_context)

        # Deal with custom sort orders
        sortable_columns = await self.sortable_columns_for_table(
            database_name, table_name, use_rowid
        )
        sort = request.args.get("_sort")
        sort_desc = request.args.get("_sort_desc")

        if not sort and not sort_desc:
            sort = table_metadata.get("sort")
            sort_desc = table_metadata.get("sort_desc")

        if sort and sort_desc:
            raise DatasetteError("Cannot use _sort and _sort_desc at the same time")

        if sort:
            if sort not in sortable_columns:
                raise DatasetteError(f"Cannot sort table by {sort}")

            order_by = escape_sqlite(sort)

        if sort_desc:
            if sort_desc not in sortable_columns:
                raise DatasetteError(f"Cannot sort table by {sort_desc}")

            order_by = f"{escape_sqlite(sort_desc)} desc"

        from_sql = "from {table_name} {where}".format(
            table_name=escape_sqlite(table_name),
            where=("where {} ".format(" and ".join(where_clauses)))
            if where_clauses
            else "",
        )
        # Copy of params so we can mutate them later:
        from_sql_params = dict(**params)

        count_sql = f"select count(*) {from_sql}"

        # Handle pagination driven by ?_next=
        _next = _next or request.args.get("_next")
        offset = ""
        if _next:
            sort_value = None
            if is_view:
                # _next is an offset
                offset = f" offset {int(_next)}"
            else:
                components = urlsafe_components(_next)
                # If a sort order is applied and there are multiple components,
                # the first of these is the sort value
                if (sort or sort_desc) and (len(components) > 1):
                    sort_value = components[0]
                    # Special case for if non-urlencoded first token was $null
                    if _next.split(",")[0] == "$null":
                        sort_value = None
                    components = components[1:]

                # Figure out the SQL for next-based-on-primary-key first
                next_by_pk_clauses = []
                if use_rowid:
                    next_by_pk_clauses.append(f"rowid > :p{len(params)}")
                    params[f"p{len(params)}"] = components[0]
                else:
                    # Apply the tie-breaker based on primary keys
                    if len(components) == len(pks):
                        param_len = len(params)
                        next_by_pk_clauses.append(
                            compound_keys_after_sql(pks, param_len)
                        )
                        for i, pk_value in enumerate(components):
                            params[f"p{param_len + i}"] = pk_value

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
                        params[f"p{len(params)}"] = sort_value
                    order_by = f"{order_by}, {order_by_pks}"
                else:
                    where_clauses.extend(next_by_pk_clauses)

        where_clause = ""
        if where_clauses:
            where_clause = f"where {' and '.join(where_clauses)} "

        if order_by:
            order_by = f"order by {order_by}"

        extra_args = {}
        # Handle ?_size=500
        page_size = _size or request.args.get("_size") or table_metadata.get("size")
        if page_size:
            if page_size == "max":
                page_size = self.ds.max_returned_rows
            try:
                page_size = int(page_size)
                if page_size < 0:
                    raise ValueError

            except ValueError:
                raise BadRequest("_size must be a positive integer")

            if page_size > self.ds.max_returned_rows:
                raise BadRequest(f"_size must be <= {self.ds.max_returned_rows}")

            extra_args["page_size"] = page_size
        else:
            page_size = self.ds.page_size

        # Facets are calculated against SQL without order by or limit
        sql_no_order_no_limit = (
            "select {select_all_columns} from {table_name} {where}".format(
                select_all_columns=select_all_columns,
                table_name=escape_sqlite(table_name),
                where=where_clause,
            )
        )

        # This is the SQL that populates the main table on the page
        sql = "select {select_specified_columns} from {table_name} {where}{order_by} limit {page_size}{offset}".format(
            select_specified_columns=select_specified_columns,
            table_name=escape_sqlite(table_name),
            where=where_clause,
            order_by=order_by,
            page_size=page_size + 1,
            offset=offset,
        )

        if request.args.get("_timelimit"):
            extra_args["custom_time_limit"] = int(request.args.get("_timelimit"))

        # Execute the main query!
        results = await db.execute(sql, params, truncate=True, **extra_args)

        # Calculate the total count for this query
        filtered_table_rows_count = None
        if (
            not db.is_mutable
            and self.ds.inspect_data
            and count_sql == f"select count(*) from {table_name} "
        ):
            # We can use a previously cached table row count
            try:
                filtered_table_rows_count = self.ds.inspect_data[database_name][
                    "tables"
                ][table_name]["count"]
            except KeyError:
                pass

        # Otherwise run a select count(*) ...
        if count_sql and filtered_table_rows_count is None and not nocount:
            try:
                count_rows = list(await db.execute(count_sql, from_sql_params))
                filtered_table_rows_count = count_rows[0][0]
            except QueryInterrupted:
                pass

        # Faceting
        if not self.ds.setting("allow_facet") and any(
            arg.startswith("_facet") for arg in request.args
        ):
            raise BadRequest("_facet= is not allowed")

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
                    database_name,
                    sql=sql_no_order_no_limit,
                    params=params,
                    table=table_name,
                    metadata=table_metadata,
                    row_count=filtered_table_rows_count,
                )
            )

        async def execute_facets():
            if not nofacet:
                # Run them in parallel
                facet_awaitables = [facet.facet_results() for facet in facet_instances]
                facet_awaitable_results = await gather(*facet_awaitables)
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

        suggested_facets = []

        async def execute_suggested_facets():
            # Calculate suggested facets
            if (
                self.ds.setting("suggest_facets")
                and self.ds.setting("allow_facet")
                and not _next
                and not nofacet
                and not nosuggest
            ):
                # Run them in parallel
                facet_suggest_awaitables = [
                    facet.suggest() for facet in facet_instances
                ]
                for suggest_result in await gather(*facet_suggest_awaitables):
                    suggested_facets.extend(suggest_result)

        await gather(execute_facets(), execute_suggested_facets())

        # Figure out columns and rows for the query
        columns = [r[0] for r in results.description]
        rows = list(results.rows)

        # Expand labeled columns if requested
        expanded_columns = []
        expandable_columns = await self.expandable_columns(database_name, table_name)
        columns_to_expand = None
        try:
            all_labels = value_as_boolean(request.args.get("_labels", ""))
        except ValueError:
            all_labels = default_labels
        # Check for explicit _label=
        if "_label" in request.args:
            columns_to_expand = request.args.getlist("_label")
        if columns_to_expand is None and all_labels:
            # expand all columns with foreign keys
            columns_to_expand = [fk["column"] for fk, _ in expandable_columns]

        if columns_to_expand:
            expanded_labels = {}
            for fk, _ in expandable_columns:
                column = fk["column"]
                if column not in columns_to_expand:
                    continue
                if column not in columns:
                    continue
                expanded_columns.append(column)
                # Gather the values
                column_index = columns.index(column)
                values = [row[column_index] for row in rows]
                # Expand them
                expanded_labels.update(
                    await self.ds.expand_foreign_keys(
                        database_name, table_name, column, values
                    )
                )
            if expanded_labels:
                # Rewrite the rows
                new_rows = []
                for row in rows:
                    new_row = CustomRow(columns)
                    for column in row.keys():
                        value = row[column]
                        if (column, value) in expanded_labels and value is not None:
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
        if 0 < page_size < len(rows):
            if is_view:
                next_value = int(_next or 0) + page_size
            else:
                next_value = path_from_row_pks(rows[-2], pks, use_rowid)
            # If there's a sort or sort_desc, add that value as a prefix
            if (sort or sort_desc) and not is_view:
                try:
                    prefix = rows[-2][sort or sort_desc]
                except IndexError:
                    # sort/sort_desc column missing from SELECT - look up value by PK instead
                    prefix_where_clause = " and ".join(
                        "[{}] = :pk{}".format(pk, i) for i, pk in enumerate(pks)
                    )
                    prefix_lookup_sql = "select [{}] from [{}] where {}".format(
                        sort or sort_desc, table_name, prefix_where_clause
                    )
                    prefix = (
                        await db.execute(
                            prefix_lookup_sql,
                            {
                                **{
                                    "pk{}".format(i): rows[-2][pk]
                                    for i, pk in enumerate(pks)
                                }
                            },
                        )
                    ).single_value()
                if isinstance(prefix, dict) and "value" in prefix:
                    prefix = prefix["value"]
                if prefix is None:
                    prefix = "$null"
                else:
                    prefix = tilde_encode(str(prefix))
                next_value = f"{prefix},{next_value}"
                added_args = {"_next": next_value}
                if sort:
                    added_args["_sort"] = sort
                else:
                    added_args["_sort_desc"] = sort_desc
            else:
                added_args = {"_next": next_value}
            next_url = self.ds.absolute_url(
                request, self.ds.urls.path(path_with_replaced_args(request, added_args))
            )
            rows = rows[:page_size]

        # human_description_en combines filters AND search, if provided
        human_description_en = filters.human_description_en(
            extra=extra_human_descriptions
        )

        if sort or sort_desc:
            sorted_by = "sorted by {}{}".format(
                (sort or sort_desc), " descending" if sort_desc else ""
            )
            human_description_en = " ".join(
                [b for b in [human_description_en, sorted_by] if b]
            )

        async def extra_template():
            nonlocal sort

            display_columns, display_rows = await display_columns_and_rows(
                self.ds,
                database_name,
                table_name,
                results.description,
                rows,
                link_column=not is_view,
                truncate_cells=self.ds.setting("truncate_cells_html"),
                sortable_columns=await self.sortable_columns_for_table(
                    database_name, table_name, use_rowid=True
                ),
            )
            metadata = (
                (self.ds.metadata("databases") or {})
                .get(database_name, {})
                .get("tables", {})
                .get(table_name, {})
            )
            self.ds.update_with_inherited_metadata(metadata)

            form_hidden_args = []
            for key in request.args:
                if (
                    key.startswith("_")
                    and key not in ("_sort", "_sort_desc", "_search", "_next")
                    and "__" not in key
                ):
                    for value in request.args.getlist(key):
                        form_hidden_args.append((key, value))

            # if no sort specified AND table has a single primary key,
            # set sort to that so arrow is displayed
            if not sort and not sort_desc:
                if 1 == len(pks):
                    sort = pks[0]
                elif use_rowid:
                    sort = "rowid"

            async def table_actions():
                links = []
                for hook in pm.hook.table_actions(
                    datasette=self.ds,
                    table=table_name,
                    database=database_name,
                    actor=request.actor,
                    request=request,
                ):
                    extra_links = await await_me_maybe(hook)
                    if extra_links:
                        links.extend(extra_links)
                return links

            # filter_columns combine the columns we know are available
            # in the table with any additional columns (such as rowid)
            # which are available in the query
            filter_columns = list(columns) + [
                table_column
                for table_column in table_columns
                if table_column not in columns
            ]
            d = {
                "table_actions": table_actions,
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
                "form_hidden_args": form_hidden_args,
                "is_sortable": any(c["sortable"] for c in display_columns),
                "fix_path": self.ds.urls.path,
                "path_with_replaced_args": path_with_replaced_args,
                "path_with_removed_args": path_with_removed_args,
                "append_querystring": append_querystring,
                "request": request,
                "sort": sort,
                "sort_desc": sort_desc,
                "disable_sort": is_view,
                "custom_table_templates": [
                    f"_table-{to_css_class(database_name)}-{to_css_class(table_name)}.html",
                    f"_table-table-{to_css_class(database_name)}-{to_css_class(table_name)}.html",
                    "_table.html",
                ],
                "metadata": metadata,
                "view_definition": await db.get_view_definition(table_name),
                "table_definition": await db.get_table_definition(table_name),
                "datasette_allow_facet": "true"
                if self.ds.setting("allow_facet")
                else "false",
            }
            d.update(extra_context_from_filters)
            return d

        return (
            {
                "database": database_name,
                "table": table_name,
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
                "private": private,
                "allow_execute_sql": await self.ds.permission_allowed(
                    request.actor, "execute-sql", database_name, default=True
                ),
            },
            extra_template,
            (
                f"table-{to_css_class(database_name)}-{to_css_class(table_name)}.html",
                "table.html",
            ),
        )


async def _sql_params_pks(db, table, pk_values):
    pks = await db.primary_keys(table)
    use_rowid = not pks
    select = "*"
    if use_rowid:
        select = "rowid, *"
        pks = ["rowid"]
    wheres = [f'"{pk}"=:p{i}' for i, pk in enumerate(pks)]
    sql = f"select {select} from {escape_sqlite(table)} where {' AND '.join(wheres)}"
    params = {}
    for i, pk_value in enumerate(pk_values):
        params[f"p{i}"] = pk_value
    return sql, params, pks


async def display_columns_and_rows(
    datasette,
    database_name,
    table_name,
    description,
    rows,
    link_column=False,
    truncate_cells=0,
    sortable_columns=None,
):
    """Returns columns, rows for specified table - including fancy foreign key treatment"""
    sortable_columns = sortable_columns or set()
    db = datasette.databases[database_name]
    table_metadata = datasette.table_metadata(database_name, table_name)
    column_descriptions = table_metadata.get("columns") or {}
    column_details = {
        col.name: col for col in await db.table_column_details(table_name)
    }
    pks = await db.primary_keys(table_name)
    pks_for_display = pks
    if not pks_for_display:
        pks_for_display = ["rowid"]

    columns = []
    for r in description:
        if r[0] == "rowid" and "rowid" not in column_details:
            type_ = "integer"
            notnull = 0
        else:
            type_ = column_details[r[0]].type
            notnull = column_details[r[0]].notnull
        columns.append(
            {
                "name": r[0],
                "sortable": r[0] in sortable_columns,
                "is_pk": r[0] in pks_for_display,
                "type": type_,
                "notnull": notnull,
                "description": column_descriptions.get(r[0]),
            }
        )

    column_to_foreign_key_table = {
        fk["column"]: fk["other_table"]
        for fk in await db.foreign_keys_for_table(table_name)
    }

    cell_rows = []
    base_url = datasette.setting("base_url")
    for row in rows:
        cells = []
        # Unless we are a view, the first column is a link - either to the rowid
        # or to the simple or compound primary key
        if link_column:
            is_special_link_column = len(pks) != 1
            pk_path = path_from_row_pks(row, pks, not pks, False)
            cells.append(
                {
                    "column": pks[0] if len(pks) == 1 else "Link",
                    "value_type": "pk",
                    "is_special_link_column": is_special_link_column,
                    "raw": pk_path,
                    "value": markupsafe.Markup(
                        '<a href="{table_path}/{flat_pks_quoted}">{flat_pks}</a>'.format(
                            base_url=base_url,
                            table_path=datasette.urls.table(database_name, table_name),
                            flat_pks=str(markupsafe.escape(pk_path)),
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
            plugin_display_value = None
            for candidate in pm.hook.render_cell(
                row=row,
                value=value,
                column=column,
                table=table_name,
                database=database_name,
                datasette=datasette,
            ):
                candidate = await await_me_maybe(candidate)
                if candidate is not None:
                    plugin_display_value = candidate
                    break
            if plugin_display_value:
                display_value = plugin_display_value
            elif isinstance(value, bytes):
                formatted = format_bytes(len(value))
                display_value = markupsafe.Markup(
                    '<a class="blob-download" href="{}"{}>&lt;Binary:&nbsp;{:,}&nbsp;byte{}&gt;</a>'.format(
                        datasette.urls.row_blob(
                            database_name,
                            table_name,
                            path_from_row_pks(row, pks, not pks),
                            column,
                        ),
                        ' title="{}"'.format(formatted)
                        if "bytes" not in formatted
                        else "",
                        len(value),
                        "" if len(value) == 1 else "s",
                    )
                )
            elif isinstance(value, dict):
                # It's an expanded foreign key - display link to other row
                label = value["label"]
                value = value["value"]
                # The table we link to depends on the column
                other_table = column_to_foreign_key_table[column]
                link_template = LINK_WITH_LABEL if (label != value) else LINK_WITH_VALUE
                display_value = markupsafe.Markup(
                    link_template.format(
                        database=database_name,
                        base_url=base_url,
                        table=tilde_encode(other_table),
                        link_id=tilde_encode(str(value)),
                        id=str(markupsafe.escape(value)),
                        label=str(markupsafe.escape(label)) or "-",
                    )
                )
            elif value in ("", None):
                display_value = markupsafe.Markup("&nbsp;")
            elif is_url(str(value).strip()):
                display_value = markupsafe.Markup(
                    '<a href="{url}">{truncated_url}</a>'.format(
                        url=markupsafe.escape(value.strip()),
                        truncated_url=markupsafe.escape(
                            truncate_url(value.strip(), truncate_cells)
                        ),
                    )
                )
            elif column in table_metadata.get("units", {}) and value != "":
                # Interpret units using pint
                value = value * ureg(table_metadata["units"][column])
                # Pint uses floating point which sometimes introduces errors in the compact
                # representation, which we have to round off to avoid ugliness. In the vast
                # majority of cases this rounding will be inconsequential. I hope.
                value = round(value.to_compact(), 6)
                display_value = markupsafe.Markup(f"{value:~P}".replace(" ", "&nbsp;"))
            else:
                display_value = str(value)
                if truncate_cells and len(display_value) > truncate_cells:
                    display_value = display_value[:truncate_cells] + "\u2026"

            cells.append(
                {
                    "column": column,
                    "value": display_value,
                    "raw": value,
                    "value_type": "none"
                    if value is None
                    else str(type(value).__name__),
                }
            )
        cell_rows.append(Row(cells))

    if link_column:
        # Add the link column header.
        # If it's a simple primary key, we have to remove and re-add that column name at
        # the beginning of the header row.
        first_column = None
        if len(pks) == 1:
            columns = [col for col in columns if col["name"] != pks[0]]
            first_column = {
                "name": pks[0],
                "sortable": len(pks) == 1,
                "is_pk": True,
                "type": column_details[pks[0]].type,
                "notnull": column_details[pks[0]].notnull,
            }
        else:
            first_column = {
                "name": "Link",
                "sortable": False,
                "is_pk": False,
                "type": "",
                "notnull": 0,
            }
        columns = [first_column] + columns
    return columns, cell_rows
