from sanic import Sanic
from sanic import response
from sanic_jinja2 import SanicJinja2
import sqlite3
from functools import wraps
import json

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
