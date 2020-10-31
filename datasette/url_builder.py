from .utils import path_with_format, HASH_LENGTH
import urllib


class Urls:
    def __init__(self, ds):
        self.ds = ds

    def path(self, path, format=None):
        if path.startswith("/"):
            path = path[1:]
        path = self.ds.config("base_url") + path
        if format is not None:
            path = path_with_format(path=path, format=format)
        return path

    def instance(self, format=None):
        return self.path("", format=format)

    def static(self, path):
        return self.path("-/static/{}".format(path))

    def static_plugins(self, plugin, path):
        return self.path("-/static-plugins/{}/{}".format(plugin, path))

    def logout(self):
        return self.path("-/logout")

    def database(self, database, format=None):
        db = self.ds.databases[database]
        if self.ds.config("hash_urls") and db.hash:
            path = self.path(
                "{}-{}".format(database, db.hash[:HASH_LENGTH]), format=format
            )
        else:
            path = self.path(database, format=format)
        return path

    def table(self, database, table, format=None):
        path = "{}/{}".format(self.database(database), urllib.parse.quote_plus(table))
        if format is not None:
            path = path_with_format(path=path, format=format)
        return path

    def query(self, database, query, format=None):
        path = "{}/{}".format(self.database(database), urllib.parse.quote_plus(query))
        if format is not None:
            path = path_with_format(path=path, format=format)
        return path

    def row(self, database, table, row_path, format=None):
        path = "{}/{}".format(self.table(database, table), row_path)
        if format is not None:
            path = path_with_format(path=path, format=format)
        return path

    def row_blob(self, database, table, row_path, column):
        return self.table(database, table) + "/{}.blob?_blob_column={}".format(
            row_path, urllib.parse.quote_plus(column)
        )
