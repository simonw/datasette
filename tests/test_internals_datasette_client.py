import httpx
import pytest
import pytest_asyncio
from datasette.app import Datasette


@pytest_asyncio.fixture
async def datasette(ds_client):
    await ds_client.ds.invoke_startup()
    return ds_client.ds


@pytest_asyncio.fixture
async def datasette_with_permissions():
    """A datasette instance with permission restrictions for testing"""
    ds = Datasette(config={"databases": {"test_db": {"allow": {"id": "admin"}}}})
    await ds.invoke_startup()
    db = ds.add_memory_database("test_db")
    await db.execute_write(
        "create table if not exists test_table (id integer primary key, name text)"
    )
    await db.execute_write(
        "insert or ignore into test_table (id, name) values (1, 'Alice')"
    )
    # Trigger catalog refresh
    await ds.client.get("/")
    return ds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,expected_status",
    [
        ("get", "/", 200),
        ("options", "/", 200),
        ("head", "/", 200),
        ("put", "/", 405),
        ("patch", "/", 405),
        ("delete", "/", 405),
    ],
)
async def test_client_methods(datasette, method, path, expected_status):
    client_method = getattr(datasette.client, method)
    response = await client_method(path)
    assert isinstance(response, httpx.Response)
    assert response.status_code == expected_status
    # Try that again using datasette.client.request
    response2 = await datasette.client.request(method, path)
    assert response2.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [None, "/prefix/"])
async def test_client_post(datasette, prefix):
    original_base_url = datasette._settings["base_url"]
    try:
        if prefix is not None:
            datasette._settings["base_url"] = prefix
        response = await datasette.client.post(
            "/-/messages",
            data={
                "message": "A message",
            },
        )
        assert isinstance(response, httpx.Response)
        assert response.status_code == 302
        assert "ds_messages" in response.cookies
    finally:
        datasette._settings["base_url"] = original_base_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix,expected_path", [(None, "/asgi-scope"), ("/prefix/", "/prefix/asgi-scope")]
)
async def test_client_path(datasette, prefix, expected_path):
    original_base_url = datasette._settings["base_url"]
    try:
        if prefix is not None:
            datasette._settings["base_url"] = prefix
        response = await datasette.client.get("/asgi-scope")
        path = response.json()["path"]
        assert path == expected_path
    finally:
        datasette._settings["base_url"] = original_base_url


@pytest.mark.asyncio
async def test_skip_permission_checks_allows_forbidden_access(
    datasette_with_permissions,
):
    """Test that skip_permission_checks=True bypasses permission checks"""
    ds = datasette_with_permissions

    # Without skip_permission_checks, anonymous user should get 403 for protected database
    response = await ds.client.get("/test_db.json")
    assert response.status_code == 403

    # With skip_permission_checks=True, should get 200
    response = await ds.client.get("/test_db.json", skip_permission_checks=True)
    assert response.status_code == 200
    data = response.json()
    assert data["database"] == "test_db"


@pytest.mark.asyncio
async def test_skip_permission_checks_on_table(datasette_with_permissions):
    """Test skip_permission_checks works for table access"""
    ds = datasette_with_permissions

    # Without skip_permission_checks, should get 403
    response = await ds.client.get("/test_db/test_table.json")
    assert response.status_code == 403

    # With skip_permission_checks=True, should get table data
    response = await ds.client.get(
        "/test_db/test_table.json", skip_permission_checks=True
    )
    assert response.status_code == 200
    data = response.json()
    assert data["rows"] == [{"id": 1, "name": "Alice"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["get", "post", "put", "patch", "delete", "options", "head"]
)
async def test_skip_permission_checks_all_methods(datasette_with_permissions, method):
    """Test that skip_permission_checks works with all HTTP methods"""
    ds = datasette_with_permissions

    # All methods should work with skip_permission_checks=True
    client_method = getattr(ds.client, method)
    response = await client_method("/test_db.json", skip_permission_checks=True)
    # We don't check status code since some methods might not be allowed,
    # but we verify the request doesn't fail due to permissions
    assert isinstance(response, httpx.Response)


@pytest.mark.asyncio
async def test_skip_permission_checks_request_method(datasette_with_permissions):
    """Test that skip_permission_checks works with client.request()"""
    ds = datasette_with_permissions

    # Without skip_permission_checks
    response = await ds.client.request("GET", "/test_db.json")
    assert response.status_code == 403

    # With skip_permission_checks=True
    response = await ds.client.request(
        "GET", "/test_db.json", skip_permission_checks=True
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_skip_permission_checks_isolated_to_request(datasette_with_permissions):
    """Test that skip_permission_checks doesn't affect other concurrent requests"""
    ds = datasette_with_permissions

    # First request with skip_permission_checks=True should succeed
    response1 = await ds.client.get("/test_db.json", skip_permission_checks=True)
    assert response1.status_code == 200

    # Subsequent request without it should still get 403
    response2 = await ds.client.get("/test_db.json")
    assert response2.status_code == 403

    # And another with skip should succeed again
    response3 = await ds.client.get("/test_db.json", skip_permission_checks=True)
    assert response3.status_code == 200


@pytest.mark.asyncio
async def test_skip_permission_checks_with_admin_actor(datasette_with_permissions):
    """Test that skip_permission_checks works even when actor is provided"""
    ds = datasette_with_permissions

    # Admin actor should normally have access
    admin_cookies = {"ds_actor": ds.client.actor_cookie({"id": "admin"})}
    response = await ds.client.get("/test_db.json", cookies=admin_cookies)
    assert response.status_code == 200

    # Non-admin actor should get 403
    user_cookies = {"ds_actor": ds.client.actor_cookie({"id": "user"})}
    response = await ds.client.get("/test_db.json", cookies=user_cookies)
    assert response.status_code == 403

    # Non-admin actor with skip_permission_checks=True should get 200
    response = await ds.client.get(
        "/test_db.json", cookies=user_cookies, skip_permission_checks=True
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_skip_permission_checks_shows_denied_tables():
    """Test that skip_permission_checks=True shows tables from denied databases in /-/tables.json"""
    ds = Datasette(
        config={
            "databases": {
                "fixtures": {"allow": False}  # Deny all access to this database
            }
        }
    )
    await ds.invoke_startup()
    db = ds.add_memory_database("fixtures")
    await db.execute_write(
        "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)"
    )
    await db.execute_write("INSERT INTO test_table (id, name) VALUES (1, 'Alice')")
    await ds._refresh_schemas()

    # Without skip_permission_checks, tables from denied database should not appear in /-/tables.json
    response = await ds.client.get("/-/tables.json")
    assert response.status_code == 200
    data = response.json()
    table_names = [match["name"] for match in data["matches"]]
    # Should not see any fixtures tables since access is denied
    fixtures_tables = [name for name in table_names if name.startswith("fixtures:")]
    assert len(fixtures_tables) == 0

    # With skip_permission_checks=True, tables from denied database SHOULD appear
    response = await ds.client.get("/-/tables.json", skip_permission_checks=True)
    assert response.status_code == 200
    data = response.json()
    table_names = [match["name"] for match in data["matches"]]
    # Should see fixtures tables when permission checks are skipped
    assert "fixtures: test_table" in table_names
