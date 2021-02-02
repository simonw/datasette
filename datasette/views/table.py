import urllib
import itertools
import json

import jinja2

from datasette.plugins import pm
from datasette.database import QueryInterrupted
from datasette.utils import (
    await_me_maybe,
    CustomRow,
    MultiParams,
    append_querystring,
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
    value_as_boolean,
)
from datasette.utils.asgi import BadRequest, NotFound
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


class RowTableShared(DataView):
    async def sortable_columns_for_table(self, database, table, use_rowid):
        db = self.ds.databases[database]
        table_metadata = self.ds.table_metadata(database, table)
        if "sortable_columns" in table_metadata:
            sortable_columns = set(table_metadata["sortable_columns"])
        else:
            sortable_columns = set(await db.table_columns(table))
        if use_rowid:
            sortable_columns.add("rowid")
        return sortable_columns

    async def expandable_columns(self, database, table):
        # Returns list of (fk_dict, label_column-or-None) pairs for that table
        expandables = []
        db = self.ds.databases[database]
        for fk in await db.foreign_keys_for_table(table):
            label_column = await db.label_column_for_table(fk["other_table"])
            expandables.append((fk, label_column))
        return expandables

    async def display_columns_and_rows(
        self, database, table, description, rows, link_column=False, truncate_cells=0
    ):
        """Returns columns, rows for specified table - including fancy foreign key treatment"""
        db = self.ds.databases[database]
        table_metadata = self.ds.table_metadata(database, table)
        column_details = {col.name: col for col in await db.table_column_details(table)}
        sortable_columns = await self.sortable_columns_for_table(database, table, True)
        pks = await db.primary_keys(table)
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
                }
            )

        column_to_foreign_key_table = {
            fk["column"]: fk["other_table"]
            for fk in await db.foreign_keys_for_table(table)
        }

        cell_rows = []
        base_url = self.ds.setting("base_url")
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
                        "value": jinja2.Markup(
                            '<a href="{base_url}{database}/{table}/{flat_pks_quoted}">{flat_pks}</a>'.format(
                                base_url=base_url,
                                database=database,
                                table=urllib.parse.quote_plus(table),
                                flat_pks=str(jinja2.escape(pk_path)),
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
                        '<a class="blob-download" href="{}">&lt;Binary:&nbsp;{}&nbsp;byte{}&gt;</a>'.format(
                            self.ds.urls.row_blob(
                                database,
                                table,
                                path_from_row_pks(row, pks, not pks),
                                column,
                            ),
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
                    link_template = (
                        LINK_WITH_LABEL if (label != value) else LINK_WITH_VALUE
                    )
                    display_value = jinja2.Markup(
                        link_template.format(
                            database=database,
                            base_url=base_url,
                            table=urllib.parse.quote_plus(other_table),
                            link_id=urllib.parse.quote_plus(str(value)),
                            id=str(jinja2.escape(value)),
                            label=str(jinja2.escape(label)) or "-",
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
                    display_value = jinja2.Markup(f"{value:~P}".replace(" ", "&nbsp;"))
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


class TableView(RowTableShared):
    name = "table"

    async def post(self, request, db_name, table_and_format):
        # Handle POST to a canned query
        canned_query = await self.ds.get_canned_query(
            db_name, table_and_format, request.actor
        )
        assert canned_query, "You may only POST to a canned query"
        return await QueryView(self.ds).data(
            request,
            db_name,
            None,
            canned_query["sql"],
            metadata=canned_query,
            editable=False,
            canned_query=table_and_format,
            named_parameters=canned_query.get("params"),
            write=bool(canned_query.get("write")),
        )

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
        canned_query = await self.ds.get_canned_query(database, table, request.actor)
        if canned_query:
            return await QueryView(self.ds).data(
                request,
                database,
                hash,
                canned_query["sql"],
                metadata=canned_query,
                editable=False,
                canned_query=table,
                named_parameters=canned_query.get("params"),
                write=bool(canned_query.get("write")),
            )

        db = self.ds.databases[database]
        is_view = bool(await db.get_view_definition(table))
        table_exists = bool(await db.table_exists(table))
        if not is_view and not table_exists:
            raise NotFound(f"Table not found: {table}")

        await self.check_permissions(
            request,
            [
                ("view-table", (database, table)),
                ("view-database", database),
                "view-instance",
            ],
        )

        private = not await self.ds.permission_allowed(
            None, "view-table", (database, table), default=True
        )

        pks = await db.primary_keys(table)
        table_column_details = await db.table_column_details(table)
        table_columns = [column.name for column in table_column_details]

        select_columns = ", ".join(escape_sqlite(t) for t in table_columns)

        use_rowid = not pks and not is_view
        if use_rowid:
            select = f"rowid, {select_columns}"
            order_by = "rowid"
            order_by_pks = "rowid"
        else:
            select = select_columns
            order_by_pks = ", ".join([escape_sqlite(pk) for pk in pks])
            order_by = order_by_pks

        if is_view:
            order_by = ""

        # Ensure we don't drop anything with an empty value e.g. ?name__exact=
        args = MultiParams(
            urllib.parse.parse_qs(request.query_string, keep_blank_values=True)
        )

        # Special args start with _ and do not contain a __
        # That's so if there is a column that starts with _
        # it can still be queried using ?_col__exact=blah
        special_args = {}
        other_args = []
        for key in args:
            if key.startswith("_") and "__" not in key:
                special_args[key] = args[key]
            else:
                for v in args.getlist(key):
                    other_args.append((key, v))

        # Handle ?_filter_column and redirect, if present
        redirect_params = filters_should_redirect(special_args)
        if redirect_params:
            return self.redirect(
                request,
                path_with_added_args(request, redirect_params),
                forward_querystring=False,
            )

        # If ?_sort_by_desc=on (from checkbox) redirect to _sort_desc=(_sort)
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
            if not await self.ds.permission_allowed(
                request.actor,
                "execute-sql",
                resource=database,
                default=True,
            ):
                raise DatasetteError("_where= is not allowed", status=403)
            else:
                where_clauses.extend(request.args.getlist("_where"))
                extra_wheres_for_ui = [
                    {
                        "text": text,
                        "remove_url": path_with_removed_args(request, {"_where": text}),
                    }
                    for text in request.args.getlist("_where")
                ]

        # Support for ?_through={table, column, value}
        extra_human_descriptions = []
        if "_through" in request.args:
            for through in request.args.getlist("_through"):
                through_data = json.loads(through)
                through_table = through_data["table"]
                other_column = through_data["column"]
                value = through_data["value"]
                outgoing_foreign_keys = await db.foreign_keys_for_table(through_table)
                try:
                    fk_to_us = [
                        fk for fk in outgoing_foreign_keys if fk["other_table"] == table
                    ][0]
                except IndexError:
                    raise DatasetteError(
                        "Invalid _through - could not find corresponding foreign key"
                    )
                param = f"p{len(params)}"
                where_clauses.append(
                    "{our_pk} in (select {our_column} from {through_table} where {other_column} = :{param})".format(
                        through_table=escape_sqlite(through_table),
                        our_pk=escape_sqlite(fk_to_us["other_column"]),
                        our_column=escape_sqlite(fk_to_us["column"]),
                        other_column=escape_sqlite(other_column),
                        param=param,
                    )
                )
                params[param] = value
                extra_human_descriptions.append(
                    f'{through_table}.{other_column} = "{value}"'
                )

        # _search support:
        fts_table = special_args.get("_fts_table")
        fts_table = fts_table or table_metadata.get("fts_table")
        fts_table = fts_table or await db.fts_table(table)
        fts_pk = special_args.get("_fts_pk", table_metadata.get("fts_pk", "rowid"))
        search_args = dict(
            pair
            for pair in special_args.items()
            if pair[0].startswith("_search") and pair[0] != "_searchmode"
        )
        search = ""
        search_mode_raw = special_args.get("_searchmode") == "raw"
        if fts_table and search_args:
            if "_search" in search_args:
                # Simple ?_search=xxx
                search = search_args["_search"]
                where_clauses.append(
                    "{fts_pk} in (select rowid from {fts_table} where {fts_table} match {match_clause})".format(
                        fts_table=escape_sqlite(fts_table),
                        fts_pk=escape_sqlite(fts_pk),
                        match_clause=":search"
                        if search_mode_raw
                        else "escape_fts(:search)",
                    )
                )
                extra_human_descriptions.append(f'search matches "{search}"')
                params["search"] = search
            else:
                # More complex: search against specific columns
                for i, (key, search_text) in enumerate(search_args.items()):
                    search_col = key.split("_search_", 1)[1]
                    if search_col not in await db.table_columns(fts_table):
                        raise BadRequest("Cannot search by that column")

                    where_clauses.append(
                        "rowid in (select rowid from {fts_table} where {search_col} match {match_clause})".format(
                            fts_table=escape_sqlite(fts_table),
                            search_col=escape_sqlite(search_col),
                            match_clause=":search_{}".format(i)
                            if search_mode_raw
                            else "escape_fts(:search_{})".format(i),
                        )
                    )
                    extra_human_descriptions.append(
                        f'search column "{search_col}" matches "{search_text}"'
                    )
                    params[f"search_{i}"] = search_text

        sortable_columns = set()

        sortable_columns = await self.sortable_columns_for_table(
            database, table, use_rowid
        )

        # Allow for custom sort order
        sort = special_args.get("_sort")
        sort_desc = special_args.get("_sort_desc")

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
            table_name=escape_sqlite(table),
            where=("where {} ".format(" and ".join(where_clauses)))
            if where_clauses
            else "",
        )
        # Copy of params so we can mutate them later:
        from_sql_params = dict(**params)

        count_sql = f"select count(*) {from_sql}"

        _next = _next or special_args.get("_next")
        offset = ""
        if _next:
            if is_view:
                # _next is an offset
                offset = f" offset {int(_next)}"
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
            order_by = f"order by {order_by} "

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

        sql_no_limit = "select {select} from {table_name} {where}{order_by}".format(
            select=select,
            table_name=escape_sqlite(table),
            where=where_clause,
            order_by=order_by,
        )
        sql = f"{sql_no_limit.rstrip()} limit {page_size + 1}{offset}"

        if request.args.get("_timelimit"):
            extra_args["custom_time_limit"] = int(request.args.get("_timelimit"))

        results = await db.execute(sql, params, truncate=True, **extra_args)

        # Number of filtered rows in whole set:
        filtered_table_rows_count = None
        if (
            not db.is_mutable
            and self.ds.inspect_data
            and count_sql == f"select count(*) from {table} "
        ):
            try:
                filtered_table_rows_count = self.ds.inspect_data[database]["tables"][
                    table
                ]["count"]
            except KeyError:
                pass

        if count_sql and filtered_table_rows_count is None:
            try:
                count_rows = list(await db.execute(count_sql, from_sql_params))
                filtered_table_rows_count = count_rows[0][0]
            except QueryInterrupted:
                pass

        # facets support
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
                    database,
                    sql=sql_no_limit,
                    params=params,
                    table=table,
                    metadata=table_metadata,
                    row_count=filtered_table_rows_count,
                )
            )

        for facet in facet_instances:
            (
                instance_facet_results,
                instance_facets_timed_out,
            ) = await facet.facet_results()
            facet_results.update(instance_facet_results)
            facets_timed_out.extend(instance_facets_timed_out)

        # Figure out columns and rows for the query
        columns = [r[0] for r in results.description]
        rows = list(results.rows)

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
                prefix = rows[-2][sort or sort_desc]
                if isinstance(prefix, dict) and "value" in prefix:
                    prefix = prefix["value"]
                if prefix is None:
                    prefix = "$null"
                else:
                    prefix = urllib.parse.quote_plus(str(prefix))
                next_value = f"{prefix},{next_value}"
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
            self.ds.setting("suggest_facets")
            and self.ds.setting("allow_facet")
            and not _next
        ):
            for facet in facet_instances:
                suggested_facets.extend(await facet.suggest())

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

            display_columns, display_rows = await self.display_columns_and_rows(
                database,
                table,
                results.description,
                rows,
                link_column=not is_view,
                truncate_cells=self.ds.setting("truncate_cells_html"),
            )
            metadata = (
                (self.ds.metadata("databases") or {})
                .get(database, {})
                .get("tables", {})
                .get(table, {})
            )
            self.ds.update_with_inherited_metadata(metadata)

            form_hidden_args = []
            for key in request.args:
                if key.startswith("_") and key not in ("_sort", "_search"):
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
                    table=table,
                    database=database,
                    actor=request.actor,
                ):
                    extra_links = await await_me_maybe(hook)
                    if extra_links:
                        links.extend(extra_links)
                return links

            return {
                "table_actions": table_actions,
                "supports_search": bool(fts_table),
                "search": search or "",
                "use_rowid": use_rowid,
                "filters": filters,
                "display_columns": display_columns,
                "filter_columns": columns,
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
                "custom_table_templates": [
                    f"_table-{to_css_class(database)}-{to_css_class(table)}.html",
                    f"_table-table-{to_css_class(database)}-{to_css_class(table)}.html",
                    "_table.html",
                ],
                "metadata": metadata,
                "view_definition": await db.get_view_definition(table),
                "table_definition": await db.get_table_definition(table),
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
                "private": private,
                "allow_execute_sql": await self.ds.permission_allowed(
                    request.actor, "execute-sql", database, default=True
                ),
            },
            extra_template,
            (
                f"table-{to_css_class(database)}-{to_css_class(table)}.html",
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


class RowView(RowTableShared):
    name = "row"

    async def data(self, request, database, hash, table, pk_path, default_labels=False):
        await self.check_permissions(
            request,
            [
                ("view-table", (database, table)),
                ("view-database", database),
                "view-instance",
            ],
        )
        pk_values = urlsafe_components(pk_path)
        db = self.ds.databases[database]
        sql, params, pks = await _sql_params_pks(db, table, pk_values)
        results = await db.execute(sql, params, truncate=True)
        columns = [r[0] for r in results.description]
        rows = list(results.rows)
        if not rows:
            raise NotFound(f"Record not found: {pk_values}")

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
                "custom_table_templates": [
                    f"_table-{to_css_class(database)}-{to_css_class(table)}.html",
                    f"_table-row-{to_css_class(database)}-{to_css_class(table)}.html",
                    "_table.html",
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

        if "foreign_key_tables" in (request.args.get("_extras") or "").split(","):
            data["foreign_key_tables"] = await self.foreign_key_tables(
                database, table, pk_values
            )

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
            foreign_key_tables.append({**fk, **{"count": count}})
        return foreign_key_tables
