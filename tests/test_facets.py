from datasette.facets import ColumnFacet, ArrayFacet
from datasette.utils import detect_json1
from .fixtures import app_client  # noqa
from .utils import MockRequest
from collections import namedtuple
import pytest


@pytest.mark.asyncio
async def test_column_facet_suggest(app_client):
    facet = ColumnFacet(
        app_client.ds,
        MockRequest("http://localhost/"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    suggestions = await facet.suggest()
    assert [
        {"name": "planet_int", "toggle_url": "http://localhost/?_facet=planet_int"},
        {"name": "on_earth", "toggle_url": "http://localhost/?_facet=on_earth"},
        {"name": "state", "toggle_url": "http://localhost/?_facet=state"},
        {"name": "city_id", "toggle_url": "http://localhost/?_facet=city_id"},
        {"name": "neighborhood", "toggle_url": "http://localhost/?_facet=neighborhood"},
        {"name": "tags", "toggle_url": "http://localhost/?_facet=tags"},
    ] == suggestions


@pytest.mark.asyncio
async def test_column_facet_suggest_skip_if_already_selected(app_client):
    facet = ColumnFacet(
        app_client.ds,
        MockRequest("http://localhost/?_facet=planet_int&_facet=on_earth"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    suggestions = await facet.suggest()
    assert [
        {
            "name": "state",
            "toggle_url": "http://localhost/?_facet=planet_int&_facet=on_earth&_facet=state",
        },
        {
            "name": "city_id",
            "toggle_url": "http://localhost/?_facet=planet_int&_facet=on_earth&_facet=city_id",
        },
        {
            "name": "neighborhood",
            "toggle_url": "http://localhost/?_facet=planet_int&_facet=on_earth&_facet=neighborhood",
        },
        {
            "name": "tags",
            "toggle_url": "http://localhost/?_facet=planet_int&_facet=on_earth&_facet=tags",
        },
    ] == suggestions


@pytest.mark.asyncio
async def test_column_facet_suggest_skip_if_enabled_by_metadata(app_client):
    facet = ColumnFacet(
        app_client.ds,
        MockRequest("http://localhost/"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
        metadata={"facets": ["city_id"]},
    )
    suggestions = [s["name"] for s in await facet.suggest()]
    assert ["planet_int", "on_earth", "state", "neighborhood", "tags"] == suggestions


@pytest.mark.asyncio
async def test_column_facet_results(app_client):
    facet = ColumnFacet(
        app_client.ds,
        MockRequest("http://localhost/?_facet=city_id"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    buckets, timed_out = await facet.facet_results()
    assert [] == timed_out
    assert {
        "city_id": {
            "name": "city_id",
            "type": "column",
            "hideable": True,
            "toggle_url": "/",
            "results": [
                {
                    "value": 1,
                    "label": "San Francisco",
                    "count": 6,
                    "toggle_url": "http://localhost/?_facet=city_id&city_id=1",
                    "selected": False,
                },
                {
                    "value": 2,
                    "label": "Los Angeles",
                    "count": 4,
                    "toggle_url": "http://localhost/?_facet=city_id&city_id=2",
                    "selected": False,
                },
                {
                    "value": 3,
                    "label": "Detroit",
                    "count": 4,
                    "toggle_url": "http://localhost/?_facet=city_id&city_id=3",
                    "selected": False,
                },
                {
                    "value": 4,
                    "label": "Memnonia",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=city_id&city_id=4",
                    "selected": False,
                },
            ],
            "truncated": False,
        }
    } == buckets


@pytest.mark.asyncio
async def test_column_facet_from_metadata_cannot_be_hidden(app_client):
    facet = ColumnFacet(
        app_client.ds,
        MockRequest("http://localhost/"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
        metadata={"facets": ["city_id"]},
    )
    buckets, timed_out = await facet.facet_results()
    assert [] == timed_out
    assert {
        "city_id": {
            "name": "city_id",
            "type": "column",
            "hideable": False,
            "toggle_url": "/",
            "results": [
                {
                    "value": 1,
                    "label": "San Francisco",
                    "count": 6,
                    "toggle_url": "http://localhost/?city_id=1",
                    "selected": False,
                },
                {
                    "value": 2,
                    "label": "Los Angeles",
                    "count": 4,
                    "toggle_url": "http://localhost/?city_id=2",
                    "selected": False,
                },
                {
                    "value": 3,
                    "label": "Detroit",
                    "count": 4,
                    "toggle_url": "http://localhost/?city_id=3",
                    "selected": False,
                },
                {
                    "value": 4,
                    "label": "Memnonia",
                    "count": 1,
                    "toggle_url": "http://localhost/?city_id=4",
                    "selected": False,
                },
            ],
            "truncated": False,
        }
    } == buckets


@pytest.mark.asyncio
@pytest.mark.skipif(not detect_json1(), reason="Requires the SQLite json1 module")
async def test_array_facet_suggest(app_client):
    facet = ArrayFacet(
        app_client.ds,
        MockRequest("http://localhost/"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    suggestions = await facet.suggest()
    assert [
        {
            "name": "tags",
            "type": "array",
            "toggle_url": "http://localhost/?_facet_array=tags",
        }
    ] == suggestions


@pytest.mark.asyncio
@pytest.mark.skipif(not detect_json1(), reason="Requires the SQLite json1 module")
async def test_array_facet_results(app_client):
    facet = ArrayFacet(
        app_client.ds,
        MockRequest("http://localhost/?_facet_array=tags"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    buckets, timed_out = await facet.facet_results()
    assert [] == timed_out
    assert {
        "tags": {
            "name": "tags",
            "type": "array",
            "results": [
                {
                    "value": "tag1",
                    "label": "tag1",
                    "count": 2,
                    "toggle_url": "http://localhost/?_facet_array=tags&tags__arraycontains=tag1",
                    "selected": False,
                },
                {
                    "value": "tag2",
                    "label": "tag2",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet_array=tags&tags__arraycontains=tag2",
                    "selected": False,
                },
                {
                    "value": "tag3",
                    "label": "tag3",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet_array=tags&tags__arraycontains=tag3",
                    "selected": False,
                },
            ],
            "hideable": True,
            "toggle_url": "/",
            "truncated": False,
        }
    } == buckets
