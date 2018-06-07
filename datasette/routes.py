from collections import namedtuple
from datasette.views.database import DatabaseView
from datasette.views.special import JsonDataView
from datasette.views.table import TableView

RouteResult = namedtuple('RouteResult', ('view', 'kwargs', 'redirect'))


def redirect(path):
    return RouteResult(None, None, path)


def resolve(path, database_exists, table_exists, database_hash):
    bits = path.split('/')
    # Kill the leading /:
    del bits[0]
    # /-/...
    if bits[0] == '-':
        return resolve_special(bits[1:])
    # /databasename
    bit = bits[0]
    rest = ''
    if bits[1:]:
        rest = '/'.join([''] + bits[1:])
    # Might be database-databasehash
    if '-' in bit:
        database, databasehash = bit.rsplit('-', 1)
        if database_exists(database):
            # Is the hash correct?
            expected_hash = database_hash(database)
            if expected_hash == databasehash:
                if not rest:
                    return RouteResult(
                        DatabaseView, {'database': database}, None
                    )
                else:
                    # Pass on to table logic
                    return resolve_table(rest, database, table_exists)
            else:
                # Bad hash, redirect
                return redirect('/{}-{}{}'.format(
                    database, expected_hash, rest)
                )
    # If we get here, maybe the full string is a DB name?
    if database_exists(bit):
        database = bit
        databasehash = database_hash(bit)
        return redirect('/{}-{}{}'.format(database, databasehash, rest))
    return None


def resolve_table(rest, database, table_exists):
    # TODO: Rows, views, canned queries
    table = rest.lstrip('/')
    if not table_exists(database, table):
        return None
    return RouteResult(TableView, {'database': database, 'table': table}, None)


specials = {'inspect', 'metadata', 'versions', 'plugins', 'config'}


def resolve_special(path_bits):
    if len(path_bits) != 1:
        return None
    filename = path_bits[0]
    as_json = False
    if filename.endswith('.json'):
        as_json = True
        filename = filename.replace('.json', '')
    if filename not in specials:
        return None
    kwargs = {
        'filename': filename,
    }
    if as_json:
        kwargs['format'] = 'json'
    return RouteResult(JsonDataView, kwargs, None)
