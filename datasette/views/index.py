import json

from sanic import response

from datasette.utils import CustomJSONEncoder
from datasette.version import __version__

from .base import HASH_LENGTH, RenderMixin


class IndexView(RenderMixin):

    def __init__(self, datasette):
        self.ds = datasette
        self.files = datasette.files
        self.jinja_env = datasette.jinja_env
        self.executor = datasette.executor

    async def get(self, request, as_json):
        databases = []
        for key, info in sorted(self.ds.inspect().items()):
            tables = [t for t in info["tables"].values() if not t["hidden"]]
            hidden_tables = [t for t in info["tables"].values() if t["hidden"]]
            database = {
                "name": key,
                "hash": info["hash"],
                "path": "{}-{}".format(key, info["hash"][:HASH_LENGTH]),
                "tables_truncated": sorted(
                    tables, key=lambda t: t["count"], reverse=True
                )[
                    :5
                ],
                "tables_count": len(tables),
                "tables_more": len(tables) > 5,
                "table_rows_sum": sum(t["count"] for t in tables),
                "hidden_table_rows_sum": sum(t["count"] for t in hidden_tables),
                "hidden_tables_count": len(hidden_tables),
                "views_count": len(info["views"]),
            }
            databases.append(database)
        if as_json:
            headers = {}
            if self.ds.cors:
                headers["Access-Control-Allow-Origin"] = "*"
            return response.HTTPResponse(
                json.dumps({db["name"]: db for db in databases}, cls=CustomJSONEncoder),
                content_type="application/json",
                headers=headers,
            )

        else:
            return self.render(
                ["index.html"],
                databases=databases,
                metadata=self.ds.metadata,
                datasette_version=__version__,
                extra_css_urls=self.ds.extra_css_urls(),
                extra_js_urls=self.ds.extra_js_urls(),
            )
