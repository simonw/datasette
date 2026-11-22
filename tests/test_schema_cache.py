"""
Tests for the cache of per-table schema lookups on the Database class.

The cache has to be invisible: a lookup must never return details of a
schema that has since been changed, whoever changed it.
"""

import contextlib

import pytest

from datasette.app import Datasette
from datasette.database import Database, _schema_checks
from datasette.utils.sqlite import sqlite3


@contextlib.contextmanager
def request_scope():
    "Stands in for the scope DatasetteRouter sets up around each request"
    token = _schema_checks.set({})
    try:
        yield
    finally:
        _schema_checks.reset(token)


@pytest.fixture
def trips(monkeypatch):
    "Counts trips to a database thread, as a list of the databases visited"
    visited = []
    original = Database._execute_fn

    async def counting_execute_fn(self, fn):
        visited.append(self.name)
        return await original(self, fn)

    monkeypatch.setattr(Database, "_execute_fn", counting_execute_fn)
    return visited


@pytest.fixture(params=[0, 1, 3], ids=lambda n: f"{n}-sql-threads")
def ds(request, tmp_path):
    db_path = tmp_path / "data.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        create table authors (id integer primary key, name text);
        create table books (
            id integer primary key,
            title text,
            author_id integer references authors(id)
        );
        """)
    conn.close()
    ds = Datasette([str(db_path)], settings={"num_sql_threads": request.param})
    ds._test_db_path = db_path
    yield ds
    ds.close()


@pytest.fixture
def db(ds):
    return ds.get_database("data")


def alter_from_outside(ds, sql):
    "Change the schema the way another process would"
    conn = sqlite3.connect(ds._test_db_path)
    conn.executescript(sql)
    conn.close()


async def all_lookups(db, table):
    return {
        "table_columns": await db.table_columns(table),
        "table_column_details": list((await db.table_column_details(table)).names),
        "primary_keys": await db.primary_keys(table),
        "foreign_keys_for_table": await db.foreign_keys_for_table(table),
        "label_column_for_table": await db.label_column_for_table(table),
        "fts_table": await db.fts_table(table),
    }


async def test_repeated_lookups_in_a_request_share_one_trip(db, trips):
    with request_scope():
        first = await all_lookups(db, "books")
        trips_for_first = len(trips)
        assert await all_lookups(db, "books") == first
        assert len(trips) == trips_for_first
    assert first["table_columns"] == ["id", "title", "author_id"]
    assert first["primary_keys"] == ["id"]
    assert first["label_column_for_table"] == "title"
    assert first["fts_table"] is None
    assert [fk["other_table"] for fk in first["foreign_keys_for_table"]] == ["authors"]


async def test_later_request_checks_schema_then_uses_cache(db, trips):
    with request_scope():
        first = await all_lookups(db, "books")
    del trips[:]
    with request_scope():
        assert await all_lookups(db, "books") == first
    # One trip to confirm the schema is unchanged, none for the lookups
    assert trips == ["data"]


@pytest.mark.parametrize("in_request", (True, False))
async def test_write_through_datasette_is_seen_immediately(db, in_request):
    with request_scope() if in_request else contextlib.nullcontext():
        before = await all_lookups(db, "books")
        assert before["table_columns"] == ["id", "title", "author_id"]
        await db.execute_write("alter table books add column name text")
        await db.execute_write("create unique index books_name on books(name)")
        await db.execute_write("create table publishers (id integer primary key)")
        await db.execute_write(
            "alter table books add column publisher_id integer"
            " references publishers(id)"
        )
        await db.execute_write(
            "create virtual table books_fts using fts5(title, content='books')"
        )
        after = await all_lookups(db, "books")
    assert after["table_columns"] == [
        "id",
        "title",
        "author_id",
        "name",
        "publisher_id",
    ]
    assert after["table_column_details"] == after["table_columns"]
    assert {fk["other_table"] for fk in after["foreign_keys_for_table"]} == {
        "authors",
        "publishers",
    }
    # A single unique text column is preferred over one called title
    assert before["label_column_for_table"] == "title"
    assert after["label_column_for_table"] == "name"
    assert after["fts_table"] == "books_fts"


@pytest.mark.parametrize(
    "write",
    (
        lambda db, fn: db.execute_write_fn(fn),
        lambda db, fn: db.execute_write_fn(fn, transaction=False),
        lambda db, fn: db.execute_isolated_fn(fn),
    ),
    ids=("execute_write_fn", "no-transaction", "execute_isolated_fn"),
)
async def test_every_write_method_invalidates_within_a_request(db, write):
    def add_column(conn):
        conn.execute("alter table books add column pages integer")
        conn.commit()

    with request_scope():
        assert "pages" not in await db.table_columns("books")
        await write(db, add_column)
        assert "pages" in await db.table_columns("books")


async def test_failed_write_still_invalidates(db):
    def add_column_then_fail(conn):
        conn.execute("alter table books add column pages integer")
        conn.commit()
        raise ValueError("after the commit")

    with request_scope():
        assert "pages" not in await db.table_columns("books")
        with pytest.raises(ValueError):
            await db.execute_write_fn(add_column_then_fail, transaction=False)
        assert "pages" in await db.table_columns("books")


async def test_change_by_another_process_is_seen_by_next_request(ds, db):
    with request_scope():
        assert await db.primary_keys("authors") == ["id"]
        assert await db.table_columns("authors") == ["id", "name"]
    alter_from_outside(
        ds,
        """
        drop table authors;
        create table authors (slug text primary key, name text, born integer);
        """,
    )
    with request_scope():
        assert await db.primary_keys("authors") == ["slug"]
        assert await db.table_columns("authors") == ["slug", "name", "born"]


async def test_change_by_another_process_is_seen_outside_a_request(ds, db):
    assert await db.table_columns("authors") == ["id", "name"]
    alter_from_outside(ds, "alter table authors add column born integer")
    assert await db.table_columns("authors") == ["id", "name", "born"]
    alter_from_outside(ds, "drop table authors")
    assert await db.table_columns("authors") == []


async def test_dropped_and_recreated_table(db):
    with request_scope():
        assert await db.table_columns("authors") == ["id", "name"]
        await db.execute_write("drop table authors")
        assert await db.table_columns("authors") == []
        assert await db.primary_keys("authors") == []
        await db.execute_write("create table authors (code text primary key)")
        assert await db.table_columns("authors") == ["code"]
        assert await db.primary_keys("authors") == ["code"]


async def test_cached_values_cannot_be_modified_by_callers(db):
    with request_scope():
        (await db.table_columns("books")).append("extra")
        (await db.primary_keys("books")).append("extra")
        foreign_keys = await db.foreign_keys_for_table("books")
        foreign_keys[0]["other_table"] = "changed"
        foreign_keys.append({})

        assert await db.table_columns("books") == ["id", "title", "author_id"]
        assert await db.primary_keys("books") == ["id"]
        assert [
            fk["other_table"] for fk in await db.foreign_keys_for_table("books")
        ] == ["authors"]
        # Columns is immutable, so the same object is returned each time
        assert await db.table_column_details("books") is (
            await db.table_column_details("books")
        )


async def test_compound_primary_key_order(db):
    await db.execute_write(
        "create table compound (a text, b text, c text, primary key (c, a))"
    )
    with request_scope():
        assert await db.primary_keys("compound") == ["c", "a"]
        assert await db.primary_keys("compound") == ["c", "a"]


async def test_label_column_config_is_not_cached(tmp_path):
    db_path = tmp_path / "data.db"
    conn = sqlite3.connect(db_path)
    conn.execute("create table books (id integer primary key, title text, isbn text)")
    conn.close()
    ds = Datasette([str(db_path)])
    db = ds.get_database("data")
    with request_scope():
        assert await db.label_column_for_table("books") == "title"
        ds.config = {
            "databases": {"data": {"tables": {"books": {"label_column": "isbn"}}}}
        }
        assert await db.label_column_for_table("books") == "isbn"
    ds.close()


async def test_immutable_database_never_rechecks(tmp_path, trips):
    db_path = tmp_path / "data.db"
    conn = sqlite3.connect(db_path)
    conn.execute("create table books (id integer primary key, title text)")
    conn.close()
    ds = Datasette(immutables=[str(db_path)])
    db = ds.get_database("data")
    assert await db.table_columns("books") == ["id", "title"]
    # One for the columns, one for the names of the tables that exist
    assert trips == ["data", "data"]
    # No request scope here, and still nothing to check
    assert await db.table_columns("books") == ["id", "title"]
    assert await db.primary_keys("books") == ["id"]
    assert trips == ["data", "data"]
    ds.close()


async def test_private_memory_database_is_not_cached(trips):
    # Every connection to an unnamed in-memory database is a separate
    # database, so there is no shared schema to cache
    ds = Datasette(memory=True)
    db = ds.get_database("_memory")
    with request_scope():
        await db.table_columns("books")
        await db.table_columns("books")
    assert trips == ["_memory", "_memory"]


async def test_named_memory_database(trips):
    ds = Datasette(memory=True)
    db = ds.add_memory_database("schema_cache_named_memory", name="shared")
    await db.execute_write("create table books (id integer primary key, title text)")
    with request_scope():
        assert await db.table_columns("books") == ["id", "title"]
        del trips[:]
        assert await db.table_columns("books") == ["id", "title"]
        assert trips == []
        await db.execute_write("alter table books add column pages integer")
        assert await db.table_columns("books") == ["id", "title", "pages"]


async def test_attached_database_table_is_not_cached(tmp_path, trips):
    # A plugin can ATTACH another file in prepare_connection, and a table
    # name can resolve to a table in that file. The schema version of the
    # main database says nothing about those, so they are not cached.
    main_path = tmp_path / "data.db"
    other_path = tmp_path / "other.db"
    conn = sqlite3.connect(main_path)
    conn.execute("create table here (id integer primary key)")
    conn.close()
    conn = sqlite3.connect(other_path)
    conn.execute("create table elsewhere (id integer primary key)")
    conn.close()
    ds = Datasette([str(main_path)])
    original = ds._prepare_connection

    def prepare_and_attach(conn, database):
        original(conn, database)
        if database == "data":
            conn.execute("attach database ? as other", [str(other_path)])

    ds._prepare_connection = prepare_and_attach
    db = ds.get_database("data")
    with request_scope():
        assert await db.table_columns("here") == ["id"]
        assert await db.table_columns("elsewhere") == ["id"]
        # This does not check that a change to the attached file shows up.
        # SQLite 3.25 does not notice one until a statement reads that file.
        del trips[:]
        assert await db.table_columns("here") == ["id"]
        assert trips == []
        assert await db.table_columns("elsewhere") == ["id"]
        assert await db.primary_keys("elsewhere") == ["id"]
        assert trips == ["data", "data"]
    assert ("column_details", "here") in db._schema_cache
    assert ("column_details", "elsewhere") not in db._schema_cache
    ds.close()


async def test_names_that_match_no_table_are_not_cached(db):
    # ?_through= and others pass table names from the query string
    with request_scope():
        await all_lookups(db, "books")
        size = len(db._schema_cache)
        for i in range(50):
            assert await db.table_columns(f"no_such_table_{i}") == []
            assert await db.foreign_keys_for_table(f"no_such_table_{i}") == []
            assert await db.fts_table(f"no_such_table_{i}") is None
            assert len(await db.table_column_details(f"missing_{i}")) == 0
        # SQLite matches table names without regard to case. The lookup
        # works, and is not cached under every spelling of the name.
        assert await db.table_columns("BOOKS") == ["id", "title", "author_id"]
        assert await db.primary_keys("Books") == ["id"]
        assert len(db._schema_cache) == size


async def test_views_are_cached(db, trips):
    await db.execute_write("create view titles as select id, title from books")
    with request_scope():
        assert await db.table_columns("titles") == ["id", "title"]
        del trips[:]
        assert await db.table_columns("titles") == ["id", "title"]
        assert trips == []
        await db.execute_write("drop view titles")
        await db.execute_write("create view titles as select title from books")
        assert await db.table_columns("titles") == ["title"]


async def test_derived_table_dependencies_follow_schema_changes(db, trips):
    with request_scope():
        assert await db.derived_table_dependencies() == {}
        del trips[:]
        assert await db.derived_table_dependencies() == {}
        assert trips == []
        await db.execute_write(
            "create virtual table books_fts using fts5(title, content='books')"
        )
        assert (await db.derived_table_dependencies())["books_fts"] == "books"


async def test_table_page_reflects_change_by_another_process(ds, monkeypatch):
    from datasette import database as database_module

    introspected = []
    table_column_details = database_module.table_column_details

    def counting_table_column_details(conn, table):
        introspected.append(table)
        return table_column_details(conn, table)

    monkeypatch.setattr(
        database_module, "table_column_details", counting_table_column_details
    )
    response = await ds.client.get("/data/books.json?_extra=columns")
    assert response.json()["columns"] == ["id", "title", "author_id"]
    assert (await ds.client.get("/data/books")).status_code == 200

    # A warm HTML table page does not introspect the columns again
    del introspected[:]
    assert (await ds.client.get("/data/books")).status_code == 200
    assert introspected == []

    alter_from_outside(ds, "alter table books add column pages integer")
    response = await ds.client.get("/data/books.json?_extra=columns")
    assert response.json()["columns"] == ["id", "title", "author_id", "pages"]
    assert 'data-column="pages"' in (await ds.client.get("/data/books")).text
