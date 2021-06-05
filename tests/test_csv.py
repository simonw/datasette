from bs4 import BeautifulSoup as Soup
from .fixtures import (  # noqa
    app_client,
    app_client_csv_max_mb_one,
    app_client_with_cors,
    app_client_with_trace,
)

EXPECTED_TABLE_CSV = """id,content
1,hello
2,world
3,
4,RENDER_CELL_DEMO
""".replace(
    "\n", "\r\n"
)

EXPECTED_CUSTOM_CSV = """content
hello
world
""".replace(
    "\n", "\r\n"
)

EXPECTED_TABLE_WITH_LABELS_CSV = """
pk,created,planet_int,on_earth,state,city_id,city_id_label,neighborhood,tags,complex_array,distinct_some_null
1,2019-01-14 08:00:00,1,1,CA,1,San Francisco,Mission,"[""tag1"", ""tag2""]","[{""foo"": ""bar""}]",one
2,2019-01-14 08:00:00,1,1,CA,1,San Francisco,Dogpatch,"[""tag1"", ""tag3""]",[],two
3,2019-01-14 08:00:00,1,1,CA,1,San Francisco,SOMA,[],[],
4,2019-01-14 08:00:00,1,1,CA,1,San Francisco,Tenderloin,[],[],
5,2019-01-15 08:00:00,1,1,CA,1,San Francisco,Bernal Heights,[],[],
6,2019-01-15 08:00:00,1,1,CA,1,San Francisco,Hayes Valley,[],[],
7,2019-01-15 08:00:00,1,1,CA,2,Los Angeles,Hollywood,[],[],
8,2019-01-15 08:00:00,1,1,CA,2,Los Angeles,Downtown,[],[],
9,2019-01-16 08:00:00,1,1,CA,2,Los Angeles,Los Feliz,[],[],
10,2019-01-16 08:00:00,1,1,CA,2,Los Angeles,Koreatown,[],[],
11,2019-01-16 08:00:00,1,1,MI,3,Detroit,Downtown,[],[],
12,2019-01-17 08:00:00,1,1,MI,3,Detroit,Greektown,[],[],
13,2019-01-17 08:00:00,1,1,MI,3,Detroit,Corktown,[],[],
14,2019-01-17 08:00:00,1,1,MI,3,Detroit,Mexicantown,[],[],
15,2019-01-17 08:00:00,2,0,MC,4,Memnonia,Arcadia Planitia,[],[],
""".lstrip().replace(
    "\n", "\r\n"
)

EXPECTED_TABLE_WITH_NULLABLE_LABELS_CSV = """
pk,foreign_key_with_label,foreign_key_with_label_label,foreign_key_with_blank_label,foreign_key_with_blank_label_label,foreign_key_with_no_label,foreign_key_with_no_label_label,foreign_key_compound_pk1,foreign_key_compound_pk2
1,1,hello,3,,1,1,a,b
2,,,,,,,,
""".lstrip().replace(
    "\n", "\r\n"
)


def test_table_csv(app_client):
    response = app_client.get("/fixtures/simple_primary_key.csv?_oh=1")
    assert response.status == 200
    assert not response.headers.get("Access-Control-Allow-Origin")
    assert "text/plain; charset=utf-8" == response.headers["content-type"]
    assert EXPECTED_TABLE_CSV == response.text


def test_table_csv_cors_headers(app_client_with_cors):
    response = app_client_with_cors.get("/fixtures/simple_primary_key.csv")
    assert response.status == 200
    assert "*" == response.headers["Access-Control-Allow-Origin"]


def test_table_csv_no_header(app_client):
    response = app_client.get("/fixtures/simple_primary_key.csv?_header=off")
    assert response.status == 200
    assert not response.headers.get("Access-Control-Allow-Origin")
    assert "text/plain; charset=utf-8" == response.headers["content-type"]
    assert EXPECTED_TABLE_CSV.split("\r\n", 1)[1] == response.text


def test_table_csv_with_labels(app_client):
    response = app_client.get("/fixtures/facetable.csv?_labels=1")
    assert response.status == 200
    assert "text/plain; charset=utf-8" == response.headers["content-type"]
    assert EXPECTED_TABLE_WITH_LABELS_CSV == response.text


def test_table_csv_with_nullable_labels(app_client):
    response = app_client.get("/fixtures/foreign_key_references.csv?_labels=1")
    assert response.status == 200
    assert "text/plain; charset=utf-8" == response.headers["content-type"]
    assert EXPECTED_TABLE_WITH_NULLABLE_LABELS_CSV == response.text


def test_table_csv_blob_columns(app_client):
    response = app_client.get("/fixtures/binary_data.csv")
    assert response.status == 200
    assert "text/plain; charset=utf-8" == response.headers["content-type"]
    assert response.text == (
        "rowid,data\r\n"
        "1,http://localhost/fixtures/binary_data/1.blob?_blob_column=data\r\n"
        "2,http://localhost/fixtures/binary_data/2.blob?_blob_column=data\r\n"
        "3,\r\n"
    )


def test_custom_sql_csv_blob_columns(app_client):
    response = app_client.get("/fixtures.csv?sql=select+rowid,+data+from+binary_data")
    assert response.status == 200
    assert "text/plain; charset=utf-8" == response.headers["content-type"]
    assert response.text == (
        "rowid,data\r\n"
        '1,"http://localhost/fixtures.blob?sql=select+rowid,+data+from+binary_data&_blob_column=data&_blob_hash=f3088978da8f9aea479ffc7f631370b968d2e855eeb172bea7f6c7a04262bb6d"\r\n'
        '2,"http://localhost/fixtures.blob?sql=select+rowid,+data+from+binary_data&_blob_column=data&_blob_hash=b835b0483cedb86130b9a2c280880bf5fadc5318ddf8c18d0df5204d40df1724"\r\n'
        "3,\r\n"
    )


def test_custom_sql_csv(app_client):
    response = app_client.get(
        "/fixtures.csv?sql=select+content+from+simple_primary_key+limit+2"
    )
    assert response.status == 200
    assert "text/plain; charset=utf-8" == response.headers["content-type"]
    assert EXPECTED_CUSTOM_CSV == response.text


def test_table_csv_download(app_client):
    response = app_client.get("/fixtures/simple_primary_key.csv?_dl=1")
    assert response.status == 200
    assert "text/csv; charset=utf-8" == response.headers["content-type"]
    expected_disposition = 'attachment; filename="simple_primary_key.csv"'
    assert expected_disposition == response.headers["content-disposition"]


def test_csv_with_non_ascii_characters(app_client):
    response = app_client.get(
        "/fixtures.csv?sql=select%0D%0A++%27%F0%9D%90%9C%F0%9D%90%A2%F0%9D%90%AD%F0%9D%90%A2%F0%9D%90%9E%F0%9D%90%AC%27+as+text%2C%0D%0A++1+as+number%0D%0Aunion%0D%0Aselect%0D%0A++%27bob%27+as+text%2C%0D%0A++2+as+number%0D%0Aorder+by%0D%0A++number"
    )
    assert response.status == 200
    assert "text/plain; charset=utf-8" == response.headers["content-type"]
    assert "text,number\r\n𝐜𝐢𝐭𝐢𝐞𝐬,1\r\nbob,2\r\n" == response.text


def test_max_csv_mb(app_client_csv_max_mb_one):
    response = app_client_csv_max_mb_one.get(
        (
            "/fixtures.csv?sql=select+'{}'+"
            "from+compound_three_primary_keys&_stream=1&_size=max"
        ).format("abcdefg" * 10000)
    )
    # It's a 200 because we started streaming before we knew the error
    assert response.status == 200
    # Last line should be an error message
    last_line = [line for line in response.body.split(b"\r\n") if line][-1]
    assert last_line.startswith(b"CSV contains more than")


def test_table_csv_stream(app_client):
    # Without _stream should return header + 100 rows:
    response = app_client.get("/fixtures/compound_three_primary_keys.csv?_size=max")
    assert 101 == len([b for b in response.body.split(b"\r\n") if b])
    # With _stream=1 should return header + 1001 rows
    response = app_client.get("/fixtures/compound_three_primary_keys.csv?_stream=1")
    assert 1002 == len([b for b in response.body.split(b"\r\n") if b])


def test_csv_trace(app_client_with_trace):
    response = app_client_with_trace.get("/fixtures/simple_primary_key.csv?_trace=1")
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    soup = Soup(response.text, "html.parser")
    assert (
        soup.find("textarea").text
        == "id,content\r\n1,hello\r\n2,world\r\n3,\r\n4,RENDER_CELL_DEMO\r\n"
    )
    assert "select id, content from simple_primary_key" in soup.find("pre").text


def test_table_csv_stream_does_not_calculate_facets(app_client_with_trace):
    response = app_client_with_trace.get("/fixtures/simple_primary_key.csv?_trace=1")
    soup = Soup(response.text, "html.parser")
    assert "select content, count(*) as n" not in soup.find("pre").text


def test_table_csv_stream_does_not_calculate_counts(app_client_with_trace):
    response = app_client_with_trace.get("/fixtures/simple_primary_key.csv?_trace=1")
    soup = Soup(response.text, "html.parser")
    assert "select count(*)" not in soup.find("pre").text
