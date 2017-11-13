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
    build_where_clauses,
    CustomJSONEncoder,
    escape_css_string,
    escape_sqlite_table_name,
    InvalidSql,
    path_from_row_pks,
    path_with_added_args,
    path_with_ext,
    compound_pks_from_path,
    sqlite_timelimit,
    validate_sql_select,
)

app_root = Path(__file__).parent.parent

HASH_BLOCK_SIZE = 1024 * 1024

connections = threading.local()


class BaseView(HTTPMethodView):
    template = None

    def __init__(self, datasette):
        self.ds = datasette
        self.files = datasette.files
        self.jinja = datasette.jinja
        self.executor = datasette.executor
        self.page_size = datasette.page_size
        self.max_returned_rows = datasette.max_returned_rows

    def options(self, request, *args, **kwargs):
        r = response.text('ok')
        if self.ds.cors:
            r.headers['Access-Control-Allow-Origin'] = '*'
        return r

    def redirect(self, request, path):
        if request.query_string:
            path = '{}?{}'.format(
                path, request.query_string
            )
        r = response.redirect(path)
        r.headers['Link'] = '<{}>; rel=preload'.format(path)
        if self.ds.cors:
            r.headers['Access-Control-Allow-Origin'] = '*'
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

    def resolve_db_name(self, db_name, **kwargs):
        databases = self.ds.inspect()
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

    async def execute(self, db_name, sql, params=None, truncate=False):
        """Executes sql against db_name in a thread"""
        def sql_operation_in_thread():
            conn = getattr(connections, db_name, None)
            if not conn:
                info = self.ds.inspect()[db_name]
                conn = sqlite3.connect(
                    'file:{}?immutable=1'.format(info['file']),
                    uri=True,
                    check_same_thread=False,
                )
                conn.row_factory = sqlite3.Row
                conn.text_factory = lambda x: str(x, 'utf-8', 'replace')
                setattr(connections, db_name, conn)

            with sqlite_timelimit(conn, self.ds.sql_time_limit_ms):
                try:
                    cursor = conn.cursor()
                    cursor.execute(sql, params or {})
                    if self.max_returned_rows and truncate:
                        rows = cursor.fetchmany(self.max_returned_rows + 1)
                        truncated = len(rows) > self.max_returned_rows
                        rows = rows[:self.max_returned_rows]
                    else:
                        rows = cursor.fetchall()
                        truncated = False
                except Exception:
                    print('ERROR: conn={}, sql = {}, params = {}'.format(
                        conn, repr(sql), params
                    ))
                    raise
            if truncate:
                return rows, truncated, cursor.description
            else:
                return rows

        return await asyncio.get_event_loop().run_in_executor(
            self.executor, sql_operation_in_thread
        )

    async def get(self, request, db_name, **kwargs):
        name, hash, should_redirect = self.resolve_db_name(db_name, **kwargs)
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
        template = self.template
        status_code = 200
        try:
            data, extra_template_data = await self.data(
                request, name, hash, **kwargs
            )
        except (sqlite3.OperationalError, InvalidSql) as e:
            data = {
                'ok': False,
                'error': str(e),
                'database': name,
                'database_hash': hash,
            }
            template = 'error.html'
            status_code = 400
        end = time.time()
        data['query_ms'] = (end - start) * 1000
        for key in ('source', 'source_url', 'license', 'license_url'):
            value = self.ds.metadata.get(key)
            if value:
                data[key] = value
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
            headers = {}
            if self.ds.cors:
                headers['Access-Control-Allow-Origin'] = '*'
            r = response.HTTPResponse(
                json.dumps(
                    data, cls=CustomJSONEncoder
                ),
                status=status_code,
                content_type='application/json',
                headers=headers,
            )
        else:
            context = {**data, **dict(
                extra_template_data()
                if callable(extra_template_data)
                else extra_template_data
            ), **{
                'url_json': path_with_ext(request, '.json'),
                'url_jsono': path_with_ext(request, '.jsono'),
                'metadata': self.ds.metadata,
            }}
            r = self.jinja.render(
                template,
                request,
                **context,
            )
            r.status = status_code
        # Set far-future cache expiry
        if self.ds.cache_headers:
            r.headers['Cache-Control'] = 'max-age={}'.format(
                365 * 24 * 60 * 60
            )
        return r


class IndexView(HTTPMethodView):
    def __init__(self, datasette):
        self.ds = datasette
        self.files = datasette.files
        self.jinja = datasette.jinja
        self.executor = datasette.executor

    async def get(self, request, as_json):
        databases = []
        for key, info in sorted(self.ds.inspect().items()):
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
                'table_rows': sum(info['tables'].values()),
            }
            databases.append(database)
        if as_json:
            return response.HTTPResponse(
                json.dumps(
                    {db['name']: db for db in databases},
                    cls=CustomJSONEncoder
                ),
                content_type='application/json',
                headers={
                    'Access-Control-Allow-Origin': '*'
                }
            )
        else:
            return self.jinja.render(
                'index.html',
                request,
                databases=databases,
                metadata=self.ds.metadata,
            )


async def favicon(request):
    return response.text('')


class DatabaseView(BaseView):
    template = 'database.html'

    async def data(self, request, name, hash):
        if request.args.get('sql'):
            return await self.custom_sql(request, name, hash)
        tables = []
        table_inspect = self.ds.inspect()[name]['tables']
        for table_name, table_rows in table_inspect.items():
            rows = await self.execute(
                name,
                'PRAGMA table_info([{}]);'.format(table_name)
            )
            tables.append({
                'name': table_name,
                'columns': [r[1] for r in rows],
                'table_rows': table_rows,
            })
        tables.sort(key=lambda t: t['name'])
        views = await self.execute(name, 'select name from sqlite_master where type = "view"')
        return {
            'database': name,
            'tables': tables,
            'views': [v[0] for v in views],
        }, {
            'database_hash': hash,
        }

    async def custom_sql(self, request, name, hash):
        params = request.raw_args
        sql = params.pop('sql')
        validate_sql_select(sql)
        rows, truncated, description = await self.execute(name, sql, params, truncate=True)
        columns = [r[0] for r in description]
        return {
            'database': name,
            'rows': rows,
            'truncated': truncated,
            'columns': columns,
            'query': {
                'sql': sql,
                'params': params,
            }
        }, {
            'database_hash': hash,
            'custom_sql': True,
        }


class DatabaseDownload(BaseView):
    async def view_get(self, request, name, hash, **kwargs):
        filepath = self.ds.inspect()[name]['file']
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
        is_view = bool(list(await self.execute(name, "SELECT count(*) from sqlite_master WHERE type = 'view' and name=:n", {
            'n': table,
        }))[0][0])
        view_definition = None
        table_definition = None
        if is_view:
            view_definition = list(await self.execute(name, 'select sql from sqlite_master where name = :n and type="view"', {
                'n': table,
            }))[0][0]
        else:
            table_definition = list(await self.execute(name, 'select sql from sqlite_master where name = :n and type="table"', {
                'n': table,
            }))[0][0]
        use_rowid = not pks and not is_view
        if use_rowid:
            select = 'rowid, *'
            order_by = 'rowid'
        else:
            select = '*'
            order_by = ', '.join(pks)

        if is_view:
            order_by = ''

        # Special args start with _ and do not contain a __
        # That's so if there is a column that starts with _
        # it can still be queried using ?_col__exact=blah
        special_args = {}
        other_args = {}
        for key, value in request.args.items():
            if key.startswith('_') and '__' not in key:
                special_args[key] = value[0]
            else:
                other_args[key] = value[0]

        if other_args:
            where_clauses, params = build_where_clauses(other_args)
        else:
            where_clauses = []
            params = {}

        next = special_args.get('_next')
        offset = ''
        if next:
            if is_view:
                # _next is an offset
                offset = ' offset {}'.format(int(next))
            elif use_rowid:
                where_clauses.append(
                    'rowid > :p{}'.format(
                        len(params),
                    )
                )
                params['p{}'.format(len(params))] = next
            else:
                pk_values = compound_pks_from_path(next)
                if len(pk_values) == len(pks):
                    param_counter = len(params)
                    for pk, value in zip(pks, pk_values):
                        where_clauses.append(
                            '"{}" > :p{}'.format(
                                pk, param_counter,
                            )
                        )
                        params['p{}'.format(param_counter)] = value
                        param_counter += 1

        where_clause = ''
        if where_clauses:
            where_clause = 'where {} '.format(' and '.join(where_clauses))

        if order_by:
            order_by = 'order by {} '.format(order_by)

        sql = 'select {select} from {table_name} {where}{order_by}limit {limit}{offset}'.format(
            select=select,
            table_name=escape_sqlite_table_name(table),
            where=where_clause,
            order_by=order_by,
            limit=self.page_size + 1,
            offset=offset,
        )

        rows, truncated, description = await self.execute(name, sql, params, truncate=True)

        columns = [r[0] for r in description]
        display_columns = columns
        if use_rowid:
            display_columns = display_columns[1:]
        rows = list(rows)
        info = self.ds.inspect()
        table_rows = info[name]['tables'].get(table)
        next_value = None
        next_url = None
        if len(rows) > self.page_size:
            if is_view:
                next_value = int(next or 0) + self.page_size
            else:
                next_value = path_from_row_pks(rows[-2], pks, use_rowid)
            next_url = urllib.parse.urljoin(request.url, path_with_added_args(request, {
                '_next': next_value,
            }))
        return {
            'database': name,
            'table': table,
            'is_view': is_view,
            'view_definition': view_definition,
            'table_definition': table_definition,
            'rows': rows[:self.page_size],
            'truncated': truncated,
            'table_rows': table_rows,
            'columns': columns,
            'primary_keys': pks,
            'query': {
                'sql': sql,
                'params': params,
            },
            'next': next_value and str(next_value) or None,
            'next_url': next_url,
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
            'primary_key_values': pk_values,
        }, {
            'database_hash': hash,
            'row_link': None,
        }


class Datasette:
    def __init__(
            self, files, num_threads=3, cache_headers=True, page_size=100,
            max_returned_rows=1000, sql_time_limit_ms=1000, cors=False,
            inspect_data=None, metadata=None):
        self.files = files
        self.num_threads = num_threads
        self.executor = futures.ThreadPoolExecutor(
            max_workers=num_threads
        )
        self.cache_headers = cache_headers
        self.page_size = page_size
        self.max_returned_rows = max_returned_rows
        self.sql_time_limit_ms = sql_time_limit_ms
        self.cors = cors
        self._inspect = inspect_data
        self.metadata = metadata or {}

    def inspect(self):
        if not self._inspect:
            self._inspect = {}
            for filename in self.files:
                path = Path(filename)
                name = path.stem
                if name in self._inspect:
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
                with sqlite3.connect('file:{}?immutable=1'.format(path), uri=True) as conn:
                    conn.row_factory = sqlite3.Row
                    table_names = [
                        r['name']
                        for r in conn.execute('select * from sqlite_master where type="table"')
                    ]
                    for table in table_names:
                        tables[table] = conn.execute('select count(*) from "{}"'.format(table)).fetchone()[0]

                self._inspect[name] = {
                    'hash': m.hexdigest(),
                    'file': str(path),
                    'tables': tables,
                }
        return self._inspect

    def app(self):
        app = Sanic(__name__)
        self.jinja = SanicJinja2(
            app,
            loader=FileSystemLoader([
                str(app_root / 'datasette' / 'templates')
            ])
        )
        self.jinja.add_env('escape_css_string', escape_css_string, 'filters')
        self.jinja.add_env('quote_plus', lambda u: urllib.parse.quote_plus(u), 'filters')
        self.jinja.add_env('escape_table_name', escape_sqlite_table_name, 'filters')
        app.add_route(IndexView.as_view(self), '/<as_json:(.jsono?)?$>')
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
