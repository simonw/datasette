import asyncio
from contextlib import contextmanager
import aiofiles
import click
from collections import OrderedDict, namedtuple, Counter
import copy
import base64
import hashlib
import inspect
import json
import markupsafe
import mergedeep
import os
import re
import shlex
import tempfile
import typing
import time
import types
import secrets
import shutil
from typing import Iterable, List, Tuple
import urllib
import yaml
from .shutil_backport import copytree
from .sqlite import sqlite3, supports_table_xinfo

if typing.TYPE_CHECKING:
    from datasette.database import Database

# From https://www.sqlite.org/lang_keywords.html
reserved_words = set(
    (
        "abort action add after all alter analyze and as asc attach autoincrement "
        "before begin between by cascade case cast check collate column commit "
        "conflict constraint create cross current_date current_time "
        "current_timestamp database default deferrable deferred delete desc detach "
        "distinct drop each else end escape except exclusive exists explain fail "
        "for foreign from full glob group having if ignore immediate in index "
        "indexed initially inner insert instead intersect into is isnull join key "
        "left like limit match natural no not notnull null of offset on or order "
        "outer plan pragma primary query raise recursive references regexp reindex "
        "release rename replace restrict right rollback row savepoint select set "
        "table temp temporary then to transaction trigger union unique update using "
        "vacuum values view virtual when where with without"
    ).split()
)

APT_GET_DOCKERFILE_EXTRAS = r"""
RUN apt-get update && \
    apt-get install -y {} && \
    rm -rf /var/lib/apt/lists/*
"""

# Can replace with sqlite-utils when I add that dependency
SPATIALITE_PATHS = (
    "/usr/lib/x86_64-linux-gnu/mod_spatialite.so",
    "/usr/local/lib/mod_spatialite.dylib",
    "/usr/local/lib/mod_spatialite.so",
    "/opt/homebrew/lib/mod_spatialite.dylib",
)
# Used to display /-/versions.json SpatiaLite information
SPATIALITE_FUNCTIONS = (
    "spatialite_version",
    "spatialite_target_cpu",
    "check_strict_sql_quoting",
    "freexl_version",
    "proj_version",
    "geos_version",
    "rttopo_version",
    "libxml2_version",
    "HasIconv",
    "HasMathSQL",
    "HasGeoCallbacks",
    "HasProj",
    "HasProj6",
    "HasGeos",
    "HasGeosAdvanced",
    "HasGeosTrunk",
    "HasGeosReentrant",
    "HasGeosOnlyReentrant",
    "HasMiniZip",
    "HasRtTopo",
    "HasLibXML2",
    "HasEpsg",
    "HasFreeXL",
    "HasGeoPackage",
    "HasGCP",
    "HasTopology",
    "HasKNN",
    "HasRouting",
)
# Length of hash subset used in hashed URLs:
HASH_LENGTH = 7


# Can replace this with Column from sqlite_utils when I add that dependency
Column = namedtuple(
    "Column", ("cid", "name", "type", "notnull", "default_value", "is_pk", "hidden")
)

functions_marked_as_documented = []


def documented(fn):
    functions_marked_as_documented.append(fn)
    return fn


@documented
async def await_me_maybe(value: typing.Any) -> typing.Any:
    "If value is callable, call it. If awaitable, await it. Otherwise return it."
    if callable(value):
        value = value()
    if asyncio.iscoroutine(value):
        value = await value
    return value


def urlsafe_components(token):
    """Splits token on commas and tilde-decodes each component"""
    return [tilde_decode(b) for b in token.split(",")]


def path_from_row_pks(row, pks, use_rowid, quote=True):
    """Generate an optionally tilde-encoded unique identifier
    for a row from its primary keys."""
    if use_rowid:
        bits = [row["rowid"]]
    else:
        bits = [
            row[pk]["value"] if isinstance(row[pk], dict) else row[pk] for pk in pks
        ]
    if quote:
        bits = [tilde_encode(str(bit)) for bit in bits]
    else:
        bits = [str(bit) for bit in bits]

    return ",".join(bits)


def compound_keys_after_sql(pks, start_index=0):
    # Implementation of keyset pagination
    # See https://github.com/simonw/datasette/issues/190
    # For pk1/pk2/pk3 returns:
    #
    # ([pk1] > :p0)
    #   or
    # ([pk1] = :p0 and [pk2] > :p1)
    #   or
    # ([pk1] = :p0 and [pk2] = :p1 and [pk3] > :p2)
    or_clauses = []
    pks_left = pks[:]
    while pks_left:
        and_clauses = []
        last = pks_left[-1]
        rest = pks_left[:-1]
        and_clauses = [
            f"{escape_sqlite(pk)} = :p{i + start_index}" for i, pk in enumerate(rest)
        ]
        and_clauses.append(f"{escape_sqlite(last)} > :p{len(rest) + start_index}")
        or_clauses.append(f"({' and '.join(and_clauses)})")
        pks_left.pop()
    or_clauses.reverse()
    return "({})".format("\n  or\n".join(or_clauses))


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, sqlite3.Row):
            return tuple(obj)
        if isinstance(obj, sqlite3.Cursor):
            return list(obj)
        if isinstance(obj, bytes):
            # Does it encode to utf8?
            try:
                return obj.decode("utf8")
            except UnicodeDecodeError:
                return {
                    "$base64": True,
                    "encoded": base64.b64encode(obj).decode("latin1"),
                }
        return json.JSONEncoder.default(self, obj)


@contextmanager
def sqlite_timelimit(conn, ms):
    deadline = time.perf_counter() + (ms / 1000)
    # n is the number of SQLite virtual machine instructions that will be
    # executed between each check. It takes about 0.08ms to execute 1000.
    # https://github.com/simonw/datasette/issues/1679
    n = 1000
    if ms <= 20:
        # This mainly happens while executing our test suite
        n = 1

    def handler():
        if time.perf_counter() >= deadline:
            # Returning 1 terminates the query with an error
            return 1

    conn.set_progress_handler(handler, n)
    try:
        yield
    finally:
        conn.set_progress_handler(None, n)


class InvalidSql(Exception):
    pass


# Allow SQL to start with a /* */ or -- comment
comment_re = (
    # Start of string, then any amount of whitespace
    r"^\s*("
    +
    # Comment that starts with -- and ends at a newline
    r"(?:\-\-.*?\n\s*)"
    +
    # Comment that starts with /* and ends with */ - but does not have */ in it
    r"|(?:\/\*((?!\*\/)[\s\S])*\*\/)"
    +
    # Whitespace
    r"\s*)*\s*"
)

allowed_sql_res = [
    re.compile(comment_re + r"select\b"),
    re.compile(comment_re + r"explain\s+select\b"),
    re.compile(comment_re + r"explain\s+query\s+plan\s+select\b"),
    re.compile(comment_re + r"with\b"),
    re.compile(comment_re + r"explain\s+with\b"),
    re.compile(comment_re + r"explain\s+query\s+plan\s+with\b"),
]

allowed_pragmas = (
    "database_list",
    "foreign_key_list",
    "function_list",
    "index_info",
    "index_list",
    "index_xinfo",
    "page_count",
    "max_page_count",
    "page_size",
    "schema_version",
    "table_info",
    "table_xinfo",
    "table_list",
)
disallawed_sql_res = [
    (
        re.compile(f"pragma(?!_({'|'.join(allowed_pragmas)}))"),
        "Statement contained a disallowed PRAGMA. Allowed pragma functions are {}".format(
            ", ".join("pragma_{}()".format(pragma) for pragma in allowed_pragmas)
        ),
    )
]


def validate_sql_select(sql):
    sql = "\n".join(
        line for line in sql.split("\n") if not line.strip().startswith("--")
    )
    sql = sql.strip().lower()
    if not any(r.match(sql) for r in allowed_sql_res):
        raise InvalidSql("Statement must be a SELECT")
    for r, msg in disallawed_sql_res:
        if r.search(sql):
            raise InvalidSql(msg)


def append_querystring(url, querystring):
    op = "&" if ("?" in url) else "?"
    return f"{url}{op}{querystring}"


def path_with_added_args(request, args, path=None):
    path = path or request.path
    if isinstance(args, dict):
        args = args.items()
    args_to_remove = {k for k, v in args if v is None}
    current = []
    for key, value in urllib.parse.parse_qsl(request.query_string):
        if key not in args_to_remove:
            current.append((key, value))
    current.extend([(key, value) for key, value in args if value is not None])
    query_string = urllib.parse.urlencode(current)
    if query_string:
        query_string = f"?{query_string}"
    return path + query_string


def path_with_removed_args(request, args, path=None):
    query_string = request.query_string
    if path is None:
        path = request.path
    else:
        if "?" in path:
            bits = path.split("?", 1)
            path, query_string = bits
    # args can be a dict or a set
    current = []
    if isinstance(args, set):

        def should_remove(key, value):
            return key in args

    elif isinstance(args, dict):
        # Must match key AND value
        def should_remove(key, value):
            return args.get(key) == value

    for key, value in urllib.parse.parse_qsl(query_string):
        if not should_remove(key, value):
            current.append((key, value))
    query_string = urllib.parse.urlencode(current)
    if query_string:
        query_string = f"?{query_string}"
    return path + query_string


def path_with_replaced_args(request, args, path=None):
    path = path or request.path
    if isinstance(args, dict):
        args = args.items()
    keys_to_replace = {p[0] for p in args}
    current = []
    for key, value in urllib.parse.parse_qsl(request.query_string):
        if key not in keys_to_replace:
            current.append((key, value))
    current.extend([p for p in args if p[1] is not None])
    query_string = urllib.parse.urlencode(current)
    if query_string:
        query_string = f"?{query_string}"
    return path + query_string


_css_re = re.compile(r"""['"\n\\]""")
_boring_keyword_re = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def escape_css_string(s):
    return _css_re.sub(
        lambda m: "\\" + (f"{ord(m.group()):X}".zfill(6)),
        s.replace("\r\n", "\n"),
    )


def escape_sqlite(s):
    if _boring_keyword_re.match(s) and (s.lower() not in reserved_words):
        return s
    else:
        return f"[{s}]"


def make_dockerfile(
    files,
    metadata_file,
    extra_options,
    branch,
    template_dir,
    plugins_dir,
    static,
    install,
    spatialite,
    version_note,
    secret,
    environment_variables=None,
    port=8001,
    apt_get_extras=None,
):
    cmd = ["datasette", "serve", "--host", "0.0.0.0"]
    environment_variables = environment_variables or {}
    environment_variables["DATASETTE_SECRET"] = secret
    apt_get_extras = apt_get_extras or []
    for filename in files:
        cmd.extend(["-i", filename])
    cmd.extend(["--cors", "--inspect-file", "inspect-data.json"])
    if metadata_file:
        cmd.extend(["--metadata", f"{metadata_file}"])
    if template_dir:
        cmd.extend(["--template-dir", "templates/"])
    if plugins_dir:
        cmd.extend(["--plugins-dir", "plugins/"])
    if version_note:
        cmd.extend(["--version-note", f"{version_note}"])
    if static:
        for mount_point, _ in static:
            cmd.extend(["--static", f"{mount_point}:{mount_point}"])
    if extra_options:
        for opt in extra_options.split():
            cmd.append(f"{opt}")
    cmd = [shlex.quote(part) for part in cmd]
    # port attribute is a (fixed) env variable and should not be quoted
    cmd.extend(["--port", "$PORT"])
    cmd = " ".join(cmd)
    if branch:
        install = [f"https://github.com/simonw/datasette/archive/{branch}.zip"] + list(
            install
        )
    else:
        install = ["datasette"] + list(install)

    apt_get_extras_ = []
    apt_get_extras_.extend(apt_get_extras)
    apt_get_extras = apt_get_extras_
    if spatialite:
        apt_get_extras.extend(["python3-dev", "gcc", "libsqlite3-mod-spatialite"])
        environment_variables["SQLITE_EXTENSIONS"] = (
            "/usr/lib/x86_64-linux-gnu/mod_spatialite.so"
        )
    return """
FROM python:3.11.0-slim-bullseye
COPY . /app
WORKDIR /app
{apt_get_extras}
{environment_variables}
RUN pip install -U {install_from}
RUN datasette inspect {files} --inspect-file inspect-data.json
ENV PORT {port}
EXPOSE {port}
CMD {cmd}""".format(
        apt_get_extras=(
            APT_GET_DOCKERFILE_EXTRAS.format(" ".join(apt_get_extras))
            if apt_get_extras
            else ""
        ),
        environment_variables="\n".join(
            [
                "ENV {} '{}'".format(key, value)
                for key, value in environment_variables.items()
            ]
        ),
        install_from=" ".join(install),
        files=" ".join(files),
        port=port,
        cmd=cmd,
    ).strip()


@contextmanager
def temporary_docker_directory(
    files,
    name,
    metadata,
    extra_options,
    branch,
    template_dir,
    plugins_dir,
    static,
    install,
    spatialite,
    version_note,
    secret,
    extra_metadata=None,
    environment_variables=None,
    port=8001,
    apt_get_extras=None,
):
    extra_metadata = extra_metadata or {}
    tmp = tempfile.TemporaryDirectory()
    # We create a datasette folder in there to get a nicer now deploy name
    datasette_dir = os.path.join(tmp.name, name)
    os.mkdir(datasette_dir)
    saved_cwd = os.getcwd()
    file_paths = [os.path.join(saved_cwd, file_path) for file_path in files]
    file_names = [os.path.split(f)[-1] for f in files]
    if metadata:
        metadata_content = parse_metadata(metadata.read())
    else:
        metadata_content = {}
    # Merge in the non-null values in extra_metadata
    mergedeep.merge(
        metadata_content,
        {key: value for key, value in extra_metadata.items() if value is not None},
    )
    try:
        dockerfile = make_dockerfile(
            file_names,
            metadata_content and "metadata.json",
            extra_options,
            branch,
            template_dir,
            plugins_dir,
            static,
            install,
            spatialite,
            version_note,
            secret,
            environment_variables,
            port=port,
            apt_get_extras=apt_get_extras,
        )
        os.chdir(datasette_dir)
        if metadata_content:
            with open("metadata.json", "w") as fp:
                fp.write(json.dumps(metadata_content, indent=2))
        with open("Dockerfile", "w") as fp:
            fp.write(dockerfile)
        for path, filename in zip(file_paths, file_names):
            link_or_copy(path, os.path.join(datasette_dir, filename))
        if template_dir:
            link_or_copy_directory(
                os.path.join(saved_cwd, template_dir),
                os.path.join(datasette_dir, "templates"),
            )
        if plugins_dir:
            link_or_copy_directory(
                os.path.join(saved_cwd, plugins_dir),
                os.path.join(datasette_dir, "plugins"),
            )
        for mount_point, path in static:
            link_or_copy_directory(
                os.path.join(saved_cwd, path), os.path.join(datasette_dir, mount_point)
            )
        yield datasette_dir
    finally:
        tmp.cleanup()
        os.chdir(saved_cwd)


def detect_primary_keys(conn, table):
    """Figure out primary keys for a table."""
    columns = table_column_details(conn, table)
    pks = [column for column in columns if column.is_pk]
    pks.sort(key=lambda column: column.is_pk)
    return [column.name for column in pks]


def get_outbound_foreign_keys(conn, table):
    infos = conn.execute(f"PRAGMA foreign_key_list([{table}])").fetchall()
    fks = []
    for info in infos:
        if info is not None:
            id, seq, table_name, from_, to_, on_update, on_delete, match = info
            fks.append(
                {
                    "column": from_,
                    "other_table": table_name,
                    "other_column": to_,
                    "id": id,
                    "seq": seq,
                }
            )
    # Filter out compound foreign keys by removing any where "id" is not unique
    id_counts = Counter(fk["id"] for fk in fks)
    return [
        {
            "column": fk["column"],
            "other_table": fk["other_table"],
            "other_column": fk["other_column"],
        }
        for fk in fks
        if id_counts[fk["id"]] == 1
    ]


def get_all_foreign_keys(conn):
    tables = [
        r[0] for r in conn.execute('select name from sqlite_master where type="table"')
    ]
    table_to_foreign_keys = {}
    for table in tables:
        table_to_foreign_keys[table] = {"incoming": [], "outgoing": []}
    for table in tables:
        fks = get_outbound_foreign_keys(conn, table)
        for fk in fks:
            table_name = fk["other_table"]
            from_ = fk["column"]
            to_ = fk["other_column"]
            if table_name not in table_to_foreign_keys:
                # Weird edge case where something refers to a table that does
                # not actually exist
                continue
            table_to_foreign_keys[table_name]["incoming"].append(
                {"other_table": table, "column": to_, "other_column": from_}
            )
            table_to_foreign_keys[table]["outgoing"].append(
                {"other_table": table_name, "column": from_, "other_column": to_}
            )

    return table_to_foreign_keys


def detect_spatialite(conn):
    rows = conn.execute(
        'select 1 from sqlite_master where tbl_name = "geometry_columns"'
    ).fetchall()
    return len(rows) > 0


def detect_fts(conn, table):
    """Detect if table has a corresponding FTS virtual table and return it"""
    rows = conn.execute(detect_fts_sql(table)).fetchall()
    if len(rows) == 0:
        return None
    else:
        return rows[0][0]


def detect_fts_sql(table):
    return r"""
        select name from sqlite_master
            where rootpage = 0
            and (
                sql like '%VIRTUAL TABLE%USING FTS%content="{table}"%'
                or sql like '%VIRTUAL TABLE%USING FTS%content=[{table}]%'
                or (
                    tbl_name = "{table}"
                    and sql like '%VIRTUAL TABLE%USING FTS%'
                )
            )
    """.format(
        table=table.replace("'", "''")
    )


def detect_json1(conn=None):
    if conn is None:
        conn = sqlite3.connect(":memory:")
    try:
        conn.execute("SELECT json('{}')")
        return True
    except Exception:
        return False


def table_columns(conn, table):
    return [column.name for column in table_column_details(conn, table)]


def table_column_details(conn, table):
    if supports_table_xinfo():
        # table_xinfo was added in 3.26.0
        return [
            Column(*r)
            for r in conn.execute(
                f"PRAGMA table_xinfo({escape_sqlite(table)});"
            ).fetchall()
        ]
    else:
        # Treat hidden as 0 for all columns
        return [
            Column(*(list(r) + [0]))
            for r in conn.execute(
                f"PRAGMA table_info({escape_sqlite(table)});"
            ).fetchall()
        ]


filter_column_re = re.compile(r"^_filter_column_\d+$")


def filters_should_redirect(special_args):
    redirect_params = []
    # Handle _filter_column=foo&_filter_op=exact&_filter_value=...
    filter_column = special_args.get("_filter_column")
    filter_op = special_args.get("_filter_op") or ""
    filter_value = special_args.get("_filter_value") or ""
    if "__" in filter_op:
        filter_op, filter_value = filter_op.split("__", 1)
    if filter_column:
        redirect_params.append((f"{filter_column}__{filter_op}", filter_value))
    for key in ("_filter_column", "_filter_op", "_filter_value"):
        if key in special_args:
            redirect_params.append((key, None))
    # Now handle _filter_column_1=name&_filter_op_1=contains&_filter_value_1=hello
    column_keys = [k for k in special_args if filter_column_re.match(k)]
    for column_key in column_keys:
        number = column_key.split("_")[-1]
        column = special_args[column_key]
        op = special_args.get(f"_filter_op_{number}") or "exact"
        value = special_args.get(f"_filter_value_{number}") or ""
        if "__" in op:
            op, value = op.split("__", 1)
        if column:
            redirect_params.append((f"{column}__{op}", value))
        redirect_params.extend(
            [
                (f"_filter_column_{number}", None),
                (f"_filter_op_{number}", None),
                (f"_filter_value_{number}", None),
            ]
        )
    return redirect_params


whitespace_re = re.compile(r"\s")


def is_url(value):
    """Must start with http:// or https:// and contain JUST a URL"""
    if not isinstance(value, str):
        return False
    if not value.startswith("http://") and not value.startswith("https://"):
        return False
    # Any whitespace at all is invalid
    if whitespace_re.search(value):
        return False
    return True


css_class_re = re.compile(r"^[a-zA-Z]+[_a-zA-Z0-9-]*$")
css_invalid_chars_re = re.compile(r"[^a-zA-Z0-9_\-]")


def to_css_class(s):
    """
    Given a string (e.g. a table name) returns a valid unique CSS class.
    For simple cases, just returns the string again. If the string is not a
    valid CSS class (we disallow - and _ prefixes even though they are valid
    as they may be confused with browser prefixes) we strip invalid characters
    and add a 6 char md5 sum suffix, to make sure two tables with identical
    names after stripping characters don't end up with the same CSS class.
    """
    if css_class_re.match(s):
        return s
    md5_suffix = md5_not_usedforsecurity(s)[:6]
    # Strip leading _, -
    s = s.lstrip("_").lstrip("-")
    # Replace any whitespace with hyphens
    s = "-".join(s.split())
    # Remove any remaining invalid characters
    s = css_invalid_chars_re.sub("", s)
    # Attach the md5 suffix
    bits = [b for b in (s, md5_suffix) if b]
    return "-".join(bits)


def link_or_copy(src, dst):
    # Intended for use in populating a temp directory. We link if possible,
    # but fall back to copying if the temp directory is on a different device
    # https://github.com/simonw/datasette/issues/141
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def link_or_copy_directory(src, dst):
    try:
        copytree(src, dst, copy_function=os.link, dirs_exist_ok=True)
    except OSError:
        copytree(src, dst, dirs_exist_ok=True)


def module_from_path(path, name):
    # Adapted from http://sayspy.blogspot.com/2011/07/how-to-import-module-from-just-file.html
    mod = types.ModuleType(name)
    mod.__file__ = path
    with open(path, "r") as file:
        code = compile(file.read(), path, "exec", dont_inherit=True)
    exec(code, mod.__dict__)
    return mod


def path_with_format(
    *, request=None, path=None, format=None, extra_qs=None, replace_format=None
):
    qs = extra_qs or {}
    path = request.path if request else path
    if replace_format and path.endswith(f".{replace_format}"):
        path = path[: -(1 + len(replace_format))]
    if "." in path:
        qs["_format"] = format
    else:
        path = f"{path}.{format}"
    if qs:
        extra = urllib.parse.urlencode(sorted(qs.items()))
        if request and request.query_string:
            path = f"{path}?{request.query_string}&{extra}"
        else:
            path = f"{path}?{extra}"
    elif request and request.query_string:
        path = f"{path}?{request.query_string}"
    return path


class CustomRow(OrderedDict):
    # Loose imitation of sqlite3.Row which offers
    # both index-based AND key-based lookups
    def __init__(self, columns, values=None):
        self.columns = columns
        if values:
            self.update(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self.columns[key])
        else:
            return super().__getitem__(key)

    def __iter__(self):
        for column in self.columns:
            yield self[column]


def value_as_boolean(value):
    if value.lower() not in ("on", "off", "true", "false", "1", "0"):
        raise ValueAsBooleanError
    return value.lower() in ("on", "true", "1")


class ValueAsBooleanError(ValueError):
    pass


class WriteLimitExceeded(Exception):
    pass


class LimitedWriter:
    def __init__(self, writer, limit_mb):
        self.writer = writer
        self.limit_bytes = limit_mb * 1024 * 1024
        self.bytes_count = 0

    async def write(self, bytes):
        self.bytes_count += len(bytes)
        if self.limit_bytes and (self.bytes_count > self.limit_bytes):
            raise WriteLimitExceeded(f"CSV contains more than {self.limit_bytes} bytes")
        await self.writer.write(bytes)


class EscapeHtmlWriter:
    def __init__(self, writer):
        self.writer = writer

    async def write(self, content):
        await self.writer.write(markupsafe.escape(content))


_infinities = {float("inf"), float("-inf")}


def remove_infinites(row):
    to_check = row
    if isinstance(row, dict):
        to_check = row.values()
    if not any((c in _infinities) if isinstance(c, float) else 0 for c in to_check):
        return row
    if isinstance(row, dict):
        return {
            k: (None if (isinstance(v, float) and v in _infinities) else v)
            for k, v in row.items()
        }
    else:
        return [None if (isinstance(c, float) and c in _infinities) else c for c in row]


class StaticMount(click.ParamType):
    name = "mount:directory"

    def convert(self, value, param, ctx):
        if ":" not in value:
            self.fail(
                f'"{value}" should be of format mountpoint:directory',
                param,
                ctx,
            )
        path, dirpath = value.split(":", 1)
        dirpath = os.path.abspath(dirpath)
        if not os.path.exists(dirpath) or not os.path.isdir(dirpath):
            self.fail(f"{value} is not a valid directory path", param, ctx)
        return path, dirpath


# The --load-extension parameter can optionally include a specific entrypoint.
# This is done by appending ":entrypoint_name" after supplying the path to the extension
class LoadExtension(click.ParamType):
    name = "path:entrypoint?"

    def convert(self, value, param, ctx):
        if ":" not in value:
            return value
        path, entrypoint = value.split(":", 1)
        return path, entrypoint


def format_bytes(bytes):
    current = float(bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if current < 1024:
            break
        current = current / 1024
    if unit == "bytes":
        return f"{int(current)} {unit}"
    else:
        return f"{current:.1f} {unit}"


_escape_fts_re = re.compile(r'\s+|(".*?")')


def escape_fts(query):
    # If query has unbalanced ", add one at end
    if query.count('"') % 2:
        query += '"'
    bits = _escape_fts_re.split(query)
    bits = [b for b in bits if b and b != '""']
    return " ".join(
        '"{}"'.format(bit) if not bit.startswith('"') else bit for bit in bits
    )


class MultiParams:
    def __init__(self, data):
        # data is a dictionary of key => [list, of, values] or a list of [["key", "value"]] pairs
        if isinstance(data, dict):
            for key in data:
                assert isinstance(
                    data[key], (list, tuple)
                ), "dictionary data should be a dictionary of key => [list]"
            self._data = data
        elif isinstance(data, list) or isinstance(data, tuple):
            new_data = {}
            for item in data:
                assert (
                    isinstance(item, (list, tuple)) and len(item) == 2
                ), "list data should be a list of [key, value] pairs"
                key, value = item
                new_data.setdefault(key, []).append(value)
            self._data = new_data

    def __repr__(self):
        return f"<MultiParams: {self._data}>"

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        return self._data[key][0]

    def keys(self):
        return self._data.keys()

    def __iter__(self):
        yield from self._data.keys()

    def __len__(self):
        return len(self._data)

    def get(self, name, default=None):
        """Return first value in the list, if available"""
        try:
            return self._data.get(name)[0]
        except (KeyError, TypeError):
            return default

    def getlist(self, name):
        """Return full list"""
        return self._data.get(name) or []


class ConnectionProblem(Exception):
    pass


class SpatialiteConnectionProblem(ConnectionProblem):
    pass


def check_connection(conn):
    tables = [
        r[0]
        for r in conn.execute(
            "select name from sqlite_master where type='table'"
        ).fetchall()
    ]
    for table in tables:
        try:
            conn.execute(
                f"PRAGMA table_info({escape_sqlite(table)});",
            )
        except sqlite3.OperationalError as e:
            if e.args[0] == "no such module: VirtualSpatialIndex":
                raise SpatialiteConnectionProblem(e)
            else:
                raise ConnectionProblem(e)


class BadMetadataError(Exception):
    pass


@documented
def parse_metadata(content: str) -> dict:
    "Detects if content is JSON or YAML and parses it appropriately."
    # content can be JSON or YAML
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        try:
            return yaml.safe_load(content)
        except yaml.YAMLError:
            raise BadMetadataError("Metadata is not valid JSON or YAML")


def _gather_arguments(fn, kwargs):
    parameters = inspect.signature(fn).parameters.keys()
    call_with = []
    for parameter in parameters:
        if parameter not in kwargs:
            raise TypeError(
                "{} requires parameters {}, missing: {}".format(
                    fn, tuple(parameters), set(parameters) - set(kwargs.keys())
                )
            )
        call_with.append(kwargs[parameter])
    return call_with


def call_with_supported_arguments(fn, **kwargs):
    call_with = _gather_arguments(fn, kwargs)
    return fn(*call_with)


async def async_call_with_supported_arguments(fn, **kwargs):
    call_with = _gather_arguments(fn, kwargs)
    return await fn(*call_with)


def actor_matches_allow(actor, allow):
    if allow is True:
        return True
    if allow is False:
        return False
    if actor is None and allow and allow.get("unauthenticated") is True:
        return True
    if allow is None:
        return True
    actor = actor or {}
    for key, values in allow.items():
        if values == "*" and key in actor:
            return True
        if not isinstance(values, list):
            values = [values]
        actor_values = actor.get(key)
        if actor_values is None:
            continue
        if not isinstance(actor_values, list):
            actor_values = [actor_values]
        actor_values = set(actor_values)
        if actor_values.intersection(values):
            return True
    return False


def resolve_env_secrets(config, environ):
    """Create copy that recursively replaces {"$env": "NAME"} with values from environ"""
    if isinstance(config, dict):
        if list(config.keys()) == ["$env"]:
            return environ.get(list(config.values())[0])
        elif list(config.keys()) == ["$file"]:
            with open(list(config.values())[0]) as fp:
                return fp.read()
        else:
            return {
                key: resolve_env_secrets(value, environ)
                for key, value in config.items()
            }
    elif isinstance(config, list):
        return [resolve_env_secrets(value, environ) for value in config]
    else:
        return config


def display_actor(actor):
    for key in ("display", "name", "username", "login", "id"):
        if actor.get(key):
            return actor[key]
    return str(actor)


class SpatialiteNotFound(Exception):
    pass


# Can replace with sqlite-utils when I add that dependency
def find_spatialite():
    for path in SPATIALITE_PATHS:
        if os.path.exists(path):
            return path
    raise SpatialiteNotFound


async def initial_path_for_datasette(datasette):
    """Return suggested path for opening this Datasette, based on number of DBs and tables"""
    databases = dict([p for p in datasette.databases.items() if p[0] != "_internal"])
    if len(databases) == 1:
        db_name = next(iter(databases.keys()))
        path = datasette.urls.database(db_name)
        # Does this DB only have one table?
        db = next(iter(databases.values()))
        tables = await db.table_names()
        if len(tables) == 1:
            path = datasette.urls.table(db_name, tables[0])
    else:
        path = datasette.urls.instance()
    return path


class PrefixedUrlString(str):
    def __add__(self, other):
        return type(self)(super().__add__(other))

    def __str__(self):
        return super().__str__()

    def __getattribute__(self, name):
        if not name.startswith("__") and name in dir(str):

            def method(self, *args, **kwargs):
                value = getattr(super(), name)(*args, **kwargs)
                if isinstance(value, str):
                    return type(self)(value)
                elif isinstance(value, list):
                    return [type(self)(i) for i in value]
                elif isinstance(value, tuple):
                    return tuple(type(self)(i) for i in value)
                else:
                    return value

            return method.__get__(self)
        else:
            return super().__getattribute__(name)


class StartupError(Exception):
    pass


_single_line_comment_re = re.compile(r"--.*")
_multi_line_comment_re = re.compile(r"/\*.*?\*/", re.DOTALL)
_single_quote_re = re.compile(r"'(?:''|[^'])*'")
_double_quote_re = re.compile(r'"(?:\"\"|[^"])*"')
_named_param_re = re.compile(r":(\w+)")


@documented
def named_parameters(sql: str) -> List[str]:
    """
    Given a SQL statement, return a list of named parameters that are used in the statement

    e.g. for ``select * from foo where id=:id`` this would return ``["id"]``
    """
    sql = _single_line_comment_re.sub("", sql)
    sql = _multi_line_comment_re.sub("", sql)
    sql = _single_quote_re.sub("", sql)
    sql = _double_quote_re.sub("", sql)
    # Extract parameters from what is left
    return _named_param_re.findall(sql)


async def derive_named_parameters(db: "Database", sql: str) -> List[str]:
    """
    This undocumented but stable method exists for backwards compatibility
    with plugins that were using it before it switched to named_parameters()
    """
    return named_parameters(sql)


def add_cors_headers(headers):
    headers["Access-Control-Allow-Origin"] = "*"
    headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    headers["Access-Control-Expose-Headers"] = "Link"
    headers["Access-Control-Allow-Methods"] = "GET, POST, HEAD, OPTIONS"
    headers["Access-Control-Max-Age"] = "3600"


_TILDE_ENCODING_SAFE = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    b"abcdefghijklmnopqrstuvwxyz"
    b"0123456789_-"
    # This is the same as Python percent-encoding but I removed
    # '.' and '~'
)

_space = ord(" ")


class TildeEncoder(dict):
    # Keeps a cache internally, via __missing__
    def __missing__(self, b):
        # Handle a cache miss, store encoded string in cache and return.
        if b in _TILDE_ENCODING_SAFE:
            res = chr(b)
        elif b == _space:
            res = "+"
        else:
            res = "~{:02X}".format(b)
        self[b] = res
        return res


_tilde_encoder = TildeEncoder().__getitem__


@documented
def tilde_encode(s: str) -> str:
    "Returns tilde-encoded string - for example ``/foo/bar`` -> ``~2Ffoo~2Fbar``"
    return "".join(_tilde_encoder(char) for char in s.encode("utf-8"))


@documented
def tilde_decode(s: str) -> str:
    "Decodes a tilde-encoded string, so ``~2Ffoo~2Fbar`` -> ``/foo/bar``"
    # Avoid accidentally decoding a %2f style sequence
    temp = secrets.token_hex(16)
    s = s.replace("%", temp)
    decoded = urllib.parse.unquote_plus(s.replace("~", "%"))
    return decoded.replace(temp, "%")


def resolve_routes(routes, path):
    for regex, view in routes:
        match = regex.match(path)
        if match is not None:
            return match, view
    return None, None


def truncate_url(url, length):
    if (not length) or (len(url) <= length):
        return url
    bits = url.rsplit(".", 1)
    if len(bits) == 2 and 1 <= len(bits[1]) <= 4 and "/" not in bits[1]:
        rest, ext = bits
        return rest[: length - 1 - len(ext)] + "…." + ext
    return url[: length - 1] + "…"


async def row_sql_params_pks(db, table, pk_values):
    pks = await db.primary_keys(table)
    use_rowid = not pks
    select = "*"
    if use_rowid:
        select = "rowid, *"
        pks = ["rowid"]
    wheres = [f'"{pk}"=:p{i}' for i, pk in enumerate(pks)]
    sql = f"select {select} from {escape_sqlite(table)} where {' AND '.join(wheres)}"
    params = {}
    for i, pk_value in enumerate(pk_values):
        params[f"p{i}"] = pk_value
    return sql, params, pks


def _handle_pair(key: str, value: str) -> dict:
    """
    Turn a key-value pair into a nested dictionary.
    foo, bar => {'foo': 'bar'}
    foo.bar, baz => {'foo': {'bar': 'baz'}}
    foo.bar, [1, 2, 3] => {'foo': {'bar': [1, 2, 3]}}
    foo.bar, "baz" => {'foo': {'bar': 'baz'}}
    foo.bar, '{"baz": "qux"}' => {'foo': {'bar': "{'baz': 'qux'}"}}
    """
    try:
        value = json.loads(value)
    except json.JSONDecodeError:
        # If it doesn't parse as JSON, treat it as a string
        pass

    keys = key.split(".")
    result = current_dict = {}

    for k in keys[:-1]:
        current_dict[k] = {}
        current_dict = current_dict[k]

    current_dict[keys[-1]] = value
    return result


def _combine(base: dict, update: dict) -> dict:
    """
    Recursively merge two dictionaries.
    """
    for key, value in update.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            base[key] = _combine(base[key], value)
        else:
            base[key] = value
    return base


def pairs_to_nested_config(pairs: typing.List[typing.Tuple[str, typing.Any]]) -> dict:
    """
    Parse a list of key-value pairs into a nested dictionary.
    """
    result = {}
    for key, value in pairs:
        parsed_pair = _handle_pair(key, value)
        result = _combine(result, parsed_pair)
    return result


def make_slot_function(name, datasette, request, **kwargs):
    from datasette.plugins import pm

    method = getattr(pm.hook, name, None)
    assert method is not None, "No hook found for {}".format(name)

    async def inner():
        html_bits = []
        for hook in method(datasette=datasette, request=request, **kwargs):
            html = await await_me_maybe(hook)
            if html is not None:
                html_bits.append(html)
        return markupsafe.Markup("".join(html_bits))

    return inner


def prune_empty_dicts(d: dict):
    """
    Recursively prune all empty dictionaries from a given dictionary.
    """
    for key, value in list(d.items()):
        if isinstance(value, dict):
            prune_empty_dicts(value)
            if value == {}:
                d.pop(key, None)


def move_plugins_and_allow(source: dict, destination: dict) -> Tuple[dict, dict]:
    """
    Move 'plugins' and 'allow' keys from source to destination dictionary. Creates
    hierarchy in destination if needed. After moving, recursively remove any keys
    in the source that are left empty.
    """
    source = copy.deepcopy(source)
    destination = copy.deepcopy(destination)

    def recursive_move(src, dest, path=None):
        if path is None:
            path = []
        for key, value in list(src.items()):
            new_path = path + [key]
            if key in ("plugins", "allow"):
                # Navigate and create the hierarchy in destination if needed
                d = dest
                for step in path:
                    d = d.setdefault(step, {})
                # Move the plugins
                d[key] = value
                # Remove the plugins from source
                src.pop(key, None)
            elif isinstance(value, dict):
                recursive_move(value, dest, new_path)
                # After moving, check if the current dictionary is empty and remove it if so
                if not value:
                    src.pop(key, None)

    recursive_move(source, destination)
    prune_empty_dicts(source)
    return source, destination


_table_config_keys = (
    "hidden",
    "sort",
    "sort_desc",
    "size",
    "sortable_columns",
    "label_column",
    "facets",
    "fts_table",
    "fts_pk",
    "searchmode",
)


def move_table_config(metadata: dict, config: dict):
    """
    Move all known table configuration keys from metadata to config.
    """
    if "databases" not in metadata:
        return metadata, config
    metadata = copy.deepcopy(metadata)
    config = copy.deepcopy(config)
    for database_name, database in metadata["databases"].items():
        if "tables" not in database:
            continue
        for table_name, table in database["tables"].items():
            for key in _table_config_keys:
                if key in table:
                    config.setdefault("databases", {}).setdefault(
                        database_name, {}
                    ).setdefault("tables", {}).setdefault(table_name, {})[
                        key
                    ] = table.pop(
                        key
                    )
    prune_empty_dicts(metadata)
    return metadata, config


def redact_keys(original: dict, key_patterns: Iterable) -> dict:
    """
    Recursively redact sensitive keys in a dictionary based on given patterns

    :param original: The original dictionary
    :param key_patterns: A list of substring patterns to redact
    :return: A copy of the original dictionary with sensitive values redacted
    """

    def redact(data):
        if isinstance(data, dict):
            return {
                k: (
                    redact(v)
                    if not any(pattern in k for pattern in key_patterns)
                    else "***"
                )
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [redact(item) for item in data]
        else:
            return data

    return redact(original)


def md5_not_usedforsecurity(s):
    try:
        return hashlib.md5(s.encode("utf8"), usedforsecurity=False).hexdigest()
    except TypeError:
        # For Python 3.8 which does not support usedforsecurity=False
        return hashlib.md5(s.encode("utf8")).hexdigest()


_etag_cache = {}


async def calculate_etag(filepath, chunk_size=4096):
    if filepath in _etag_cache:
        return _etag_cache[filepath]

    hasher = hashlib.md5()
    async with aiofiles.open(filepath, "rb") as f:
        while True:
            chunk = await f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)

    etag = f'"{hasher.hexdigest()}"'
    _etag_cache[filepath] = etag

    return etag


def deep_dict_update(dict1, dict2):
    for key, value in dict2.items():
        if isinstance(value, dict):
            dict1[key] = deep_dict_update(dict1.get(key, type(value)()), value)
        else:
            dict1[key] = value
    return dict1
