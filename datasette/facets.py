import json
import urllib
from datasette import hookimpl
from datasette.database import QueryInterrupted
from datasette.utils import (
    escape_sqlite,
    path_with_added_args,
    path_with_removed_args,
    detect_json1,
    sqlite3,
)


def load_facet_configs(request, table_config):
    # Given a request and the configuration for a table, return
    # a dictionary of selected facets, their lists of configs and for each
    # config whether it came from the request or the metadata.
    #
    #   return {type: [
    #       {"source": "metadata", "config": config1},
    #       {"source": "request", "config": config2}]}
    facet_configs = {}
    table_config = table_config or {}
    table_facet_configs = table_config.get("facets", [])
    for facet_config in table_facet_configs:
        if isinstance(facet_config, str):
            type = "column"
            facet_config = {"simple": facet_config}
        else:
            assert (
                len(facet_config.values()) == 1
            ), "Metadata config dicts should be {type: config}"
            type, facet_config = list(facet_config.items())[0]
            if isinstance(facet_config, str):
                facet_config = {"simple": facet_config}
        facet_configs.setdefault(type, []).append(
            {"source": "metadata", "config": facet_config}
        )
    qs_pairs = urllib.parse.parse_qs(request.query_string, keep_blank_values=True)
    for key, values in qs_pairs.items():
        if key.startswith("_facet"):
            # Figure out the facet type
            if key == "_facet":
                type = "column"
            elif key.startswith("_facet_"):
                type = key[len("_facet_") :]
            for value in values:
                # The value is the facet_config - either JSON or not
                facet_config = (
                    json.loads(value) if value.startswith("{") else {"simple": value}
                )
                facet_configs.setdefault(type, []).append(
                    {"source": "request", "config": facet_config}
                )
    return facet_configs


@hookimpl
def register_facet_classes():
    classes = [ColumnFacet, DateFacet]
    if detect_json1():
        classes.append(ArrayFacet)
    return classes


class Facet:
    type = None
    # How many rows to consider when suggesting facets:
    suggest_consider = 1000

    def __init__(
        self,
        ds,
        request,
        database,
        sql=None,
        table=None,
        params=None,
        table_config=None,
        row_count=None,
    ):
        assert table or sql, "Must provide either table= or sql="
        self.ds = ds
        self.request = request
        self.database = database
        # For foreign key expansion. Can be None for e.g. canned SQL queries:
        self.table = table
        self.sql = sql or f"select * from [{table}]"
        self.params = params or []
        self.table_config = table_config
        # row_count can be None, in which case we calculate it ourselves:
        self.row_count = row_count

    def get_configs(self):
        configs = load_facet_configs(self.request, self.table_config)
        return configs.get(self.type) or []

    def get_querystring_pairs(self):
        # ?_foo=bar&_foo=2&empty= becomes:
        # [('_foo', 'bar'), ('_foo', '2'), ('empty', '')]
        return urllib.parse.parse_qsl(self.request.query_string, keep_blank_values=True)

    def get_facet_size(self):
        facet_size = self.ds.setting("default_facet_size")
        max_returned_rows = self.ds.setting("max_returned_rows")
        table_facet_size = None
        if self.table:
            config_facet_size = (
                self.ds.config.get("databases", {})
                .get(self.database, {})
                .get("tables", {})
                .get(self.table, {})
                .get("facet_size")
            )
            if config_facet_size:
                table_facet_size = config_facet_size
        custom_facet_size = self.request.args.get("_facet_size")
        if custom_facet_size:
            if custom_facet_size == "max":
                facet_size = max_returned_rows
            elif custom_facet_size.isdigit():
                facet_size = int(custom_facet_size)
            else:
                # Invalid value, ignore it
                custom_facet_size = None
        if table_facet_size and not custom_facet_size:
            if table_facet_size == "max":
                facet_size = max_returned_rows
            else:
                facet_size = table_facet_size
        return min(facet_size, max_returned_rows)

    async def suggest(self):
        return []

    async def facet_results(self):
        # returns ([results], [timed_out])
        # TODO: Include "hideable" with each one somehow, which indicates if it was
        # defined in metadata (in which case you cannot turn it off)
        raise NotImplementedError

    async def get_columns(self, sql, params=None):
        # Detect column names using the "limit 0" trick
        return (
            await self.ds.execute(
                self.database, f"select * from ({sql}) limit 0", params or []
            )
        ).columns


class ColumnFacet(Facet):
    type = "column"

    async def suggest(self):
        suggested_facets = []
        try:
            row_count = await self.get_row_count()
            columns = await self.get_columns(self.sql, self.params)
        except QueryInterrupted:
            return suggested_facets
        facet_size = self.get_facet_size()
        already_enabled = [c["config"]["simple"] for c in self.get_configs()]
        for column in columns:
            if column in already_enabled:
                continue
            suggested_facet_sql = """
                with limited as (select * from ({sql}) limit {suggest_consider})
                select {column} as value, count(*) as n from limited
                where value is not null
                group by value
                limit {limit}
            """.format(
                column=escape_sqlite(column),
                sql=self.sql,
                limit=facet_size + 1,
                suggest_consider=self.suggest_consider,
            )
            distinct_values = None
            try:
                distinct_values = await self.ds.execute(
                    self.database,
                    suggested_facet_sql,
                    self.params,
                    truncate=False,
                    custom_time_limit=self.ds.setting("facet_suggest_time_limit_ms"),
                )
                num_distinct_values = len(distinct_values)
                if (
                    1 < num_distinct_values < row_count
                    and num_distinct_values <= facet_size
                    # And at least one has n > 1
                    and any(r["n"] > 1 for r in distinct_values)
                ):
                    suggested_facets.append(
                        {
                            "name": column,
                            "toggle_url": self.ds.absolute_url(
                                self.request,
                                self.ds.urls.path(
                                    path_with_added_args(
                                        self.request, {"_facet": column}
                                    )
                                ),
                            ),
                        }
                    )
            except QueryInterrupted:
                continue
        return suggested_facets

    async def get_row_count(self):
        if self.row_count is None:
            self.row_count = (
                await self.ds.execute(
                    self.database,
                    f"select count(*) from (select * from ({self.sql}) limit {self.suggest_consider})",
                    self.params,
                )
            ).rows[0][0]
        return self.row_count

    async def facet_results(self):
        facet_results = []
        facets_timed_out = []

        qs_pairs = self.get_querystring_pairs()

        facet_size = self.get_facet_size()
        for source_and_config in self.get_configs():
            config = source_and_config["config"]
            source = source_and_config["source"]
            column = config.get("column") or config["simple"]
            facet_sql = """
                select {col} as value, count(*) as count from (
                    {sql}
                )
                where {col} is not null
                group by {col} order by count desc, value limit {limit}
            """.format(
                col=escape_sqlite(column), sql=self.sql, limit=facet_size + 1
            )
            try:
                facet_rows_results = await self.ds.execute(
                    self.database,
                    facet_sql,
                    self.params,
                    truncate=False,
                    custom_time_limit=self.ds.setting("facet_time_limit_ms"),
                )
                facet_results_values = []
                facet_results.append(
                    {
                        "name": column,
                        "type": self.type,
                        "hideable": source != "metadata",
                        "toggle_url": self.ds.urls.path(
                            path_with_removed_args(self.request, {"_facet": column})
                        ),
                        "results": facet_results_values,
                        "truncated": len(facet_rows_results) > facet_size,
                    }
                )
                facet_rows = facet_rows_results.rows[:facet_size]
                if self.table:
                    # Attempt to expand foreign keys into labels
                    values = [row["value"] for row in facet_rows]
                    expanded = await self.ds.expand_foreign_keys(
                        self.request.actor, self.database, self.table, column, values
                    )
                else:
                    expanded = {}
                for row in facet_rows:
                    column_qs = column
                    if column.startswith("_"):
                        column_qs = "{}__exact".format(column)
                    selected = (column_qs, str(row["value"])) in qs_pairs
                    if selected:
                        toggle_path = path_with_removed_args(
                            self.request, {column_qs: str(row["value"])}
                        )
                    else:
                        toggle_path = path_with_added_args(
                            self.request, {column_qs: row["value"]}
                        )
                    facet_results_values.append(
                        {
                            "value": row["value"],
                            "label": expanded.get((column, row["value"]), row["value"]),
                            "count": row["count"],
                            "toggle_url": self.ds.absolute_url(
                                self.request, self.ds.urls.path(toggle_path)
                            ),
                            "selected": selected,
                        }
                    )
            except QueryInterrupted:
                facets_timed_out.append(column)

        return facet_results, facets_timed_out


class ArrayFacet(Facet):
    type = "array"

    def _is_json_array_of_strings(self, json_string):
        try:
            array = json.loads(json_string)
        except ValueError:
            return False
        for item in array:
            if not isinstance(item, str):
                return False
        return True

    async def suggest(self):
        columns = await self.get_columns(self.sql, self.params)
        suggested_facets = []
        already_enabled = [c["config"]["simple"] for c in self.get_configs()]
        for column in columns:
            if column in already_enabled:
                continue
            # Is every value in this column either null or a JSON array?
            suggested_facet_sql = """
                with limited as (select * from ({sql}) limit {suggest_consider})
                select distinct json_type({column})
                from limited
                where {column} is not null and {column} != ''
            """.format(
                column=escape_sqlite(column),
                sql=self.sql,
                suggest_consider=self.suggest_consider,
            )
            try:
                results = await self.ds.execute(
                    self.database,
                    suggested_facet_sql,
                    self.params,
                    truncate=False,
                    custom_time_limit=self.ds.setting("facet_suggest_time_limit_ms"),
                    log_sql_errors=False,
                )
                types = tuple(r[0] for r in results.rows)
                if types in (("array",), ("array", None)):
                    # Now check that first 100 arrays contain only strings
                    first_100 = [
                        v[0]
                        for v in await self.ds.execute(
                            self.database,
                            (
                                "select {column} from ({sql}) "
                                "where {column} is not null "
                                "and {column} != '' "
                                "and json_array_length({column}) > 0 "
                                "limit 100"
                            ).format(column=escape_sqlite(column), sql=self.sql),
                            self.params,
                            truncate=False,
                            custom_time_limit=self.ds.setting(
                                "facet_suggest_time_limit_ms"
                            ),
                            log_sql_errors=False,
                        )
                    ]
                    if first_100 and all(
                        self._is_json_array_of_strings(r) for r in first_100
                    ):
                        suggested_facets.append(
                            {
                                "name": column,
                                "type": "array",
                                "toggle_url": self.ds.absolute_url(
                                    self.request,
                                    self.ds.urls.path(
                                        path_with_added_args(
                                            self.request, {"_facet_array": column}
                                        )
                                    ),
                                ),
                            }
                        )
            except (QueryInterrupted, sqlite3.OperationalError):
                continue
        return suggested_facets

    async def facet_results(self):
        # self.configs should be a plain list of columns
        facet_results = []
        facets_timed_out = []

        facet_size = self.get_facet_size()
        for source_and_config in self.get_configs():
            config = source_and_config["config"]
            source = source_and_config["source"]
            column = config.get("column") or config["simple"]
            # https://github.com/simonw/datasette/issues/448
            facet_sql = """
                with inner as ({sql}),
                deduped_array_items as (
                    select
                        distinct j.value,
                        inner.*
                    from
                        json_each([inner].{col}) j
                        join inner
                )
                select
                    value as value,
                    count(*) as count
                from
                    deduped_array_items
                group by
                    value
                order by
                    count(*) desc, value limit {limit}
            """.format(
                col=escape_sqlite(column),
                sql=self.sql,
                limit=facet_size + 1,
            )
            try:
                facet_rows_results = await self.ds.execute(
                    self.database,
                    facet_sql,
                    self.params,
                    truncate=False,
                    custom_time_limit=self.ds.setting("facet_time_limit_ms"),
                )
                facet_results_values = []
                facet_results.append(
                    {
                        "name": column,
                        "type": self.type,
                        "results": facet_results_values,
                        "hideable": source != "metadata",
                        "toggle_url": self.ds.urls.path(
                            path_with_removed_args(
                                self.request, {"_facet_array": column}
                            )
                        ),
                        "truncated": len(facet_rows_results) > facet_size,
                    }
                )
                facet_rows = facet_rows_results.rows[:facet_size]
                pairs = self.get_querystring_pairs()
                for row in facet_rows:
                    value = str(row["value"])
                    selected = (f"{column}__arraycontains", value) in pairs
                    if selected:
                        toggle_path = path_with_removed_args(
                            self.request, {f"{column}__arraycontains": value}
                        )
                    else:
                        toggle_path = path_with_added_args(
                            self.request, {f"{column}__arraycontains": value}
                        )
                    facet_results_values.append(
                        {
                            "value": value,
                            "label": value,
                            "count": row["count"],
                            "toggle_url": self.ds.absolute_url(
                                self.request, toggle_path
                            ),
                            "selected": selected,
                        }
                    )
            except QueryInterrupted:
                facets_timed_out.append(column)

        return facet_results, facets_timed_out


class DateFacet(Facet):
    type = "date"

    async def suggest(self):
        columns = await self.get_columns(self.sql, self.params)
        already_enabled = [c["config"]["simple"] for c in self.get_configs()]
        suggested_facets = []
        for column in columns:
            if column in already_enabled:
                continue
            # Does this column contain any dates in the first 100 rows?
            suggested_facet_sql = """
                select date({column}) from (
                    select * from ({sql}) limit 100
                ) where {column} glob "????-??-*"
            """.format(
                column=escape_sqlite(column), sql=self.sql
            )
            try:
                results = await self.ds.execute(
                    self.database,
                    suggested_facet_sql,
                    self.params,
                    truncate=False,
                    custom_time_limit=self.ds.setting("facet_suggest_time_limit_ms"),
                    log_sql_errors=False,
                )
                values = tuple(r[0] for r in results.rows)
                if any(values):
                    suggested_facets.append(
                        {
                            "name": column,
                            "type": "date",
                            "toggle_url": self.ds.absolute_url(
                                self.request,
                                self.ds.urls.path(
                                    path_with_added_args(
                                        self.request, {"_facet_date": column}
                                    )
                                ),
                            ),
                        }
                    )
            except (QueryInterrupted, sqlite3.OperationalError):
                continue
        return suggested_facets

    async def facet_results(self):
        facet_results = []
        facets_timed_out = []
        args = dict(self.get_querystring_pairs())
        facet_size = self.get_facet_size()
        for source_and_config in self.get_configs():
            config = source_and_config["config"]
            source = source_and_config["source"]
            column = config.get("column") or config["simple"]
            # TODO: does this query break if inner sql produces value or count columns?
            facet_sql = """
                select date({col}) as value, count(*) as count from (
                    {sql}
                )
                where date({col}) is not null
                group by date({col}) order by count desc, value limit {limit}
            """.format(
                col=escape_sqlite(column), sql=self.sql, limit=facet_size + 1
            )
            try:
                facet_rows_results = await self.ds.execute(
                    self.database,
                    facet_sql,
                    self.params,
                    truncate=False,
                    custom_time_limit=self.ds.setting("facet_time_limit_ms"),
                )
                facet_results_values = []
                facet_results.append(
                    {
                        "name": column,
                        "type": self.type,
                        "results": facet_results_values,
                        "hideable": source != "metadata",
                        "toggle_url": path_with_removed_args(
                            self.request, {"_facet_date": column}
                        ),
                        "truncated": len(facet_rows_results) > facet_size,
                    }
                )
                facet_rows = facet_rows_results.rows[:facet_size]
                for row in facet_rows:
                    selected = str(args.get(f"{column}__date")) == str(row["value"])
                    if selected:
                        toggle_path = path_with_removed_args(
                            self.request, {f"{column}__date": str(row["value"])}
                        )
                    else:
                        toggle_path = path_with_added_args(
                            self.request, {f"{column}__date": row["value"]}
                        )
                    facet_results_values.append(
                        {
                            "value": row["value"],
                            "label": row["value"],
                            "count": row["count"],
                            "toggle_url": self.ds.absolute_url(
                                self.request, toggle_path
                            ),
                            "selected": selected,
                        }
                    )
            except QueryInterrupted:
                facets_timed_out.append(column)

        return facet_results, facets_timed_out
