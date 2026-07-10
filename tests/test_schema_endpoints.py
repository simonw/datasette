import json

import pytest
import pytest_asyncio
from datasette.app import Datasette


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
        actor={"id": "root"},
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
        actor={"id": "root"},
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
async def test_database_not_exists(schema_ds):
    """Test schema for a non-existent database returns 404."""
    # Test JSON format
    response = await schema_ds.client.get("/nonexistent_db/-/schema.json")
    assert response.status_code == 404
    data = response.json()
    assert data["ok"] is False
    assert "not found" in data["error"].lower()

    # Test HTML format (returns text)
    response = await schema_ds.client.get("/nonexistent_db/-/schema")
    assert response.status_code == 404
    assert "not found" in response.text.lower()

    # Test Markdown format (returns text)
    response = await schema_ds.client.get("/nonexistent_db/-/schema.md")
    assert response.status_code == 404
    assert "not found" in response.text.lower()


@pytest.mark.asyncio
async def test_table_not_exists(schema_ds):
    """Test schema for a non-existent table returns 404."""
    # Test JSON format
    response = await schema_ds.client.get("/schema_public_db/nonexistent/-/schema.json")
    assert response.status_code == 404
    data = response.json()
    assert data["ok"] is False
    assert "not found" in data["error"].lower()

    # Test HTML format (returns text)
    response = await schema_ds.client.get("/schema_public_db/nonexistent/-/schema")
    assert response.status_code == 404
    assert "not found" in response.text.lower()

    # Test Markdown format (returns text)
    response = await schema_ds.client.get("/schema_public_db/nonexistent/-/schema.md")
    assert response.status_code == 404
    assert "not found" in response.text.lower()


# ---------------------------------------------------------------------------
# /<database>/-/editor-schema.json — neutral structured schema for SQL editors
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def editor_schema_ds():
    """Datasette instance exercising the editor-schema endpoint.

    - public db: tables + a view + an FTS table (hidden shadow tables)
    - private db: gated behind view-database (allow root only)
    - noexec db: view-database allowed for anyone, execute-sql denied
    """
    ds = Datasette(
        config={
            "databases": {
                "editor_private_db": {"allow": {"id": "root"}},
                "editor_noexec_db": {
                    # Everyone may view the database, but nobody may run SQL
                    "allow_sql": {"id": "root"},
                },
            }
        }
    )

    public_db = ds.add_memory_database("editor_public_db")
    await public_db.execute_write(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"
    )
    await public_db.execute_write(
        "CREATE TABLE posts (id INTEGER PRIMARY KEY, title TEXT, body TEXT)"
    )
    await public_db.execute_write(
        "CREATE VIEW recent_posts AS SELECT id, title FROM posts ORDER BY id DESC"
    )
    # An FTS table produces hidden shadow tables (users_fts_data, etc.)
    await public_db.execute_write(
        "CREATE VIRTUAL TABLE users_fts USING fts5(name, content=users)"
    )

    private_db = ds.add_memory_database("editor_private_db")
    await private_db.execute_write(
        "CREATE TABLE secret_data (id INTEGER PRIMARY KEY, value TEXT)"
    )

    noexec_db = ds.add_memory_database("editor_noexec_db")
    await noexec_db.execute_write(
        "CREATE TABLE locked (id INTEGER PRIMARY KEY, value TEXT)"
    )

    await ds.invoke_startup()
    await ds.refresh_schemas()
    return ds


@pytest.mark.asyncio
async def test_editor_schema_allowed_shape(editor_schema_ds):
    """Authorized fetch returns tables, columns, types and views in the
    documented neutral shape."""
    response = await editor_schema_ds.client.get(
        "/editor_public_db/-/editor-schema.json"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["database"] == "editor_public_db"
    assert isinstance(data["tables"], list)

    by_name = {t["name"]: t for t in data["tables"]}

    # Regular table with columns + declared types
    users = by_name["users"]
    assert users["view"] is False
    assert users["columns"] == [
        {"name": "id", "type": "INTEGER"},
        {"name": "name", "type": "TEXT"},
    ]

    posts = by_name["posts"]
    assert posts["view"] is False
    assert {c["name"] for c in posts["columns"]} == {"id", "title", "body"}

    # View is flagged and carries its real columns
    view = by_name["recent_posts"]
    assert view["view"] is True
    assert [c["name"] for c in view["columns"]] == ["id", "title"]

    # Whole payload is JSON-serializable and every entry matches the shape
    for table in data["tables"]:
        assert set(table) == {"name", "view", "columns"}
        for column in table["columns"]:
            assert set(column) == {"name", "type"}


@pytest.mark.asyncio
async def test_editor_schema_excludes_hidden_tables(editor_schema_ds):
    """FTS shadow tables (hidden_table_names) must not appear."""
    response = await editor_schema_ds.client.get(
        "/editor_public_db/-/editor-schema.json"
    )
    assert response.status_code == 200
    names = {t["name"] for t in response.json()["tables"]}
    assert not any("_fts_" in name or name.endswith("_fts") for name in names), names
    # Sanity: the visible objects are still there
    assert {"users", "posts", "recent_posts"} <= names


@pytest.mark.asyncio
async def test_editor_schema_denied_view_database_403_no_leak(editor_schema_ds):
    """Anonymous user lacking view-database gets a 403 that leaks no names."""
    response = await editor_schema_ds.client.get(
        "/editor_private_db/-/editor-schema.json"
    )
    assert response.status_code == 403
    body = response.text
    assert "secret_data" not in body
    data = response.json()
    assert data["ok"] is False
    assert "secret_data" not in json.dumps(data)

    # The permitted actor can read it
    response = await editor_schema_ds.client.get(
        "/editor_private_db/-/editor-schema.json", actor={"id": "root"}
    )
    assert response.status_code == 200
    names = {t["name"] for t in response.json()["tables"]}
    assert "secret_data" in names


@pytest.mark.asyncio
async def test_editor_schema_denied_execute_sql_403_no_leak(editor_schema_ds):
    """A viewer who lacks execute-sql gets a 403 with no schema data."""
    # Anonymous user may view editor_noexec_db but not run SQL against it
    response = await editor_schema_ds.client.get(
        "/editor_noexec_db/-/editor-schema.json"
    )
    assert response.status_code == 403
    data = response.json()
    assert data["ok"] is False
    assert "tables" not in data
    assert "locked" not in json.dumps(data)

    # The actor granted execute-sql can read the schema
    response = await editor_schema_ds.client.get(
        "/editor_noexec_db/-/editor-schema.json", actor={"id": "root"}
    )
    assert response.status_code == 200
    names = {t["name"] for t in response.json()["tables"]}
    assert "locked" in names


@pytest.mark.asyncio
async def test_editor_schema_database_not_found(editor_schema_ds):
    """A non-existent database returns a 404 JSON error."""
    response = await editor_schema_ds.client.get(
        "/nonexistent_db/-/editor-schema.json"
    )
    assert response.status_code == 404
    data = response.json()
    assert data["ok"] is False
    assert "not found" in data["error"].lower()
