import urllib

from .utils import (
    PrefixedUrlString,
    modify_query_string,
    request_route_path,
    tilde_encode,
)


class Urls:
    def __init__(self, ds):
        self.ds = ds

    def path(self, path, *, format=None):
        if not isinstance(path, PrefixedUrlString):
            path = path.removeprefix("/")
            path = PrefixedUrlString(self.ds.setting("base_url") + path)
        if format is not None:
            return self.modify(path, format=format)
        return path

    def modify(self, target, *, add=None, replace=None, remove=None, format=None):
        """
        Return a modified copy of a URL, with ``base_url`` applied.

        ``target`` is the current ``request`` or a path string, which can
        include a query string. See ``modify_query_string()`` for how
        ``add``, ``replace`` and ``remove`` behave.
        """
        fragment = ""
        if isinstance(target, str):
            prefixed = isinstance(target, PrefixedUrlString)
            path, _, fragment = str(target).partition("#")
            path, _, query_string = path.partition("?")
            replace_format = None
        else:
            # The route path has had any base_url stripped by the router
            prefixed = False
            path = request_route_path(target)
            query_string = target.query_string
            replace_format = target.url_vars.get("format")
        if format is not None:
            # Swap out the extension the request used, e.g. .json
            if replace_format:
                path = path.removesuffix(f".{replace_format}")
            # format=False removes the extension without adding a new one
            if format:
                path = f"{path}.{format}"
        if prefixed:
            path = PrefixedUrlString(path)
        url = self.path(path)
        query_string = modify_query_string(
            query_string, add=add, replace=replace, remove=remove
        )
        if query_string:
            url += f"?{query_string}"
        if fragment:
            url += f"#{fragment}"
        return url

    def instance(self, *, format=None):
        return self.path("", format=format)

    def static(self, path):
        return self.path(f"-/static/{path}")

    def static_plugins(self, plugin, path):
        return self.path(f"-/static-plugins/{plugin}/{path}")

    def logout(self):
        return self.path("-/logout")

    def database(self, database, *, format=None):
        db = self.ds.get_database(database)
        return self.path(tilde_encode(db.route), format=format)

    def database_query(self, database, sql, *, format=None):
        path = f"{self.database(database)}/-/query?" + urllib.parse.urlencode(
            {"sql": sql}
        )
        return self.path(path, format=format)

    def table(self, database, table, *, format=None):
        path = f"{self.database(database)}/{tilde_encode(table)}"
        if format is not None:
            path = f"{path}.{format}"
        return PrefixedUrlString(path)

    def query(self, database, query, *, format=None):
        path = f"{self.database(database)}/{tilde_encode(query)}"
        if format is not None:
            path = f"{path}.{format}"
        return PrefixedUrlString(path)

    def _row_path(self, row_path):
        # A list or tuple of primary key values is tilde-encoded and joined
        # with commas. Anything else is assumed to be already encoded.
        if isinstance(row_path, (list, tuple)):
            return ",".join(tilde_encode(str(value)) for value in row_path)
        return row_path

    def row(self, database, table, row_path, *, format=None):
        path = f"{self.table(database, table)}/{self._row_path(row_path)}"
        if format is not None:
            path = f"{path}.{format}"
        return PrefixedUrlString(path)

    def row_blob(self, database, table, row_path, column):
        return (
            self.table(database, table)
            + f"/{self._row_path(row_path)}.blob?_blob_column={urllib.parse.quote_plus(column)}"
        )
