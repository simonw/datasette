import pytest
from datasette.app import Datasette
from .fixtures import make_app_client


@pytest.mark.asyncio
async def test_database_schema_json():
    """Test /database/-/schema.json endpoint."""
    ds = Datasette()
    db = ds.add_memory_database("test_db_json")
    await db.execute_write(
        "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)"
    )

    response = await ds.client.get("/test_db_json/-/schema.json")
    assert response.status_code == 200
    data = response.json()
    assert "database" in data
    assert data["database"] == "test_db_json"
    assert "schema" in data
    assert "CREATE TABLE test_table" in data["schema"]


@pytest.mark.asyncio
async def test_database_schema_md():
    """Test /database/-/schema.md endpoint."""
    ds = Datasette()
    db = ds.add_memory_database("test_db_md")
    await db.execute_write(
        "CREATE TABLE test_table_md (id INTEGER PRIMARY KEY, name TEXT)"
    )

    response = await ds.client.get("/test_db_md/-/schema.md")
    assert response.status_code == 200
    assert "text/" in response.headers["content-type"]
    content = response.text
    assert "# Schema for test_db_md" in content
    assert "```sql" in content
    assert "CREATE TABLE test_table_md" in content


@pytest.mark.asyncio
async def test_database_schema_html():
    """Test /database/-/schema endpoint (HTML)."""
    ds = Datasette()
    db = ds.add_memory_database("test_db_html")
    await db.execute_write(
        "CREATE TABLE test_table_html (id INTEGER PRIMARY KEY, name TEXT)"
    )

    response = await ds.client.get("/test_db_html/-/schema")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    content = response.text
    assert "Schema for test_db_html" in content
    assert "CREATE TABLE test_table_html" in content


@pytest.mark.asyncio
async def test_instance_schema_json():
    """Test /-/schema.json endpoint."""
    ds = Datasette()
    db1 = ds.add_memory_database("db_inst_json_1")
    db2 = ds.add_memory_database("db_inst_json_2")
    await db1.execute_write("CREATE TABLE table1 (id INTEGER PRIMARY KEY)")
    await db2.execute_write("CREATE TABLE table2 (id INTEGER PRIMARY KEY)")

    response = await ds.client.get("/-/schema.json")
    assert response.status_code == 200
    data = response.json()
    assert "schemas" in data
    assert isinstance(data["schemas"], list)

    # Check that both databases are in the response
    db_names = [item["database"] for item in data["schemas"]]
    assert "db_inst_json_1" in db_names
    assert "db_inst_json_2" in db_names

    # Check that schemas are present
    for item in data["schemas"]:
        if item["database"] == "db_inst_json_1":
            assert "CREATE TABLE table1" in item["schema"]
        elif item["database"] == "db_inst_json_2":
            assert "CREATE TABLE table2" in item["schema"]


@pytest.mark.asyncio
async def test_instance_schema_md():
    """Test /-/schema.md endpoint."""
    ds = Datasette()
    db1 = ds.add_memory_database("db_inst_md")
    await db1.execute_write("CREATE TABLE table1 (id INTEGER PRIMARY KEY)")

    response = await ds.client.get("/-/schema.md")
    assert response.status_code == 200
    assert "text/" in response.headers["content-type"]
    content = response.text
    assert "# Schema for db_inst_md" in content
    assert "```sql" in content
    assert "CREATE TABLE table1" in content


@pytest.mark.asyncio
async def test_instance_schema_html():
    """Test /-/schema endpoint (HTML)."""
    ds = Datasette()
    db1 = ds.add_memory_database("db_inst_html")
    await db1.execute_write("CREATE TABLE table1 (id INTEGER PRIMARY KEY)")

    response = await ds.client.get("/-/schema")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    content = response.text
    assert "Schema for all databases" in content
    assert "CREATE TABLE table1" in content


@pytest.mark.asyncio
async def test_table_schema_json():
    """Test /database/table/-/schema.json endpoint."""
    ds = Datasette()
    db = ds.add_memory_database("test_db_tbl_json")
    await db.execute_write(
        "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)"
    )

    response = await ds.client.get("/test_db_tbl_json/test_table/-/schema.json")
    assert response.status_code == 200
    data = response.json()
    assert "database" in data
    assert data["database"] == "test_db_tbl_json"
    assert "table" in data
    assert data["table"] == "test_table"
    assert "schema" in data
    assert "CREATE TABLE test_table" in data["schema"]


@pytest.mark.asyncio
async def test_table_schema_md():
    """Test /database/table/-/schema.md endpoint."""
    ds = Datasette()
    db = ds.add_memory_database("test_db_tbl_md")
    await db.execute_write(
        "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)"
    )

    response = await ds.client.get("/test_db_tbl_md/test_table/-/schema.md")
    assert response.status_code == 200
    assert "text/" in response.headers["content-type"]
    content = response.text
    assert "# Schema for test_db_tbl_md.test_table" in content
    assert "```sql" in content
    assert "CREATE TABLE test_table" in content


@pytest.mark.asyncio
async def test_table_schema_default_json():
    """Test /database/table/-/schema endpoint defaults to JSON."""
    ds = Datasette()
    db = ds.add_memory_database("test_db_tbl_default")
    await db.execute_write(
        "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)"
    )

    response = await ds.client.get("/test_db_tbl_default/test_table/-/schema")
    assert response.status_code == 200
    # Should default to JSON for table schema
    data = response.json()
    assert "database" in data
    assert "table" in data
    assert "schema" in data


def test_database_schema_permission_denied():
    """Test that view-database permission is enforced."""
    with make_app_client(
        config={"databases": {"fixtures": {"allow": {"id": "root"}}}},
    ) as client:
        # Anonymous user should get 403
        response = client.get("/fixtures/-/schema.json")
        assert response.status == 403

        # Authenticated user with permission should succeed
        response = client.get(
            "/fixtures/-/schema.json",
            cookies={"ds_actor": client.actor_cookie({"id": "root"})},
        )
        assert response.status == 200


def test_table_schema_permission_denied():
    """Test that view-table permission is enforced."""
    with make_app_client(
        config={
            "databases": {
                "fixtures": {
                    "tables": {
                        "simple_primary_key": {"allow": {"id": "root"}}
                    }
                }
            }
        },
    ) as client:
        # Anonymous user should get 403
        response = client.get("/fixtures/simple_primary_key/-/schema.json")
        assert response.status == 403

        # Authenticated user with permission should succeed
        response = client.get(
            "/fixtures/simple_primary_key/-/schema.json",
            cookies={"ds_actor": client.actor_cookie({"id": "root"})},
        )
        assert response.status == 200


def test_instance_schema_respects_database_permissions():
    """Test that /-/schema only shows databases the user can view."""
    with make_app_client(
        config={"databases": {"fixtures": {"allow": {"id": "root"}}}},
        extra_databases={"public.db": "create table foo (id integer)"},
    ) as client:
        # Anonymous user should only see public database
        response = client.get("/-/schema.json")
        assert response.status == 200
        data = response.json
        db_names = [item["database"] for item in data["schemas"]]
        assert "public" in db_names
        assert "fixtures" not in db_names

        # Authenticated user should see both
        response = client.get(
            "/-/schema.json",
            cookies={"ds_actor": client.actor_cookie({"id": "root"})},
        )
        assert response.status == 200
        data = response.json
        db_names = [item["database"] for item in data["schemas"]]
        assert "public" in db_names
        assert "fixtures" in db_names


@pytest.mark.asyncio
async def test_database_schema_with_multiple_tables():
    """Test schema with multiple tables in a database."""
    ds = Datasette()
    db = ds.add_memory_database("test_db_multi")
    await db.execute_write("CREATE TABLE table1 (id INTEGER PRIMARY KEY)")
    await db.execute_write("CREATE TABLE table2 (name TEXT)")
    await db.execute_write("CREATE VIEW view1 AS SELECT * FROM table1")

    response = await ds.client.get("/test_db_multi/-/schema.json")
    assert response.status_code == 200
    data = response.json()
    schema = data["schema"]

    # All objects should be in the schema
    assert "CREATE TABLE table1" in schema
    assert "CREATE TABLE table2" in schema
    assert "CREATE VIEW view1" in schema


@pytest.mark.asyncio
async def test_empty_database_schema():
    """Test schema for an empty database."""
    ds = Datasette()
    ds.add_memory_database("empty_db")

    response = await ds.client.get("/empty_db/-/schema.json")
    assert response.status_code == 200
    data = response.json()
    assert data["database"] == "empty_db"
    assert data["schema"] == ""


@pytest.mark.asyncio
async def test_table_not_exists():
    """Test schema for a non-existent table."""
    ds = Datasette()
    ds.add_memory_database("test_db_noexist")

    response = await ds.client.get("/test_db_noexist/nonexistent/-/schema.json")
    assert response.status_code == 200
    data = response.json()
    assert data["schema"] == ""
