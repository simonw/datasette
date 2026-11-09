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

    def row(self, database, table, row_path, format=None):
        """Build a row URL from an already encoded row_path.

        Use path_from_row_pks(row, pks, use_rowid) to encode primary key
        values (or the rowid). Composite keys are separated by commas.
        This method appends row_path unchanged; it does not encode it.
        """
        path = f"{self.table(database, table)}/{row_path}"
        if format is not None:
            path = path_with_format(path=path, format=format)
        return PrefixedUrlString(path)

    def row_blob(self, database, table, row_path, column):
        """Build a binary column download URL for an already encoded row_path.

        As with row(), generate row_path using path_from_row_pks(). Pass
        column as an unencoded column name; it is query-string encoded here.
        """
        return (
            self.table(database, table)
            + f"/{row_path}.blob?_blob_column={urllib.parse.quote_plus(column)}"
        )
