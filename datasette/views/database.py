import os

from sanic import response

from datasette.utils import to_css_class, validate_sql_select

from .base import BaseView, DatasetteError


class DatabaseView(BaseView):

    async def data(self, qs, name, hash, default_labels=False):
        if qs.first_or_none("sql"):
            if not self.ds.config["allow_sql"]:
                raise DatasetteError("sql= is not allowed", status=400)
            sql = qs.first("sql")
            validate_sql_select(sql)
            return await self.custom_sql(qs, name, hash, sql)

        info = self.ds.inspect()[name]
        metadata = self.ds.metadata.get("databases", {}).get(name, {})
        self.ds.update_with_inherited_metadata(metadata)
        tables = list(info["tables"].values())
        tables.sort(key=lambda t: (t["hidden"], t["name"]))
        return {
            "database": name,
            "tables": tables,
            "hidden_count": len([t for t in tables if t["hidden"]]),
            "views": info["views"],
            "queries": [
                {"name": query_name, "sql": query_sql}
                for query_name, query_sql in (metadata.get("queries") or {}).items()
            ],
            "config": self.ds.config,
        }, {
            "database_hash": hash,
            "show_hidden": qs.first_or_none("_show_hidden"),
            "editable": True,
            "metadata": metadata,
        }, (
            "database-{}.html".format(to_css_class(name)), "database.html"
        )


class DatabaseDownload(BaseView):

    async def view_get(self, qs, name, hash, **kwargs):
        if not self.ds.config["allow_download"]:
            raise DatasetteError("Database download is forbidden", status=403)
        filepath = self.ds.inspect()[name]["file"]
        return await response.file_stream(
            filepath,
            filename=os.path.basename(filepath),
            mime_type="application/octet-stream",
        )
