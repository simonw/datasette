from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from sanic.views import HTTPMethodView
from sanic_jinja2 import SanicJinja2
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from functools import wraps
import urllib.parse
import json
import base64
import hashlib
import sys
import time

app_root = Path(__file__).parent

BUILD_METADATA = 'build-metadata.json'
DB_GLOBS = ('*.db', '*.sqlite', '*.sqlite3')
HASH_BLOCK_SIZE = 1024 * 1024
SQL_TIME_LIMIT_MS = 1000

conns = {}


app = Sanic(__name__)
jinja = SanicJinja2(app)


def get_conn(name):
    if name not in conns:
        info = ensure_build_metadata()[name]
        conns[name] = sqlite3.connect(
            'file:{}?immutable=1'.format(info['file']),
            uri=True
        )
        conns[name].row_factory = sqlite3.Row
        conns[name].text_factory = lambda x: str(x, 'utf-8', 'replace')
    return conns[name]


def ensure_build_metadata(regenerate=False):
    build_metadata = app_root / BUILD_METADATA
    if build_metadata.exists() and not regenerate:
        json.loads(build_metadata.read_text())
    metadata = {}
    for glob in DB_GLOBS:
        for path in app_root.glob(glob):
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

    def redirect(self, request, path):
        if request.query_string:
            path = '{}?{}'.format(
                path, request.query_string
            )
        r = response.redirect(path)
        r.headers['Link'] = '<{}>; rel=preload'.format(path)
        return r

    async def get(self, request, db_name, **kwargs):
        name, hash, should_redirect = resolve_db_name(db_name, **kwargs)
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
            data, extra_template_data = self.data(
                request, name, hash, **kwargs
            )
        except sqlite3.OperationalError as e:
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
            r = jinja.render(
                self.template,
                request,
                **context,
            )
        # Set far-future cache expiry
        r.headers['Cache-Control'] = 'max-age={}'.format(
            365 * 24 * 60 * 60
        )
        return r


@app.route('/')
async def index(request, sql=None):
    databases = ensure_build_metadata(True)
    return jinja.render(
        'index.html',
        request,
        databases=databases,
    )


@app.route('/favicon.ico')
async def favicon(request):
    return response.text('')


class DatabaseView(BaseView):
    template = 'database.html'

    def data(self, request, name, hash):
        conn = get_conn(name)
        sql = request.args.get('sql') or 'select * from sqlite_master'
        with sqlite_timelimit(conn, SQL_TIME_LIMIT_MS):
            rows = conn.execute(sql)
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
        filepath = ensure_build_metadata()[name]['file']
        return await response.file_stream(
            filepath, headers={
                'Content-Disposition': 'attachment; filename="{}"'.format(filepath)
            }
        )


class TableView(BaseView):
    template = 'table.html'

    def data(self, request, name, hash, table):
        conn = get_conn(name)
        table = urllib.parse.unquote_plus(table)
        if request.args:
            where_clause, params = build_where_clause(request.args)
            sql = 'select * from "{}" where {} limit 50'.format(
                table, where_clause
            )
        else:
            sql = 'select * from "{}" limit 50'.format(table)
            params = []

        with sqlite_timelimit(conn, SQL_TIME_LIMIT_MS):
            rows = conn.execute(sql, params)

        columns = [r[0] for r in rows.description]
        rows = list(rows)
        pks = pks_for_table(conn, table)
        info = ensure_build_metadata()
        total_rows = info[name]['tables'].get(table)
        return {
            'database': name,
            'table': table,
            'rows': rows,
            'total_rows': total_rows,
            'columns': columns,
            'primary_keys': pks,
        }, lambda: {
            'database_hash': hash,
            'row_link': lambda row: path_from_row_pks(row, pks),
        }


class RowView(BaseView):
    template = 'table.html'

    def data(self, request, name, hash, table, pk_path):
        conn = get_conn(name)
        pk_values = compound_pks_from_path(pk_path)
        pks = pks_for_table(conn, table)
        wheres = [
            '"{}"=?'.format(pk)
            for pk in pks
        ]
        sql = 'select * from "{}" where {}'.format(
            table, ' AND '.join(wheres)
        )
        rows = conn.execute(sql, pk_values)
        columns = [r[0] for r in rows.description]
        pks = pks_for_table(conn, table)
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


app.add_route(DatabaseView.as_view(), '/<db_name:[^/\.]+?><as_json:(.jsono?)?$>')
app.add_route(DatabaseDownload.as_view(), '/<db_name:[^/]+?><as_db:(\.db)$>')
app.add_route(TableView.as_view(), '/<db_name:[^/]+>/<table:[^/]+?><as_json:(.jsono?)?$>')
app.add_route(RowView.as_view(), '/<db_name:[^/]+>/<table:[^/]+?>/<pk_path:[^/]+?><as_json:(.jsono?)?$>')


def resolve_db_name(db_name, **kwargs):
    databases = ensure_build_metadata()
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


def pks_for_table(conn, table):
    rows = [
        row for row in conn.execute(
            'PRAGMA table_info("{}")'.format(table)
        ).fetchall()
        if row[-1]
    ]
    rows.sort(key=lambda row: row[-1])
    return [str(r[1]) for r in rows]


def path_from_row_pks(row, pks):
    if not pks:
        return ''
    bits = []
    for pk in pks:
        bits.append(
            urllib.parse.quote_plus(str(row[pk]))
        )
    return ','.join(bits)


def build_where_clause(args):
    sql_bits = []
    for key, values in args.items():
        if '__' in key:
            column, lookup = key.rsplit('__', 1)
        else:
            column = key
            lookup = 'exact'
        template = {
            'exact': '"{}" = ?',
            'contains': '"{}" like ?',
            'endswith': '"{}" like ?',
            'startswith': '"{}" like ?',
            'gt': '"{}" > ?',
            'gte': '"{}" >= ?',
            'lt': '"{}" < ?',
            'lte': '"{}" <= ?',
            'glob': '"{}" glob ?',
            'like': '"{}" like ?',
        }[lookup]
        value = values[0]
        value_convert = {
            'contains': lambda s: '%{}%'.format(s),
            'endswith': lambda s: '%{}'.format(s),
            'startswith': lambda s: '{}%'.format(s),
        }.get(lookup, lambda s: s)
        converted = value_convert(value)
        sql_bits.append(
            (template.format(column), converted)
        )
    sql_bits.sort(key=lambda p: p[0])
    where_clause = ' and '.join(p[0] for p in sql_bits)
    params = [p[1] for p in sql_bits]
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


if __name__ == '__main__':
    if '--build' in sys.argv:
        ensure_build_metadata(True)
    else:
        app.run(host="0.0.0.0", port=8006)
