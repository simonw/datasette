import collections
import hashlib
import itertools
import json
import os
import sqlite3
import sys
import traceback
import urllib.parse
from concurrent import futures
from pathlib import Path

import pluggy
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader
from sanic import Sanic, response
from sanic.exceptions import InvalidUsage, NotFound

from .views.base import (
    DatasetteError,
    RenderMixin,
    ureg
)
from .views.database import DatabaseDownload, DatabaseView
from .views.index import IndexView
from .views.table import RowView, TableView

from . import hookspecs
from .utils import (
    escape_css_string,
    escape_sqlite,
    get_plugins,
    module_from_path,
    to_css_class
)
from .inspect import inspect_hash, inspect_views, inspect_tables
from .version import __version__

app_root = Path(__file__).parent.parent


pm = pluggy.PluginManager("datasette")
pm.add_hookspecs(hookspecs)
pm.load_setuptools_entrypoints("datasette")


ConfigOption = collections.namedtuple(
    "ConfigOption", ("name", "default", "help")
)
CONFIG_OPTIONS = (
    ConfigOption("default_page_size", 100, """
        Default page size for the table view
    """.strip()),
    ConfigOption("max_returned_rows", 1000, """
        Maximum rows that can be returned from a table or custom query
    """.strip()),
    ConfigOption("sql_time_limit_ms", 1000, """
        Time limit for a SQL query in milliseconds
    """.strip()),
    ConfigOption("default_facet_size", 30, """
        Number of values to return for requested facets
    """.strip()),
    ConfigOption("facet_time_limit_ms", 200, """
        Time limit for calculating a requested facet
    """.strip()),
    ConfigOption("facet_suggest_time_limit_ms", 50, """
        Time limit for calculating a suggested facet
    """.strip()),
)
DEFAULT_CONFIG = {
    option.name: option.default
    for option in CONFIG_OPTIONS
}


class JsonDataView(RenderMixin):

    def __init__(self, datasette, filename, data_callback):
        self.ds = datasette
        self.jinja_env = datasette.jinja_env
        self.filename = filename
        self.data_callback = data_callback

    async def get(self, request, as_json):
        data = self.data_callback()
        if as_json:
            headers = {}
            if self.ds.cors:
                headers["Access-Control-Allow-Origin"] = "*"
            return response.HTTPResponse(
                json.dumps(data), content_type="application/json", headers=headers
            )

        else:
            return self.render(["show_json.html"], filename=self.filename, data=data)


async def favicon(request):
    return response.text("")


class Datasette:

    def __init__(
        self,
        files,
        num_threads=3,
        cache_headers=True,
        cors=False,
        inspect_data=None,
        metadata=None,
        sqlite_extensions=None,
        template_dir=None,
        plugins_dir=None,
        static_mounts=None,
        config=None,
    ):
        self.files = files
        self.num_threads = num_threads
        self.executor = futures.ThreadPoolExecutor(max_workers=num_threads)
        self.cache_headers = cache_headers
        self.cors = cors
        self._inspect = inspect_data
        self.metadata = metadata or {}
        self.sqlite_functions = []
        self.sqlite_extensions = sqlite_extensions or []
        self.template_dir = template_dir
        self.plugins_dir = plugins_dir
        self.static_mounts = static_mounts or []
        self.config = dict(DEFAULT_CONFIG, **(config or {}))
        self.max_returned_rows = self.config["max_returned_rows"]
        self.sql_time_limit_ms = self.config["sql_time_limit_ms"]
        self.page_size = self.config["default_page_size"]
        # Execute plugins in constructor, to ensure they are available
        # when the rest of `datasette inspect` executes
        if self.plugins_dir:
            for filename in os.listdir(self.plugins_dir):
                filepath = os.path.join(self.plugins_dir, filename)
                mod = module_from_path(filepath, name=filename)
                try:
                    pm.register(mod)
                except ValueError:
                    # Plugin already registered
                    pass

    def app_css_hash(self):
        if not hasattr(self, "_app_css_hash"):
            self._app_css_hash = hashlib.sha1(
                open(
                    os.path.join(str(app_root), "datasette/static/app.css")
                ).read().encode(
                    "utf8"
                )
            ).hexdigest()[
                :6
            ]
        return self._app_css_hash

    def get_canned_query(self, database_name, query_name):
        query = self.metadata.get("databases", {}).get(database_name, {}).get(
            "queries", {}
        ).get(
            query_name
        )
        if query:
            return {"name": query_name, "sql": query}

    def asset_urls(self, key):
        urls_or_dicts = (self.metadata.get(key) or [])
        # Flatten list-of-lists from plugins:
        urls_or_dicts += list(itertools.chain.from_iterable(getattr(pm.hook, key)()))
        for url_or_dict in urls_or_dicts:
            if isinstance(url_or_dict, dict):
                yield {"url": url_or_dict["url"], "sri": url_or_dict.get("sri")}

            else:
                yield {"url": url_or_dict}

    def extra_css_urls(self):
        return self.asset_urls("extra_css_urls")

    def extra_js_urls(self):
        return self.asset_urls("extra_js_urls")

    def update_with_inherited_metadata(self, metadata):
        # Fills in source/license with defaults, if available
        metadata.update(
            {
                "source": metadata.get("source") or self.metadata.get("source"),
                "source_url": metadata.get("source_url")
                or self.metadata.get("source_url"),
                "license": metadata.get("license") or self.metadata.get("license"),
                "license_url": metadata.get("license_url")
                or self.metadata.get("license_url"),
            }
        )

    def prepare_connection(self, conn):
        conn.row_factory = sqlite3.Row
        conn.text_factory = lambda x: str(x, "utf-8", "replace")
        for name, num_args, func in self.sqlite_functions:
            conn.create_function(name, num_args, func)
        if self.sqlite_extensions:
            conn.enable_load_extension(True)
            for extension in self.sqlite_extensions:
                conn.execute("SELECT load_extension('{}')".format(extension))
        pm.hook.prepare_connection(conn=conn)

    def inspect(self):
        " Inspect the database and return a dictionary of table metadata "
        if self._inspect:
            return self._inspect

        self._inspect = {}
        for filename in self.files:
            path = Path(filename)
            name = path.stem
            if name in self._inspect:
                raise Exception("Multiple files with same stem %s" % name)

            with sqlite3.connect(
                "file:{}?immutable=1".format(path), uri=True
            ) as conn:
                self.prepare_connection(conn)
                self._inspect[name] = {
                    "hash": inspect_hash(path),
                    "file": str(path),
                    "views": inspect_views(conn),
                    "tables": inspect_tables(conn, self.metadata.get("databases", {}).get(name, {}))
                }
        return self._inspect

    def register_custom_units(self):
        "Register any custom units defined in the metadata.json with Pint"
        for unit in self.metadata.get("custom_units", []):
            ureg.define(unit)

    def versions(self):
        conn = sqlite3.connect(":memory:")
        self.prepare_connection(conn)
        sqlite_version = conn.execute("select sqlite_version()").fetchone()[0]
        sqlite_extensions = {}
        for extension, testsql, hasversion in (
            ("json1", "SELECT json('{}')", False),
            ("spatialite", "SELECT spatialite_version()", True),
        ):
            try:
                result = conn.execute(testsql)
                if hasversion:
                    sqlite_extensions[extension] = result.fetchone()[0]
                else:
                    sqlite_extensions[extension] = None
            except Exception as e:
                pass
        # Figure out supported FTS versions
        fts_versions = []
        for fts in ("FTS5", "FTS4", "FTS3"):
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE v{fts} USING {fts} (t TEXT)".format(fts=fts)
                )
                fts_versions.append(fts)
            except sqlite3.OperationalError:
                continue

        return {
            "python": {
                "version": ".".join(map(str, sys.version_info[:3])), "full": sys.version
            },
            "datasette": {"version": __version__},
            "sqlite": {
                "version": sqlite_version,
                "fts_versions": fts_versions,
                "extensions": sqlite_extensions,
            },
        }

    def plugins(self):
        return [
            {
                "name": p["name"],
                "static": p["static_path"] is not None,
                "templates": p["templates_path"] is not None,
                "version": p.get("version"),
            }
            for p in get_plugins(pm)
        ]

    def app(self):
        app = Sanic(__name__)
        default_templates = str(app_root / "datasette" / "templates")
        template_paths = []
        if self.template_dir:
            template_paths.append(self.template_dir)
        template_paths.extend(
            [
                plugin["templates_path"]
                for plugin in get_plugins(pm)
                if plugin["templates_path"]
            ]
        )
        template_paths.append(default_templates)
        template_loader = ChoiceLoader(
            [
                FileSystemLoader(template_paths),
                # Support {% extends "default:table.html" %}:
                PrefixLoader(
                    {"default": FileSystemLoader(default_templates)}, delimiter=":"
                ),
            ]
        )
        self.jinja_env = Environment(loader=template_loader, autoescape=True)
        self.jinja_env.filters["escape_css_string"] = escape_css_string
        self.jinja_env.filters["quote_plus"] = lambda u: urllib.parse.quote_plus(u)
        self.jinja_env.filters["escape_sqlite"] = escape_sqlite
        self.jinja_env.filters["to_css_class"] = to_css_class
        pm.hook.prepare_jinja2_environment(env=self.jinja_env)
        app.add_route(IndexView.as_view(self), "/<as_json:(\.jsono?)?$>")
        # TODO: /favicon.ico and /-/static/ deserve far-future cache expires
        app.add_route(favicon, "/favicon.ico")
        app.static("/-/static/", str(app_root / "datasette" / "static"))
        for path, dirname in self.static_mounts:
            app.static(path, dirname)
        # Mount any plugin static/ directories
        for plugin in get_plugins(pm):
            if plugin["static_path"]:
                modpath = "/-/static-plugins/{}/".format(plugin["name"])
                app.static(modpath, plugin["static_path"])
        app.add_route(
            JsonDataView.as_view(self, "inspect.json", self.inspect),
            "/-/inspect<as_json:(\.json)?$>",
        )
        app.add_route(
            JsonDataView.as_view(self, "metadata.json", lambda: self.metadata),
            "/-/metadata<as_json:(\.json)?$>",
        )
        app.add_route(
            JsonDataView.as_view(self, "versions.json", self.versions),
            "/-/versions<as_json:(\.json)?$>",
        )
        app.add_route(
            JsonDataView.as_view(self, "plugins.json", self.plugins),
            "/-/plugins<as_json:(\.json)?$>",
        )
        app.add_route(
            JsonDataView.as_view(self, "config.json", lambda: self.config),
            "/-/config<as_json:(\.json)?$>",
        )
        app.add_route(
            DatabaseView.as_view(self), "/<db_name:[^/\.]+?><as_json:(\.jsono?)?$>"
        )
        app.add_route(
            DatabaseDownload.as_view(self), "/<db_name:[^/]+?><as_db:(\.db)$>"
        )
        app.add_route(
            TableView.as_view(self),
            "/<db_name:[^/]+>/<table:[^/]+?><as_json:(\.jsono?)?$>",
        )
        app.add_route(
            RowView.as_view(self),
            "/<db_name:[^/]+>/<table:[^/]+?>/<pk_path:[^/]+?><as_json:(\.jsono?)?$>",
        )

        self.register_custom_units()

        @app.exception(Exception)
        def on_exception(request, exception):
            title = None
            if isinstance(exception, NotFound):
                status = 404
                info = {}
                message = exception.args[0]
            elif isinstance(exception, InvalidUsage):
                status = 405
                info = {}
                message = exception.args[0]
            elif isinstance(exception, DatasetteError):
                status = exception.status
                info = exception.error_dict
                message = exception.message
                title = exception.title
            else:
                status = 500
                info = {}
                message = str(exception)
                traceback.print_exc()
            templates = ["500.html"]
            if status != 500:
                templates = ["{}.html".format(status)] + templates
            info.update(
                {"ok": False, "error": message, "status": status, "title": title}
            )
            if request.path.split("?")[0].endswith(".json"):
                return response.json(info, status=status)

            else:
                template = self.jinja_env.select_template(templates)
                return response.html(template.render(info), status=status)

        return app
