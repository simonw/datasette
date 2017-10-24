from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from sanic_jinja2 import SanicJinja2
import sqlite3
from pathlib import Path
from functools import wraps
import json
import hashlib

app_root = Path(__file__).parent

BUILD_METADATA = 'build-metadata.json'
DB_GLOBS = ('*.db', '*.sqlite', '*.sqlite3')
HASH_BLOCK_SIZE = 1024 * 1024

conns = {}


def get_conn(name):
    if name not in conns:
        info = ensure_build_metadata()[name]
        conns[name] = sqlite3.connect(
            'file:{}?immutable=1'.format(info['file']),
            uri=True
        )
        conns[name].row_factory = sqlite3.Row
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


app = Sanic(__name__)
jinja = SanicJinja2(app)


def sqlerrors(fn):
    @wraps(fn)
    async def inner(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            return response.json({
                'ok': False,
                'error': str(e),
            })
    return inner


@app.route('/')
@sqlerrors
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


@app.route('/<db_name:[^/]+$>')
@sqlerrors
async def database(request, db_name):
    name, hash, should_redirect = resolve_db_name(db_name)
    if should_redirect:
        return response.redirect(should_redirect)
    conn = get_conn(name)
    rows = conn.execute('select * from sqlite_master')
    headers = [r[0] for r in rows.description]
    return jinja.render(
        'database.html',
        request,
        database=name,
        database_hash=hash,
        headers=headers,
        rows=rows,
    )


@app.route('/<db_name:[^/]+>/<table:[^/]+$>')
@sqlerrors
async def table(request, db_name, table):
    # The name should have the hash - if it
    # does not, serve a redirect
    name, hash, should_redirect = resolve_db_name(db_name)
    if should_redirect:
        return response.redirect(should_redirect + '/' + table)
    conn = get_conn(name)
    rows = conn.execute('select * from {} limit 20'.format(table))
    headers = [r[0] for r in rows.description]
    return jinja.render(
        'table.html',
        request,
        database=name,
        database_hash=hash,
        table=table,
        headers=headers,
        rows=rows,
    )


def resolve_db_name(db_name):
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
        raise NotFound()
    expected = info['hash'][:7]
    if expected != hash:
        return name, expected, '/{}-{}'.format(
            name, expected,
        )
    return name, expected, None


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8006)
