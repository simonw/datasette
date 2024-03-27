"""
Tests for various datasette helper functions.
"""

from datasette.app import Datasette
from datasette import utils
from datasette.utils.asgi import Request
from datasette.utils.sqlite import sqlite3
import json
import os
import pathlib
import pytest
import tempfile
from unittest.mock import patch


@pytest.mark.parametrize(
    "path,expected",
    [
        ("foo", ["foo"]),
        ("foo,bar", ["foo", "bar"]),
        ("123,433,112", ["123", "433", "112"]),
        ("123~2C433,112", ["123,433", "112"]),
        ("123~2F433~2F112", ["123/433/112"]),
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
        ({"A": "f,o", "B": "bar"}, ["A", "B"], "f~2Co,bar"),
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
        "/* This comment is not valid. select 1",
        "/**/\nupdate foo set bar = 1\n/* test */ select 1",
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
        "explain\nselect 1 + 1",
        "explain query plan select 1 + 1",
        "explain  query  plan\nselect 1 + 1",
        "SELECT\nblah FROM foo",
        "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt LIMIT 10) SELECT x FROM cnt;",
        "explain WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt LIMIT 10) SELECT x FROM cnt;",
        "explain query plan WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt LIMIT 10) SELECT x FROM cnt;",
        "SELECT * FROM pragma_index_info('idx52')",
        "select * from pragma_table_xinfo('table')",
        # Various types of comment
        "-- comment\nselect 1",
        "-- one line\n  -- two line\nselect 1",
        "  /* comment */\nselect 1",
        "  /* comment */select 1",
        "/* comment */\n -- another\n /* one more */ select 1",
        "/* This comment \n has multiple lines */\nselect 1",
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


@pytest.mark.parametrize("table", ("regular", "has'single quote"))
def test_detect_fts_different_table_names(table):
    sql = """
    CREATE TABLE [{table}] (
      "TreeID" INTEGER,
      "qSpecies" TEXT
    );
    CREATE VIRTUAL TABLE [{table}_fts] USING FTS4 ("qSpecies", content="{table}");
    """.format(
        table=table
    )
    conn = utils.sqlite3.connect(":memory:")
    conn.executescript(sql)
    assert "{table}_fts".format(table=table) == utils.detect_fts(conn, table)


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
        with open("hello", "w") as fp:
            fp.write("world")
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
            with open(hello) as fp:
                assert "world" == fp.read()
            # It should be a hard link
            assert 2 == os.stat(hello).st_nlink


@patch("os.link")
def test_temporary_docker_directory_uses_copy_if_hard_link_fails(mock_link):
    # Copy instead if os.link raises OSError (normally due to different device)
    mock_link.side_effect = OSError
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        with open("hello", "w") as fp:
            fp.write("world")
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
            with open(hello) as fp:
                assert "world" == fp.read()
            # It should be a copy, not a hard link
            assert 1 == os.stat(hello).st_nlink


def test_temporary_docker_directory_quotes_args():
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        with open("hello", "w") as fp:
            fp.write("world")
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
            with open(df) as fp:
                df_contents = fp.read()
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
        ("/foo/bar", "csv", {"_dl": 1}, "/foo/bar.csv?_dl=1"),
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
    actual = utils.path_with_format(request=request, format=format, extra_qs=extra_qs)
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


@pytest.mark.parametrize(
    "input,expected",
    [
        ("dog", "dog"),
        ('dateutil_parse("1/2/2020")', r"dateutil_parse(\0000221/2/2020\000022)"),
        ("this\r\nand\r\nthat", r"this\00000Aand\00000Athat"),
    ],
)
def test_escape_css_string(input, expected):
    assert expected == utils.escape_css_string(input)


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
        return f"{a}+{b}"

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
        # true means allow-all
        ({"id": "root"}, True, True),
        (None, True, True),
        # false means deny-all
        ({"id": "root"}, False, False),
        (None, False, False),
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


@pytest.mark.parametrize(
    "actor,expected",
    [
        ({"id": "blah"}, "blah"),
        ({"id": "blah", "login": "l"}, "l"),
        ({"id": "blah", "login": "l"}, "l"),
        ({"id": "blah", "login": "l", "username": "u"}, "u"),
        ({"login": "l", "name": "n"}, "n"),
        (
            {"id": "blah", "login": "l", "username": "u", "name": "n", "display": "d"},
            "d",
        ),
        ({"weird": "shape"}, "{'weird': 'shape'}"),
    ],
)
def test_display_actor(actor, expected):
    assert expected == utils.display_actor(actor)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dbs,expected_path",
    [
        (["one_table"], "/one/one"),
        (["two_tables"], "/two"),
        (["one_table", "two_tables"], "/"),
    ],
)
async def test_initial_path_for_datasette(tmp_path_factory, dbs, expected_path):
    db_dir = tmp_path_factory.mktemp("dbs")
    one_table = str(db_dir / "one.db")
    sqlite3.connect(one_table).execute("create table one (id integer primary key)")
    two_tables = str(db_dir / "two.db")
    sqlite3.connect(two_tables).execute("create table two (id integer primary key)")
    sqlite3.connect(two_tables).execute("create table three (id integer primary key)")
    datasette = Datasette(
        [{"one_table": one_table, "two_tables": two_tables}[db] for db in dbs]
    )
    path = await utils.initial_path_for_datasette(datasette)
    assert path == expected_path


@pytest.mark.parametrize(
    "content,expected",
    (
        ("title: Hello", {"title": "Hello"}),
        ('{"title": "Hello"}', {"title": "Hello"}),
        ("{{ this }} is {{ bad }}", None),
    ),
)
def test_parse_metadata(content, expected):
    if expected is None:
        with pytest.raises(utils.BadMetadataError):
            utils.parse_metadata(content)
    else:
        assert utils.parse_metadata(content) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql,expected",
    (
        ("select 1", []),
        ("select 1 + :one", ["one"]),
        ("select 1 + :one + :two", ["one", "two"]),
        ("select 'bob' || '0:00' || :cat", ["cat"]),
        ("select this is invalid :one, :two, :three", ["one", "two", "three"]),
    ),
)
async def test_derive_named_parameters(sql, expected):
    ds = Datasette([], memory=True)
    db = ds.get_database("_memory")
    params = await utils.derive_named_parameters(db, sql)
    assert params == expected


@pytest.mark.parametrize(
    "original,expected",
    (
        ("abc", "abc"),
        ("/foo/bar", "~2Ffoo~2Fbar"),
        ("/-/bar", "~2F-~2Fbar"),
        ("-/db-/table.csv", "-~2Fdb-~2Ftable~2Ecsv"),
        (r"%~-/", "~25~7E-~2F"),
        ("~25~7E~2D~2F", "~7E25~7E7E~7E2D~7E2F"),
        ("with space", "with+space"),
    ),
)
def test_tilde_encoding(original, expected):
    actual = utils.tilde_encode(original)
    assert actual == expected
    # And test round-trip
    assert original == utils.tilde_decode(actual)


@pytest.mark.parametrize(
    "url,length,expected",
    (
        ("https://example.com/", 5, "http…"),
        ("https://example.com/foo/bar", 15, "https://exampl…"),
        ("https://example.com/foo/bar/baz.jpg", 30, "https://example.com/foo/ba….jpg"),
        # Extensions longer than 4 characters are not treated specially:
        ("https://example.com/foo/bar/baz.jpeg2", 30, "https://example.com/foo/bar/b…"),
        (
            "https://example.com/foo/bar/baz.jpeg2",
            None,
            "https://example.com/foo/bar/baz.jpeg2",
        ),
    ),
)
def test_truncate_url(url, length, expected):
    actual = utils.truncate_url(url, length)
    assert actual == expected


@pytest.mark.parametrize(
    "pairs,expected",
    (
        # Simple nested objects
        ([("a", "b")], {"a": "b"}),
        ([("a.b", "c")], {"a": {"b": "c"}}),
        # JSON literals
        ([("a.b", "true")], {"a": {"b": True}}),
        ([("a.b", "false")], {"a": {"b": False}}),
        ([("a.b", "null")], {"a": {"b": None}}),
        ([("a.b", "1")], {"a": {"b": 1}}),
        ([("a.b", "1.1")], {"a": {"b": 1.1}}),
        # Nested JSON literals
        ([("a.b", '{"foo": "bar"}')], {"a": {"b": {"foo": "bar"}}}),
        ([("a.b", "[1, 2, 3]")], {"a": {"b": [1, 2, 3]}}),
        # JSON strings are preserved
        ([("a.b", '"true"')], {"a": {"b": "true"}}),
        ([("a.b", '"[1, 2, 3]"')], {"a": {"b": "[1, 2, 3]"}}),
        # Later keys over-ride the previous
        (
            [
                ("a", "b"),
                ("a.b", "c"),
            ],
            {"a": {"b": "c"}},
        ),
        (
            [
                ("settings.trace_debug", "true"),
                ("plugins.datasette-ripgrep.path", "/etc"),
                ("settings.trace_debug", "false"),
            ],
            {
                "settings": {
                    "trace_debug": False,
                },
                "plugins": {
                    "datasette-ripgrep": {
                        "path": "/etc",
                    }
                },
            },
        ),
    ),
)
def test_pairs_to_nested_config(pairs, expected):
    actual = utils.pairs_to_nested_config(pairs)
    assert actual == expected
