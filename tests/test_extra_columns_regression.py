import pytest


@pytest.mark.asyncio
async def test_columns_extra_with_count_and_primary_keys(ds_client):
    response = await ds_client.get(
        "/fixtures/primary_key_multiple_columns.json?_extra=columns,count,primary_keys"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["columns"] == ["id", "content", "content2"]
    assert data["count"] == 1
    assert data["primary_keys"] == ["id"]
