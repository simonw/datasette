import math
import urllib

from .utils import (
    PrefixedUrlString,
    path_with_format,
    replace_named_parameters,
    tilde_encode,
)


class Urls:
    def __init__(self, ds):
        self.ds = ds

    def path(self, path, format=None):
        if not isinstance(path, PrefixedUrlString):
            path = path.removeprefix("/")
            path = self.ds.setting("base_url") + path
        if format is not None:
            path = path_with_format(path=path, format=format)
        return PrefixedUrlString(path)

    def instance(self, format=None):
        return self.path("", format=format)

    def static(self, path):
        return self.path(f"-/static/{path}")

    def static_plugins(self, plugin, path):
        return self.path(f"-/static-plugins/{plugin}/{path}")

    def logout(self):
        return self.path("-/logout")

    def database(self, database, format=None):
        db = self.ds.get_database(database)
        return self.path(tilde_encode(db.route), format=format)

    def database_query(self, database, sql, format=None, *, params=None):
        params = dict(params or {})
        replacements = {}
        for name, value in params.items():
            # A numeric parameter such as 42 becomes the string "42" in a URL.
            # CAST turns it back into a number, but also tells SQLite to try converting
            # the other side of a comparison to a number. Wrapping CAST in unary +
            # keeps the number while removing that extra conversion rule, so the
            # comparison behaves as it did before.
            if isinstance(value, float) and not math.isfinite(value):
                if math.isnan(value):
                    replacements[name] = "NULL"
                else:
                    replacements[name] = "1e999" if value > 0 else "(-1e999)"
            elif isinstance(value, (int, float)):
                sql_type = "integer" if isinstance(value, int) else "real"
                replacements[name] = f"(+cast(:{name} as {sql_type}))"
                if isinstance(value, bool):
                    params[name] = int(value)
            elif value is None:
                replacements[name] = "NULL"
            elif isinstance(value, bytes):
                replacements[name] = f"X'{value.hex()}'"
        sql = replace_named_parameters(sql, replacements)
        path = self.database(database) + "/-/query"
        return (
            self.path(path, format=format)
            + "?"
            + urllib.parse.urlencode({**params, "sql": sql})
        )

    def table(self, database, table, format=None):
        path = f"{self.database(database)}/{tilde_encode(table)}"
        if format is not None:
            path = path_with_format(path=path, format=format)
        return PrefixedUrlString(path)

    def query(self, database, query, format=None):
        path = f"{self.database(database)}/{tilde_encode(query)}"
        if format is not None:
            path = path_with_format(path=path, format=format)
        return PrefixedUrlString(path)

    def row(self, database, table, row_path, format=None):
        path = f"{self.table(database, table)}/{row_path}"
        if format is not None:
            path = path_with_format(path=path, format=format)
        return PrefixedUrlString(path)

    def row_blob(self, database, table, row_path, column):
        return (
            self.table(database, table)
            + f"/{row_path}.blob?_blob_column={urllib.parse.quote_plus(column)}"
        )
