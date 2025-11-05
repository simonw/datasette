from __future__ import annotations

from asgi_csrf import Errors
import asyncio
from typing import TYPE_CHECKING, Any, Dict, Iterable, List

if TYPE_CHECKING:
    from datasette.permissions import AllowedResource, Resource
import asgi_csrf
import collections
import dataclasses
import datetime
import functools
import glob
import hashlib
import httpx
import importlib.metadata
import inspect
from itsdangerous import BadSignature
import json
import os
import re
import secrets
import sys
import threading
import time
import types
import urllib.parse
from concurrent import futures
from pathlib import Path

from markupsafe import Markup, escape
from itsdangerous import URLSafeSerializer
from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PrefixLoader,
)
from jinja2.environment import Template
from jinja2.exceptions import TemplateNotFound

from .events import Event
from .views import Context
from .views.database import database_download, DatabaseView, TableCreateView, QueryView
from .views.index import IndexView
from .views.special import (
    JsonDataView,
    PatternPortfolioView,
    AuthTokenView,
    ApiExplorerView,
    CreateTokenView,
    LogoutView,
    AllowDebugView,
    PermissionsDebugView,
    MessagesDebugView,
    AllowedResourcesView,
    PermissionRulesView,
    PermissionCheckView,
    TablesView,
)
from .views.table import (
    TableInsertView,
    TableUpsertView,
    TableDropView,
    table_view,
)
from .views.row import RowView, RowDeleteView, RowUpdateView
from .renderer import json_renderer
from .url_builder import Urls
from .database import Database, QueryInterrupted

from .utils import (
    PaginatedResources,
    PrefixedUrlString,
    SPATIALITE_FUNCTIONS,
    StartupError,
    async_call_with_supported_arguments,
    await_me_maybe,
    baseconv,
    call_with_supported_arguments,
    detect_json1,
    display_actor,
    escape_css_string,
    escape_sqlite,
    find_spatialite,
    format_bytes,
    module_from_path,
    move_plugins_and_allow,
    move_table_config,
    parse_metadata,
    resolve_env_secrets,
    resolve_routes,
    tilde_decode,
    tilde_encode,
    to_css_class,
    urlsafe_components,
    redact_keys,
    row_sql_params_pks,
)
from .utils.asgi import (
    AsgiLifespan,
    Forbidden,
    NotFound,
    DatabaseNotFound,
    TableNotFound,
    RowNotFound,
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

from .resources import DatabaseResource, TableResource

app_root = Path(__file__).parent.parent


@dataclasses.dataclass
class PermissionCheck:
    """Represents a logged permission check for debugging purposes."""

    when: str
    actor: Dict[str, Any] | None
    action: str
    parent: str | None
    child: str | None
    result: bool


# https://github.com/simonw/datasette/issues/283#issuecomment-781591015
SQLITE_LIMIT_ATTACHED = 10

INTERNAL_DB_NAME = "__INTERNAL__"

Setting = collections.namedtuple("Setting", ("name", "default", "help"))
SETTINGS = (
    Setting("default_page_size", 100, "Default page size for the table view"),
    Setting(
        "max_returned_rows",
        1000,
        "Maximum rows that can be returned from a table or custom query",
    ),
    Setting(
        "max_insert_rows",
        100,
        "Maximum rows that can be inserted at a time using the bulk insert API",
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
        "allow_download",
        True,
        "Allow users to download the original SQLite database files",
    ),
    Setting(
        "allow_signed_tokens",
        True,
        "Allow users to create and use signed API tokens",
    ),
    Setting(
        "default_allow_sql",
        True,
        "Allow anyone to run arbitrary SQL queries",
    ),
    Setting(
        "max_signed_tokens_ttl",
        0,
        "Maximum allowed expiry time for signed API tokens",
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

DEFAULT_NOT_SET = object()


ResourcesSQL = collections.namedtuple("ResourcesSQL", ("sql", "params"))


async def favicon(request, send):
    await asgi_send_file(
        send,
        str(FAVICON_PATH),
        content_type="image/png",
        headers={"Cache-Control": "max-age=3600, immutable, public"},
    )


ResolvedTable = collections.namedtuple("ResolvedTable", ("db", "table", "is_view"))
ResolvedRow = collections.namedtuple(
    "ResolvedRow", ("db", "table", "sql", "params", "pks", "pk_values", "row")
)


def _to_string(value):
    if isinstance(value, str):
        return value
    else:
        return json.dumps(value, default=str)


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
        config=None,
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
        internal=None,
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
        self.actions = {}  # .invoke_startup() will populate this
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
            self.add_database(
                Database(self, is_mutable=False, is_memory=True), name="_memory"
            )
        for file in self.files:
            self.add_database(
                Database(self, file, is_mutable=file not in self.immutables)
            )

        self.internal_db_created = False
        if internal is None:
            self._internal_database = Database(self, memory_name=secrets.token_hex())
        else:
            self._internal_database = Database(self, path=internal, mode="rwc")
        self._internal_database.name = INTERNAL_DB_NAME

        self.cache_headers = cache_headers
        self.cors = cors
        config_files = []
        metadata_files = []
        if config_dir:
            metadata_files = [
                config_dir / filename
                for filename in ("metadata.json", "metadata.yaml", "metadata.yml")
                if (config_dir / filename).exists()
            ]
            config_files = [
                config_dir / filename
                for filename in ("datasette.json", "datasette.yaml", "datasette.yml")
                if (config_dir / filename).exists()
            ]
        if config_dir and metadata_files and not metadata:
            with metadata_files[0].open() as fp:
                metadata = parse_metadata(fp.read())

        if config_dir and config_files and not config:
            with config_files[0].open() as fp:
                config = parse_metadata(fp.read())

        # Move any "plugins" and "allow" settings from metadata to config - updates them in place
        metadata = metadata or {}
        config = config or {}
        metadata, config = move_plugins_and_allow(metadata, config)
        # Now migrate any known table configuration settings over as well
        metadata, config = move_table_config(metadata, config)

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
        if config_dir and (config_dir / "datasette.json").exists() and not config:
            config = json.loads((config_dir / "datasette.json").read_text())

        config = config or {}
        config_settings = config.get("settings") or {}

        # Validate settings from config file
        for key, value in config_settings.items():
            if key not in DEFAULT_SETTINGS:
                raise StartupError(f"Invalid setting '{key}' in config file")
            # Validate type matches expected type from DEFAULT_SETTINGS
            if value is not None:  # Allow None/null values
                expected_type = type(DEFAULT_SETTINGS[key])
                actual_type = type(value)
                if actual_type != expected_type:
                    raise StartupError(
                        f"Setting '{key}' in config file has incorrect type. "
                        f"Expected {expected_type.__name__}, got {actual_type.__name__}. "
                        f"Value: {value!r}. "
                        f"Hint: In YAML/JSON config files, remove quotes from boolean and integer values."
                    )

        # Validate settings from constructor parameter
        if settings:
            for key, value in settings.items():
                if key not in DEFAULT_SETTINGS:
                    raise StartupError(f"Invalid setting '{key}' in settings parameter")
                if value is not None:
                    expected_type = type(DEFAULT_SETTINGS[key])
                    actual_type = type(value)
                    if actual_type != expected_type:
                        raise StartupError(
                            f"Setting '{key}' in settings parameter has incorrect type. "
                            f"Expected {expected_type.__name__}, got {actual_type.__name__}. "
                            f"Value: {value!r}"
                        )

        self.config = config
        # CLI settings should overwrite datasette.json settings
        self._settings = dict(DEFAULT_SETTINGS, **(config_settings), **(settings or {}))
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
        environment = Environment(
            loader=template_loader,
            autoescape=True,
            enable_async=True,
            # undefined=StrictUndefined,
        )
        environment.filters["escape_css_string"] = escape_css_string
        environment.filters["quote_plus"] = urllib.parse.quote_plus
        self._jinja_env = environment
        environment.filters["escape_sqlite"] = escape_sqlite
        environment.filters["to_css_class"] = to_css_class
        self._register_renderers()
        self._permission_checks = collections.deque(maxlen=200)
        self._root_token = secrets.token_hex(32)
        self.root_enabled = False
        self.client = DatasetteClient(self)

    async def apply_metadata_json(self):
        # Apply any metadata entries from metadata.json to the internal tables
        # step 1: top-level metadata
        for key in self._metadata_local or {}:
            if key == "databases":
                continue
            value = self._metadata_local[key]
            await self.set_instance_metadata(key, _to_string(value))

        # step 2: database-level metadata
        for dbname, db in self._metadata_local.get("databases", {}).items():
            for key, value in db.items():
                if key in ("tables", "queries"):
                    continue
                await self.set_database_metadata(dbname, key, _to_string(value))

            # step 3: table-level metadata
            for tablename, table in db.get("tables", {}).items():
                for key, value in table.items():
                    if key == "columns":
                        continue
                    await self.set_resource_metadata(
                        dbname, tablename, key, _to_string(value)
                    )

                # step 4: column-level metadata (only descriptions in metadata.json)
                for columnname, column_description in table.get("columns", {}).items():
                    await self.set_column_metadata(
                        dbname, tablename, columnname, "description", column_description
                    )

            # TODO(alex) is metadata.json was loaded in, and --internal is not memory, then log
            # a warning to user that they should delete their metadata.json file

    def get_jinja_environment(self, request: Request = None) -> Environment:
        environment = self._jinja_env
        if request:
            for environment in pm.hook.jinja2_environment_from_request(
                datasette=self, request=request, env=environment
            ):
                pass
        return environment

    def get_action(self, name_or_abbr: str):
        """
        Returns an Action object for the given name or abbreviation. Returns None if not found.
        """
        if name_or_abbr in self.actions:
            return self.actions[name_or_abbr]
        # Try abbreviation
        for action in self.actions.values():
            if action.abbr == name_or_abbr:
                return action
        return None

    async def refresh_schemas(self):
        if self._refresh_schemas_lock.locked():
            return
        async with self._refresh_schemas_lock:
            await self._refresh_schemas()

    async def _refresh_schemas(self):
        internal_db = self.get_internal_database()
        if not self.internal_db_created:
            await init_internal_db(internal_db)
            await self.apply_metadata_json()
            self.internal_db_created = True
        current_schema_versions = {
            row["database_name"]: row["schema_version"]
            for row in await internal_db.execute(
                "select database_name, schema_version from catalog_databases"
            )
        }
        for database_name, db in self.databases.items():
            schema_version = (await db.execute("PRAGMA schema_version")).first()[0]
            # Compare schema versions to see if we should skip it
            if schema_version == current_schema_versions.get(database_name):
                continue
            placeholders = "(?, ?, ?, ?)"
            values = [database_name, str(db.path), db.is_memory, schema_version]
            if db.path is None:
                placeholders = "(?, null, ?, ?)"
                values = [database_name, db.is_memory, schema_version]
            await internal_db.execute_write(
                """
                INSERT OR REPLACE INTO catalog_databases (database_name, path, is_memory, schema_version)
                VALUES {}
            """.format(
                    placeholders
                ),
                values,
            )
            await populate_schema_tables(internal_db, db)

    @property
    def urls(self):
        return Urls(self)

    async def invoke_startup(self):
        # This must be called for Datasette to be in a usable state
        if self._startup_invoked:
            return
        # Register event classes
        event_classes = []
        for hook in pm.hook.register_events(datasette=self):
            extra_classes = await await_me_maybe(hook)
            if extra_classes:
                event_classes.extend(extra_classes)
        self.event_classes = tuple(event_classes)

        # Register actions, but watch out for duplicate name/abbr
        action_names = {}
        action_abbrs = {}
        for hook in pm.hook.register_actions(datasette=self):
            if hook:
                for action in hook:
                    if (
                        action.name in action_names
                        and action != action_names[action.name]
                    ):
                        raise StartupError(
                            "Duplicate action name: {}".format(action.name)
                        )
                    if (
                        action.abbr
                        and action.abbr in action_abbrs
                        and action != action_abbrs[action.abbr]
                    ):
                        raise StartupError(
                            "Duplicate action abbr: {}".format(action.abbr)
                        )
                    action_names[action.name] = action
                    if action.abbr:
                        action_abbrs[action.abbr] = action
                    self.actions[action.name] = action

        for hook in pm.hook.prepare_jinja2_environment(
            env=self._jinja_env, datasette=self
        ):
            await await_me_maybe(hook)
        for hook in pm.hook.startup(datasette=self):
            await await_me_maybe(hook)
        self._startup_invoked = True

    def sign(self, value, namespace="default"):
        return URLSafeSerializer(self._secret, namespace).dumps(value)

    def unsign(self, signed, namespace="default"):
        return URLSafeSerializer(self._secret, namespace).loads(signed)

    def create_token(
        self,
        actor_id: str,
        *,
        expires_after: int | None = None,
        restrict_all: Iterable[str] | None = None,
        restrict_database: Dict[str, Iterable[str]] | None = None,
        restrict_resource: Dict[str, Dict[str, Iterable[str]]] | None = None,
    ):
        token = {"a": actor_id, "t": int(time.time())}
        if expires_after:
            token["d"] = expires_after

        def abbreviate_action(action):
            # rename to abbr if possible
            action_obj = self.actions.get(action)
            if not action_obj:
                return action
            return action_obj.abbr or action

        if expires_after:
            token["d"] = expires_after
        if restrict_all or restrict_database or restrict_resource:
            token["_r"] = {}
            if restrict_all:
                token["_r"]["a"] = [abbreviate_action(a) for a in restrict_all]
            if restrict_database:
                token["_r"]["d"] = {}
                for database, actions in restrict_database.items():
                    token["_r"]["d"][database] = [abbreviate_action(a) for a in actions]
            if restrict_resource:
                token["_r"]["r"] = {}
                for database, resources in restrict_resource.items():
                    for resource, actions in resources.items():
                        token["_r"]["r"].setdefault(database, {})[resource] = [
                            abbreviate_action(a) for a in actions
                        ]
        return "dstok_{}".format(self.sign(token, namespace="token"))

    def get_database(self, name=None, route=None):
        if route is not None:
            matches = [db for db in self.databases.values() if db.route == route]
            if not matches:
                raise KeyError
            return matches[0]
        if name is None:
            name = [key for key in self.databases.keys()][0]
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
        self.get_database(name).close()
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

    async def get_instance_metadata(self):
        rows = await self.get_internal_database().execute(
            """
              SELECT
                key,
                value
              FROM metadata_instance
            """
        )
        return dict(rows)

    async def get_database_metadata(self, database_name: str):
        rows = await self.get_internal_database().execute(
            """
              SELECT
                key,
                value
              FROM metadata_databases
              WHERE database_name = ?
            """,
            [database_name],
        )
        return dict(rows)

    async def get_resource_metadata(self, database_name: str, resource_name: str):
        rows = await self.get_internal_database().execute(
            """
              SELECT
                key,
                value
              FROM metadata_resources
              WHERE database_name = ?
                AND resource_name = ?
            """,
            [database_name, resource_name],
        )
        return dict(rows)

    async def get_column_metadata(
        self, database_name: str, resource_name: str, column_name: str
    ):
        rows = await self.get_internal_database().execute(
            """
              SELECT
                key,
                value
              FROM metadata_columns
              WHERE database_name = ?
                AND resource_name = ?
                AND column_name = ?
            """,
            [database_name, resource_name, column_name],
        )
        return dict(rows)

    async def set_instance_metadata(self, key: str, value: str):
        # TODO upsert only supported on SQLite 3.24.0 (2018-06-04)
        await self.get_internal_database().execute_write(
            """
              INSERT INTO metadata_instance(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """,
            [key, value],
        )

    async def set_database_metadata(self, database_name: str, key: str, value: str):
        # TODO upsert only supported on SQLite 3.24.0 (2018-06-04)
        await self.get_internal_database().execute_write(
            """
              INSERT INTO metadata_databases(database_name, key, value)
                VALUES(?, ?, ?)
                ON CONFLICT(database_name, key) DO UPDATE SET value = excluded.value;
            """,
            [database_name, key, value],
        )

    async def set_resource_metadata(
        self, database_name: str, resource_name: str, key: str, value: str
    ):
        # TODO upsert only supported on SQLite 3.24.0 (2018-06-04)
        await self.get_internal_database().execute_write(
            """
              INSERT INTO metadata_resources(database_name, resource_name, key, value)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(database_name, resource_name, key) DO UPDATE SET value = excluded.value;
            """,
            [database_name, resource_name, key, value],
        )

    async def set_column_metadata(
        self,
        database_name: str,
        resource_name: str,
        column_name: str,
        key: str,
        value: str,
    ):
        # TODO upsert only supported on SQLite 3.24.0 (2018-06-04)
        await self.get_internal_database().execute_write(
            """
              INSERT INTO metadata_columns(database_name, resource_name, column_name, key, value)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(database_name, resource_name, column_name, key) DO UPDATE SET value = excluded.value;
            """,
            [database_name, resource_name, column_name, key, value],
        )

    def get_internal_database(self):
        return self._internal_database

    def plugin_config(self, plugin_name, database=None, table=None, fallback=True):
        """Return config for plugin, falling back from specified database/table"""
        if database is None and table is None:
            config = self._plugin_config_top(plugin_name)
        else:
            config = self._plugin_config_nested(plugin_name, database, table, fallback)

        return resolve_env_secrets(config, os.environ)

    def _plugin_config_top(self, plugin_name):
        """Returns any top-level plugin configuration for the specified plugin."""
        return ((self.config or {}).get("plugins") or {}).get(plugin_name)

    def _plugin_config_nested(self, plugin_name, database, table=None, fallback=True):
        """Returns any database or table-level plugin configuration for the specified plugin."""
        db_config = ((self.config or {}).get("databases") or {}).get(database)

        # if there's no db-level configuration, then return early, falling back to top-level if needed
        if not db_config:
            return self._plugin_config_top(plugin_name) if fallback else None

        db_plugin_config = (db_config.get("plugins") or {}).get(plugin_name)

        if table:
            table_plugin_config = (
                ((db_config.get("tables") or {}).get(table) or {}).get("plugins") or {}
            ).get(plugin_name)

            # fallback to db_config or top-level config, in that order, if needed
            if table_plugin_config is None and fallback:
                return db_plugin_config or self._plugin_config_top(plugin_name)

            return table_plugin_config

        # fallback to top-level if needed
        if db_plugin_config is None and fallback:
            self._plugin_config_top(plugin_name)

        return db_plugin_config

    def app_css_hash(self):
        if not hasattr(self, "_app_css_hash"):
            with open(os.path.join(str(app_root), "datasette/static/app.css")) as fp:
                self._app_css_hash = hashlib.sha1(fp.read().encode("utf8")).hexdigest()[
                    :6
                ]
        return self._app_css_hash

    async def get_canned_queries(self, database_name, actor):
        queries = {}
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

    def _prepare_connection(self, conn, database):
        conn.row_factory = sqlite3.Row
        conn.text_factory = lambda x: str(x, "utf-8", "replace")
        if self.sqlite_extensions and database != INTERNAL_DB_NAME:
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
        if database != INTERNAL_DB_NAME:
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
        if await self.allowed(action="view-instance", actor=actor):
            crumbs.append({"href": self.urls.instance(), "label": "home"})
        # Database link
        if database:
            if await self.allowed(
                action="view-database",
                resource=DatabaseResource(database=database),
                actor=actor,
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
            if await self.allowed(
                action="view-table",
                resource=TableResource(database=database, table=table),
                actor=actor,
            ):
                crumbs.append(
                    {
                        "href": self.urls.table(database, table),
                        "label": table,
                    }
                )
        return crumbs

    async def actors_from_ids(
        self, actor_ids: Iterable[str | int]
    ) -> Dict[int | str, Dict]:
        result = pm.hook.actors_from_ids(datasette=self, actor_ids=actor_ids)
        if result is None:
            # Do the default thing
            return {actor_id: {"id": actor_id} for actor_id in actor_ids}
        result = await await_me_maybe(result)
        return result

    async def track_event(self, event: Event):
        assert isinstance(event, self.event_classes), "Invalid event type: {}".format(
            type(event)
        )
        for hook in pm.hook.track_event(datasette=self, event=event):
            await await_me_maybe(hook)

    def resource_for_action(self, action: str, parent: str | None, child: str | None):
        """
        Create a Resource instance for the given action with parent/child values.

        Looks up the action's resource_class and instantiates it with the
        provided parent and child identifiers.

        Args:
            action: The action name (e.g., "view-table", "view-query")
            parent: The parent resource identifier (e.g., database name)
            child: The child resource identifier (e.g., table/query name)

        Returns:
            A Resource instance of the appropriate subclass

        Raises:
            ValueError: If the action is unknown
        """
        from datasette.permissions import Resource

        action_obj = self.actions.get(action)
        if not action_obj:
            raise ValueError(f"Unknown action: {action}")

        resource_class = action_obj.resource_class
        instance = object.__new__(resource_class)
        Resource.__init__(instance, parent=parent, child=child)
        return instance

    async def check_visibility(
        self,
        actor: dict,
        action: str,
        resource: "Resource" | None = None,
    ):
        """
        Check if actor can see a resource and if it's private.

        Returns (visible, private) tuple:
        - visible: bool - can the actor see it?
        - private: bool - if visible, can anonymous users NOT see it?
        """
        from datasette.permissions import Resource

        # Validate that resource is a Resource object or None
        if resource is not None and not isinstance(resource, Resource):
            raise TypeError(f"resource must be a Resource subclass instance or None.")

        # Check if actor can see it
        if not await self.allowed(action=action, resource=resource, actor=actor):
            return False, False

        # Check if anonymous user can see it (for "private" flag)
        if not await self.allowed(action=action, resource=resource, actor=None):
            # Actor can see it but anonymous cannot - it's private
            return True, True

        # Both actor and anonymous can see it - it's public
        return True, False

    async def allowed_resources_sql(
        self,
        *,
        action: str,
        actor: dict | None = None,
        parent: str | None = None,
        include_is_private: bool = False,
    ) -> ResourcesSQL:
        """
        Build SQL query to get all resources the actor can access for the given action.

        Args:
            action: The action name (e.g., "view-table")
            actor: The actor dict (or None for unauthenticated)
            parent: Optional parent filter (e.g., database name) to limit results
            include_is_private: If True, include is_private column showing if anonymous cannot access

        Returns a namedtuple of (query: str, params: dict) that can be executed against the internal database.
        The query returns rows with (parent, child, reason) columns, plus is_private if requested.

        Example:
            query, params = await datasette.allowed_resources_sql(
                action="view-table",
                actor=actor,
                parent="mydb",
                include_is_private=True
            )
            result = await datasette.get_internal_database().execute(query, params)
        """
        from datasette.utils.actions_sql import build_allowed_resources_sql

        action_obj = self.actions.get(action)
        if not action_obj:
            raise ValueError(f"Unknown action: {action}")

        sql, params = await build_allowed_resources_sql(
            self, actor, action, parent=parent, include_is_private=include_is_private
        )
        return ResourcesSQL(sql, params)

    async def allowed_resources(
        self,
        action: str,
        actor: dict | None = None,
        *,
        parent: str | None = None,
        include_is_private: bool = False,
        include_reasons: bool = False,
        limit: int = 100,
        next: str | None = None,
    ) -> PaginatedResources:
        """
        Return paginated resources the actor can access for the given action.

        Uses SQL with keyset pagination to efficiently filter resources.
        Returns PaginatedResources with list of Resource instances and pagination metadata.

        Args:
            action: The action name (e.g., "view-table")
            actor: The actor dict (or None for unauthenticated)
            parent: Optional parent filter (e.g., database name) to limit results
            include_is_private: If True, adds a .private attribute to each Resource
            include_reasons: If True, adds a .reasons attribute with List[str] of permission reasons
            limit: Maximum number of results to return (1-1000, default 100)
            next: Keyset token from previous page for pagination

        Returns:
            PaginatedResources with:
                - resources: List of Resource objects for this page
                - next: Token for next page (None if no more results)

        Example:
            # Get first page of tables
            page = await datasette.allowed_resources("view-table", actor, limit=50)
            for table in page.resources:
                print(f"{table.parent}/{table.child}")

            # Get next page
            if page.next:
                next_page = await datasette.allowed_resources(
                    "view-table", actor, limit=50, next=page.next
                )

            # With reasons for debugging
            page = await datasette.allowed_resources(
                "view-table", actor, include_reasons=True
            )
            for table in page.resources:
                print(f"{table.child}: {table.reasons}")

            # Iterate through all results with async generator
            page = await datasette.allowed_resources("view-table", actor)
            async for table in page.all():
                print(table.child)
        """

        action_obj = self.actions.get(action)
        if not action_obj:
            raise ValueError(f"Unknown action: {action}")

        # Validate and cap limit
        limit = min(max(1, limit), 1000)

        # Get base SQL query
        query, params = await self.allowed_resources_sql(
            action=action,
            actor=actor,
            parent=parent,
            include_is_private=include_is_private,
        )

        # Add keyset pagination WHERE clause if next token provided
        if next:
            try:
                components = urlsafe_components(next)
                if len(components) >= 2:
                    last_parent, last_child = components[0], components[1]
                    # Keyset condition: (parent > last) OR (parent = last AND child > last)
                    keyset_where = """
                        (parent > :keyset_parent OR
                         (parent = :keyset_parent AND child > :keyset_child))
                    """
                    # Wrap original query and add keyset filter
                    query = f"SELECT * FROM ({query}) WHERE {keyset_where}"
                    params["keyset_parent"] = last_parent
                    params["keyset_child"] = last_child
            except (ValueError, KeyError):
                # Invalid token - ignore and start from beginning
                pass

        # Add LIMIT (fetch limit+1 to detect if there are more results)
        # Note: query from allowed_resources_sql() already includes ORDER BY parent, child
        query = f"{query} LIMIT :limit"
        params["limit"] = limit + 1

        # Execute query
        result = await self.get_internal_database().execute(query, params)
        rows = list(result.rows)

        # Check if truncated (got more than limit rows)
        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]  # Remove the extra row

        # Build Resource objects with optional attributes
        resources = []
        for row in rows:
            # row[0]=parent, row[1]=child, row[2]=reason, row[3]=is_private (if requested)
            resource = self.resource_for_action(action, parent=row[0], child=row[1])

            # Add reasons if requested
            if include_reasons:
                reason_json = row[2]
                try:
                    reasons_array = (
                        json.loads(reason_json) if isinstance(reason_json, str) else []
                    )
                    resource.reasons = [r for r in reasons_array if r is not None]
                except (json.JSONDecodeError, TypeError):
                    resource.reasons = [reason_json] if reason_json else []

            # Add private flag if requested
            if include_is_private:
                resource.private = bool(row[3])

            resources.append(resource)

        # Generate next token if there are more results
        next_token = None
        if truncated and resources:
            last_resource = resources[-1]
            # Use tilde-encoding like table pagination
            next_token = "{},{}".format(
                tilde_encode(str(last_resource.parent)),
                tilde_encode(str(last_resource.child)),
            )

        return PaginatedResources(
            resources=resources,
            next=next_token,
            _datasette=self,
            _action=action,
            _actor=actor,
            _parent=parent,
            _include_is_private=include_is_private,
            _include_reasons=include_reasons,
            _limit=limit,
        )

    async def allowed(
        self,
        *,
        action: str,
        resource: "Resource" = None,
        actor: dict | None = None,
    ) -> bool:
        """
        Check if actor can perform action on specific resource.

        Uses SQL to check permission for a single resource without fetching all resources.
        This is efficient - it does NOT call allowed_resources() and check membership.

        For global actions, resource should be None (or omitted).

        Example:
            from datasette.resources import TableResource
            can_view = await datasette.allowed(
                action="view-table",
                resource=TableResource(database="analytics", table="users"),
                actor=actor
            )

            # For global actions, resource can be omitted:
            can_debug = await datasette.allowed(action="permissions-debug", actor=actor)
        """
        from datasette.utils.actions_sql import check_permission_for_resource

        # For global actions, resource remains None

        # Check if this action has also_requires - if so, check that action first
        action_obj = self.actions.get(action)
        if action_obj and action_obj.also_requires:
            # Must have the required action first
            if not await self.allowed(
                action=action_obj.also_requires,
                resource=resource,
                actor=actor,
            ):
                return False

        # For global actions, resource is None
        parent = resource.parent if resource else None
        child = resource.child if resource else None

        result = await check_permission_for_resource(
            datasette=self,
            actor=actor,
            action=action,
            parent=parent,
            child=child,
        )

        # Log the permission check for debugging
        self._permission_checks.append(
            PermissionCheck(
                when=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                actor=actor,
                action=action,
                parent=parent,
                child=child,
                result=result,
            )
        )

        return result

    async def ensure_permission(
        self,
        *,
        action: str,
        resource: "Resource" = None,
        actor: dict | None = None,
    ):
        """
        Check if actor can perform action on resource, raising Forbidden if not.

        This is a convenience wrapper around allowed() that raises Forbidden
        instead of returning False. Use this when you want to enforce a permission
        check and halt execution if it fails.

        Example:
            from datasette.resources import TableResource

            # Will raise Forbidden if actor cannot view the table
            await datasette.ensure_permission(
                action="view-table",
                resource=TableResource(database="analytics", table="users"),
                actor=request.actor
            )

            # For instance-level actions, resource can be omitted:
            await datasette.ensure_permission(
                action="permissions-debug",
                actor=request.actor
            )
        """
        if not await self.allowed(action=action, resource=resource, actor=actor):
            raise Forbidden(action)

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

    async def expand_foreign_keys(self, actor, database, table, column, values):
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
        # Ensure user has permission to view the referenced table
        from datasette.resources import TableResource

        other_table = fk["other_table"]
        other_column = fk["other_column"]
        visible, _ = await self.check_visibility(
            actor,
            action="view-table",
            resource=TableResource(database=database, table=other_table),
        )
        if not visible:
            return {}
        label_column = await db.label_column_for_table(other_table)
        if not label_column:
            return {(fk["column"], value): str(value) for value in values}
        labeled_fks = {}
        sql = """
            select {other_column}, {label_column}
            from {other_table}
            where {other_column} in ({placeholders})
        """.format(
            other_column=escape_sqlite(other_column),
            label_column=escape_sqlite(label_column),
            other_table=escape_sqlite(other_table),
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
        ]

    def _versions(self):
        conn = sqlite3.connect(":memory:")
        self._prepare_connection(conn, "_memory")
        sqlite_version = conn.execute("select sqlite_version()").fetchone()[0]
        sqlite_extensions = {"json1": detect_json1(conn)}
        for extension, testsql, hasversion in (
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
                    info["pysqlite3"] = importlib.metadata.version(package)
                    break
                except importlib.metadata.PackageNotFoundError:
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

    def _actions(self):
        return [
            {
                "name": action.name,
                "abbr": action.abbr,
                "description": action.description,
                "takes_parent": action.takes_parent,
                "takes_child": action.takes_child,
                "resource_class": (
                    action.resource_class.__name__ if action.resource_class else None
                ),
                "also_requires": action.also_requires,
            }
            for action in sorted(self.actions.values(), key=lambda a: a.name)
        ]

    async def table_config(self, database: str, table: str) -> dict:
        """Return dictionary of configuration for specified table"""
        return (
            (self.config or {})
            .get("databases", {})
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
        self,
        templates: List[str] | str | Template,
        context: Dict[str, Any] | Context | None = None,
        request: Request | None = None,
        view_name: str | None = None,
    ):
        if not self._startup_invoked:
            raise Exception("render_template() called before await ds.invoke_startup()")
        context = context or {}
        if isinstance(templates, Template):
            template = templates
        else:
            if isinstance(templates, str):
                templates = [templates]
            template = self.get_jinja_environment(request).select_template(templates)
        if dataclasses.is_dataclass(context):
            context = dataclasses.asdict(context)
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
                "datasette_version": __version__,
            },
            **extra_template_vars,
        }
        if request and request.args.get("_context") and self.setting("template_debug"):
            return "<pre>{}</pre>".format(
                escape(json.dumps(template_context, default=repr, indent=4))
            )

        return await template.render_async(template_context)

    def set_actor_cookie(
        self, response: Response, actor: dict, expire_after: int | None = None
    ):
        data = {"a": actor}
        if expire_after:
            expires_at = int(time.time()) + (24 * 60 * 60)
            data["e"] = baseconv.base62.encode(expires_at)
        response.set_cookie("ds_actor", self.sign(data, "actor"))

    def delete_actor_cookie(self, response: Response):
        response.set_cookie("ds_actor", "", expires=0, max_age=0)

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
        collected.extend((self.config or {}).get(key) or [])
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

    def _config(self):
        return redact_keys(
            self.config, ("secret", "key", "password", "token", "hash", "dsn")
        )

    def _routes(self):
        routes = []

        for routes_to_add in pm.hook.register_routes(datasette=self):
            for regex, view_fn in routes_to_add:
                routes.append((regex, wrap_view(view_fn, self)))

        def add_route(view, regex):
            routes.append((regex, view))

        add_route(IndexView.as_view(self), r"/(\.(?P<format>jsono?))?$")
        add_route(IndexView.as_view(self), r"/-/(\.(?P<format>jsono?))?$")
        add_route(permanent_redirect("/-/"), r"/-$")
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
            JsonDataView.as_view(self, "config.json", lambda: self._config()),
            r"/-/config(\.(?P<format>json))?$",
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
            JsonDataView.as_view(
                self, "actor.json", self._actor, needs_request=True, permission=None
            ),
            r"/-/actor(\.(?P<format>json))?$",
        )
        add_route(
            JsonDataView.as_view(
                self,
                "actions.json",
                self._actions,
                template="debug_actions.html",
                permission="permissions-debug",
            ),
            r"/-/actions(\.(?P<format>json))?$",
        )
        add_route(
            AuthTokenView.as_view(self),
            r"/-/auth-token$",
        )
        add_route(
            CreateTokenView.as_view(self),
            r"/-/create-token$",
        )
        add_route(
            ApiExplorerView.as_view(self),
            r"/-/api$",
        )
        add_route(
            TablesView.as_view(self),
            r"/-/tables(\.(?P<format>json))?$",
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
            AllowedResourcesView.as_view(self),
            r"/-/allowed(\.(?P<format>json))?$",
        )
        add_route(
            PermissionRulesView.as_view(self),
            r"/-/rules(\.(?P<format>json))?$",
        )
        add_route(
            PermissionCheckView.as_view(self),
            r"/-/check(\.(?P<format>json))?$",
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
            wrap_view(PatternPortfolioView, self),
            r"/-/patterns$",
        )
        add_route(
            wrap_view(database_download, self),
            r"/(?P<database>[^\/\.]+)\.db$",
        )
        add_route(
            wrap_view(DatabaseView, self),
            r"/(?P<database>[^\/\.]+)(\.(?P<format>\w+))?$",
        )
        add_route(TableCreateView.as_view(self), r"/(?P<database>[^\/\.]+)/-/create$")
        add_route(
            wrap_view(QueryView, self),
            r"/(?P<database>[^\/\.]+)/-/query(\.(?P<format>\w+))?$",
        )
        add_route(
            wrap_view(table_view, self),
            r"/(?P<database>[^\/\.]+)/(?P<table>[^\/\.]+)(\.(?P<format>\w+))?$",
        )
        add_route(
            RowView.as_view(self),
            r"/(?P<database>[^\/\.]+)/(?P<table>[^/]+?)/(?P<pks>[^/]+?)(\.(?P<format>\w+))?$",
        )
        add_route(
            TableInsertView.as_view(self),
            r"/(?P<database>[^\/\.]+)/(?P<table>[^\/\.]+)/-/insert$",
        )
        add_route(
            TableUpsertView.as_view(self),
            r"/(?P<database>[^\/\.]+)/(?P<table>[^\/\.]+)/-/upsert$",
        )
        add_route(
            TableDropView.as_view(self),
            r"/(?P<database>[^\/\.]+)/(?P<table>[^\/\.]+)/-/drop$",
        )
        add_route(
            RowDeleteView.as_view(self),
            r"/(?P<database>[^\/\.]+)/(?P<table>[^/]+?)/(?P<pks>[^/]+?)/-/delete$",
        )
        add_route(
            RowUpdateView.as_view(self),
            r"/(?P<database>[^\/\.]+)/(?P<table>[^/]+?)/(?P<pks>[^/]+?)/-/update$",
        )
        return [
            # Compile any strings to regular expressions
            ((re.compile(pattern) if isinstance(pattern, str) else pattern), view)
            for pattern, view in routes
        ]

    async def resolve_database(self, request):
        database_route = tilde_decode(request.url_vars["database"])
        try:
            return self.get_database(route=database_route)
        except KeyError:
            raise DatabaseNotFound(database_route)

    async def resolve_table(self, request):
        db = await self.resolve_database(request)
        table_name = tilde_decode(request.url_vars["table"])
        # Table must exist
        is_view = False
        table_exists = await db.table_exists(table_name)
        if not table_exists:
            is_view = await db.view_exists(table_name)
        if not (table_exists or is_view):
            raise TableNotFound(db.name, table_name)
        return ResolvedTable(db, table_name, is_view)

    async def resolve_row(self, request):
        db, table_name, _ = await self.resolve_table(request)
        pk_values = urlsafe_components(request.url_vars["pks"])
        sql, params, pks = await row_sql_params_pks(db, table_name, pk_values)
        results = await db.execute(sql, params, truncate=True)
        row = results.first()
        if row is None:
            raise RowNotFound(db.name, table_name, pk_values)
        return ResolvedRow(db, table_name, sql, params, pks, pk_values, results.first())

    def app(self):
        """Returns an ASGI app function that serves the whole of Datasette"""
        routes = self._routes()

        async def setup_db():
            # First time server starts up, calculate table counts for immutable databases
            for database in self.databases.values():
                if not database.is_mutable:
                    await database.table_counts(limit=60 * 60 * 1000)

        async def custom_csrf_error(scope, send, message_id):
            await asgi_send(
                send,
                content=await self.render_template(
                    "csrf_error.html",
                    {"message_id": message_id, "message_name": Errors(message_id).name},
                ),
                status=403,
                content_type="text/html; charset=utf-8",
            )

        asgi = asgi_csrf.asgi_csrf(
            DatasetteRouter(self, routes),
            signing_secret=self._secret,
            cookie_name="ds_csrftoken",
            skip_if_scope=lambda scope: any(
                pm.hook.skip_csrf(datasette=self, scope=scope)
            ),
            send_csrf_failed=custom_csrf_error,
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
            # Build a list of pages/blah/{name}.html matching expressions
            environment = self.ds.get_jinja_environment(request)
            pattern_templates = [
                filepath
                for filepath in environment.list_templates()
                if "{" in filepath and filepath.startswith("pages/")
            ]
            page_routes = [
                (route_pattern_from_filepath(filepath[len("pages/") :]), filepath)
                for filepath in pattern_templates
            ]
            try:
                template = environment.select_template([template_name])
            except TemplateNotFound:
                template = None
            if template is None:
                # Try for a pages/blah/{name}.html template match
                for regex, wildcard_template in page_routes:
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


def wrap_view(view_fn_or_class, datasette):
    is_function = isinstance(view_fn_or_class, types.FunctionType)
    if is_function:
        return wrap_view_function(view_fn_or_class, datasette)
    else:
        if not isinstance(view_fn_or_class, type):
            raise ValueError("view_fn_or_class must be a function or a class")
        return wrap_view_class(view_fn_or_class, datasette)


def wrap_view_class(view_class, datasette):
    async def async_view_for_class(request, send):
        instance = view_class()
        if inspect.iscoroutinefunction(instance.__call__):
            return await async_call_with_supported_arguments(
                instance.__call__,
                scope=request.scope,
                receive=request.receive,
                send=send,
                request=request,
                datasette=datasette,
            )
        else:
            return call_with_supported_arguments(
                instance.__call__,
                scope=request.scope,
                receive=request.receive,
                send=send,
                request=request,
                datasette=datasette,
            )

    async_view_for_class.view_class = view_class
    return async_view_for_class


def wrap_view_function(view_fn, datasette):
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
    """Internal HTTP client for making requests to a Datasette instance.

    Used for testing and for internal operations that need to make HTTP requests
    to the Datasette app without going through an actual HTTP server.
    """

    def __init__(self, ds):
        self.ds = ds
        self.app = ds.app()

    def actor_cookie(self, actor):
        # Utility method, mainly for tests
        return self.ds.sign({"a": actor}, "actor")

    def _fix(self, path, avoid_path_rewrites=False):
        if not isinstance(path, PrefixedUrlString) and not avoid_path_rewrites:
            path = self.ds.urls.path(path)
        if path.startswith("/"):
            path = f"http://localhost{path}"
        return path

    async def _request(self, method, path, skip_permission_checks=False, **kwargs):
        from datasette.permissions import SkipPermissions

        if skip_permission_checks:
            with SkipPermissions():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.app),
                    cookies=kwargs.pop("cookies", None),
                ) as client:
                    return await getattr(client, method)(self._fix(path), **kwargs)
        else:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                cookies=kwargs.pop("cookies", None),
            ) as client:
                return await getattr(client, method)(self._fix(path), **kwargs)

    async def get(self, path, skip_permission_checks=False, **kwargs):
        return await self._request(
            "get", path, skip_permission_checks=skip_permission_checks, **kwargs
        )

    async def options(self, path, skip_permission_checks=False, **kwargs):
        return await self._request(
            "options", path, skip_permission_checks=skip_permission_checks, **kwargs
        )

    async def head(self, path, skip_permission_checks=False, **kwargs):
        return await self._request(
            "head", path, skip_permission_checks=skip_permission_checks, **kwargs
        )

    async def post(self, path, skip_permission_checks=False, **kwargs):
        return await self._request(
            "post", path, skip_permission_checks=skip_permission_checks, **kwargs
        )

    async def put(self, path, skip_permission_checks=False, **kwargs):
        return await self._request(
            "put", path, skip_permission_checks=skip_permission_checks, **kwargs
        )

    async def patch(self, path, skip_permission_checks=False, **kwargs):
        return await self._request(
            "patch", path, skip_permission_checks=skip_permission_checks, **kwargs
        )

    async def delete(self, path, skip_permission_checks=False, **kwargs):
        return await self._request(
            "delete", path, skip_permission_checks=skip_permission_checks, **kwargs
        )

    async def request(self, method, path, skip_permission_checks=False, **kwargs):
        """Make an HTTP request with the specified method.

        Args:
            method: HTTP method (e.g., "GET", "POST", "PUT")
            path: The path to request
            skip_permission_checks: If True, bypass all permission checks for this request
            **kwargs: Additional arguments to pass to httpx

        Returns:
            httpx.Response: The response from the request
        """
        from datasette.permissions import SkipPermissions

        avoid_path_rewrites = kwargs.pop("avoid_path_rewrites", None)
        if skip_permission_checks:
            with SkipPermissions():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.app),
                    cookies=kwargs.pop("cookies", None),
                ) as client:
                    return await client.request(
                        method, self._fix(path, avoid_path_rewrites), **kwargs
                    )
        else:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                cookies=kwargs.pop("cookies", None),
            ) as client:
                return await client.request(
                    method, self._fix(path, avoid_path_rewrites), **kwargs
                )
