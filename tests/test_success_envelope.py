"""
Tests for the canonical JSON success envelope.

Every JSON object returned by a Datasette endpoint on success should include
"ok": true. (Endpoints that return a top-level array are being converted to
objects separately - see /-/plugins, /-/databases, /-/actions.)
"""

import pytest
from datasette.app import Datasette
from datasette.utils import sqlite3


@pytest.fixture
def ds_envelope(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    conn = sqlite3.connect(db_path)
    conn.execute("vacuum")
    conn.execute("create table docs (id integer primary key, title text)")
    conn.close()
    ds = Datasette([db_path])
    ds.root_enabled = True
    yield ds
    ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/.json",
        "/-/.json",
        "/-/versions.json",
        "/-/settings.json",
        "/-/config.json",
        "/-/actor.json",
        "/-/jump.json",
        "/-/schema.json",
        "/fixtures/-/schema.json",
        "/fixtures/facetable/-/schema.json",
        "/-/allowed.json?action=view-instance",
        "/fixtures/facet_cities/-/autocomplete?_initial=1",
    ),
)
async def test_success_object_has_ok_true(ds_client, path):
    response = await ds_client.get(path)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert data["ok"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/-/rules.json?action=view-instance",
        "/-/check.json?action=view-instance",
        "/-/threads.json",
    ),
)
async def test_permission_debug_success_has_ok_true(ds_envelope, path):
    response = await ds_envelope.client.get(path, actor={"id": "root"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_permissions_post_success_has_ok_true(ds_envelope):
    response = await ds_envelope.client.post(
        "/-/permissions",
        data={"actor": '{"id": "root"}', "permission": "view-instance"},
        actor={"id": "root"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_plugins_json_is_object(ds_client):
    response = await ds_client.get("/-/plugins.json")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"ok", "plugins"}
    assert data["ok"] is True
    assert isinstance(data["plugins"], list)
    # ?all=1 should include Datasette's default plugins in the same shape
    response_all = await ds_client.get("/-/plugins.json?all=1")
    all_plugins = response_all.json()["plugins"]
    assert len(all_plugins) > len(data["plugins"])


@pytest.mark.asyncio
async def test_databases_json_is_object(ds_client):
    response = await ds_client.get("/-/databases.json")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"ok", "databases"}
    assert data["ok"] is True
    assert isinstance(data["databases"], list)
    assert "fixtures" in {db["name"] for db in data["databases"]}


@pytest.mark.asyncio
async def test_actions_json_is_object(ds_envelope):
    response = await ds_envelope.client.get("/-/actions.json", actor={"id": "root"})
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"ok", "actions"}
    assert data["ok"] is True
    assert isinstance(data["actions"], list)
    assert "view-instance" in {action["name"] for action in data["actions"]}
