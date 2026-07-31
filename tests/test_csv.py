import urllib.parse

import pytest

from datasette.app import Datasette
from datasette.telemetry_registry import DB_QUERY, DB_QUERY_TEXT

EXPECTED_TABLE_CSV = """id,content
1,hello
2,world
3,
4,RENDER_CELL_DEMO
5,RENDER_CELL_ASYNC
""".replace("\n", "\r\n")

EXPECTED_CUSTOM_CSV = """content
hello
world
""".replace("\n", "\r\n")

EXPECTED_TABLE_WITH_LABELS_CSV = """
pk,created,planet_int,on_earth,state,_city_id,_city_id_label,_neighborhood,tags,complex_array,distinct_some_null,n
1,2019-01-14 08:00:00,1,1,CA,1,San Francisco,Mission,"[""tag1"", ""tag2""]","[{""foo"": ""bar""}]",one,n1
2,2019-01-14 08:00:00,1,1,CA,1,San Francisco,Dogpatch,"[""tag1"", ""tag3""]",[],two,n2
3,2019-01-14 08:00:00,1,1,CA,1,San Francisco,SOMA,[],[],,
4,2019-01-14 08:00:00,1,1,CA,1,San Francisco,Tenderloin,[],[],,
5,2019-01-15 08:00:00,1,1,CA,1,San Francisco,Bernal Heights,[],[],,
6,2019-01-15 08:00:00,1,1,CA,1,San Francisco,Hayes Valley,[],[],,
7,2019-01-15 08:00:00,1,1,CA,2,Los Angeles,Hollywood,[],[],,
8,2019-01-15 08:00:00,1,1,CA,2,Los Angeles,Downtown,[],[],,
9,2019-01-16 08:00:00,1,1,CA,2,Los Angeles,Los Feliz,[],[],,
10,2019-01-16 08:00:00,1,1,CA,2,Los Angeles,Koreatown,[],[],,
11,2019-01-16 08:00:00,1,1,MI,3,Detroit,Downtown,[],[],,
12,2019-01-17 08:00:00,1,1,MI,3,Detroit,Greektown,[],[],,
13,2019-01-17 08:00:00,1,1,MI,3,Detroit,Corktown,[],[],,
14,2019-01-17 08:00:00,1,1,MI,3,Detroit,Mexicantown,[],[],,
15,2019-01-17 08:00:00,2,0,MC,4,Memnonia,Arcadia Planitia,[],[],,
""".lstrip().replace("\n", "\r\n")

EXPECTED_TABLE_WITH_NULLABLE_LABELS_CSV = """
pk,foreign_key_with_label,foreign_key_with_label_label,foreign_key_with_blank_label,foreign_key_with_blank_label_label,foreign_key_with_no_label,foreign_key_with_no_label_label,foreign_key_compound_pk1,foreign_key_compound_pk2
1,1,hello,3,,1,1,a,b
2,,,,,,,,
""".lstrip().replace("\n", "\r\n")


@pytest.mark.asyncio
async def test_table_csv(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key.csv?_oh=1")
    assert response.status_code == 200
    assert not response.headers.get("Access-Control-Allow-Origin")
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == EXPECTED_TABLE_CSV


def test_table_csv_cors_headers(app_client_with_cors):
    response = app_client_with_cors.get("/fixtures/simple_primary_key.csv")
    assert response.status == 200
    assert response.headers["Access-Control-Allow-Origin"] == "*"


@pytest.mark.asyncio
async def test_table_csv_no_header(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key.csv?_header=off")
    assert response.status_code == 200
    assert not response.headers.get("Access-Control-Allow-Origin")
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == EXPECTED_TABLE_CSV.split("\r\n", 1)[1]


@pytest.mark.asyncio
async def test_table_csv_with_labels(ds_client):
    response = await ds_client.get("/fixtures/facetable.csv?_labels=1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == EXPECTED_TABLE_WITH_LABELS_CSV


@pytest.mark.asyncio
async def test_table_csv_with_nullable_labels(ds_client):
    response = await ds_client.get("/fixtures/foreign_key_references.csv?_labels=1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == EXPECTED_TABLE_WITH_NULLABLE_LABELS_CSV


@pytest.mark.asyncio
async def test_table_csv_with_invalid_labels():
    # https://github.com/simonw/datasette/issues/2214
    ds = Datasette(
        config={
            "databases": {
                "db_2214": {
                    "tables": {
                        "t2": {
                            "label_column": "name",
                        }
                    }
                }
            }
        }
    )
    await ds.invoke_startup()
    db = ds.add_memory_database("db_2214")
    await db.execute_write_script("""
        create table t1 (id integer primary key, name text);
        insert into t1 (id, name) values (1, 'one');
        insert into t1 (id, name) values (2, 'two');
        create table t2 (textid text primary key, name text);
        insert into t2 (textid, name) values ('a', 'alpha');
        insert into t2 (textid, name) values ('b', 'beta');
        create table if not exists maintable (
            id integer primary key,
            fk_integer integer references t1(id),
            fk_text text references t2(textid)
        );
        insert into maintable (id, fk_integer, fk_text) values (1, 1, 'a');
        insert into maintable (id, fk_integer, fk_text) values (2, 3, 'b'); -- invalid fk_integer
        insert into maintable (id, fk_integer, fk_text) values (3, 2, 'c'); -- invalid fk_text
    """)
    response = await ds.client.get("/db_2214/maintable.csv?_labels=1")
    assert response.status_code == 200
    assert response.text == (
        "id,fk_integer,fk_integer_label,fk_text,fk_text_label\r\n"
        "1,1,one,a,alpha\r\n"
        "2,3,,b,beta\r\n"
        "3,2,two,c,\r\n"
    )


@pytest.mark.asyncio
async def test_table_csv_blob_columns(ds_client):
    response = await ds_client.get("/fixtures/binary_data.csv")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == (
        "rowid,data\r\n"
        "1,http://localhost/fixtures/binary_data/1.blob?_blob_column=data\r\n"
        "2,http://localhost/fixtures/binary_data/2.blob?_blob_column=data\r\n"
        "3,\r\n"
    )


@pytest.mark.asyncio
async def test_custom_sql_csv_blob_columns(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query.csv?sql=select+rowid,+data+from+binary_data"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == (
        "rowid,data\r\n"
        '1,"http://localhost/fixtures/-/query.blob?sql=select+rowid,+data+from+binary_data&_blob_column=data&_blob_hash=f3088978da8f9aea479ffc7f631370b968d2e855eeb172bea7f6c7a04262bb6d"\r\n'
        '2,"http://localhost/fixtures/-/query.blob?sql=select+rowid,+data+from+binary_data&_blob_column=data&_blob_hash=b835b0483cedb86130b9a2c280880bf5fadc5318ddf8c18d0df5204d40df1724"\r\n'
        "3,\r\n"
    )


@pytest.mark.asyncio
async def test_custom_sql_csv(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query.csv?sql=select+content+from+simple_primary_key+limit+2"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == EXPECTED_CUSTOM_CSV


@pytest.mark.asyncio
async def test_table_csv_download(ds_client):
    response = await ds_client.get("/fixtures/simple_primary_key.csv?_dl=1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="simple_primary_key.csv"'
    )


@pytest.mark.asyncio
async def test_csv_with_non_ascii_characters(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query.csv?sql=select%0D%0A++%27%F0%9D%90%9C%F0%9D%90%A2%F0%9D%90%AD%F0%9D%90%A2%F0%9D%90%9E%F0%9D%90%AC%27+as+text%2C%0D%0A++1+as+number%0D%0Aunion%0D%0Aselect%0D%0A++%27bob%27+as+text%2C%0D%0A++2+as+number%0D%0Aorder+by%0D%0A++number"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == "text,number\r\n𝐜𝐢𝐭𝐢𝐞𝐬,1\r\nbob,2\r\n"


@pytest.mark.xfail(reason="Flaky, see https://github.com/simonw/datasette/issues/2355")
def test_max_csv_mb(app_client_csv_max_mb_one):
    # This query deliberately generates a really long string
    # should be 100*100*100*2 = roughly 2MB
    response = app_client_csv_max_mb_one.get(
        "/fixtures.csv?"
        + urllib.parse.urlencode(
            {
                "sql": """
            select group_concat('ab', '')
            from json_each(json_array({lots})),
                json_each(json_array({lots})),
                json_each(json_array({lots}))
            """.format(
                    lots=", ".join(str(i) for i in range(100))
                ),
                "_stream": 1,
                "_size": "max",
            }
        ),
    )
    # It's a 200 because we started streaming before we knew the error
    assert response.status == 200
    # Last line should be an error message
    last_line = [line for line in response.body.split(b"\r\n") if line][-1]
    assert last_line.startswith(b"CSV contains more than")


@pytest.mark.asyncio
async def test_table_csv_stream(ds_client):
    # Without _stream should return header + 100 rows:
    response = await ds_client.get(
        "/fixtures/compound_three_primary_keys.csv?_size=max"
    )
    assert len([b for b in response.content.split(b"\r\n") if b]) == 101
    # With _stream=1 should return header + 1001 rows
    response = await ds_client.get(
        "/fixtures/compound_three_primary_keys.csv?_stream=1"
    )
    assert len([b for b in response.content.split(b"\r\n") if b]) == 1002


def db_query_texts(otel_spans):
    "Every db.query.text recorded by a db.query span since the exporter was cleared."
    return [
        span.attributes.get(DB_QUERY_TEXT, "")
        for span in otel_spans.get_finished_spans()
        if span.name == DB_QUERY
    ]


# Both faceting and facet suggestion aggregate with a named count: facet
# results use "count(*) as count", suggestions use "count(*) as n". Matching
# on those rather than on a whole query string, because the surrounding SQL
# has been rewritten before - the previous version of this test looked for
# "select content, count(*) as n", which facet suggestion stopped emitting
# when it moved to a "with limited as (...)" CTE, leaving the assertion
# unable to fail.
FACET_QUERY_MARKERS = ("count(*) as n", "count(*) as count")


@pytest.mark.asyncio
async def test_table_csv_stream_does_not_calculate_facets(ds_client, otel_spans):
    response = await ds_client.get("/fixtures/simple_primary_key.csv")
    assert response.status_code == 200
    queries = db_query_texts(otel_spans)
    # Guard: without this, a change that stopped the CSV route running any
    # query at all - or that broke span capture - would leave the real
    # assertion below trivially true.
    assert any("from simple_primary_key" in q for q in queries), queries
    assert not any(
        marker in query for query in queries for marker in FACET_QUERY_MARKERS
    ), queries


@pytest.mark.asyncio
async def test_table_csv_stream_does_not_calculate_counts(ds_client, otel_spans):
    response = await ds_client.get("/fixtures/simple_primary_key.csv")
    assert response.status_code == 200
    queries = db_query_texts(otel_spans)
    assert any("from simple_primary_key" in q for q in queries), queries
    assert not any("select count(*)" in q for q in queries), queries
