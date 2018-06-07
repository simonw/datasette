from datasette import routes
from datasette.views.database import DatabaseView
from datasette.views.special import JsonDataView
from datasette.views.table import TableView
import pytest

MOCK_DATABASES = {
    # database: set-of-tables
    'foo': {'bar'},
    'foo-bar': {'baz'}
}
MOCK_DATABASE_HASHES = {
    'foo': 'foohash',
    'foo-bar': 'foobarhash',
}


def database_exists(database):
    return database in MOCK_DATABASES


def table_exists(database, table):
    print('table_exists: ', database, table)
    return table in MOCK_DATABASES.get(database, set())


def database_hash(database):
    return MOCK_DATABASE_HASHES[database]


@pytest.mark.parametrize('path,expected', [
    ('/does-not-exist', None),
    # This should redirect
    ('/foo', routes.RouteResult(
        None, None, '/foo-foohash'
    )),
    ('/foo-bar-badhash', routes.RouteResult(
        None, None, '/foo-bar-foobarhash'
    )),
    ('/foo-foohash', routes.RouteResult(
        DatabaseView, {'database': 'foo'}, None
    )),
    # Table views
    ('/foo/bar', routes.RouteResult(
        None, None, '/foo-foohash/bar'
    )),
    ('/foo/bad', routes.RouteResult(
        None, None, '/foo-foohash/bad'
    )),
    ('/foo-foohash/bad', None),
    ('/foo-foohash/bar', routes.RouteResult(
        TableView, {'database': 'foo', 'table': 'bar'}, None
    )),
] + [
    ('/-/{}'.format(filename), routes.RouteResult(
        JsonDataView, {'filename': filename}, None
    )) for filename in ('inspect', 'metadata', 'versions', 'plugins', 'config')
] + [
    ('/-/{}.json'.format(filename), routes.RouteResult(
        JsonDataView, {'filename': filename, 'format': 'json'}, None
    )) for filename in ('inspect', 'metadata', 'versions', 'plugins', 'config')
])
def test_routes(path, expected):
    actual = routes.resolve(path, database_exists, table_exists, database_hash)
    assert actual == expected
