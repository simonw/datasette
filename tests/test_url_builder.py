import urllib.parse

import pytest

from datasette.app import Datasette


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "42",
        "O'Reilly & :value -- /* ? #",
        "",
        42,
        0,
        -9223372036854775808,
        42.5,
        1e-20,
        float("inf"),
        float("-inf"),
        float("nan"),
        True,
        False,
        None,
        b"\x00\xff'",
    ],
)
async def test_database_query_parameters_preserve_sqlite_types(value):
    ds = Datasette(memory=True)
    try:
        # Compare a directly bound query with the same query sent through a URL.
        # The text comparison catches casts accidentally introducing affinity.
        sql = """select typeof(:value) as type, quote(:value) as quoted,
            :value is :value2 as equal,
            '42' = :value as text_equal, ':value' as literal,
            :value10 as ":value", 1 as [:value2], 2 as `:value10`
            -- :value
            /* ':value' */
        """
        params = {"value": value, "value2": value, "value10": "separate parameter"}
        expected = [
            dict(row) for row in await ds.get_database("_memory").execute(sql, params)
        ]
        url = ds.urls.database_query("_memory", sql, params=params)
        result = await ds.client.get(url.replace("?", ".json?", 1))
        assert result.status_code == 200
        assert result.json()["rows"] == expected
        # Building the URL must not mutate the caller's parameter dictionary.
        assert params["value"] is value
    finally:
        ds.close()


@pytest.mark.parametrize("format", [None, "json", "csv"])
def test_database_query_without_parameters(format):
    ds = Datasette(memory=True, settings={"base_url": "/prefix/"})
    try:
        suffix = "." + format if format else ""
        assert ds.urls.database_query("_memory", "select 1", format=format) == (
            "/prefix/_memory/-/query"
            + suffix
            + "?"
            + urllib.parse.urlencode({"sql": "select 1"})
        )
    finally:
        ds.close()
