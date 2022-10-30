from datasette.app import Datasette
from datasette.utils import sqlite3
import pytest
import time


@pytest.fixture
def ds_write(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute("create table docs (id integer primary key, title text, score float)")
    ds = Datasette([db_path])
    yield ds
    db.close()


def write_token(ds, actor_id="root"):
    return "dstok_{}".format(
        ds.sign(
            {"a": actor_id, "token": "dstok", "t": int(time.time())}, namespace="token"
        )
    )


@pytest.mark.asyncio
async def test_write_row(ds_write):
    token = write_token(ds_write)
    response = await ds_write.client.post(
        "/data/docs/-/insert",
        json={"row": {"title": "Test", "score": 1.0}},
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json",
        },
    )
    expected_row = {"id": 1, "title": "Test", "score": 1.0}
    assert response.status_code == 201
    assert response.json()["inserted"] == [expected_row]
    rows = (await ds_write.get_database("data").execute("select * from docs")).rows
    assert dict(rows[0]) == expected_row


@pytest.mark.asyncio
@pytest.mark.parametrize("return_rows", (True, False))
async def test_write_rows(ds_write, return_rows):
    token = write_token(ds_write)
    data = {"rows": [{"title": "Test {}".format(i), "score": 1.0} for i in range(20)]}
    if return_rows:
        data["return_rows"] = True
    response = await ds_write.client.post(
        "/data/docs/-/insert",
        json=data,
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 201
    actual_rows = [
        dict(r)
        for r in (
            await ds_write.get_database("data").execute("select * from docs")
        ).rows
    ]
    assert len(actual_rows) == 20
    assert actual_rows == [
        {"id": i + 1, "title": "Test {}".format(i), "score": 1.0} for i in range(20)
    ]
    assert response.json()["ok"] is True
    if return_rows:
        assert response.json()["inserted"] == actual_rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,input,special_case,expected_status,expected_errors",
    (
        (
            "/data2/docs/-/insert",
            {},
            None,
            404,
            ["Database not found: data2"],
        ),
        (
            "/data/docs2/-/insert",
            {},
            None,
            404,
            ["Table not found: docs2"],
        ),
        (
            "/data/docs/-/insert",
            {"rows": [{"title": "Test"} for i in range(10)]},
            "bad_token",
            403,
            ["Permission denied"],
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
            {},
            "invalid_content_type",
            400,
            ["Invalid content-type, must be application/json"],
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
    ),
)
async def test_write_row_errors(
    ds_write, path, input, special_case, expected_status, expected_errors
):
    token = write_token(ds_write)
    if special_case == "bad_token":
        token += "bad"
    kwargs = dict(
        json=input,
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "text/plain"
            if special_case == "invalid_content_type"
            else "application/json",
        },
    )
    if special_case == "invalid_json":
        del kwargs["json"]
        kwargs["content"] = "{bad json"
    response = await ds_write.client.post(
        path,
        **kwargs,
    )
    assert response.status_code == expected_status
    assert response.json()["ok"] is False
    assert response.json()["errors"] == expected_errors


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ("no_token", "no_perm", "bad_table", "has_perm"))
async def test_delete_row(ds_write, scenario):
    if scenario == "no_token":
        token = "bad_token"
    elif scenario == "no_perm":
        token = write_token(ds_write, actor_id="not-root")
    else:
        token = write_token(ds_write)
    should_work = scenario == "has_perm"

    # Insert a row
    insert_response = await ds_write.client.post(
        "/data/docs/-/insert",
        json={"row": {"title": "Row one", "score": 1.0}, "return_rows": True},
        headers={
            "Authorization": "Bearer {}".format(write_token(ds_write)),
            "Content-Type": "application/json",
        },
    )
    assert insert_response.status_code == 201
    pk = insert_response.json()["inserted"][0]["id"]

    path = "/data/{}/{}/-/delete".format(
        "docs" if scenario != "bad_table" else "bad_table", pk
    )
    response = await ds_write.client.post(
        path,
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json",
        },
    )
    if should_work:
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert (await ds_write.client.get("/data/docs.json?_shape=array")).json() == []
    else:
        assert (
            response.status_code == 403
            if scenario in ("no_token", "bad_token")
            else 404
        )
        assert response.json()["ok"] is False
        assert (
            response.json()["errors"] == ["Permission denied"]
            if scenario == "no_token"
            else ["Table not found: bad_table"]
        )
        assert (
            len((await ds_write.client.get("/data/docs.json?_shape=array")).json()) == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ("no_token", "no_perm", "bad_table", "has_perm"))
async def test_drop_table(ds_write, scenario):
    if scenario == "no_token":
        token = "bad_token"
    elif scenario == "no_perm":
        token = write_token(ds_write, actor_id="not-root")
    else:
        token = write_token(ds_write)
    should_work = scenario == "has_perm"

    path = "/data/{}/-/drop".format("docs" if scenario != "bad_table" else "bad_table")
    response = await ds_write.client.post(
        path,
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json",
        },
    )
    if should_work:
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert (await ds_write.client.get("/data/docs")).status_code == 404
    else:
        assert (
            response.status_code == 403
            if scenario in ("no_token", "bad_token")
            else 404
        )
        assert response.json()["ok"] is False
        assert (
            response.json()["errors"] == ["Permission denied"]
            if scenario == "no_token"
            else ["Table not found: bad_table"]
        )
        assert (await ds_write.client.get("/data/docs")).status_code == 200
