from sanic.request import RequestParameters
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
    #metadata_facets = table_metadata.get("facets", [])
    #facets = metadata_facets[:]
    args = RequestParameters(
        urllib.parse.parse_qs(request.query_string, keep_blank_values=True)
    )
    for key, values in args.items():
        if key.startswith("_facet"):
            # Figure out the facet type
            if key == "_facet":
                type = "column"
            elif key.startswith("_facet_"):
                type = key[len("_facet_"):]
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
    return [ColumnFacet, ArrayFacet, ManyToManyFacet, DateFacet, EmojiFacet, PhrasesFacet]
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

    async def suggest(self, sql, params, filtered_table_rows_count):
        return []

    async def facet_results(self, sql, params):
        # returns ([results], [timed_out])
        # TODO: Include "hideable" with each one somehow, which indicates if it was
        # defined in metadata (in which case you cannot turn it off)
        raise NotImplementedError

    async def get_columns(self, sql, params=None):
        return (
            await self.ds.execute(
                self.database, "select * from ({}) limit 0".format(sql),
                params or []
            )
        ).columns


class ColumnFacet(Facet):
    type = "column"

    async def suggest(self, sql, params, filtered_table_rows_count):
        # Detect column names using the "limit 0" trick
        columns = await self.get_columns(sql, params)
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
                continue
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
        for config in (self.configs or []):
            column = config.get("column") or config["single"]
            # TODO: does this query break if inner sql produces value or count columns?
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

    async def suggest(self, sql, params, filtered_table_rows_count):
        # This is calculated based on foreign key relationships to this table
        # Are there any many-to-many tables pointing here?
        suggested_facets = []
        all_foreign_keys = await self.ds.execute_against_connection_in_thread(
            self.database, get_all_foreign_keys
        )
        if not all_foreign_keys.get(self.table):
            # It's probably a view
            return []
        incoming = all_foreign_keys[self.table]["incoming"]
        # Do any of these incoming tables have exactly two outgoing keys?
        for fk in incoming:
            other_table = fk["other_table"]
            other_table_outgoing_foreign_keys = all_foreign_keys[other_table]["outgoing"]
            if len(other_table_outgoing_foreign_keys) == 2:
                suggested_facets.append({
                    "name": other_table,
                    "type": "m2m",
                    "toggle_url": self.ds.absolute_url(
                        self.request, path_with_added_args(
                            self.request, {"_facet_m2m": other_table}
                        )
                    ),
                })
        return suggested_facets

    async def facet_results(self, *args, **kwargs):

        return [], []


class ArrayFacet(Facet):
    type = "array"

    async def suggest(self, sql, params, filtered_table_rows_count):
        columns = await self.get_columns(sql, params)
        suggested_facets = []
        for column in columns:
            # Is every value in this column either null or a JSON array?
            suggested_facet_sql = """
                select distinct json_type({column})
                from ({sql})
            """.format(
                column=escape_sqlite(column),
                sql=sql,
            )
            try:
                results = await self.ds.execute(
                    self.database, suggested_facet_sql, params,
                    truncate=False,
                    custom_time_limit=self.ds.config("facet_suggest_time_limit_ms"),
                    log_sql_errors=False,
                )
                types = tuple(r[0] for r in results.rows)
                if types in (
                    ("array",),
                    ("array", None)
                ):
                    suggested_facets.append({
                        "name": column,
                        "type": "array",
                        "toggle_url": self.ds.absolute_url(
                            self.request, path_with_added_args(
                                self.request, {"_facet_array": column}
                            )
                        ),
                    })
            except (InterruptedError, sqlite3.OperationalError):
                continue
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
        for config in (self.configs or []):
            column = config.get("column") or config["single"]
            facet_sql = """
                select j.value as value, count(*) as count from (
                    {sql}
                ) join json_each({col}) j
                group by j.value order by count desc limit {limit}
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
                for row in facet_rows:
                    selected = str(other_args.get(column)) == str(row["value"])
                    if selected:
                        toggle_path = path_with_removed_args(
                            self.request, {"{}__arraycontains".format(column): str(row["value"])}
                        )
                    else:
                        toggle_path = path_with_added_args(
                            self.request, {"{}__arraycontains".format(column): row["value"]}
                        )
                    facet_results_values.append({
                        "value": row["value"],
                        "label": row["value"],
                        "count": row["count"],
                        "toggle_url": self.ds.absolute_url(self.request, toggle_path),
                        "selected": selected,
                    })
            except InterruptedError:
                facets_timed_out.append(column)
    
        return facet_results, facets_timed_out



class DateFacet(Facet):
    type = "date"

    async def suggest(self, sql, params, filtered_table_rows_count):
        columns = await self.get_columns(sql, params)
        suggested_facets = []
        for column in columns:
            # Does this column contain any dates in the first 100 rows?
            suggested_facet_sql = """
                select date({column}) from (
                    {sql}
                ) where {column} glob "????-??-??" limit 100;
            """.format(
                column=escape_sqlite(column),
                sql=sql,
            )
            try:
                results = await self.ds.execute(
                    self.database, suggested_facet_sql, params,
                    truncate=False,
                    custom_time_limit=self.ds.config("facet_suggest_time_limit_ms"),
                    log_sql_errors=False,
                )
                values = tuple(r[0] for r in results.rows)
                if (any(values)):
                    suggested_facets.append({
                        "name": column,
                        "type": "date",
                        "toggle_url": self.ds.absolute_url(
                            self.request, path_with_added_args(
                                self.request, {"_facet_date": column}
                            )
                        ),
                    })
            except (InterruptedError, sqlite3.OperationalError):
                continue
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
        for config in (self.configs or []):
            column = config.get("column") or config["single"]
            # TODO: does this query break if inner sql produces value or count columns?
            facet_sql = """
                select date({col}) as value, count(*) as count from (
                    {sql}
                )
                where date({col}) is not null
                group by date({col}) order by count desc limit {limit}
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
                for row in facet_rows:
                    selected = str(other_args.get("{}__date".format(column))) == str(row["value"])
                    if selected:
                        toggle_path = path_with_removed_args(
                            self.request, {"{}__date".format(column): str(row["value"])}
                        )
                    else:
                        toggle_path = path_with_added_args(
                            self.request, {"{}__date".format(column): row["value"]}
                        )
                    facet_results_values.append({
                        "value": row["value"],
                        "label": row["value"],
                        "count": row["count"],
                        "toggle_url": self.ds.absolute_url(self.request, toggle_path),
                        "selected": selected,
                    })
            except InterruptedError:
                facets_timed_out.append(column)
    
        return facet_results, facets_timed_out



class PhrasesFacet(Facet):
    type = "phrases"

    async def facet_results(self, sql, params):
        # Hmm... for this one we actually need the column name(s) AND the word list
        # Current design supports one of the following:
        #   ?_facet_phrases=column:word1,word2,word3
        # which means we could support multiple columns like so:
        #   ?_facet_phrases=column1:column2:word1,word2,word3
        # As JSON:
        #   ?_facet_phrases={"columns":["column1","column2"],"phrases":["word1","word2"]}
        # Urgh, the filter option when one is selected is going to be pretty nasty
        facet_results = {}
        facets_timed_out = []

        facet_size = self.ds.config("default_facet_size")
        for config in (self.configs or []):
            if isinstance(config, dict) and "single" in config:
                config = config["single"]
            if isinstance(config, str):
                columns = config.rsplit(":", 1)[0].split(":")
                phrases = config.rsplit(":", 1)[1].split(",")
            else:
                columns = config["columns"]
                phases = config["phrases"]
            # FOR THE MOMENT only support one column
            column = columns[0]
            facet_sql = """
                select count(*) as count, j.value as value
                from (
                    select extract_phrases_json({col}, '{json_phrases}') as a from (
                        {sql}
                    )
                )
                join json_each(a) j
                group by j.value order by count desc limit {limit}
            """.format(
                col=escape_sqlite(column),
                sql=sql,
                # TODO: this will break if any phrases contain '
                json_phrases=json.dumps(phrases),
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
                for row in facet_rows:
                    facet_results_values.append({
                        "value": row["value"],
                        "label": row["value"],
                        "count": row["count"],
                        # TODO: toggle_url for selected
                        "toggle_url": "", # self.ds.absolute_url(self.request, toggle_path),
                        # TODO: identify selected
                        "selected": False,
                    })
            except InterruptedError:
                facets_timed_out.append(column)
    
        return facet_results, facets_timed_out


class EmojiFacet(Facet):
    type = "emoji"

    async def suggest(self, sql, params, filtered_table_rows_count):
        columns = await self.get_columns(sql, params)
        suggested_facets = []
        for column in columns:
            # Is every value in this column either null or a JSON array?
            suggested_facet_sql = """
                select extract_emoji({column}) as emojis
                from ({sql}) where emojis != "" limit 1
            """.format(
                column=escape_sqlite(column),
                sql=sql,
            )
            try:
                results = await self.ds.execute(
                    self.database, suggested_facet_sql, params,
                    truncate=False,
                    custom_time_limit=self.ds.config("facet_suggest_time_limit_ms"),
                    log_sql_errors=True,
                )
                if results.rows:
                    suggested_facets.append({
                        "name": column,
                        "type": "emoji",
                        "toggle_url": self.ds.absolute_url(
                            self.request, path_with_added_args(
                                self.request, {"_facet_emoji": column}
                            )
                        ),
                    })
            except (InterruptedError, sqlite3.OperationalError) as e:
                continue
        return suggested_facets

    async def facet_results(self, *args, **kwargs):
        return [], []


@hookimpl
def prepare_connection(conn):
    conn.create_function("extract_emoji", 1, extract_emoji)
    conn.create_function("extract_emoji_json", 1, extract_emoji_json)
    conn.create_function("extract_phrases_json", 2, extract_phrases_json)
    conn.create_function("extract_name_json", 1, extract_name_json)
    conn.create_function("decode_punycode", 1, decode_punycode)


import json

def extract_emoji(s):
    if not isinstance(s, str):
        return ""
    try:
        return "".join(emoji_re.findall(s))
    except Exception as e:
        print(e)
        raise


def extract_emoji_json(s):
    try:
        if not isinstance(s, str):
            return "[]"
        return json.dumps(list(set([
            c.encode("punycode").decode("latin1") for c in emoji_re.findall(s)
        ])))
    except Exception as e:
        print(e)
        raise


def extract_name_json(s):
    try:
        if not isinstance(s, str):
            return "[]"
        return json.dumps(list(set([m.group(0) for m in name_re.finditer(s)])))
    except Exception as e:
        print(e)
        raise


def extract_phrases_json(s, phrases):
    # phrases is a '["json", "list", "of", "phrases"]'
    if not isinstance(s, str):
        return "[]"
    phrases_list = json.loads(phrases)
    # I tried caching the regex but the performance boost was negligible
    r = re.compile(r"\b{}\b".format("|".join(phrases_list)), re.I)
    return json.dumps(list(set(w.lower() for w in r.findall(s))))


name_re = re.compile("([A-Z][a-z]+)+( [A-Z][a-z]+)")



def decode_punycode(s):
    return s.encode("latin1").decode("punycode")


emoji_re = re.compile(
    "[\xa9\xae\u203c\u2049\u2122\u2139\u2194-\u2199\u21a9-\u21aa\u231a-\u231b"
    "\u2328\u23cf\u23e9-\u23f3\u23f8-\u23fa\u24c2\u25aa-\u25ab\u25b6\u25c0"
    "\u25fb-\u25fe\u2600-\u2604\u260e\u2611\u2614-\u2615\u2618\u261d\u2620"
    "\u2622-\u2623\u2626\u262a\u262e-\u262f\u2638-\u263a\u2640\u2642\u2648-"
    "\u2653\u2660\u2663\u2665-\u2666\u2668\u267b\u267f\u2692-\u2697\u2699"
    "\u269b-\u269c\u26a0-\u26a1\u26aa-\u26ab\u26b0-\u26b1\u26bd-\u26be\u26c4-"
    "\u26c5\u26c8\u26ce\u26cf\u26d1\u26d3-\u26d4\u26e9-\u26ea\u26f0-\u26f5"
    "\u26f7-\u26fa\u26fd\u2702\u2705\u2708-\u2709\u270a-\u270b\u270c-\u270d"
    "\u270f\u2712\u2714\u2716\u271d\u2721\u2728\u2733-\u2734\u2744\u2747\u274c"
    "\u274e\u2753-\u2755\u2757\u2763-\u2764\u2795-\u2797\u27a1\u27b0\u27bf"
    "\u2934-\u2935\u2b05-\u2b07\u2b1b-\u2b1c\u2b50\u2b55\u3030\u303d\u3297"
    "\u3299\U0001f004\U0001f0cf\U0001f170-\U0001f171\U0001f17e\U0001f17f"
    "\U0001f18e\U0001f191-\U0001f19a\U0001f1e6-\U0001f1ff\U0001f201-\U0001f202"
    "\U0001f21a\U0001f22f\U0001f232-\U0001f23a\U0001f250-\U0001f251\U0001f300-"
    "\U0001f320\U0001f321\U0001f324-\U0001f32c\U0001f32d-\U0001f32f\U0001f330-"
    "\U0001f335\U0001f336\U0001f337-\U0001f37c\U0001f37d\U0001f37e-\U0001f37f"
    "\U0001f380-\U0001f393\U0001f396-\U0001f397\U0001f399-\U0001f39b\U0001f39e-"
    "\U0001f39f\U0001f3a0-\U0001f3c4\U0001f3c5\U0001f3c6-\U0001f3ca\U0001f3cb-"
    "\U0001f3ce\U0001f3cf-\U0001f3d3\U0001f3d4-\U0001f3df\U0001f3e0-\U0001f3f0"
    "\U0001f3f3-\U0001f3f5\U0001f3f7\U0001f3f8-\U0001f3ff\U0001f400-\U0001f43e"
    "\U0001f43f\U0001f440\U0001f441\U0001f442-\U0001f4f7\U0001f4f8\U0001f4f9-"
    "\U0001f4fc\U0001f4fd\U0001f4ff\U0001f500-\U0001f53d\U0001f549-\U0001f54a"
    "\U0001f54b-\U0001f54e\U0001f550-\U0001f567\U0001f56f-\U0001f570\U0001f573-"
    "\U0001f579\U0001f57a\U0001f587\U0001f58a-\U0001f58d\U0001f590\U0001f595-"
    "\U0001f596\U0001f5a4\U0001f5a5\U0001f5a8\U0001f5b1-\U0001f5b2\U0001f5bc"
    "\U0001f5c2-\U0001f5c4\U0001f5d1-\U0001f5d3\U0001f5dc-\U0001f5de\U0001f5e1"
    "\U0001f5e3\U0001f5e8\U0001f5ef\U0001f5f3\U0001f5fa\U0001f5fb-\U0001f5ff"
    "\U0001f600\U0001f601-\U0001f610\U0001f611\U0001f612-\U0001f614\U0001f615"
    "\U0001f616\U0001f617\U0001f618\U0001f619\U0001f61a\U0001f61b\U0001f61c-"
    "\U0001f61e\U0001f61f\U0001f620-\U0001f625\U0001f626-\U0001f627\U0001f628-"
    "\U0001f62b\U0001f62c\U0001f62d\U0001f62e-\U0001f62f\U0001f630-\U0001f633"
    "\U0001f634\U0001f635-\U0001f640\U0001f641-\U0001f642\U0001f643-\U0001f644"
    "\U0001f645-\U0001f64f\U0001f680-\U0001f6c5\U0001f6cb-\U0001f6cf\U0001f6d0"
    "\U0001f6d1-\U0001f6d2\U0001f6e0-\U0001f6e5\U0001f6e9\U0001f6eb-\U0001f6ec"
    "\U0001f6f0\U0001f6f3\U0001f6f4-\U0001f6f6\U0001f6f7-\U0001f6f8\U0001f910-"
    "\U0001f918\U0001f919-\U0001f91e\U0001f91f\U0001f920-\U0001f927\U0001f928-"
    "\U0001f92f\U0001f930\U0001f931-\U0001f932\U0001f933-\U0001f93a\U0001f93c-"
    "\U0001f93e\U0001f940-\U0001f945\U0001f947-\U0001f94b\U0001f94c\U0001f950-"
    "\U0001f95e\U0001f95f-\U0001f96b\U0001f980-\U0001f984\U0001f985-\U0001f991"
    "\U0001f992-\U0001f997\U0001f9c0\U0001f9d0-\U0001f9e6]"
)
