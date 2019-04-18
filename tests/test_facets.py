from datasette.facets import ColumnFacet
from .fixtures import app_client  # noqa
from .utils import MockRequest
from collections import namedtuple
import pytest


@pytest.mark.asyncio
async def test_column_facet_suggest(app_client):
    facet = ColumnFacet(
        app_client.ds, MockRequest("http://localhost/"), "fixtures", "facetable", []
    )
    suggestions = await facet.suggest(
        "select * from facetable",
        [],
        await app_client.ds.table_count("fixtures", "facetable"),
    )
    assert [
        {"name": "planet_int", "toggle_url": "http://localhost/?_facet=planet_int"},
        {"name": "on_earth", "toggle_url": "http://localhost/?_facet=on_earth"},
        {"name": "state", "toggle_url": "http://localhost/?_facet=state"},
        {"name": "city_id", "toggle_url": "http://localhost/?_facet=city_id"},
        {"name": "neighborhood", "toggle_url": "http://localhost/?_facet=neighborhood"},
        {"name": "tags", "toggle_url": "http://localhost/?_facet=tags"},
    ] == suggestions


@pytest.mark.asyncio
async def test_column_facet_results(app_client):
    facet = ColumnFacet(
        app_client.ds,
        MockRequest("http://localhost/?_facet=city_id"),
        "fixtures",
        "facetable",
        [{"single": "city_id"}],
    )
    buckets, timed_out = await facet.facet_results(
        """
        select * from facetable
    """,
        [],
    )
    assert [] == timed_out
    assert {
        "city_id": {
            "name": "city_id",
            "results": [
                {
                    "value": 1,
                    "label": "San Francisco",
                    "count": 6,
                    "toggle_url": "http://localhost/?_facet=city_id?_facet=city_id&city_id=1",
                    "selected": False,
                },
                {
                    "value": 2,
                    "label": "Los Angeles",
                    "count": 4,
                    "toggle_url": "http://localhost/?_facet=city_id?_facet=city_id&city_id=2",
                    "selected": False,
                },
                {
                    "value": 3,
                    "label": "Detroit",
                    "count": 4,
                    "toggle_url": "http://localhost/?_facet=city_id?_facet=city_id&city_id=3",
                    "selected": False,
                },
                {
                    "value": 4,
                    "label": "Memnonia",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=city_id?_facet=city_id&city_id=4",
                    "selected": False,
                },
            ],
            "truncated": False,
        }
    } == buckets
