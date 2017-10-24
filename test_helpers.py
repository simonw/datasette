import app
import pytest
import sqlite3


@pytest.mark.parametrize('path,expected', [
    ('foo', ['foo']),
    ('foo,bar', ['foo', 'bar']),
    ('123,433,112', ['123', '433', '112']),
    ('123%2C433,112', ['123,433', '112']),
    ('123%2F433%2F112', ['123/433/112']),
])
def test_compound_pks_from_path(path, expected):
    assert expected == app.compound_pks_from_path(path)


@pytest.mark.parametrize('sql,table,expected_keys', [
    ('''
        CREATE TABLE `Compound` (
            A varchar(5) NOT NULL,
            B varchar(10) NOT NULL,
            PRIMARY KEY (A, B)
        );
    ''', 'Compound', ['A', 'B']),
    ('''
        CREATE TABLE `Compound2` (
            A varchar(5) NOT NULL,
            B varchar(10) NOT NULL,
            PRIMARY KEY (B, A)
        );
    ''', 'Compound2', ['B', 'A']),
])
def test_pks_for_table(sql, table, expected_keys):
    conn = sqlite3.connect(':memory:')
    conn.execute(sql)
    actual = app.pks_for_table(conn, table)
    assert expected_keys == actual


@pytest.mark.parametrize('row,pks,expected_path', [
    ({'A': 'foo', 'B': 'bar'}, ['A', 'B'], 'foo,bar'),
    ({'A': 'f,o', 'B': 'bar'}, ['A', 'B'], 'f%2Co,bar'),
])
def test_path_from_row_pks(row, pks, expected_path):
    actual_path = app.path_from_row_pks(row, pks)
    assert expected_path == actual_path
