import asyncio
import itertools
import json
import urllib

from asyncinject import Registry
import markupsafe

from datasette.plugins import pm
from datasette.database import QueryInterrupted
from datasette import tracer
from datasette.renderer import json_renderer
from datasette.utils import (
    add_cors_headers,
    await_me_maybe,
    call_with_supported_arguments,
    CustomRow,
    append_querystring,
    compound_keys_after_sql,
    format_bytes,
    tilde_encode,
    escape_sqlite,
    filters_should_redirect,
    format_bytes,
    is_url,
    path_from_row_pks,
    path_with_added_args,
    path_with_format,
    path_with_removed_args,
    path_with_replaced_args,
    to_css_class,
    truncate_url,
    urlsafe_components,
    value_as_boolean,
    InvalidSql,
    sqlite3,
)
from datasette.utils.asgi import BadRequest, Forbidden, NotFound, Response
from datasette.filters import Filters
import sqlite_utils
from .base import BaseView, DataView, DatasetteError, ureg, _error
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


async def _gather_parallel(*args):
    return await asyncio.gather(*args)


async def _gather_sequential(*args):
    results = []
    for fn in args:
        results.append(await fn)
    return results


def _redirect(datasette, request, path, forward_querystring=True, remove_args=None):
    if request.query_string and "?" not in path and forward_querystring:
        path = f"{path}?{request.query_string}"
    if remove_args:
        path = path_with_removed_args(request, remove_args, path=path)
    r = Response.redirect(path)
    r.headers["Link"] = f"<{path}>; rel=preload"
    if datasette.cors:
        add_cors_headers(r.headers)
    return r


async def _redirect_if_needed(datasette, request, resolved):
    # Handle ?_filter_column
    redirect_params = filters_should_redirect(request.args)
    if redirect_params:
        return _redirect(
            datasette,
            request,
            datasette.urls.path(path_with_added_args(request, redirect_params)),
            forward_querystring=False,
        )

    # If ?_sort_by_desc=on (from checkbox) redirect to _sort_desc=(_sort)
    if "_sort_by_desc" in request.args:
        return _redirect(
            datasette,
            request,
            datasette.urls.path(
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
        from datasette.app import TableNotFound

        try:
            await self.ds.resolve_table(request)
        except TableNotFound as e:
            # Was this actually a canned query?
            canned_query = await self.ds.get_canned_query(
                e.database_name, e.table, request.actor
            )
            if canned_query:
                # Handle POST to a canned query
                return await QueryView(self.ds).data(
                    request,
                    canned_query["sql"],
                    metadata=canned_query,
                    editable=False,
                    canned_query=e.table,
                    named_parameters=canned_query.get("params"),
                    write=bool(canned_query.get("write")),
                )

        return Response.text("Method not allowed", status=405)

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
        from datasette.app import TableNotFound

        try:
            resolved = await self.ds.resolve_table(request)
        except TableNotFound as e:
            # Was this actually a canned query?
            canned_query = await self.ds.get_canned_query(
                e.database_name, e.table, request.actor
            )
            # If this is a canned query, not a table, then dispatch to QueryView instead
            if canned_query:
                return await QueryView(self.ds).data(
                    request,
                    canned_query["sql"],
                    metadata=canned_query,
                    editable=False,
                    canned_query=e.table,
                    named_parameters=canned_query.get("params"),
                    write=bool(canned_query.get("write")),
                )
            else:
                raise

        table_name = resolved.table
        db = resolved.db
        database_name = db.name

        # For performance profiling purposes, ?_noparallel=1 turns off asyncio.gather
        gather = (
            _gather_sequential if request.args.get("_noparallel") else _gather_parallel
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
            raise DatasetteError(
                "Cannot use _sort and _sort_desc at the same time", status=400
            )

        if sort:
            if sort not in sortable_columns:
                raise DatasetteError(f"Cannot sort table by {sort}", status=400)

            order_by = escape_sqlite(sort)

        if sort_desc:
            if sort_desc not in sortable_columns:
                raise DatasetteError(f"Cannot sort table by {sort_desc}", status=400)

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

        # Resolve extras
        extras = _get_extras(request)
        if request.args.getlist("_facet"):
            extras.add("facet_results")

        async def extra_count():
            # Calculate the total count for this query
            count = None
            if (
                not db.is_mutable
                and self.ds.inspect_data
                and count_sql == f"select count(*) from {table_name} "
            ):
                # We can use a previously cached table row count
                try:
                    count = self.ds.inspect_data[database_name]["tables"][table_name][
                        "count"
                    ]
                except KeyError:
                    pass

            # Otherwise run a select count(*) ...
            if count_sql and count is None and not nocount:
                try:
                    count_rows = list(await db.execute(count_sql, from_sql_params))
                    count = count_rows[0][0]
                except QueryInterrupted:
                    pass
            return count

        async def facet_instances(extra_count):
            facet_instances = []
            facet_classes = list(
                itertools.chain.from_iterable(pm.hook.register_facet_classes())
            )
            for facet_class in facet_classes:
                facet_instances.append(
                    facet_class(
                        self.ds,
                        request,
                        database_name,
                        sql=sql_no_order_no_limit,
                        params=params,
                        table=table_name,
                        metadata=table_metadata,
                        row_count=extra_count,
                    )
                )
            return facet_instances

        async def extra_facet_results(facet_instances):
            facet_results = {}
            facets_timed_out = []

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

            return {
                "results": facet_results,
                "timed_out": facets_timed_out,
            }

        async def extra_suggested_facets(facet_instances):
            suggested_facets = []
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
            return suggested_facets

        # Faceting
        if not self.ds.setting("allow_facet") and any(
            arg.startswith("_facet") for arg in request.args
        ):
            raise BadRequest("_facet= is not allowed")

        # pylint: disable=no-member

        # Figure out columns and rows for the query
        columns = [r[0] for r in results.description]
        rows = list(results.rows)

        # Expand labeled columns if requested
        expanded_columns = []

        # List of (fk_dict, label_column-or-None) pairs for that table
        expandable_columns = []
        for fk in await db.foreign_keys_for_table(table_name):
            label_column = await db.label_column_for_table(fk["other_table"])
            expandable_columns.append((fk, label_column))

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
        next_value, next_url = await _next_value_and_url(
            datasette,
            request,
            table_name,
            _next,
            rows,
            pks,
            use_rowid,
            sort,
            sort_desc,
            page_size,
            is_view,
        )
        rows = rows[:page_size]

        # human_description_en combines filters AND search, if provided
        async def extra_human_description_en():
            human_description_en = filters.human_description_en(
                extra=extra_human_descriptions
            )
            if sort or sort_desc:
                human_description_en = " ".join(
                    [b for b in [human_description_en, sorted_by] if b]
                )
            return human_description_en

        if sort or sort_desc:
            sorted_by = "sorted by {}{}".format(
                (sort or sort_desc), " descending" if sort_desc else ""
            )

        async def extra_next_url():
            return next_url

        async def extra_columns():
            return columns

        async def extra_primary_keys():
            return pks

        registry = Registry(
            extra_count,
            extra_facet_results,
            extra_suggested_facets,
            facet_instances,
            extra_human_description_en,
            extra_next_url,
            extra_columns,
            extra_primary_keys,
        )

        results = await registry.resolve_multi(
            ["extra_{}".format(extra) for extra in extras]
        )
        data = {
            "ok": True,
            "next": next_value and str(next_value) or None,
            "rows": rows[:page_size],
        }
        data.update(
            {
                key.replace("extra_", ""): value
                for key, value in results.items()
                if key.startswith("extra_") and key.replace("extra_", "") in extras
            }
        )
        return Response.json(data, default=repr)

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
                "count": count,
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
                    request.actor, "execute-sql", database_name
                ),
            },
            extra_template,
            (
                f"table-{to_css_class(database_name)}-{to_css_class(table_name)}.html",
                "table.html",
            ),
        )


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


class TableInsertView(BaseView):
    name = "table-insert"

    def __init__(self, datasette):
        self.ds = datasette

    async def _validate_data(self, request, db, table_name, pks, upsert):
        errors = []

        pks_list = []
        if isinstance(pks, str):
            pks_list = [pks]
        else:
            pks_list = list(pks)

        if not pks_list:
            pks_list = ["rowid"]

        def _errors(errors):
            return None, errors, {}

        if request.headers.get("content-type") != "application/json":
            # TODO: handle form-encoded data
            return _errors(["Invalid content-type, must be application/json"])
        body = await request.post_body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return _errors(["Invalid JSON: {}".format(e)])
        if not isinstance(data, dict):
            return _errors(["JSON must be a dictionary"])
        keys = data.keys()

        # keys must contain "row" or "rows"
        if "row" not in keys and "rows" not in keys:
            return _errors(['JSON must have one or other of "row" or "rows"'])
        rows = []
        if "row" in keys:
            if "rows" in keys:
                return _errors(['Cannot use "row" and "rows" at the same time'])
            row = data["row"]
            if not isinstance(row, dict):
                return _errors(['"row" must be a dictionary'])
            rows = [row]
            data["return"] = True
        else:
            rows = data["rows"]
        if not isinstance(rows, list):
            return _errors(['"rows" must be a list'])
        for row in rows:
            if not isinstance(row, dict):
                return _errors(['"rows" must be a list of dictionaries'])

        # Does this exceed max_insert_rows?
        max_insert_rows = self.ds.setting("max_insert_rows")
        if len(rows) > max_insert_rows:
            return _errors(
                ["Too many rows, maximum allowed is {}".format(max_insert_rows)]
            )

        # Validate other parameters
        extras = {
            key: value for key, value in data.items() if key not in ("row", "rows")
        }
        valid_extras = {"return", "ignore", "replace"}
        invalid_extras = extras.keys() - valid_extras
        if invalid_extras:
            return _errors(
                ['Invalid parameter: "{}"'.format('", "'.join(sorted(invalid_extras)))]
            )
        if extras.get("ignore") and extras.get("replace"):
            return _errors(['Cannot use "ignore" and "replace" at the same time'])

        # Validate columns of each row
        columns = set(await db.table_columns(table_name))
        columns.update(pks_list)

        for i, row in enumerate(rows):
            if upsert:
                # It MUST have the primary key
                missing_pks = [pk for pk in pks_list if pk not in row]
                if missing_pks:
                    errors.append(
                        'Row {} is missing primary key column(s): "{}"'.format(
                            i, '", "'.join(missing_pks)
                        )
                    )
            invalid_columns = set(row.keys()) - columns
            if invalid_columns:
                errors.append(
                    "Row {} has invalid columns: {}".format(
                        i, ", ".join(sorted(invalid_columns))
                    )
                )
        if errors:
            return _errors(errors)
        return rows, errors, extras

    async def post(self, request, upsert=False):
        try:
            resolved = await self.ds.resolve_table(request)
        except NotFound as e:
            return _error([e.args[0]], 404)
        db = resolved.db
        database_name = db.name
        table_name = resolved.table

        # Table must exist (may handle table creation in the future)
        db = self.ds.get_database(database_name)
        if not await db.table_exists(table_name):
            return _error(["Table not found: {}".format(table_name)], 404)

        if upsert:
            # Must have insert-row AND upsert-row permissions
            if not (
                await self.ds.permission_allowed(
                    request.actor, "insert-row", database_name, table_name
                )
                and await self.ds.permission_allowed(
                    request.actor, "update-row", database_name, table_name
                )
            ):
                return _error(
                    ["Permission denied: need both insert-row and update-row"], 403
                )
        else:
            # Must have insert-row permission
            if not await self.ds.permission_allowed(
                request.actor, "insert-row", resource=(database_name, table_name)
            ):
                return _error(["Permission denied"], 403)

        if not db.is_mutable:
            return _error(["Database is immutable"], 403)

        pks = await db.primary_keys(table_name)

        rows, errors, extras = await self._validate_data(
            request, db, table_name, pks, upsert
        )
        if errors:
            return _error(errors, 400)

        # No that we've passed pks to _validate_data it's safe to
        # fix the rowids case:
        if not pks:
            pks = ["rowid"]

        ignore = extras.get("ignore")
        replace = extras.get("replace")

        if upsert and (ignore or replace):
            return _error(["Upsert does not support ignore or replace"], 400)

        should_return = bool(extras.get("return", False))
        row_pk_values_for_later = []
        if should_return and upsert:
            row_pk_values_for_later = [tuple(row[pk] for pk in pks) for row in rows]

        def insert_or_upsert_rows(conn):
            table = sqlite_utils.Database(conn)[table_name]
            kwargs = {}
            if upsert:
                kwargs["pk"] = pks[0] if len(pks) == 1 else pks
            else:
                kwargs = {"ignore": ignore, "replace": replace}
            if should_return and not upsert:
                rowids = []
                method = table.upsert if upsert else table.insert
                for row in rows:
                    rowids.append(method(row, **kwargs).last_rowid)
                return list(
                    table.rows_where(
                        "rowid in ({})".format(",".join("?" for _ in rowids)),
                        rowids,
                    )
                )
            else:
                method_all = table.upsert_all if upsert else table.insert_all
                method_all(rows, **kwargs)

        try:
            rows = await db.execute_write_fn(insert_or_upsert_rows)
        except Exception as e:
            return _error([str(e)])
        result = {"ok": True}
        if should_return:
            if upsert:
                # Fetch based on initial input IDs
                where_clause = " OR ".join(
                    ["({})".format(" AND ".join("{} = ?".format(pk) for pk in pks))]
                    * len(row_pk_values_for_later)
                )
                args = list(itertools.chain.from_iterable(row_pk_values_for_later))
                fetched_rows = await db.execute(
                    "select {}* from [{}] where {}".format(
                        "rowid, " if pks == ["rowid"] else "", table_name, where_clause
                    ),
                    args,
                )
                result["rows"] = [dict(r) for r in fetched_rows.rows]
            else:
                result["rows"] = rows
        return Response.json(result, status=200 if upsert else 201)


class TableUpsertView(TableInsertView):
    name = "table-upsert"

    async def post(self, request):
        return await super().post(request, upsert=True)


class TableDropView(BaseView):
    name = "table-drop"

    def __init__(self, datasette):
        self.ds = datasette

    async def post(self, request):
        try:
            resolved = await self.ds.resolve_table(request)
        except NotFound as e:
            return _error([e.args[0]], 404)
        db = resolved.db
        database_name = db.name
        table_name = resolved.table
        # Table must exist
        db = self.ds.get_database(database_name)
        if not await db.table_exists(table_name):
            return _error(["Table not found: {}".format(table_name)], 404)
        if not await self.ds.permission_allowed(
            request.actor, "drop-table", resource=(database_name, table_name)
        ):
            return _error(["Permission denied"], 403)
        if not db.is_mutable:
            return _error(["Database is immutable"], 403)
        confirm = False
        try:
            data = json.loads(await request.post_body())
            confirm = data.get("confirm")
        except json.JSONDecodeError as e:
            pass

        if not confirm:
            return Response.json(
                {
                    "ok": True,
                    "database": database_name,
                    "table": table_name,
                    "row_count": (
                        await db.execute("select count(*) from [{}]".format(table_name))
                    ).single_value(),
                    "message": 'Pass "confirm": true to confirm',
                },
                status=200,
            )

        # Drop table
        def drop_table(conn):
            sqlite_utils.Database(conn)[table_name].drop()

        await db.execute_write_fn(drop_table)
        return Response.json({"ok": True}, status=200)


def _get_extras(request):
    extra_bits = request.args.getlist("_extra")
    extras = set()
    for bit in extra_bits:
        extras.update(bit.split(","))
    return extras


async def _columns_to_select(table_columns, pks, request):
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
            column for column in columns if column not in request.args.getlist("_nocol")
        ]
        columns = tmp_columns
    return columns


async def _sortable_columns_for_table(datasette, database_name, table_name, use_rowid):
    db = datasette.databases[database_name]
    table_metadata = datasette.table_metadata(database_name, table_name)
    if "sortable_columns" in table_metadata:
        sortable_columns = set(table_metadata["sortable_columns"])
    else:
        sortable_columns = set(await db.table_columns(table_name))
    if use_rowid:
        sortable_columns.add("rowid")
    return sortable_columns


async def _sort_order(table_metadata, sortable_columns, request, order_by):
    sort = request.args.get("_sort")
    sort_desc = request.args.get("_sort_desc")

    if not sort and not sort_desc:
        sort = table_metadata.get("sort")
        sort_desc = table_metadata.get("sort_desc")

    if sort and sort_desc:
        raise DatasetteError(
            "Cannot use _sort and _sort_desc at the same time", status=400
        )

    if sort:
        if sort not in sortable_columns:
            raise DatasetteError(f"Cannot sort table by {sort}", status=400)

        order_by = escape_sqlite(sort)

    if sort_desc:
        if sort_desc not in sortable_columns:
            raise DatasetteError(f"Cannot sort table by {sort_desc}", status=400)

        order_by = f"{escape_sqlite(sort_desc)} desc"

    return sort, sort_desc, order_by


async def table_view(datasette, request):
    await datasette.refresh_schemas()
    with tracer.trace_child_tasks():
        response = await table_view_traced(datasette, request)

    # CORS
    if datasette.cors:
        add_cors_headers(response.headers)

    # Cache TTL header
    ttl = request.args.get("_ttl", None)
    if ttl is None or not ttl.isdigit():
        ttl = datasette.setting("default_cache_ttl")

    if datasette.cache_headers and response.status == 200:
        ttl = int(ttl)
        if ttl == 0:
            ttl_header = "no-cache"
        else:
            ttl_header = f"max-age={ttl}"
        response.headers["Cache-Control"] = ttl_header

    # Referrer policy
    response.headers["Referrer-Policy"] = "no-referrer"

    return response


class CannedQueryView(DataView):
    def __init__(self, datasette):
        self.ds = datasette

    async def data(self, request, **kwargs):
        from datasette.app import TableNotFound

        try:
            await self.ds.resolve_table(request)
        except TableNotFound as not_found:
            canned_query = await self.ds.get_canned_query(
                not_found.database_name, not_found.table, request.actor
            )
            print("not_found", not_found)
            print("canned_query", canned_query)
            print("type(canned_query)", type(canned_query))
            if canned_query:
                return await QueryView(self.ds).data(
                    request,
                    canned_query["sql"],
                    metadata=canned_query,
                    editable=False,
                    canned_query=not_found.table,
                    named_parameters=canned_query.get("params"),
                    write=bool(canned_query.get("write")),
                )
            else:
                raise


async def table_view_traced(datasette, request):
    from datasette.app import TableNotFound

    try:
        resolved = await datasette.resolve_table(request)
    except TableNotFound as not_found:
        # Was this actually a canned query?
        canned_query = await datasette.get_canned_query(
            not_found.database_name, not_found.table, request.actor
        )
        # If this is a canned query, not a table, then dispatch to QueryView instead
        if canned_query:
            if request.method == "POST":
                return await CannedQueryView(datasette).post(request)
            else:
                return await CannedQueryView(datasette).get(request)
        else:
            raise

    if request.method == "POST":
        return Response.text("Method not allowed", status=405)

    format_ = request.url_vars.get("format") or "html"
    extra_extras = None
    context_for_html_hack = False
    default_labels = False
    if format_ == "html":
        extra_extras = {"_html"}
        context_for_html_hack = True
        default_labels = True

    view_data = await table_view_data(
        datasette,
        request,
        resolved,
        extra_extras=extra_extras,
        context_for_html_hack=context_for_html_hack,
        default_labels=default_labels,
    )
    if isinstance(view_data, Response):
        return view_data
    data, next_url = view_data

    # Handle formats from plugins
    if format_ == "csv":
        pass
    elif format_ in datasette.renderers.keys():
        # Dispatch request to the correct output format renderer
        # (CSV is not handled here due to streaming)
        result = call_with_supported_arguments(
            datasette.renderers[format_][0],
            datasette=datasette,
            columns=data.get("columns") or [],
            rows=data.get("rows") or [],
            sql=data.get("query", {}).get("sql", None),
            query_name=None,
            database=resolved.db.name,
            table=resolved.table,
            request=request,
            view_name="table",
            # These will be deprecated in Datasette 1.0:
            args=request.args,
            data=data,
        )
        if asyncio.iscoroutine(result):
            result = await result
        if result is None:
            raise NotFound("No data")
        if isinstance(result, dict):
            r = Response(
                body=result.get("body"),
                status=result.get("status_code", status_code or 200),
                content_type=result.get("content_type", "text/plain"),
                headers=result.get("headers"),
            )
        elif isinstance(result, Response):
            r = result
            # if status_code is not None:
            #     # Over-ride the status code
            #     r.status = status_code
        else:
            assert False, f"{result} should be dict or Response"
        return r

    if format_ == "html":
        return Response.html(
            await datasette.render_template(
                "table.html",
                dict(
                    data,
                    append_querystring=append_querystring,
                    path_with_replaced_args=path_with_replaced_args,
                    fix_path=datasette.urls.path,
                    settings=datasette.settings_dict(),
                    # TODO: clean up all of these hacks:
                    alternate_url_json=None,
                    datasette_allow_facet=(
                        "true" if datasette.setting("allow_facet") else "false"
                    ),
                    is_sortable=any(c["sortable"] for c in data["display_columns"]),
                    allow_execute_sql=await datasette.permission_allowed(
                        request.actor, "execute-sql", resolved.db.name
                    ),
                    query_ms=1.2,
                    select_templates=[],
                ),
                request=request,
                view_name="table",
            ),
            # headers=headers,
        )
    else:
        response = json_renderer(request.args, data, None)
        if next_url:
            response.headers["link"] = f'<{next_url}>; rel="next"'
        return response


async def table_view_data(
    datasette,
    request,
    resolved,
    extra_extras=None,
    context_for_html_hack=False,
    default_labels=False,
):
    extra_extras = extra_extras or set()
    # We have a table or view
    db = resolved.db
    database_name = resolved.db.name
    table_name = resolved.table
    is_view = resolved.is_view

    # Can this user view it?
    visible, private = await datasette.check_visibility(
        request.actor,
        permissions=[
            ("view-table", (database_name, table_name)),
            ("view-database", database_name),
            "view-instance",
        ],
    )
    if not visible:
        raise Forbidden("You do not have permission to view this table")

    # Redirect based on request.args, if necessary
    redirect_response = await _redirect_if_needed(datasette, request, resolved)
    if redirect_response:
        return redirect_response

    # Introspect columns and primary keys for table
    pks = await db.primary_keys(table_name)
    table_columns = await db.table_columns(table_name)

    # Take ?_col= and ?_nocol= into account
    specified_columns = await _columns_to_select(table_columns, pks, request)
    select_specified_columns = ", ".join(escape_sqlite(t) for t in specified_columns)
    select_all_columns = ", ".join(escape_sqlite(t) for t in table_columns)

    # rowid tables (no specified primary key) need a different SELECT
    use_rowid = not pks and not is_view
    order_by = ""
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

    # TODO: This logic should turn into logic about which ?_extras get
    # executed instead:
    nocount = request.args.get("_nocount")
    nofacet = request.args.get("_nofacet")
    nosuggest = request.args.get("_nosuggest")
    if request.args.get("_shape") in ("array", "object"):
        nocount = True
        nofacet = True

    table_metadata = datasette.table_metadata(database_name, table_name)
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
        datasette=datasette,
    ):
        filter_arguments = await await_me_maybe(hook)
        if filter_arguments:
            where_clauses.extend(filter_arguments.where_clauses)
            params.update(filter_arguments.params)
            extra_human_descriptions.extend(filter_arguments.human_descriptions)
            extra_context_from_filters.update(filter_arguments.extra_context)

    # Deal with custom sort orders
    sortable_columns = await _sortable_columns_for_table(
        datasette, database_name, table_name, use_rowid
    )

    sort, sort_desc, order_by = await _sort_order(
        table_metadata, sortable_columns, request, order_by
    )

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
    _next = request.args.get("_next")

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
                    next_by_pk_clauses.append(compound_keys_after_sql(pks, param_len))
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
    # TODO: This was:
    # page_size = _size or request.args.get("_size") or table_metadata.get("size")
    page_size = request.args.get("_size") or table_metadata.get("size")
    if page_size:
        if page_size == "max":
            page_size = datasette.max_returned_rows
        try:
            page_size = int(page_size)
            if page_size < 0:
                raise ValueError

        except ValueError:
            raise BadRequest("_size must be a positive integer")

        if page_size > datasette.max_returned_rows:
            raise BadRequest(f"_size must be <= {datasette.max_returned_rows}")

        extra_args["page_size"] = page_size
    else:
        page_size = datasette.page_size

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
    try:
        results = await db.execute(sql, params, truncate=True, **extra_args)
    except (sqlite3.OperationalError, InvalidSql) as e:
        raise DatasetteError(str(e), title="Invalid SQL", status=400)

    except sqlite3.OperationalError as e:
        raise DatasetteError(str(e))

    columns = [r[0] for r in results.description]
    rows = list(results.rows)

    # Expand labeled columns if requested
    expanded_columns = []
    # List of (fk_dict, label_column-or-None) pairs for that table
    expandable_columns = []
    for fk in await db.foreign_keys_for_table(table_name):
        label_column = await db.label_column_for_table(fk["other_table"])
        expandable_columns.append((fk, label_column))

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
                await datasette.expand_foreign_keys(
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

    _next = request.args.get("_next")

    # Pagination next link
    next_value, next_url = await _next_value_and_url(
        datasette,
        db,
        request,
        table_name,
        _next,
        rows,
        pks,
        use_rowid,
        sort,
        sort_desc,
        page_size,
        is_view,
    )
    rows = rows[:page_size]

    # For performance profiling purposes, ?_noparallel=1 turns off asyncio.gather
    gather = _gather_sequential if request.args.get("_noparallel") else _gather_parallel

    # Resolve extras
    extras = _get_extras(request)
    if any(k for k in request.args.keys() if k == "_facet" or k.startswith("_facet_")):
        extras.add("facet_results")
    if request.args.get("_shape") == "object":
        extras.add("primary_keys")
    if extra_extras:
        extras.update(extra_extras)

    async def extra_count():
        "Total count of rows matching these filters"
        # Calculate the total count for this query
        count = None
        if (
            not db.is_mutable
            and datasette.inspect_data
            and count_sql == f"select count(*) from {table_name} "
        ):
            # We can use a previously cached table row count
            try:
                count = datasette.inspect_data[database_name]["tables"][table_name][
                    "count"
                ]
            except KeyError:
                pass

        # Otherwise run a select count(*) ...
        if count_sql and count is None and not nocount:
            try:
                count_rows = list(await db.execute(count_sql, from_sql_params))
                count = count_rows[0][0]
            except QueryInterrupted:
                pass
        return count

    async def facet_instances(extra_count):
        facet_instances = []
        facet_classes = list(
            itertools.chain.from_iterable(pm.hook.register_facet_classes())
        )
        for facet_class in facet_classes:
            facet_instances.append(
                facet_class(
                    datasette,
                    request,
                    database_name,
                    sql=sql_no_order_no_limit,
                    params=params,
                    table=table_name,
                    metadata=table_metadata,
                    row_count=extra_count,
                )
            )
        return facet_instances

    async def extra_facet_results(facet_instances):
        "Results of facets calculated against this data"
        facet_results = {}
        facets_timed_out = []

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

        return {
            "results": facet_results,
            "timed_out": facets_timed_out,
        }

    async def extra_suggested_facets(facet_instances):
        "Suggestions for facets that might return interesting results"
        suggested_facets = []
        # Calculate suggested facets
        if (
            datasette.setting("suggest_facets")
            and datasette.setting("allow_facet")
            and not _next
            and not nofacet
            and not nosuggest
        ):
            # Run them in parallel
            facet_suggest_awaitables = [facet.suggest() for facet in facet_instances]
            for suggest_result in await gather(*facet_suggest_awaitables):
                suggested_facets.extend(suggest_result)
        return suggested_facets

    # Faceting
    if not datasette.setting("allow_facet") and any(
        arg.startswith("_facet") for arg in request.args
    ):
        raise BadRequest("_facet= is not allowed")

    # human_description_en combines filters AND search, if provided
    async def extra_human_description_en():
        "Human-readable description of the filters"
        human_description_en = filters.human_description_en(
            extra=extra_human_descriptions
        )
        if sort or sort_desc:
            human_description_en = " ".join(
                [b for b in [human_description_en, sorted_by] if b]
            )
        return human_description_en

    if sort or sort_desc:
        sorted_by = "sorted by {}{}".format(
            (sort or sort_desc), " descending" if sort_desc else ""
        )

    async def extra_next_url():
        "Full URL for the next page of results"
        return next_url

    async def extra_columns():
        "Column names returned by this query"
        return columns

    async def extra_primary_keys():
        "Primary keys for this table"
        return pks

    async def extra_table_actions():
        async def table_actions():
            links = []
            for hook in pm.hook.table_actions(
                datasette=datasette,
                table=table_name,
                database=database_name,
                actor=request.actor,
                request=request,
            ):
                extra_links = await await_me_maybe(hook)
                if extra_links:
                    links.extend(extra_links)
            return links

        return table_actions

    async def extra_is_view():
        return is_view

    async def extra_debug():
        "Extra debug information"
        return {
            "resolved": repr(resolved),
            "url_vars": request.url_vars,
            "nofacet": nofacet,
            "nosuggest": nosuggest,
        }

    async def extra_request():
        "Full information about the request"
        return {
            "url": request.url,
            "path": request.path,
            "full_path": request.full_path,
            "host": request.host,
            "args": request.args._data,
        }

    async def run_display_columns_and_rows():
        display_columns, display_rows = await display_columns_and_rows(
            datasette,
            database_name,
            table_name,
            results.description,
            rows,
            link_column=not is_view,
            truncate_cells=datasette.setting("truncate_cells_html"),
            sortable_columns=sortable_columns,
        )
        return {
            "columns": display_columns,
            "rows": display_rows,
        }

    async def extra_display_columns(run_display_columns_and_rows):
        return run_display_columns_and_rows["columns"]

    async def extra_display_rows(run_display_columns_and_rows):
        return run_display_columns_and_rows["rows"]

    async def extra_query():
        "Details of the underlying SQL query"
        return {
            "sql": sql,
            "params": params,
        }

    async def extra_metadata():
        "Metadata about the table and database"
        metadata = (
            (datasette.metadata("databases") or {})
            .get(database_name, {})
            .get("tables", {})
            .get(table_name, {})
        )
        datasette.update_with_inherited_metadata(metadata)
        print("metadata", metadata)
        return metadata

    async def extra_database():
        return database_name

    async def extra_table():
        return table_name

    async def extra_database_color():
        return lambda _: "ff0000"

    async def extra_form_hidden_args():
        form_hidden_args = []
        for key in request.args:
            if (
                key.startswith("_")
                and key not in ("_sort", "_sort_desc", "_search", "_next")
                and "__" not in key
            ):
                for value in request.args.getlist(key):
                    form_hidden_args.append((key, value))
        return form_hidden_args

    async def extra_filters():
        return filters

    async def extra_custom_table_templates():
        return [
            f"_table-{to_css_class(database_name)}-{to_css_class(table_name)}.html",
            f"_table-table-{to_css_class(database_name)}-{to_css_class(table_name)}.html",
            "_table.html",
        ]

    async def extra_sorted_facet_results(extra_facet_results):
        return sorted(
            extra_facet_results["results"].values(),
            key=lambda f: (len(f["results"]), f["name"]),
            reverse=True,
        )

    async def extra_table_definition():
        return await db.get_table_definition(table_name)

    async def extra_view_definition():
        return await db.get_view_definition(table_name)

    async def extra_renderers(extra_expandable_columns, extra_query):
        renderers = {}
        url_labels_extra = {}
        if extra_expandable_columns:
            url_labels_extra = {"_labels": "on"}
        for key, (_, can_render) in datasette.renderers.items():
            it_can_render = call_with_supported_arguments(
                can_render,
                datasette=datasette,
                columns=columns or [],
                rows=rows or [],
                sql=extra_query.get("sql", None),
                query_name=None,
                database=database_name,
                table=table_name,
                request=request,
                view_name="table",
            )
            it_can_render = await await_me_maybe(it_can_render)
            if it_can_render:
                renderers[key] = datasette.urls.path(
                    path_with_format(
                        request=request, format=key, extra_qs={**url_labels_extra}
                    )
                )
        return renderers

    async def extra_private():
        return private

    async def extra_expandable_columns():
        expandables = []
        db = datasette.databases[database_name]
        for fk in await db.foreign_keys_for_table(table_name):
            label_column = await db.label_column_for_table(fk["other_table"])
            expandables.append((fk, label_column))
        return expandables

    async def extra_extras():
        "Available ?_extra= blocks"
        return {
            "available": [
                {
                    "name": key[len("extra_") :],
                    "doc": fn.__doc__,
                }
                for key, fn in registry._registry.items()
                if key.startswith("extra_")
            ],
            "selected": list(extras),
        }

    async def extra_facets_timed_out(extra_facet_results):
        return extra_facet_results["timed_out"]

    bundles = {
        "html": [
            "suggested_facets",
            "facet_results",
            "facets_timed_out",
            "count",
            "human_description_en",
            "next_url",
            "metadata",
            "query",
            "display_columns",
            "display_rows",
            "database",
            "table",
            "database_color",
            "table_actions",
            "filters",
            "renderers",
            "custom_table_templates",
            "sorted_facet_results",
            "table_definition",
            "view_definition",
            "is_view",
            "private",
            "primary_keys",
            "expandable_columns",
            "form_hidden_args",
        ]
    }

    for key, values in bundles.items():
        if f"_{key}" in extras:
            extras.update(values)
        extras.discard(f"_{key}")

    registry = Registry(
        extra_count,
        extra_facet_results,
        extra_facets_timed_out,
        extra_suggested_facets,
        facet_instances,
        extra_human_description_en,
        extra_next_url,
        extra_columns,
        extra_primary_keys,
        run_display_columns_and_rows,
        extra_display_columns,
        extra_display_rows,
        extra_debug,
        extra_request,
        extra_query,
        extra_metadata,
        extra_extras,
        extra_database,
        extra_table,
        extra_database_color,
        extra_table_actions,
        extra_filters,
        extra_renderers,
        extra_custom_table_templates,
        extra_sorted_facet_results,
        extra_table_definition,
        extra_view_definition,
        extra_is_view,
        extra_private,
        extra_expandable_columns,
        extra_form_hidden_args,
    )

    results = await registry.resolve_multi(
        ["extra_{}".format(extra) for extra in extras]
    )
    data = {
        "ok": True,
        "next": next_value and str(next_value) or None,
    }
    data.update(
        {
            key.replace("extra_", ""): value
            for key, value in results.items()
            if key.startswith("extra_") and key.replace("extra_", "") in extras
        }
    )
    data["rows"] = [dict(r) for r in rows[:page_size]]

    if context_for_html_hack:
        data.update(extra_context_from_filters)
        # filter_columns combine the columns we know are available
        # in the table with any additional columns (such as rowid)
        # which are available in the query
        data["filter_columns"] = list(columns) + [
            table_column
            for table_column in table_columns
            if table_column not in columns
        ]
        url_labels_extra = {}
        if data.get("expandable_columns"):
            url_labels_extra = {"_labels": "on"}
        url_csv_args = {"_size": "max", **url_labels_extra}
        url_csv = datasette.urls.path(
            path_with_format(request=request, format="csv", extra_qs=url_csv_args)
        )
        url_csv_path = url_csv.split("?")[0]
        data.update(
            {
                "url_csv": url_csv,
                "url_csv_path": url_csv_path,
                "url_csv_hidden_args": [
                    (key, value)
                    for key, value in urllib.parse.parse_qsl(request.query_string)
                    if key not in ("_labels", "_facet", "_size")
                ]
                + [("_size", "max")],
            }
        )
        # if no sort specified AND table has a single primary key,
        # set sort to that so arrow is displayed
        if not sort and not sort_desc:
            if 1 == len(pks):
                sort = pks[0]
            elif use_rowid:
                sort = "rowid"
        data["sort"] = sort
        data["sort_desc"] = sort_desc

    return data, next_url


async def _next_value_and_url(
    datasette,
    db,
    request,
    table_name,
    _next,
    rows,
    pks,
    use_rowid,
    sort,
    sort_desc,
    page_size,
    is_view,
):
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
        next_url = datasette.absolute_url(
            request, datasette.urls.path(path_with_replaced_args(request, added_args))
        )
    return next_value, next_url
