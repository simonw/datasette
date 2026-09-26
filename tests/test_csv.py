import urllib.parse

import pytest
from bs4 import BeautifulSoup as Soup

from datasette.app import Datasette

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
    assert "Datasette-Truncated" not in response.headers
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
@pytest.mark.parametrize("download", (False, True))
@pytest.mark.parametrize(
    "sql,truncated",
    [
        ("select content from simple_primary_key limit 2", "false"),
        ("select * from no_primary_key", "true"),
    ],
)
async def test_custom_sql_csv_reports_row_limit(ds_client, sql, truncated, download):
    parameters = {"sql": sql}
    if download:
        parameters["_dl"] = "1"
    response = await ds_client.get(
        "/fixtures/-/query.csv?" + urllib.parse.urlencode(parameters)
    )
    assert response.status_code == 200
    assert response.headers["Datasette-Truncated"] == truncated
    assert response.headers["Datasette-Max-Returned-Rows"] == "100"


@pytest.mark.asyncio
async def test_custom_sql_csv_row_limit_headers_are_exposed_to_cors():
    ds = Datasette(memory=True, cors=True)
    try:
        response = await ds.client.get("/_memory/-/query.csv?sql=select+1")
        assert response.status_code == 200
        assert response.headers["Datasette-Truncated"] == "false"
        assert response.headers["Access-Control-Expose-Headers"] == (
            "Link, Datasette-Truncated, Datasette-Max-Returned-Rows"
        )
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_custom_sql_csv_without_row_limit():
    ds = Datasette(memory=True, settings={"max_returned_rows": 0})
    try:
        response = await ds.client.get(
            "/_memory/-/query.csv?sql=select+1+as+n+union+all+select+2"
        )
        assert response.status_code == 200
        assert response.text == "n\r\n1\r\n2\r\n"
        assert response.headers["Datasette-Truncated"] == "false"
        assert response.headers["Datasette-Max-Returned-Rows"] == "0"
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_custom_sql_csv_row_limit_matching_page_size():
    ds = Datasette(
        memory=True, settings={"max_returned_rows": 2, "default_page_size": 2}
    )
    try:
        response = await ds.client.get(
            "/_memory/-/query.csv?sql=select+1+as+n+union+all+select+2+"
            "union+all+select+3+union+all+select+4"
        )
        assert response.status_code == 200
        assert response.text == "n\r\n1\r\n2\r\n"
        assert response.headers["Datasette-Truncated"] == "true"
        assert response.headers["Datasette-Max-Returned-Rows"] == "2"
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("download", (False, True))
@pytest.mark.parametrize(
    "query_string,expected_error",
    (
        ("sql=select+blah", "no such column: blah"),
        ("sql=select+*+from+missing", "no such table: missing"),
        ("sql=select+from", 'near "from": syntax error'),
        (
            "sql=delete+from+simple_primary_key",
            "Statement must be a SELECT",
        ),
        ("", "?sql= is required"),
        (
            "sql=select+sleep(0.01)&_timelimit=5",
            (
                "SQL query took too long. The time limit is"
                " controlled by the sql_time_limit_ms setting."
            ),
        ),
    ),
)
async def test_custom_sql_csv_errors(ds_client, query_string, expected_error, download):
    if download:
        query_string += "&_dl=1"
    response = await ds_client.get(f"/fixtures/-/query.csv?{query_string}")
    assert response.status_code == 400
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert "content-disposition" not in response.headers
    assert response.text == expected_error


@pytest.mark.asyncio
async def test_custom_sql_csv_error_head(ds_client):
    response = await ds_client.head("/fixtures/-/query.csv?sql=select+blah")
    assert response.status_code == 400
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.content == b""


@pytest.mark.asyncio
async def test_custom_sql_csv_error_cors():
    ds = Datasette(cors=True)
    response = await ds.client.get("/_memory/-/query.csv?sql=select+blah")
    assert response.status_code == 400
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.text == "no such column: blah"


@pytest.mark.asyncio
async def test_table_csv_error(ds_client):
    response = await ds_client.get(
        "/fixtures/simple_primary_key.csv?_where=blah&_stream=1"
    )
    assert response.status_code == 400
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == "no such column: blah"


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


@pytest.mark.asyncio
async def test_view_csv_stream(ds_client):
    # Without _stream should return header + 100 rows:
    response = await ds_client.get("/fixtures/paginated_view.csv?_size=max")
    assert len([b for b in response.content.split(b"\r\n") if b]) == 101
    # With _stream=1 should paginate through all pages and return header + 202 rows
    response = await ds_client.get("/fixtures/paginated_view.csv?_stream=1")
    lines = [b for b in response.content.split(b"\r\n") if b]
    assert len(lines) == 203
    # Ensure there are no duplicate rows from looping
    assert len(set(lines[1:])) == 202
    assert lines[0] == b"content,content_extra"


def test_csv_trace(app_client_with_trace):
    response = app_client_with_trace.get("/fixtures/simple_primary_key.csv?_trace=1")
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    soup = Soup(response.text, "html.parser")
    assert (
        soup.find("textarea").text
        == "id,content\r\n1,hello\r\n2,world\r\n3,\r\n4,RENDER_CELL_DEMO\r\n5,RENDER_CELL_ASYNC\r\n"
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
