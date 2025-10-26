"""
Tests for special endpoints in datasette/views/special.py
"""

import pytest
import pytest_asyncio
from datasette.app import Datasette


@pytest_asyncio.fixture
async def ds_with_tables():
    """Create a Datasette instance with some tables for searching."""
    ds = Datasette(
        config={
            "databases": {
                "content": {
                    "allow": {"id": "*"},  # Allow all authenticated users
                    "tables": {
                        "articles": {
                            "allow": {"id": "editor"},  # Only editor can view
                        },
                        "comments": {
                            "allow": True,  # Everyone can view
                        },
                    },
                },
                "private": {
                    "allow": False,  # Deny everyone
                },
            }
        }
    )
    await ds.invoke_startup()

    # Add content database with some tables
    content_db = ds.add_memory_database("content")
    await content_db.execute_write(
        "CREATE TABLE IF NOT EXISTS articles (id INTEGER PRIMARY KEY, title TEXT)"
    )
    await content_db.execute_write(
        "CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY, body TEXT)"
    )
    await content_db.execute_write(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT)"
    )

    # Add private database with a table
    private_db = ds.add_memory_database("private")
    await private_db.execute_write(
        "CREATE TABLE IF NOT EXISTS secrets (id INTEGER PRIMARY KEY, data TEXT)"
    )

    # Add another public database
    public_db = ds.add_memory_database("public")
    await public_db.execute_write(
        "CREATE TABLE IF NOT EXISTS articles (id INTEGER PRIMARY KEY, content TEXT)"
    )

    return ds


# /-/tables.json tests
@pytest.mark.asyncio
async def test_tables_basic_search(ds_with_tables):
    """Test basic table search functionality."""
    # Search for "articles" - should find it in both content and public databases
    # but only return public.articles for anonymous user (content.articles requires auth)
    response = await ds_with_tables.client.get("/-/tables.json?q=articles")
    assert response.status_code == 200
    data = response.json()

    # Should only see public.articles (content.articles restricted to authenticated users)
    assert "matches" in data
    assert len(data["matches"]) == 1

    match = data["matches"][0]
    assert "url" in match
    assert "name" in match
    assert match["name"] == "public: articles"
    assert "/public/articles" in match["url"]


@pytest.mark.asyncio
async def test_tables_search_with_auth(ds_with_tables):
    """Test that authenticated users see more tables."""
    # Editor user should see content.articles
    response = await ds_with_tables.client.get(
        "/-/tables.json?q=articles",
        cookies={"ds_actor": ds_with_tables.client.actor_cookie({"id": "editor"})},
    )
    assert response.status_code == 200
    data = response.json()

    # Should see both content.articles and public.articles
    assert len(data["matches"]) == 2

    names = {match["name"] for match in data["matches"]}
    assert names == {"content: articles", "public: articles"}


@pytest.mark.asyncio
async def test_tables_search_partial_match(ds_with_tables):
    """Test that search matches partial table names."""
    # Search for "com" should match "comments"
    response = await ds_with_tables.client.get(
        "/-/tables.json?q=com",
        cookies={"ds_actor": ds_with_tables.client.actor_cookie({"id": "user"})},
    )
    assert response.status_code == 200
    data = response.json()

    assert len(data["matches"]) == 1
    assert data["matches"][0]["name"] == "content: comments"


@pytest.mark.asyncio
async def test_tables_search_respects_database_permissions(ds_with_tables):
    """Test that tables from denied databases are not shown."""
    # Search for "secrets" which is in the private database
    # Even authenticated users shouldn't see it because database is denied
    response = await ds_with_tables.client.get(
        "/-/tables.json?q=secrets",
        cookies={"ds_actor": ds_with_tables.client.actor_cookie({"id": "user"})},
    )
    assert response.status_code == 200
    data = response.json()

    # Should not see secrets table from private database
    assert len(data["matches"]) == 0


@pytest.mark.asyncio
async def test_tables_search_respects_table_permissions(ds_with_tables):
    """Test that tables with specific permissions are filtered correctly."""
    # Regular authenticated user searching for "users"
    response = await ds_with_tables.client.get(
        "/-/tables.json?q=users",
        cookies={"ds_actor": ds_with_tables.client.actor_cookie({"id": "regular"})},
    )
    assert response.status_code == 200
    data = response.json()

    # Should see content.users (authenticated users can view content database)
    assert len(data["matches"]) == 1
    assert data["matches"][0]["name"] == "content: users"


@pytest.mark.asyncio
async def test_tables_search_response_structure(ds_with_tables):
    """Test that response has correct structure."""
    response = await ds_with_tables.client.get(
        "/-/tables.json?q=users",
        cookies={"ds_actor": ds_with_tables.client.actor_cookie({"id": "user"})},
    )
    assert response.status_code == 200
    data = response.json()

    assert "matches" in data
    assert isinstance(data["matches"], list)

    if data["matches"]:
        match = data["matches"][0]
        assert "url" in match
        assert "name" in match
        assert isinstance(match["url"], str)
        assert isinstance(match["name"], str)
        # Name should be in format "database: table"
        assert ": " in match["name"]
