"""
Tests for the datasette.app.Datasette class
"""
from datasette.app import Datasette, Database
from itsdangerous import BadSignature
from .fixtures import app_client
import pytest


@pytest.fixture
def datasette(app_client):
    return app_client.ds


def test_get_database(datasette):
    db = datasette.get_database("fixtures")
    assert "fixtures" == db.name
    with pytest.raises(KeyError):
        datasette.get_database("missing")


def test_get_database_no_argument(datasette):
    # Returns the first available database:
    db = datasette.get_database()
    assert "fixtures" == db.name


@pytest.mark.parametrize("value", ["hello", 123, {"key": "value"}])
@pytest.mark.parametrize("namespace", [None, "two"])
def test_sign_unsign(datasette, value, namespace):
    extra_args = [namespace] if namespace else []
    signed = datasette.sign(value, *extra_args)
    assert value != signed
    assert value == datasette.unsign(signed, *extra_args)
    with pytest.raises(BadSignature):
        datasette.unsign(signed[:-1] + ("!" if signed[-1] != "!" else ":"))


@pytest.mark.parametrize(
    "setting,expected",
    (
        ("base_url", "/"),
        ("max_csv_mb", 100),
        ("allow_csv_stream", True),
    ),
)
def test_datasette_setting(datasette, setting, expected):
    assert datasette.setting(setting) == expected


@pytest.mark.asyncio
async def test_datasette_constructor():
    ds = Datasette()
    databases = (await ds.client.get("/-/databases.json")).json()
    assert databases == [
        {
            "name": "_memory",
            "route": "_memory",
            "path": None,
            "size": 0,
            "is_mutable": True,
            "is_memory": True,
            "hash": None,
        }
    ]


@pytest.mark.asyncio
async def test_num_sql_threads_zero():
    ds = Datasette([], memory=True, settings={"num_sql_threads": 0})
    db = ds.add_database(Database(ds, memory_name="test_num_sql_threads_zero"))
    await db.execute_write("create table t(id integer primary key)")
    await db.execute_write("insert into t (id) values (1)")
    response = await ds.client.get("/-/threads.json")
    assert response.json() == {"num_threads": 0, "threads": []}
    response2 = await ds.client.get("/test_num_sql_threads_zero/t.json?_shape=array")
    assert response2.json() == [{"id": 1}]
