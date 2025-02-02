import pytest
from datasette.database import Database
from datasette.app import Datasette


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "create_sql,table_name,config,expected_label_column",
    [
        # Explicit label_column
        (
            "create table t1 (id integer primary key, name text, title text);",
            "t1",
            {"t1": {"label_column": "title"}},
            "title",
        ),
        # Single unique text column
        (
            "create table t2 (id integer primary key, name2 text unique, title text);",
            "t2",
            {},
            "name2",
        ),
        (
            "create table t3 (id integer primary key, title2 text unique, name text);",
            "t3",
            {},
            "title2",
        ),
        # Two unique text columns means it cannot decide on one
        (
            "create table t3x (id integer primary key, name2 text unique, title2 text unique);",
            "t3x",
            {},
            None,
        ),
        # Name or title column
        (
            "create table t4 (id integer primary key, name text);",
            "t4",
            {},
            "name",
        ),
        (
            "create table t5 (id integer primary key, title text);",
            "t5",
            {},
            "title",
        ),
        # But not if there are multiple non-unique text that are not called title
        (
            "create table t5x (id integer primary key, other1 text, other2 text);",
            "t5x",
            {},
            None,
        ),
        (
            "create table t6 (id integer primary key, Name text);",
            "t6",
            {},
            "Name",
        ),
        (
            "create table t7 (id integer primary key, Title text);",
            "t7",
            {},
            "Title",
        ),
        # Two columns, one of which is id
        (
            "create table t8 (id integer primary key, content text);",
            "t8",
            {},
            "content",
        ),
        (
            "create table t9 (pk integer primary key, content text);",
            "t9",
            {},
            "content",
        ),
    ],
)
async def test_label_column_for_table(
    create_sql, table_name, config, expected_label_column
):
    """Test cases for label_column_for_table method"""
    ds = Datasette()
    db = ds.add_database(Database(ds, memory_name="test_label_column_for_table"))
    await db.execute_write_script(create_sql)
    if config:
        ds.config = {"databases": {"test_label_column_for_table": {"tables": config}}}
    actual_label_column = await db.label_column_for_table(table_name)
    if expected_label_column is None:
        assert actual_label_column is None
    else:
        assert actual_label_column == expected_label_column
