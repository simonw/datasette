"""
Tests for the datasette.database.Database class
"""

from datasette.app import Datasette
from datasette.database import Database, Results, MultipleValues
from datasette.utils.sqlite import sqlite3
from datasette.utils import Column
from .fixtures import app_client, app_client_two_attached_databases_crossdb_enabled
import pytest
import time
import uuid


@pytest.fixture
def db(app_client):
    return app_client.ds.get_database("fixtures")


@pytest.mark.asyncio
async def test_execute(db):
    results = await db.execute("select * from facetable")
    assert isinstance(results, Results)
    assert 15 == len(results)


@pytest.mark.asyncio
async def test_results_first(db):
    assert None is (await db.execute("select * from facetable where pk > 100")).first()
    results = await db.execute("select * from facetable")
    row = results.first()
    assert isinstance(row, sqlite3.Row)


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", (True, False))
async def test_results_bool(db, expected):
    where = "" if expected else "where pk = 0"
    results = await db.execute("select * from facetable {}".format(where))
    assert bool(results) is expected


@pytest.mark.parametrize(
    "query,expected",
    [
        ("select 1", 1),
        ("select 1, 2", None),
        ("select 1 as num union select 2 as num", None),
    ],
)
@pytest.mark.asyncio
async def test_results_single_value(db, query, expected):
    results = await db.execute(query)
    if expected:
        assert expected == results.single_value()
    else:
        with pytest.raises(MultipleValues):
            results.single_value()


@pytest.mark.asyncio
async def test_execute_fn(db):
    def get_1_plus_1(conn):
        return conn.execute("select 1 + 1").fetchall()[0][0]

    assert 2 == await db.execute_fn(get_1_plus_1)


@pytest.mark.asyncio
async def test_execute_fn_transaction_false():
    datasette = Datasette(memory=True)
    db = datasette.add_memory_database("test_execute_fn_transaction_false")

    def run(conn):
        try:
            with conn:
                conn.execute("create table foo (id integer primary key)")
                conn.execute("insert into foo (id) values (44)")
                # Table should exist
                assert (
                    conn.execute(
                        'select count(*) from sqlite_master where name = "foo"'
                    ).fetchone()[0]
                    == 1
                )
                assert conn.execute("select id from foo").fetchall()[0][0] == 44
                raise ValueError("Cancel commit")
        except ValueError:
            pass
        # Row should NOT exist
        assert conn.execute("select count(*) from foo").fetchone()[0] == 0

    await db.execute_write_fn(run, transaction=False)


@pytest.mark.parametrize(
    "tables,exists",
    (
        (["facetable", "searchable", "tags", "searchable_tags"], True),
        (["foo", "bar", "baz"], False),
    ),
)
@pytest.mark.asyncio
async def test_table_exists(db, tables, exists):
    for table in tables:
        actual = await db.table_exists(table)
        assert exists == actual


@pytest.mark.parametrize(
    "view,expected",
    (
        ("not_a_view", False),
        ("paginated_view", True),
    ),
)
@pytest.mark.asyncio
async def test_view_exists(db, view, expected):
    actual = await db.view_exists(view)
    assert actual == expected


@pytest.mark.parametrize(
    "table,expected",
    (
        (
            "facetable",
            [
                "pk",
                "created",
                "planet_int",
                "on_earth",
                "state",
                "_city_id",
                "_neighborhood",
                "tags",
                "complex_array",
                "distinct_some_null",
                "n",
            ],
        ),
        (
            "sortable",
            [
                "pk1",
                "pk2",
                "content",
                "sortable",
                "sortable_with_nulls",
                "sortable_with_nulls_2",
                "text",
            ],
        ),
    ),
)
@pytest.mark.asyncio
async def test_table_columns(db, table, expected):
    columns = await db.table_columns(table)
    assert columns == expected


@pytest.mark.parametrize(
    "table,expected",
    (
        (
            "facetable",
            [
                Column(
                    cid=0,
                    name="pk",
                    type="integer",
                    notnull=0,
                    default_value=None,
                    is_pk=1,
                    hidden=0,
                ),
                Column(
                    cid=1,
                    name="created",
                    type="text",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=2,
                    name="planet_int",
                    type="integer",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=3,
                    name="on_earth",
                    type="integer",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=4,
                    name="state",
                    type="text",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=5,
                    name="_city_id",
                    type="integer",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=6,
                    name="_neighborhood",
                    type="text",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=7,
                    name="tags",
                    type="text",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=8,
                    name="complex_array",
                    type="text",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=9,
                    name="distinct_some_null",
                    type="",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=10,
                    name="n",
                    type="text",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
            ],
        ),
        (
            "sortable",
            [
                Column(
                    cid=0,
                    name="pk1",
                    type="varchar(30)",
                    notnull=0,
                    default_value=None,
                    is_pk=1,
                    hidden=0,
                ),
                Column(
                    cid=1,
                    name="pk2",
                    type="varchar(30)",
                    notnull=0,
                    default_value=None,
                    is_pk=2,
                    hidden=0,
                ),
                Column(
                    cid=2,
                    name="content",
                    type="text",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=3,
                    name="sortable",
                    type="integer",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=4,
                    name="sortable_with_nulls",
                    type="real",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=5,
                    name="sortable_with_nulls_2",
                    type="real",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
                Column(
                    cid=6,
                    name="text",
                    type="text",
                    notnull=0,
                    default_value=None,
                    is_pk=0,
                    hidden=0,
                ),
            ],
        ),
    ),
)
@pytest.mark.asyncio
async def test_table_column_details(db, table, expected):
    columns = await db.table_column_details(table)
    # Convert "type" to lowercase before comparison
    # https://github.com/simonw/datasette/issues/1647
    compare_columns = [
        Column(
            c.cid, c.name, c.type.lower(), c.notnull, c.default_value, c.is_pk, c.hidden
        )
        for c in columns
    ]
    assert compare_columns == expected


@pytest.mark.asyncio
async def test_get_all_foreign_keys(db):
    all_foreign_keys = await db.get_all_foreign_keys()
    assert all_foreign_keys["roadside_attraction_characteristics"] == {
        "incoming": [],
        "outgoing": [
            {
                "other_table": "attraction_characteristic",
                "column": "characteristic_id",
                "other_column": "pk",
            },
            {
                "other_table": "roadside_attractions",
                "column": "attraction_id",
                "other_column": "pk",
            },
        ],
    }
    assert all_foreign_keys["attraction_characteristic"] == {
        "incoming": [
            {
                "other_table": "roadside_attraction_characteristics",
                "column": "pk",
                "other_column": "characteristic_id",
            }
        ],
        "outgoing": [],
    }
    assert all_foreign_keys["compound_primary_key"] == {
        # No incoming because these are compound foreign keys, which we currently ignore
        "incoming": [],
        "outgoing": [],
    }
    assert all_foreign_keys["foreign_key_references"] == {
        "incoming": [],
        "outgoing": [
            {
                "other_table": "primary_key_multiple_columns",
                "column": "foreign_key_with_no_label",
                "other_column": "id",
            },
            {
                "other_table": "simple_primary_key",
                "column": "foreign_key_with_blank_label",
                "other_column": "id",
            },
            {
                "other_table": "simple_primary_key",
                "column": "foreign_key_with_label",
                "other_column": "id",
            },
        ],
    }


@pytest.mark.asyncio
async def test_table_names(db):
    table_names = await db.table_names()
    assert table_names == [
        "simple_primary_key",
        "primary_key_multiple_columns",
        "primary_key_multiple_columns_explicit_label",
        "compound_primary_key",
        "compound_three_primary_keys",
        "foreign_key_references",
        "sortable",
        "no_primary_key",
        "123_starts_with_digits",
        "Table With Space In Name",
        "table/with/slashes.csv",
        "complex_foreign_keys",
        "custom_foreign_key_label",
        "units",
        "tags",
        "searchable",
        "searchable_tags",
        "searchable_fts",
        "searchable_fts_segments",
        "searchable_fts_segdir",
        "searchable_fts_docsize",
        "searchable_fts_stat",
        "select",
        "infinity",
        "facet_cities",
        "facetable",
        "binary_data",
        "roadside_attractions",
        "attraction_characteristic",
        "roadside_attraction_characteristics",
    ]


@pytest.mark.asyncio
async def test_view_names(db):
    view_names = await db.view_names()
    assert view_names == [
        "paginated_view",
        "simple_view",
        "searchable_view",
        "searchable_view_configured_by_metadata",
    ]


@pytest.mark.asyncio
async def test_execute_write_block_true(db):
    await db.execute_write(
        "update roadside_attractions set name = ? where pk = ?", ["Mystery!", 1]
    )
    rows = await db.execute("select name from roadside_attractions where pk = 1")
    assert "Mystery!" == rows.rows[0][0]


@pytest.mark.asyncio
async def test_execute_write_block_false(db):
    await db.execute_write(
        "update roadside_attractions set name = ? where pk = ?",
        ["Mystery!", 1],
    )
    time.sleep(0.1)
    rows = await db.execute("select name from roadside_attractions where pk = 1")
    assert "Mystery!" == rows.rows[0][0]


@pytest.mark.asyncio
async def test_execute_write_script(db):
    await db.execute_write_script(
        "create table foo (id integer primary key); create table bar (id integer primary key);"
    )
    table_names = await db.table_names()
    assert {"foo", "bar"}.issubset(table_names)


@pytest.mark.asyncio
async def test_execute_write_many(db):
    await db.execute_write_script("create table foomany (id integer primary key)")
    await db.execute_write_many(
        "insert into foomany (id) values (?)", [(1,), (10,), (100,)]
    )
    result = await db.execute("select * from foomany")
    assert [r[0] for r in result.rows] == [1, 10, 100]


@pytest.mark.asyncio
async def test_execute_write_has_correctly_prepared_connection(db):
    # The sleep() function is only available if ds._prepare_connection() was called
    await db.execute_write("select sleep(0.01)")


@pytest.mark.asyncio
async def test_execute_write_fn_block_false(db):
    def write_fn(conn):
        conn.execute("delete from roadside_attractions where pk = 1;")
        row = conn.execute("select count(*) from roadside_attractions").fetchone()
        return row[0]

    task_id = await db.execute_write_fn(write_fn, block=False)
    assert isinstance(task_id, uuid.UUID)


@pytest.mark.asyncio
async def test_execute_write_fn_block_true(db):
    def write_fn(conn):
        conn.execute("delete from roadside_attractions where pk = 1;")
        row = conn.execute("select count(*) from roadside_attractions").fetchone()
        return row[0]

    new_count = await db.execute_write_fn(write_fn)
    assert 3 == new_count


@pytest.mark.asyncio
async def test_execute_write_fn_exception(db):
    def write_fn(conn):
        assert False

    with pytest.raises(AssertionError):
        await db.execute_write_fn(write_fn)


@pytest.mark.asyncio
@pytest.mark.timeout(1)
async def test_execute_write_fn_connection_exception(tmpdir, app_client):
    path = str(tmpdir / "immutable.db")
    sqlite3.connect(path).execute("vacuum")
    db = Database(app_client.ds, path=path, is_mutable=False)
    app_client.ds.add_database(db, name="immutable-db")

    def write_fn(conn):
        assert False

    with pytest.raises(AssertionError):
        await db.execute_write_fn(write_fn)

    app_client.ds.remove_database("immutable-db")


def table_exists(conn, name):
    return bool(
        conn.execute(
            """
        with all_tables as (
            select name from sqlite_master where type = 'table'
                     union all
            select name from temp.sqlite_master where type = 'table'
        )
        select 1 from all_tables where name = ?
        """,
            (name,),
        ).fetchall(),
    )


def table_exists_checker(name):
    def inner(conn):
        return table_exists(conn, name)

    return inner


@pytest.mark.asyncio
@pytest.mark.parametrize("disable_threads", (False, True))
async def test_execute_isolated(db, disable_threads):
    if disable_threads:
        ds = Datasette(memory=True, settings={"num_sql_threads": 0})
        db = ds.add_database(Database(ds, memory_name="test_num_sql_threads_zero"))

    # Create temporary table in write
    await db.execute_write(
        "create temporary table created_by_write (id integer primary key)"
    )
    # Should stay visible to write connection
    assert await db.execute_write_fn(table_exists_checker("created_by_write"))

    def create_shared_table(conn):
        conn.execute("create table shared (id integer primary key)")
        # And a temporary table that should not continue to exist
        conn.execute(
            "create temporary table created_by_isolated (id integer primary key)"
        )
        assert table_exists(conn, "created_by_isolated")
        # Also confirm that created_by_write does not exist
        return table_exists(conn, "created_by_write")

    # shared should not exist
    assert not await db.execute_fn(table_exists_checker("shared"))

    # Create it using isolated
    created_by_write_exists = await db.execute_isolated_fn(create_shared_table)
    assert not created_by_write_exists

    # shared SHOULD exist now
    assert await db.execute_fn(table_exists_checker("shared"))

    # created_by_isolated should not exist, even in write connection
    assert not await db.execute_write_fn(table_exists_checker("created_by_isolated"))

    # ... and a second call to isolated should not see that connection either
    assert not await db.execute_isolated_fn(table_exists_checker("created_by_isolated"))


@pytest.mark.asyncio
async def test_mtime_ns(db):
    assert isinstance(db.mtime_ns, int)


def test_mtime_ns_is_none_for_memory(app_client):
    memory_db = Database(app_client.ds, is_memory=True)
    assert memory_db.is_memory is True
    assert None is memory_db.mtime_ns


def test_is_mutable(app_client):
    assert Database(app_client.ds, is_memory=True).is_mutable is True
    assert Database(app_client.ds, is_memory=True, is_mutable=True).is_mutable is True
    assert Database(app_client.ds, is_memory=True, is_mutable=False).is_mutable is False


@pytest.mark.asyncio
async def test_attached_databases(app_client_two_attached_databases_crossdb_enabled):
    database = app_client_two_attached_databases_crossdb_enabled.ds.get_database(
        "_memory"
    )
    attached = await database.attached_databases()
    assert {a.name for a in attached} == {"extra database", "fixtures"}


@pytest.mark.asyncio
async def test_database_memory_name(app_client):
    ds = app_client.ds
    foo1 = ds.add_database(Database(ds, memory_name="foo"))
    foo2 = ds.add_memory_database("foo")
    bar1 = ds.add_database(Database(ds, memory_name="bar"))
    bar2 = ds.add_memory_database("bar")
    for db in (foo1, foo2, bar1, bar2):
        table_names = await db.table_names()
        assert table_names == []
    # Now create a table in foo
    await foo1.execute_write("create table foo (t text)")
    assert await foo1.table_names() == ["foo"]
    assert await foo2.table_names() == ["foo"]
    assert await bar1.table_names() == []
    assert await bar2.table_names() == []


@pytest.mark.asyncio
async def test_in_memory_databases_forbid_writes(app_client):
    ds = app_client.ds
    db = ds.add_database(Database(ds, memory_name="test"))
    with pytest.raises(sqlite3.OperationalError):
        await db.execute("create table foo (t text)")
    assert await db.table_names() == []
    # Using db.execute_write() should work:
    await db.execute_write("create table foo (t text)")
    assert await db.table_names() == ["foo"]
