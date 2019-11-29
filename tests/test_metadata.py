from copy import deepcopy
from marshmallow import ValidationError
import pytest
from datasette.metadata import get_metadata_schema
from .fixtures import METADATA, app_client


@pytest.mark.asyncio
async def test_valid(app_client):
    Schema = await get_metadata_schema(app_client.ds)
    schema = Schema()
    assert isinstance(schema.load(METADATA), dict)


@pytest.mark.asyncio
async def test_empty(app_client):
    Schema = await get_metadata_schema(app_client.ds)
    schema = Schema()
    assert isinstance(schema.load({}), dict)


@pytest.mark.asyncio
async def test_unexpected_key(app_client):
    Schema = await get_metadata_schema(app_client.ds)
    schema = Schema()
    with pytest.raises(ValidationError):
        schema.load({**METADATA, **{"foo": "bar"}})


@pytest.mark.asyncio
async def test_unexpected_database(app_client):
    Schema = await get_metadata_schema(app_client.ds)
    schema = Schema()
    with pytest.raises(ValidationError):
        md = deepcopy(METADATA)
        md["databases"]["not_a_db"] = {}
        schema.load(md)


@pytest.mark.asyncio
async def test_unexpected_table(app_client):
    Schema = await get_metadata_schema(app_client.ds)
    schema = Schema()
    with pytest.raises(ValidationError):
        md = deepcopy(METADATA)
        md["databases"]["fixtures"]["tables"]["not_a_table"] = {}
        schema.load(md)


@pytest.mark.asyncio
async def test_unexpected_table_fts_table(app_client):
    Schema = await get_metadata_schema(app_client.ds)
    schema = Schema()
    with pytest.raises(ValidationError):
        md = deepcopy(METADATA)
        md["databases"]["fixtures"]["tables"]["sortable"]["fts_table"] = "not_a_table"
        schema.load(md)


@pytest.mark.asyncio
async def test_unexpected_column_units(app_client):
    Schema = await get_metadata_schema(app_client.ds)
    schema = Schema()
    with pytest.raises(ValidationError):
        md = deepcopy(METADATA)
        md["databases"]["fixtures"]["tables"]["units"]["units"]["not_a_column"] = "Hz"
        schema.load(md)


@pytest.mark.asyncio
async def test_unexpected_column_sortable_columns(app_client):
    Schema = await get_metadata_schema(app_client.ds)
    schema = Schema()
    with pytest.raises(ValidationError):
        md = deepcopy(METADATA)
        md["databases"]["fixtures"]["tables"]["sortable"]["sortable_columns"].append(
            "not_a_column"
        )
        schema.load(md)


@pytest.mark.asyncio
async def test_unexpected_column_label_column(app_client):
    Schema = await get_metadata_schema(app_client.ds)
    schema = Schema()
    with pytest.raises(ValidationError):
        md = deepcopy(METADATA)
        md["databases"]["fixtures"]["tables"]["sortable"][
            "label_column"
        ] = "not_a_column"
        schema.load(md)


@pytest.mark.asyncio
async def test_unexpected_column_fts_pk(app_client):
    Schema = await get_metadata_schema(app_client.ds)
    schema = Schema()
    with pytest.raises(ValidationError):
        md = deepcopy(METADATA)
        md["databases"]["fixtures"]["tables"]["sortable"]["fts_pk"] = "not_a_column"
        schema.load(md)
