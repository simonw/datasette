from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from sanic.views import HTTPMethodView
from sanic_jinja2 import SanicJinja2
from jinja2 import FileSystemLoader
import sqlite3
from pathlib import Path
from concurrent import futures
import asyncio
import threading
import urllib.parse
import json
import hashlib
import time
from .utils import (
    build_where_clause,
    CustomJSONEncoder,
    InvalidSql,
    path_from_row_pks,
    compound_pks_from_path,
    sqlite_timelimit,
    validate_sql_select,
)

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

    def __init__(self, datasette):
        self.files = datasette.files
        self.jinja = datasette.jinja
        self.executor = datasette.executor
        self.cache_headers = datasette.cache_headers

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
        if self.cache_headers:
            r.headers['Cache-Control'] = 'max-age={}'.format(
                365 * 24 * 60 * 60
            )
        return r


class IndexView(HTTPMethodView):
    def __init__(self, datasette):
        self.files = datasette.files
        self.jinja = datasette.jinja
        self.executor = datasette.executor

    async def get(self, request):
        databases = []
        for key, info in sorted(ensure_build_metadata(self.files).items()):
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
        if use_rowid:
            select = 'rowid, *'
            order_by = 'rowid'
        else:
            select = '*'
            order_by = ', '.join(pks)

        if request.args:
            where_clause, params = build_where_clause(request.args)
            sql = 'select {} from "{}" where {} order by {} limit 50'.format(
                select, table, where_clause, order_by
            )
        else:
            sql = 'select {} from "{}" order by {} limit 50'.format(
                select, table, order_by
            )
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


class Datasette:
    def __init__(self, files, num_threads=3, cache_headers=True):
        self.files = files
        self.num_threads = num_threads
        self.executor = futures.ThreadPoolExecutor(
            max_workers=num_threads
        )
        self.cache_headers = cache_headers

    def app(self):
        app = Sanic(__name__)
        self.jinja = SanicJinja2(
            app,
            loader=FileSystemLoader([
                str(app_root / 'datasette' / 'templates')
            ])
        )
        app.add_route(IndexView.as_view(self), '/')
        # TODO: /favicon.ico and /-/static/ deserve far-future cache expires
        app.add_route(favicon, '/favicon.ico')
        app.static('/-/static/', str(app_root / 'datasette' / 'static'))
        app.add_route(
            DatabaseView.as_view(self),
            '/<db_name:[^/\.]+?><as_json:(.jsono?)?$>'
        )
        app.add_route(
            DatabaseDownload.as_view(self),
            '/<db_name:[^/]+?><as_db:(\.db)$>'
        )
        app.add_route(
            TableView.as_view(self),
            '/<db_name:[^/]+>/<table:[^/]+?><as_json:(.jsono?)?$>'
        )
        app.add_route(
            RowView.as_view(self),
            '/<db_name:[^/]+>/<table:[^/]+?>/<pk_path:[^/]+?><as_json:(.jsono?)?$>'
        )
        return app
