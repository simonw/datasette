from datasette.app import Datasette
from datasette.column_types import ColumnType
from datasette.utils import sqlite3
import json
import pytest
import time


@pytest.fixture
def ds_ct(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute(
        "create table posts (id integer primary key, title text, body text, "
        "author_email text, website text, metadata text)"
    )
    db.execute(
        "insert into posts values (1, 'Hello', '# World', 'test@example.com', "
        "'https://example.com', '{\"key\": \"value\"}')"
    )
    db.commit()
    ds = Datasette(
        [db_path],
        config={
            "databases": {
                "data": {
                    "tables": {
                        "posts": {
                            "column_types": {
                                "body": "markdown",
                                "author_email": "email",
                                "website": "url",
                                "metadata": "json",
                            }
                        }
                    }
                }
            }
        },
    )
    ds.root_enabled = True
    yield ds
    db.close()
    for database in ds.databases.values():
        if not database.is_memory:
            database.close()


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


# --- Internal DB and config loading ---


@pytest.mark.asyncio
async def test_column_types_table_created(ds_ct):
    await ds_ct.invoke_startup()
    internal = ds_ct.get_internal_database()
    result = await internal.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='column_types'"
    )
    assert len(result.rows) == 1


@pytest.mark.asyncio
async def test_config_loaded_into_internal_db(ds_ct):
    await ds_ct.invoke_startup()
    ct_map = await ds_ct.get_column_types("data", "posts")
    assert "body" in ct_map
    assert ct_map["body"] == ("markdown", None)
    assert ct_map["author_email"] == ("email", None)
    assert ct_map["website"] == ("url", None)
    assert ct_map["metadata"] == ("json", None)


@pytest.mark.asyncio
async def test_config_with_type_and_config(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute("create table geo (id integer primary key, location text)")
    ds = Datasette(
        [db_path],
        config={
            "databases": {
                "data": {
                    "tables": {
                        "geo": {
                            "column_types": {
                                "location": {
                                    "type": "point",
                                    "config": {"srid": 4326},
                                }
                            }
                        }
                    }
                }
            }
        },
    )
    await ds.invoke_startup()
    ct, config = await ds.get_column_type("data", "geo", "location")
    assert ct == "point"
    assert config == {"srid": 4326}
    db.close()
    for database in ds.databases.values():
        if not database.is_memory:
            database.close()


# --- Datasette API methods ---


@pytest.mark.asyncio
async def test_get_column_type(ds_ct):
    await ds_ct.invoke_startup()
    ct, config = await ds_ct.get_column_type("data", "posts", "author_email")
    assert ct == "email"
    assert config is None


@pytest.mark.asyncio
async def test_get_column_type_missing(ds_ct):
    await ds_ct.invoke_startup()
    ct, config = await ds_ct.get_column_type("data", "posts", "title")
    assert ct is None
    assert config is None


@pytest.mark.asyncio
async def test_set_and_remove_column_type(ds_ct):
    await ds_ct.invoke_startup()
    await ds_ct.set_column_type("data", "posts", "title", "markdown")
    ct, config = await ds_ct.get_column_type("data", "posts", "title")
    assert ct == "markdown"
    assert config is None

    await ds_ct.remove_column_type("data", "posts", "title")
    ct, config = await ds_ct.get_column_type("data", "posts", "title")
    assert ct is None


@pytest.mark.asyncio
async def test_set_column_type_with_config(ds_ct):
    await ds_ct.invoke_startup()
    await ds_ct.set_column_type("data", "posts", "title", "file", {"accept": "image/*"})
    ct, config = await ds_ct.get_column_type("data", "posts", "title")
    assert ct == "file"
    assert config == {"accept": "image/*"}


# --- Plugin registration ---


@pytest.mark.asyncio
async def test_builtin_column_types_registered(ds_ct):
    await ds_ct.invoke_startup()
    assert ds_ct.get_column_type_class("url") is not None
    assert ds_ct.get_column_type_class("email") is not None
    assert ds_ct.get_column_type_class("json") is not None
    assert ds_ct.get_column_type_class("nonexistent") is None


@pytest.mark.asyncio
async def test_column_type_class_attributes(ds_ct):
    await ds_ct.invoke_startup()
    url_type = ds_ct.get_column_type_class("url")
    assert url_type.name == "url"
    assert url_type.description == "URL"
    email_type = ds_ct.get_column_type_class("email")
    assert email_type.name == "email"
    assert email_type.description == "Email address"


# --- JSON API ---


@pytest.mark.asyncio
async def test_column_types_extra(ds_ct):
    await ds_ct.invoke_startup()
    response = await ds_ct.client.get("/data/posts.json?_extra=column_types")
    assert response.status_code == 200
    data = response.json()
    assert "column_types" in data
    assert data["column_types"]["body"] == {"type": "markdown", "config": None}
    assert data["column_types"]["author_email"] == {"type": "email", "config": None}
    assert data["column_types"]["website"] == {"type": "url", "config": None}
    # title has no column type, should not appear
    assert "title" not in data["column_types"]


@pytest.mark.asyncio
async def test_display_columns_include_column_type(ds_ct):
    await ds_ct.invoke_startup()
    response = await ds_ct.client.get("/data/posts.json?_extra=display_columns")
    assert response.status_code == 200
    data = response.json()
    cols = {c["name"]: c for c in data["display_columns"]}
    assert cols["author_email"]["column_type"] == "email"
    assert cols["author_email"]["column_type_config"] is None
    assert cols["website"]["column_type"] == "url"
    assert cols["title"]["column_type"] is None


# --- Rendering ---


@pytest.mark.asyncio
async def test_url_render_cell(ds_ct):
    await ds_ct.invoke_startup()
    response = await ds_ct.client.get("/data/posts.json?_extra=render_cell")
    assert response.status_code == 200
    data = response.json()
    rendered = data["render_cell"][0]
    assert "href" in rendered["website"]
    assert "https://example.com" in rendered["website"]


@pytest.mark.asyncio
async def test_email_render_cell(ds_ct):
    await ds_ct.invoke_startup()
    response = await ds_ct.client.get("/data/posts.json?_extra=render_cell")
    assert response.status_code == 200
    data = response.json()
    rendered = data["render_cell"][0]
    assert "mailto:" in rendered["author_email"]
    assert "test@example.com" in rendered["author_email"]


@pytest.mark.asyncio
async def test_json_render_cell(ds_ct):
    await ds_ct.invoke_startup()
    response = await ds_ct.client.get("/data/posts.json?_extra=render_cell")
    assert response.status_code == 200
    data = response.json()
    rendered = data["render_cell"][0]
    assert "<pre>" in rendered["metadata"]


# --- Validation ---


@pytest.mark.asyncio
async def test_email_validation_on_insert(ds_ct):
    await ds_ct.invoke_startup()
    token = write_token(ds_ct)
    response = await ds_ct.client.post(
        "/data/posts/-/insert",
        json={"row": {"title": "Test", "author_email": "not-an-email"}},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert "author_email" in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_email_validation_passes_valid(ds_ct):
    await ds_ct.invoke_startup()
    token = write_token(ds_ct)
    response = await ds_ct.client.post(
        "/data/posts/-/insert",
        json={"row": {"title": "Test", "author_email": "valid@example.com"}},
        headers=_headers(token),
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_url_validation_on_insert(ds_ct):
    await ds_ct.invoke_startup()
    token = write_token(ds_ct)
    response = await ds_ct.client.post(
        "/data/posts/-/insert",
        json={"row": {"title": "Test", "website": "not-a-url"}},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert "website" in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_json_validation_on_insert(ds_ct):
    await ds_ct.invoke_startup()
    token = write_token(ds_ct)
    response = await ds_ct.client.post(
        "/data/posts/-/insert",
        json={"row": {"title": "Test", "metadata": "not-json{"}},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert "metadata" in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_validation_on_update(ds_ct):
    await ds_ct.invoke_startup()
    token = write_token(ds_ct)
    response = await ds_ct.client.post(
        "/data/posts/1/-/update",
        json={"update": {"author_email": "invalid"}},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert "author_email" in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_validation_allows_null(ds_ct):
    await ds_ct.invoke_startup()
    token = write_token(ds_ct)
    response = await ds_ct.client.post(
        "/data/posts/-/insert",
        json={"row": {"title": "Test", "author_email": None}},
        headers=_headers(token),
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_validation_allows_empty_string(ds_ct):
    await ds_ct.invoke_startup()
    token = write_token(ds_ct)
    response = await ds_ct.client.post(
        "/data/posts/-/insert",
        json={"row": {"title": "Test", "author_email": ""}},
        headers=_headers(token),
    )
    assert response.status_code == 201


# --- ColumnType base class ---


@pytest.mark.asyncio
async def test_column_type_base_defaults():
    ct = ColumnType(name="test", description="Test type")
    assert await ct.render_cell(
        "val", "col", "tbl", "db", None, None, None
    ) is None
    assert await ct.validate("val", None, None) is None
    assert await ct.transform_value("val", None, None) == "val"


# --- render_cell extra with column types ---


@pytest.mark.asyncio
async def test_render_cell_extra_with_column_types(ds_ct):
    await ds_ct.invoke_startup()
    response = await ds_ct.client.get("/data/posts.json?_extra=render_cell")
    assert response.status_code == 200
    data = response.json()
    rendered = data["render_cell"][0]
    assert "mailto:" in rendered["author_email"]
    assert "href" in rendered["website"]
