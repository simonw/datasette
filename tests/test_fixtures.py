from datasette.fixtures import (
    populate_extra_database,
    populate_fixture_database,
    write_extra_database,
    write_fixture_database,
)
from datasette.utils.sqlite import sqlite3


def count(conn, table):
    return conn.execute(f"select count(*) from [{table}]").fetchone()[0]


def test_populate_fixture_database():
    conn = sqlite3.connect(":memory:")
    try:
        populate_fixture_database(conn)
        assert count(conn, "facetable") == 15
        assert count(conn, "compound_three_primary_keys") == 1001
        assert count(conn, "binary_data") == 3
    finally:
        conn.close()


def test_write_fixture_database(tmp_path):
    db_path = tmp_path / "fixtures.db"
    write_fixture_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        assert count(conn, "sortable") == 201
    finally:
        conn.close()


def test_extra_database_helpers(tmp_path):
    conn = sqlite3.connect(":memory:")
    try:
        populate_extra_database(conn)
        assert count(conn, "searchable") == 2
    finally:
        conn.close()

    db_path = tmp_path / "extra.db"
    write_extra_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        assert count(conn, "searchable") == 2
    finally:
        conn.close()
