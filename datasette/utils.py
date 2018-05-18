from contextlib import contextmanager
import base64
import hashlib
import imp
import json
import os
import pkg_resources
import re
import shlex
import sqlite3
import tempfile
import time
import shutil
import urllib
import numbers


# From https://www.sqlite.org/lang_keywords.html
reserved_words = set((
    'abort action add after all alter analyze and as asc attach autoincrement '
    'before begin between by cascade case cast check collate column commit '
    'conflict constraint create cross current_date current_time '
    'current_timestamp database default deferrable deferred delete desc detach '
    'distinct drop each else end escape except exclusive exists explain fail '
    'for foreign from full glob group having if ignore immediate in index '
    'indexed initially inner insert instead intersect into is isnull join key '
    'left like limit match natural no not notnull null of offset on or order '
    'outer plan pragma primary query raise recursive references regexp reindex '
    'release rename replace restrict right rollback row savepoint select set '
    'table temp temporary then to transaction trigger union unique update using '
    'vacuum values view virtual when where with without'
).split())


class InterruptedError(Exception):
    pass


def urlsafe_components(token):
    "Splits token on commas and URL decodes each component"
    return [
        urllib.parse.unquote_plus(b) for b in token.split(',')
    ]


def path_from_row_pks(row, pks, use_rowid, quote=True):
    """ Generate an optionally URL-quoted unique identifier
        for a row from its primary keys."""
    if use_rowid:
        bits = [row['rowid']]
    else:
        bits = [row[pk] for pk in pks]

    if quote:
        bits = [urllib.parse.quote_plus(str(bit)) for bit in bits]
    else:
        bits = [str(bit) for bit in bits]

    return ','.join(bits)


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
        and_clauses = ['{} = :p{}'.format(
            escape_sqlite(pk), (i + start_index)
        ) for i, pk in enumerate(rest)]
        and_clauses.append('{} > :p{}'.format(
            escape_sqlite(last), (len(rest) + start_index)
        ))
        or_clauses.append('({})'.format(' and '.join(and_clauses)))
        pks_left.pop()
    or_clauses.reverse()
    return '({})'.format('\n  or\n'.join(or_clauses))


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, sqlite3.Row):
            return tuple(obj)
        if isinstance(obj, sqlite3.Cursor):
            return list(obj)
        if isinstance(obj, bytes):
            # Does it encode to utf8?
            try:
                return obj.decode('utf8')
            except UnicodeDecodeError:
                return {
                    '$base64': True,
                    'encoded': base64.b64encode(obj).decode('latin1'),
                }
        return json.JSONEncoder.default(self, obj)


@contextmanager
def sqlite_timelimit(conn, ms):
    deadline = time.time() + (ms / 1000)
    # n is the number of SQLite virtual machine instructions that will be
    # executed between each check. It's hard to know what to pick here.
    # After some experimentation, I've decided to go with 1000 by default and
    # 1 for time limits that are less than 50ms
    n = 1000
    if ms < 50:
        n = 1

    def handler():
        if time.time() >= deadline:
            return 1

    conn.set_progress_handler(handler, n)
    yield
    conn.set_progress_handler(None, n)


class InvalidSql(Exception):
    pass


allowed_sql_res = [
    re.compile(r'^select\b'),
    re.compile(r'^explain select\b'),
    re.compile(r'^explain query plan select\b'),
    re.compile(r'^with\b'),
]
disallawed_sql_res = [
    (re.compile('pragma'), 'Statement may not contain PRAGMA'),
]


def validate_sql_select(sql):
    sql = sql.strip().lower()
    if not any(r.match(sql) for r in allowed_sql_res):
        raise InvalidSql('Statement must be a SELECT')
    for r, msg in disallawed_sql_res:
        if r.search(sql):
            raise InvalidSql(msg)


def path_with_added_args(request, args, path=None):
    path = path or request.path
    if isinstance(args, dict):
        args = args.items()
    args_to_remove = {k for k, v in args if v is None}
    current = []
    for key, value in urllib.parse.parse_qsl(request.query_string):
        if key not in args_to_remove:
            current.append((key, value))
    current.extend([
        (key, value)
        for key, value in args
        if value is not None
    ])
    query_string = urllib.parse.urlencode(current)
    if query_string:
        query_string = '?{}'.format(query_string)
    return path + query_string


def path_with_removed_args(request, args, path=None):
    # args can be a dict or a set
    path = path or request.path
    current = []
    if isinstance(args, set):
        def should_remove(key, value):
            return key in args
    elif isinstance(args, dict):
        # Must match key AND value
        def should_remove(key, value):
            return args.get(key) == value
    for key, value in urllib.parse.parse_qsl(request.query_string):
        if not should_remove(key, value):
            current.append((key, value))
    query_string = urllib.parse.urlencode(current)
    if query_string:
        query_string = '?{}'.format(query_string)
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
        query_string = '?{}'.format(query_string)
    return path + query_string


def path_with_ext(request, ext):
    path = request.path
    path += ext
    if request.query_string:
        path += '?' + request.query_string
    return path


_css_re = re.compile(r'''['"\n\\]''')
_boring_keyword_re = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def escape_css_string(s):
    return _css_re.sub(lambda m: '\\{:X}'.format(ord(m.group())), s)


def escape_sqlite(s):
    if _boring_keyword_re.match(s) and (s.lower() not in reserved_words):
        return s
    else:
        return '[{}]'.format(s)


def make_dockerfile(files, metadata_file, extra_options, branch, template_dir, plugins_dir, static, install):
    cmd = ['"datasette"', '"serve"', '"--host"', '"0.0.0.0"']
    cmd.append('"' + '", "'.join(files) + '"')
    cmd.extend(['"--cors"', '"--port"', '"8001"', '"--inspect-file"', '"inspect-data.json"'])
    if metadata_file:
        cmd.extend(['"--metadata"', '"{}"'.format(metadata_file)])
    if template_dir:
        cmd.extend(['"--template-dir"', '"templates/"'])
    if plugins_dir:
        cmd.extend(['"--plugins-dir"', '"plugins/"'])
    if static:
        for mount_point, _ in static:
            cmd.extend(['"--static"', '"{}:{}"'.format(mount_point, mount_point)])
    if extra_options:
        for opt in extra_options.split():
            cmd.append('"{}"'.format(opt))

    if branch:
        install = ['https://github.com/simonw/datasette/archive/{}.zip'.format(
            branch
        )] + list(install)
    else:
        install = ['datasette'] + list(install)

    return '''
FROM python:3
COPY . /app
WORKDIR /app
RUN pip install {install_from}
RUN datasette inspect {files} --inspect-file inspect-data.json
EXPOSE 8001
CMD [{cmd}]'''.format(
        files=' '.join(files),
        cmd=', '.join(cmd),
        install_from=' '.join(install),
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
    extra_metadata=None
):
    extra_metadata = extra_metadata or {}
    tmp = tempfile.TemporaryDirectory()
    # We create a datasette folder in there to get a nicer now deploy name
    datasette_dir = os.path.join(tmp.name, name)
    os.mkdir(datasette_dir)
    saved_cwd = os.getcwd()
    file_paths = [
        os.path.join(saved_cwd, file_path)
        for file_path in files
    ]
    file_names = [os.path.split(f)[-1] for f in files]
    if metadata:
        metadata_content = json.load(metadata)
    else:
        metadata_content = {}
    for key, value in extra_metadata.items():
        if value:
            metadata_content[key] = value
    try:
        dockerfile = make_dockerfile(
            file_names,
            metadata_content and 'metadata.json',
            extra_options,
            branch,
            template_dir,
            plugins_dir,
            static,
            install,
        )
        os.chdir(datasette_dir)
        if metadata_content:
            open('metadata.json', 'w').write(json.dumps(metadata_content, indent=2))
        open('Dockerfile', 'w').write(dockerfile)
        for path, filename in zip(file_paths, file_names):
            link_or_copy(path, os.path.join(datasette_dir, filename))
        if template_dir:
            link_or_copy_directory(
                os.path.join(saved_cwd, template_dir),
                os.path.join(datasette_dir, 'templates')
            )
        if plugins_dir:
            link_or_copy_directory(
                os.path.join(saved_cwd, plugins_dir),
                os.path.join(datasette_dir, 'plugins')
            )
        for mount_point, path in static:
            link_or_copy_directory(
                os.path.join(saved_cwd, path),
                os.path.join(datasette_dir, mount_point)
            )
        yield datasette_dir
    finally:
        tmp.cleanup()
        os.chdir(saved_cwd)


@contextmanager
def temporary_heroku_directory(
    files,
    name,
    metadata,
    extra_options,
    branch,
    template_dir,
    plugins_dir,
    static,
    install,
    extra_metadata=None
):
    # FIXME: lots of duplicated code from above

    extra_metadata = extra_metadata or {}
    tmp = tempfile.TemporaryDirectory()
    saved_cwd = os.getcwd()

    file_paths = [
        os.path.join(saved_cwd, file_path)
        for file_path in files
    ]
    file_names = [os.path.split(f)[-1] for f in files]

    if metadata:
        metadata_content = json.load(metadata)
    else:
        metadata_content = {}
    for key, value in extra_metadata.items():
        if value:
            metadata_content[key] = value

    try:
        os.chdir(tmp.name)

        if metadata_content:
            open('metadata.json', 'w').write(json.dumps(metadata_content, indent=2))

        open('runtime.txt', 'w').write('python-3.6.3')

        if branch:
            install = ['https://github.com/simonw/datasette/archive/{branch}.zip'.format(
                branch=branch
            )] + list(install)
        else:
            install = ['datasette'] + list(install)

        open('requirements.txt', 'w').write('\n'.join(install))
        os.mkdir('bin')
        open('bin/post_compile', 'w').write('datasette inspect --inspect-file inspect-data.json')

        extras = []
        if template_dir:
            link_or_copy_directory(
                os.path.join(saved_cwd, template_dir),
                os.path.join(tmp.name, 'templates')
            )
            extras.extend(['--template-dir', 'templates/'])
        if plugins_dir:
            link_or_copy_directory(
                os.path.join(saved_cwd, plugins_dir),
                os.path.join(tmp.name, 'plugins')
            )
            extras.extend(['--plugins-dir', 'plugins/'])

        if metadata:
            extras.extend(['--metadata', 'metadata.json'])
        for mount_point, path in static:
            link_or_copy_directory(
                os.path.join(saved_cwd, path),
                os.path.join(tmp.name, mount_point)
            )
            extras.extend(['--static', '{}:{}'.format(mount_point, mount_point)])

        quoted_files = " ".join(map(shlex.quote, file_names))
        procfile_cmd = 'web: datasette serve --host 0.0.0.0 {quoted_files} --cors --port $PORT --inspect-file inspect-data.json {extras}'.format(
            quoted_files=quoted_files,
            extras=' '.join(extras),
        )
        open('Procfile', 'w').write(procfile_cmd)

        for path, filename in zip(file_paths, file_names):
            link_or_copy(path, os.path.join(tmp.name, filename))

        yield

    finally:
        tmp.cleanup()
        os.chdir(saved_cwd)


def get_all_foreign_keys(conn):
    tables = [r[0] for r in conn.execute('select name from sqlite_master where type="table"')]
    table_to_foreign_keys = {}
    for table in tables:
        table_to_foreign_keys[table] = {
            'incoming': [],
            'outgoing': [],
        }
    for table in tables:
        infos = conn.execute(
            'PRAGMA foreign_key_list([{}])'.format(table)
        ).fetchall()
        for info in infos:
            if info is not None:
                id, seq, table_name, from_, to_, on_update, on_delete, match = info
                if table_name not in table_to_foreign_keys:
                    # Weird edge case where something refers to a table that does
                    # not actually exist
                    continue
                table_to_foreign_keys[table_name]['incoming'].append({
                    'other_table': table,
                    'column': to_,
                    'other_column': from_
                })
                table_to_foreign_keys[table]['outgoing'].append({
                    'other_table': table_name,
                    'column': from_,
                    'other_column': to_
                })

    return table_to_foreign_keys


def detect_spatialite(conn):
    rows = conn.execute('select 1 from sqlite_master where tbl_name = "geometry_columns"').fetchall()
    return len(rows) > 0


def detect_fts(conn, table):
    "Detect if table has a corresponding FTS virtual table and return it"
    rows = conn.execute(detect_fts_sql(table)).fetchall()
    if len(rows) == 0:
        return None
    else:
        return rows[0][0]


def detect_fts_sql(table):
    return r'''
        select name from sqlite_master
            where rootpage = 0
            and (
                sql like '%VIRTUAL TABLE%USING FTS%content="{table}"%'
                or (
                    tbl_name = "{table}"
                    and sql like '%VIRTUAL TABLE%USING FTS%'
                )
            )
    '''.format(table=table)


class Filter:
    def __init__(self, key, display, sql_template, human_template, format='{}', numeric=False, no_argument=False):
        self.key = key
        self.display = display
        self.sql_template = sql_template
        self.human_template = human_template
        self.format = format
        self.numeric = numeric
        self.no_argument = no_argument

    def where_clause(self, column, value, param_counter):
        converted = self.format.format(value)
        if self.numeric and converted.isdigit():
            converted = int(converted)
        if self.no_argument:
            kwargs = {
                'c': column,
            }
            converted = None
        else:
            kwargs = {
                'c': column,
                'p': 'p{}'.format(param_counter),
            }
        return self.sql_template.format(**kwargs), converted

    def human_clause(self, column, value):
        if callable(self.human_template):
            template = self.human_template(column, value)
        else:
            template = self.human_template
        if self.no_argument:
            return template.format(c=column)
        else:
            return template.format(c=column, v=value)


class Filters:
    _filters = [
        Filter('exact', '=', '"{c}" = :{p}', lambda c, v: '{c} = {v}' if v.isdigit() else '{c} = "{v}"'),
        Filter('not', '!=', '"{c}" != :{p}', lambda c, v: '{c} != {v}' if v.isdigit() else '{c} != "{v}"'),
        Filter('contains', 'contains', '"{c}" like :{p}', '{c} contains "{v}"', format='%{}%'),
        Filter('endswith', 'ends with', '"{c}" like :{p}', '{c} ends with "{v}"', format='%{}'),
        Filter('startswith', 'starts with', '"{c}" like :{p}', '{c} starts with "{v}"', format='{}%'),
        Filter('gt', '>', '"{c}" > :{p}', '{c} > {v}', numeric=True),
        Filter('gte', '\u2265', '"{c}" >= :{p}', '{c} \u2265 {v}', numeric=True),
        Filter('lt', '<', '"{c}" < :{p}', '{c} < {v}', numeric=True),
        Filter('lte', '\u2264', '"{c}" <= :{p}', '{c} \u2264 {v}', numeric=True),
        Filter('glob', 'glob', '"{c}" glob :{p}', '{c} glob "{v}"'),
        Filter('like', 'like', '"{c}" like :{p}', '{c} like "{v}"'),
        Filter('isnull', 'is null', '"{c}" is null', '{c} is null', no_argument=True),
        Filter('notnull', 'is not null', '"{c}" is not null', '{c} is not null', no_argument=True),
        Filter('isblank', 'is blank', '("{c}" is null or "{c}" = "")', '{c} is blank', no_argument=True),
        Filter('notblank', 'is not blank', '("{c}" is not null and "{c}" != "")', '{c} is not blank', no_argument=True),
    ]
    _filters_by_key = {
        f.key: f for f in _filters
    }

    def __init__(self, pairs, units={}, ureg=None):
        self.pairs = pairs
        self.units = units
        self.ureg = ureg

    def lookups(self):
        "Yields (lookup, display, no_argument) pairs"
        for filter in self._filters:
            yield filter.key, filter.display, filter.no_argument

    def human_description_en(self, extra=None):
        bits = []
        if extra:
            bits.extend(extra)
        for column, lookup, value in self.selections():
            filter = self._filters_by_key.get(lookup, None)
            if filter:
                bits.append(filter.human_clause(column, value))
        # Comma separated, with an ' and ' at the end
        and_bits = []
        commas, tail = bits[:-1], bits[-1:]
        if commas:
            and_bits.append(', '.join(commas))
        if tail:
            and_bits.append(tail[0])
        s = ' and '.join(and_bits)
        if not s:
            return ''
        return 'where {}'.format(s)

    def selections(self):
        "Yields (column, lookup, value) tuples"
        for key, value in self.pairs:
            if '__' in key:
                column, lookup = key.rsplit('__', 1)
            else:
                column = key
                lookup = 'exact'
            yield column, lookup, value

    def has_selections(self):
        return bool(self.pairs)

    def convert_unit(self, column, value):
        "If the user has provided a unit in the quey, convert it into the column unit, if present."
        if column not in self.units:
            return value

        # Try to interpret the value as a unit
        value = self.ureg(value)
        if isinstance(value, numbers.Number):
            # It's just a bare number, assume it's the column unit
            return value

        column_unit = self.ureg(self.units[column])
        return value.to(column_unit).magnitude

    def build_where_clauses(self):
        sql_bits = []
        params = {}
        for i, (column, lookup, value) in enumerate(self.selections()):
            filter = self._filters_by_key.get(lookup, None)
            if filter:
                sql_bit, param = filter.where_clause(column, self.convert_unit(column, value), i)
                sql_bits.append(sql_bit)
                if param is not None:
                    param_id = 'p{}'.format(i)
                    params[param_id] = param
        return sql_bits, params


filter_column_re = re.compile(r'^_filter_column_\d+$')


def filters_should_redirect(special_args):
    redirect_params = []
    # Handle _filter_column=foo&_filter_op=exact&_filter_value=...
    filter_column = special_args.get('_filter_column')
    filter_op = special_args.get('_filter_op') or ''
    filter_value = special_args.get('_filter_value') or ''
    if '__' in filter_op:
        filter_op, filter_value = filter_op.split('__', 1)
    if filter_column:
        redirect_params.append(
            ('{}__{}'.format(filter_column, filter_op), filter_value)
        )
    for key in ('_filter_column', '_filter_op', '_filter_value'):
        if key in special_args:
            redirect_params.append((key, None))
    # Now handle _filter_column_1=name&_filter_op_1=contains&_filter_value_1=hello
    column_keys = [k for k in special_args if filter_column_re.match(k)]
    for column_key in column_keys:
        number = column_key.split('_')[-1]
        column = special_args[column_key]
        op = special_args.get('_filter_op_{}'.format(number)) or 'exact'
        value = special_args.get('_filter_value_{}'.format(number)) or ''
        if '__' in op:
            op, value = op.split('__', 1)
        if column:
            redirect_params.append(('{}__{}'.format(column, op), value))
        redirect_params.extend([
            ('_filter_column_{}'.format(number), None),
            ('_filter_op_{}'.format(number), None),
            ('_filter_value_{}'.format(number), None),
        ])
    return redirect_params


whitespace_re = re.compile(r'\s')


def is_url(value):
    "Must start with http:// or https:// and contain JUST a URL"
    if not isinstance(value, str):
        return False
    if not value.startswith('http://') and not value.startswith('https://'):
        return False
    # Any whitespace at all is invalid
    if whitespace_re.search(value):
        return False
    return True


css_class_re = re.compile(r'^[a-zA-Z]+[_a-zA-Z0-9-]*$')
css_invalid_chars_re = re.compile(r'[^a-zA-Z0-9_\-]')


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
    md5_suffix = hashlib.md5(s.encode('utf8')).hexdigest()[:6]
    # Strip leading _, -
    s = s.lstrip('_').lstrip('-')
    # Replace any whitespace with hyphens
    s = '-'.join(s.split())
    # Remove any remaining invalid characters
    s = css_invalid_chars_re.sub('', s)
    # Attach the md5 suffix
    bits = [b for b in (s, md5_suffix) if b]
    return '-'.join(bits)


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
        shutil.copytree(src, dst, copy_function=os.link)
    except OSError:
        shutil.copytree(src, dst)


def module_from_path(path, name):
    # Adapted from http://sayspy.blogspot.com/2011/07/how-to-import-module-from-just-file.html
    mod = imp.new_module(name)
    mod.__file__ = path
    with open(path, 'r') as file:
        code = compile(file.read(), path, 'exec', dont_inherit=True)
    exec(code, mod.__dict__)
    return mod


def get_plugins(pm):
    plugins = []
    plugin_to_distinfo = dict(pm.list_plugin_distinfo())
    for plugin in pm.get_plugins():
        static_path = None
        templates_path = None
        try:
            if pkg_resources.resource_isdir(plugin.__name__, 'static'):
                static_path = pkg_resources.resource_filename(plugin.__name__, 'static')
            if pkg_resources.resource_isdir(plugin.__name__, 'templates'):
                templates_path = pkg_resources.resource_filename(plugin.__name__, 'templates')
        except (KeyError, ImportError):
            # Caused by --plugins_dir= plugins - KeyError/ImportError thrown in Py3.5
            pass
        plugin_info = {
            'name': plugin.__name__,
            'static_path': static_path,
            'templates_path': templates_path,
        }
        distinfo = plugin_to_distinfo.get(plugin)
        if distinfo:
            plugin_info['version'] = distinfo.version
        plugins.append(plugin_info)
    return plugins
