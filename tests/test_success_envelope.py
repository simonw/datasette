"""
Tests for the canonical JSON success envelope.

Every JSON object returned by a Datasette endpoint on success should include
"ok": true. /-/plugins intentionally returns a top-level array instead, while
/-/databases and /-/actions use the object envelope.
"""

import pytest
from datasette.app import Datasette
from datasette.utils import sqlite3, UNSTABLE_API_MESSAGE


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
async def test_plugins_json_is_array(ds_client):
    response = await ds_client.get("/-/plugins.json")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert all(isinstance(plugin, dict) for plugin in data)
    # ?all=1 should include Datasette's default plugins in the same shape
    response_all = await ds_client.get("/-/plugins.json?all=1")
    all_plugins = response_all.json()
    assert len(all_plugins) > len(data)


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/.json",
        "/-/.json",
        "/fixtures/-/queries/analyze?sql=select+1",
        "/fixtures/-/query/parameters?sql=select+:name",
        "/fixtures/-/execute-write/analyze?sql=delete+from+facetable",
    ),
)
async def test_undocumented_endpoints_report_unstable(ds_client, path):
    ds_client.ds.root_enabled = True
    try:
        response = await ds_client.get(path, actor={"id": "root"})
    finally:
        ds_client.ds.root_enabled = False
    assert response.status_code == 200
    assert response.json()["unstable"] == UNSTABLE_API_MESSAGE


@pytest.mark.asyncio
async def test_query_store_and_definition_report_unstable(ds_envelope):
    store = await ds_envelope.client.post(
        "/data/-/queries/store",
        json={"query": {"name": "unstable_check", "sql": "select 1"}},
        actor={"id": "root"},
    )
    assert store.status_code == 201
    assert store.json()["unstable"] == UNSTABLE_API_MESSAGE
    definition = await ds_envelope.client.get(
        "/data/unstable_check/-/definition", actor={"id": "root"}
    )
    assert definition.status_code == 200
    assert definition.json()["unstable"] == UNSTABLE_API_MESSAGE


@pytest.mark.asyncio
async def test_permissions_post_reports_unstable(ds_envelope):
    response = await ds_envelope.client.post(
        "/-/permissions",
        data={"actor": '{"id": "root"}', "permission": "view-instance"},
        actor={"id": "root"},
    )
    assert response.status_code == 200
    assert response.json()["unstable"] == UNSTABLE_API_MESSAGE


@pytest.mark.asyncio
async def test_documented_endpoints_do_not_report_unstable(ds_client):
    for path in ("/-/versions.json", "/fixtures.json", "/fixtures/facetable.json"):
        response = await ds_client.get(path)
        assert response.status_code == 200
        assert "unstable" not in response.json()
