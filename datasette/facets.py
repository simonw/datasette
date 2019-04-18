import json
import urllib
import re
from datasette import hookimpl
from datasette.utils import (
    escape_sqlite,
    get_all_foreign_keys,
    path_with_added_args,
    path_with_removed_args,
    detect_json1,
    InterruptedError,
    InvalidSql,
    sqlite3,
)


def load_facet_configs(request, table_metadata):
    # Given a request and this tables metadata, return
    # a dict of selected facets and their configs
    #   return {type, [config1, config2]...}
    facet_configs = {}
    # metadata_facets = table_metadata.get("facets", [])
    # facets = metadata_facets[:]
    qs_pairs = urllib.parse.parse_qs(request.query_string, keep_blank_values=True)
    for key, values in qs_pairs.items():
        if key.startswith("_facet"):
            # Figure out the facet type
            if key == "_facet":
                type = "column"
            elif key.startswith("_facet_"):
                type = key[len("_facet_") :]
            for value in values:
                # The value is the config - either JSON or not
                if value.startswith("{"):
                    config = json.loads(value)
                else:
                    config = {"single": value}
                facet_configs.setdefault(type, []).append(config)
    return facet_configs


@hookimpl
def register_facet_classes():
    return [ColumnFacet]


class Facet:
    type = None

    def __init__(self, ds, request, database, table, configs):
        self.ds = ds
        self.request = request
        self.database = database
        self.table = (
            table
        )  # For foreign key expansion. Can be None for e.g. canned SQL queries
        self.configs = configs

    def get_querystring_pairs(self):
        # ?_foo=bar&_foo=2&empty= becomes:
        # [('_foo', 'bar'), ('_foo', '2'), ('empty', '')]
        return urllib.parse.parse_qsl(self.request.query_string, keep_blank_values=True)

    async def suggest(self, sql, params, filtered_table_rows_count):
        return []

    async def facet_results(self, sql, params):
        # returns ([results], [timed_out])
        # TODO: Include "hideable" with each one somehow, which indicates if it was
        # defined in metadata (in which case you cannot turn it off)
        raise NotImplementedError

    async def get_columns(self, sql, params=None):
        # Detect column names using the "limit 0" trick
        return (
            await self.ds.execute(
                self.database, "select * from ({}) limit 0".format(sql), params or []
            )
        ).columns


class ColumnFacet(Facet):
    type = "column"

    async def suggest(self, sql, params, filtered_table_rows_count):
        columns = await self.get_columns(sql, params)
        facet_size = self.ds.config("default_facet_size")
        suggested_facets = []
        for column in columns:
            suggested_facet_sql = """
                select distinct {column} from (
                    {sql}
                ) where {column} is not null
                limit {limit}
            """.format(
                column=escape_sqlite(column), sql=sql, limit=facet_size + 1
            )
            distinct_values = None
            try:
                distinct_values = await self.ds.execute(
                    self.database,
                    suggested_facet_sql,
                    params,
                    truncate=False,
                    custom_time_limit=self.ds.config("facet_suggest_time_limit_ms"),
                )
                num_distinct_values = len(distinct_values)
                if (
                    num_distinct_values
                    and num_distinct_values > 1
                    and num_distinct_values <= facet_size
                    and num_distinct_values < filtered_table_rows_count
                ):
                    suggested_facets.append(
                        {
                            "name": column,
                            "toggle_url": self.ds.absolute_url(
                                self.request,
                                path_with_added_args(self.request, {"_facet": column}),
                            ),
                        }
                    )
            except InterruptedError:
                continue
        return suggested_facets

    async def facet_results(self, sql, params):
        facet_results = {}
        facets_timed_out = []

        qs_pairs = self.get_querystring_pairs()

        facet_size = self.ds.config("default_facet_size")
        for config in self.configs or []:
            column = config.get("column") or config["single"]
            facet_sql = """
                select {col} as value, count(*) as count from (
                    {sql}
                )
                where {col} is not null
                group by {col} order by count desc limit {limit}
            """.format(
                col=escape_sqlite(column), sql=sql, limit=facet_size + 1
            )
            try:
                facet_rows_results = await self.ds.execute(
                    self.database,
                    facet_sql,
                    params,
                    truncate=False,
                    custom_time_limit=self.ds.config("facet_time_limit_ms"),
                )
                facet_results_values = []
                facet_results[column] = {
                    "name": column,
                    "results": facet_results_values,
                    "truncated": len(facet_rows_results) > facet_size,
                }
                facet_rows = facet_rows_results.rows[:facet_size]
                if self.table:
                    # Attempt to expand foreign keys into labels
                    values = [row["value"] for row in facet_rows]
                    expanded = await self.ds.expand_foreign_keys(
                        self.database, self.table, column, values
                    )
                else:
                    expanded = {}
                for row in facet_rows:
                    selected = (column, str(row["value"])) in qs_pairs
                    if selected:
                        toggle_path = path_with_removed_args(
                            self.request, {column: str(row["value"])}
                        )
                    else:
                        toggle_path = path_with_added_args(
                            self.request, {column: row["value"]}
                        )
                    facet_results_values.append(
                        {
                            "value": row["value"],
                            "label": expanded.get((column, row["value"]), row["value"]),
                            "count": row["count"],
                            "toggle_url": self.ds.absolute_url(
                                self.request, toggle_path
                            ),
                            "selected": selected,
                        }
                    )
            except InterruptedError:
                facets_timed_out.append(column)

        return facet_results, facets_timed_out
