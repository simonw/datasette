from .fixtures import app_client
import pytest
import time
import uuid


@pytest.mark.parametrize(
    "tables,exists",
    (
        (["facetable", "searchable", "tags", "searchable_tags"], True),
        (["foo", "bar", "baz"], False),
    ),
)
@pytest.mark.asyncio
async def test_table_exists(app_client, tables, exists):
    db = app_client.ds.databases["fixtures"]
    for table in tables:
        actual = await db.table_exists(table)
        assert exists == actual


@pytest.mark.asyncio
async def test_get_all_foreign_keys(app_client):
    db = app_client.ds.databases["fixtures"]
    all_foreign_keys = await db.get_all_foreign_keys()
    assert {
        "incoming": [],
        "outgoing": [
            {
                "other_table": "attraction_characteristic",
                "column": "characteristic_id",
                "other_column": "pk",
            },
            {
                "other_table": "roadside_attractions",
                "column": "attraction_id",
                "other_column": "pk",
            },
        ],
    } == all_foreign_keys["roadside_attraction_characteristics"]
    assert {
        "incoming": [
            {
                "other_table": "roadside_attraction_characteristics",
                "column": "pk",
                "other_column": "characteristic_id",
            }
        ],
        "outgoing": [],
    } == all_foreign_keys["attraction_characteristic"]


@pytest.mark.asyncio
async def test_table_names(app_client):
    db = app_client.ds.databases["fixtures"]
    table_names = await db.table_names()
    assert [
        "simple_primary_key",
        "primary_key_multiple_columns",
        "primary_key_multiple_columns_explicit_label",
        "compound_primary_key",
        "compound_three_primary_keys",
        "foreign_key_references",
        "sortable",
        "no_primary_key",
        "123_starts_with_digits",
        "Table With Space In Name",
        "table/with/slashes.csv",
        "complex_foreign_keys",
        "custom_foreign_key_label",
        "units",
        "tags",
        "searchable",
        "searchable_tags",
        "searchable_fts",
        "searchable_fts_content",
        "searchable_fts_segments",
        "searchable_fts_segdir",
        "select",
        "infinity",
        "facet_cities",
        "facetable",
        "binary_data",
        "roadside_attractions",
        "attraction_characteristic",
        "roadside_attraction_characteristics",
    ] == table_names


@pytest.mark.asyncio
async def test_execute_write_block_true(app_client):
    db = app_client.ds.databases["fixtures"]
    await db.execute_write(
        "update roadside_attractions set name = ? where pk = ?",
        ["Mystery!", 1],
        block=True,
    )
    rows = await db.execute("select name from roadside_attractions where pk = 1")
    assert "Mystery!" == rows.rows[0][0]


@pytest.mark.asyncio
async def test_execute_write_block_false(app_client):
    db = app_client.ds.databases["fixtures"]
    await db.execute_write(
        "update roadside_attractions set name = ? where pk = ?", ["Mystery!", 1],
    )
    time.sleep(0.1)
    rows = await db.execute("select name from roadside_attractions where pk = 1")
    assert "Mystery!" == rows.rows[0][0]


@pytest.mark.asyncio
async def test_execute_write_fn_block_false(app_client):
    db = app_client.ds.databases["fixtures"]

    def write_fn(conn):
        with conn:
            conn.execute("delete from roadside_attractions where id = 1;")
            row = conn.execute("select count(*) from roadside_attractions").fetchone()
            print("row = ", row)
        return row[0]

    task_id = await db.execute_write_fn(write_fn)
    assert isinstance(task_id, uuid.UUID)


@pytest.mark.asyncio
async def test_execute_write_fn_block_true(app_client):
    db = app_client.ds.databases["fixtures"]

    def write_fn(conn):
        with conn:
            conn.execute("delete from roadside_attractions where id = 1;")
            row = conn.execute("select count(*) from roadside_attractions").fetchone()
            print("row = ", row)
        return row[0]

    new_count = await db.execute_write_fn(write_fn, block=True)
    assert 3 == new_count
