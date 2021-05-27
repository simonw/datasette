from datasette.app import Datasette
from datasette.database import Database
from datasette.facets import ColumnFacet, ArrayFacet, DateFacet
from datasette.utils.asgi import Request
from datasette.utils import detect_json1
from .fixtures import app_client  # noqa
import pytest


@pytest.mark.asyncio
async def test_column_facet_suggest(app_client):
    facet = ColumnFacet(
        app_client.ds,
        Request.fake("/"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    suggestions = await facet.suggest()
    assert [
        {"name": "created", "toggle_url": "http://localhost/?_facet=created"},
        {"name": "planet_int", "toggle_url": "http://localhost/?_facet=planet_int"},
        {"name": "on_earth", "toggle_url": "http://localhost/?_facet=on_earth"},
        {"name": "state", "toggle_url": "http://localhost/?_facet=state"},
        {"name": "city_id", "toggle_url": "http://localhost/?_facet=city_id"},
        {"name": "neighborhood", "toggle_url": "http://localhost/?_facet=neighborhood"},
        {"name": "tags", "toggle_url": "http://localhost/?_facet=tags"},
        {
            "name": "complex_array",
            "toggle_url": "http://localhost/?_facet=complex_array",
        },
    ] == suggestions


@pytest.mark.asyncio
async def test_column_facet_suggest_skip_if_already_selected(app_client):
    facet = ColumnFacet(
        app_client.ds,
        Request.fake("/?_facet=planet_int&_facet=on_earth"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    suggestions = await facet.suggest()
    assert [
        {
            "name": "created",
            "toggle_url": "http://localhost/?_facet=planet_int&_facet=on_earth&_facet=created",
        },
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
        {
            "name": "complex_array",
            "toggle_url": "http://localhost/?_facet=planet_int&_facet=on_earth&_facet=complex_array",
        },
    ] == suggestions


@pytest.mark.asyncio
async def test_column_facet_suggest_skip_if_enabled_by_metadata(app_client):
    facet = ColumnFacet(
        app_client.ds,
        Request.fake("/"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
        metadata={"facets": ["city_id"]},
    )
    suggestions = [s["name"] for s in await facet.suggest()]
    assert [
        "created",
        "planet_int",
        "on_earth",
        "state",
        "neighborhood",
        "tags",
        "complex_array",
    ] == suggestions


@pytest.mark.asyncio
async def test_column_facet_results(app_client):
    facet = ColumnFacet(
        app_client.ds,
        Request.fake("/?_facet=city_id"),
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
        Request.fake("/"),
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
        Request.fake("/"),
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
async def test_array_facet_suggest_not_if_all_empty_arrays(app_client):
    facet = ArrayFacet(
        app_client.ds,
        Request.fake("/"),
        database="fixtures",
        sql="select * from facetable where tags = '[]'",
        table="facetable",
    )
    suggestions = await facet.suggest()
    assert [] == suggestions


@pytest.mark.asyncio
@pytest.mark.skipif(not detect_json1(), reason="Requires the SQLite json1 module")
async def test_array_facet_results(app_client):
    facet = ArrayFacet(
        app_client.ds,
        Request.fake("/?_facet_array=tags"),
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


@pytest.mark.asyncio
async def test_date_facet_results(app_client):
    facet = DateFacet(
        app_client.ds,
        Request.fake("/?_facet_date=created"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    buckets, timed_out = await facet.facet_results()
    assert [] == timed_out
    assert {
        "created": {
            "name": "created",
            "type": "date",
            "results": [
                {
                    "value": "2019-01-14",
                    "label": "2019-01-14",
                    "count": 4,
                    "toggle_url": "http://localhost/?_facet_date=created&created__date=2019-01-14",
                    "selected": False,
                },
                {
                    "value": "2019-01-15",
                    "label": "2019-01-15",
                    "count": 4,
                    "toggle_url": "http://localhost/?_facet_date=created&created__date=2019-01-15",
                    "selected": False,
                },
                {
                    "value": "2019-01-17",
                    "label": "2019-01-17",
                    "count": 4,
                    "toggle_url": "http://localhost/?_facet_date=created&created__date=2019-01-17",
                    "selected": False,
                },
                {
                    "value": "2019-01-16",
                    "label": "2019-01-16",
                    "count": 3,
                    "toggle_url": "http://localhost/?_facet_date=created&created__date=2019-01-16",
                    "selected": False,
                },
            ],
            "hideable": True,
            "toggle_url": "/",
            "truncated": False,
        }
    } == buckets


@pytest.mark.asyncio
async def test_json_array_with_blanks_and_nulls():
    ds = Datasette([], memory=True)
    db = ds.add_database(Database(ds, memory_name="test_json_array"))
    await db.execute_write("create table foo(json_column text)", block=True)
    for value in ('["a", "b", "c"]', '["a", "b"]', "", None):
        await db.execute_write(
            "insert into foo (json_column) values (?)", [value], block=True
        )
    response = await ds.client.get("/test_json_array/foo.json")
    data = response.json()
    assert data["suggested_facets"] == [
        {
            "name": "json_column",
            "type": "array",
            "toggle_url": "http://localhost/test_json_array/foo.json?_facet_array=json_column",
        }
    ]


@pytest.mark.asyncio
async def test_facet_size():
    ds = Datasette([], memory=True, config={"max_returned_rows": 50})
    db = ds.add_database(Database(ds, memory_name="test_facet_size"))
    await db.execute_write(
        "create table neighbourhoods(city text, neighbourhood text)", block=True
    )
    for i in range(1, 51):
        for j in range(1, 4):
            await db.execute_write(
                "insert into neighbourhoods (city, neighbourhood) values (?, ?)",
                ["City {}".format(i), "Neighbourhood {}".format(j)],
                block=True,
            )
    response = await ds.client.get("/test_facet_size/neighbourhoods.json")
    data = response.json()
    assert data["suggested_facets"] == [
        {
            "name": "neighbourhood",
            "toggle_url": "http://localhost/test_facet_size/neighbourhoods.json?_facet=neighbourhood",
        }
    ]
    # Bump up _facet_size= to suggest city too
    response2 = await ds.client.get(
        "/test_facet_size/neighbourhoods.json?_facet_size=50"
    )
    data2 = response2.json()
    assert sorted(data2["suggested_facets"], key=lambda f: f["name"]) == [
        {
            "name": "city",
            "toggle_url": "http://localhost/test_facet_size/neighbourhoods.json?_facet_size=50&_facet=city",
        },
        {
            "name": "neighbourhood",
            "toggle_url": "http://localhost/test_facet_size/neighbourhoods.json?_facet_size=50&_facet=neighbourhood",
        },
    ]
    # Facet by city should return expected number of results
    response3 = await ds.client.get(
        "/test_facet_size/neighbourhoods.json?_facet_size=50&_facet=city"
    )
    data3 = response3.json()
    assert len(data3["facet_results"]["city"]["results"]) == 50
    # Reduce max_returned_rows and check that it's respected
    ds._settings["max_returned_rows"] = 20
    response4 = await ds.client.get(
        "/test_facet_size/neighbourhoods.json?_facet_size=50&_facet=city"
    )
    data4 = response4.json()
    assert len(data4["facet_results"]["city"]["results"]) == 20
    # Test _facet_size=max
    response5 = await ds.client.get(
        "/test_facet_size/neighbourhoods.json?_facet_size=max&_facet=city"
    )
    data5 = response5.json()
    assert len(data5["facet_results"]["city"]["results"]) == 20
