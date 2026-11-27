"""Examples behind the database execution error documentation."""

import uuid

import pytest

from datasette.app import Datasette
from datasette.database import QueryInterrupted
from datasette.utils.sqlite import sqlite3


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 3])
@pytest.mark.parametrize(
    "method,sql,params,error",
    [
        ("execute", "select * from missing_table", None, sqlite3.OperationalError),
        ("execute", "select ?", [], sqlite3.ProgrammingError),
        ("execute_write", "not valid sql", None, sqlite3.OperationalError),
        (
            "execute_write",
            "insert into items values (?)",
            [1],
            sqlite3.IntegrityError,
        ),
        (
            "execute_write_many",
            "insert into items values (?)",
            [[1]],
            sqlite3.IntegrityError,
        ),
        (
            "execute_write_script",
            "insert into items values (1);",
            None,
            sqlite3.IntegrityError,
        ),
    ],
)
async def test_database_error_classes(num_sql_threads, method, sql, params, error):
    ds = Datasette(settings={"num_sql_threads": num_sql_threads})
    db = ds.add_memory_database(uuid.uuid4().hex, name="errors")
    try:
        await ds.invoke_startup()
        await db.execute_write("create table items(id integer primary key)")
        await db.execute_write("insert into items values (1)")
        kwargs = {"log_sql_errors": False} if method == "execute" else {}
        args = [sql] if params is None else [sql, params]
        with pytest.raises(error) as exc:
            await getattr(db, method)(*args, **kwargs)
        assert isinstance(exc.value, sqlite3.DatabaseError)
        assert (await db.execute("select count(*) from items")).single_value() == 1
    finally:
        ds.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_sql_threads", [0, 3])
async def test_query_interrupted_is_separate_database_error(num_sql_threads):
    ds = Datasette(settings={"num_sql_threads": num_sql_threads})
    db = ds.add_memory_database(uuid.uuid4().hex, name="interruptions")
    sql = """
        with recursive n(x) as (
            values(1) union all select x + 1 from n where x < :limit
        ) select sum(x) from n
    """
    params = {"limit": 100000000}
    try:
        await ds.invoke_startup()
        with pytest.raises(QueryInterrupted) as exc:
            await db.execute(sql, params, custom_time_limit=1, log_sql_errors=False)
        assert not isinstance(exc.value, sqlite3.DatabaseError)
        assert isinstance(exc.value.e, sqlite3.OperationalError)
        assert exc.value.sql == sql
        assert exc.value.params == params
        assert (await db.execute("select 1")).single_value() == 1
    finally:
        ds.close()
