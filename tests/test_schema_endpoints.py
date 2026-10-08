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


@pytest_asyncio.fixture(scope="module")
async def schema_table_perms_ds():
    """
    A database that is viewable by anonymous users, but with one table
    locked down using the documented per-table lockdown recipe:
    a table-level allow block combined with allow_sql: false.
    """
    ds = Datasette(
        config={
            "databases": {
                "schema_table_perms_db": {
                    "allow_sql": False,
                    "tables": {"employee_salaries": {"allow": {"id": "root"}}},
                }
            }
        }
    )
    db = ds.add_memory_database("schema_table_perms_db")
    await db.execute_write(
        "CREATE TABLE IF NOT EXISTS public_posts (id INTEGER PRIMARY KEY, title TEXT)"
    )
    await db.execute_write(
        "CREATE TABLE IF NOT EXISTS employee_salaries "
        "(id INTEGER PRIMARY KEY, ssn TEXT, salary_usd INTEGER)"
    )
    await db.execute_write(
        "CREATE INDEX IF NOT EXISTS idx_employee_salaries_ssn ON employee_salaries(ssn)"
    )
    await db.execute_write(
        "CREATE TRIGGER IF NOT EXISTS trg_employee_salaries "
        "AFTER INSERT ON employee_salaries BEGIN SELECT 1; END"
    )
    return ds


@pytest.mark.asyncio
async def test_schema_table_perms_controls(schema_table_perms_ds):
    """Sanity check: the locked down table really is denied to anonymous users."""
    ds = schema_table_perms_ds
    for path in (
        "/schema_table_perms_db/employee_salaries.json",
        "/schema_table_perms_db/employee_salaries/-/schema.json",
        "/schema_table_perms_db/-/query.json?sql=select+*+from+employee_salaries",
    ):
        response = await ds.client.get(path)
        assert response.status_code == 403, path
    response = await ds.client.get("/schema_table_perms_db.json")
    assert response.status_code == 200
    assert "employee_salaries" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    ["/-/schema", "/schema_table_perms_db/-/schema"],
)
@pytest.mark.parametrize("format_ext", ["json", "md", ""])
async def test_schema_parent_views_hide_denied_tables(
    schema_table_perms_ds, base_url, format_ext
):
    """
    GHSA-926p-cw2f-643h: /-/schema and /db/-/schema must not disclose the DDL
    of tables the actor is denied view-table on, including indexes and
    triggers that belong to those tables.
    """
    url = base_url + (f".{format_ext}" if format_ext else "")

    # Anonymous: allowed table visible, denied table (and its columns,
    # index and trigger) absent
    response = await schema_table_perms_ds.client.get(url)
    assert response.status_code == 200
    assert "public_posts" in response.text
    assert "employee_salaries" not in response.text
    assert "ssn" not in response.text
    assert "salary_usd" not in response.text
    assert "idx_employee_salaries_ssn" not in response.text
    assert "trg_employee_salaries" not in response.text

    # root can see everything
    response = await schema_table_perms_ds.client.get(url, actor={"id": "root"})
    assert response.status_code == 200
    assert "public_posts" in response.text
    assert "CREATE TABLE employee_salaries" in response.text
    assert "idx_employee_salaries_ssn" in response.text
    assert "trg_employee_salaries" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "object_name", ["idx_employee_salaries_ssn", "trg_employee_salaries"]
)
@pytest.mark.parametrize("format_ext", ["json", "md", ""])
async def test_table_schema_does_not_serve_objects_of_denied_table(
    schema_table_perms_ds, object_name, format_ext
):
    """
    Related to GHSA-926p-cw2f-643h: /db/<name>/-/schema looks up sqlite_master
    by name without restricting to tables/views, so requesting the name of an
    index or trigger that belongs to a denied table serves its DDL. The
    view-table check runs against the index/trigger name, which is not a
    restricted table, so it passes.
    """
    url = f"/schema_table_perms_db/{object_name}/-/schema"
    if format_ext:
        url += f".{format_ext}"
    response = await schema_table_perms_ds.client.get(url)
    assert response.status_code in (403, 404)
    assert "employee_salaries" not in response.text
    assert "ssn" not in response.text
