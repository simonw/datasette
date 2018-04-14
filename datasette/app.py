from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from sanic.views import HTTPMethodView
from sanic.request import RequestParameters
from jinja2 import Environment, FileSystemLoader, ChoiceLoader, PrefixLoader
import re
import sqlite3
from pathlib import Path
from concurrent import futures
import asyncio
import os
import threading
import urllib.parse
import json
import jinja2
import hashlib
import time
import pint
import traceback
from .utils import (
    Filters,
    CustomJSONEncoder,
    compound_keys_after_sql,
    detect_fts_sql,
    detect_spatialite,
    escape_css_string,
    escape_sqlite,
    filters_should_redirect,
    get_all_foreign_keys,
    is_url,
    InvalidSql,
    path_from_row_pks,
    path_with_added_args,
    path_with_ext,
    sqlite_timelimit,
    to_css_class,
    urlsafe_components,
    validate_sql_select,
)
from .version import __version__

app_root = Path(__file__).parent.parent

HASH_BLOCK_SIZE = 1024 * 1024
HASH_LENGTH = 7

connections = threading.local()
ureg = pint.UnitRegistry()


class DatasetteError(Exception):
    def __init__(self, message, title=None, error_dict=None, status=500, template=None):
        self.message = message
        self.title = title
        self.error_dict = error_dict or {}
        self.status = status


class RenderMixin(HTTPMethodView):
    def render(self, templates, **context):
        template = self.jinja_env.select_template(templates)
        select_templates = ['{}{}'.format(
            '*' if template_name == template.name else '',
            template_name
        ) for template_name in templates]
        return response.html(
            template.render({
                **context, **{
                    'app_css_hash': self.ds.app_css_hash(),
                    'select_templates': select_templates,
                }
            })
        )


class BaseView(RenderMixin):
    re_named_parameter = re.compile(':([a-zA-Z0-9_]+)')

    def __init__(self, datasette):
        self.ds = datasette
        self.files = datasette.files
        self.jinja_env = datasette.jinja_env
        self.executor = datasette.executor
        self.page_size = datasette.page_size
        self.max_returned_rows = datasette.max_returned_rows

    def table_metadata(self, database, table):
        "Fetch table-specific metadata."
        return self.ds.metadata.get(
            'databases', {}
        ).get(database, {}).get('tables', {}).get(table, {})

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
        expected = info['hash'][:HASH_LENGTH]
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
                self.ds.prepare_connection(conn)
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
                except Exception as e:
                    print('ERROR: conn={}, sql = {}, params = {}: {}'.format(
                        conn, repr(sql), params, e
                    ))
                    raise
            if truncate:
                return rows, truncated, cursor.description
            else:
                return rows

        return await asyncio.get_event_loop().run_in_executor(
            self.executor, sql_operation_in_thread
        )

    def get_templates(self, database, table=None):
        assert NotImplemented

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
        status_code = 200
        templates = []
        try:
            response_or_template_contexts = await self.data(
                request, name, hash, **kwargs
            )
            if isinstance(response_or_template_contexts, response.HTTPResponse):
                return response_or_template_contexts
            else:
                data, extra_template_data, templates = response_or_template_contexts
        except (sqlite3.OperationalError, InvalidSql, DatasetteError) as e:
            raise DatasetteError(str(e), title='Invalid SQL', status=400)
        except (sqlite3.OperationalError) as e:
            raise DatasetteError(str(e))
        except DatasetteError:
            raise
        end = time.time()
        data['query_ms'] = (end - start) * 1000
        for key in ('source', 'source_url', 'license', 'license_url'):
            value = self.ds.metadata.get(key)
            if value:
                data[key] = value
        if as_json:
            # Special case for .jsono extension - redirect to _shape=objects
            if as_json == '.jsono':
                return self.redirect(
                    request,
                    path_with_added_args(
                        request,
                        {'_shape': 'objects'},
                        path=request.path.rsplit('.jsono', 1)[0] + '.json'
                    ),
                    forward_querystring=False
                )
            # Deal with the _shape option
            shape = request.args.get('_shape', 'lists')
            if shape in ('objects', 'object'):
                columns = data.get('columns')
                rows = data.get('rows')
                if rows and columns:
                    data['rows'] = [
                        dict(zip(columns, row))
                        for row in rows
                    ]
                if shape == 'object':
                    error = None
                    if 'primary_keys' not in data:
                        error = '_shape=object is only available on tables'
                    else:
                        pks = data['primary_keys']
                        if not pks:
                            error = '_shape=object not available for tables with no primary keys'
                        else:
                            object_rows = {}
                            for row in data['rows']:
                                pk_string = path_from_row_pks(row, pks, not pks)
                                object_rows[pk_string] = row
                            data['rows'] = object_rows
                    if error:
                        data = {
                            'ok': False,
                            'error': error,
                            'database': name,
                            'database_hash': hash,
                        }

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
                    'extra_css_urls': self.ds.extra_css_urls(),
                    'extra_js_urls': self.ds.extra_js_urls(),
                    'datasette_version': __version__,
                }
            }
            if 'metadata' not in context:
                context['metadata'] = self.ds.metadata
            r = self.render(
                templates,
                **context,
            )
            r.status = status_code
        # Set far-future cache expiry
        if self.ds.cache_headers:
            r.headers['Cache-Control'] = 'max-age={}'.format(
                365 * 24 * 60 * 60
            )
        return r

    async def custom_sql(self, request, name, hash, sql, editable=True, canned_query=None):
        params = request.raw_args
        if 'sql' in params:
            params.pop('sql')
        if '_shape' in params:
            params.pop('_shape')
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

        templates = ['query-{}.html'.format(to_css_class(name)), 'query.html']
        if canned_query:
            templates.insert(0, 'query-{}-{}.html'.format(
                to_css_class(name), to_css_class(canned_query)
            ))

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
            'editable': editable,
            'canned_query': canned_query,
        }, templates


class IndexView(RenderMixin):
    def __init__(self, datasette):
        self.ds = datasette
        self.files = datasette.files
        self.jinja_env = datasette.jinja_env
        self.executor = datasette.executor

    async def get(self, request, as_json):
        databases = []
        for key, info in sorted(self.ds.inspect().items()):
            tables = [t for t in info['tables'].values() if not t['hidden']]
            hidden_tables = [t for t in info['tables'].values() if t['hidden']]
            database = {
                'name': key,
                'hash': info['hash'],
                'path': '{}-{}'.format(key, info['hash'][:HASH_LENGTH]),
                'tables_truncated': sorted(
                    tables,
                    key=lambda t: t['count'],
                    reverse=True
                )[:5],
                'tables_count': len(tables),
                'tables_more': len(tables) > 5,
                'table_rows_sum': sum(t['count'] for t in tables),
                'hidden_table_rows_sum': sum(t['count'] for t in hidden_tables),
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
            return self.render(
                ['index.html'],
                databases=databases,
                metadata=self.ds.metadata,
                datasette_version=__version__,
                extra_css_urls=self.ds.extra_css_urls(),
                extra_js_urls=self.ds.extra_js_urls(),
            )


async def favicon(request):
    return response.text('')


class DatabaseView(BaseView):
    async def data(self, request, name, hash):
        if request.args.get('sql'):
            sql = request.raw_args.pop('sql')
            validate_sql_select(sql)
            return await self.custom_sql(request, name, hash, sql)
        info = self.ds.inspect()[name]
        metadata = self.ds.metadata.get('databases', {}).get(name, {})
        self.ds.update_with_inherited_metadata(metadata)
        tables = list(info['tables'].values())
        tables.sort(key=lambda t: (t['hidden'], t['name']))
        return {
            'database': name,
            'tables': tables,
            'hidden_count': len([t for t in tables if t['hidden']]),
            'views': info['views'],
            'queries': [{
                'name': query_name,
                'sql': query_sql,
            } for query_name, query_sql in (metadata.get('queries') or {}).items()],
        }, {
            'database_hash': hash,
            'show_hidden': request.args.get('_show_hidden'),
            'editable': True,
            'metadata': metadata,
        }, ('database-{}.html'.format(to_css_class(name)), 'database.html')


class DatabaseDownload(BaseView):
    async def view_get(self, request, name, hash, **kwargs):
        filepath = self.ds.inspect()[name]['file']
        return await response.file_stream(
            filepath, headers={
                'Content-Disposition': 'attachment; filename="{}"'.format(filepath)
            }
        )


class RowTableShared(BaseView):
    def sortable_columns_for_table(self, name, table, use_rowid):
        table_metadata = self.table_metadata(name, table)
        if 'sortable_columns' in table_metadata:
            sortable_columns = set(table_metadata['sortable_columns'])
        else:
            table_info = self.ds.inspect()[name]['tables'].get(table) or {}
            sortable_columns = set(table_info.get('columns', []))
        if use_rowid:
            sortable_columns.add('rowid')
        return sortable_columns

    async def display_columns_and_rows(self, database, table, description, rows, link_column=False, expand_foreign_keys=True):
        "Returns columns, rows for specified table - including fancy foreign key treatment"
        table_metadata = self.table_metadata(database, table)
        info = self.ds.inspect()[database]
        sortable_columns = self.sortable_columns_for_table(database, table, True)
        columns = [{
            'name': r[0],
            'sortable': r[0] in sortable_columns,
        } for r in description]
        tables = info['tables']
        table_info = tables.get(table) or {}
        pks = table_info.get('primary_keys') or []

        # Prefetch foreign key resolutions for later expansion:
        fks = {}
        labeled_fks = {}
        if table_info and expand_foreign_keys:
            foreign_keys = table_info['foreign_keys']['outgoing']
            for fk in foreign_keys:
                label_column = tables.get(fk['other_table'], {}).get('label_column')
                if not label_column:
                    # No label for this FK
                    fks[fk['column']] = fk['other_table']
                    continue
                ids_to_lookup = set([row[fk['column']] for row in rows])
                sql = 'select "{other_column}", "{label_column}" from {other_table} where "{other_column}" in ({placeholders})'.format(
                    other_column=fk['other_column'],
                    label_column=label_column,
                    other_table=escape_sqlite(fk['other_table']),
                    placeholders=', '.join(['?'] * len(ids_to_lookup)),
                )
                try:
                    results = await self.execute(database, sql, list(set(ids_to_lookup)))
                except sqlite3.OperationalError:
                    # Probably hit the timelimit
                    pass
                else:
                    for id, value in results:
                        labeled_fks[(fk['column'], id)] = (fk['other_table'], value)

        cell_rows = []
        for row in rows:
            cells = []
            # Unless we are a view, the first column is a link - either to the rowid
            # or to the simple or compound primary key
            if link_column:
                cells.append({
                    'column': 'Link',
                    'value': jinja2.Markup(
                        '<a href="/{database}/{table}/{flat_pks}">{flat_pks}</a>'.format(
                            database=database,
                            table=urllib.parse.quote_plus(table),
                            flat_pks=path_from_row_pks(row, pks, not pks),
                        )
                    ),
                })
            for value, column_dict in zip(row, columns):
                column = column_dict['name']
                if (column, value) in labeled_fks:
                    other_table, label = labeled_fks[(column, value)]
                    display_value = jinja2.Markup(
                        '<a href="/{database}/{table}/{link_id}">{label}</a>&nbsp;<em>{id}</em>'.format(
                            database=database,
                            table=urllib.parse.quote_plus(other_table),
                            link_id=urllib.parse.quote_plus(str(value)),
                            id=str(jinja2.escape(value)),
                            label=str(jinja2.escape(label)),
                        )
                    )
                elif column in fks:
                    display_value = jinja2.Markup(
                        '<a href="/{database}/{table}/{link_id}">{id}</a>'.format(
                            database=database,
                            table=urllib.parse.quote_plus(fks[column]),
                            link_id=urllib.parse.quote_plus(str(value)),
                            id=str(jinja2.escape(value))))
                elif value is None:
                    display_value = jinja2.Markup('&nbsp;')
                elif is_url(str(value).strip()):
                    display_value = jinja2.Markup(
                        '<a href="{url}">{url}</a>'.format(
                            url=jinja2.escape(value.strip())
                        )
                    )
                else:
                    if column in table_metadata.get('units', {}) and value != '':
                        # Interpret units using pint
                        value = value * ureg(table_metadata['units'][column])
                        # Pint uses floating point which sometimes introduces errors in the compact
                        # representation, which we have to round off to avoid ugliness. In the vast
                        # majority of cases this rounding will be inconsequential. I hope.
                        value = round(value.to_compact(), 6)
                        display_value = jinja2.Markup('{:~P}'.format(value).replace(' ', '&nbsp;'))
                    else:
                        display_value = str(value)
                cells.append({
                    'column': column,
                    'value': display_value,
                })
            cell_rows.append(cells)

        if link_column:
            columns = [{
                'name': 'Link',
                'sortable': False,
            }] + columns
        return columns, cell_rows


class TableView(RowTableShared):
    async def data(self, request, name, hash, table):
        table = urllib.parse.unquote_plus(table)
        canned_query = self.ds.get_canned_query(name, table)
        if canned_query is not None:
            return await self.custom_sql(request, name, hash, canned_query['sql'], editable=False, canned_query=table)
        is_view = bool(list(await self.execute(
            name,
            "SELECT count(*) from sqlite_master WHERE type = 'view' and name=:n",
            {'n': table}
        ))[0][0])
        view_definition = None
        table_definition = None
        if is_view:
            view_definition = list(await self.execute(
                name,
                'select sql from sqlite_master where name = :n and type="view"',
                {'n': table}
            ))[0][0]
        else:
            table_definition_rows = list(await self.execute(
                name,
                'select sql from sqlite_master where name = :n and type="table"',
                {'n': table}
            ))
            if not table_definition_rows:
                raise NotFound('Table not found: {}'.format(table))
            table_definition = table_definition_rows[0][0]
        info = self.ds.inspect()
        table_info = info[name]['tables'].get(table) or {}
        pks = table_info.get('primary_keys') or []
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

        # Spot ?_sort_by_desc and redirect to _sort_desc=(_sort)
        if '_sort_by_desc' in special_args:
            return self.redirect(
                request,
                path_with_added_args(request, {
                    '_sort_desc': special_args.get('_sort'),
                    '_sort_by_desc': None,
                    '_sort': None,
                }),
                forward_querystring=False
            )

        units = self.table_metadata(name, table).get('units', {})

        filters = Filters(sorted(other_args.items()), units, ureg)
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

        table_rows_count = None
        sortable_columns = set()
        if not is_view:
            table_rows_count = table_info['count']
            sortable_columns = self.sortable_columns_for_table(name, table, use_rowid)

        # Allow for custom sort order
        sort = special_args.get('_sort')
        if sort:
            if sort not in sortable_columns:
                raise DatasetteError('Cannot sort table by {}'.format(sort))
            order_by = escape_sqlite(sort)
        sort_desc = special_args.get('_sort_desc')
        if sort_desc:
            if sort_desc not in sortable_columns:
                raise DatasetteError('Cannot sort table by {}'.format(sort_desc))
            if sort:
                raise DatasetteError('Cannot use _sort and _sort_desc at the same time')
            order_by = '{} desc'.format(escape_sqlite(sort_desc))

        count_sql = 'select count(*) from {table_name} {where}'.format(
            table_name=escape_sqlite(table),
            where=(
                'where {} '.format(' and '.join(where_clauses))
            ) if where_clauses else '',
        )

        # _group_count=col1&_group_count=col2
        group_count = special_args_lists.get('_group_count') or []
        if group_count:
            sql = 'select {group_cols}, count(*) as "count" from {table_name} {where} group by {group_cols} order by "count" desc limit 100'.format(
                group_cols=', '.join('"{}"'.format(group_count_col) for group_count_col in group_count),
                table_name=escape_sqlite(table),
                where=(
                    'where {} '.format(' and '.join(where_clauses))
                ) if where_clauses else '',
            )
            return await self.custom_sql(request, name, hash, sql, editable=True)

        _next = special_args.get('_next')
        offset = ''
        if _next:
            if is_view:
                # _next is an offset
                offset = ' offset {}'.format(int(_next))
            else:
                components = urlsafe_components(_next)
                # If a sort order is applied, the first of these is the sort value
                if sort or sort_desc:
                    sort_value = components[0]
                    components = components[1:]
                    print('sort_varlue = {}, components = {}'.format(
                        sort_value, components
                    ))

                # Figure out the SQL for next-based-on-primary-key first
                next_by_pk_clauses = []
                if use_rowid:
                    next_by_pk_clauses.append(
                        'rowid > :p{}'.format(
                            len(params),
                        )
                    )
                    params['p{}'.format(len(params))] = components[0]
                else:
                    # Apply the tie-breaker based on primary keys
                    if len(components) == len(pks):
                        param_len = len(params)
                        next_by_pk_clauses.append(compound_keys_after_sql(pks, param_len))
                        for i, pk_value in enumerate(components):
                            params['p{}'.format(param_len + i)] = pk_value

                # Now add the sort SQL, which may incorporate next_by_pk_clauses
                if sort or sort_desc:
                    where_clauses.append(
                        '({column} {op} :p{p} or ({column} = :p{p} and {next_clauses}))'.format(
                            column=escape_sqlite(sort or sort_desc),
                            op='>' if sort else '<',
                            p=len(params),
                            next_clauses=' and '.join(next_by_pk_clauses),
                        )
                    )
                    params['p{}'.format(len(params))] = sort_value
                else:
                    where_clauses.extend(next_by_pk_clauses)

        where_clause = ''
        if where_clauses:
            where_clause = 'where {} '.format(' and '.join(where_clauses))

        if order_by:
            order_by = 'order by {} '.format(order_by)

        # _group_count=col1&_group_count=col2
        group_count = special_args_lists.get('_group_count') or []
        if group_count:
            sql = 'select {group_cols}, count(*) as "count" from {table_name} {where} group by {group_cols} order by "count" desc limit 100'.format(
                group_cols=', '.join('"{}"'.format(group_count_col) for group_count_col in group_count),
                table_name=escape_sqlite(table),
                where=where_clause,
            )
            return await self.custom_sql(request, name, hash, sql, editable=True)

        sql = 'select {select} from {table_name} {where}{order_by}limit {limit}{offset}'.format(
            select=select,
            table_name=escape_sqlite(table),
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

        filter_columns = columns[:]
        if use_rowid and filter_columns[0] == 'rowid':
            filter_columns = filter_columns[1:]

        # Pagination next link
        next_value = None
        next_url = None
        if len(rows) > self.page_size:
            if is_view:
                next_value = int(_next or 0) + self.page_size
            else:
                next_value = path_from_row_pks(rows[-2], pks, use_rowid)
            # If there's a sort or sort_desc, add that value as a prefix
            if (sort or sort_desc) and not is_view:
                prefix = str(rows[-2][sort or sort_desc])
                next_value = '{},{}'.format(
                    urllib.parse.quote_plus(prefix), next_value
                )
                added_args = {
                    '_next': next_value,
                }
                if sort:
                    added_args['_sort'] = sort
                else:
                    added_args['_sort_desc'] = sort_desc
            else:
                added_args = {
                    '_next': next_value,
                }
            next_url = urllib.parse.urljoin(request.url, path_with_added_args(
                request, added_args
            ))
            rows = rows[:self.page_size]

        # Number of filtered rows in whole set:
        filtered_table_rows_count = None
        if count_sql:
            try:
                count_rows = list(await self.execute(name, count_sql, params))
                filtered_table_rows_count = count_rows[0][0]
            except sqlite3.OperationalError:
                # Almost certainly hit the timeout
                pass

        # human_description_en combines filters AND search, if provided
        human_description_en = filters.human_description_en(extra=search_description)

        if sort or sort_desc:
            sorted_by = 'sorted by {}{}'.format(
                (sort or sort_desc),
                ' descending' if sort_desc else '',
            )
            human_description_en = ' '.join([
                b for b in [human_description_en, sorted_by] if b
            ])

        async def extra_template():
            display_columns, display_rows = await self.display_columns_and_rows(
                name, table, description, rows, link_column=not is_view, expand_foreign_keys=True
            )
            metadata = self.ds.metadata.get(
                'databases', {}
            ).get(name, {}).get('tables', {}).get(table, {})
            self.ds.update_with_inherited_metadata(metadata)
            return {
                'database_hash': hash,
                'supports_search': bool(fts_table),
                'search': search or '',
                'use_rowid': use_rowid,
                'filters': filters,
                'display_columns': display_columns,
                'filter_columns': filter_columns,
                'display_rows': display_rows,
                'is_sortable': any(c['sortable'] for c in display_columns),
                'path_with_added_args': path_with_added_args,
                'request': request,
                'sort': sort,
                'sort_desc': sort_desc,
                'disable_sort': is_view,
                'custom_rows_and_columns_templates': [
                    '_rows_and_columns-{}-{}.html'.format(to_css_class(name), to_css_class(table)),
                    '_rows_and_columns-table-{}-{}.html'.format(to_css_class(name), to_css_class(table)),
                    '_rows_and_columns.html',
                ],
                'metadata': metadata,
            }

        return {
            'database': name,
            'table': table,
            'is_view': is_view,
            'view_definition': view_definition,
            'table_definition': table_definition,
            'human_description_en': human_description_en,
            'rows': rows[:self.page_size],
            'truncated': truncated,
            'table_rows_count': table_rows_count,
            'filtered_table_rows_count': filtered_table_rows_count,
            'columns': columns,
            'primary_keys': pks,
            'units': units,
            'query': {
                'sql': sql,
                'params': params,
            },
            'next': next_value and str(next_value) or None,
            'next_url': next_url,
        }, extra_template, (
            'table-{}-{}.html'.format(to_css_class(name), to_css_class(table)),
            'table.html'
        )


class RowView(RowTableShared):
    async def data(self, request, name, hash, table, pk_path):
        table = urllib.parse.unquote_plus(table)
        pk_values = urlsafe_components(pk_path)
        info = self.ds.inspect()[name]
        table_info = info['tables'].get(table) or {}
        pks = table_info.get('primary_keys') or []
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
            display_columns, display_rows = await self.display_columns_and_rows(
                name, table, description, rows, link_column=False, expand_foreign_keys=True
            )
            for column in display_columns:
                column['sortable'] = False
            return {
                'database_hash': hash,
                'foreign_key_tables': await self.foreign_key_tables(name, table, pk_values),
                'display_columns': display_columns,
                'display_rows': display_rows,
                'custom_rows_and_columns_templates': [
                    '_rows_and_columns-{}-{}.html'.format(to_css_class(name), to_css_class(table)),
                    '_rows_and_columns-row-{}-{}.html'.format(to_css_class(name), to_css_class(table)),
                    '_rows_and_columns.html',
                ],
                'metadata': self.ds.metadata.get(
                    'databases', {}
                ).get(name, {}).get('tables', {}).get(table, {}),
            }

        data = {
            'database': name,
            'table': table,
            'rows': rows,
            'columns': columns,
            'primary_keys': pks,
            'primary_key_values': pk_values,
            'units': self.table_metadata(name, table).get('units', {})
        }

        if 'foreign_key_tables' in (request.raw_args.get('_extras') or '').split(','):
            data['foreign_key_tables'] = await self.foreign_key_tables(name, table, pk_values)

        return data, template_data, (
            'row-{}-{}.html'.format(to_css_class(name), to_css_class(table)),
            'row.html'
        )

    async def foreign_key_tables(self, name, table, pk_values):
        if len(pk_values) != 1:
            return []
        table_info = self.ds.inspect()[name]['tables'].get(table)
        if not table_info:
            return []
        foreign_keys = table_info['foreign_keys']['incoming']
        if len(foreign_keys) == 0:
            return []

        sql = 'select ' + ', '.join([
            '(select count(*) from {table} where "{column}"=:id)'.format(
                table=escape_sqlite(fk['other_table']),
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
            inspect_data=None, metadata=None, sqlite_extensions=None,
            template_dir=None, static_mounts=None):
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
        self.template_dir = template_dir
        self.static_mounts = static_mounts or []

    def app_css_hash(self):
        if not hasattr(self, '_app_css_hash'):
            self._app_css_hash = hashlib.sha1(
                open(os.path.join(str(app_root), 'datasette/static/app.css')).read().encode('utf8')
            ).hexdigest()[:6]
        return self._app_css_hash

    def get_canned_query(self, database_name, query_name):
        query = self.metadata.get(
            'databases', {}
        ).get(
            database_name, {}
        ).get(
            'queries', {}
        ).get(query_name)
        if query:
            return {
                'name': query_name,
                'sql': query,
            }

    def asset_urls(self, key):
        for url_or_dict in (self.metadata.get(key) or []):
            if isinstance(url_or_dict, dict):
                yield {
                    'url': url_or_dict['url'],
                    'sri': url_or_dict.get('sri'),
                }
            else:
                yield {
                    'url': url_or_dict,
                }

    def extra_css_urls(self):
        return self.asset_urls('extra_css_urls')

    def extra_js_urls(self):
        return self.asset_urls('extra_js_urls')

    def update_with_inherited_metadata(self, metadata):
        # Fills in source/license with defaults, if available
        metadata.update({
            'source': metadata.get('source') or self.metadata.get('source'),
            'source_url': metadata.get('source_url') or self.metadata.get('source_url'),
            'license': metadata.get('license') or self.metadata.get('license'),
            'license_url': metadata.get('license_url') or self.metadata.get('license_url'),
        })

    def prepare_connection(self, conn):
        conn.row_factory = sqlite3.Row
        conn.text_factory = lambda x: str(x, 'utf-8', 'replace')
        for name, num_args, func in self.sqlite_functions:
            conn.create_function(name, num_args, func)
        if self.sqlite_extensions:
            conn.enable_load_extension(True)
            for extension in self.sqlite_extensions:
                conn.execute("SELECT load_extension('{}')".format(extension))

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
                    self.prepare_connection(conn)
                    table_names = [
                        r['name']
                        for r in conn.execute('select * from sqlite_master where type="table"')
                    ]
                    views = [v[0] for v in conn.execute('select name from sqlite_master where type = "view"')]
                    for table in table_names:
                        try:
                            count = conn.execute(
                                'select count(*) from {}'.format(escape_sqlite(table))
                            ).fetchone()[0]
                        except sqlite3.OperationalError:
                            # This can happen when running against a FTS virtual tables
                            # e.g. "select count(*) from some_fts;"
                            count = 0
                        # Figure out primary keys
                        table_info_rows = [
                            row for row in conn.execute(
                                'PRAGMA table_info("{}")'.format(table)
                            ).fetchall()
                            if row[-1]
                        ]
                        table_info_rows.sort(key=lambda row: row[-1])
                        primary_keys = [str(r[1]) for r in table_info_rows]
                        label_column = None
                        # If table has two columns, one of which is ID, then label_column is the other one
                        column_names = [r[1] for r in conn.execute(
                            'PRAGMA table_info({});'.format(escape_sqlite(table))
                        ).fetchall()]
                        if column_names and len(column_names) == 2 and 'id' in column_names:
                            label_column = [c for c in column_names if c != 'id'][0]
                        tables[table] = {
                            'name': table,
                            'columns': column_names,
                            'primary_keys': primary_keys,
                            'count': count,
                            'label_column': label_column,
                            'hidden': False,
                        }

                    foreign_keys = get_all_foreign_keys(conn)
                    for table, info in foreign_keys.items():
                        tables[table]['foreign_keys'] = info

                    # Mark tables 'hidden' if they relate to FTS virtual tables
                    hidden_tables = [
                        r['name']
                        for r in conn.execute(
                            '''
                                select name from sqlite_master
                                where rootpage = 0
                                and sql like '%VIRTUAL TABLE%USING FTS%'
                            '''
                        )
                    ]

                    if detect_spatialite(conn):
                        # Also hide Spatialite internal tables
                        hidden_tables += [
                            'ElementaryGeometries', 'SpatialIndex', 'geometry_columns',
                            'spatial_ref_sys', 'spatialite_history', 'sql_statements_log',
                            'sqlite_sequence', 'views_geometry_columns', 'virts_geometry_columns'
                        ]

                    for t in tables.keys():
                        for hidden_table in hidden_tables:
                            if t == hidden_table or t.startswith(hidden_table):
                                tables[t]['hidden'] = True
                                continue

                self._inspect[name] = {
                    'hash': m.hexdigest(),
                    'file': str(path),
                    'tables': tables,
                    'views': views,

                }
        return self._inspect

    def register_custom_units(self):
        "Register any custom units defined in the metadata.json with Pint"
        for unit in self.metadata.get('custom_units', []):
            ureg.define(unit)

    def app(self):
        app = Sanic(__name__)
        default_templates = str(app_root / 'datasette' / 'templates')
        if self.template_dir:
            template_loader = ChoiceLoader([
                FileSystemLoader([self.template_dir, default_templates]),
                # Support {% extends "default:table.html" %}:
                PrefixLoader({
                    'default': FileSystemLoader(default_templates),
                }, delimiter=':')
            ])
        else:
            template_loader = FileSystemLoader(default_templates)
        self.jinja_env = Environment(
            loader=template_loader,
            autoescape=True,
        )
        self.jinja_env.filters['escape_css_string'] = escape_css_string
        self.jinja_env.filters['quote_plus'] = lambda u: urllib.parse.quote_plus(u)
        self.jinja_env.filters['escape_sqlite'] = escape_sqlite
        self.jinja_env.filters['to_css_class'] = to_css_class
        app.add_route(IndexView.as_view(self), '/<as_json:(\.jsono?)?$>')
        # TODO: /favicon.ico and /-/static/ deserve far-future cache expires
        app.add_route(favicon, '/favicon.ico')
        app.static('/-/static/', str(app_root / 'datasette' / 'static'))
        for path, dirname in self.static_mounts:
            app.static(path, dirname)
        app.add_route(
            DatabaseView.as_view(self),
            '/<db_name:[^/\.]+?><as_json:(\.jsono?)?$>'
        )
        app.add_route(
            DatabaseDownload.as_view(self),
            '/<db_name:[^/]+?><as_db:(\.db)$>'
        )
        app.add_route(
            TableView.as_view(self),
            '/<db_name:[^/]+>/<table:[^/]+?><as_json:(\.jsono?)?$>'
        )
        app.add_route(
            RowView.as_view(self),
            '/<db_name:[^/]+>/<table:[^/]+?>/<pk_path:[^/]+?><as_json:(\.jsono?)?$>'
        )

        self.register_custom_units()

        @app.exception(Exception)
        def on_exception(request, exception):
            title = None
            if isinstance(exception, NotFound):
                status = 404
                info = {}
                message = exception.args[0]
            elif isinstance(exception, DatasetteError):
                status = exception.status
                info = exception.error_dict
                message = exception.message
                title = exception.title
            else:
                status = 500
                info = {}
                message = str(exception)
                traceback.print_exc()
            templates = ['500.html']
            if status != 500:
                templates = ['{}.html'.format(status)] + templates
            info.update({
                'ok': False,
                'error': message,
                'status': status,
                'title': title,
            })
            if (request.path.split('?')[0].endswith('.json')):
                return response.json(info, status=status)
            else:
                template = self.jinja_env.select_template(templates)
                return response.html(template.render(info), status=status)

        return app
