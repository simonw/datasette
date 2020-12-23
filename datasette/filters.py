import json
import numbers

from .utils import detect_json1, escape_sqlite


class Filter:
    key = None
    display = None
    no_argument = False

    def where_clause(self, table, column, value, param_counter):
        raise NotImplementedError

    def human_clause(self, column, value):
        raise NotImplementedError


class TemplatedFilter(Filter):
    def __init__(
        self,
        key,
        display,
        sql_template,
        human_template,
        format="{}",
        numeric=False,
        no_argument=False,
    ):
        self.key = key
        self.display = display
        self.sql_template = sql_template
        self.human_template = human_template
        self.format = format
        self.numeric = numeric
        self.no_argument = no_argument

    def where_clause(self, table, column, value, param_counter):
        converted = self.format.format(value)
        if self.numeric and converted.isdigit():
            converted = int(converted)
        if self.no_argument:
            kwargs = {"c": column}
            converted = None
        else:
            kwargs = {"c": column, "p": f"p{param_counter}", "t": table}
        return self.sql_template.format(**kwargs), converted

    def human_clause(self, column, value):
        if callable(self.human_template):
            template = self.human_template(column, value)
        else:
            template = self.human_template
        if self.no_argument:
            return template.format(c=column)
        else:
            return template.format(c=column, v=value)


class InFilter(Filter):
    key = "in"
    display = "in"

    def split_value(self, value):
        if value.startswith("["):
            return json.loads(value)
        else:
            return [v.strip() for v in value.split(",")]

    def where_clause(self, table, column, value, param_counter):
        values = self.split_value(value)
        params = [f":p{param_counter + i}" for i in range(len(values))]
        sql = f"{escape_sqlite(column)} in ({', '.join(params)})"
        return sql, values

    def human_clause(self, column, value):
        return f"{column} in {json.dumps(self.split_value(value))}"


class NotInFilter(InFilter):
    key = "notin"
    display = "not in"

    def where_clause(self, table, column, value, param_counter):
        values = self.split_value(value)
        params = [f":p{param_counter + i}" for i in range(len(values))]
        sql = f"{escape_sqlite(column)} not in ({', '.join(params)})"
        return sql, values

    def human_clause(self, column, value):
        return f"{column} not in {json.dumps(self.split_value(value))}"


class Filters:
    _filters = (
        [
            # key, display, sql_template, human_template, format=, numeric=, no_argument=
            TemplatedFilter(
                "exact",
                "=",
                '"{c}" = :{p}',
                lambda c, v: "{c} = {v}" if v.isdigit() else '{c} = "{v}"',
            ),
            TemplatedFilter(
                "not",
                "!=",
                '"{c}" != :{p}',
                lambda c, v: "{c} != {v}" if v.isdigit() else '{c} != "{v}"',
            ),
            TemplatedFilter(
                "contains",
                "contains",
                '"{c}" like :{p}',
                '{c} contains "{v}"',
                format="%{}%",
            ),
            TemplatedFilter(
                "endswith",
                "ends with",
                '"{c}" like :{p}',
                '{c} ends with "{v}"',
                format="%{}",
            ),
            TemplatedFilter(
                "startswith",
                "starts with",
                '"{c}" like :{p}',
                '{c} starts with "{v}"',
                format="{}%",
            ),
            TemplatedFilter("gt", ">", '"{c}" > :{p}', "{c} > {v}", numeric=True),
            TemplatedFilter(
                "gte", "\u2265", '"{c}" >= :{p}', "{c} \u2265 {v}", numeric=True
            ),
            TemplatedFilter("lt", "<", '"{c}" < :{p}', "{c} < {v}", numeric=True),
            TemplatedFilter(
                "lte", "\u2264", '"{c}" <= :{p}', "{c} \u2264 {v}", numeric=True
            ),
            TemplatedFilter("like", "like", '"{c}" like :{p}', '{c} like "{v}"'),
            TemplatedFilter(
                "notlike", "not like", '"{c}" not like :{p}', '{c} not like "{v}"'
            ),
            TemplatedFilter("glob", "glob", '"{c}" glob :{p}', '{c} glob "{v}"'),
            InFilter(),
            NotInFilter(),
        ]
        + (
            [
                TemplatedFilter(
                    "arraycontains",
                    "array contains",
                    """rowid in (
            select {t}.rowid from {t}, json_each({t}.{c}) j
            where j.value = :{p}
        )""",
                    '{c} contains "{v}"',
                ),
                TemplatedFilter(
                    "arraynotcontains",
                    "array does not contain",
                    """rowid not in (
            select {t}.rowid from {t}, json_each({t}.{c}) j
            where j.value = :{p}
        )""",
                    '{c} does not contain "{v}"',
                ),
            ]
            if detect_json1()
            else []
        )
        + [
            TemplatedFilter(
                "date", "date", 'date("{c}") = :{p}', '"{c}" is on date {v}'
            ),
            TemplatedFilter(
                "isnull", "is null", '"{c}" is null', "{c} is null", no_argument=True
            ),
            TemplatedFilter(
                "notnull",
                "is not null",
                '"{c}" is not null',
                "{c} is not null",
                no_argument=True,
            ),
            TemplatedFilter(
                "isblank",
                "is blank",
                '("{c}" is null or "{c}" = "")',
                "{c} is blank",
                no_argument=True,
            ),
            TemplatedFilter(
                "notblank",
                "is not blank",
                '("{c}" is not null and "{c}" != "")',
                "{c} is not blank",
                no_argument=True,
            ),
        ]
    )
    _filters_by_key = {f.key: f for f in _filters}

    def __init__(self, pairs, units=None, ureg=None):
        if units is None:
            units = {}
        self.pairs = pairs
        self.units = units
        self.ureg = ureg

    def lookups(self):
        """Yields (lookup, display, no_argument) pairs"""
        for filter in self._filters:
            yield filter.key, filter.display, filter.no_argument

    def human_description_en(self, extra=None):
        bits = []
        if extra:
            bits.extend(extra)
        for column, lookup, value in self.selections():
            filter = self._filters_by_key.get(lookup, None)
            if filter:
                bits.append(filter.human_clause(column, value))
        # Comma separated, with an ' and ' at the end
        and_bits = []
        commas, tail = bits[:-1], bits[-1:]
        if commas:
            and_bits.append(", ".join(commas))
        if tail:
            and_bits.append(tail[0])
        s = " and ".join(and_bits)
        if not s:
            return ""
        return f"where {s}"

    def selections(self):
        """Yields (column, lookup, value) tuples"""
        for key, value in self.pairs:
            if "__" in key:
                column, lookup = key.rsplit("__", 1)
            else:
                column = key
                lookup = "exact"
            yield column, lookup, value

    def has_selections(self):
        return bool(self.pairs)

    def convert_unit(self, column, value):
        """If the user has provided a unit in the query, convert it into the column unit, if present."""
        if column not in self.units:
            return value

        # Try to interpret the value as a unit
        value = self.ureg(value)
        if isinstance(value, numbers.Number):
            # It's just a bare number, assume it's the column unit
            return value

        column_unit = self.ureg(self.units[column])
        return value.to(column_unit).magnitude

    def build_where_clauses(self, table):
        sql_bits = []
        params = {}
        i = 0
        for column, lookup, value in self.selections():
            filter = self._filters_by_key.get(lookup, None)
            if filter:
                sql_bit, param = filter.where_clause(
                    table, column, self.convert_unit(column, value), i
                )
                sql_bits.append(sql_bit)
                if param is not None:
                    if not isinstance(param, list):
                        param = [param]
                    for individual_param in param:
                        param_id = f"p{i}"
                        params[param_id] = individual_param
                        i += 1
        return sql_bits, params
