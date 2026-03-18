import logging

from datasette.app import Datasette
from datasette.column_types import (
    ColumnType,
    SQLiteType,
)
from datasette.hookspecs import hookimpl
from datasette.plugins import pm
from datasette.utils import sqlite3
from datasette.utils import StartupError
import markupsafe
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
    # "markdown" is not a registered type, so it won't appear
    assert "body" not in ct_map
    assert ct_map["author_email"].name == "email"
    assert ct_map["author_email"].config is None
    assert ct_map["website"].name == "url"
    assert ct_map["metadata"].name == "json"


@pytest.mark.asyncio
async def test_config_with_type_and_config(tmp_path_factory):
    class PointColumnType(ColumnType):
        name = "point"
        description = "Geographic point"

    class _Plugin:
        @hookimpl
        def register_column_types(self, datasette):
            return [PointColumnType]

    plugin = _Plugin()
    pm.register(plugin, name="test_point_ct")
    try:
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
        ct = await ds.get_column_type("data", "geo", "location")
        assert ct.name == "point"
        assert ct.config == {"srid": 4326}
        db.close()
        for database in ds.databases.values():
            if not database.is_memory:
                database.close()
    finally:
        pm.unregister(plugin, name="test_point_ct")


# --- Datasette API methods ---


@pytest.mark.asyncio
async def test_get_column_type(ds_ct):
    await ds_ct.invoke_startup()
    ct = await ds_ct.get_column_type("data", "posts", "author_email")
    assert isinstance(ct, ColumnType)
    assert ct.name == "email"
    assert ct.config is None


@pytest.mark.asyncio
async def test_get_column_type_missing(ds_ct):
    await ds_ct.invoke_startup()
    ct = await ds_ct.get_column_type("data", "posts", "title")
    assert ct is None


@pytest.mark.asyncio
async def test_set_and_remove_column_type(ds_ct):
    await ds_ct.invoke_startup()
    await ds_ct.set_column_type("data", "posts", "title", "email")
    ct = await ds_ct.get_column_type("data", "posts", "title")
    assert ct.name == "email"
    assert ct.config is None

    await ds_ct.remove_column_type("data", "posts", "title")
    ct = await ds_ct.get_column_type("data", "posts", "title")
    assert ct is None


@pytest.mark.asyncio
async def test_set_column_type_with_config(ds_ct):
    await ds_ct.invoke_startup()
    await ds_ct.set_column_type("data", "posts", "title", "url", {"max_length": 200})
    ct = await ds_ct.get_column_type("data", "posts", "title")
    assert ct.name == "url"
    assert ct.config == {"max_length": 200}


@pytest.mark.asyncio
async def test_set_column_type_rejects_incompatible_sqlite_type(ds_ct):
    await ds_ct.invoke_startup()
    with pytest.raises(ValueError, match="only applicable to SQLite types TEXT"):
        await ds_ct.set_column_type("data", "posts", "id", "json")


@pytest.mark.asyncio
async def test_set_column_type_allows_varchar_for_text_only_type(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute("create table links (id integer primary key, url varchar(255))")
    db.commit()
    ds = Datasette([db_path])
    await ds.invoke_startup()
    await ds.set_column_type("data", "links", "url", "url")
    ct = await ds.get_column_type("data", "links", "url")
    assert ct.name == "url"
    db.close()
    for database in ds.databases.values():
        if not database.is_memory:
            database.close()


# --- Plugin registration ---


@pytest.mark.asyncio
async def test_builtin_column_types_registered(ds_ct):
    """register_column_types returns classes; _column_types stores them by name."""
    await ds_ct.invoke_startup()
    assert "url" in ds_ct._column_types
    assert "email" in ds_ct._column_types
    assert "json" in ds_ct._column_types
    assert "nonexistent" not in ds_ct._column_types


@pytest.mark.asyncio
async def test_column_type_class_attributes(ds_ct):
    await ds_ct.invoke_startup()
    url_cls = ds_ct._column_types["url"]
    assert url_cls.name == "url"
    assert url_cls.description == "URL"
    assert url_cls.sqlite_types == (SQLiteType.TEXT,)
    email_cls = ds_ct._column_types["email"]
    assert email_cls.name == "email"
    assert email_cls.description == "Email address"
    assert email_cls.sqlite_types == (SQLiteType.TEXT,)
    json_cls = ds_ct._column_types["json"]
    assert json_cls.sqlite_types == (SQLiteType.TEXT,)


def test_sqlite_type_from_declared_type():
    assert SQLiteType.from_declared_type("text") == SQLiteType.TEXT
    assert SQLiteType.from_declared_type("varchar(255)") == SQLiteType.TEXT
    assert SQLiteType.from_declared_type("integer") == SQLiteType.INTEGER
    assert SQLiteType.from_declared_type("float") == SQLiteType.REAL
    assert SQLiteType.from_declared_type("blob") == SQLiteType.BLOB
    assert SQLiteType.from_declared_type("") == SQLiteType.NULL
    assert SQLiteType.from_declared_type("numeric") is None


# --- JSON API ---


@pytest.mark.asyncio
async def test_column_types_extra(ds_ct):
    await ds_ct.invoke_startup()
    response = await ds_ct.client.get("/data/posts.json?_extra=column_types")
    assert response.status_code == 200
    data = response.json()
    assert "column_types" in data
    assert data["column_types"]["author_email"] == {"type": "email", "config": None}
    assert data["column_types"]["website"] == {"type": "url", "config": None}
    assert data["column_types"]["metadata"] == {"type": "json", "config": None}
    # "markdown" is not a registered type, so body should not appear
    assert "body" not in data["column_types"]
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
    class TestType(ColumnType):
        name = "test"
        description = "Test type"

    ct = TestType()
    assert ct.config is None
    assert await ct.render_cell("val", "col", "tbl", "db", None, None) is None
    assert await ct.validate("val", None) is None
    assert await ct.transform_value("val", None) == "val"


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


# --- Duplicate column type name ---


@pytest.mark.asyncio
async def test_duplicate_column_type_name_raises_error():
    class DuplicateUrlType(ColumnType):
        name = "url"
        description = "Duplicate URL"

        async def render_cell(self, value, column, table, database, datasette, request):
            return None

    class _Plugin:
        @hookimpl
        def register_column_types(self, datasette):
            return [DuplicateUrlType]

    plugin = _Plugin()
    pm.register(plugin, name="test_duplicate_ct")
    try:
        ds = Datasette()
        with pytest.raises(StartupError, match="Duplicate column type name: url"):
            await ds.invoke_startup()
    finally:
        pm.unregister(plugin, name="test_duplicate_ct")


# --- Row endpoint ---


@pytest.mark.asyncio
async def test_row_endpoint_render_cell_with_column_types(ds_ct):
    await ds_ct.invoke_startup()
    response = await ds_ct.client.get("/data/posts/1.json?_extra=render_cell")
    assert response.status_code == 200
    data = response.json()
    rendered = data["render_cell"][0]
    assert "mailto:" in rendered["author_email"]
    assert "href" in rendered["website"]


# --- transform_value in JSON output ---


@pytest.mark.asyncio
async def test_transform_value_in_json_output(tmp_path_factory):
    """A column type with transform_value should modify rows in JSON API."""

    class UpperColumnType(ColumnType):
        name = "upper"
        description = "Uppercase"

        async def transform_value(self, value, datasette):
            if isinstance(value, str):
                return value.upper()
            return value

    class _Plugin:
        @hookimpl
        def register_column_types(self, datasette):
            return [UpperColumnType]

    plugin = _Plugin()
    pm.register(plugin, name="test_transform_ct")
    try:
        db_directory = tmp_path_factory.mktemp("dbs")
        db_path = str(db_directory / "data.db")
        db = sqlite3.connect(str(db_path))
        db.execute("vacuum")
        db.execute("create table t (id integer primary key, name text)")
        db.execute("insert into t values (1, 'hello')")
        db.commit()
        ds = Datasette(
            [db_path],
            config={
                "databases": {
                    "data": {"tables": {"t": {"column_types": {"name": "upper"}}}}
                }
            },
        )
        await ds.invoke_startup()
        response = await ds.client.get("/data/t.json")
        assert response.status_code == 200
        data = response.json()
        assert data["rows"][0]["name"] == "HELLO"
        db.close()
        for database in ds.databases.values():
            if not database.is_memory:
                database.close()
    finally:
        pm.unregister(plugin, name="test_transform_ct")


# --- Column type priority over plugins ---


@pytest.mark.asyncio
async def test_column_type_render_cell_has_priority_over_plugins(tmp_path_factory):
    """Column type render_cell should take priority over render_cell plugin hook."""

    class PriorityColumnType(ColumnType):
        name = "priority_test"
        description = "Priority test"

        async def render_cell(self, value, column, table, database, datasette, request):
            if value is not None:
                return markupsafe.Markup(
                    f"<b>COLUMN_TYPE:{markupsafe.escape(value)}</b>"
                )
            return None

    class _ColumnTypePlugin:
        @hookimpl
        def register_column_types(self, datasette):
            return [PriorityColumnType]

    class _RenderCellPlugin:
        @hookimpl
        def render_cell(
            self,
            row,
            value,
            column,
            table,
            pks,
            database,
            datasette,
            request,
            column_type,
        ):
            if column == "name":
                return markupsafe.Markup(f"<i>PLUGIN:{markupsafe.escape(value)}</i>")

    ct_plugin = _ColumnTypePlugin()
    rc_plugin = _RenderCellPlugin()
    pm.register(ct_plugin, name="test_priority_ct")
    pm.register(rc_plugin, name="test_priority_render")
    try:
        db_directory = tmp_path_factory.mktemp("dbs")
        db_path = str(db_directory / "data.db")
        db = sqlite3.connect(str(db_path))
        db.execute("vacuum")
        db.execute("create table t (id integer primary key, name text)")
        db.execute("insert into t values (1, 'hello')")
        db.commit()
        ds = Datasette(
            [db_path],
            config={
                "databases": {
                    "data": {
                        "tables": {"t": {"column_types": {"name": "priority_test"}}}
                    }
                }
            },
        )
        await ds.invoke_startup()
        response = await ds.client.get("/data/t.json?_extra=render_cell")
        assert response.status_code == 200
        data = response.json()
        rendered = data["render_cell"][0]
        # Column type should win over the plugin
        assert "COLUMN_TYPE:" in rendered["name"]
        assert "PLUGIN:" not in rendered["name"]
        db.close()
        for database in ds.databases.values():
            if not database.is_memory:
                database.close()
    finally:
        pm.unregister(ct_plugin, name="test_priority_ct")
        pm.unregister(rc_plugin, name="test_priority_render")


# --- Row detail page rendering ---


@pytest.mark.asyncio
async def test_row_detail_page_html_rendering(ds_ct):
    """Row detail HTML page should use column type rendering."""
    await ds_ct.invoke_startup()
    response = await ds_ct.client.get("/data/posts/1")
    assert response.status_code == 200
    html = response.text
    # The email column should be rendered with mailto: link
    assert "mailto:test@example.com" in html
    # The url column should be rendered with href
    assert 'href="https://example.com"' in html


# --- HTML table page rendering ---


@pytest.mark.asyncio
async def test_html_table_page_rendering(ds_ct):
    """HTML table page should use column type rendering."""
    await ds_ct.invoke_startup()
    response = await ds_ct.client.get("/data/posts")
    assert response.status_code == 200
    html = response.text
    assert "mailto:test@example.com" in html
    assert 'href="https://example.com"' in html


# --- Validation on upsert ---


@pytest.mark.asyncio
async def test_validation_on_upsert(ds_ct):
    await ds_ct.invoke_startup()
    token = write_token(ds_ct)
    response = await ds_ct.client.post(
        "/data/posts/-/upsert",
        json={
            "rows": [{"id": 1, "title": "Updated", "author_email": "invalid"}],
        },
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert "author_email" in response.json()["errors"][0]


@pytest.mark.asyncio
async def test_validation_on_upsert_passes_valid(ds_ct):
    await ds_ct.invoke_startup()
    token = write_token(ds_ct)
    response = await ds_ct.client.post(
        "/data/posts/-/upsert",
        json={
            "rows": [{"id": 1, "title": "Updated", "author_email": "valid@test.com"}],
        },
        headers=_headers(token),
    )
    assert response.status_code == 200


# --- Unknown type warning logged ---


@pytest.mark.asyncio
async def test_unknown_type_warning_logged(tmp_path_factory, caplog):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute("create table t (id integer primary key, col text)")
    db.commit()
    ds = Datasette(
        [db_path],
        config={
            "databases": {
                "data": {"tables": {"t": {"column_types": {"col": "nonexistent_type"}}}}
            }
        },
    )
    with caplog.at_level(logging.WARNING):
        await ds.invoke_startup()
    assert "unknown type" in caplog.text.lower()
    assert "nonexistent_type" in caplog.text
    db.close()
    for database in ds.databases.values():
        if not database.is_memory:
            database.close()


@pytest.mark.asyncio
async def test_incompatible_sqlite_type_warning_logged(tmp_path_factory, caplog):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute("create table t (id integer primary key, col integer)")
    db.commit()
    ds = Datasette(
        [db_path],
        config={
            "databases": {"data": {"tables": {"t": {"column_types": {"col": "json"}}}}}
        },
    )
    with caplog.at_level(logging.WARNING):
        await ds.invoke_startup()
    assert "only applicable to sqlite types text" in caplog.text.lower()
    assert await ds.get_column_type("data", "t", "col") is None
    db.close()
    for database in ds.databases.values():
        if not database.is_memory:
            database.close()


# --- Config overwrites on restart ---


@pytest.mark.asyncio
async def test_config_overwrites_on_restart(tmp_path_factory):
    """Config values should overwrite any existing column types in internal DB on startup."""
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute("create table t (id integer primary key, col text)")
    db.commit()
    ds = Datasette(
        [db_path],
        config={
            "databases": {"data": {"tables": {"t": {"column_types": {"col": "email"}}}}}
        },
    )
    await ds.invoke_startup()
    ct = await ds.get_column_type("data", "t", "col")
    assert ct.name == "email"

    # Manually change the column type in the internal DB
    await ds.set_column_type("data", "t", "col", "url")
    ct = await ds.get_column_type("data", "t", "col")
    assert ct.name == "url"

    # Re-apply config (simulating what happens on restart)
    await ds._apply_column_types_config()
    ct = await ds.get_column_type("data", "t", "col")
    assert ct.name == "email"  # Config wins

    db.close()
    for database in ds.databases.values():
        if not database.is_memory:
            database.close()


# --- No column_types in config ---


@pytest.mark.asyncio
async def test_no_column_types_in_config(tmp_path_factory):
    """Datasette should work fine without any column_types configuration."""
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    db = sqlite3.connect(str(db_path))
    db.execute("vacuum")
    db.execute("create table t (id integer primary key, col text)")
    db.execute("insert into t values (1, 'hello')")
    db.commit()
    ds = Datasette([db_path])
    await ds.invoke_startup()

    # No column types assigned
    ct_map = await ds.get_column_types("data", "t")
    assert ct_map == {}

    # JSON endpoint should work without column_types extra
    response = await ds.client.get("/data/t.json")
    assert response.status_code == 200
    assert response.json()["rows"][0]["col"] == "hello"

    # column_types extra should return empty
    response = await ds.client.get("/data/t.json?_extra=column_types")
    assert response.status_code == 200
    assert response.json()["column_types"] == {}

    db.close()
    for database in ds.databases.values():
        if not database.is_memory:
            database.close()
