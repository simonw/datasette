import os

from sanic import response

from datasette.utils import to_css_class, validate_sql_select

from .base import BaseView, DatasetteError


class DatabaseView(BaseView):

    async def data(self, request, name, hash, default_labels=False, _size=None):
        if request.args.get("sql"):
            if not self.ds.config["allow_sql"]:
                raise DatasetteError("sql= is not allowed", status=400)
            sql = request.raw_args.pop("sql")
            validate_sql_select(sql)
            return await self.custom_sql(request, name, hash, sql, _size=_size)

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
        }, {
            "database_hash": hash,
            "show_hidden": request.args.get("_show_hidden"),
            "editable": True,
            "metadata": metadata,
        }, (
            "database-{}.html".format(to_css_class(name)), "database.html"
        )


class DatabaseDownload(BaseView):

    async def view_get(self, request, name, hash, **kwargs):
        if not self.ds.config["allow_download"]:
            raise DatasetteError("Database download is forbidden", status=403)
        filepath = self.ds.inspect()[name]["file"]
        return await response.file_stream(
            filepath,
            filename=os.path.basename(filepath),
            mime_type="application/octet-stream",
        )
