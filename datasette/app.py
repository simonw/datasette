import asyncio
from typing import Sequence, Union, Tuple, Optional
import asgi_csrf
import collections
import datetime
import functools
import glob
import hashlib
import httpx
import inspect
from itsdangerous import BadSignature
import json
import os
import pkg_resources
import re
import secrets
import sys
import threading
import urllib.parse
from concurrent import futures
from pathlib import Path

from markupsafe import Markup, escape
from itsdangerous import URLSafeSerializer
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader
from jinja2.environment import Template
from jinja2.exceptions import TemplateNotFound

from .views.base import ureg
from .views.database import DatabaseDownload, DatabaseView
from .views.index import IndexView
from .views.special import (
    JsonDataView,
    PatternPortfolioView,
    AuthTokenView,
    LogoutView,
    AllowDebugView,
    PermissionsDebugView,
    MessagesDebugView,
)
from .views.table import TableView
from .views.row import RowView
from .renderer import json_renderer
from .url_builder import Urls
from .database import Database, QueryInterrupted

from .utils import (
    PrefixedUrlString,
    SPATIALITE_FUNCTIONS,
    StartupError,
    async_call_with_supported_arguments,
    await_me_maybe,
    call_with_supported_arguments,
    display_actor,
    escape_css_string,
    escape_sqlite,
    find_spatialite,
    format_bytes,
    module_from_path,
    parse_metadata,
    resolve_env_secrets,
    resolve_routes,
    to_css_class,
)
from .utils.asgi import (
    AsgiLifespan,
    Forbidden,
    NotFound,
    Request,
    Response,
    AsgiRunOnFirstRequest,
    asgi_static,
    asgi_send,
    asgi_send_file,
    asgi_send_redirect,
)
from .utils.internal_db import init_internal_db, populate_schema_tables
from .utils.sqlite import (
    sqlite3,
    using_pysqlite3,
)
from .tracer import AsgiTracer
from .plugins import pm, DEFAULT_PLUGINS, get_plugins
from .version import __version__

app_root = Path(__file__).parent.parent

# https://github.com/simonw/datasette/issues/283#issuecomment-781591015
SQLITE_LIMIT_ATTACHED = 10

Setting = collections.namedtuple("Setting", ("name", "default", "help"))
SETTINGS = (
    Setting("default_page_size", 100, "Default page size for the table view"),
    Setting(
        "max_returned_rows",
        1000,
        "Maximum rows that can be returned from a table or custom query",
    ),
    Setting(
        "num_sql_threads",
        3,
        "Number of threads in the thread pool for executing SQLite queries",
    ),
    Setting("sql_time_limit_ms", 1000, "Time limit for a SQL query in milliseconds"),
    Setting(
        "default_facet_size", 30, "Number of values to return for requested facets"
    ),
    Setting("facet_time_limit_ms", 200, "Time limit for calculating a requested facet"),
    Setting(
        "facet_suggest_time_limit_ms",
        50,
        "Time limit for calculating a suggested facet",
    ),
    Setting(
        "allow_facet",
        True,
        "Allow users to specify columns to facet using ?_facet= parameter",
    ),
    Setting(
        "default_allow_sql",
        True,
        "Allow anyone to run arbitrary SQL queries",
    ),
    Setting(
        "allow_download",
        True,
        "Allow users to download the original SQLite database files",
    ),
    Setting("suggest_facets", True, "Calculate and display suggested facets"),
    Setting(
        "default_cache_ttl",
        5,
        "Default HTTP cache TTL (used in Cache-Control: max-age= header)",
    ),
    Setting("cache_size_kb", 0, "SQLite cache size in KB (0 == use SQLite default)"),
    Setting(
        "allow_csv_stream",
        True,
        "Allow .csv?_stream=1 to download all rows (ignoring max_returned_rows)",
    ),
    Setting(
        "max_csv_mb",
        100,
        "Maximum size allowed for CSV export in MB - set 0 to disable this limit",
    ),
    Setting(
        "truncate_cells_html",
        2048,
        "Truncate cells longer than this in HTML table view - set 0 to disable",
    ),
    Setting(
        "force_https_urls",
        False,
        "Force URLs in API output to always use https:// protocol",
    ),
    Setting(
        "template_debug",
        False,
        "Allow display of template debug information with ?_context=1",
    ),
    Setting(
        "trace_debug",
        False,
        "Allow display of SQL trace debug information with ?_trace=1",
    ),
    Setting("base_url", "/", "Datasette URLs should use this base path"),
)
_HASH_URLS_REMOVED = "The hash_urls setting has been removed, try the datasette-hashed-urls plugin instead"
OBSOLETE_SETTINGS = {
    "hash_urls": _HASH_URLS_REMOVED,
    "default_cache_ttl_hashed": _HASH_URLS_REMOVED,
}
DEFAULT_SETTINGS = {option.name: option.default for option in SETTINGS}

FAVICON_PATH = app_root / "datasette" / "static" / "favicon.png"


async def favicon(request, send):
    await asgi_send_file(
        send,
        str(FAVICON_PATH),
        content_type="image/png",
        headers={"Cache-Control": "max-age=3600, immutable, public"},
    )


class Datasette:
    # Message constants:
    INFO = 1
    WARNING = 2
    ERROR = 3

    def __init__(
        self,
        files=None,
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
        settings=None,
        secret=None,
        version_note=None,
        config_dir=None,
        pdb=False,
        crossdb=False,
        nolock=False,
    ):
        self._startup_invoked = False
        assert config_dir is None or isinstance(
            config_dir, Path
        ), "config_dir= should be a pathlib.Path"
        self.config_dir = config_dir
        self.pdb = pdb
        self._secret = secret or secrets.token_hex(32)
        if files is not None and isinstance(files, str):
            raise ValueError("files= must be a list of paths, not a string")
        self.files = tuple(files or []) + tuple(immutables or [])
        if config_dir:
            db_files = []
            for ext in ("db", "sqlite", "sqlite3"):
                db_files.extend(config_dir.glob("*.{}".format(ext)))
            self.files += tuple(str(f) for f in db_files)
        if (
            config_dir
            and (config_dir / "inspect-data.json").exists()
            and not inspect_data
        ):
            inspect_data = json.loads((config_dir / "inspect-data.json").read_text())
            if not immutables:
                immutable_filenames = [i["file"] for i in inspect_data.values()]
                immutables = [
                    f for f in self.files if Path(f).name in immutable_filenames
                ]
        self.inspect_data = inspect_data
        self.immutables = set(immutables or [])
        self.databases = collections.OrderedDict()
        try:
            self._refresh_schemas_lock = asyncio.Lock()
        except RuntimeError as rex:
            # Workaround for intermittent test failure, see:
            # https://github.com/simonw/datasette/issues/1802
            if "There is no current event loop in thread" in str(rex):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._refresh_schemas_lock = asyncio.Lock()
            else:
                raise
        self.crossdb = crossdb
        self.nolock = nolock
        if memory or crossdb or not self.files:
            self.add_database(Database(self, is_memory=True), name="_memory")
        # memory_name is a random string so that each Datasette instance gets its own
        # unique in-memory named database - otherwise unit tests can fail with weird
        # errors when different instances accidentally share an in-memory database
        self.add_database(
            Database(self, memory_name=secrets.token_hex()), name="_internal"
        )
        self.internal_db_created = False
        for file in self.files:
            self.add_database(
                Database(self, file, is_mutable=file not in self.immutables)
            )
        self.cache_headers = cache_headers
        self.cors = cors
        metadata_files = []
        if config_dir:
            metadata_files = [
                config_dir / filename
                for filename in ("metadata.json", "metadata.yaml", "metadata.yml")
                if (config_dir / filename).exists()
            ]
        if config_dir and metadata_files and not metadata:
            with metadata_files[0].open() as fp:
                metadata = parse_metadata(fp.read())
        self._metadata_local = metadata or {}
        self.sqlite_extensions = []
        for extension in sqlite_extensions or []:
            # Resolve spatialite, if requested
            if extension == "spatialite":
                # Could raise SpatialiteNotFound
                self.sqlite_extensions.append(find_spatialite())
            else:
                self.sqlite_extensions.append(extension)
        if config_dir and (config_dir / "templates").is_dir() and not template_dir:
            template_dir = str((config_dir / "templates").resolve())
        self.template_dir = template_dir
        if config_dir and (config_dir / "plugins").is_dir() and not plugins_dir:
            plugins_dir = str((config_dir / "plugins").resolve())
        self.plugins_dir = plugins_dir
        if config_dir and (config_dir / "static").is_dir() and not static_mounts:
            static_mounts = [("static", str((config_dir / "static").resolve()))]
        self.static_mounts = static_mounts or []
        if config_dir and (config_dir / "config.json").exists():
            raise StartupError("config.json should be renamed to settings.json")
        if config_dir and (config_dir / "settings.json").exists() and not settings:
            settings = json.loads((config_dir / "settings.json").read_text())
            # Validate those settings
            for key in settings:
                if key not in DEFAULT_SETTINGS:
                    raise StartupError(
                        "Invalid setting '{}' in settings.json".format(key)
                    )
        self._settings = dict(DEFAULT_SETTINGS, **(settings or {}))
        self.renderers = {}  # File extension -> (renderer, can_render) functions
        self.version_note = version_note
        if self.setting("num_sql_threads") == 0:
            self.executor = None
        else:
            self.executor = futures.ThreadPoolExecutor(
                max_workers=self.setting("num_sql_threads")
            )
        self.max_returned_rows = self.setting("max_returned_rows")
        self.sql_time_limit_ms = self.setting("sql_time_limit_ms")
        self.page_size = self.setting("default_page_size")
        # Execute plugins in constructor, to ensure they are available
        # when the rest of `datasette inspect` executes
        if self.plugins_dir:
            for filepath in glob.glob(os.path.join(self.plugins_dir, "*.py")):
                if not os.path.isfile(filepath):
                    continue
                mod = module_from_path(filepath, name=os.path.basename(filepath))
                try:
                    pm.register(mod)
                except ValueError:
                    # Plugin already registered
                    pass

        # Configure Jinja
        default_templates = str(app_root / "datasette" / "templates")
        template_paths = []
        if self.template_dir:
            template_paths.append(self.template_dir)
        plugin_template_paths = [
            plugin["templates_path"]
            for plugin in get_plugins()
            if plugin["templates_path"]
        ]
        template_paths.extend(plugin_template_paths)
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
        self.jinja_env = Environment(
            loader=template_loader, autoescape=True, enable_async=True
        )
        self.jinja_env.filters["escape_css_string"] = escape_css_string
        self.jinja_env.filters["quote_plus"] = urllib.parse.quote_plus
        self.jinja_env.filters["escape_sqlite"] = escape_sqlite
        self.jinja_env.filters["to_css_class"] = to_css_class
        self._register_renderers()
        self._permission_checks = collections.deque(maxlen=200)
        self._root_token = secrets.token_hex(32)
        self.client = DatasetteClient(self)

    async def refresh_schemas(self):
        if self._refresh_schemas_lock.locked():
            return
        async with self._refresh_schemas_lock:
            await self._refresh_schemas()

    async def _refresh_schemas(self):
        internal_db = self.databases["_internal"]
        if not self.internal_db_created:
            await init_internal_db(internal_db)
            self.internal_db_created = True

        current_schema_versions = {
            row["database_name"]: row["schema_version"]
            for row in await internal_db.execute(
                "select database_name, schema_version from databases"
            )
        }
        for database_name, db in self.databases.items():
            schema_version = (await db.execute("PRAGMA schema_version")).first()[0]
            # Compare schema versions to see if we should skip it
            if schema_version == current_schema_versions.get(database_name):
                continue
            await internal_db.execute_write(
                """
                INSERT OR REPLACE INTO databases (database_name, path, is_memory, schema_version)
                VALUES (?, ?, ?, ?)
            """,
                [database_name, str(db.path), db.is_memory, schema_version],
            )
            await populate_schema_tables(internal_db, db)

    @property
    def urls(self):
        return Urls(self)

    async def invoke_startup(self):
        # This must be called for Datasette to be in a usable state
        if self._startup_invoked:
            return
        for hook in pm.hook.prepare_jinja2_environment(
            env=self.jinja_env, datasette=self
        ):
            await await_me_maybe(hook)
        for hook in pm.hook.startup(datasette=self):
            await await_me_maybe(hook)
        self._startup_invoked = True

    def sign(self, value, namespace="default"):
        return URLSafeSerializer(self._secret, namespace).dumps(value)

    def unsign(self, signed, namespace="default"):
        return URLSafeSerializer(self._secret, namespace).loads(signed)

    def get_database(self, name=None, route=None):
        if route is not None:
            matches = [db for db in self.databases.values() if db.route == route]
            if not matches:
                raise KeyError
            return matches[0]
        if name is None:
            # Return first database that isn't "_internal"
            name = [key for key in self.databases.keys() if key != "_internal"][0]
        return self.databases[name]

    def add_database(self, db, name=None, route=None):
        new_databases = self.databases.copy()
        if name is None:
            # Pick a unique name for this database
            suggestion = db.suggest_name()
            name = suggestion
        else:
            suggestion = name
        i = 2
        while name in self.databases:
            name = "{}_{}".format(suggestion, i)
            i += 1
        db.name = name
        db.route = route or name
        new_databases[name] = db
        # don't mutate! that causes race conditions with live import
        self.databases = new_databases
        return db

    def add_memory_database(self, memory_name):
        return self.add_database(Database(self, memory_name=memory_name))

    def remove_database(self, name):
        new_databases = self.databases.copy()
        new_databases.pop(name)
        self.databases = new_databases

    def setting(self, key):
        return self._settings.get(key, None)

    def settings_dict(self):
        # Returns a fully resolved settings dictionary, useful for templates
        return {option.name: self.setting(option.name) for option in SETTINGS}

    def _metadata_recursive_update(self, orig, updated):
        if not isinstance(orig, dict) or not isinstance(updated, dict):
            return orig

        for key, upd_value in updated.items():
            if isinstance(upd_value, dict) and isinstance(orig.get(key), dict):
                orig[key] = self._metadata_recursive_update(orig[key], upd_value)
            else:
                orig[key] = upd_value
        return orig

    def metadata(self, key=None, database=None, table=None, fallback=True):
        """
        Looks up metadata, cascading backwards from specified level.
        Returns None if metadata value is not found.
        """
        assert not (
            database is None and table is not None
        ), "Cannot call metadata() with table= specified but not database="
        metadata = {}

        for hook_dbs in pm.hook.get_metadata(
            datasette=self, key=key, database=database, table=table
        ):
            metadata = self._metadata_recursive_update(metadata, hook_dbs)

        # security precaution!! don't allow anything in the local config
        # to be overwritten. this is a temporary measure, not sure if this
        # is a good idea long term or maybe if it should just be a concern
        # of the plugin's implemtnation
        metadata = self._metadata_recursive_update(metadata, self._metadata_local)

        databases = metadata.get("databases") or {}

        search_list = []
        if database is not None:
            search_list.append(databases.get(database) or {})
        if table is not None:
            table_metadata = ((databases.get(database) or {}).get("tables") or {}).get(
                table
            ) or {}
            search_list.insert(0, table_metadata)

        search_list.append(metadata)
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

    @property
    def _metadata(self):
        return self.metadata()

    def plugin_config(self, plugin_name, database=None, table=None, fallback=True):
        """Return config for plugin, falling back from specified database/table"""
        plugins = self.metadata(
            "plugins", database=database, table=table, fallback=fallback
        )
        if plugins is None:
            return None
        plugin_config = plugins.get(plugin_name)
        # Resolve any $file and $env keys
        plugin_config = resolve_env_secrets(plugin_config, os.environ)
        return plugin_config

    def app_css_hash(self):
        if not hasattr(self, "_app_css_hash"):
            with open(os.path.join(str(app_root), "datasette/static/app.css")) as fp:
                self._app_css_hash = hashlib.sha1(fp.read().encode("utf8")).hexdigest()[
                    :6
                ]
        return self._app_css_hash

    async def get_canned_queries(self, database_name, actor):
        queries = self.metadata("queries", database=database_name, fallback=False) or {}
        for more_queries in pm.hook.canned_queries(
            datasette=self,
            database=database_name,
            actor=actor,
        ):
            more_queries = await await_me_maybe(more_queries)
            queries.update(more_queries or {})
        # Fix any {"name": "select ..."} queries to be {"name": {"sql": "select ..."}}
        for key in queries:
            if not isinstance(queries[key], dict):
                queries[key] = {"sql": queries[key]}
            # Also make sure "name" is available:
            queries[key]["name"] = key
        return queries

    async def get_canned_query(self, database_name, query_name, actor):
        queries = await self.get_canned_queries(database_name, actor)
        query = queries.get(query_name)
        if query:
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

    def _prepare_connection(self, conn, database):
        conn.row_factory = sqlite3.Row
        conn.text_factory = lambda x: str(x, "utf-8", "replace")
        if self.sqlite_extensions:
            conn.enable_load_extension(True)
            for extension in self.sqlite_extensions:
                # "extension" is either a string path to the extension
                # or a 2-item tuple that specifies which entrypoint to load.
                if isinstance(extension, tuple):
                    path, entrypoint = extension
                    conn.execute("SELECT load_extension(?, ?)", [path, entrypoint])
                else:
                    conn.execute("SELECT load_extension(?)", [extension])
        if self.setting("cache_size_kb"):
            conn.execute(f"PRAGMA cache_size=-{self.setting('cache_size_kb')}")
        # pylint: disable=no-member
        pm.hook.prepare_connection(conn=conn, database=database, datasette=self)
        # If self.crossdb and this is _memory, connect the first SQLITE_LIMIT_ATTACHED databases
        if self.crossdb and database == "_memory":
            count = 0
            for db_name, db in self.databases.items():
                if count >= SQLITE_LIMIT_ATTACHED or db.is_memory:
                    continue
                sql = 'ATTACH DATABASE "file:{path}?{qs}" AS [{name}];'.format(
                    path=db.path,
                    qs="mode=ro" if db.is_mutable else "immutable=1",
                    name=db_name,
                )
                conn.execute(sql)
                count += 1

    def add_message(self, request, message, type=INFO):
        if not hasattr(request, "_messages"):
            request._messages = []
            request._messages_should_clear = False
        request._messages.append((message, type))

    def _write_messages_to_response(self, request, response):
        if getattr(request, "_messages", None):
            # Set those messages
            response.set_cookie("ds_messages", self.sign(request._messages, "messages"))
        elif getattr(request, "_messages_should_clear", False):
            response.set_cookie("ds_messages", "", expires=0, max_age=0)

    def _show_messages(self, request):
        if getattr(request, "_messages", None):
            request._messages_should_clear = True
            messages = request._messages
            request._messages = []
            return messages
        else:
            return []

    async def _crumb_items(self, request, table=None, database=None):
        crumbs = []
        actor = None
        if request:
            actor = request.actor
        # Top-level link
        if await self.permission_allowed(
            actor=actor, action="view-instance", default=True
        ):
            crumbs.append({"href": self.urls.instance(), "label": "home"})
        # Database link
        if database:
            if await self.permission_allowed(
                actor=actor,
                action="view-database",
                resource=database,
                default=True,
            ):
                crumbs.append(
                    {
                        "href": self.urls.database(database),
                        "label": database,
                    }
                )
        # Table link
        if table:
            assert database, "table= requires database="
            if await self.permission_allowed(
                actor=actor,
                action="view-table",
                resource=(database, table),
                default=True,
            ):
                crumbs.append(
                    {
                        "href": self.urls.table(database, table),
                        "label": table,
                    }
                )
        return crumbs

    async def permission_allowed(self, actor, action, resource=None, default=False):
        """Check permissions using the permissions_allowed plugin hook"""
        result = None
        for check in pm.hook.permission_allowed(
            datasette=self,
            actor=actor,
            action=action,
            resource=resource,
        ):
            check = await await_me_maybe(check)
            if check is not None:
                result = check
        used_default = False
        if result is None:
            result = default
            used_default = True
        self._permission_checks.append(
            {
                "when": datetime.datetime.utcnow().isoformat(),
                "actor": actor,
                "action": action,
                "resource": resource,
                "used_default": used_default,
                "result": result,
            }
        )
        return result

    async def ensure_permissions(
        self,
        actor: dict,
        permissions: Sequence[Union[Tuple[str, Union[str, Tuple[str, str]]], str]],
    ):
        """
        permissions is a list of (action, resource) tuples or 'action' strings

        Raises datasette.Forbidden() if any of the checks fail
        """
        assert actor is None or isinstance(actor, dict), "actor must be None or a dict"
        for permission in permissions:
            if isinstance(permission, str):
                action = permission
                resource = None
            elif isinstance(permission, (tuple, list)) and len(permission) == 2:
                action, resource = permission
            else:
                assert (
                    False
                ), "permission should be string or tuple of two items: {}".format(
                    repr(permission)
                )
            ok = await self.permission_allowed(
                actor,
                action,
                resource=resource,
                default=None,
            )
            if ok is not None:
                if ok:
                    return
                else:
                    raise Forbidden(action)

    async def check_visibility(
        self,
        actor: dict,
        action: Optional[str] = None,
        resource: Optional[Union[str, Tuple[str, str]]] = None,
        permissions: Optional[
            Sequence[Union[Tuple[str, Union[str, Tuple[str, str]]], str]]
        ] = None,
    ):
        """Returns (visible, private) - visible = can you see it, private = can others see it too"""
        if permissions:
            assert (
                not action and not resource
            ), "Can't use action= or resource= with permissions="
        else:
            permissions = [(action, resource)]
        try:
            await self.ensure_permissions(actor, permissions)
        except Forbidden:
            return False, False
        # User can see it, but can the anonymous user see it?
        try:
            await self.ensure_permissions(None, permissions)
        except Forbidden:
            # It's visible but private
            return True, True
        # It's visible to everyone
        return True, False

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
        return await self.databases[db_name].execute(
            sql,
            params=params,
            truncate=truncate,
            custom_time_limit=custom_time_limit,
            page_size=page_size,
            log_sql_errors=log_sql_errors,
        )

    async def expand_foreign_keys(self, database, table, column, values):
        """Returns dict mapping (column, value) -> label"""
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
        if url.startswith("http://") and self.setting("force_https_urls"):
            url = "https://" + url[len("http://") :]
        return url

    def _register_custom_units(self):
        """Register any custom units defined in the metadata.json with Pint"""
        for unit in self.metadata("custom_units") or []:
            ureg.define(unit)

    def _connected_databases(self):
        return [
            {
                "name": d.name,
                "route": d.route,
                "path": d.path,
                "size": d.size,
                "is_mutable": d.is_mutable,
                "is_memory": d.is_memory,
                "hash": d.hash,
            }
            for name, d in self.databases.items()
            if name != "_internal"
        ]

    def _versions(self):
        conn = sqlite3.connect(":memory:")
        self._prepare_connection(conn, "_memory")
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
        # More details on SpatiaLite
        if "spatialite" in sqlite_extensions:
            spatialite_details = {}
            for fn in SPATIALITE_FUNCTIONS:
                try:
                    result = conn.execute("select {}()".format(fn))
                    spatialite_details[fn] = result.fetchone()[0]
                except Exception as e:
                    spatialite_details[fn] = {"error": str(e)}
            sqlite_extensions["spatialite"] = spatialite_details

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

        try:
            # Optional import to avoid breaking Pyodide
            # https://github.com/simonw/datasette/issues/1733#issuecomment-1115268245
            import uvicorn

            uvicorn_version = uvicorn.__version__
        except ImportError:
            uvicorn_version = None
        info = {
            "python": {
                "version": ".".join(map(str, sys.version_info[:3])),
                "full": sys.version,
            },
            "datasette": datasette_version,
            "asgi": "3.0",
            "uvicorn": uvicorn_version,
            "sqlite": {
                "version": sqlite_version,
                "fts_versions": fts_versions,
                "extensions": sqlite_extensions,
                "compile_options": [
                    r[0] for r in conn.execute("pragma compile_options;").fetchall()
                ],
            },
        }
        if using_pysqlite3:
            for package in ("pysqlite3", "pysqlite3-binary"):
                try:
                    info["pysqlite3"] = pkg_resources.get_distribution(package).version
                    break
                except pkg_resources.DistributionNotFound:
                    pass
        return info

    def _plugins(self, request=None, all=False):
        ps = list(get_plugins())
        should_show_all = False
        if request is not None:
            should_show_all = request.args.get("all")
        else:
            should_show_all = all
        if not should_show_all:
            ps = [p for p in ps if p["name"] not in DEFAULT_PLUGINS]
        ps.sort(key=lambda p: p["name"])
        return [
            {
                "name": p["name"],
                "static": p["static_path"] is not None,
                "templates": p["templates_path"] is not None,
                "version": p.get("version"),
                "hooks": list(sorted(set(p["hooks"]))),
            }
            for p in ps
        ]

    def _threads(self):
        if self.setting("num_sql_threads") == 0:
            return {"num_threads": 0, "threads": []}
        threads = list(threading.enumerate())
        d = {
            "num_threads": len(threads),
            "threads": [
                {"name": t.name, "ident": t.ident, "daemon": t.daemon} for t in threads
            ],
        }
        # Only available in Python 3.7+
        if hasattr(asyncio, "all_tasks"):
            tasks = asyncio.all_tasks()
            d.update(
                {
                    "num_tasks": len(tasks),
                    "tasks": [_cleaner_task_str(t) for t in tasks],
                }
            )
        return d

    def _actor(self, request):
        return {"actor": request.actor}

    def table_metadata(self, database, table):
        """Fetch table-specific metadata."""
        return (
            (self.metadata("databases") or {})
            .get(database, {})
            .get("tables", {})
            .get(table, {})
        )

    def _register_renderers(self):
        """Register output renderers which output data in custom formats."""
        # Built-in renderers
        self.renderers["json"] = (json_renderer, lambda: True)

        # Hooks
        hook_renderers = []
        # pylint: disable=no-member
        for hook in pm.hook.register_output_renderer(datasette=self):
            if type(hook) is list:
                hook_renderers += hook
            else:
                hook_renderers.append(hook)

        for renderer in hook_renderers:
            self.renderers[renderer["extension"]] = (
                # It used to be called "callback" - remove this in Datasette 1.0
                renderer.get("render") or renderer["callback"],
                renderer.get("can_render") or (lambda: True),
            )

    async def render_template(
        self, templates, context=None, request=None, view_name=None
    ):
        if not self._startup_invoked:
            raise Exception("render_template() called before await ds.invoke_startup()")
        context = context or {}
        if isinstance(templates, Template):
            template = templates
        else:
            if isinstance(templates, str):
                templates = [templates]
            template = self.jinja_env.select_template(templates)
        body_scripts = []
        # pylint: disable=no-member
        for extra_script in pm.hook.extra_body_script(
            template=template.name,
            database=context.get("database"),
            table=context.get("table"),
            columns=context.get("columns"),
            view_name=view_name,
            request=request,
            datasette=self,
        ):
            extra_script = await await_me_maybe(extra_script)
            if isinstance(extra_script, dict):
                script = extra_script["script"]
                module = bool(extra_script.get("module"))
            else:
                script = extra_script
                module = False
            body_scripts.append({"script": Markup(script), "module": module})

        extra_template_vars = {}
        # pylint: disable=no-member
        for extra_vars in pm.hook.extra_template_vars(
            template=template.name,
            database=context.get("database"),
            table=context.get("table"),
            columns=context.get("columns"),
            view_name=view_name,
            request=request,
            datasette=self,
        ):
            extra_vars = await await_me_maybe(extra_vars)
            assert isinstance(extra_vars, dict), "extra_vars is of type {}".format(
                type(extra_vars)
            )
            extra_template_vars.update(extra_vars)

        async def menu_links():
            links = []
            for hook in pm.hook.menu_links(
                datasette=self,
                actor=request.actor if request else None,
                request=request or None,
            ):
                extra_links = await await_me_maybe(hook)
                if extra_links:
                    links.extend(extra_links)
            return links

        template_context = {
            **context,
            **{
                "request": request,
                "crumb_items": self._crumb_items,
                "urls": self.urls,
                "actor": request.actor if request else None,
                "menu_links": menu_links,
                "display_actor": display_actor,
                "show_logout": request is not None
                and "ds_actor" in request.cookies
                and request.actor,
                "app_css_hash": self.app_css_hash(),
                "zip": zip,
                "body_scripts": body_scripts,
                "format_bytes": format_bytes,
                "show_messages": lambda: self._show_messages(request),
                "extra_css_urls": await self._asset_urls(
                    "extra_css_urls", template, context, request, view_name
                ),
                "extra_js_urls": await self._asset_urls(
                    "extra_js_urls", template, context, request, view_name
                ),
                "base_url": self.setting("base_url"),
                "csrftoken": request.scope["csrftoken"] if request else lambda: "",
            },
            **extra_template_vars,
        }
        if request and request.args.get("_context") and self.setting("template_debug"):
            return "<pre>{}</pre>".format(
                escape(json.dumps(template_context, default=repr, indent=4))
            )

        return await template.render_async(template_context)

    async def _asset_urls(self, key, template, context, request, view_name):
        # Flatten list-of-lists from plugins:
        seen_urls = set()
        collected = []
        for hook in getattr(pm.hook, key)(
            template=template.name,
            database=context.get("database"),
            table=context.get("table"),
            columns=context.get("columns"),
            view_name=view_name,
            request=request,
            datasette=self,
        ):
            hook = await await_me_maybe(hook)
            collected.extend(hook)
        collected.extend(self.metadata(key) or [])
        output = []
        for url_or_dict in collected:
            if isinstance(url_or_dict, dict):
                url = url_or_dict["url"]
                sri = url_or_dict.get("sri")
                module = bool(url_or_dict.get("module"))
            else:
                url = url_or_dict
                sri = None
                module = False
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if url.startswith("/"):
                # Take base_url into account:
                url = self.urls.path(url)
            script = {"url": url}
            if sri:
                script["sri"] = sri
            if module:
                script["module"] = True
            output.append(script)
        return output

    def _routes(self):
        routes = []

        for routes_to_add in pm.hook.register_routes(datasette=self):
            for regex, view_fn in routes_to_add:
                routes.append((regex, wrap_view(view_fn, self)))

        def add_route(view, regex):
            routes.append((regex, view))

        add_route(IndexView.as_view(self), r"/(\.(?P<format>jsono?))?$")
        # TODO: /favicon.ico and /-/static/ deserve far-future cache expires
        add_route(favicon, "/favicon.ico")

        add_route(
            asgi_static(app_root / "datasette" / "static"), r"/-/static/(?P<path>.*)$"
        )
        for path, dirname in self.static_mounts:
            add_route(asgi_static(dirname), r"/" + path + "/(?P<path>.*)$")

        # Mount any plugin static/ directories
        for plugin in get_plugins():
            if plugin["static_path"]:
                add_route(
                    asgi_static(plugin["static_path"]),
                    f"/-/static-plugins/{plugin['name']}/(?P<path>.*)$",
                )
                # Support underscores in name in addition to hyphens, see https://github.com/simonw/datasette/issues/611
                add_route(
                    asgi_static(plugin["static_path"]),
                    "/-/static-plugins/{}/(?P<path>.*)$".format(
                        plugin["name"].replace("-", "_")
                    ),
                )
        add_route(
            permanent_redirect(
                "/_memory", forward_query_string=True, forward_rest=True
            ),
            r"/:memory:(?P<rest>.*)$",
        )
        add_route(
            JsonDataView.as_view(self, "metadata.json", lambda: self.metadata()),
            r"/-/metadata(\.(?P<format>json))?$",
        )
        add_route(
            JsonDataView.as_view(self, "versions.json", self._versions),
            r"/-/versions(\.(?P<format>json))?$",
        )
        add_route(
            JsonDataView.as_view(
                self, "plugins.json", self._plugins, needs_request=True
            ),
            r"/-/plugins(\.(?P<format>json))?$",
        )
        add_route(
            JsonDataView.as_view(self, "settings.json", lambda: self._settings),
            r"/-/settings(\.(?P<format>json))?$",
        )
        add_route(
            permanent_redirect("/-/settings.json"),
            r"/-/config.json",
        )
        add_route(
            permanent_redirect("/-/settings"),
            r"/-/config",
        )
        add_route(
            JsonDataView.as_view(self, "threads.json", self._threads),
            r"/-/threads(\.(?P<format>json))?$",
        )
        add_route(
            JsonDataView.as_view(self, "databases.json", self._connected_databases),
            r"/-/databases(\.(?P<format>json))?$",
        )
        add_route(
            JsonDataView.as_view(self, "actor.json", self._actor, needs_request=True),
            r"/-/actor(\.(?P<format>json))?$",
        )
        add_route(
            AuthTokenView.as_view(self),
            r"/-/auth-token$",
        )
        add_route(
            LogoutView.as_view(self),
            r"/-/logout$",
        )
        add_route(
            PermissionsDebugView.as_view(self),
            r"/-/permissions$",
        )
        add_route(
            MessagesDebugView.as_view(self),
            r"/-/messages$",
        )
        add_route(
            AllowDebugView.as_view(self),
            r"/-/allow-debug$",
        )
        add_route(
            PatternPortfolioView.as_view(self),
            r"/-/patterns$",
        )
        add_route(DatabaseDownload.as_view(self), r"/(?P<database>[^\/\.]+)\.db$")
        add_route(
            DatabaseView.as_view(self), r"/(?P<database>[^\/\.]+)(\.(?P<format>\w+))?$"
        )
        add_route(
            TableView.as_view(self),
            r"/(?P<database>[^\/\.]+)/(?P<table>[^\/\.]+)(\.(?P<format>\w+))?$",
        )
        add_route(
            RowView.as_view(self),
            r"/(?P<database>[^\/\.]+)/(?P<table>[^/]+?)/(?P<pks>[^/]+?)(\.(?P<format>\w+))?$",
        )
        return [
            # Compile any strings to regular expressions
            ((re.compile(pattern) if isinstance(pattern, str) else pattern), view)
            for pattern, view in routes
        ]

    def app(self):
        """Returns an ASGI app function that serves the whole of Datasette"""
        routes = self._routes()
        self._register_custom_units()

        async def setup_db():
            # First time server starts up, calculate table counts for immutable databases
            for database in self.databases.values():
                if not database.is_mutable:
                    await database.table_counts(limit=60 * 60 * 1000)

        asgi = asgi_csrf.asgi_csrf(
            DatasetteRouter(self, routes),
            signing_secret=self._secret,
            cookie_name="ds_csrftoken",
            skip_if_scope=lambda scope: any(
                pm.hook.skip_csrf(datasette=self, scope=scope)
            ),
        )
        if self.setting("trace_debug"):
            asgi = AsgiTracer(asgi)
        asgi = AsgiLifespan(asgi)
        asgi = AsgiRunOnFirstRequest(asgi, on_startup=[setup_db, self.invoke_startup])
        for wrapper in pm.hook.asgi_wrapper(datasette=self):
            asgi = wrapper(asgi)
        return asgi


class DatasetteRouter:
    def __init__(self, datasette, routes):
        self.ds = datasette
        self.routes = routes or []
        # Build a list of pages/blah/{name}.html matching expressions
        pattern_templates = [
            filepath
            for filepath in self.ds.jinja_env.list_templates()
            if "{" in filepath and filepath.startswith("pages/")
        ]
        self.page_routes = [
            (route_pattern_from_filepath(filepath[len("pages/") :]), filepath)
            for filepath in pattern_templates
        ]

    async def __call__(self, scope, receive, send):
        # Because we care about "foo/bar" v.s. "foo%2Fbar" we decode raw_path ourselves
        path = scope["path"]
        raw_path = scope.get("raw_path")
        if raw_path:
            path = raw_path.decode("ascii")
        path = path.partition("?")[0]
        return await self.route_path(scope, receive, send, path)

    async def route_path(self, scope, receive, send, path):
        # Strip off base_url if present before routing
        base_url = self.ds.setting("base_url")
        if base_url != "/" and path.startswith(base_url):
            path = "/" + path[len(base_url) :]
            scope = dict(scope, route_path=path)
        request = Request(scope, receive)
        # Populate request_messages if ds_messages cookie is present
        try:
            request._messages = self.ds.unsign(
                request.cookies.get("ds_messages", ""), "messages"
            )
        except BadSignature:
            pass

        scope_modifications = {}
        # Apply force_https_urls, if set
        if (
            self.ds.setting("force_https_urls")
            and scope["type"] == "http"
            and scope.get("scheme") != "https"
        ):
            scope_modifications["scheme"] = "https"
        # Handle authentication
        default_actor = scope.get("actor") or None
        actor = None
        for actor in pm.hook.actor_from_request(datasette=self.ds, request=request):
            actor = await await_me_maybe(actor)
            if actor:
                break
        scope_modifications["actor"] = actor or default_actor
        scope = dict(scope, **scope_modifications)

        match, view = resolve_routes(self.routes, path)

        if match is None:
            return await self.handle_404(request, send)

        new_scope = dict(scope, url_route={"kwargs": match.groupdict()})
        request.scope = new_scope
        try:
            response = await view(request, send)
            if response:
                self.ds._write_messages_to_response(request, response)
                await response.asgi_send(send)
            return
        except NotFound as exception:
            return await self.handle_404(request, send, exception)
        except Forbidden as exception:
            # Try the forbidden() plugin hook
            for custom_response in pm.hook.forbidden(
                datasette=self.ds, request=request, message=exception.args[0]
            ):
                custom_response = await await_me_maybe(custom_response)
                assert (
                    custom_response
                ), "Default forbidden() hook should have been called"
                return await custom_response.asgi_send(send)
        except Exception as exception:
            return await self.handle_exception(request, send, exception)

    async def handle_404(self, request, send, exception=None):
        # If path contains % encoding, redirect to tilde encoding
        if "%" in request.path:
            # Try the same path but with "%" replaced by "~"
            # and "~" replaced with "~7E"
            # and "." replaced with "~2E"
            new_path = (
                request.path.replace("~", "~7E").replace("%", "~").replace(".", "~2E")
            )
            if request.query_string:
                new_path += "?{}".format(request.query_string)
            await asgi_send_redirect(send, new_path)
            return
        # If URL has a trailing slash, redirect to URL without it
        path = request.scope.get(
            "raw_path", request.scope["path"].encode("utf8")
        ).partition(b"?")[0]
        context = {}
        if path.endswith(b"/"):
            path = path.rstrip(b"/")
            if request.scope["query_string"]:
                path += b"?" + request.scope["query_string"]
            await asgi_send_redirect(send, path.decode("latin1"))
        else:
            # Is there a pages/* template matching this path?
            route_path = request.scope.get("route_path", request.scope["path"])
            # Jinja requires template names to use "/" even on Windows
            template_name = "pages" + route_path + ".html"
            try:
                template = self.ds.jinja_env.select_template([template_name])
            except TemplateNotFound:
                template = None
            if template is None:
                # Try for a pages/blah/{name}.html template match
                for regex, wildcard_template in self.page_routes:
                    match = regex.match(route_path)
                    if match is not None:
                        context.update(match.groupdict())
                        template = wildcard_template
                        break

            if template:
                headers = {}
                status = [200]

                def custom_header(name, value):
                    headers[name] = value
                    return ""

                def custom_status(code):
                    status[0] = code
                    return ""

                def custom_redirect(location, code=302):
                    status[0] = code
                    headers["Location"] = location
                    return ""

                def raise_404(message=""):
                    raise NotFoundExplicit(message)

                context.update(
                    {
                        "custom_header": custom_header,
                        "custom_status": custom_status,
                        "custom_redirect": custom_redirect,
                        "raise_404": raise_404,
                    }
                )
                try:
                    body = await self.ds.render_template(
                        template,
                        context,
                        request=request,
                        view_name="page",
                    )
                except NotFoundExplicit as e:
                    await self.handle_exception(request, send, e)
                    return
                # Pull content-type out into separate parameter
                content_type = "text/html; charset=utf-8"
                matches = [k for k in headers if k.lower() == "content-type"]
                if matches:
                    content_type = headers[matches[0]]
                await asgi_send(
                    send,
                    body,
                    status=status[0],
                    headers=headers,
                    content_type=content_type,
                )
            else:
                await self.handle_exception(request, send, exception or NotFound("404"))

    async def handle_exception(self, request, send, exception):
        responses = []
        for hook in pm.hook.handle_exception(
            datasette=self.ds,
            request=request,
            exception=exception,
        ):
            response = await await_me_maybe(hook)
            if response is not None:
                responses.append(response)

        assert responses, "Default exception handler should have returned something"
        # Even if there are multiple responses use just the first one
        response = responses[0]
        await response.asgi_send(send)


_cleaner_task_str_re = re.compile(r"\S*site-packages/")


def _cleaner_task_str(task):
    s = str(task)
    # This has something like the following in it:
    # running at /Users/simonw/Dropbox/Development/datasette/venv-3.7.5/lib/python3.7/site-packages/uvicorn/main.py:361>
    # Clean up everything up to and including site-packages
    return _cleaner_task_str_re.sub("", s)


def wrap_view(view_fn, datasette):
    @functools.wraps(view_fn)
    async def async_view_fn(request, send):
        if inspect.iscoroutinefunction(view_fn):
            response = await async_call_with_supported_arguments(
                view_fn,
                scope=request.scope,
                receive=request.receive,
                send=send,
                request=request,
                datasette=datasette,
            )
        else:
            response = call_with_supported_arguments(
                view_fn,
                scope=request.scope,
                receive=request.receive,
                send=send,
                request=request,
                datasette=datasette,
            )
        if response is not None:
            return response

    return async_view_fn


def permanent_redirect(path, forward_query_string=False, forward_rest=False):
    return wrap_view(
        lambda request, send: Response.redirect(
            path
            + (request.url_vars["rest"] if forward_rest else "")
            + (
                ("?" + request.query_string)
                if forward_query_string and request.query_string
                else ""
            ),
            status=301,
        ),
        datasette=None,
    )


_curly_re = re.compile(r"({.*?})")


def route_pattern_from_filepath(filepath):
    # Drop the ".html" suffix
    if filepath.endswith(".html"):
        filepath = filepath[: -len(".html")]
    re_bits = ["/"]
    for bit in _curly_re.split(filepath):
        if _curly_re.match(bit):
            re_bits.append(f"(?P<{bit[1:-1]}>[^/]*)")
        else:
            re_bits.append(re.escape(bit))
    return re.compile("^" + "".join(re_bits) + "$")


class NotFoundExplicit(NotFound):
    pass


class DatasetteClient:
    def __init__(self, ds):
        self.ds = ds
        self.app = ds.app()

    def _fix(self, path, avoid_path_rewrites=False):
        if not isinstance(path, PrefixedUrlString) and not avoid_path_rewrites:
            path = self.ds.urls.path(path)
        if path.startswith("/"):
            path = f"http://localhost{path}"
        return path

    async def get(self, path, **kwargs):
        async with httpx.AsyncClient(app=self.app) as client:
            return await client.get(self._fix(path), **kwargs)

    async def options(self, path, **kwargs):
        async with httpx.AsyncClient(app=self.app) as client:
            return await client.options(self._fix(path), **kwargs)

    async def head(self, path, **kwargs):
        async with httpx.AsyncClient(app=self.app) as client:
            return await client.head(self._fix(path), **kwargs)

    async def post(self, path, **kwargs):
        async with httpx.AsyncClient(app=self.app) as client:
            return await client.post(self._fix(path), **kwargs)

    async def put(self, path, **kwargs):
        async with httpx.AsyncClient(app=self.app) as client:
            return await client.put(self._fix(path), **kwargs)

    async def patch(self, path, **kwargs):
        async with httpx.AsyncClient(app=self.app) as client:
            return await client.patch(self._fix(path), **kwargs)

    async def delete(self, path, **kwargs):
        async with httpx.AsyncClient(app=self.app) as client:
            return await client.delete(self._fix(path), **kwargs)

    async def request(self, method, path, **kwargs):
        avoid_path_rewrites = kwargs.pop("avoid_path_rewrites", None)
        async with httpx.AsyncClient(app=self.app) as client:
            return await client.request(
                method, self._fix(path, avoid_path_rewrites), **kwargs
            )
