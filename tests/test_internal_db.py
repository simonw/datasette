from .fixtures import app_client
import pytest


def test_internal_only_available_to_root(app_client):
    cookie = app_client.actor_cookie({"id": "root"})
    assert app_client.get("/_internal").status == 403
    assert app_client.get("/_internal", cookies={"ds_actor": cookie}).status == 200


def test_internal_databases(app_client):
    cookie = app_client.actor_cookie({"id": "root"})
    databases = app_client.get(
        "/_internal/databases.json?_shape=array", cookies={"ds_actor": cookie}
    ).json
    assert len(databases) == 2
    assert databases[0]["database_name"] == "_internal"
    assert databases[1]["database_name"] == "fixtures"


def test_internal_tables(app_client):
    cookie = app_client.actor_cookie({"id": "root"})
    tables = app_client.get(
        "/_internal/tables.json?_shape=array", cookies={"ds_actor": cookie}
    ).json
    assert len(tables) > 5
    table = tables[0]
    assert set(table.keys()) == {"rootpage", "table_name", "database_name", "sql"}


def test_internal_indexes(app_client):
    cookie = app_client.actor_cookie({"id": "root"})
    indexes = app_client.get(
        "/_internal/indexes.json?_shape=array", cookies={"ds_actor": cookie}
    ).json
    assert len(indexes) > 5
    index = indexes[0]
    assert set(index.keys()) == {
        "partial",
        "name",
        "table_name",
        "unique",
        "seq",
        "database_name",
        "origin",
    }


def test_internal_foreign_keys(app_client):
    cookie = app_client.actor_cookie({"id": "root"})
    foreign_keys = app_client.get(
        "/_internal/foreign_keys.json?_shape=array", cookies={"ds_actor": cookie}
    ).json
    assert len(foreign_keys) > 5
    foreign_key = foreign_keys[0]
    assert set(foreign_key.keys()) == {
        "table",
        "seq",
        "on_update",
        "on_delete",
        "to",
        "id",
        "match",
        "database_name",
        "table_name",
        "from",
    }
