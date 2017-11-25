from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from sanic.views import HTTPMethodView
from sanic.request import RequestParameters
from sanic_jinja2 import SanicJinja2
from jinja2 import FileSystemLoader
import re
import sqlite3
from pathlib import Path
from concurrent import futures
import asyncio
import threading
import urllib.parse
import json
import jinja2
import hashlib
import time
from .utils import (
    Filters,
    compound_pks_from_path,
    CustomJSONEncoder,
    detect_fts_sql,
    escape_css_string,
    escape_sqlite_table_name,
    filters_should_redirect,
    get_all_foreign_keys,
    InvalidSql,
    path_from_row_pks,
    path_with_added_args,
    path_with_ext,
    sqlite_timelimit,
    validate_sql_select,
)
from .version import __version__

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

    def redirect(self, request, path, forward_querystring=True):
        if request.query_string and '?' not in path and forward_querystring:
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
            if 'pk_path' in kwargs:
                should_redirect += '/' + kwargs['pk_path']
            if 'as_json' in kwargs:
                should_redirect += kwargs['as_json']
            if 'as_db' in kwargs:
                should_redirect += kwargs['as_db']
            return name, expected, should_redirect
        return name, expected, None

    def prepare_connection(self, conn):
        conn.row_factory = sqlite3.Row
        conn.text_factory = lambda x: str(x, 'utf-8', 'replace')
        for name, num_args, func in self.ds.sqlite_functions:
            conn.create_function(name, num_args, func)
        if self.ds.sqlite_extensions:
            conn.enable_load_extension(True)
            for extension in self.ds.sqlite_extensions:
                conn.execute("SELECT load_extension('{}')".format(extension))

    async def execute(self, db_name, sql, params=None, truncate=False, custom_time_limit=None):
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
                self.prepare_connection(conn)
                setattr(connections, db_name, conn)

            time_limit_ms = self.ds.sql_time_limit_ms
            if custom_time_limit and custom_time_limit < self.ds.sql_time_limit_ms:
                time_limit_ms = custom_time_limit

            with sqlite_timelimit(conn, time_limit_ms):
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
            response_or_template_contexts = await self.data(
                request, name, hash, **kwargs
            )
            if isinstance(response_or_template_contexts, response.HTTPResponse):
                return response_or_template_contexts
            else:
                data, extra_template_data = response_or_template_contexts
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
            extras = {}
            if callable(extra_template_data):
                extras = extra_template_data()
                if asyncio.iscoroutine(extras):
                    extras = await extras
            else:
                extras = extra_template_data
            context = {
                **data,
                **extras,
                **{
                    'url_json': path_with_ext(request, '.json'),
                    'url_jsono': path_with_ext(request, '.jsono'),
                    'metadata': self.ds.metadata,
                    'datasette_version': __version__,
                }
            }
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
            tables = [t for t in info['tables'].values() if not t['hidden']]
            hidden_tables = [t for t in info['tables'].values() if t['hidden']]
            database = {
                'name': key,
                'hash': info['hash'],
                'path': '{}-{}'.format(key, info['hash'][:7]),
                'tables_truncated': sorted(
                    tables,
                    key=lambda t: t['count'],
                    reverse=True
                )[:5],
                'tables_count': len(tables),
                'tables_more': len(tables) > 5,
                'table_rows': sum(t['count'] for t in tables),
                'hidden_table_rows': sum(t['count'] for t in hidden_tables),
                'hidden_tables_count': len(hidden_tables),
                'views_count': len(info['views']),
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
                datasette_version=__version__,
            )


async def favicon(request):
    return response.text('')


class DatabaseView(BaseView):
    template = 'database.html'
    re_named_parameter = re.compile(':([a-zA-Z0-9_]+)')

    async def data(self, request, name, hash):
        if request.args.get('sql'):
            return await self.custom_sql(request, name, hash)
        info = self.ds.inspect()[name]
        tables = list(info['tables'].values())
        tables.sort(key=lambda t: (t['hidden'], t['name']))
        return {
            'database': name,
            'tables': tables,
            'hidden_count': len([t for t in tables if t['hidden']]),
            'views': info['views'],
        }, {
            'database_hash': hash,
            'show_hidden': request.args.get('_show_hidden'),
        }

    async def custom_sql(self, request, name, hash):
        params = request.raw_args
        sql = params.pop('sql')
        validate_sql_select(sql)

        # Extract any :named parameters
        named_parameters = self.re_named_parameter.findall(sql)
        named_parameter_values = {
            named_parameter: params.get(named_parameter) or ''
            for named_parameter in named_parameters
        }

        # Set to blank string if missing from params
        for named_parameter in named_parameters:
            if named_parameter not in params:
                params[named_parameter] = ''

        extra_args = {}
        if params.get('_sql_time_limit_ms'):
            extra_args['custom_time_limit'] = int(params['_sql_time_limit_ms'])
        rows, truncated, description = await self.execute(
            name, sql, params, truncate=True, **extra_args
        )
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
            'named_parameter_values': named_parameter_values,
        }


class DatabaseDownload(BaseView):
    async def view_get(self, request, name, hash, **kwargs):
        filepath = self.ds.inspect()[name]['file']
        return await response.file_stream(
            filepath, headers={
                'Content-Disposition': 'attachment; filename="{}"'.format(filepath)
            }
        )


class RowTableShared(BaseView):
    async def make_display_rows(self, database, database_hash, table, rows, display_columns, pks, is_view, use_rowid):
        # Get fancy with foreign keys
        expanded = {}
        tables = self.ds.inspect()[database]['tables']
        table_info = tables.get(table) or {}
        if table_info and not is_view:
            foreign_keys = table_info['foreign_keys']['outgoing']
            for fk in foreign_keys:
                label_column = tables.get(fk['other_table'], {}).get('label_column')
                if not label_column:
                    # We only link cells to other tables with label columns defined
                    continue
                ids_to_lookup = set([row[fk['column']] for row in rows])
                sql = 'select "{other_column}", "{label_column}" from {other_table} where "{other_column}" in ({placeholders})'.format(
                    other_column=fk['other_column'],
                    label_column=label_column,
                    other_table=escape_sqlite_table_name(fk['other_table']),
                    placeholders=', '.join(['?'] * len(ids_to_lookup)),
                )
                try:
                    results = await self.execute(database, sql, list(set(ids_to_lookup)))
                except sqlite3.OperationalError:
                    # Probably hit the timelimit
                    pass
                else:
                    for id, value in results:
                        expanded[(fk['column'], id)] = (fk['other_table'], value)

        to_return = []
        for row in rows:
            cells = []
            # Unless we are a view, the first column is a link - either to the rowid
            # or to the simple or compound primary key
            if not is_view:
                display_value = jinja2.Markup(
                    '<a href="/{database}-{database_hash}/{table}/{flat_pks}">{flat_pks}</a>'.format(
                        database=database,
                        database_hash=database_hash,
                        table=urllib.parse.quote_plus(table),
                        flat_pks=path_from_row_pks(row, pks, use_rowid),
                    )
                )
                cells.append({
                    'column': 'rowid' if use_rowid else 'Link',
                    'value': display_value,
                })

            for value, column in zip(row, display_columns):
                if use_rowid and column == 'rowid':
                    # We already showed this in the linked first column
                    continue
                elif (column, value) in expanded:
                    other_table, label = expanded[(column, value)]
                    display_value = jinja2.Markup(
                        # TODO: Escape id/label/etc so no XSS here
                        '<a href="/{database}-{database_hash}/{table}/{id}">{label}</a>&nbsp;<em>{id}</em>'.format(
                            database=database,
                            database_hash=database_hash,
                            table=urllib.parse.quote_plus(other_table),
                            id=value,
                            label=label,
                        )
                    )
                elif value is None:
                    display_value = jinja2.Markup('&nbsp;')
                else:
                    display_value = str(value)
                cells.append({
                    'column': column,
                    'value': display_value,
                })
            to_return.append(cells)
        return to_return


class TableView(RowTableShared):
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

        # We roll our own query_string decoder because by default Sanic
        # drops anything with an empty value e.g. ?name__exact=
        args = RequestParameters(
            urllib.parse.parse_qs(request.query_string, keep_blank_values=True)
        )

        # Special args start with _ and do not contain a __
        # That's so if there is a column that starts with _
        # it can still be queried using ?_col__exact=blah
        special_args = {}
        special_args_lists = {}
        other_args = {}
        for key, value in args.items():
            if key.startswith('_') and '__' not in key:
                special_args[key] = value[0]
                special_args_lists[key] = value
            else:
                other_args[key] = value[0]

        # Handle ?_filter_column and redirect, if present
        redirect_params = filters_should_redirect(special_args)
        if redirect_params:
            return self.redirect(
                request,
                path_with_added_args(request, redirect_params),
                forward_querystring=False
            )

        filters = Filters(sorted(other_args.items()))
        where_clauses, params = filters.build_where_clauses()

        # _search support:
        fts_table = None
        fts_sql = detect_fts_sql(table)
        fts_rows = list(await self.execute(name, fts_sql))
        if fts_rows:
            fts_table = fts_rows[0][0]

        search = special_args.get('_search')
        search_description = None
        if search and fts_table:
            where_clauses.append(
                'rowid in (select rowid from [{fts_table}] where [{fts_table}] match :search)'.format(
                    fts_table=fts_table
                )
            )
            search_description = 'search matches "{}"'.format(search)
            params['search'] = search

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

        # _group_count=col1&_group_count=col2
        group_count = special_args_lists.get('_group_count') or []
        if group_count:
            count_sql = None
            sql = 'select {group_cols}, count(*) as "count" from {table_name} {where} group by {group_cols} order by "count" desc limit 100'.format(
                group_cols=', '.join('"{}"'.format(group_count_col) for group_count_col in group_count),
                table_name=escape_sqlite_table_name(table),
                where=where_clause,
            )
            is_view = True
        else:
            count_sql = 'select count(*) from {table_name} {where}'.format(
                table_name=escape_sqlite_table_name(table),
                where=where_clause,
            )
            sql = 'select {select} from {table_name} {where}{order_by}limit {limit}{offset}'.format(
                select=select,
                table_name=escape_sqlite_table_name(table),
                where=where_clause,
                order_by=order_by,
                limit=self.page_size + 1,
                offset=offset,
            )

        extra_args = {}
        if request.raw_args.get('_sql_time_limit_ms'):
            extra_args['custom_time_limit'] = int(request.raw_args['_sql_time_limit_ms'])

        rows, truncated, description = await self.execute(
            name, sql, params, truncate=True, **extra_args
        )

        columns = [r[0] for r in description]
        rows = list(rows)

        display_columns = columns
        if not use_rowid and not is_view:
            display_columns = ['Link'] + display_columns

        info = self.ds.inspect()
        table_rows = None
        if not is_view:
            table_rows = info[name]['tables'][table]['count']

        # Pagination next link
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

        # Number of filtered rows in whole set:
        filtered_table_rows = None
        if not where_clauses and not is_view:
            # Use the pre-calculated total
            filtered_table_rows = table_rows
        elif not truncated and len(rows) < self.page_size:
            filtered_table_rows = len(rows)
        else:
            # Attempt a full count, if we can do it in < X ms
            if count_sql:
                try:
                    count_rows = list(await self.execute(name, count_sql, params))
                    filtered_table_rows = count_rows[0][0]
                except sqlite3.OperationalError:
                    # Almost certainly hit the timeout
                    pass

        # human_filter_description combines filters AND search, if provided
        human_description = filters.human_description(extra=search_description)

        async def extra_template():
            return {
                'database_hash': hash,
                'human_filter_description': human_description,
                'supports_search': bool(fts_table),
                'search': search or '',
                'use_rowid': use_rowid,
                'filters': filters,
                'display_columns': display_columns,
                'display_rows': await self.make_display_rows(name, hash, table, rows, display_columns, pks, is_view, use_rowid),
            }

        return {
            'database': name,
            'table': table,
            'is_view': is_view,
            'view_definition': view_definition,
            'table_definition': table_definition,
            'rows': rows[:self.page_size],
            'truncated': truncated,
            'table_rows': table_rows,
            'filtered_table_rows': filtered_table_rows,
            'columns': columns,
            'primary_keys': pks,
            'query': {
                'sql': sql,
                'params': params,
            },
            'next': next_value and str(next_value) or None,
            'next_url': next_url,
        }, extra_template


class RowView(RowTableShared):
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
        # rows, truncated, description = await self.execute(name, sql, params, truncate=True)
        rows, truncated, description = await self.execute(name, sql, params, truncate=True)
        columns = [r[0] for r in description]
        rows = list(rows)
        if not rows:
            raise NotFound('Record not found: {}'.format(pk_values))

        async def template_data():
            return {
                'database_hash': hash,
                'foreign_key_tables': await self.foreign_key_tables(name, table, pk_values),
                'display_columns': columns,
                'display_rows': await self.make_display_rows(name, hash, table, rows, columns, pks, False, use_rowid),
            }

        data = {
            'database': name,
            'table': table,
            'rows': rows,
            'columns': columns,
            'primary_keys': pks,
            'primary_key_values': pk_values,
        }

        if 'foreign_key_tables' in (request.raw_args.get('_extras') or '').split(','):
            data['foreign_key_tables'] = await self.foreign_key_tables(name, table, pk_values)

        return data, template_data

    async def foreign_key_tables(self, name, table, pk_values):
        if len(pk_values) != 1:
            return []
        table_info = self.ds.inspect()[name]['tables'].get(table)
        if not table:
            return []
        foreign_keys = table_info['foreign_keys']['incoming']
        sql = 'select ' + ', '.join([
            '(select count(*) from {table} where "{column}"=:id)'.format(
                table=escape_sqlite_table_name(fk['other_table']),
                column=fk['other_column'],
            )
            for fk in foreign_keys
        ])
        try:
            rows = list(await self.execute(name, sql, {'id': pk_values[0]}))
        except sqlite3.OperationalError:
            # Almost certainly hit the timeout
            return []
        foreign_table_counts = dict(
            zip(
                [(fk['other_table'], fk['other_column']) for fk in foreign_keys],
                list(rows[0]),
            )
        )
        foreign_key_tables = []
        for fk in foreign_keys:
            count = foreign_table_counts.get((fk['other_table'], fk['other_column'])) or 0
            foreign_key_tables.append({**fk, **{'count': count}})
        return foreign_key_tables


class Datasette:
    def __init__(
            self, files, num_threads=3, cache_headers=True, page_size=100,
            max_returned_rows=1000, sql_time_limit_ms=1000, cors=False,
            inspect_data=None, metadata=None, sqlite_extensions=None):
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
        self.sqlite_functions = []
        self.sqlite_extensions = sqlite_extensions or []

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
                views = []
                with sqlite3.connect('file:{}?immutable=1'.format(path), uri=True) as conn:
                    conn.row_factory = sqlite3.Row
                    table_names = [
                        r['name']
                        for r in conn.execute('select * from sqlite_master where type="table"')
                    ]
                    views = [v[0] for v in conn.execute('select name from sqlite_master where type = "view"')]
                    for table in table_names:
                        count = conn.execute(
                            'select count(*) from {}'.format(escape_sqlite_table_name(table))
                        ).fetchone()[0]
                        label_column = None
                        # If table has two columns, one of which is ID, then label_column is the other one
                        column_names = [r[1] for r in conn.execute(
                            'PRAGMA table_info({});'.format(escape_sqlite_table_name(table))
                        ).fetchall()]
                        if column_names and len(column_names) == 2 and 'id' in column_names:
                            label_column = [c for c in column_names if c != 'id'][0]
                        tables[table] = {
                            'name': table,
                            'columns': column_names,
                            'count': count,
                            'label_column': label_column,
                            'hidden': False,
                        }

                    foreign_keys = get_all_foreign_keys(conn)
                    for table, info in foreign_keys.items():
                        tables[table]['foreign_keys'] = info

                    # Mark tables 'hidden' if they relate to FTS virtual tables
                    fts_tables = [
                        r['name']
                        for r in conn.execute(
                            '''
                                select name from sqlite_master
                                where rootpage = 0
                                and sql like '%VIRTUAL TABLE%USING FTS%'
                            '''
                        )
                    ]
                    for t in tables.keys():
                        for fts_table in fts_tables:
                            if t == fts_table or t.startswith(fts_table):
                                tables[t]['hidden'] = True
                                continue

                self._inspect[name] = {
                    'hash': m.hexdigest(),
                    'file': str(path),
                    'tables': tables,
                    'views': views,

                }
        return self._inspect

    def app(self):
        app = Sanic(__name__)
        self.jinja = SanicJinja2(
            app,
            loader=FileSystemLoader([
                str(app_root / 'datasette' / 'templates')
            ]),
            autoescape=True,
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
