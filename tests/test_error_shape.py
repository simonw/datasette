"""
Tests for the canonical JSON error shape.

Every JSON error response from Datasette should use one shape:

    {
        "ok": false,
        "error": "<all messages joined with '; '>",
        "errors": ["<message>", ...],
        "status": <int matching the HTTP status code>
    }

Additional context keys (for example "rows" and "truncated" on SQL errors)
are permitted, but "ok", "error", "errors" and "status" must always be
present and the legacy "title" key must not be.

https://github.com/simonw/datasette/issues - 1.0 API consistency
"""

import pytest
from datasette.app import Datasette
from datasette.utils import sqlite3


def assert_canonical_error(response, expected_status):
    assert response.status_code == expected_status
    data = response.json()
    assert data["ok"] is False
    assert isinstance(data["error"], str)
    assert data["error"]
    assert isinstance(data["errors"], list)
    assert data["errors"]
    assert all(isinstance(message, str) for message in data["errors"])
    assert data["error"] == "; ".join(data["errors"])
    assert data["status"] == expected_status
    assert "title" not in data
    return data


@pytest.fixture
def ds_error_shape(tmp_path_factory):
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


# Shape 1: the exception handler (handle_exception.py)


@pytest.mark.asyncio
async def test_not_found_error_shape(ds_client):
    response = await ds_client.get("/fixtures/no_such_table.json")
    assert_canonical_error(response, 404)


@pytest.mark.asyncio
async def test_datasette_error_with_title_omits_title_key(ds_client):
    # DatasetteError(title="Invalid SQL") previously leaked a "title" key
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=update+facetable+set+state+=+1"
    )
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["Statement must be a SELECT"]


# Shape 2: the _error() helper (views/base.py) - write API and friends


@pytest.mark.asyncio
async def test_write_api_validation_error_shape(ds_error_shape):
    token = "dstok_{}".format(
        ds_error_shape.sign(
            {"a": "root", "token": "dstok", "t": 0},
            namespace="token",
        )
    )
    response = await ds_error_shape.client.post(
        "/data/docs/-/insert",
        json={"rows": [{"nope": 1}, {"also_nope": 2}]},
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json",
        },
    )
    data = assert_canonical_error(response, 400)
    # Multiple messages: errors keeps them all, error joins them
    assert len(data["errors"]) == 2
    assert data["errors"][0].startswith("Row 0")
    assert data["errors"][1].startswith("Row 1")


@pytest.mark.asyncio
async def test_write_api_permission_denied_shape(ds_error_shape):
    response = await ds_error_shape.client.post(
        "/data/docs/-/insert",
        json={"rows": [{"title": "hello"}]},
        headers={"Content-Type": "application/json"},
    )
    assert_canonical_error(response, 403)


# Shape 3: the JSON renderer (renderer.py)


@pytest.mark.asyncio
async def test_sql_error_shape_keeps_context_keys(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=select+*+from+no_such_table"
    )
    data = assert_canonical_error(response, 400)
    # Renderer errors keep their context keys
    assert data["rows"] == []
    assert "truncated" in data


@pytest.mark.asyncio
async def test_invalid_shape_error_shape(ds_client):
    response = await ds_client.get("/fixtures/-/query.json?sql=select+1&_shape=bananas")
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["Invalid _shape: bananas"]


@pytest.mark.asyncio
async def test_shape_object_on_query_is_a_400_error(ds_client):
    # Previously returned HTTP 200 with an ok: false body
    response = await ds_client.get("/fixtures/-/query.json?sql=select+1&_shape=object")
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["_shape=object is only available on tables"]


# Shape 4: bare {"error": ...} from the permission debug endpoints


@pytest.mark.asyncio
async def test_allowed_missing_action_error_shape(ds_client):
    response = await ds_client.get("/-/allowed.json")
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["action parameter is required"]


@pytest.mark.asyncio
async def test_allowed_unknown_action_error_shape(ds_client):
    response = await ds_client.get("/-/allowed.json?action=no_such_action")
    assert_canonical_error(response, 404)


@pytest.mark.asyncio
async def test_check_unknown_action_error_shape(ds_error_shape):
    response = await ds_error_shape.client.get(
        "/-/check.json?action=no_such_action",
        actor={"id": "root"},
    )
    assert_canonical_error(response, 404)


@pytest.mark.asyncio
async def test_rules_missing_action_error_shape(ds_error_shape):
    response = await ds_error_shape.client.get(
        "/-/rules.json",
        actor={"id": "root"},
    )
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["action parameter is required"]


# Other stragglers


@pytest.mark.asyncio
async def test_method_not_allowed_error_shape(ds_client):
    response = await ds_client.post("/fixtures.json")
    assert_canonical_error(response, 405)


@pytest.mark.asyncio
async def test_schema_unknown_database_error_shape(ds_client):
    response = await ds_client.get("/no_such_db/-/schema.json")
    assert_canonical_error(response, 404)
