"""
Tests for various datasette helper functions.
"""

from datasette import utils
from datasette.utils.asgi import Request
from datasette.filters import Filters
import json
import os
import pathlib
import pytest
import sqlite3
import tempfile
from unittest.mock import patch


@pytest.mark.parametrize(
    "path,expected",
    [
        ("foo", ["foo"]),
        ("foo,bar", ["foo", "bar"]),
        ("123,433,112", ["123", "433", "112"]),
        ("123%2C433,112", ["123,433", "112"]),
        ("123%2F433%2F112", ["123/433/112"]),
    ],
)
def test_urlsafe_components(path, expected):
    assert expected == utils.urlsafe_components(path)


@pytest.mark.parametrize(
    "path,added_args,expected",
    [
        ("/foo", {"bar": 1}, "/foo?bar=1"),
        ("/foo?bar=1", {"baz": 2}, "/foo?bar=1&baz=2"),
        ("/foo?bar=1&bar=2", {"baz": 3}, "/foo?bar=1&bar=2&baz=3"),
        ("/foo?bar=1", {"bar": None}, "/foo"),
        # Test order is preserved
        (
            "/?_facet=prim_state&_facet=area_name",
            (("prim_state", "GA"),),
            "/?_facet=prim_state&_facet=area_name&prim_state=GA",
        ),
        (
            "/?_facet=state&_facet=city&state=MI",
            (("city", "Detroit"),),
            "/?_facet=state&_facet=city&state=MI&city=Detroit",
        ),
        (
            "/?_facet=state&_facet=city",
            (("_facet", "planet_int"),),
            "/?_facet=state&_facet=city&_facet=planet_int",
        ),
    ],
)
def test_path_with_added_args(path, added_args, expected):
    request = Request.fake(path)
    actual = utils.path_with_added_args(request, added_args)
    assert expected == actual


@pytest.mark.parametrize(
    "path,args,expected",
    [
        ("/foo?bar=1", {"bar"}, "/foo"),
        ("/foo?bar=1&baz=2", {"bar"}, "/foo?baz=2"),
        ("/foo?bar=1&bar=2&bar=3", {"bar": "2"}, "/foo?bar=1&bar=3"),
    ],
)
def test_path_with_removed_args(path, args, expected):
    request = Request.fake(path)
    actual = utils.path_with_removed_args(request, args)
    assert expected == actual
    # Run the test again but this time use the path= argument
    request = Request.fake("/")
    actual = utils.path_with_removed_args(request, args, path=path)
    assert expected == actual


@pytest.mark.parametrize(
    "path,args,expected",
    [
        ("/foo?bar=1", {"bar": 2}, "/foo?bar=2"),
        ("/foo?bar=1&baz=2", {"bar": None}, "/foo?baz=2"),
    ],
)
def test_path_with_replaced_args(path, args, expected):
    request = Request.fake(path)
    actual = utils.path_with_replaced_args(request, args)
    assert expected == actual


@pytest.mark.parametrize(
    "row,pks,expected_path",
    [
        ({"A": "foo", "B": "bar"}, ["A", "B"], "foo,bar"),
        ({"A": "f,o", "B": "bar"}, ["A", "B"], "f%2Co,bar"),
        ({"A": 123}, ["A"], "123"),
        (
            utils.CustomRow(
                ["searchable_id", "tag"],
                [
                    ("searchable_id", {"value": 1, "label": "1"}),
                    ("tag", {"value": "feline", "label": "feline"}),
                ],
            ),
            ["searchable_id", "tag"],
            "1,feline",
        ),
    ],
)
def test_path_from_row_pks(row, pks, expected_path):
    actual_path = utils.path_from_row_pks(row, pks, False)
    assert expected_path == actual_path


@pytest.mark.parametrize(
    "obj,expected",
    [
        (
            {
                "Description": "Soft drinks",
                "Picture": b"\x15\x1c\x02\xc7\xad\x05\xfe",
                "CategoryID": 1,
            },
            """
        {"CategoryID": 1, "Description": "Soft drinks", "Picture": {"$base64": true, "encoded": "FRwCx60F/g=="}}
    """.strip(),
        )
    ],
)
def test_custom_json_encoder(obj, expected):
    actual = json.dumps(obj, cls=utils.CustomJSONEncoder, sort_keys=True)
    assert expected == actual


@pytest.mark.parametrize(
    "bad_sql",
    [
        "update blah;",
        "-- sql comment to skip\nupdate blah;",
        "update blah set some_column='# Hello there\n\n* This is a list\n* of items\n--\n[And a link](https://github.com/simonw/datasette-render-markdown).'\nas demo_markdown",
        "PRAGMA case_sensitive_like = true",
        "SELECT * FROM pragma_not_on_allow_list('idx52')",
    ],
)
def test_validate_sql_select_bad(bad_sql):
    with pytest.raises(utils.InvalidSql):
        utils.validate_sql_select(bad_sql)


@pytest.mark.parametrize(
    "good_sql",
    [
        "select count(*) from airports",
        "select foo from bar",
        "--sql comment to skip\nselect foo from bar",
        "select '# Hello there\n\n* This is a list\n* of items\n--\n[And a link](https://github.com/simonw/datasette-render-markdown).'\nas demo_markdown",
        "select 1 + 1",
        "explain select 1 + 1",
        "explain query plan select 1 + 1",
        "SELECT\nblah FROM foo",
        "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt LIMIT 10) SELECT x FROM cnt;",
        "explain WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt LIMIT 10) SELECT x FROM cnt;",
        "explain query plan WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt LIMIT 10) SELECT x FROM cnt;",
        "SELECT * FROM pragma_index_info('idx52')",
        "select * from pragma_table_xinfo('table')",
    ],
)
def test_validate_sql_select_good(good_sql):
    utils.validate_sql_select(good_sql)


@pytest.mark.parametrize("open_quote,close_quote", [('"', '"'), ("[", "]")])
def test_detect_fts(open_quote, close_quote):
    sql = """
    CREATE TABLE "Dumb_Table" (
      "TreeID" INTEGER,
      "qSpecies" TEXT
    );
    CREATE TABLE "Street_Tree_List" (
      "TreeID" INTEGER,
      "qSpecies" TEXT,
      "qAddress" TEXT,
      "SiteOrder" INTEGER,
      "qSiteInfo" TEXT,
      "PlantType" TEXT,
      "qCaretaker" TEXT
    );
    CREATE VIEW Test_View AS SELECT * FROM Dumb_Table;
    CREATE VIRTUAL TABLE {open}Street_Tree_List_fts{close} USING FTS4 ("qAddress", "qCaretaker", "qSpecies", content={open}Street_Tree_List{close});
    CREATE VIRTUAL TABLE r USING rtree(a, b, c);
    """.format(
        open=open_quote, close=close_quote
    )
    conn = utils.sqlite3.connect(":memory:")
    conn.executescript(sql)
    assert None is utils.detect_fts(conn, "Dumb_Table")
    assert None is utils.detect_fts(conn, "Test_View")
    assert None is utils.detect_fts(conn, "r")
    assert "Street_Tree_List_fts" == utils.detect_fts(conn, "Street_Tree_List")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://www.google.com/", True),
        ("https://example.com/", True),
        ("www.google.com", False),
        ("http://www.google.com/ is a search engine", False),
    ],
)
def test_is_url(url, expected):
    assert expected == utils.is_url(url)


@pytest.mark.parametrize(
    "s,expected",
    [
        ("simple", "simple"),
        ("MixedCase", "MixedCase"),
        ("-no-leading-hyphens", "no-leading-hyphens-65bea6"),
        ("_no-leading-underscores", "no-leading-underscores-b921bc"),
        ("no spaces", "no-spaces-7088d7"),
        ("-", "336d5e"),
        ("no $ characters", "no--characters-59e024"),
    ],
)
def test_to_css_class(s, expected):
    assert expected == utils.to_css_class(s)


def test_temporary_docker_directory_uses_hard_link():
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        open("hello", "w").write("world")
        # Default usage of this should use symlink
        with utils.temporary_docker_directory(
            files=["hello"],
            name="t",
            metadata=None,
            extra_options=None,
            branch=None,
            template_dir=None,
            plugins_dir=None,
            static=[],
            install=[],
            spatialite=False,
            version_note=None,
            secret="secret",
        ) as temp_docker:
            hello = os.path.join(temp_docker, "hello")
            assert "world" == open(hello).read()
            # It should be a hard link
            assert 2 == os.stat(hello).st_nlink


@patch("os.link")
def test_temporary_docker_directory_uses_copy_if_hard_link_fails(mock_link):
    # Copy instead if os.link raises OSError (normally due to different device)
    mock_link.side_effect = OSError
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        open("hello", "w").write("world")
        # Default usage of this should use symlink
        with utils.temporary_docker_directory(
            files=["hello"],
            name="t",
            metadata=None,
            extra_options=None,
            branch=None,
            template_dir=None,
            plugins_dir=None,
            static=[],
            install=[],
            spatialite=False,
            version_note=None,
            secret=None,
        ) as temp_docker:
            hello = os.path.join(temp_docker, "hello")
            assert "world" == open(hello).read()
            # It should be a copy, not a hard link
            assert 1 == os.stat(hello).st_nlink


def test_temporary_docker_directory_quotes_args():
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        open("hello", "w").write("world")
        with utils.temporary_docker_directory(
            files=["hello"],
            name="t",
            metadata=None,
            extra_options="--$HOME",
            branch=None,
            template_dir=None,
            plugins_dir=None,
            static=[],
            install=[],
            spatialite=False,
            version_note="$PWD",
            secret="secret",
        ) as temp_docker:
            df = os.path.join(temp_docker, "Dockerfile")
            df_contents = open(df).read()
            assert "'$PWD'" in df_contents
            assert "'--$HOME'" in df_contents
            assert "ENV DATASETTE_SECRET 'secret'" in df_contents


def test_compound_keys_after_sql():
    assert "((a > :p0))" == utils.compound_keys_after_sql(["a"])
    assert """
((a > :p0)
  or
(a = :p0 and b > :p1))
    """.strip() == utils.compound_keys_after_sql(
        ["a", "b"]
    )
    assert """
((a > :p0)
  or
(a = :p0 and b > :p1)
  or
(a = :p0 and b = :p1 and c > :p2))
    """.strip() == utils.compound_keys_after_sql(
        ["a", "b", "c"]
    )


async def table_exists(table):
    return table == "exists.csv"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table_and_format,expected_table,expected_format",
    [
        ("blah", "blah", None),
        ("blah.csv", "blah", "csv"),
        ("blah.json", "blah", "json"),
        ("blah.baz", "blah.baz", None),
        ("exists.csv", "exists.csv", None),
    ],
)
async def test_resolve_table_and_format(
    table_and_format, expected_table, expected_format
):
    actual_table, actual_format = await utils.resolve_table_and_format(
        table_and_format, table_exists, ["json"]
    )
    assert expected_table == actual_table
    assert expected_format == actual_format


def test_table_columns():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
    create table places (id integer primary key, name text, bob integer)
    """
    )
    assert ["id", "name", "bob"] == utils.table_columns(conn, "places")


@pytest.mark.parametrize(
    "path,format,extra_qs,expected",
    [
        ("/foo?sql=select+1", "csv", {}, "/foo.csv?sql=select+1"),
        ("/foo?sql=select+1", "json", {}, "/foo.json?sql=select+1"),
        ("/foo/bar", "json", {}, "/foo/bar.json"),
        ("/foo/bar", "csv", {}, "/foo/bar.csv"),
        ("/foo/bar.csv", "json", {}, "/foo/bar.csv?_format=json"),
        ("/foo/bar", "csv", {"_dl": 1}, "/foo/bar.csv?_dl=1"),
        ("/foo/b.csv", "json", {"_dl": 1}, "/foo/b.csv?_dl=1&_format=json"),
        (
            "/sf-trees/Street_Tree_List?_search=cherry&_size=1000",
            "csv",
            {"_dl": 1},
            "/sf-trees/Street_Tree_List.csv?_search=cherry&_size=1000&_dl=1",
        ),
    ],
)
def test_path_with_format(path, format, extra_qs, expected):
    request = Request.fake(path)
    actual = utils.path_with_format(request, format, extra_qs)
    assert expected == actual


@pytest.mark.parametrize(
    "bytes,expected",
    [
        (120, "120 bytes"),
        (1024, "1.0 KB"),
        (1024 * 1024, "1.0 MB"),
        (1024 * 1024 * 1024, "1.0 GB"),
        (1024 * 1024 * 1024 * 1.3, "1.3 GB"),
        (1024 * 1024 * 1024 * 1024, "1.0 TB"),
    ],
)
def test_format_bytes(bytes, expected):
    assert expected == utils.format_bytes(bytes)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("dog", '"dog"'),
        ("cat,", '"cat,"'),
        ("cat dog", '"cat" "dog"'),
        # If a phrase is already double quoted, leave it so
        ('"cat dog"', '"cat dog"'),
        ('"cat dog" fish', '"cat dog" "fish"'),
        # Sensibly handle unbalanced double quotes
        ('cat"', '"cat"'),
        ('"cat dog" "fish', '"cat dog" "fish"'),
    ],
)
def test_escape_fts(query, expected):
    assert expected == utils.escape_fts(query)


def test_check_connection_spatialite_raises():
    path = str(pathlib.Path(__file__).parent / "spatialite.db")
    conn = sqlite3.connect(path)
    with pytest.raises(utils.SpatialiteConnectionProblem):
        utils.check_connection(conn)


def test_check_connection_passes():
    conn = sqlite3.connect(":memory:")
    utils.check_connection(conn)


def test_call_with_supported_arguments():
    def foo(a, b):
        return "{}+{}".format(a, b)

    assert "1+2" == utils.call_with_supported_arguments(foo, a=1, b=2)
    assert "1+2" == utils.call_with_supported_arguments(foo, a=1, b=2, c=3)

    with pytest.raises(TypeError):
        utils.call_with_supported_arguments(foo, a=1)


@pytest.mark.parametrize(
    "data,should_raise",
    [
        ([["foo", "bar"], ["foo", "baz"]], False),
        ([("foo", "bar"), ("foo", "baz")], False),
        ((["foo", "bar"], ["foo", "baz"]), False),
        ([["foo", "bar"], ["foo", "baz", "bax"]], True),
        ({"foo": ["bar", "baz"]}, False),
        ({"foo": ("bar", "baz")}, False),
        ({"foo": "bar"}, True),
    ],
)
def test_multi_params(data, should_raise):
    if should_raise:
        with pytest.raises(AssertionError):
            utils.MultiParams(data)
        return
    p1 = utils.MultiParams(data)
    assert "bar" == p1["foo"]
    assert ["bar", "baz"] == list(p1.getlist("foo"))


@pytest.mark.parametrize(
    "actor,allow,expected",
    [
        # Default is to allow:
        (None, None, True),
        # {} means deny-all:
        (None, {}, False),
        ({"id": "root"}, {}, False),
        # Special case for "unauthenticated": true
        (None, {"unauthenticated": True}, True),
        (None, {"unauthenticated": False}, False),
        # Match on just one property:
        (None, {"id": "root"}, False),
        ({"id": "root"}, None, True),
        ({"id": "simon", "staff": True}, {"staff": True}, True),
        ({"id": "simon", "staff": False}, {"staff": True}, False),
        # Special "*" value for any key:
        ({"id": "root"}, {"id": "*"}, True),
        ({}, {"id": "*"}, False),
        ({"name": "root"}, {"id": "*"}, False),
        # Supports single strings or list of values:
        ({"id": "root"}, {"id": "bob"}, False),
        ({"id": "root"}, {"id": ["bob"]}, False),
        ({"id": "root"}, {"id": "root"}, True),
        ({"id": "root"}, {"id": ["root"]}, True),
        # Any matching role will work:
        ({"id": "garry", "roles": ["staff", "dev"]}, {"roles": ["staff"]}, True),
        ({"id": "garry", "roles": ["staff", "dev"]}, {"roles": ["dev"]}, True),
        ({"id": "garry", "roles": ["staff", "dev"]}, {"roles": ["otter"]}, False),
        ({"id": "garry", "roles": ["staff", "dev"]}, {"roles": ["dev", "otter"]}, True),
        ({"id": "garry", "roles": []}, {"roles": ["staff"]}, False),
        ({"id": "garry"}, {"roles": ["staff"]}, False),
        # Any single matching key works:
        ({"id": "root"}, {"bot_id": "my-bot", "id": ["root"]}, True),
    ],
)
def test_actor_matches_allow(actor, allow, expected):
    assert expected == utils.actor_matches_allow(actor, allow)


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"foo": "bar"}, {"foo": "bar"}),
        ({"$env": "FOO"}, "x"),
        ({"k": {"$env": "FOO"}}, {"k": "x"}),
        ([{"k": {"$env": "FOO"}}, {"z": {"$env": "FOO"}}], [{"k": "x"}, {"z": "x"}]),
        ({"k": [{"in_a_list": {"$env": "FOO"}}]}, {"k": [{"in_a_list": "x"}]}),
    ],
)
def test_resolve_env_secrets(config, expected):
    assert expected == utils.resolve_env_secrets(config, {"FOO": "x"})
