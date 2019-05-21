"""
    A straghtforward set of utility classes for building SQL queries.

    This doesn't attempt to model every eventuality, so you can pass raw SQL
    into all parameters. Notably, it doesn't have enough information on the
    context of parameters to know whether to escape them, so you should either
    use utils.escape_sqlite or the Table object to do so.
"""
import copy
from .utils import escape_sqlite


class OperatorList(list):
    """ Represents a list of SQL clauses which will be combined with an
        operator (like AND/OR).
    """

    operator = None

    def __str__(self):
        join_str = " {} ".format(self.operator)
        return join_str.join(["({})".format(item) for item in self])


class And(OperatorList):
    operator = "AND"


class Or(OperatorList):
    operator = "OR"


class Table(object):
    def __init__(self, name, alias=None):
        self.name = name
        self.alias = alias

    def __str__(self):
        alias_clause = ""
        if self.alias:
            alias_clause = " AS {}".format(self.alias)
        return escape_sqlite(self.name) + alias_clause


def list_or_string(arg):
    if arg is None:
        return []
    if isinstance(arg, list):
        return arg
    return [arg]


class Select(object):
    """
        A SELECT query.
    """

    def __init__(
        self,
        fields=None,
        from_tables=None,
        where=None,
        order_by=None,
        group_by=None,
        limit=None,
        offset=None,
    ):
        self.fields = list_or_string(fields)
        self.from_tables = list_or_string(from_tables)
        if isinstance(where, OperatorList):
            self.where = where
        else:
            self.where = And(list_or_string(where))
        self.order_by = list_or_string(order_by)
        self.group_by = list_or_string(group_by)
        self.limit = limit
        self.offset = offset

    def generate(self):
        sql = "SELECT "

        if self.fields == []:
            sql += "*"
        else:
            sql += ", ".join(map(str, self.fields))

        if self.from_tables != []:
            sql += " FROM " + ", ".join(map(str, self.from_tables))

        if self.where != []:
            if not isinstance(self.where, OperatorList):
                self.where = And(self.where)

            sql += " WHERE " + str(self.where)

        if self.group_by != []:
            sql += " GROUP BY " + ", ".join(map(str, self.group_by))

        if self.order_by != []:
            sql += " ORDER BY " + ", ".join(map(str, self.order_by))

        if self.limit is not None:
            sql += " LIMIT " + str(self.limit)

        if self.offset is not None:
            sql += " OFFSET " + str(self.offset)

        return sql

    def count(self):
        """ Return a copy of this query which returns the count."""
        count_sql = self.copy()
        count_sql.fields = ["COUNT(*)"]
        count_sql.limit = None
        count_sql.offset = None
        return count_sql

    def copy(self):
        return copy.deepcopy(self)

    def __str__(self):
        return self.generate()


__all__ = [And, Or, Table, Select]
