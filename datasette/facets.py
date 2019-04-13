from sanic.request import RequestParameters
import urllib
from datasette import hookimpl
from datasette.utils import (
    escape_sqlite,
    path_with_added_args,
    path_with_removed_args,
    detect_json1
)


@hookimpl
def register_facet_classes():
    return [ColumnFacet]
    # classes = [ColumnFacet, ManyToManyFacet]
    # if detect_json1():
    #     classes.append(ArrayFacet)
    # return classes


class Facet:
    type = None

    def __init__(self, ds, request, database, table, configs):
        self.ds = ds
        self.request = request
        self.database = database
        self.table = table # can be None
        self.configs = configs

    async def suggest(self, sql, params):
        raise NotImplementedError

    async def facet_results(self, sql, params):
        # returns ([results], [timed_out])
        raise NotImplementedError


class ColumnFacet(Facet):
    # This is the default so type=""
    type = ""

    async def suggest(self, sql, params, filtered_table_rows_count):
        # Detect column names
        columns = (
            await self.ds.execute(
                self.database, "select * from ({}) limit 0".format(sql),
                params
            )
        ).columns
        facet_size = self.ds.config("default_facet_size")
        suggested_facets = []
        for column in columns:
            suggested_facet_sql = '''
                select distinct {column} from (
                    {sql}
                ) where {column} is not null
                limit {limit}
            '''.format(
                column=escape_sqlite(column),
                sql=sql,
                limit=facet_size+1
            )
            distinct_values = None
            try:
                distinct_values = await self.ds.execute(
                    self.database, suggested_facet_sql, params,
                    truncate=False,
                    custom_time_limit=self.ds.config("facet_suggest_time_limit_ms"),
                )
                num_distinct_values = len(distinct_values)
                if (
                    num_distinct_values and
                    num_distinct_values > 1 and
                    num_distinct_values <= facet_size and
                    num_distinct_values < filtered_table_rows_count
                ):
                    suggested_facets.append({
                        'name': column,
                        'toggle_url': self.ds.absolute_url(
                            self.request, path_with_added_args(
                                self.request, {"_facet": column}
                            )
                        ),
                    })
            except InterruptedError:
                pass
        return suggested_facets

    async def facet_results(self, sql, params):
        # self.configs should be a plain list of columns
        facet_results = {}
        facets_timed_out = []

        # TODO: refactor this
        args = RequestParameters(
            urllib.parse.parse_qs(self.request.query_string, keep_blank_values=True)
        )
        other_args = {}
        for key, value in args.items():
            if key.startswith("_") and "__" not in key:
                pass
            else:
                other_args[key] = value[0]

        facet_size = self.ds.config("default_facet_size")
        for column in self.configs:
            facet_sql = """
                select {col} as value, count(*) as count from (
                    {sql}
                )
                where {col} is not null
                group by {col} order by count desc limit {limit}
            """.format(
                col=escape_sqlite(column),
                sql=sql,
                limit=facet_size+1,
            )
            try:
                facet_rows_results = await self.ds.execute(
                    self.database, facet_sql, params,
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
                    expanded = (await self.ds.expand_foreign_keys(
                        self.database, self.table, column, values
                    ))
                else:
                    expanded = {}
                for row in facet_rows:
                    selected = str(other_args.get(column)) == str(row["value"])
                    if selected:
                        toggle_path = path_with_removed_args(
                            self.request, {column: str(row["value"])}
                        )
                    else:
                        toggle_path = path_with_added_args(
                            self.request, {column: row["value"]}
                        )
                    facet_results_values.append({
                        "value": row["value"],
                        "label": expanded.get(
                            (column, row["value"]),
                            row["value"]
                        ),
                        "count": row["count"],
                        "toggle_url": self.ds.absolute_url(self.request, toggle_path),
                        "selected": selected,
                    })
            except InterruptedError:
                facets_timed_out.append(column)
    
        return facet_results, facets_timed_out


class ManyToManyFacet(Facet):
    type = "m2m"


class ArrayFacet(Facet):
    type = "array"
