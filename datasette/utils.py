from contextlib import contextmanager
import base64
import json
import os
import re
import sqlite3
import tempfile
import time
import urllib


def compound_pks_from_path(path):
    return [
        urllib.parse.unquote_plus(b) for b in path.split(',')
    ]


def path_from_row_pks(row, pks, use_rowid):
    if use_rowid:
        return urllib.parse.quote_plus(str(row['rowid']))
    bits = []
    for pk in pks:
        bits.append(
            urllib.parse.quote_plus(str(row[pk]))
        )
    return ','.join(bits)


def build_where_clauses(args):
    sql_bits = []
    params = {}
    for i, (key, value) in enumerate(sorted(args.items())):
        if '__' in key:
            column, lookup = key.rsplit('__', 1)
        else:
            column = key
            lookup = 'exact'
        template = {
            'exact': '"{}" = :{}',
            'contains': '"{}" like :{}',
            'endswith': '"{}" like :{}',
            'startswith': '"{}" like :{}',
            'gt': '"{}" > :{}',
            'gte': '"{}" >= :{}',
            'lt': '"{}" < :{}',
            'lte': '"{}" <= :{}',
            'glob': '"{}" glob :{}',
            'like': '"{}" like :{}',
        }[lookup]
        numeric_operators = {'gt', 'gte', 'lt', 'lte'}
        value_convert = {
            'contains': lambda s: '%{}%'.format(s),
            'endswith': lambda s: '%{}'.format(s),
            'startswith': lambda s: '{}%'.format(s),
        }.get(lookup, lambda s: s)
        converted = value_convert(value)
        if lookup in numeric_operators and converted.isdigit():
            converted = int(converted)
        param_id = 'p{}'.format(i)
        sql_bits.append(
            template.format(column, param_id)
        )
        params[param_id] = converted
    return sql_bits, params


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

    def handler():
        if time.time() >= deadline:
            return 1
    conn.set_progress_handler(handler, 10000)
    yield
    conn.set_progress_handler(None, 10000)


class InvalidSql(Exception):
    pass


def validate_sql_select(sql):
    sql = sql.strip().lower()
    if not sql.startswith('select '):
        raise InvalidSql('Statement must begin with SELECT')
    if 'pragma' in sql:
        raise InvalidSql('Statement may not contain PRAGMA')


def path_with_added_args(request, args):
    current = request.raw_args.copy()
    current.update(args)
    return request.path + '?' + urllib.parse.urlencode(current)


def path_with_ext(request, ext):
    path = request.path
    path += ext
    if request.query_string:
        path += '?' + request.query_string
    return path


_css_re = re.compile(r'''['"\n\\]''')
_boring_table_name_re = re.compile(r'^[a-zA-Z0-9_]+$')


def escape_css_string(s):
    return _css_re.sub(lambda m: '\\{:X}'.format(ord(m.group())), s)


def escape_sqlite_table_name(s):
    if _boring_table_name_re.match(s):
        return s
    else:
        return '[{}]'.format(s)


def make_dockerfile(files, metadata_file, extra_options=''):
    cmd = ['"datasette"', '"serve"']
    cmd.append('"' + '", "'.join(files) + '"')
    cmd.extend(['"--port"', '"8001"', '"--inspect-file"', '"inspect-data.json"'])
    if metadata_file:
        cmd.extend(['"--metadata"', '"{}"'.format(metadata_file)])
    if extra_options:
        for opt in extra_options.split():
            cmd.append('"{}"'.format(opt))
    return '''
FROM python:3
COPY . /app
WORKDIR /app
RUN pip install datasette
RUN datasette build {} --inspect-file inspect-data.json
EXPOSE 8001
CMD [{}]'''.format(
        ' '.join(files),
        ', '.join(cmd)
    ).strip()


@contextmanager
def temporary_docker_directory(files, name, metadata, extra_options):
    tmp = tempfile.TemporaryDirectory()
    # We create a datasette folder in there to get a nicer now deploy name
    datasette_dir = os.path.join(tmp.name, name)
    os.mkdir(datasette_dir)
    saved_cwd = os.getcwd()
    file_paths = [
        os.path.join(saved_cwd, name)
        for name in files
    ]
    file_names = [os.path.split(f)[-1] for f in files]
    try:
        dockerfile = make_dockerfile(file_names, metadata and 'metadata.json', extra_options)
        os.chdir(datasette_dir)
        open('Dockerfile', 'w').write(dockerfile)
        if metadata:
            open('metadata.json', 'w').write(metadata.read())
        for path, filename in zip(file_paths, file_names):
            os.link(path, os.path.join(datasette_dir, filename))
        yield
    finally:
        tmp.cleanup()
        os.chdir(saved_cwd)
