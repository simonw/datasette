from datasette.app import Datasette
from datasette.database import Database
from datasette.facets import ColumnFacet, ArrayFacet, DateFacet
from datasette.utils.asgi import Request
from datasette.utils import detect_json1
from .fixtures import make_app_client
import json
import pytest


@pytest.mark.asyncio
async def test_column_facet_suggest(ds_client):
    facet = ColumnFacet(
        ds_client.ds,
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
        {"name": "_city_id", "toggle_url": "http://localhost/?_facet=_city_id"},
        {
            "name": "_neighborhood",
            "toggle_url": "http://localhost/?_facet=_neighborhood",
        },
        {"name": "tags", "toggle_url": "http://localhost/?_facet=tags"},
        {
            "name": "complex_array",
            "toggle_url": "http://localhost/?_facet=complex_array",
        },
    ] == suggestions


@pytest.mark.asyncio
async def test_column_facet_suggest_skip_if_already_selected(ds_client):
    facet = ColumnFacet(
        ds_client.ds,
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
            "name": "_city_id",
            "toggle_url": "http://localhost/?_facet=planet_int&_facet=on_earth&_facet=_city_id",
        },
        {
            "name": "_neighborhood",
            "toggle_url": "http://localhost/?_facet=planet_int&_facet=on_earth&_facet=_neighborhood",
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
async def test_column_facet_suggest_skip_if_enabled_by_metadata(ds_client):
    facet = ColumnFacet(
        ds_client.ds,
        Request.fake("/"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
        table_config={"facets": ["_city_id"]},
    )
    suggestions = [s["name"] for s in await facet.suggest()]
    assert [
        "created",
        "planet_int",
        "on_earth",
        "state",
        "_neighborhood",
        "tags",
        "complex_array",
    ] == suggestions


@pytest.mark.asyncio
async def test_column_facet_results(ds_client):
    facet = ColumnFacet(
        ds_client.ds,
        Request.fake("/?_facet=_city_id"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    buckets, timed_out = await facet.facet_results()
    assert [] == timed_out
    assert [
        {
            "name": "_city_id",
            "type": "column",
            "hideable": True,
            "toggle_url": "/",
            "results": [
                {
                    "value": 1,
                    "label": "San Francisco",
                    "count": 6,
                    "toggle_url": "http://localhost/?_facet=_city_id&_city_id__exact=1",
                    "selected": False,
                },
                {
                    "value": 2,
                    "label": "Los Angeles",
                    "count": 4,
                    "toggle_url": "http://localhost/?_facet=_city_id&_city_id__exact=2",
                    "selected": False,
                },
                {
                    "value": 3,
                    "label": "Detroit",
                    "count": 4,
                    "toggle_url": "http://localhost/?_facet=_city_id&_city_id__exact=3",
                    "selected": False,
                },
                {
                    "value": 4,
                    "label": "Memnonia",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_city_id&_city_id__exact=4",
                    "selected": False,
                },
            ],
            "truncated": False,
        }
    ] == buckets


@pytest.mark.asyncio
async def test_column_facet_results_column_starts_with_underscore(ds_client):
    facet = ColumnFacet(
        ds_client.ds,
        Request.fake("/?_facet=_neighborhood"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    buckets, timed_out = await facet.facet_results()
    assert [] == timed_out
    assert buckets == [
        {
            "name": "_neighborhood",
            "type": "column",
            "hideable": True,
            "toggle_url": "/",
            "results": [
                {
                    "value": "Downtown",
                    "label": "Downtown",
                    "count": 2,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Downtown",
                    "selected": False,
                },
                {
                    "value": "Arcadia Planitia",
                    "label": "Arcadia Planitia",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Arcadia+Planitia",
                    "selected": False,
                },
                {
                    "value": "Bernal Heights",
                    "label": "Bernal Heights",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Bernal+Heights",
                    "selected": False,
                },
                {
                    "value": "Corktown",
                    "label": "Corktown",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Corktown",
                    "selected": False,
                },
                {
                    "value": "Dogpatch",
                    "label": "Dogpatch",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Dogpatch",
                    "selected": False,
                },
                {
                    "value": "Greektown",
                    "label": "Greektown",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Greektown",
                    "selected": False,
                },
                {
                    "value": "Hayes Valley",
                    "label": "Hayes Valley",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Hayes+Valley",
                    "selected": False,
                },
                {
                    "value": "Hollywood",
                    "label": "Hollywood",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Hollywood",
                    "selected": False,
                },
                {
                    "value": "Koreatown",
                    "label": "Koreatown",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Koreatown",
                    "selected": False,
                },
                {
                    "value": "Los Feliz",
                    "label": "Los Feliz",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Los+Feliz",
                    "selected": False,
                },
                {
                    "value": "Mexicantown",
                    "label": "Mexicantown",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Mexicantown",
                    "selected": False,
                },
                {
                    "value": "Mission",
                    "label": "Mission",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Mission",
                    "selected": False,
                },
                {
                    "value": "SOMA",
                    "label": "SOMA",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=SOMA",
                    "selected": False,
                },
                {
                    "value": "Tenderloin",
                    "label": "Tenderloin",
                    "count": 1,
                    "toggle_url": "http://localhost/?_facet=_neighborhood&_neighborhood__exact=Tenderloin",
                    "selected": False,
                },
            ],
            "truncated": False,
        }
    ]


@pytest.mark.asyncio
async def test_column_facet_from_metadata_cannot_be_hidden(ds_client):
    facet = ColumnFacet(
        ds_client.ds,
        Request.fake("/"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
        table_config={"facets": ["_city_id"]},
    )
    buckets, timed_out = await facet.facet_results()
    assert [] == timed_out
    assert [
        {
            "name": "_city_id",
            "type": "column",
            "hideable": False,
            "toggle_url": "/",
            "results": [
                {
                    "value": 1,
                    "label": "San Francisco",
                    "count": 6,
                    "toggle_url": "http://localhost/?_city_id__exact=1",
                    "selected": False,
                },
                {
                    "value": 2,
                    "label": "Los Angeles",
                    "count": 4,
                    "toggle_url": "http://localhost/?_city_id__exact=2",
                    "selected": False,
                },
                {
                    "value": 3,
                    "label": "Detroit",
                    "count": 4,
                    "toggle_url": "http://localhost/?_city_id__exact=3",
                    "selected": False,
                },
                {
                    "value": 4,
                    "label": "Memnonia",
                    "count": 1,
                    "toggle_url": "http://localhost/?_city_id__exact=4",
                    "selected": False,
                },
            ],
            "truncated": False,
        }
    ] == buckets


@pytest.mark.asyncio
@pytest.mark.skipif(not detect_json1(), reason="Requires the SQLite json1 module")
async def test_array_facet_suggest(ds_client):
    facet = ArrayFacet(
        ds_client.ds,
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
async def test_array_facet_suggest_not_if_all_empty_arrays(ds_client):
    facet = ArrayFacet(
        ds_client.ds,
        Request.fake("/"),
        database="fixtures",
        sql="select * from facetable where tags = '[]'",
        table="facetable",
    )
    suggestions = await facet.suggest()
    assert [] == suggestions


@pytest.mark.asyncio
@pytest.mark.skipif(not detect_json1(), reason="Requires the SQLite json1 module")
async def test_array_facet_results(ds_client):
    facet = ArrayFacet(
        ds_client.ds,
        Request.fake("/?_facet_array=tags"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    buckets, timed_out = await facet.facet_results()
    assert [] == timed_out
    assert [
        {
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
    ] == buckets


@pytest.mark.asyncio
@pytest.mark.skipif(not detect_json1(), reason="Requires the SQLite json1 module")
async def test_array_facet_handle_duplicate_tags():
    ds = Datasette([], memory=True)
    db = ds.add_database(Database(ds, memory_name="test_array_facet"))
    await db.execute_write("create table otters(name text, tags text)")
    for name, tags in (
        ("Charles", ["friendly", "cunning", "friendly"]),
        ("Shaun", ["cunning", "empathetic", "friendly"]),
        ("Tracy", ["empathetic", "eager"]),
    ):
        await db.execute_write(
            "insert into otters (name, tags) values (?, ?)", [name, json.dumps(tags)]
        )

    response = await ds.client.get("/test_array_facet/otters.json?_facet_array=tags")
    assert response.json()["facet_results"]["results"]["tags"] == {
        "name": "tags",
        "type": "array",
        "results": [
            {
                "value": "cunning",
                "label": "cunning",
                "count": 2,
                "toggle_url": "http://localhost/test_array_facet/otters.json?_facet_array=tags&tags__arraycontains=cunning",
                "selected": False,
            },
            {
                "value": "empathetic",
                "label": "empathetic",
                "count": 2,
                "toggle_url": "http://localhost/test_array_facet/otters.json?_facet_array=tags&tags__arraycontains=empathetic",
                "selected": False,
            },
            {
                "value": "friendly",
                "label": "friendly",
                "count": 2,
                "toggle_url": "http://localhost/test_array_facet/otters.json?_facet_array=tags&tags__arraycontains=friendly",
                "selected": False,
            },
            {
                "value": "eager",
                "label": "eager",
                "count": 1,
                "toggle_url": "http://localhost/test_array_facet/otters.json?_facet_array=tags&tags__arraycontains=eager",
                "selected": False,
            },
        ],
        "hideable": True,
        "toggle_url": "/test_array_facet/otters.json",
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_date_facet_results(ds_client):
    facet = DateFacet(
        ds_client.ds,
        Request.fake("/?_facet_date=created"),
        database="fixtures",
        sql="select * from facetable",
        table="facetable",
    )
    buckets, timed_out = await facet.facet_results()
    assert [] == timed_out
    assert [
        {
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
    ] == buckets


@pytest.mark.asyncio
async def test_json_array_with_blanks_and_nulls():
    ds = Datasette([], memory=True)
    db = ds.add_database(Database(ds, memory_name="test_json_array"))
    await db.execute_write("create table foo(json_column text)")
    for value in ('["a", "b", "c"]', '["a", "b"]', "", None):
        await db.execute_write("insert into foo (json_column) values (?)", [value])
    response = await ds.client.get("/test_json_array/foo.json?_extra=suggested_facets")
    data = response.json()
    assert data["suggested_facets"] == [
        {
            "name": "json_column",
            "type": "array",
            "toggle_url": "http://localhost/test_json_array/foo.json?_extra=suggested_facets&_facet_array=json_column",
        }
    ]


@pytest.mark.asyncio
async def test_facet_size():
    ds = Datasette([], memory=True, settings={"max_returned_rows": 50})
    db = ds.add_database(Database(ds, memory_name="test_facet_size"))
    await db.execute_write("create table neighbourhoods(city text, neighbourhood text)")
    for i in range(1, 51):
        for j in range(1, 4):
            await db.execute_write(
                "insert into neighbourhoods (city, neighbourhood) values (?, ?)",
                ["City {}".format(i), "Neighbourhood {}".format(j)],
            )
    response = await ds.client.get(
        "/test_facet_size/neighbourhoods.json?_extra=suggested_facets"
    )
    data = response.json()
    assert data["suggested_facets"] == [
        {
            "name": "neighbourhood",
            "toggle_url": "http://localhost/test_facet_size/neighbourhoods.json?_extra=suggested_facets&_facet=neighbourhood",
        }
    ]
    # Bump up _facet_size= to suggest city too
    response2 = await ds.client.get(
        "/test_facet_size/neighbourhoods.json?_facet_size=50&_extra=suggested_facets"
    )
    data2 = response2.json()
    assert sorted(data2["suggested_facets"], key=lambda f: f["name"]) == [
        {
            "name": "city",
            "toggle_url": "http://localhost/test_facet_size/neighbourhoods.json?_facet_size=50&_extra=suggested_facets&_facet=city",
        },
        {
            "name": "neighbourhood",
            "toggle_url": "http://localhost/test_facet_size/neighbourhoods.json?_facet_size=50&_extra=suggested_facets&_facet=neighbourhood",
        },
    ]
    # Facet by city should return expected number of results
    response3 = await ds.client.get(
        "/test_facet_size/neighbourhoods.json?_facet_size=50&_facet=city"
    )
    data3 = response3.json()
    assert len(data3["facet_results"]["results"]["city"]["results"]) == 50
    # Reduce max_returned_rows and check that it's respected
    ds._settings["max_returned_rows"] = 20
    response4 = await ds.client.get(
        "/test_facet_size/neighbourhoods.json?_facet_size=50&_facet=city"
    )
    data4 = response4.json()
    assert len(data4["facet_results"]["results"]["city"]["results"]) == 20
    # Test _facet_size=max
    response5 = await ds.client.get(
        "/test_facet_size/neighbourhoods.json?_facet_size=max&_facet=city"
    )
    data5 = response5.json()
    assert len(data5["facet_results"]["results"]["city"]["results"]) == 20
    # Now try messing with facet_size in the table metadata
    orig_metadata = ds._metadata_local
    try:
        ds._metadata_local = {
            "databases": {
                "test_facet_size": {"tables": {"neighbourhoods": {"facet_size": 6}}}
            }
        }
        response6 = await ds.client.get(
            "/test_facet_size/neighbourhoods.json?_facet=city"
        )
        data6 = response6.json()
        assert len(data6["facet_results"]["results"]["city"]["results"]) == 6
        # Setting it to max bumps it up to 50 again
        ds._metadata_local["databases"]["test_facet_size"]["tables"]["neighbourhoods"][
            "facet_size"
        ] = "max"
        data7 = (
            await ds.client.get("/test_facet_size/neighbourhoods.json?_facet=city")
        ).json()
        assert len(data7["facet_results"]["results"]["city"]["results"]) == 20
    finally:
        ds._metadata_local = orig_metadata


def test_other_types_of_facet_in_metadata():
    with make_app_client(
        metadata={
            "databases": {
                "fixtures": {
                    "tables": {
                        "facetable": {
                            "facets": ["state", {"array": "tags"}, {"date": "created"}]
                        }
                    }
                }
            }
        }
    ) as client:
        response = client.get("/fixtures/facetable")
        for fragment in (
            "<strong>created (date)\n",
            "<strong>tags (array)\n",
            "<strong>state\n",
        ):
            assert fragment in response.text


@pytest.mark.asyncio
async def test_conflicting_facet_names_json(ds_client):
    response = await ds_client.get(
        "/fixtures/facetable.json?_facet=created&_facet_date=created"
        "&_facet=tags&_facet_array=tags"
    )
    assert set(response.json()["facet_results"]["results"].keys()) == {
        "created",
        "tags",
        "created_2",
        "tags_2",
    }


@pytest.mark.asyncio
async def test_facet_against_in_memory_database():
    ds = Datasette()
    db = ds.add_memory_database("mem")
    await db.execute_write(
        "create table t (id integer primary key, name text, name2 text)"
    )
    to_insert = [{"name": "one", "name2": "1"} for _ in range(800)] + [
        {"name": "two", "name2": "2"} for _ in range(300)
    ]
    print(to_insert)
    await db.execute_write_many(
        "insert into t (name, name2) values (:name, :name2)", to_insert
    )
    response1 = await ds.client.get("/mem/t")
    assert response1.status_code == 200
    response2 = await ds.client.get("/mem/t?_facet=name&_facet=name2")
    assert response2.status_code == 200
