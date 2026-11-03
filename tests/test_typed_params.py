import sqlite3
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from bs4 import BeautifulSoup as Soup

from datasette.app import Datasette

PEOPLE_SQL = """
create table people (
  id integer primary key,
  name text,
  age integer
);
insert into people (name, age) values
  ('Alice', 30),
  ('Bob', 41),
  ('Carol', 0),
  ('Dave', -5);
"""

AGE_SQL = (
    "select name, age, typeof(:age) as bound_type, :age as bound_value, "
    "age + 1 > :age as matches from people order by id"
)


@pytest_asyncio.fixture
async def ds():
    ds = Datasette(memory=True)
    db = ds.add_memory_database("typed")
    await ds.invoke_startup()
    await db.execute_write_script(PEOPLE_SQL)
    await ds.add_query(
        "typed",
        "ages_above",
        "select name, age from people where age + 1 > :age order by age",
        parameters=["age"],
        is_trusted=True,
        source="user",
        owner_id="root",
    )
    await ds.add_query(
        "typed",
        "underscore_declared",
        "select 1 as one",
        parameters=["_foo"],
        is_trusted=True,
        source="user",
        owner_id="root",
    )
    await ds.add_query(
        "typed",
        "add_probe",
        "insert into probes (note) values (typeof(:age))",
        parameters=["age"],
        is_write=True,
        is_trusted=True,
        source="user",
        owner_id="root",
    )
    await db.execute_write("create table probes (note text)")
    await ds.add_query(
        "typed",
        "add_note",
        "insert into notes (value) values (:value)",
        parameters=["value"],
        is_write=True,
        is_trusted=True,
        source="user",
        owner_id="root",
    )
    await db.execute_write("create table notes (value text)")
    try:
        yield ds
    finally:
        ds.close()


POST_FORM_HEADERS = {"content-type": "application/x-www-form-urlencoded"}
POST_JSON_HEADERS = {"content-type": "application/json"}


def replay_in_memory(sql, params):
    """Replay reported SQL/params in an independent SQLite connection."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(PEOPLE_SQL)
        cursor = conn.execute(sql, params)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
    finally:
        conn.close()


def typed_path(sql, pairs):
    return "/typed/-/query.json?" + urlencode([("sql", sql)] + list(pairs))


@pytest.mark.asyncio
async def test_default_string_semantics_unchanged(ds):
    response = await ds.client.get(typed_path(AGE_SQL, [("age", "30")]))
    assert response.status_code == 200
    data = response.json()
    assert [
        (row["name"], row["bound_type"], row["bound_value"]) for row in data["rows"]
    ] == [
        ("Alice", "text", "30"),
        ("Bob", "text", "30"),
        ("Carol", "text", "30"),
        ("Dave", "text", "30"),
    ]
    # age + 1 > '30' is false for every row: expression comparison is not coerced
    assert [row["matches"] for row in data["rows"]] == [0, 0, 0, 0]


@pytest.mark.asyncio
async def test_explicit_int_binds_integer_and_changes_expression_comparison(ds):
    response = await ds.client.get(
        typed_path(AGE_SQL, [("_typed_params", "1"), ("age::int", "30")])
    )
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert [(row["name"], row["bound_type"], row["bound_value"]) for row in rows] == [
        ("Alice", "integer", 30),
        ("Bob", "integer", 30),
        ("Carol", "integer", 30),
        ("Dave", "integer", 30),
    ]
    # Manually checkable: age + 1 > 30 is true only for Alice (31) and Bob (42)
    assert [row["name"] for row in rows if row["matches"]] == ["Alice", "Bob"]


@pytest.mark.asyncio
async def test_typed_suffix_ignored_without_explicit_enable(ds):
    response = await ds.client.get(typed_path(AGE_SQL, [("age::int", "30")]))
    assert response.status_code == 200
    rows = response.json()["rows"]
    # :age was missing, so Datasette's legacy blank-string fallback applied
    assert {row["bound_type"] for row in rows} == {"text"}
    assert {row["bound_value"] for row in rows} == {""}
    assert [row["matches"] for row in rows] == [0, 0, 0, 0]


@pytest.mark.asyncio
async def test_disabled_toggle_keeps_colon_colon_as_literal_argument(ds):
    response = await ds.client.get(
        typed_path(
            "select :name as value, typeof(:name) as t",
            [("_typed_params", "0"), ("name::int", "7")],
        )
    )
    assert response.status_code == 200
    assert response.json()["rows"] == [{"value": "", "t": "text"}]


@pytest.mark.asyncio
async def test_zero_is_bound_and_rendered_in_html(ds):
    response = await ds.client.get(
        typed_path(
            "select :age as v, typeof(:age) as t",
            [("_typed_params", "1"), ("age::int", "0")],
        )
    )
    assert response.status_code == 200
    assert response.json()["rows"] == [{"v": 0, "t": "integer"}]

    html = await ds.client.get(
        "/typed/-/query?"
        + urlencode(
            {
                "sql": "select :age as v, typeof(:age) as t",
                "_typed_params": "1",
                "age::int": "0",
            }
        )
    )
    assert html.status_code == 200
    assert (
        'name="age::int" value="0" data-parameter-control '
        'data-parameter-name="age::int"' in html.text
    )
    assert '<input type="hidden" name="_typed_params" value="1">' in html.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,expected",
    [
        ("-9223372036854775808", -9223372036854775808),
        ("9223372036854775807", 9223372036854775807),
        ("-1", -1),
        ("007", 7),
    ],
)
async def test_int_bounds_and_canonical_form(ds, value, expected):
    response = await ds.client.get(
        typed_path(
            "select :age as v, typeof(:age) as t",
            [("_typed_params", "1"), ("age::int", value)],
        )
    )
    assert response.status_code == 200
    assert response.json()["rows"] == [{"v": expected, "t": "integer"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "-9223372036854775809",
        "9223372036854775808",
        "99999999999999999999999999999999",
    ],
)
async def test_int_out_of_sqlite_range_is_400(ds, value):
    response = await ds.client.get(
        typed_path("select :age as v", [("_typed_params", "1"), ("age::int", value)])
    )
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert data["status"] == 400
    assert "age::int" in data["error"]
    assert "64-bit" in data["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "",
        "12.5",
        "abc",
        " 1",
        "1 ",
        "1_0",
        "+1",
        "0x10",
        "1e3",
        "-",
        "--1",
        "١٢",
        "9" * 5000,
    ],
)
async def test_invalid_integer_values_are_400(ds, value):
    response = await ds.client.get(
        typed_path("select :age as v", [("_typed_params", "1"), ("age::int", value)])
    )
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert data["status"] == 400
    assert "age::int" in data["error"]


@pytest.mark.asyncio
async def test_missing_parameter_keeps_legacy_blank_string(ds):
    response = await ds.client.get(
        typed_path("select :age as v, typeof(:age) as t", [("_typed_params", "1")])
    )
    assert response.status_code == 200
    assert response.json()["rows"] == [{"v": "", "t": "text"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pairs,fragment",
    [
        ([("age::int", "1"), ("age::int", "2")], "more than once"),
        ([("age::int", "1"), ("age::int", "1")], "more than once"),
        ([("age", "1"), ("age::int", "2")], "both with and without"),
        ([("age::float", "1")], "Unsupported parameter type"),
        ([("age::", "1")], "Unsupported parameter type"),
        ([("::int", "1")], "Invalid typed parameter name"),
        ([("other::int", "1")], "does not match a named parameter"),
        (
            [("_typed_params", "1"), ("_typed_params", "1"), ("age::int", "1")],
            "only be supplied once",
        ),
    ],
)
async def test_duplicate_conflict_unknown_and_repeated_toggle_are_400(
    ds, pairs, fragment
):
    response = await ds.client.get(
        typed_path("select :age as v", [("_typed_params", "1")] + pairs)
    )
    assert response.status_code == 400
    data = response.json()
    assert data["status"] == 400
    assert fragment in data["error"]


@pytest.mark.asyncio
async def test_unicode_named_parameter_keeps_default_then_opts_in(ds):
    sql = "select :naïve as v, typeof(:naïve) as t"
    default = await ds.client.get(
        "/typed/-/query.json?" + urlencode({"sql": sql, "naïve": "café"})
    )
    assert default.status_code == 200
    assert default.json()["rows"] == [{"v": "café", "t": "text"}]

    typed = await ds.client.get(
        "/typed/-/query.json?"
        + urlencode({"sql": sql, "_typed_params": "1", "naïve::int": "5"})
    )
    assert typed.status_code == 200
    assert typed.json()["rows"] == [{"v": 5, "t": "integer"}]


@pytest.mark.asyncio
async def test_typed_underscore_target_is_rejected(ds):
    response = await ds.client.get(
        typed_path("select :_foo as v", [("_typed_params", "1"), ("_foo::int", "1")])
    )
    assert response.status_code == 400
    assert "cannot target an underscore parameter" in response.json()["error"]


@pytest.mark.asyncio
async def test_url_encoded_key_and_value(ds):
    path = (
        "/typed/-/query.json?"
        + urlencode({"sql": "select :age as v, typeof(:age) as t"})
        + "&_typed_params=1&age%3A%3Aint=%2D5"
    )
    response = await ds.client.get(path)
    assert response.status_code == 200
    assert response.json()["rows"] == [{"v": -5, "t": "integer"}]


@pytest.mark.asyncio
async def test_stored_query_json_and_html_refill(ds):
    response = await ds.client.get(
        "/typed/ages_above.json?" + urlencode({"_typed_params": "1", "age::int": "30"})
    )
    assert response.status_code == 200
    assert response.json()["rows"] == [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "age": 41},
    ]

    default_response = await ds.client.get(
        "/typed/ages_above.json?" + urlencode({"age": "30"})
    )
    assert default_response.status_code == 200
    assert default_response.json()["rows"] == []

    html = await ds.client.get(
        "/typed/ages_above?" + urlencode({"_typed_params": "1", "age::int": "30"})
    )
    assert html.status_code == 200
    assert (
        'name="age::int" value="30" data-parameter-control '
        'data-parameter-name="age::int"' in html.text
    )
    assert '<input type="hidden" name="_typed_params" value="1">' in html.text
    table = Soup(html.text, "html.parser").find("table")
    rows = [
        [td.get_text() for td in tr.select("td")] for tr in table.select("tbody tr")
    ]
    assert rows == [["Alice", "30"], ["Bob", "41"]]
    edit_link = Soup(html.text, "html.parser").select_one("a.stored-query-edit-sql")
    assert edit_link is not None
    assert "_typed_params=1" in edit_link["href"]
    assert "age%3A%3Aint=30" in edit_link["href"]


@pytest.mark.asyncio
async def test_stored_write_post_uses_integer_binding(ds):
    response = await ds.client.post(
        "/typed/add_probe",
        data={"_typed_params": "1", "age::int": "0"},
    )
    assert response.status_code == 302
    note = (await ds.get_database("typed").execute("select note from probes")).first()[
        0
    ]
    assert note == "integer"


@pytest.mark.asyncio
async def test_underscore_declared_parameters_are_not_refilled(ds):
    response = await ds.client.get(
        "/typed/underscore_declared.json?" + urlencode({"_typed_params": "1"})
    )
    assert response.status_code == 200
    assert response.json()["rows"] == [{"one": 1}]


@pytest.mark.asyncio
async def test_stored_write_post_json_uses_integer_binding(ds):
    response = await ds.client.post(
        "/typed/add_probe",
        json={"_typed_params": "1", "age::int": "-7"},
    )
    assert response.status_code == 302
    note = (await ds.get_database("typed").execute("select note from probes")).first()[
        0
    ]
    assert note == "integer"


@pytest.mark.asyncio
async def test_stored_write_post_typed_error_is_400(ds):
    response = await ds.client.post(
        "/typed/add_probe",
        data={"_typed_params": "1", "age::int": "bad"},
    )
    assert response.status_code == 400
    assert "age::int" in response.text
    note_rows = (await ds.get_database("typed").execute("select note from probes")).rows
    assert note_rows == []


@pytest.mark.asyncio
async def test_typed_errors_do_not_leak_as_500_for_html(ds):
    response = await ds.client.get(
        "/typed/-/query?"
        + urlencode({"sql": "select :age", "_typed_params": "1", "age::int": "nope"})
    )
    assert response.status_code == 400
    assert "age::int" in response.text


@pytest.mark.asyncio
async def test_permission_denial_precedes_typed_validation():
    ds = Datasette(memory=True, config={"allow_sql": {"id": "root"}})
    db = ds.add_memory_database("typed")
    await ds.invoke_startup()
    await db.execute_write_script(PEOPLE_SQL)
    try:
        response = await ds.client.get(
            typed_path("select :age", [("_typed_params", "1"), ("age::int", "nope")])
        )
        assert response.status_code == 403
        assert "age::int" not in response.text
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_untrusted_stored_permissions_precede_typed_validation():
    ds = Datasette(memory=True, config={"allow_sql": {"id": "root"}})
    db = ds.add_memory_database("p")
    await ds.invoke_startup()
    await db.execute_write_script(
        "create table notes (id integer primary key, note text)"
    )
    await ds.add_query(
        "p",
        "read_untrusted",
        "select :age as v",
        parameters=["age"],
        is_trusted=False,
        source="user",
        owner_id="root",
    )
    await ds.add_query(
        "p",
        "write_untrusted",
        "insert into notes (note) values (:note)",
        parameters=["note"],
        is_write=True,
        is_trusted=False,
        source="user",
        owner_id="root",
    )
    try:
        read = await ds.client.get(
            "/p/read_untrusted.json?"
            + urlencode({"_typed_params": "1", "age::int": "bad"})
        )
        assert read.status_code == 403
        assert "age::int" not in read.text

        write = await ds.client.post(
            "/p/write_untrusted", data={"_typed_params": "1", "note::int": "bad"}
        )
        assert write.status_code == 403
        assert "note::int" not in write.text
        assert (await db.execute("select * from notes")).rows == []
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_unenabled_post_duplicate_plain_keys_keep_last_value(ds):
    # Legacy a8e13cc POST semantics: dict(parse_qsl(...)) -> last value wins.
    response = await ds.client.post(
        "/typed/add_note",
        content="value=1&value=2",
        headers=POST_FORM_HEADERS,
    )
    assert response.status_code == 302
    row = (await ds.get_database("typed").execute("select value from notes")).first()
    assert row[0] == "2"


@pytest.mark.asyncio
async def test_enabled_mode_leaves_untyped_parameter_as_text(ds):
    response = await ds.client.get(
        typed_path(AGE_SQL, [("_typed_params", "1"), ("age", "30")])
    )
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert {(row["bound_type"], row["bound_value"]) for row in rows} == {("text", "30")}
    assert [row["matches"] for row in rows] == [0, 0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flags",
    [
        [("_typed_params", "0"), ("_typed_params", "1")],
        [("_typed_params", "1"), ("_typed_params", "0")],
        [("_typed_params", "0"), ("_typed_params", "0")],
    ],
)
async def test_repeated_toggle_is_400_regardless_of_order(ds, flags):
    response = await ds.client.get(
        typed_path("select :age as v", flags + [("age::int", "1")])
    )
    assert response.status_code == 400
    assert "only be supplied once" in response.json()["error"]


@pytest.mark.asyncio
async def test_post_duplicate_typed_values_are_400(ds):
    response = await ds.client.post(
        "/typed/add_probe",
        content="_typed_params=1&age::int=1&age::int=2",
        headers=POST_FORM_HEADERS,
    )
    assert response.status_code == 400
    assert "more than once" in response.text
    assert (
        await ds.get_database("typed").execute("select note from probes")
    ).rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        '{"_typed_params":"1","age::int":"1","age::int":"2"}',
        '{"_typed_params":"1","age::int":"1","age::int":"1"}',
    ],
)
async def test_post_json_duplicate_typed_values_are_400(ds, body):
    response = await ds.client.post(
        "/typed/add_probe", content=body, headers=POST_JSON_HEADERS
    )
    assert response.status_code == 400
    assert "more than once" in response.text
    assert (
        await ds.get_database("typed").execute("select note from probes")
    ).rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        '{"_typed_params":0,"_typed_params":1,"age::int":"1"}',
        '{"_typed_params":1,"_typed_params":0,"age::int":"1"}',
        '{"_typed_params":0,"_typed_params":0,"age::int":"1"}',
    ],
)
async def test_post_json_repeated_toggle_is_400_and_does_not_write(ds, body):
    response = await ds.client.post(
        "/typed/add_probe", content=body, headers=POST_JSON_HEADERS
    )
    assert response.status_code == 400
    assert "only be supplied once" in response.text
    assert (
        await ds.get_database("typed").execute("select note from probes")
    ).rows == []


@pytest.mark.asyncio
async def test_post_json_duplicate_plain_keys_keep_last_value(ds):
    response = await ds.client.post(
        "/typed/add_note",
        content='{"value":"1","value":"2"}',
        headers=POST_JSON_HEADERS,
    )
    assert response.status_code == 302
    row = (await ds.get_database("typed").execute("select value from notes")).first()
    assert row[0] == "2"


@pytest.mark.asyncio
async def test_post_json_nested_object_keeps_legacy_stringification(ds):
    response = await ds.client.post("/typed/add_note", json={"value": {"a": 1}})
    assert response.status_code == 302
    row = (await ds.get_database("typed").execute("select value from notes")).first()
    assert row[0] == "{'a': 1}"


@pytest.mark.asyncio
async def test_custom_query_extra_reports_base_binding_and_replays(ds):
    response = await ds.client.get(
        typed_path(
            AGE_SQL,
            [("_typed_params", "1"), ("age::int", "0"), ("_extra", "query")],
        )
    )
    assert response.status_code == 200
    data = response.json()
    assert data["query"]["sql"] == AGE_SQL
    # Extras must expose the SQL binding name and resolved integer, not the
    # query-string spelling, or the reported SQL/params cannot be replayed.
    assert data["query"]["params"] == {"age": 0}
    replayed = replay_in_memory(data["query"]["sql"], data["query"]["params"])
    assert replayed == data["rows"]
    assert {row["bound_type"] for row in replayed} == {"integer"}
    assert {row["bound_value"] for row in replayed} == {0}


@pytest.mark.asyncio
async def test_stored_query_extra_reports_base_binding_and_replays(ds):
    response = await ds.client.get(
        "/typed/ages_above.json?"
        + urlencode({"_typed_params": "1", "age::int": "0", "_extra": "query"})
    )
    assert response.status_code == 200
    data = response.json()
    assert data["query"]["sql"] == (
        "select name, age from people where age + 1 > :age order by age"
    )
    assert data["query"]["params"] == {"age": 0}
    replayed = replay_in_memory(data["query"]["sql"], data["query"]["params"])
    assert (
        replayed
        == data["rows"]
        == [
            {"name": "Carol", "age": 0},
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 41},
        ]
    )
