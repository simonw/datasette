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
    # Given a request and the metadata configuration for a table, return
    # a dictionary of selected facets, their lists of configs and for each
    # config whether it came from the request or the metadata.
    #
    #   return {type: [
    #       {"source": "metadata", "config": config1},
    #       {"source": "request", "config": config2}]}
    facet_configs = {}
    table_metadata = table_metadata or {}
    metadata_facets = table_metadata.get("facets", [])
    for metadata_config in metadata_facets:
        if isinstance(metadata_config, str):
            type = "column"
            metadata_config = {"simple": metadata_config}
        else:
            # This should have a single key and a single value
            assert (
                len(metadata_config.values()) == 1
            ), "Metadata config dicts should be {type: config}"
            type, metadata_config = metadata_config.items()[0]
            if isinstance(metadata_config, str):
                metadata_config = {"simple": metadata_config}
        facet_configs.setdefault(type, []).append(
            {"source": "metadata", "config": metadata_config}
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
                # The value is the config - either JSON or not
                if value.startswith("{"):
                    config = json.loads(value)
                else:
                    config = {"simple": value}
                facet_configs.setdefault(type, []).append(
                    {"source": "request", "config": config}
                )
    return facet_configs


@hookimpl
def register_facet_classes():
    classes = [ColumnFacet]
    if detect_json1():
        classes.append(ArrayFacet)
    return classes


class Facet:
    type = None

    def __init__(
        self,
        ds,
        request,
        database,
        sql=None,
        table=None,
        params=None,
        metadata=None,
        row_count=None,
    ):
        assert table or sql, "Must provide either table= or sql="
        self.ds = ds
        self.request = request
        self.database = database
        # For foreign key expansion. Can be None for e.g. canned SQL queries:
        self.table = table
        self.sql = sql or "select * from [{}]".format(table)
        self.params = params or []
        self.metadata = metadata
        # row_count can be None, in which case we calculate it ourselves:
        self.row_count = row_count

    def get_configs(self):
        configs = load_facet_configs(self.request, self.metadata)
        return configs.get(self.type) or []

    def get_querystring_pairs(self):
        # ?_foo=bar&_foo=2&empty= becomes:
        # [('_foo', 'bar'), ('_foo', '2'), ('empty', '')]
        return urllib.parse.parse_qsl(self.request.query_string, keep_blank_values=True)

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
                self.database, "select * from ({}) limit 0".format(sql), params or []
            )
        ).columns

    async def get_row_count(self):
        if self.row_count is None:
            self.row_count = (
                await self.ds.execute(
                    self.database,
                    "select count(*) from ({})".format(self.sql),
                    self.params,
                )
            ).rows[0][0]
        return self.row_count


class ColumnFacet(Facet):
    type = "column"

    async def suggest(self):
        row_count = await self.get_row_count()
        columns = await self.get_columns(self.sql, self.params)
        facet_size = self.ds.config("default_facet_size")
        suggested_facets = []
        already_enabled = [c["config"]["simple"] for c in self.get_configs()]
        for column in columns:
            if column in already_enabled:
                continue
            suggested_facet_sql = """
                select distinct {column} from (
                    {sql}
                ) where {column} is not null
                limit {limit}
            """.format(
                column=escape_sqlite(column), sql=self.sql, limit=facet_size + 1
            )
            distinct_values = None
            try:
                distinct_values = await self.ds.execute(
                    self.database,
                    suggested_facet_sql,
                    self.params,
                    truncate=False,
                    custom_time_limit=self.ds.config("facet_suggest_time_limit_ms"),
                )
                num_distinct_values = len(distinct_values)
                if (
                    num_distinct_values
                    and num_distinct_values > 1
                    and num_distinct_values <= facet_size
                    and num_distinct_values < row_count
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

    async def facet_results(self):
        facet_results = {}
        facets_timed_out = []

        qs_pairs = self.get_querystring_pairs()

        facet_size = self.ds.config("default_facet_size")
        for source_and_config in self.get_configs():
            config = source_and_config["config"]
            source = source_and_config["source"]
            column = config.get("column") or config["simple"]
            facet_sql = """
                select {col} as value, count(*) as count from (
                    {sql}
                )
                where {col} is not null
                group by {col} order by count desc limit {limit}
            """.format(
                col=escape_sqlite(column), sql=self.sql, limit=facet_size + 1
            )
            try:
                facet_rows_results = await self.ds.execute(
                    self.database,
                    facet_sql,
                    self.params,
                    truncate=False,
                    custom_time_limit=self.ds.config("facet_time_limit_ms"),
                )
                facet_results_values = []
                facet_results[column] = {
                    "name": column,
                    "type": self.type,
                    "hideable": source != "metadata",
                    "toggle_url": path_with_removed_args(
                        self.request, {"_facet": column}
                    ),
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


class ArrayFacet(Facet):
    type = "array"

    async def suggest(self):
        columns = await self.get_columns(self.sql, self.params)
        suggested_facets = []
        already_enabled = [c["config"]["simple"] for c in self.get_configs()]
        for column in columns:
            if column in already_enabled:
                continue
            # Is every value in this column either null or a JSON array?
            suggested_facet_sql = """
                select distinct json_type({column})
                from ({sql})
            """.format(
                column=escape_sqlite(column), sql=self.sql
            )
            try:
                results = await self.ds.execute(
                    self.database,
                    suggested_facet_sql,
                    self.params,
                    truncate=False,
                    custom_time_limit=self.ds.config("facet_suggest_time_limit_ms"),
                    log_sql_errors=False,
                )
                types = tuple(r[0] for r in results.rows)
                if types in (("array",), ("array", None)):
                    suggested_facets.append(
                        {
                            "name": column,
                            "type": "array",
                            "toggle_url": self.ds.absolute_url(
                                self.request,
                                path_with_added_args(
                                    self.request, {"_facet_array": column}
                                ),
                            ),
                        }
                    )
            except (InterruptedError, sqlite3.OperationalError):
                continue
        return suggested_facets

    async def facet_results(self):
        # self.configs should be a plain list of columns
        facet_results = {}
        facets_timed_out = []

        facet_size = self.ds.config("default_facet_size")
        for source_and_config in self.get_configs():
            config = source_and_config["config"]
            source = source_and_config["source"]
            column = config.get("column") or config["simple"]
            facet_sql = """
                select j.value as value, count(*) as count from (
                    {sql}
                ) join json_each({col}) j
                group by j.value order by count desc limit {limit}
            """.format(
                col=escape_sqlite(column), sql=self.sql, limit=facet_size + 1
            )
            try:
                facet_rows_results = await self.ds.execute(
                    self.database,
                    facet_sql,
                    self.params,
                    truncate=False,
                    custom_time_limit=self.ds.config("facet_time_limit_ms"),
                )
                facet_results_values = []
                facet_results[column] = {
                    "name": column,
                    "type": self.type,
                    "results": facet_results_values,
                    "hideable": source != "metadata",
                    "toggle_url": path_with_removed_args(
                        self.request, {"_facet_array": column}
                    ),
                    "truncated": len(facet_rows_results) > facet_size,
                }
                facet_rows = facet_rows_results.rows[:facet_size]
                pairs = self.get_querystring_pairs()
                for row in facet_rows:
                    value = str(row["value"])
                    selected = ("{}__arraycontains".format(column), value) in pairs
                    if selected:
                        toggle_path = path_with_removed_args(
                            self.request, {"{}__arraycontains".format(column): value}
                        )
                    else:
                        toggle_path = path_with_added_args(
                            self.request, {"{}__arraycontains".format(column): value}
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
            except InterruptedError:
                facets_timed_out.append(column)

        return facet_results, facets_timed_out
