from datasette.app import Datasette
from datasette.events import RenameTableEvent
from datasette.utils import error_body, escape_sqlite, path_from_row_pks, sqlite3
from .utils import last_event
import pytest
import time


def assert_schema_contains(fragment, schema):
    assert fragment in schema, "Expected schema to contain {!r}, got {!r}".format(
        fragment, schema
    )


def assert_schema_not_contains(fragment, schema):
    assert (
        fragment not in schema
    ), "Expected schema not to contain {!r}, got {!r}".format(fragment, schema)


@pytest.fixture
def ds_write(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db_path_immutable = str(db_directory / "immutable.db")
    db1 = sqlite3.connect(str(db_path))
    db2 = sqlite3.connect(str(db_path_immutable))
    for db in (db1, db2):
        db.execute("vacuum")
        db.execute(
            "create table docs (id integer primary key, title text, score float, age integer)"
        )
    db1.close()
    db2.close()
    ds = Datasette([db_path], immutables=[db_path_immutable])
    ds.root_enabled = True
    yield ds
    ds.close()


def write_token(ds, actor_id="root", permissions=None):
    to_sign = {"a": actor_id, "token": "dstok", "t": int(time.time())}
    if permissions:
        to_sign["_r"] = {"a": permissions}
    return "dstok_{}".format(ds.sign(to_sign, namespace="token"))


def _headers(token):
    return {
        "Authorization": "Bearer {}".format(token),
        "Content-Type": "application/json",
    }


def _insert_and_fetch_created(conn, table, insert_sql):
    cursor = conn.execute(insert_sql)
    return conn.execute(
        "select created, typeof(created) from {} where rowid = ?".format(
            escape_sqlite(table)
        ),
        (cursor.lastrowid,),
    ).fetchone()


BASE64_WRITE_API_VALUE = {"$base64": True, "encoded": "AAEC/f7/"}
BASE64_WRITE_API_LITERAL = '{"$base64": true, "encoded": "AAEC/f7/"}'


@pytest.mark.asyncio
async def test_base64_write_api_create_table_infers_blob_and_raw_escapes(ds_write):
    token = write_token(ds_write)
    response = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": "binary_create",
            "row": {
                "id": 1,
                "data": BASE64_WRITE_API_VALUE,
                "literal": {"$raw": BASE64_WRITE_API_VALUE},
                "double_raw": {"$raw": {"$raw": BASE64_WRITE_API_VALUE}},
            },
            "pk": "id",
        },
        headers=_headers(token),
    )
    assert response.status_code == 201
    assert_schema_contains('"data" BLOB', response.json()["schema"])
    assert_schema_contains('"literal" TEXT', response.json()["schema"])

    rows = (await ds_write.get_database("data").execute("""
            select
              typeof(data) as data_type,
              hex(data) as data_hex,
              typeof(literal) as literal_type,
              literal,
              typeof(double_raw) as double_raw_type,
              double_raw
            from binary_create
            """)).dicts()
    assert rows == [
        {
            "data_type": "blob",
            "data_hex": "000102FDFEFF",
            "literal_type": "text",
            "literal": BASE64_WRITE_API_LITERAL,
            "double_raw_type": "text",
            "double_raw": '{"$raw": {"$base64": true, "encoded": "AAEC/f7/"}}',
        }
    ]


@pytest.mark.asyncio
async def test_base64_write_api_insert_upsert_update_decode_blobs(ds_write):
    token = write_token(ds_write)
    db = ds_write.get_database("data")
    await db.execute_write(
        "create table binary_api (id integer primary key, data blob, literal text)"
    )

    insert_response = await ds_write.client.post(
        "/data/binary_api/-/insert",
        json={
            "row": {
                "id": 1,
                "data": BASE64_WRITE_API_VALUE,
                "literal": {"$raw": BASE64_WRITE_API_VALUE},
            }
        },
        headers=_headers(token),
    )
    assert insert_response.status_code == 201
    assert insert_response.json()["rows"][0]["data"] == BASE64_WRITE_API_VALUE

    upsert_response = await ds_write.client.post(
        "/data/binary_api/-/upsert",
        json={
            "rows": [
                {
                    "id": 2,
                    "data": BASE64_WRITE_API_VALUE,
                    "literal": {"$raw": BASE64_WRITE_API_VALUE},
                }
            ]
        },
        headers=_headers(token),
    )
    assert upsert_response.status_code == 200
    assert upsert_response.json() == {"ok": True}

    update_response = await ds_write.client.post(
        "/data/binary_api/1/-/update",
        json={
            "update": {
                "data": {"$base64": True, "encoded": "/wAB"},
                "literal": {"$raw": {"$raw": BASE64_WRITE_API_VALUE}},
            },
            "return": True,
        },
        headers=_headers(token),
    )
    assert update_response.status_code == 200
    assert update_response.json()["rows"][0]["data"] == {
        "$base64": True,
        "encoded": "/wAB",
    }

    rows = (await db.execute("""
            select
              id,
              typeof(data) as data_type,
              hex(data) as data_hex,
              typeof(literal) as literal_type,
              literal
            from binary_api
            order by id
            """)).dicts()
    assert rows == [
        {
            "id": 1,
            "data_type": "blob",
            "data_hex": "FF0001",
            "literal_type": "text",
            "literal": '{"$raw": {"$base64": true, "encoded": "AAEC/f7/"}}',
        },
        {
            "id": 2,
            "data_type": "blob",
            "data_hex": "000102FDFEFF",
            "literal_type": "text",
            "literal": BASE64_WRITE_API_LITERAL,
        },
    ]


@pytest.mark.asyncio
async def test_api_explorer_upsert_example_json(ds_write):
    response = await ds_write.client.get("/-/api", actor={"id": "root"})
    assert response.status_code == 200
    import urllib.parse

    text = urllib.parse.unquote_plus(response.text)
    upsert_idx = text.index("/data/docs/-/upsert")
    upsert_chunk = text[upsert_idx : upsert_idx + 500]
    assert '"id": "<id (primary key)>"' in upsert_chunk
    assert '"title": "<title>"' in upsert_chunk
    assert '"score": "<score>"' in upsert_chunk
    assert '"age": "<age>"' in upsert_chunk


@pytest.mark.asyncio
async def test_api_explorer_upsert_example_json_rowid_table(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("dbs") / "data.db")
    conn = sqlite3.connect(db_path)
    conn.execute("create table things (title text, score float)")
    conn.close()
    ds = Datasette([db_path])
    ds.root_enabled = True
    response = await ds.client.get("/-/api", actor={"id": "root"})
    assert response.status_code == 200
    import urllib.parse

    text = urllib.parse.unquote_plus(response.text)
    upsert_idx = text.index("/data/things/-/upsert")
    upsert_chunk = text[upsert_idx : upsert_idx + 500]
    assert '"rowid": "<rowid (primary key)>"' in upsert_chunk
    assert '"title": "<title>"' in upsert_chunk
    assert '"score": "<score>"' in upsert_chunk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type",
    (
        "application/json",
        "application/json; charset=utf-8",
    ),
)
async def test_insert_row(ds_write, content_type):
    token = write_token(ds_write)
    response = await ds_write.client.post(
        "/data/docs/-/insert",
        json={"row": {"title": "Test", "score": 1.2, "age": 5}},
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": content_type,
        },
    )
    expected_row = {"id": 1, "title": "Test", "score": 1.2, "age": 5}
    assert response.status_code == 201
    assert response.json()["ok"] is True
    assert response.json()["rows"] == [expected_row]
    rows = (await ds_write.get_database("data").execute("select * from docs")).dicts()
    assert rows[0] == expected_row
    # Analytics event
    event = last_event(ds_write)
    assert event.name == "insert-rows"
    assert event.num_rows == 1
    assert event.database == "data"
    assert event.table == "docs"
    assert not event.ignore
    assert not event.replace


@pytest.mark.asyncio
async def test_insert_row_alter(ds_write):
    token = write_token(ds_write)
    response = await ds_write.client.post(
        "/data/docs/-/insert",
        json={
            "row": {"title": "Test", "score": 1.2, "age": 5, "extra": "extra"},
            "alter": True,
        },
        headers=_headers(token),
    )
    assert response.status_code == 201
    assert response.json()["ok"] is True
    assert response.json()["rows"][0]["extra"] == "extra"
    # Analytics event
    event = last_event(ds_write)
    assert event.name == "alter-table"
    assert "extra" not in event.before_schema
    assert "extra" in event.after_schema


@pytest.mark.asyncio
@pytest.mark.parametrize("return_rows", (True, False))
async def test_insert_rows(ds_write, return_rows):
    token = write_token(ds_write)
    data = {
        "rows": [
            {"title": "Test {}".format(i), "score": 1.0, "age": 5} for i in range(20)
        ]
    }
    if return_rows:
        data["return"] = True
    response = await ds_write.client.post(
        "/data/docs/-/insert",
        json=data,
        headers=_headers(token),
    )
    assert response.status_code == 201

    # Analytics event
    event = last_event(ds_write)
    assert event.name == "insert-rows"
    assert event.num_rows == 20
    assert event.database == "data"
    assert event.table == "docs"
    assert not event.ignore
    assert not event.replace

    actual_rows = (
        await ds_write.get_database("data").execute("select * from docs")
    ).dicts()
    assert len(actual_rows) == 20
    assert actual_rows == [
        {"id": i + 1, "title": "Test {}".format(i), "score": 1.0, "age": 5}
        for i in range(20)
    ]
    assert response.json()["ok"] is True
    if return_rows:
        assert response.json()["rows"] == actual_rows


@pytest.mark.asyncio
async def test_insert_rows_post_body_too_large(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("dbs") / "data.db")
    conn = sqlite3.connect(db_path)
    conn.execute("create table docs (id integer primary key, title text)")
    conn.close()
    ds = Datasette([db_path], settings={"max_post_body_bytes": 100})
    ds.root_enabled = True
    token = write_token(ds)
    response = await ds.client.post(
        "/data/docs/-/insert",
        json={"rows": [{"title": "x" * 200}]},
        headers=_headers(token),
    )
    assert response.status_code == 413
    assert response.json() == error_body(
        ["Request body exceeded maximum size of 100 bytes"], 413
    )
    # A small body should still work
    response2 = await ds.client.post(
        "/data/docs/-/insert",
        json={"row": {"title": "hi"}},
        headers=_headers(token),
    )
    assert response2.status_code == 201
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,input,special_case,expected_status,expected_errors",
    (
        (
            "/data2/docs/-/insert",
            {},
            None,
            404,
            ["Database not found"],
        ),
        (
            "/data/docs2/-/insert",
            {},
            None,
            404,
            ["Table not found"],
        ),
        (
            "/data/docs/-/insert",
            {"rows": [{"title": "Test"} for i in range(10)]},
            "bad_token",
            401,
            ["Invalid token signature"],
        ),
        (
            "/data/docs/-/insert",
            {},
            "invalid_json",
            400,
            [
                "Invalid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
            ],
        ),
        (
            "/data/docs/-/insert",
            [],
            None,
            400,
            ["JSON must be a dictionary"],
        ),
        (
            "/data/docs/-/insert",
            {"row": "blah"},
            None,
            400,
            ['"row" must be a dictionary'],
        ),
        (
            "/data/docs/-/insert",
            {"blah": "blah"},
            None,
            400,
            ['JSON must have one or other of "row" or "rows"'],
        ),
        (
            "/data/docs/-/insert",
            {"rows": "blah"},
            None,
            400,
            ['"rows" must be a list'],
        ),
        (
            "/data/docs/-/insert",
            {"rows": ["blah"]},
            None,
            400,
            ['"rows" must be a list of dictionaries'],
        ),
        (
            "/data/docs/-/insert",
            {"rows": [{"title": "Test"} for i in range(101)]},
            None,
            400,
            ["Too many rows, maximum allowed is 100"],
        ),
        (
            "/data/docs/-/insert",
            {"rows": [{"id": 1, "title": "Test"}, {"id": 2, "title": "Test"}]},
            "duplicate_id",
            400,
            ["UNIQUE constraint failed: docs.id"],
        ),
        (
            "/data/docs/-/insert",
            {"rows": [{"title": "Test"}], "ignore": True, "replace": True},
            None,
            400,
            ['Cannot use "ignore" and "replace" at the same time'],
        ),
        (
            # Replace is not allowed if you don't have update-row
            "/data/docs/-/insert",
            {"rows": [{"title": "Test"}], "replace": True},
            "insert-but-not-update",
            403,
            ['Permission denied: need update-row to use "replace"'],
        ),
        (
            "/data/docs/-/insert",
            {"rows": [{"title": "Test"}], "invalid_param": True},
            None,
            400,
            ['Invalid parameter: "invalid_param"'],
        ),
        (
            "/data/docs/-/insert",
            {"rows": [{"title": "Test"}], "one": True, "two": True},
            None,
            400,
            ['Invalid parameter: "one", "two"'],
        ),
        (
            "/immutable/docs/-/insert",
            {"rows": [{"title": "Test"}]},
            None,
            403,
            ["Database is immutable"],
        ),
        # Validate columns of each row
        (
            "/data/docs/-/insert",
            {"rows": [{"title": "Test", "bad": 1, "worse": 2} for i in range(2)]},
            None,
            400,
            [
                "Row 0 has invalid columns: bad, worse",
                "Row 1 has invalid columns: bad, worse",
            ],
        ),
        ## UPSERT ERRORS:
        (
            "/immutable/docs/-/upsert",
            {"rows": [{"title": "Test"}]},
            None,
            403,
            ["Database is immutable"],
        ),
        (
            "/data/badtable/-/upsert",
            {"rows": [{"title": "Test"}]},
            None,
            404,
            ["Table not found"],
        ),
        # missing primary key
        (
            "/data/docs/-/upsert",
            {"rows": [{"title": "Missing PK"}]},
            None,
            400,
            ['Row 0 is missing primary key column(s): "id"'],
        ),
        # null primary key
        (
            "/data/docs/-/upsert",
            {"rows": [{"id": None, "title": "Null PK"}]},
            None,
            400,
            ['Row 0 has null primary key column(s): "id"'],
        ),
        # Upsert does not support ignore or replace
        (
            "/data/docs/-/upsert",
            {"rows": [{"id": 1, "title": "Bad"}], "ignore": True},
            None,
            400,
            ["Upsert does not support ignore or replace"],
        ),
        # Upsert permissions
        (
            "/data/docs/-/upsert",
            {"rows": [{"id": 1, "title": "Disallowed"}]},
            "insert-but-not-update",
            403,
            ["Permission denied: need both insert-row and update-row"],
        ),
        (
            "/data/docs/-/upsert",
            {"rows": [{"id": 1, "title": "Disallowed"}]},
            "update-but-not-insert",
            403,
            ["Permission denied: need both insert-row and update-row"],
        ),
        # Alter table forbidden without alter permission
        (
            "/data/docs/-/upsert",
            {"rows": [{"id": 1, "title": "One", "extra": "extra"}], "alter": True},
            "update-and-insert-but-no-alter",
            403,
            ["Permission denied for alter-table"],
        ),
    ),
)
async def test_insert_or_upsert_row_errors(
    ds_write, path, input, special_case, expected_status, expected_errors
):
    token_permissions = []
    if special_case == "insert-but-not-update":
        token_permissions = ["ir", "vi"]
    if special_case == "update-but-not-insert":
        token_permissions = ["ur", "vi"]
    if special_case == "update-and-insert-but-no-alter":
        token_permissions = ["ur", "ir"]
    token = write_token(ds_write, permissions=token_permissions)
    if special_case == "duplicate_id":
        await ds_write.get_database("data").execute_write(
            "insert into docs (id) values (1)"
        )
    if special_case == "bad_token":
        token += "bad"
    kwargs = dict(
        json=input,
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json",
        },
    )

    if special_case != "bad_token":
        actor_response = (
            await ds_write.client.get("/-/actor.json", headers=kwargs["headers"])
        ).json()
        assert set((actor_response["actor"] or {}).get("_r", {}).get("a") or []) == set(
            token_permissions
        )

    if special_case == "invalid_json":
        del kwargs["json"]
        kwargs["content"] = "{bad json"
    before_count = (
        await ds_write.get_database("data").execute("select count(*) from docs")
    ).rows[0][0] == 0
    response = await ds_write.client.post(
        path,
        **kwargs,
    )
    assert response.status_code == expected_status
    assert response.json()["ok"] is False
    assert response.json()["errors"] == expected_errors
    # Check that no rows were inserted
    after_count = (
        await ds_write.get_database("data").execute("select count(*) from docs")
    ).rows[0][0] == 0
    assert before_count == after_count


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", (True, False))
async def test_upsert_permissions_per_table(ds_write, allowed):
    # https://github.com/simonw/datasette/issues/2262
    token = "dstok_{}".format(
        ds_write.sign(
            {
                "a": "root",
                "token": "dstok",
                "t": int(time.time()),
                "_r": {
                    "r": {
                        "data": {
                            "docs" if allowed else "other": ["ir", "ur"],
                        }
                    }
                },
            },
            namespace="token",
        )
    )
    response = await ds_write.client.post(
        "/data/docs/-/upsert",
        json={"rows": [{"id": 1, "title": "One"}]},
        headers={
            "Authorization": "Bearer {}".format(token),
        },
    )
    if allowed:
        assert response.status_code == 200
        assert response.json()["ok"] is True
    else:
        assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ignore,replace,expected_rows",
    (
        (
            True,
            False,
            [
                {"id": 1, "title": "Exists", "score": None, "age": None},
            ],
        ),
        (
            False,
            True,
            [
                {"id": 1, "title": "One", "score": None, "age": None},
            ],
        ),
    ),
)
@pytest.mark.parametrize("should_return", (True, False))
async def test_insert_ignore_replace(
    ds_write, ignore, replace, expected_rows, should_return
):
    await ds_write.get_database("data").execute_write(
        "insert into docs (id, title) values (1, 'Exists')"
    )
    token = write_token(ds_write)
    data = {"rows": [{"id": 1, "title": "One"}]}
    if ignore:
        data["ignore"] = True
    if replace:
        data["replace"] = True
    if should_return:
        data["return"] = True
    response = await ds_write.client.post(
        "/data/docs/-/insert",
        json=data,
        headers=_headers(token),
    )
    assert response.status_code == 201

    # Analytics event
    event = last_event(ds_write)
    assert event.name == "insert-rows"
    assert event.num_rows == 1
    assert event.database == "data"
    assert event.table == "docs"
    assert event.ignore == ignore
    assert event.replace == replace

    actual_rows = (
        await ds_write.get_database("data").execute("select * from docs")
    ).dicts()

    assert actual_rows == expected_rows
    assert response.json()["ok"] is True
    if should_return:
        assert response.json()["rows"] == expected_rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial,input,expected_rows",
    (
        (
            # Simple primary key update
            {"rows": [{"id": 1, "title": "One"}], "pk": "id"},
            {"rows": [{"id": 1, "title": "Two"}]},
            [
                {"id": 1, "title": "Two"},
            ],
        ),
        (
            # Multiple rows update one of them
            {
                "rows": [{"id": 1, "title": "One"}, {"id": 2, "title": "Two"}],
                "pk": "id",
            },
            {"rows": [{"id": 1, "title": "Three"}]},
            [
                {"id": 1, "title": "Three"},
                {"id": 2, "title": "Two"},
            ],
        ),
        (
            # rowid update
            {"rows": [{"title": "One"}]},
            {"rows": [{"rowid": 1, "title": "Two"}]},
            [
                {"rowid": 1, "title": "Two"},
            ],
        ),
        (
            # Compound primary key update
            {"rows": [{"id": 1, "title": "One", "score": 1}], "pks": ["id", "score"]},
            {"rows": [{"id": 1, "title": "Two", "score": 1}]},
            [
                {"id": 1, "title": "Two", "score": 1},
            ],
        ),
        (
            # Upsert with an alter
            {"rows": [{"id": 1, "title": "One"}], "pk": "id"},
            {"rows": [{"id": 1, "title": "Two", "extra": "extra"}], "alter": True},
            [{"id": 1, "title": "Two", "extra": "extra"}],
        ),
    ),
)
@pytest.mark.parametrize("should_return", (False, True))
async def test_upsert(ds_write, initial, input, expected_rows, should_return):
    token = write_token(ds_write)
    # Insert initial data
    initial["table"] = "upsert_test"
    create_response = await ds_write.client.post(
        "/data/-/create",
        json=initial,
        headers=_headers(token),
    )
    assert create_response.status_code == 201
    if should_return:
        input["return"] = True
    response = await ds_write.client.post(
        "/data/upsert_test/-/upsert",
        json=input,
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True

    # Analytics event
    event = last_event(ds_write)
    assert event.database == "data"
    assert event.table == "upsert_test"
    if input.get("alter"):
        assert event.name == "alter-table"
        assert "extra" in event.after_schema
    else:
        assert event.name == "upsert-rows"
        assert event.num_rows == 1

    if should_return:
        # We only expect it to return rows corresponding to those we sent
        expected_returned_rows = expected_rows[: len(input["rows"])]
        assert response.json()["rows"] == expected_returned_rows
    # Check the database too
    actual_rows = (
        await ds_write.client.get("/data/upsert_test.json?_shape=array")
    ).json()
    assert actual_rows == expected_rows
    # Drop the upsert_test table
    await ds_write.get_database("data").execute_write("drop table upsert_test")


async def _insert_row(ds):
    insert_response = await ds.client.post(
        "/data/docs/-/insert",
        json={"row": {"title": "Row one", "score": 1.2, "age": 5}, "return": True},
        headers=_headers(write_token(ds)),
    )
    assert insert_response.status_code == 201
    return insert_response.json()["rows"][0]["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ("no_token", "no_perm", "bad_table"))
async def test_delete_row_errors(ds_write, scenario):
    if scenario == "no_token":
        token = "bad_token"
    elif scenario == "no_perm":
        token = write_token(ds_write, actor_id="not-root")
    else:
        token = write_token(ds_write)

    pk = await _insert_row(ds_write)

    path = "/data/{}/{}/-/delete".format(
        "docs" if scenario != "bad_table" else "bad_table", pk
    )
    response = await ds_write.client.post(
        path,
        headers=_headers(token),
    )
    assert response.status_code == 403 if scenario in ("no_token", "bad_token") else 404
    assert response.json()["ok"] is False
    assert (
        response.json()["errors"] == ["Permission denied"]
        if scenario == "no_token"
        else ["Table not found"]
    )
    assert len((await ds_write.client.get("/data/docs.json?_shape=array")).json()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,row_for_create,pks,delete_path",
    (
        ("rowid_table", {"name": "rowid row"}, None, None),
        ("pk_table", {"id": 1, "name": "ID table"}, "id", "1"),
        (
            "compound_pk_table",
            {"type": "article", "key": "k"},
            ["type", "key"],
            "article,k",
        ),
    ),
)
async def test_delete_row(ds_write, table, row_for_create, pks, delete_path):
    # First create the table with that example row
    create_data = {
        "table": table,
        "row": row_for_create,
    }
    if pks:
        if isinstance(pks, str):
            create_data["pk"] = pks
        else:
            create_data["pks"] = pks
    create_response = await ds_write.client.post(
        "/data/-/create",
        json=create_data,
        headers=_headers(write_token(ds_write)),
    )
    assert create_response.status_code == 201, create_response.json()
    # Should be a single row
    assert (
        await ds_write.client.get(
            "/data/-/query.json?_shape=arrayfirst&sql=select+count(*)+from+{}".format(
                table
            )
        )
    ).json() == [1]
    # Now delete the row
    if delete_path is None:
        # Special case for that rowid table
        delete_path = (
            await ds_write.client.get(
                "/data/-/query.json?_shape=arrayfirst&sql=select+rowid+from+{}".format(
                    table
                )
            )
        ).json()[0]

    delete_response = await ds_write.client.post(
        "/data/{}/{}/-/delete".format(table, delete_path),
        headers=_headers(write_token(ds_write)),
    )
    assert delete_response.status_code == 200

    # Analytics event
    event = last_event(ds_write)
    assert event.name == "delete-row"
    assert event.database == "data"
    assert event.table == table
    assert event.pks == str(delete_path).split(",")
    assert (
        await ds_write.client.get(
            "/data/-/query.json?_shape=arrayfirst&sql=select+count(*)+from+{}".format(
                table
            )
        )
    ).json() == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ("no_token", "no_perm", "bad_table", "cannot_alter")
)
async def test_update_row_check_permission(ds_write, scenario):
    if scenario == "no_token":
        token = "bad_token"
    elif scenario == "no_perm":
        token = write_token(ds_write, actor_id="not-root")
    elif scenario == "cannot_alter":
        # update-row but no alter-table:
        token = write_token(ds_write, permissions=["ur"])
    else:
        token = write_token(ds_write)

    pk = await _insert_row(ds_write)

    path = "/data/{}/{}/-/update".format(
        "docs" if scenario != "bad_table" else "bad_table", pk
    )

    json_body = {"update": {"title": "New title"}}
    if scenario == "cannot_alter":
        json_body["alter"] = True

    response = await ds_write.client.post(
        path,
        json=json_body,
        headers=_headers(token),
    )
    assert response.status_code == 403 if scenario in ("no_token", "bad_token") else 404
    assert response.json()["ok"] is False
    assert (
        response.json()["errors"] == ["Permission denied"]
        if scenario == "no_token"
        else ["Table not found"]
    )


@pytest.mark.asyncio
async def test_update_row_invalid_key(ds_write):
    token = write_token(ds_write)

    pk = await _insert_row(ds_write)

    path = "/data/docs/{}/-/update".format(pk)
    response = await ds_write.client.post(
        path,
        json={"update": {"title": "New title"}, "bad_key": 1},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error": "Invalid keys: bad_key",
        "errors": ["Invalid keys: bad_key"],
        "status": 400,
    }


@pytest.mark.asyncio
async def test_update_row_alter(ds_write):
    token = write_token(ds_write, permissions=["ur", "at"])
    pk = await _insert_row(ds_write)
    path = "/data/docs/{}/-/update".format(pk)
    response = await ds_write.client.post(
        path,
        json={"update": {"title": "New title", "extra": "extra"}, "alter": True},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_alter_table_operations(ds_write):
    token = write_token(ds_write, permissions=["at"])
    db = ds_write.get_database("data")
    before_schema = await db.execute_fn(
        lambda conn: conn.execute(
            "select sql from sqlite_master where type = 'table' and name = 'docs'"
        ).fetchone()[0]
    )

    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json={
            "operations": [
                {
                    "op": "add_column",
                    "args": {
                        "name": "slug",
                        "type": "text",
                        "not_null": True,
                        "default": "",
                    },
                },
                {
                    "op": "add_column",
                    "args": {
                        "name": "created",
                        "type": "text",
                        "default_expr": "current_timestamp",
                    },
                },
                {
                    "op": "add_column",
                    "args": {
                        "name": "literal_default",
                        "type": "text",
                        "default": "hello)",
                    },
                },
                {"op": "rename_column", "args": {"name": "title", "to": "headline"}},
                {
                    "op": "alter_column",
                    "args": {"name": "age", "type": "text", "default": "0"},
                },
                {"op": "drop_column", "args": {"name": "score"}},
                {
                    "op": "reorder_columns",
                    "args": {
                        "columns": [
                            "id",
                            "headline",
                            "slug",
                            "created",
                            "literal_default",
                            "age",
                        ]
                    },
                },
                {"op": "set_primary_key", "args": {"columns": ["id"]}},
            ]
        },
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["database"] == "data"
    assert data["table"] == "docs"
    assert data["altered"] is True
    assert data["operations_applied"] == 8
    assert data["before_schema"] == before_schema
    assert "headline" in data["schema"]
    assert "score" not in data["schema"]
    assert "DEFAULT CURRENT_TIMESTAMP" in data["schema"]
    assert "DEFAULT 'hello)'" in data["schema"]

    columns = (
        await db.execute("select * from pragma_table_info('docs') order by cid")
    ).dicts()
    assert [column["name"] for column in columns] == [
        "id",
        "headline",
        "slug",
        "created",
        "literal_default",
        "age",
    ]
    assert columns[0]["pk"] == 1
    assert columns[2]["notnull"] == 1
    assert columns[2]["dflt_value"] == "''"
    assert columns[3]["dflt_value"] == "CURRENT_TIMESTAMP"
    assert columns[4]["dflt_value"] == "'hello)'"
    assert columns[5]["type"] == "TEXT"
    assert columns[5]["dflt_value"] == "'0'"

    event = last_event(ds_write)
    assert event.name == "alter-table"
    assert event.database == "data"
    assert event.table == "docs"
    assert event.before_schema == before_schema
    assert event.after_schema == data["schema"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "default_expr,minimum_value,expected_schema",
    (
        (
            "current_unixtime",
            1_600_000_000,
            "strftime('%s', 'now')",
        ),
        (
            "current_unixtime_ms",
            1_600_000_000_000,
            "julianday('now')",
        ),
    ),
)
async def test_alter_table_integer_default_expr(
    ds_write, default_expr, minimum_value, expected_schema
):
    token = write_token(ds_write, permissions=["at"])
    db = ds_write.get_database("data")
    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json={
            "operations": [
                {
                    "op": "add_column",
                    "args": {
                        "name": "created",
                        "type": "integer",
                        "default_expr": default_expr,
                    },
                }
            ]
        },
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert expected_schema in data["schema"]

    columns = await db.execute("select * from pragma_table_info('docs')")
    created_column = [
        column for column in columns.dicts() if column["name"] == "created"
    ][0]
    assert created_column["type"] == "INTEGER"
    assert expected_schema in created_column["dflt_value"]

    row = await db.execute_write_fn(
        lambda conn: _insert_and_fetch_created(
            conn, "docs", "insert into docs (title) values ('with default')"
        )
    )
    assert row[0] > minimum_value
    assert row[1] == "integer"


@pytest.mark.asyncio
async def test_alter_table_rename_table(ds_write):
    token = write_token(ds_write, permissions=["at"])
    db = ds_write.get_database("data")
    before_schema = await db.execute_fn(
        lambda conn: conn.execute(
            "select sql from sqlite_master where type = 'table' and name = 'docs'"
        ).fetchone()[0]
    )

    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json={
            "operations": [
                {"op": "rename_table", "args": {"to": "documents"}},
            ]
        },
        headers=_headers(token),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["database"] == "data"
    assert data["table"] == "documents"
    assert data["table_url"].endswith("/data/documents")
    assert data["table_api_url"].endswith("/data/documents.json")
    assert data["altered"] is True
    assert data["operations_applied"] == 1
    assert data["before_schema"] == before_schema
    assert 'CREATE TABLE "documents"' in data["schema"]

    tables = (
        await db.execute(
            "select name from sqlite_master where type = 'table' order by name"
        )
    ).dicts()
    table_names = [table["name"] for table in tables]
    assert "docs" not in table_names
    assert "documents" in table_names

    rename_events = [
        event
        for event in ds_write._tracked_events
        if isinstance(event, RenameTableEvent)
    ]
    assert len(rename_events) == 1
    assert rename_events[0].database == "data"
    assert rename_events[0].old_table == "docs"
    assert rename_events[0].new_table == "documents"


@pytest.mark.asyncio
async def test_alter_table_foreign_key_operations(ds_write):
    token = write_token(ds_write, permissions=["at"])
    db = ds_write.get_database("data")
    await db.execute_write("create table owners (id integer primary key)")
    await db.execute_write("create table categories (id integer primary key)")

    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json={
            "operations": [
                {"op": "add_column", "args": {"name": "owner_id", "type": "integer"}},
                {
                    "op": "add_foreign_key",
                    "args": {"column": "owner_id", "fk_table": "owners"},
                },
            ]
        },
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["operations_applied"] == 2
    assert_schema_contains(
        '"owner_id" INTEGER REFERENCES "owners"("id")', data["schema"]
    )

    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json={
            "operations": [{"op": "drop_foreign_key", "args": {"column": "owner_id"}}]
        },
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert_schema_not_contains('"owner_id" INTEGER REFERENCES', data["schema"])

    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json={
            "operations": [
                {
                    "op": "set_foreign_keys",
                    "args": {
                        "foreign_keys": [
                            {
                                "column": "owner_id",
                                "fk_table": "categories",
                                "fk_column": "id",
                            }
                        ]
                    },
                }
            ]
        },
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert_schema_contains(
        '"owner_id" INTEGER REFERENCES "categories"("id")', data["schema"]
    )

    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json={"operations": [{"op": "set_foreign_keys", "args": {"foreign_keys": []}}]},
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert_schema_not_contains('"owner_id" INTEGER REFERENCES', data["schema"])


@pytest.mark.asyncio
async def test_alter_table_foreign_key_requires_fk_table_for_fk_column(ds_write):
    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json={
            "operations": [
                {
                    "op": "add_foreign_key",
                    "args": {"column": "age", "fk_column": "id"},
                }
            ]
        },
        headers=_headers(write_token(ds_write, permissions=["at"])),
    )
    assert response.status_code == 400
    assert response.json() == error_body(
        ["operations.0.add_foreign_key.args: fk_column requires fk_table"], 400
    )


@pytest.mark.asyncio
async def test_alter_table_foreign_key_without_fk_column_requires_single_pk(ds_write):
    token = write_token(ds_write, permissions=["at"])
    db = ds_write.get_database("data")
    await db.execute_write(
        "create table accounts (tenant_id integer, id integer, primary key (tenant_id, id))"
    )

    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json={
            "operations": [
                {
                    "op": "add_foreign_key",
                    "args": {"column": "age", "fk_table": "accounts"},
                }
            ]
        },
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert response.json() == error_body(
        ["Could not detect single primary key for table 'accounts'"], 400
    )


@pytest.mark.asyncio
async def test_foreign_key_suggestions(ds_write):
    token = write_token(ds_write, permissions=["at"])
    db = ds_write.get_database("data")
    await db.execute_write("create table owners (id integer primary key)")
    await db.execute_write("insert into owners (id) values (1), (2), (3)")
    await db.execute_write("create table categories (slug text primary key)")
    await db.execute_write("insert into categories (slug) values ('one'), ('two')")
    await db.execute_write("create table numbers (id integer primary key)")
    await db.execute_write("insert into numbers (id) values (10), (20)")
    await db.execute_write("create table weights (id real primary key)")
    await db.execute_write("insert into weights (id) values (1.5), (2.5)")
    await db.execute_write(
        "insert into docs (id, title, score, age) values "
        "(1, 'one', 1.5, 1), (2, 'two', 999.5, 2), (3, null, null, null)"
    )

    response = await ds_write.client.get(
        "/data/docs/-/foreign-key-suggestions",
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["database"] == "data"
    assert data["table"] == "docs"
    assert data["row_check"]["attempted"] is True
    assert data["row_check"]["status"] == "completed"
    assert data["row_check"]["row_limit"] == 500
    assert data["row_check"]["sampled_rows"] == 3

    columns = {column["column"]: column for column in data["columns"]}
    assert columns["age"]["options"] == [
        {"fk_table": "numbers", "fk_column": "id", "type": "INTEGER"},
        {"fk_table": "owners", "fk_column": "id", "type": "INTEGER"},
    ]
    assert columns["age"]["suggestions"] == [
        {
            "fk_table": "owners",
            "fk_column": "id",
            "confidence": "sampled",
            "sampled_values": 2,
            "reasons": ["type_match", "sample_values_exist"],
        }
    ]
    assert columns["title"]["options"] == [
        {"fk_table": "categories", "fk_column": "slug", "type": "TEXT"}
    ]
    assert columns["title"]["suggestions"][0]["fk_table"] == "categories"
    assert columns["score"]["options"] == [
        {"fk_table": "weights", "fk_column": "id", "type": "REAL"}
    ]
    assert columns["score"]["suggestions"] == []


@pytest.mark.asyncio
async def test_foreign_key_suggestions_permission_denied(ds_write):
    token = write_token(ds_write, permissions=["ir"])
    response = await ds_write.client.get(
        "/data/docs/-/foreign-key-suggestions",
        headers=_headers(token),
    )
    assert response.status_code == 403
    assert response.json() == error_body(["Permission denied: need alter-table"], 403)


@pytest.mark.asyncio
async def test_foreign_key_suggestions_fail_open(ds_write, monkeypatch):
    token = write_token(ds_write, permissions=["at"])
    db = ds_write.get_database("data")
    await db.execute_write("create table owners (id integer primary key)")

    async def raise_timeout(*args, **kwargs):
        raise table_create_alter.ForeignKeySuggestionTimedOut

    from datasette.views import table_create_alter

    monkeypatch.setattr(
        table_create_alter,
        "_foreign_key_suggestion_samples",
        raise_timeout,
    )

    response = await ds_write.client.get(
        "/data/docs/-/foreign-key-suggestions",
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["row_check"]["status"] == "timed_out"
    columns = {column["column"]: column for column in data["columns"]}
    assert columns["age"]["options"] == [
        {"fk_table": "owners", "fk_column": "id", "type": "INTEGER"}
    ]
    assert columns["age"]["suggestions"] == []


@pytest.mark.asyncio
async def test_foreign_key_targets(ds_write):
    token = write_token(ds_write, permissions=["ct"])
    db = ds_write.get_database("data")
    await db.execute_write("create table owners (id integer primary key)")
    await db.execute_write("create table categories (slug varchar(30) primary key)")
    await db.execute_write("create table blob_things (hash blob primary key)")
    await db.execute_write(
        "create table numeric_codes (code decimal(10,5) primary key)"
    )
    await db.execute_write(
        'create table floating_point (value "FLOATING POINT" primary key)'
    )
    await db.execute_write(
        "create table compound (a integer, b integer, primary key (a, b))"
    )
    await db.execute_write("create table no_pk (name text)")
    try:
        await db.execute_write("create virtual table search_docs using fts5(body)")
    except Exception:
        pass

    response = await ds_write.client.get(
        "/data/-/foreign-key-targets",
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "database": "data",
        "targets": [
            {
                "fk_table": "blob_things",
                "fk_column": "hash",
                "type": "blob",
            },
            {
                "fk_table": "categories",
                "fk_column": "slug",
                "type": "text",
            },
            {
                "fk_table": "docs",
                "fk_column": "id",
                "type": "integer",
            },
            {
                "fk_table": "floating_point",
                "fk_column": "value",
                "type": "integer",
            },
            {
                "fk_table": "numeric_codes",
                "fk_column": "code",
                "type": "numeric",
            },
            {
                "fk_table": "owners",
                "fk_column": "id",
                "type": "integer",
            },
        ],
    }
    assert not any(
        target["fk_table"].startswith("search_docs_")
        for target in response.json()["targets"]
    )


@pytest.mark.asyncio
async def test_foreign_key_targets_permission_denied(ds_write):
    token = write_token(ds_write, permissions=["ir"])
    response = await ds_write.client.get(
        "/data/-/foreign-key-targets",
        headers=_headers(token),
    )
    assert response.status_code == 403
    assert response.json() == error_body(["Permission denied: need create-table"], 403)


@pytest.mark.asyncio
async def test_foreign_key_targets_allowed_for_alter_table(ds_write):
    token = write_token(ds_write, permissions=["at"])
    response = await ds_write.client.get(
        "/data/-/foreign-key-targets?table=docs",
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_alter_table_permission_denied(ds_write):
    token = write_token(ds_write, permissions=["ir"])
    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json={"operations": [{"op": "add_column", "args": {"name": "slug"}}]},
        headers=_headers(token),
    )
    assert response.status_code == 403
    assert response.json() == error_body(["Permission denied: need alter-table"], 403)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,expected_error",
    (
        (
            {
                "dry_run": True,
                "operations": [
                    {"op": "add_column", "args": {"name": "slug", "type": "text"}}
                ],
            },
            "dry_run: Extra inputs are not permitted",
        ),
        (
            {"operations": [{"op": "add_column", "args": {"type": "text"}}]},
            "operations.0.add_column.args.name: Field required",
        ),
        (
            {
                "operations": [
                    {"op": "add_column", "args": {"name": "x", "type": "bad"}}
                ]
            },
            "operations.0.add_column.args.type: Input should be 'text', 'integer', 'float' or 'blob'",
        ),
        (
            {
                "operations": [
                    {
                        "op": "add_column",
                        "args": {
                            "name": "x",
                            "default_expr": "datetime('now')",
                        },
                    }
                ]
            },
            "operations.0.add_column.args.default_expr: Input should be 'current_timestamp', 'current_date', 'current_time', 'current_unixtime' or 'current_unixtime_ms'",
        ),
        (
            {
                "operations": [
                    {
                        "op": "add_column",
                        "args": {
                            "name": "x",
                            "default": "x",
                            "default_expr": "current_timestamp",
                        },
                    }
                ]
            },
            "operations.0.add_column.args: Value error, default and default_expr cannot both be provided",
        ),
    ),
)
async def test_alter_table_validation_errors(ds_write, body, expected_error):
    response = await ds_write.client.post(
        "/data/docs/-/alter",
        json=body,
        headers=_headers(write_token(ds_write, permissions=["at"])),
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert response.json()["errors"] == [expected_error]


@pytest.mark.asyncio
async def test_execute_write_form_parameter_called_sql():
    ds = Datasette(memory=True, default_deny=True)
    ds.root_enabled = True
    db = ds.add_memory_database("execute_write_parameter_sql", name="data")
    await db.execute_write("create table docs (id integer primary key, title text)")
    await db.execute_write("insert into docs (id, title) values (1, 'Initial')")
    await ds.invoke_startup()

    form_response = await ds.client.get(
        "/data/-/execute-write",
        actor={"id": "root"},
        params={"sql": "update docs set title = :sql where id = :id"},
    )
    assert form_response.status_code == 200
    assert 'data-parameter-name-prefix="_sql_param_"' in form_response.text
    assert '<label for="qp1">sql</label>' in form_response.text
    assert 'name="_sql_param_sql"' in form_response.text
    assert 'data-parameter-name="sql"' in form_response.text
    assert 'name="_sql_param_id"' in form_response.text

    response = await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        data={
            "sql": "update docs set title = :sql where id = :id",
            "_sql_param_sql": "Updated",
            "_sql_param_id": "1",
        },
    )

    assert response.status_code == 200
    assert "Query executed, 1 row affected" in response.text
    assert (await db.execute("select title from docs where id = 1")).first()[
        0
    ] == "Updated"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input,expected_errors",
    (
        ({"title": "New title"}, None),
        ({"title": None}, None),
        ({"score": 1.6}, None),
        ({"age": 10}, None),
        ({"title": "New title", "score": 1.6}, None),
        ({"title2": "New title"}, ["no such column: title2"]),
    ),
)
@pytest.mark.parametrize("use_return", (True, False))
async def test_update_row(ds_write, input, expected_errors, use_return):
    token = write_token(ds_write)
    pk = await _insert_row(ds_write)

    path = "/data/docs/{}/-/update".format(pk)

    data = {"update": input}
    if use_return:
        data["return"] = True

    response = await ds_write.client.post(
        path,
        json=data,
        headers=_headers(token),
    )
    if expected_errors:
        assert response.status_code == 400
        assert response.json()["ok"] is False
        assert response.json()["errors"] == expected_errors
        return

    assert response.json()["ok"] is True
    if not use_return:
        assert "rows" not in response.json()
    else:
        returned_row = response.json()["rows"][0]
        assert returned_row["id"] == pk
        for k, v in input.items():
            assert returned_row[k] == v

    # Analytics event
    event = last_event(ds_write)
    assert event.actor == {"id": "root", "token": "dstok"}
    assert event.database == "data"
    assert event.table == "docs"
    assert event.pks == [str(pk)]

    # And fetch the row to check it's updated
    response = await ds_write.client.get(
        "/data/docs/{}.json?_shape=array".format(pk),
    )
    assert response.status_code == 200
    row = response.json()[0]
    assert row["id"] == pk
    for k, v in input.items():
        assert row[k] == v


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ("no_token", "no_perm", "bad_table", "has_perm", "immutable")
)
async def test_drop_table(ds_write, scenario):
    if scenario == "no_token":
        token = "bad_token"
    elif scenario == "no_perm":
        token = write_token(ds_write, actor_id="not-root")
    else:
        token = write_token(ds_write)
    should_work = scenario == "has_perm"
    await ds_write.get_database("data").execute_write(
        "insert into docs (id, title) values (1, 'Row 1')"
    )
    path = "/{database}/{table}/-/drop".format(
        database="immutable" if scenario == "immutable" else "data",
        table="docs" if scenario != "bad_table" else "bad_table",
    )
    response = await ds_write.client.post(
        path,
        headers=_headers(token),
    )
    if not should_work:
        assert (
            response.status_code == 403
            if scenario in ("no_token", "bad_token")
            else 404
        )
        assert response.json()["ok"] is False
        expected_error = "Permission denied"
        if scenario == "bad_table":
            expected_error = "Table not found"
        elif scenario == "immutable":
            expected_error = "Database is immutable"
        assert response.json()["errors"] == [expected_error]
        assert (await ds_write.client.get("/data/docs")).status_code == 200
    else:
        # It should show a confirmation page
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "database": "data",
            "table": "docs",
            "row_count": 1,
            "message": 'Pass "confirm": true to confirm',
        }
        assert (await ds_write.client.get("/data/docs")).status_code == 200
        # Now send confirm: true
        response2 = await ds_write.client.post(
            path,
            json={"confirm": True},
            headers=_headers(token),
        )
        assert response2.json() == {"ok": True}
        # Check event
        event = last_event(ds_write)
        assert event.name == "drop-table"
        assert event.actor == {"id": "root", "token": "dstok"}
        assert event.table == "docs"
        assert event.database == "data"
        # Table should 404
        assert (await ds_write.client.get("/data/docs")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input,expected_status,expected_response,expected_events",
    (
        # Permission error with a bad token
        (
            {"table": "bad", "row": {"id": 1}},
            403,
            {"ok": False, "errors": ["Permission denied"]},
            [],
        ),
        # Successful creation with columns:
        (
            {
                "table": "one",
                "columns": [
                    {
                        "name": "id",
                        "type": "integer",
                    },
                    {
                        "name": "title",
                        "type": "text",
                    },
                    {
                        "name": "score",
                        "type": "integer",
                    },
                    {
                        "name": "weight",
                        "type": "float",
                    },
                    {
                        "name": "thumbnail",
                        "type": "blob",
                    },
                ],
                "pk": "id",
            },
            201,
            {
                "ok": True,
                "database": "data",
                "table": "one",
                "table_url": "http://localhost/data/one",
                "table_api_url": "http://localhost/data/one.json",
                "schema": (
                    'CREATE TABLE "one" (\n'
                    '   "id" INTEGER PRIMARY KEY,\n'
                    '   "title" TEXT,\n'
                    '   "score" INTEGER,\n'
                    '   "weight" REAL,\n'
                    '   "thumbnail" BLOB\n'
                    ")"
                ),
            },
            ["create-table"],
        ),
        # Successful creation with rows:
        (
            {
                "table": "two",
                "rows": [
                    {
                        "id": 1,
                        "title": "Row 1",
                        "score": 1.5,
                    },
                    {
                        "id": 2,
                        "title": "Row 2",
                        "score": 1.5,
                    },
                ],
                "pk": "id",
            },
            201,
            {
                "ok": True,
                "database": "data",
                "table": "two",
                "table_url": "http://localhost/data/two",
                "table_api_url": "http://localhost/data/two.json",
                "schema": (
                    'CREATE TABLE "two" (\n'
                    '   "id" INTEGER PRIMARY KEY,\n'
                    '   "title" TEXT,\n'
                    '   "score" REAL\n'
                    ")"
                ),
                "row_count": 2,
            },
            ["create-table", "insert-rows"],
        ),
        # Successful creation with row:
        (
            {
                "table": "three",
                "row": {
                    "id": 1,
                    "title": "Row 1",
                    "score": 1.5,
                },
                "pk": "id",
            },
            201,
            {
                "ok": True,
                "database": "data",
                "table": "three",
                "table_url": "http://localhost/data/three",
                "table_api_url": "http://localhost/data/three.json",
                "schema": (
                    'CREATE TABLE "three" (\n'
                    '   "id" INTEGER PRIMARY KEY,\n'
                    '   "title" TEXT,\n'
                    '   "score" REAL\n'
                    ")"
                ),
                "row_count": 1,
            },
            ["create-table", "insert-rows"],
        ),
        # Create with row and no primary key
        (
            {
                "table": "four",
                "row": {
                    "name": "Row 1",
                },
            },
            201,
            {
                "ok": True,
                "database": "data",
                "table": "four",
                "table_url": "http://localhost/data/four",
                "table_api_url": "http://localhost/data/four.json",
                "schema": ('CREATE TABLE "four" (\n' '   "name" TEXT\n' ")"),
                "row_count": 1,
            },
            ["create-table", "insert-rows"],
        ),
        # Create table with compound primary key
        (
            {
                "table": "five",
                "row": {"type": "article", "key": 123, "title": "Article 1"},
                "pks": ["type", "key"],
            },
            201,
            {
                "ok": True,
                "database": "data",
                "table": "five",
                "table_url": "http://localhost/data/five",
                "table_api_url": "http://localhost/data/five.json",
                "schema": (
                    'CREATE TABLE "five" (\n   "type" TEXT,\n   "key" INTEGER,\n'
                    '   "title" TEXT,\n   PRIMARY KEY ("type", "key")\n)'
                ),
                "row_count": 1,
            },
            ["create-table", "insert-rows"],
        ),
        # Error: Table is required
        (
            {
                "row": {"id": 1},
            },
            400,
            {
                "ok": False,
                "errors": ["Table is required"],
            },
            [],
        ),
        # Error: Invalid table name
        (
            {
                "table": "sqlite_bad_name",
                "row": {"id": 1},
            },
            400,
            {
                "ok": False,
                "errors": ["Invalid table name"],
            },
            [],
        ),
        # Error: JSON must be an object
        (
            [],
            400,
            {
                "ok": False,
                "errors": ["JSON must be an object"],
            },
            [],
        ),
        # Error: Cannot specify columns with rows or row
        (
            {
                "table": "bad",
                "columns": [{"name": "id", "type": "integer"}],
                "rows": [{"id": 1}],
            },
            400,
            {
                "ok": False,
                "errors": ["Cannot specify columns with rows or row"],
            },
            [],
        ),
        # Error: columns, rows or row is required
        (
            {
                "table": "bad",
            },
            400,
            {
                "ok": False,
                "errors": ["columns, rows or row is required"],
            },
            [],
        ),
        # Error: columns must be a list
        (
            {
                "table": "bad",
                "columns": {"name": "id", "type": "integer"},
            },
            400,
            {
                "ok": False,
                "errors": ["columns must be a list"],
            },
            [],
        ),
        # Error: columns must be a list of objects
        (
            {
                "table": "bad",
                "columns": ["id"],
            },
            400,
            {
                "ok": False,
                "errors": ["columns must be a list of objects"],
            },
            [],
        ),
        # Error: Column name is required
        (
            {
                "table": "bad",
                "columns": [{"type": "integer"}],
            },
            400,
            {
                "ok": False,
                "errors": ["Column name is required"],
            },
            [],
        ),
        # Error: Unsupported column type
        (
            {
                "table": "bad",
                "columns": [{"name": "id", "type": "bad"}],
            },
            400,
            {
                "ok": False,
                "errors": ["Unsupported column type: bad"],
            },
            [],
        ),
        # Error: Duplicate column name
        (
            {
                "table": "bad",
                "columns": [
                    {"name": "id", "type": "integer"},
                    {"name": "id", "type": "integer"},
                ],
            },
            400,
            {
                "ok": False,
                "errors": ["Duplicate column name: id"],
            },
            [],
        ),
        # Error: rows must be a list
        (
            {
                "table": "bad",
                "rows": {"id": 1},
            },
            400,
            {
                "ok": False,
                "errors": ["rows must be a list"],
            },
            [],
        ),
        # Error: rows must be a list of objects
        (
            {
                "table": "bad",
                "rows": ["id"],
            },
            400,
            {
                "ok": False,
                "errors": ["rows must be a list of objects"],
            },
            [],
        ),
        # Error: pk must be a string
        (
            {
                "table": "bad",
                "row": {"id": 1},
                "pk": 1,
            },
            400,
            {
                "ok": False,
                "errors": ["pk must be a string"],
            },
            [],
        ),
        # Error: Cannot specify both pk and pks
        (
            {
                "table": "bad",
                "row": {"id": 1, "name": "Row 1"},
                "pk": "id",
                "pks": ["id", "name"],
            },
            400,
            {
                "ok": False,
                "errors": ["Cannot specify both pk and pks"],
            },
            [],
        ),
        # Error: pks must be a list
        (
            {
                "table": "bad",
                "row": {"id": 1, "name": "Row 1"},
                "pks": "id",
            },
            400,
            {
                "ok": False,
                "errors": ["pks must be a list"],
            },
            [],
        ),
        # Error: pks must be a list of strings
        (
            {"table": "bad", "row": {"id": 1, "name": "Row 1"}, "pks": [1, 2]},
            400,
            {"ok": False, "errors": ["pks must be a list of strings"]},
            [],
        ),
        # Error: ignore and replace are mutually exclusive
        (
            {
                "table": "bad",
                "row": {"id": 1, "name": "Row 1"},
                "pk": "id",
                "ignore": True,
                "replace": True,
            },
            400,
            {
                "ok": False,
                "errors": ["ignore and replace are mutually exclusive"],
            },
            [],
        ),
        # ignore and replace require row or rows
        (
            {
                "table": "bad",
                "columns": [{"name": "id", "type": "integer"}],
                "ignore": True,
            },
            400,
            {
                "ok": False,
                "errors": ["ignore and replace require row or rows"],
            },
            [],
        ),
        # ignore and replace require pk or pks
        (
            {
                "table": "bad",
                "row": {"id": 1},
                "ignore": True,
            },
            400,
            {
                "ok": False,
                "errors": ["ignore and replace require pk or pks"],
            },
            [],
        ),
        (
            {
                "table": "bad",
                "row": {"id": 1},
                "replace": True,
            },
            400,
            {
                "ok": False,
                "errors": ["ignore and replace require pk or pks"],
            },
            [],
        ),
    ),
)
async def test_create_table(
    ds_write, input, expected_status, expected_response, expected_events
):
    ds_write._tracked_events = []
    # Special case for expected status of 403
    if expected_status == 403:
        token = "bad_token"
    else:
        token = write_token(ds_write)
    response = await ds_write.client.post(
        "/data/-/create",
        json=input,
        headers=_headers(token),
    )
    assert response.status_code == expected_status
    data = response.json()
    if expected_response.get("ok") is False:
        # Error expectations list their messages; derive the canonical envelope
        expected_response = error_body(expected_response["errors"], expected_status)
    if isinstance(expected_response, dict) and "schema" in expected_response:
        assert data.get("schema") == expected_response["schema"]
        expected_response = dict(expected_response, schema=data.get("schema"))
    assert data == expected_response
    # Should have tracked the expected events
    events = ds_write._tracked_events
    assert [e.name for e in events] == expected_events


@pytest.mark.asyncio
async def test_create_table_with_foreign_key(ds_write):
    token = write_token(ds_write)
    response = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": "owners",
            "columns": [
                {"name": "id", "type": "integer"},
                {"name": "name", "type": "text"},
            ],
            "pk": "id",
        },
        headers=_headers(token),
    )
    assert response.status_code == 201

    response = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": "projects",
            "columns": [
                {"name": "id", "type": "integer"},
                {
                    "name": "owner_id",
                    "type": "integer",
                    "fk_table": "owners",
                },
                {"name": "title", "type": "text"},
            ],
            "pk": "id",
        },
        headers=_headers(token),
    )
    assert response.status_code == 201
    data = response.json()
    assert_schema_contains(
        '"owner_id" INTEGER REFERENCES "owners"("id")', data["schema"]
    )


@pytest.mark.asyncio
async def test_create_table_with_column_constraints(ds_write):
    token = write_token(ds_write)
    response = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": "constrained",
            "columns": [
                {"name": "id", "type": "integer"},
                {
                    "name": "title",
                    "type": "text",
                    "not_null": True,
                    "default": "Untitled",
                },
                {
                    "name": "created",
                    "type": "text",
                    "default_expr": "current_timestamp",
                },
                {"name": "score", "type": "integer", "default": 0},
                {"name": "literal_default", "type": "text", "default": "hello)"},
            ],
            "pk": "id",
        },
        headers=_headers(token),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["ok"] is True
    assert "NOT NULL DEFAULT 'Untitled'" in data["schema"]
    assert "DEFAULT CURRENT_TIMESTAMP" in data["schema"]
    assert "DEFAULT 0" in data["schema"]
    assert "DEFAULT 'hello)'" in data["schema"]

    db = ds_write.get_database("data")
    columns = (
        await db.execute("select * from pragma_table_info('constrained') order by cid")
    ).dicts()
    assert [column["name"] for column in columns] == [
        "id",
        "title",
        "created",
        "score",
        "literal_default",
    ]
    assert columns[0]["pk"] == 1
    assert columns[1]["notnull"] == 1
    assert columns[1]["dflt_value"] == "'Untitled'"
    assert columns[2]["dflt_value"] == "CURRENT_TIMESTAMP"
    assert columns[3]["dflt_value"] == "0"
    assert columns[4]["dflt_value"] == "'hello)'"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "default_expr,minimum_value,expected_schema",
    (
        (
            "current_unixtime",
            1_600_000_000,
            "strftime('%s', 'now')",
        ),
        (
            "current_unixtime_ms",
            1_600_000_000_000,
            "julianday('now')",
        ),
    ),
)
async def test_create_table_integer_default_expr(
    ds_write, default_expr, minimum_value, expected_schema
):
    token = write_token(ds_write)
    table = "default_{}".format(default_expr)
    response = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": table,
            "columns": [
                {"name": "id", "type": "integer"},
                {
                    "name": "created",
                    "type": "integer",
                    "default_expr": default_expr,
                },
            ],
            "pk": "id",
        },
        headers=_headers(token),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert expected_schema in data["schema"]

    db = ds_write.get_database("data")
    columns = (await db.execute("select * from pragma_table_info(?)", [table])).dicts()
    assert columns[1]["type"] == "INTEGER"
    assert expected_schema in columns[1]["dflt_value"]

    row = await db.execute_write_fn(
        lambda conn: _insert_and_fetch_created(
            conn, table, "insert into {} default values".format(escape_sqlite(table))
        )
    )
    assert row[0] > minimum_value
    assert row[1] == "integer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "column,expected_error",
    (
        (
            {"name": "owner_id", "type": "integer", "fk_table": "owners"},
            None,
        ),
        (
            {"name": "owner_id", "type": "integer", "fk_column": "id"},
            "columns.0: fk_column requires fk_table",
        ),
        (
            {
                "name": "created",
                "type": "text",
                "default_expr": "datetime('now')",
            },
            "columns.0.default_expr: Input should be 'current_timestamp', 'current_date', 'current_time', 'current_unixtime' or 'current_unixtime_ms'",
        ),
        (
            {
                "name": "created",
                "type": "text",
                "default": "x",
                "default_expr": "current_timestamp",
            },
            "columns.0: Value error, default and default_expr cannot both be provided",
        ),
    ),
)
async def test_create_table_column_validation(ds_write, column, expected_error):
    token = write_token(ds_write)
    response = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": "projects",
            "columns": [column],
        },
        headers=_headers(token),
    )
    if expected_error:
        assert response.status_code == 400
        assert response.json() == error_body([expected_error], 400)
    else:
        assert response.status_code == 400
        assert response.json() == error_body(
            ["Could not detect single primary key for table 'owners'"], 400
        )


@pytest.mark.asyncio
async def test_create_table_foreign_key_without_fk_column_requires_single_pk(ds_write):
    token = write_token(ds_write)
    response = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": "accounts",
            "columns": [
                {"name": "tenant_id", "type": "integer"},
                {"name": "id", "type": "integer"},
                {"name": "name", "type": "text"},
            ],
            "pks": ["tenant_id", "id"],
        },
        headers=_headers(token),
    )
    assert response.status_code == 201

    response = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": "projects",
            "columns": [
                {"name": "id", "type": "integer"},
                {
                    "name": "account_id",
                    "type": "integer",
                    "fk_table": "accounts",
                },
            ],
            "pk": "id",
        },
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert response.json() == error_body(
        ["Could not detect single primary key for table 'accounts'"], 400
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permissions,body,expected_status,expected_errors",
    (
        (["create-table"], {"table": "t", "columns": [{"name": "c"}]}, 201, None),
        # Need insert-row too if you use "rows":
        (
            ["create-table"],
            {"table": "t", "rows": [{"name": "c"}]},
            403,
            ["Permission denied: need insert-row"],
        ),
        # This should work:
        (
            ["create-table", "insert-row"],
            {"table": "t", "rows": [{"name": "c"}]},
            201,
            None,
        ),
        # If you use replace: true you need update-row too:
        (
            ["create-table", "insert-row"],
            {"table": "t", "rows": [{"id": 1}], "pk": "id", "replace": True},
            403,
            ["Permission denied: need update-row"],
        ),
    ),
)
async def test_create_table_permissions(
    ds_write, permissions, body, expected_status, expected_errors
):
    from datasette.tokens import TokenRestrictions

    restrictions = TokenRestrictions()
    for action in ["view-instance"] + permissions:
        restrictions.allow_all(action)
    token = await ds_write.create_token(
        "root", handler="signed", restrictions=restrictions
    )
    response = await ds_write.client.post(
        "/data/-/create",
        json=body,
        headers=_headers(token),
    )
    assert response.status_code == expected_status
    if expected_errors:
        data = response.json()
        assert data["ok"] is False
        assert data["errors"] == expected_errors


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Flaky, see https://github.com/simonw/datasette/issues/2356")
@pytest.mark.parametrize(
    "input,expected_rows_after",
    (
        (
            {
                "table": "test_insert_replace",
                "rows": [
                    {"id": 1, "name": "Row 1 new"},
                    {"id": 3, "name": "Row 3 new"},
                ],
                "pk": "id",
                "ignore": True,
            },
            [
                {"id": 1, "name": "Row 1"},
                {"id": 2, "name": "Row 2"},
                {"id": 3, "name": "Row 3 new"},
            ],
        ),
        (
            {
                "table": "test_insert_replace",
                "rows": [
                    {"id": 1, "name": "Row 1 new"},
                    {"id": 3, "name": "Row 3 new"},
                ],
                "pk": "id",
                "replace": True,
            },
            [
                {"id": 1, "name": "Row 1 new"},
                {"id": 2, "name": "Row 2"},
                {"id": 3, "name": "Row 3 new"},
            ],
        ),
    ),
)
async def test_create_table_ignore_replace(ds_write, input, expected_rows_after):
    # Create table with two rows
    token = write_token(ds_write)
    first_response = await ds_write.client.post(
        "/data/-/create",
        json={
            "rows": [{"id": 1, "name": "Row 1"}, {"id": 2, "name": "Row 2"}],
            "table": "test_insert_replace",
            "pk": "id",
        },
        headers=_headers(token),
    )
    assert first_response.status_code == 201

    ds_write._tracked_events = []

    # Try a second time
    second_response = await ds_write.client.post(
        "/data/-/create",
        json=input,
        headers=_headers(token),
    )
    assert second_response.status_code == 201
    # Check that the rows are as expected
    rows = await ds_write.client.get("/data/test_insert_replace.json?_shape=array")
    assert rows.json() == expected_rows_after

    # Check it fired the right events
    event_names = [e.name for e in ds_write._tracked_events]
    assert event_names == ["insert-rows"]


@pytest.mark.asyncio
async def test_create_table_error_if_pk_changed(ds_write):
    token = write_token(ds_write)
    first_response = await ds_write.client.post(
        "/data/-/create",
        json={
            "rows": [{"id": 1, "name": "Row 1"}, {"id": 2, "name": "Row 2"}],
            "table": "test_insert_replace",
            "pk": "id",
        },
        headers=_headers(token),
    )
    assert first_response.status_code == 201
    # Try a second time with a different pk
    second_response = await ds_write.client.post(
        "/data/-/create",
        json={
            "rows": [{"id": 1, "name": "Row 1"}, {"id": 2, "name": "Row 2"}],
            "table": "test_insert_replace",
            "pk": "name",
            "replace": True,
        },
        headers=_headers(token),
    )
    assert second_response.status_code == 400
    assert second_response.json() == error_body(
        ["pk cannot be changed for existing table"], 400
    )


@pytest.mark.asyncio
async def test_create_table_error_rows_twice_with_duplicates(ds_write):
    # Error if you don't send ignore: True or replace: True
    token = write_token(ds_write)
    input = {
        "rows": [{"id": 1, "name": "Row 1"}, {"id": 2, "name": "Row 2"}],
        "table": "test_create_twice",
        "pk": "id",
    }
    first_response = await ds_write.client.post(
        "/data/-/create",
        json=input,
        headers=_headers(token),
    )
    assert first_response.status_code == 201
    second_response = await ds_write.client.post(
        "/data/-/create",
        json=input,
        headers=_headers(token),
    )
    assert second_response.status_code == 400
    assert second_response.json() == error_body(
        ["UNIQUE constraint failed: test_create_twice.id"], 400
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/data/-/create",
        "/data/docs/-/drop",
        "/data/docs/-/insert",
    ),
)
async def test_method_not_allowed(ds_write, path):
    response = await ds_write.client.get(
        path,
        headers={
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 405
    assert response.json() == {
        "ok": False,
        "error": "Method not allowed",
        "errors": ["Method not allowed"],
        "status": 405,
    }


@pytest.mark.asyncio
async def test_create_uses_alter_by_default_for_new_table(ds_write):
    ds_write._tracked_events = []
    token = write_token(ds_write)
    response = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": "new_table",
            "rows": [
                {
                    "name": "Row 1",
                }
            ]
            * 100
            + [
                {"name": "Row 2", "extra": "Extra"},
            ],
            "pk": "id",
        },
        headers=_headers(token),
    )
    assert response.status_code == 201
    event_names = [e.name for e in ds_write._tracked_events]
    assert event_names == ["create-table", "insert-rows"]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_alter_permission", (True, False))
async def test_create_using_alter_against_existing_table(
    ds_write, has_alter_permission
):
    token = write_token(
        ds_write, permissions=["ir", "ct"] + (["at"] if has_alter_permission else [])
    )
    # First create the table
    response = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": "new_table",
            "rows": [
                {
                    "name": "Row 1",
                }
            ],
            "pk": "id",
        },
        headers=_headers(token),
    )
    assert response.status_code == 201

    ds_write._tracked_events = []
    # Now try to insert more rows using /-/create with alter=True
    response2 = await ds_write.client.post(
        "/data/-/create",
        json={
            "table": "new_table",
            "rows": [{"name": "Row 2", "extra": "extra"}],
            "pk": "id",
            "alter": True,
        },
        headers=_headers(token),
    )
    if not has_alter_permission:
        assert response2.status_code == 403
        assert response2.json() == error_body(
            ["Permission denied: need alter-table"], 403
        )
    else:
        assert response2.status_code == 201

        event_names = [e.name for e in ds_write._tracked_events]
        assert event_names == ["alter-table", "insert-rows"]

        # It should have altered the table
        alter_event = ds_write._tracked_events[0]
        assert alter_event.name == "alter-table"
        assert "extra" not in alter_event.before_schema
        assert "extra" in alter_event.after_schema

        insert_rows_event = ds_write._tracked_events[1]
        assert insert_rows_event.name == "insert-rows"
        assert insert_rows_event.num_rows == 1


@pytest.mark.asyncio
async def test_row_page_with_binary_primary_key(ds_write):
    # Regression test for #2419 - a row with a binary (BLOB) primary key value
    # should be reachable via its generated URL instead of returning a 404.
    db = ds_write.get_database("data")
    await db.execute_write(
        "create table binary_pk (id integer, term blob, primary key (id, term))"
    )
    terms = (b"0thei'", b"\xff\x00abc")
    for term in terms:
        await db.execute_write(
            "insert into binary_pk (id, term) values (?, ?)", [3, term]
        )
    for term in terms:
        path = path_from_row_pks(
            {"id": 3, "term": term}, ["id", "term"], use_rowid=False
        )
        html = await ds_write.client.get(f"/data/binary_pk/{path}")
        assert html.status_code == 200, html.text
        js = await ds_write.client.get(f"/data/binary_pk/{path}.json?_shape=array")
        assert js.status_code == 200, js.text
        assert len(js.json()) == 1
