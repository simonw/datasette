from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from sanic.views import HTTPMethodView
from sanic_jinja2 import SanicJinja2
from jinja2 import FileSystemLoader
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from functools import wraps
from concurrent import futures
import asyncio
import threading
import urllib.parse
import json
import base64
import hashlib
import sys
import time

app_root = Path(__file__).parent.parent

BUILD_METADATA = 'build-metadata.json'
HASH_BLOCK_SIZE = 1024 * 1024
SQL_TIME_LIMIT_MS = 1000

connections = threading.local()


def ensure_build_metadata(files, regenerate=False):
    build_metadata = app_root / BUILD_METADATA
    if build_metadata.exists() and not regenerate:
        return json.loads(build_metadata.read_text())
    print('Building metadata... path={}'.format(build_metadata))
    metadata = {}
    for filename in files:
        path = Path(filename)
        name = path.stem
        if name in metadata:
            raise Exception('Multiple files with same stem %s' % name)
        # Calculate hash, efficiently
        m = hashlib.sha256()
        with path.open('rb') as fp:
            while True:
                data = fp.read(HASH_BLOCK_SIZE)
                if not data:
                    break
                m.update(data)
        # List tables and their row counts
        tables = {}
        with sqlite3.connect('file:{}?immutable=1'.format(path.name), uri=True) as conn:
            conn.row_factory = sqlite3.Row
            table_names = [
                r['name']
                for r in conn.execute('select * from sqlite_master where type="table"')
            ]
            for table in table_names:
                tables[table] = conn.execute('select count(*) from "{}"'.format(table)).fetchone()[0]

        metadata[name] = {
            'hash': m.hexdigest(),
            'file': path.name,
            'tables': tables,
        }
    build_metadata.write_text(json.dumps(metadata, indent=4))
    return metadata


class BaseView(HTTPMethodView):
    template = None

    def __init__(self, files, jinja, executor):
        self.files = files
        self.jinja = jinja
        self.executor = executor

    def redirect(self, request, path):
        if request.query_string:
            path = '{}?{}'.format(
                path, request.query_string
            )
        r = response.redirect(path)
        r.headers['Link'] = '<{}>; rel=preload'.format(path)
        return r

    async def pks_for_table(self, name, table):
        rows = [
            row for row in await self.execute(
                name,
                'PRAGMA table_info("{}")'.format(table)
            )
            if row[-1]
        ]
        rows.sort(key=lambda row: row[-1])
        return [str(r[1]) for r in rows]

    async def execute(self, db_name, sql, params=None):
        """Executes sql against db_name in a thread"""
        def sql_operation_in_thread():
            conn = getattr(connections, db_name, None)
            if not conn:
                info = ensure_build_metadata(self.files)[db_name]
                conn = sqlite3.connect(
                    'file:{}?immutable=1'.format(info['file']),
                    uri=True,
                    check_same_thread=False,
                )
                conn.row_factory = sqlite3.Row
                conn.text_factory = lambda x: str(x, 'utf-8', 'replace')
                setattr(connections, db_name, conn)

            with sqlite_timelimit(conn, SQL_TIME_LIMIT_MS):
                try:
                    rows = conn.execute(sql, params or {})
                except Exception:
                    print('sql = {}, params = {}'.format(
                        sql, params
                    ))
                    raise
            return rows

        return await asyncio.get_event_loop().run_in_executor(
            self.executor, sql_operation_in_thread
        )

    async def get(self, request, db_name, **kwargs):
        name, hash, should_redirect = resolve_db_name(self.files, db_name, **kwargs)
        if should_redirect:
            return self.redirect(request, should_redirect)
        return await self.view_get(request, name, hash, **kwargs)

    async def view_get(self, request, name, hash, **kwargs):
        try:
            as_json = kwargs.pop('as_json')
        except KeyError:
            as_json = False
        extra_template_data = {}
        start = time.time()
        try:
            data, extra_template_data = await self.data(
                request, name, hash, **kwargs
            )
        except (sqlite3.OperationalError, InvalidSql) as e:
            data = {
                'ok': False,
                'error': str(e),
            }
        end = time.time()
        data['took_ms'] = (end - start) * 1000
        if as_json:
            # Special case for .jsono extension
            if as_json == '.jsono':
                columns = data.get('columns')
                rows = data.get('rows')
                if rows and columns:
                    data['rows'] = [
                        dict(zip(columns, row))
                        for row in rows
                    ]
            r = response.HTTPResponse(
                json.dumps(
                    data, cls=CustomJSONEncoder
                ),
                content_type='application/json',
                headers={
                    'Access-Control-Allow-Origin': '*'
                }
            )
        else:
            context = {**data, **dict(
                extra_template_data()
                if callable(extra_template_data)
                else extra_template_data
            )}
            r = self.jinja.render(
                self.template,
                request,
                **context,
            )
        # Set far-future cache expiry
        r.headers['Cache-Control'] = 'max-age={}'.format(
            365 * 24 * 60 * 60
        )
        return r


class IndexView(HTTPMethodView):
    def __init__(self, files, jinja, executor):
        self.files = files
        self.jinja = jinja
        self.executor = executor

    async def get(self, request):
        databases = []
        for key, info in ensure_build_metadata(self.files).items():
            database = {
                'name': key,
                'hash': info['hash'],
                'path': '{}-{}'.format(key, info['hash'][:7]),
                'tables_truncated': sorted(
                    info['tables'].items(),
                    key=lambda p: p[1],
                    reverse=True
                )[:5],
                'tables_count': len(info['tables'].items()),
                'tables_more': len(info['tables'].items()) > 5,
                'total_rows': sum(info['tables'].values()),
            }
            databases.append(database)
        return self.jinja.render(
            'index.html',
            request,
            databases=databases,
        )


async def favicon(request):
    return response.text('')


class DatabaseView(BaseView):
    template = 'database.html'

    async def data(self, request, name, hash):
        sql = 'select * from sqlite_master'
        params = {}
        if request.args.get('sql'):
            params = request.raw_args
            sql = params.pop('sql')
            validate_sql_select(sql)
        rows = await self.execute(name, sql, params)
        columns = [r[0] for r in rows.description]
        return {
            'database': name,
            'rows': rows,
            'columns': columns,
        }, {
            'database_hash': hash,
        }


class DatabaseDownload(BaseView):
    async def view_get(self, request, name, hash, **kwargs):
        filepath = ensure_build_metadata(self.files)[name]['file']
        return await response.file_stream(
            filepath, headers={
                'Content-Disposition': 'attachment; filename="{}"'.format(filepath)
            }
        )


class TableView(BaseView):
    template = 'table.html'

    async def data(self, request, name, hash, table):
        table = urllib.parse.unquote_plus(table)
        pks = await self.pks_for_table(name, table)
        use_rowid = not pks
        select = '*'
        if use_rowid:
            select = 'rowid, *'
        if request.args:
            where_clause, params = build_where_clause(request.args)
            sql = 'select {} from "{}" where {} limit 50'.format(
                select, table, where_clause
            )
        else:
            sql = 'select {} from "{}" limit 50'.format(select, table)
            params = []

        rows = await self.execute(name, sql, params)

        columns = [r[0] for r in rows.description]
        display_columns = columns
        if use_rowid:
            display_columns = display_columns[1:]
        rows = list(rows)
        info = ensure_build_metadata(self.files)
        total_rows = info[name]['tables'].get(table)
        return {
            'database': name,
            'table': table,
            'rows': rows,
            'total_rows': total_rows,
            'columns': columns,
            'primary_keys': pks,
            'sql': sql,
            'sql_params': params,
        }, lambda: {
            'database_hash': hash,
            'use_rowid': use_rowid,
            'row_link': lambda row: path_from_row_pks(row, pks, use_rowid),
            'display_columns': display_columns,
        }


class RowView(BaseView):
    template = 'row.html'

    async def data(self, request, name, hash, table, pk_path):
        table = urllib.parse.unquote_plus(table)
        pk_values = compound_pks_from_path(pk_path)
        pks = await self.pks_for_table(name, table)
        use_rowid = not pks
        select = '*'
        if use_rowid:
            select = 'rowid, *'
            pks = ['rowid']
        wheres = [
            '"{}"=:p{}'.format(pk, i)
            for i, pk in enumerate(pks)
        ]
        sql = 'select {} from "{}" where {}'.format(
            select, table, ' AND '.join(wheres)
        )
        params = {}
        for i, pk_value in enumerate(pk_values):
            params['p{}'.format(i)] = pk_value
        rows = await self.execute(name, sql, params)
        columns = [r[0] for r in rows.description]
        rows = list(rows)
        if not rows:
            raise NotFound('Record not found: {}'.format(pk_values))
        return {
            'database': name,
            'table': table,
            'rows': rows,
            'columns': columns,
            'primary_keys': pks,
        }, {
            'database_hash': hash,
            'row_link': None,
        }


def resolve_db_name(files, db_name, **kwargs):
    databases = ensure_build_metadata(files)
    hash = None
    name = None
    if '-' in db_name:
        # Might be name-and-hash, or might just be
        # a name with a hyphen in it
        name, hash = db_name.rsplit('-', 1)
        if name not in databases:
            # Try the whole name
            name = db_name
            hash = None
    else:
        name = db_name
    # Verify the hash
    try:
        info = databases[name]
    except KeyError:
        raise NotFound('Database not found: {}'.format(name))
    expected = info['hash'][:7]
    if expected != hash:
        should_redirect = '/{}-{}'.format(
            name, expected,
        )
        if 'table' in kwargs:
            should_redirect += '/' + kwargs['table']
        if 'as_json' in kwargs:
            should_redirect += kwargs['as_json']
        if 'as_db' in kwargs:
            should_redirect += kwargs['as_db']
        return name, expected, should_redirect
    return name, expected, None


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


def build_where_clause(args):
    sql_bits = []
    params = {}
    for i, (key, values) in enumerate(args.items()):
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
        value = values[0]
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
    where_clause = ' and '.join(sql_bits)
    return where_clause, params


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


def app_factory(files, num_threads=3):
    app = Sanic(__name__)
    executor = futures.ThreadPoolExecutor(max_workers=num_threads)
    jinja = SanicJinja2(
        app,
        loader=FileSystemLoader([
            str(app_root / 'immutabase' / 'templates')
        ])
    )
    app.add_route(IndexView.as_view(files, jinja, executor), '/')
    # TODO: /favicon.ico and /-/static/ deserve far-future cache expires
    app.add_route(favicon, '/favicon.ico')
    app.static('/-/static/', str(app_root / 'immutabase' / 'static'))
    app.add_route(
        DatabaseView.as_view(files, jinja, executor),
        '/<db_name:[^/\.]+?><as_json:(.jsono?)?$>'
    )
    app.add_route(
        DatabaseDownload.as_view(files, jinja, executor),
        '/<db_name:[^/]+?><as_db:(\.db)$>'
    )
    app.add_route(
        TableView.as_view(files, jinja, executor),
        '/<db_name:[^/]+>/<table:[^/]+?><as_json:(.jsono?)?$>'
    )
    app.add_route(
        RowView.as_view(files, jinja, executor),
        '/<db_name:[^/]+>/<table:[^/]+?>/<pk_path:[^/]+?><as_json:(.jsono?)?$>'
    )
    return app


class InvalidSql(Exception):
    pass


def validate_sql_select(sql):
    sql = sql.strip().lower()
    if not sql.startswith('select '):
        raise InvalidSql('Statement must begin with SELECT')
    if 'pragma' in sql:
        raise InvalidSql('Statement may not contain PRAGMA')
