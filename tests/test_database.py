from .fixtures import app_client
import pytest


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
