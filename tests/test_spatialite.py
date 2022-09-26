from datasette.app import Datasette
from datasette.utils import find_spatialite, SpatialiteNotFound, SPATIALITE_FUNCTIONS
from .utils import has_load_extension
import pytest


def has_spatialite():
    try:
        find_spatialite()
        return True
    except SpatialiteNotFound:
        return False


@pytest.mark.asyncio
@pytest.mark.skipif(not has_spatialite(), reason="Requires SpatiaLite")
@pytest.mark.skipif(not has_load_extension(), reason="Requires enable_load_extension")
async def test_spatialite_version_info():
    ds = Datasette(sqlite_extensions=["spatialite"])
    response = await ds.client.get("/-/versions.json")
    assert response.status_code == 200
    spatialite = response.json()["sqlite"]["extensions"]["spatialite"]
    assert set(SPATIALITE_FUNCTIONS) == set(spatialite)
