import pytest
from datasette.database import Database
from datasette.app import Datasette


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "create_sql,table_name,config,expected_label_columns",
    [
        # Explicit label_column, single string
        (
            "create table t1 (id integer primary key, name text, title text);",
            "t1",
            {"t1": {"label_column": "title"}},
            ["title"],
        ),
        # Explicit label_column, list of columns
        (
            "create table t1b (id integer primary key, first_name text, last_name text);",
            "t1b",
            {"t1b": {"label_column": ["first_name", "last_name"]}},
            ["first_name", "last_name"],
        ),
        # Single unique text column
        (
            "create table t2 (id integer primary key, name2 text unique, title text);",
            "t2",
            {},
            ["name2"],
        ),
        (
            "create table t3 (id integer primary key, title2 text unique, name text);",
            "t3",
            {},
            ["title2"],
        ),
        # Two unique text columns means it cannot decide on one
        (
            "create table t3x (id integer primary key, name2 text unique, title2 text unique);",
            "t3x",
            {},
            [],
        ),
        # Name or title column
        (
            "create table t4 (id integer primary key, name text);",
            "t4",
            {},
            ["name"],
        ),
        (
            "create table t5 (id integer primary key, title text);",
            "t5",
            {},
            ["title"],
        ),
        # But not if there are multiple non-unique text that are not called title
        (
            "create table t5x (id integer primary key, other1 text, other2 text);",
            "t5x",
            {},
            [],
        ),
        (
            "create table t6 (id integer primary key, Name text);",
            "t6",
            {},
            ["Name"],
        ),
        (
            "create table t7 (id integer primary key, Title text);",
            "t7",
            {},
            ["Title"],
        ),
        # Two columns, one of which is id
        (
            "create table t8 (id integer primary key, content text);",
            "t8",
            {},
            ["content"],
        ),
        (
            "create table t9 (pk integer primary key, content text);",
            "t9",
            {},
            ["content"],
        ),
    ],
)
async def test_label_columns_for_table(
    create_sql, table_name, config, expected_label_columns
):
    """Test cases for label_columns_for_table method"""
    ds = Datasette()
    db = ds.add_database(Database(ds, memory_name="test_label_columns_for_table"))
    await db.execute_write_script(create_sql)
    await ds.invoke_startup()
    if config:
        ds.config = {"databases": {"test_label_columns_for_table": {"tables": config}}}
        # label_column config is only seeded into the internal DB once, so
        # re-apply it explicitly for tables configured after startup.
        await ds._apply_label_columns_config()
    actual_label_columns = await db.label_columns_for_table(table_name)
    assert actual_label_columns == expected_label_columns


@pytest.mark.asyncio
async def test_internal_db_override_wins_over_config():
    ds = Datasette()
    db = ds.add_database(Database(ds, memory_name="test_label_columns_override"))
    await db.execute_write_script(
        "create table t1 (id integer primary key, name text, title text);"
    )
    await ds.invoke_startup()
    ds.config = {
        "databases": {
            "test_label_columns_override": {"tables": {"t1": {"label_column": "title"}}}
        }
    }
    await ds._apply_label_columns_config()
    assert await db.label_columns_for_table("t1") == ["title"]

    await ds.set_label_columns("test_label_columns_override", "t1", ["name"])
    assert await db.label_columns_for_table("t1") == ["name"]

    # Re-applying config should NOT stomp the override (seed-once semantics)
    await ds._apply_label_columns_config()
    assert await db.label_columns_for_table("t1") == ["name"]


@pytest.mark.asyncio
async def test_removing_label_columns_override_reverts_to_config():
    ds = Datasette()
    db = ds.add_database(Database(ds, memory_name="test_label_columns_remove"))
    await db.execute_write_script(
        "create table t1 (id integer primary key, name text, title text);"
    )
    await ds.invoke_startup()
    ds.config = {
        "databases": {
            "test_label_columns_remove": {"tables": {"t1": {"label_column": "title"}}}
        }
    }
    await ds._apply_label_columns_config()

    await ds.set_label_columns("test_label_columns_remove", "t1", ["name"])
    assert await db.label_columns_for_table("t1") == ["name"]

    await ds.remove_label_columns("test_label_columns_remove", "t1")
    # No row left in the internal DB, so falls through to auto-detection
    # (both "name" and "title" columns exist, but "name" wins the "name or
    # title" heuristic tie-break since it is listed first).
    assert await db.label_columns_for_table("t1") == ["name"]

    # A subsequent startup re-seeds the config value, since no row exists
    await ds._apply_label_columns_config()
    assert await db.label_columns_for_table("t1") == ["title"]
