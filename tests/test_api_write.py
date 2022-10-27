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
    token = "dstok_{}".format(
        ds_write.sign(
            {"a": "root", "token": "dstok", "t": int(time.time())}, namespace="token"
        )
    )
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
