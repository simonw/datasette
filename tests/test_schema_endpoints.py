import asyncio
import pytest
import pytest_asyncio
from datasette.app import Datasette
from .fixtures import make_app_client


@pytest.fixture(scope="module")
def event_loop():
    """Create an instance of the default event loop for the test module."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def schema_ds():
    """Create a Datasette instance with test databases and permission config."""
    ds = Datasette(
        config={
            "databases": {
                "schema_private_db": {"allow": {"id": "root"}},
            }
        }
    )

    # Create public database with multiple tables
    public_db = ds.add_memory_database("schema_public_db")
    await public_db.execute_write(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT)"
    )
    await public_db.execute_write(
        "CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY, title TEXT)"
    )
    await public_db.execute_write(
        "CREATE VIEW IF NOT EXISTS recent_posts AS SELECT * FROM posts ORDER BY id DESC"
    )

    # Create a database with restricted access (requires root permission)
    private_db = ds.add_memory_database("schema_private_db")
    await private_db.execute_write(
        "CREATE TABLE IF NOT EXISTS secret_data (id INTEGER PRIMARY KEY, value TEXT)"
    )

    # Create an empty database
    ds.add_memory_database("schema_empty_db")

    return ds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "format_ext,expected_in_content",
    [
        ("json", None),
        ("md", ["# Schema for", "```sql"]),
        ("", ["Schema for", "CREATE TABLE"]),
    ],
)
async def test_database_schema_formats(schema_ds, format_ext, expected_in_content):
    """Test /database/-/schema endpoint in different formats."""
    url = "/schema_public_db/-/schema"
    if format_ext:
        url += f".{format_ext}"
    response = await schema_ds.client.get(url)
    assert response.status_code == 200

    if format_ext == "json":
        data = response.json()
        assert "database" in data
        assert data["database"] == "schema_public_db"
        assert "schema" in data
        assert "CREATE TABLE users" in data["schema"]
    else:
        content = response.text
        for expected in expected_in_content:
            assert expected in content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "format_ext,expected_in_content",
    [
        ("json", None),
        ("md", ["# Schema for", "```sql"]),
        ("", ["Schema for all databases"]),
    ],
)
async def test_instance_schema_formats(schema_ds, format_ext, expected_in_content):
    """Test /-/schema endpoint in different formats."""
    url = "/-/schema"
    if format_ext:
        url += f".{format_ext}"
    response = await schema_ds.client.get(url)
    assert response.status_code == 200

    if format_ext == "json":
        data = response.json()
        assert "schemas" in data
        assert isinstance(data["schemas"], list)
        db_names = [item["database"] for item in data["schemas"]]
        # Should see schema_public_db and schema_empty_db, but not schema_private_db (anonymous user)
        assert "schema_public_db" in db_names
        assert "schema_empty_db" in db_names
        assert "schema_private_db" not in db_names
        # Check schemas are present
        for item in data["schemas"]:
            if item["database"] == "schema_public_db":
                assert "CREATE TABLE users" in item["schema"]
    else:
        content = response.text
        for expected in expected_in_content:
            assert expected in content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "format_ext,expected_in_content",
    [
        ("json", None),
        ("md", ["# Schema for", "```sql"]),
        ("", ["Schema for users"]),
    ],
)
async def test_table_schema_formats(schema_ds, format_ext, expected_in_content):
    """Test /database/table/-/schema endpoint in different formats."""
    url = "/schema_public_db/users/-/schema"
    if format_ext:
        url += f".{format_ext}"
    response = await schema_ds.client.get(url)
    assert response.status_code == 200

    if format_ext == "json":
        data = response.json()
        assert "database" in data
        assert data["database"] == "schema_public_db"
        assert "table" in data
        assert data["table"] == "users"
        assert "schema" in data
        assert "CREATE TABLE users" in data["schema"]
    else:
        content = response.text
        for expected in expected_in_content:
            assert expected in content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/schema_private_db/-/schema.json",
        "/schema_private_db/secret_data/-/schema.json",
    ],
)
async def test_schema_permission_enforcement(schema_ds, url):
    """Test that permissions are enforced for schema endpoints."""
    # Anonymous user should get 403
    response = await schema_ds.client.get(url)
    assert response.status_code == 403

    # Authenticated user with permission should succeed
    response = await schema_ds.client.get(
        url,
        cookies={"ds_actor": schema_ds.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_instance_schema_respects_database_permissions(schema_ds):
    """Test that /-/schema only shows databases the user can view."""
    # Anonymous user should only see public databases
    response = await schema_ds.client.get("/-/schema.json")
    assert response.status_code == 200
    data = response.json()
    db_names = [item["database"] for item in data["schemas"]]
    assert "schema_public_db" in db_names
    assert "schema_empty_db" in db_names
    assert "schema_private_db" not in db_names

    # Authenticated user should see all databases
    response = await schema_ds.client.get(
        "/-/schema.json",
        cookies={"ds_actor": schema_ds.client.actor_cookie({"id": "root"})},
    )
    assert response.status_code == 200
    data = response.json()
    db_names = [item["database"] for item in data["schemas"]]
    assert "schema_public_db" in db_names
    assert "schema_empty_db" in db_names
    assert "schema_private_db" in db_names


@pytest.mark.asyncio
async def test_database_schema_with_multiple_tables(schema_ds):
    """Test schema with multiple tables in a database."""
    response = await schema_ds.client.get("/schema_public_db/-/schema.json")
    assert response.status_code == 200
    data = response.json()
    schema = data["schema"]

    # All objects should be in the schema
    assert "CREATE TABLE users" in schema
    assert "CREATE TABLE posts" in schema
    assert "CREATE VIEW recent_posts" in schema


@pytest.mark.asyncio
async def test_empty_database_schema(schema_ds):
    """Test schema for an empty database."""
    response = await schema_ds.client.get("/schema_empty_db/-/schema.json")
    assert response.status_code == 200
    data = response.json()
    assert data["database"] == "schema_empty_db"
    assert data["schema"] == ""


@pytest.mark.asyncio
async def test_table_not_exists(schema_ds):
    """Test schema for a non-existent table."""
    response = await schema_ds.client.get("/schema_public_db/nonexistent/-/schema.json")
    assert response.status_code == 200
    data = response.json()
    assert data["schema"] == ""
