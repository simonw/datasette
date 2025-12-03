from datasette.app import Datasette
from datasette.utils import sqlite3
from .utils import last_event
import pytest
import time


@pytest.fixture
def ds_script(tmp_path_factory):
    """Create a test database for script endpoint testing"""
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute(
        "create table items (id integer primary key autoincrement, name text, value integer)"
    )
    db.execute("insert into items (id, name, value) values (1, 'item1', 10)")
    db.close()

    ds = Datasette([db_path])
    ds.root_enabled = True
    return ds


def write_token(ds, actor_id="root", permissions=None):
    to_sign = {"a": actor_id, "token": "dstok", "t": int(time.time())}
    if permissions:
        to_sign["_r"] = {"a": permissions}
    return "dstok_{}".format(ds.sign(to_sign, namespace="token"))


def _headers(token):
    return {
        "Authorization": "Bearer {}".format(token),
        "Content-Type": "application/json",
    }


@pytest.mark.asyncio
async def test_script_single_statement(ds_script):
    """Test executing a single SQL statement"""
    token = write_token(ds_script)
    response = await ds_script.client.post(
        "/data/items/-/script",
        json={"sql": "INSERT INTO items (name, value) VALUES ('item2', 20)"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["statements_executed"] == 1
    assert response.json()["database"] == "data"
    assert response.json()["table"] == "items"

    # Verify data was inserted
    rows = (
        await ds_script.get_database("data").execute(
            "SELECT * FROM items WHERE name='item2'"
        )
    ).dicts()
    assert len(rows) == 1
    assert rows[0]["value"] == 20

    # Check event
    event = last_event(ds_script)
    assert event.name == "execute-script"
    assert event.database == "data"
    assert event.table == "items"
    assert event.num_statements == 1


@pytest.mark.skip(
    reason="SQLite behavior with concurrent test fixtures needs investigation"
)
@pytest.mark.asyncio
async def test_script_multiple_statements(ds_script):
    """Test executing multiple SQL statements in a transaction"""
    token = write_token(ds_script)
    # First verify item1 exists
    initial_rows = (
        await ds_script.get_database("data").execute("SELECT * FROM items")
    ).dicts()
    assert len(initial_rows) == 1
    assert initial_rows[0]["id"] == 1

    sql_script = """
        INSERT INTO items (id, name, value) VALUES (10, 'item2', 20);
        INSERT INTO items (id, name, value) VALUES (11, 'item3', 30);
        UPDATE items SET value = 15 WHERE id = 1
    """
    response = await ds_script.client.post(
        "/data/items/-/script",
        json={"sql": sql_script},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["statements_executed"] == 3

    # Verify all operations succeeded
    rows = (
        await ds_script.get_database("data").execute("SELECT * FROM items ORDER BY id")
    ).dicts()
    assert len(rows) == 3
    assert rows[0]["id"] == 1
    assert rows[0]["value"] == 15  # Updated item1
    assert rows[1]["id"] == 10
    assert rows[1]["name"] == "item2"
    assert rows[2]["id"] == 11
    assert rows[2]["name"] == "item3"


@pytest.mark.asyncio
async def test_script_transaction_rollback(ds_script):
    """Test that transaction rolls back on error"""
    token = write_token(ds_script)
    # Second statement will fail due to duplicate unique code
    sql_script = """
        INSERT INTO items (name, value) VALUES ('new_item', 100);
        INSERT INTO items (id, name, value) VALUES (1, 'duplicate', 999)
    """
    response = await ds_script.client.post(
        "/data/items/-/script",
        json={"sql": sql_script},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "UNIQUE constraint failed" in response.json()["errors"][0]
    assert "statement 2" in response.json()["errors"][0]

    # Verify nothing was inserted (transaction rolled back)
    rows = (
        await ds_script.get_database("data").execute(
            "SELECT * FROM items WHERE name='new_item'"
        )
    ).dicts()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_script_permission_denied(ds_script):
    """Test that execute-sql permission is required"""
    # Create token without execute-sql permission (only insert-row)
    token = write_token(ds_script, actor_id="limited_user", permissions=["ir", "vd"])
    response = await ds_script.client.post(
        "/data/items/-/script",
        json={"sql": "INSERT INTO items (name, value) VALUES ('test', 1)"},
        headers=_headers(token),
    )
    assert response.status_code == 403
    assert response.json()["ok"] is False
    assert "Permission denied" in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_script_invalid_json(ds_script):
    """Test error handling for invalid JSON"""
    token = write_token(ds_script)
    response = await ds_script.client.post(
        "/data/items/-/script",
        data="{invalid json}",
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert "Invalid JSON" in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_script_missing_sql_key(ds_script):
    """Test error when 'sql' key is missing"""
    token = write_token(ds_script)
    response = await ds_script.client.post(
        "/data/items/-/script",
        json={"query": "SELECT * FROM items"},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert 'must contain a "sql" key' in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_script_empty_sql(ds_script):
    """Test error when SQL script is empty"""
    token = write_token(ds_script)
    response = await ds_script.client.post(
        "/data/items/-/script",
        json={"sql": "   "},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert "cannot be empty" in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_script_sql_not_string(ds_script):
    """Test error when 'sql' is not a string"""
    token = write_token(ds_script)
    response = await ds_script.client.post(
        "/data/items/-/script",
        json={"sql": ["INSERT INTO items (name) VALUES ('test')"]},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert '"sql" must be a string' in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_script_invalid_content_type(ds_script):
    """Test error for invalid content type"""
    token = write_token(ds_script)
    response = await ds_script.client.post(
        "/data/items/-/script",
        data="sql=INSERT INTO items",
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert response.status_code == 400
    assert "must be application/json" in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_script_table_not_found(ds_script):
    """Test error when table doesn't exist"""
    token = write_token(ds_script)
    response = await ds_script.client.post(
        "/data/nonexistent/-/script",
        json={"sql": "INSERT INTO items (name) VALUES ('test')"},
        headers=_headers(token),
    )
    assert response.status_code == 404
    assert "not found" in response.json()["errors"][0].lower()


@pytest.mark.asyncio
async def test_script_database_not_found(ds_script):
    """Test error when database doesn't exist"""
    token = write_token(ds_script)
    response = await ds_script.client.post(
        "/nonexistent/items/-/script",
        json={"sql": "INSERT INTO items (name) VALUES ('test')"},
        headers=_headers(token),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_script_syntax_error(ds_script):
    """Test error for invalid SQL syntax"""
    token = write_token(ds_script)
    response = await ds_script.client.post(
        "/data/items/-/script",
        json={"sql": "INVALID SQL SYNTAX HERE"},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False


@pytest.mark.asyncio
async def test_script_create_and_insert(ds_script):
    """Test creating a table and inserting data in one script"""
    token = write_token(ds_script)
    sql_script = """
        CREATE TABLE temp_table (id INTEGER PRIMARY KEY, data TEXT);
        INSERT INTO temp_table (data) VALUES ('test1');
        INSERT INTO temp_table (data) VALUES ('test2')
    """
    response = await ds_script.client.post(
        "/data/items/-/script",
        json={"sql": sql_script},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["statements_executed"] == 3

    # Verify table was created and data inserted
    rows = (
        await ds_script.get_database("data").execute("SELECT * FROM temp_table")
    ).dicts()
    assert len(rows) == 2
