import os

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


def test_make_app_client_restores_working_directory(tmp_path, monkeypatch):
    # make_app_client() changes into a temporary directory that is deleted
    # when it exits: it must change back first, or every later test on the
    # same worker runs in a directory that no longer exists (#1361)
    from .fixtures import make_app_client

    monkeypatch.chdir(tmp_path)
    with make_app_client() as client:
        assert client.get("/-/versions.json").status == 200
        assert os.getcwd() != str(tmp_path)
    assert os.getcwd() == str(tmp_path)
