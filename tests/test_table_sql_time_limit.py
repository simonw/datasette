"""
A table page whose query takes longer than sql_time_limit_ms answers 400
with the same "SQL query took too long" error as the query page, not 500.
"""

import time

import pytest
import pytest_asyncio

from datasette import hookimpl
from datasette.app import Datasette
from datasette.plugins import pm

MESSAGE = (
    "SQL query took too long. The time limit is"
    " controlled by the sql_time_limit_ms setting."
)


class SleepFunction:
    __name__ = "SleepFunction"

    @staticmethod
    @hookimpl
    def prepare_connection(conn):
        conn.create_function("slow_down", 1, lambda n: time.sleep(float(n)))


@pytest_asyncio.fixture
async def ds():
    pm.register(SleepFunction(), name="test_table_sql_time_limit_sleep")
    try:
        ds = Datasette(settings={"sql_time_limit_ms": 50})
        db = ds.add_memory_database("table_time_limit")
        await db.execute_write_script("""
            create table big (id integer primary key, value real, label text);
            with recursive n(i) as (
                select 1 union all select i + 1 from n where i < 200000
            )
            insert into big (value, label) select random(), 'row ' || i from n;
        """)
        yield ds
    finally:
        pm.unregister(name="test_table_sql_time_limit_sleep")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    (
        # Sorting 200,000 rows by a column with no index
        "_sort=value&_timelimit=1",
        "_sort_desc=label&_timelimit=1",
        # A filter that takes 10ms a row
        "_where=slow_down(0.01)+is+null&_timelimit=5",
    ),
)
async def test_slow_table_query_is_a_400(ds, query):
    response = await ds.client.get(f"/table_time_limit/big.json?{query}")
    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error": MESSAGE,
        "errors": [MESSAGE],
        "status": 400,
    }
    html = await ds.client.get(f"/table_time_limit/big?{query}")
    assert html.status_code == 400
    assert "SQL query took too long" in html.text
    assert "sql_time_limit_ms" in html.text
    # The query is shown, escaped
    assert "<textarea" in html.text
    assert "from big" in html.text


@pytest.mark.asyncio
async def test_fast_table_query_still_works(ds):
    response = await ds.client.get("/table_time_limit/big.json?_size=2&id__lt=3")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["rows"]] == [1, 2]
