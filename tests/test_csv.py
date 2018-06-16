from .fixtures import app_client # noqa

EXPECTED_TABLE_CSV = '''id,content
1,hello
2,world
3,
'''.replace('\n', '\r\n')

EXPECTED_CUSTOM_CSV = '''content
hello
world
'''.replace('\n', '\r\n')

EXPECTED_TABLE_WITH_LABELS_CSV = '''
pk,planet_int,state,city_id,city_id_label,neighborhood
1,1,CA,1,San Francisco,Mission
2,1,CA,1,San Francisco,Dogpatch
3,1,CA,1,San Francisco,SOMA
4,1,CA,1,San Francisco,Tenderloin
5,1,CA,1,San Francisco,Bernal Heights
6,1,CA,1,San Francisco,Hayes Valley
7,1,CA,2,Los Angeles,Hollywood
8,1,CA,2,Los Angeles,Downtown
9,1,CA,2,Los Angeles,Los Feliz
10,1,CA,2,Los Angeles,Koreatown
11,1,MI,3,Detroit,Downtown
12,1,MI,3,Detroit,Greektown
13,1,MI,3,Detroit,Corktown
14,1,MI,3,Detroit,Mexicantown
15,2,MC,4,Memnonia,Arcadia Planitia
'''.strip().replace('\n', '\r\n')

def test_table_csv(app_client):
    response = app_client.get('/test_tables/simple_primary_key.csv')
    assert response.status == 200
    assert 'text/plain; charset=utf-8' == response.headers['Content-Type']
    assert EXPECTED_TABLE_CSV == response.text


def test_table_csv_with_labels(app_client):
    response = app_client.get('/test_tables/facetable.csv?_labels=1')
    assert response.status == 200
    assert 'text/plain; charset=utf-8' == response.headers['Content-Type']
    assert EXPECTED_TABLE_WITH_LABELS_CSV == response.text


def test_custom_sql_csv(app_client):
    response = app_client.get(
        '/test_tables.csv?sql=select+content+from+simple_primary_key+limit+2'
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
