import asyncio
import collections
import hashlib
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
from concurrent import futures
from pathlib import Path

import click
from markupsafe import Markup
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader
from sanic import Sanic, response
from sanic.exceptions import InvalidUsage, NotFound

from .views.base import DatasetteError, ureg
from .views.database import DatabaseDownload, DatabaseView
from .views.index import IndexView
from .views.special import JsonDataView
from .views.table import RowView, TableView
from .renderer import json_renderer
from .database import Database

from .utils import (
    QueryInterrupted,
    Results,
    escape_css_string,
    escape_sqlite,
    get_plugins,
    module_from_path,
    sqlite3,
    sqlite_timelimit,
    to_css_class,
)
from .tracer import capture_traces, trace
from .plugins import pm, DEFAULT_PLUGINS
from .version import __version__

app_root = Path(__file__).parent.parent

connections = threading.local()
MEMORY = object()

ConfigOption = collections.namedtuple("ConfigOption", ("name", "default", "help"))
CONFIG_OPTIONS = (
    ConfigOption("default_page_size", 100, "Default page size for the table view"),
    ConfigOption(
        "max_returned_rows",
        1000,
        "Maximum rows that can be returned from a table or custom query",
    ),
    ConfigOption(
        "num_sql_threads",
        3,
        "Number of threads in the thread pool for executing SQLite queries",
    ),
    ConfigOption(
        "sql_time_limit_ms", 1000, "Time limit for a SQL query in milliseconds"
    ),
    ConfigOption(
        "default_facet_size", 30, "Number of values to return for requested facets"
    ),
    ConfigOption(
        "facet_time_limit_ms", 200, "Time limit for calculating a requested facet"
    ),
    ConfigOption(
        "facet_suggest_time_limit_ms",
        50,
        "Time limit for calculating a suggested facet",
    ),
    ConfigOption(
        "hash_urls",
        False,
        "Include DB file contents hash in URLs, for far-future caching",
    ),
    ConfigOption(
        "allow_facet",
        True,
        "Allow users to specify columns to facet using ?_facet= parameter",
    ),
    ConfigOption(
        "allow_download",
        True,
        "Allow users to download the original SQLite database files",
    ),
    ConfigOption("suggest_facets", True, "Calculate and display suggested facets"),
    ConfigOption("allow_sql", True, "Allow arbitrary SQL queries via ?sql= parameter"),
    ConfigOption(
        "default_cache_ttl",
        5,
        "Default HTTP cache TTL (used in Cache-Control: max-age= header)",
    ),
    ConfigOption(
        "default_cache_ttl_hashed",
        365 * 24 * 60 * 60,
        "Default HTTP cache TTL for hashed URL pages",
    ),
    ConfigOption(
        "cache_size_kb", 0, "SQLite cache size in KB (0 == use SQLite default)"
    ),
    ConfigOption(
        "allow_csv_stream",
        True,
        "Allow .csv?_stream=1 to download all rows (ignoring max_returned_rows)",
    ),
    ConfigOption(
        "max_csv_mb",
        100,
        "Maximum size allowed for CSV export in MB - set 0 to disable this limit",
    ),
    ConfigOption(
        "truncate_cells_html",
        2048,
        "Truncate cells longer than this in HTML table view - set 0 to disable",
    ),
    ConfigOption(
        "force_https_urls",
        False,
        "Force URLs in API output to always use https:// protocol",
    ),
)
DEFAULT_CONFIG = {option.name: option.default for option in CONFIG_OPTIONS}


async def favicon(request):
    return response.text("")


class Datasette:
    def __init__(
        self,
        files,
        immutables=None,
        cache_headers=True,
        cors=False,
        inspect_data=None,
        metadata=None,
        sqlite_extensions=None,
        template_dir=None,
        plugins_dir=None,
        static_mounts=None,
        memory=False,
        config=None,
        version_note=None,
    ):
        immutables = immutables or []
        self.files = tuple(files) + tuple(immutables)
        self.immutables = set(immutables)
        if not self.files:
            self.files = [MEMORY]
        elif memory:
            self.files = (MEMORY,) + self.files
        self.databases = {}
        self.inspect_data = inspect_data
        for file in self.files:
            path = file
            is_memory = False
            if file is MEMORY:
                path = None
                is_memory = True
            is_mutable = path not in self.immutables
            db = Database(self, path, is_mutable=is_mutable, is_memory=is_memory)
            if db.name in self.databases:
                raise Exception("Multiple files with same stem: {}".format(db.name))
            self.databases[db.name] = db
        self.cache_headers = cache_headers
        self.cors = cors
        self._metadata = metadata or {}
        self.sqlite_functions = []
        self.sqlite_extensions = sqlite_extensions or []
        self.template_dir = template_dir
        self.plugins_dir = plugins_dir
        self.static_mounts = static_mounts or []
        self._config = dict(DEFAULT_CONFIG, **(config or {}))
        self.renderers = {}  # File extension -> renderer function
        self.version_note = version_note
        self.executor = futures.ThreadPoolExecutor(
            max_workers=self.config("num_sql_threads")
        )
        self.max_returned_rows = self.config("max_returned_rows")
        self.sql_time_limit_ms = self.config("sql_time_limit_ms")
        self.page_size = self.config("default_page_size")
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

    async def run_sanity_checks(self):
        # Only one check right now, for Spatialite
        for database_name, database in self.databases.items():
            # Run pragma_info on every table
            for table in await database.table_names():
                try:
                    await self.execute(
                        database_name,
                        "PRAGMA table_info({});".format(escape_sqlite(table)),
                    )
                except sqlite3.OperationalError as e:
                    if e.args[0] == "no such module: VirtualSpatialIndex":
                        raise click.UsageError(
                            "It looks like you're trying to load a SpatiaLite"
                            " database without first loading the SpatiaLite module."
                            "\n\nRead more: https://datasette.readthedocs.io/en/latest/spatialite.html"
                        )
                    else:
                        raise

    def config(self, key):
        return self._config.get(key, None)

    def config_dict(self):
        # Returns a fully resolved config dictionary, useful for templates
        return {option.name: self.config(option.name) for option in CONFIG_OPTIONS}

    def metadata(self, key=None, database=None, table=None, fallback=True):
        """
        Looks up metadata, cascading backwards from specified level.
        Returns None if metadata value is not found.
        """
        assert not (
            database is None and table is not None
        ), "Cannot call metadata() with table= specified but not database="
        databases = self._metadata.get("databases") or {}
        search_list = []
        if database is not None:
            search_list.append(databases.get(database) or {})
        if table is not None:
            table_metadata = ((databases.get(database) or {}).get("tables") or {}).get(
                table
            ) or {}
            search_list.insert(0, table_metadata)
        search_list.append(self._metadata)
        if not fallback:
            # No fallback allowed, so just use the first one in the list
            search_list = search_list[:1]
        if key is not None:
            for item in search_list:
                if key in item:
                    return item[key]
            return None
        else:
            # Return the merged list
            m = {}
            for item in search_list:
                m.update(item)
            return m

    def plugin_config(self, plugin_name, database=None, table=None, fallback=True):
        "Return config for plugin, falling back from specified database/table"
        plugins = self.metadata(
            "plugins", database=database, table=table, fallback=fallback
        )
        if plugins is None:
            return None
        return plugins.get(plugin_name)

    def app_css_hash(self):
        if not hasattr(self, "_app_css_hash"):
            self._app_css_hash = hashlib.sha1(
                open(os.path.join(str(app_root), "datasette/static/app.css"))
                .read()
                .encode("utf8")
            ).hexdigest()[:6]
        return self._app_css_hash

    def get_canned_queries(self, database_name):
        queries = self.metadata("queries", database=database_name, fallback=False) or {}
        names = queries.keys()
        return [self.get_canned_query(database_name, name) for name in names]

    def get_canned_query(self, database_name, query_name):
        queries = self.metadata("queries", database=database_name, fallback=False) or {}
        query = queries.get(query_name)
        if query:
            if not isinstance(query, dict):
                query = {"sql": query}
            query["name"] = query_name
            return query

    def update_with_inherited_metadata(self, metadata):
        # Fills in source/license with defaults, if available
        metadata.update(
            {
                "source": metadata.get("source") or self.metadata("source"),
                "source_url": metadata.get("source_url") or self.metadata("source_url"),
                "license": metadata.get("license") or self.metadata("license"),
                "license_url": metadata.get("license_url")
                or self.metadata("license_url"),
                "about": metadata.get("about") or self.metadata("about"),
                "about_url": metadata.get("about_url") or self.metadata("about_url"),
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
        if self.config("cache_size_kb"):
            conn.execute("PRAGMA cache_size=-{}".format(self.config("cache_size_kb")))
        # pylint: disable=no-member
        pm.hook.prepare_connection(conn=conn)

    async def expand_foreign_keys(self, database, table, column, values):
        "Returns dict mapping (column, value) -> label"
        labeled_fks = {}
        db = self.databases[database]
        foreign_keys = await db.foreign_keys_for_table(table)
        # Find the foreign_key for this column
        try:
            fk = [
                foreign_key
                for foreign_key in foreign_keys
                if foreign_key["column"] == column
            ][0]
        except IndexError:
            return {}
        label_column = await db.label_column_for_table(fk["other_table"])
        if not label_column:
            return {(fk["column"], value): str(value) for value in values}
        labeled_fks = {}
        sql = """
            select {other_column}, {label_column}
            from {other_table}
            where {other_column} in ({placeholders})
        """.format(
            other_column=escape_sqlite(fk["other_column"]),
            label_column=escape_sqlite(label_column),
            other_table=escape_sqlite(fk["other_table"]),
            placeholders=", ".join(["?"] * len(set(values))),
        )
        try:
            results = await self.execute(database, sql, list(set(values)))
        except QueryInterrupted:
            pass
        else:
            for id, value in results:
                labeled_fks[(fk["column"], id)] = value
        return labeled_fks

    def absolute_url(self, request, path):
        url = urllib.parse.urljoin(request.url, path)
        if url.startswith("http://") and self.config("force_https_urls"):
            url = "https://" + url[len("http://") :]
        return url

    def register_custom_units(self):
        "Register any custom units defined in the metadata.json with Pint"
        for unit in self.metadata("custom_units") or []:
            ureg.define(unit)

    def connected_databases(self):
        return [
            {
                "name": d.name,
                "path": d.path,
                "size": d.size,
                "is_mutable": d.is_mutable,
                "is_memory": d.is_memory,
                "hash": d.hash,
            }
            for d in sorted(self.databases.values(), key=lambda d: d.name)
        ]

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
            except Exception:
                pass
        # Figure out supported FTS versions
        fts_versions = []
        for fts in ("FTS5", "FTS4", "FTS3"):
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE v{fts} USING {fts} (data)".format(fts=fts)
                )
                fts_versions.append(fts)
            except sqlite3.OperationalError:
                continue
        datasette_version = {"version": __version__}
        if self.version_note:
            datasette_version["note"] = self.version_note
        return {
            "python": {
                "version": ".".join(map(str, sys.version_info[:3])),
                "full": sys.version,
            },
            "datasette": datasette_version,
            "sqlite": {
                "version": sqlite_version,
                "fts_versions": fts_versions,
                "extensions": sqlite_extensions,
                "compile_options": [
                    r[0] for r in conn.execute("pragma compile_options;").fetchall()
                ],
            },
        }

    def plugins(self, show_all=False):
        ps = list(get_plugins(pm))
        if not show_all:
            ps = [p for p in ps if p["name"] not in DEFAULT_PLUGINS]
        return [
            {
                "name": p["name"],
                "static": p["static_path"] is not None,
                "templates": p["templates_path"] is not None,
                "version": p.get("version"),
            }
            for p in ps
        ]

    def table_metadata(self, database, table):
        "Fetch table-specific metadata."
        return (
            (self.metadata("databases") or {})
            .get(database, {})
            .get("tables", {})
            .get(table, {})
        )

    async def execute_against_connection_in_thread(self, db_name, fn):
        def in_thread():
            conn = getattr(connections, db_name, None)
            if not conn:
                db = self.databases[db_name]
                if db.is_memory:
                    conn = sqlite3.connect(":memory:")
                else:
                    # mode=ro or immutable=1?
                    if db.is_mutable:
                        qs = "mode=ro"
                    else:
                        qs = "immutable=1"
                    conn = sqlite3.connect(
                        "file:{}?{}".format(db.path, qs),
                        uri=True,
                        check_same_thread=False,
                    )
                self.prepare_connection(conn)
                setattr(connections, db_name, conn)
            return fn(conn)

        return await asyncio.get_event_loop().run_in_executor(self.executor, in_thread)

    async def execute(
        self,
        db_name,
        sql,
        params=None,
        truncate=False,
        custom_time_limit=None,
        page_size=None,
        log_sql_errors=True,
    ):
        """Executes sql against db_name in a thread"""
        page_size = page_size or self.page_size

        def sql_operation_in_thread(conn):
            time_limit_ms = self.sql_time_limit_ms
            if custom_time_limit and custom_time_limit < time_limit_ms:
                time_limit_ms = custom_time_limit

            with sqlite_timelimit(conn, time_limit_ms):
                try:
                    cursor = conn.cursor()
                    cursor.execute(sql, params or {})
                    max_returned_rows = self.max_returned_rows
                    if max_returned_rows == page_size:
                        max_returned_rows += 1
                    if max_returned_rows and truncate:
                        rows = cursor.fetchmany(max_returned_rows + 1)
                        truncated = len(rows) > max_returned_rows
                        rows = rows[:max_returned_rows]
                    else:
                        rows = cursor.fetchall()
                        truncated = False
                except sqlite3.OperationalError as e:
                    if e.args == ("interrupted",):
                        raise QueryInterrupted(e, sql, params)
                    if log_sql_errors:
                        print(
                            "ERROR: conn={}, sql = {}, params = {}: {}".format(
                                conn, repr(sql), params, e
                            )
                        )
                    raise

            if truncate:
                return Results(rows, truncated, cursor.description)

            else:
                return Results(rows, False, cursor.description)

        with trace("sql", database=db_name, sql=sql.strip(), params=params):
            results = await self.execute_against_connection_in_thread(
                db_name, sql_operation_in_thread
            )
        return results

    def register_renderers(self):
        """ Register output renderers which output data in custom formats. """
        # Built-in renderers
        self.renderers["json"] = json_renderer

        # Hooks
        hook_renderers = []
        # pylint: disable=no-member
        for hook in pm.hook.register_output_renderer(datasette=self):
            if type(hook) == list:
                hook_renderers += hook
            else:
                hook_renderers.append(hook)

        for renderer in hook_renderers:
            self.renderers[renderer["extension"]] = renderer["callback"]

    def app(self):
        class TracingSanic(Sanic):
            async def handle_request(self, request, write_callback, stream_callback):
                if request.args.get("_trace"):
                    request["traces"] = []
                    request["trace_start"] = time.time()
                    with capture_traces(request["traces"]):
                        await super().handle_request(
                            request, write_callback, stream_callback
                        )
                else:
                    await super().handle_request(
                        request, write_callback, stream_callback
                    )

        app = TracingSanic(__name__)
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
        # pylint: disable=no-member
        pm.hook.prepare_jinja2_environment(env=self.jinja_env)

        self.register_renderers()
        # Generate a regex snippet to match all registered renderer file extensions
        renderer_regex = "|".join(r"\." + key for key in self.renderers.keys())

        app.add_route(IndexView.as_view(self), r"/<as_format:(\.jsono?)?$>")
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
            JsonDataView.as_view(self, "metadata.json", lambda: self._metadata),
            r"/-/metadata<as_format:(\.json)?$>",
        )
        app.add_route(
            JsonDataView.as_view(self, "versions.json", self.versions),
            r"/-/versions<as_format:(\.json)?$>",
        )
        app.add_route(
            JsonDataView.as_view(self, "plugins.json", self.plugins),
            r"/-/plugins<as_format:(\.json)?$>",
        )
        app.add_route(
            JsonDataView.as_view(self, "config.json", lambda: self._config),
            r"/-/config<as_format:(\.json)?$>",
        )
        app.add_route(
            JsonDataView.as_view(self, "databases.json", self.connected_databases),
            r"/-/databases<as_format:(\.json)?$>",
        )
        app.add_route(
            DatabaseDownload.as_view(self), r"/<db_name:[^/]+?><as_db:(\.db)$>"
        )
        app.add_route(
            DatabaseView.as_view(self),
            r"/<db_name:[^/]+?><as_format:(" + renderer_regex + r"|.jsono|\.csv)?$>",
        )
        app.add_route(
            TableView.as_view(self), r"/<db_name:[^/]+>/<table_and_format:[^/]+?$>"
        )
        app.add_route(
            RowView.as_view(self),
            r"/<db_name:[^/]+>/<table:[^/]+?>/<pk_path:[^/]+?><as_format:("
            + renderer_regex
            + r")?$>",
        )
        self.register_custom_units()

        # On 404 with a trailing slash redirect to path without that slash:
        # pylint: disable=unused-variable
        @app.middleware("response")
        def redirect_on_404_with_trailing_slash(request, original_response):
            if original_response.status == 404 and request.path.endswith("/"):
                path = request.path.rstrip("/")
                if request.query_string:
                    path = "{}?{}".format(path, request.query_string)
                return response.redirect(path)

        @app.middleware("response")
        async def add_traces_to_response(request, response):
            if request.get("traces") is None:
                return
            traces = request["traces"]
            trace_info = {
                "request_duration_ms": 1000 * (time.time() - request["trace_start"]),
                "sum_trace_duration_ms": sum(t["duration_ms"] for t in traces),
                "num_traces": len(traces),
                "traces": traces,
            }
            if "text/html" in response.content_type and b"</body>" in response.body:
                extra = json.dumps(trace_info, indent=2)
                extra_html = "<pre>{}</pre></body>".format(extra).encode("utf8")
                response.body = response.body.replace(b"</body>", extra_html)
            elif "json" in response.content_type and response.body.startswith(b"{"):
                data = json.loads(response.body.decode("utf8"))
                if "_trace" not in data:
                    data["_trace"] = trace_info
                    response.body = json.dumps(data).encode("utf8")

        @app.exception(Exception)
        def on_exception(request, exception):
            title = None
            help = None
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
                if exception.messagge_is_html:
                    message = Markup(message)
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
            if request is not None and request.path.split("?")[0].endswith(".json"):
                r = response.json(info, status=status)

            else:
                template = self.jinja_env.select_template(templates)
                r = response.html(template.render(info), status=status)
            if self.cors:
                r.headers["Access-Control-Allow-Origin"] = "*"
            return r

        # First time server starts up, calculate table counts for immutable databases
        @app.listener("before_server_start")
        async def setup_db(app, loop):
            for dbname, database in self.databases.items():
                if not database.is_mutable:
                    await database.table_counts(limit=60 * 60 * 1000)

        return app
