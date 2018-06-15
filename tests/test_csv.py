from .fixtures import app_client # noqa

EXPECTED_TABLE_CSV = '''id,content
1,hello
2,world
3,
'''.replace('\n', '\r\n')

EXPECTED_CUSTOM_CSV = '''content
hello
world
""
'''.replace('\n', '\r\n')


def test_table_csv(app_client):
    response = app_client.get('/test_tables/simple_primary_key.csv')
    assert response.status == 200
    assert 'text/plain; charset=utf-8' == response.headers['Content-Type']
    assert EXPECTED_TABLE_CSV == response.text


def test_custom_sql_csv(app_client):
    response = app_client.get(
        '/test_tables.csv?sql=select+content+from+simple_primary_key'
    )
    assert response.status == 200
    assert 'text/plain; charset=utf-8' == response.headers['Content-Type']
    assert EXPECTED_CUSTOM_CSV == response.text


def test_table_csv_download(app_client):
    response = app_client.get('/test_tables/simple_primary_key.csv?_dl=1')
    assert response.status == 200
    assert 'text/csv; charset=utf-8' == response.headers['Content-Type']
    expected_disposition = 'attachment; filename="simple_primary_key.csv"'
    assert expected_disposition == response.headers['Content-Disposition']
