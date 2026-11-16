import urllib

from .utils import PrefixedUrlString, path_with_format, tilde_encode


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

    def database_query(self, database, sql, format=None):
        path = f"{self.database(database)}/-/query?" + urllib.parse.urlencode(
            {"sql": sql}
        )
        return self.path(path, format=format)

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

    def _row_path(self, row_path):
        # A list or tuple of primary key values is tilde-encoded and joined
        # with commas. Anything else is assumed to be already encoded.
        if isinstance(row_path, (list, tuple)):
            return ",".join(tilde_encode(str(value)) for value in row_path)
        return row_path

    def row(self, database, table, row_path, format=None):
        path = f"{self.table(database, table)}/{self._row_path(row_path)}"
        if format is not None:
            path = path_with_format(path=path, format=format)
        return PrefixedUrlString(path)

    def row_blob(self, database, table, row_path, column):
        return (
            self.table(database, table)
            + f"/{self._row_path(row_path)}.blob?_blob_column={urllib.parse.quote_plus(column)}"
        )
