from sanic import Sanic
from sanic import response
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

#conn = sqlite3.connect('file:flights.db?immutable=1', uri=True)
conn = sqlite3.connect('file:northwind.db?immutable=1', uri=True)
conn.row_factory = sqlite3.Row


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
    sql = sql or request.args.get('sql', '')
    if not sql:
        sql = 'select * from sqlite_master'
    rows = conn.execute(sql)
    headers = [r[0] for r in rows.description]
    return jinja.render('index.html', request,
        headers=headers,
        rows=list(rows),
        metadata=json.dumps(ensure_build_metadata(True), indent=2)
    )


@app.route('/<table:[a-zA-Z0-9].*>.json')
@sqlerrors
async def table_json(request, table):
    sql = 'select * from {} limit 20'.format(table)
    return response.json([
        dict(r) for r in conn.execute(sql)
    ])


@app.route('/<table:[a-zA-Z0-9].*>')
@sqlerrors
async def table(request, table):
    sql = 'select * from {} limit 20'.format(table)
    return await index(request, sql)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8006)
