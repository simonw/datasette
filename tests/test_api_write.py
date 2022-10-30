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


def write_token(ds):
    return "dstok_{}".format(
        ds.sign(
            {"a": "root", "token": "dstok", "t": int(time.time())}, namespace="token"
        )
    )
