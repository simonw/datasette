import json
import re

import pytest
from asgiref.sync import async_to_sync
from bs4 import BeautifulSoup as Soup

from datasette.app import Datasette

from .fixtures import make_app_client


def update_query(client, name, **kwargs):
    async_to_sync(client.ds.invoke_startup)()
    async_to_sync(client.ds.update_query)("data", name, **kwargs)


@pytest.fixture
def stored_write_client(tmpdir):
    template_dir = tmpdir / "stored_write_templates"
    template_dir.mkdir()
    (template_dir / "query-data-update_name.html").write_text(
        """
    {% extends "query.html" %}
    {% block content %}!!!CUSTOM_UPDATE_NAME_TEMPLATE!!!{{ super() }}{% endblock %}
    """,
        "utf-8",
    )
    with make_app_client(
        extra_databases={"data.db": "create table names (name text)"},
        template_dir=str(template_dir),
        config={
            "databases": {
                "data": {
                    "queries": {
                        "stored_read": {"sql": "select * from names"},
                        "add_name": {
                            "sql": "insert into names (name) values (:name)",
                            "write": True,
                            "on_success_redirect": "/data/add_name?success",
                        },
                        "add_name_specify_id": {
                            "sql": "insert into names (rowid, name) values (:rowid, :name)",
                            "on_success_message_sql": "select 'Name added: ' || :name || ' with rowid ' || :rowid",
                            "write": True,
                            "on_error_redirect": "/data/add_name_specify_id?error",
                        },
                        "add_name_specify_id_with_error_in_on_success_message_sql": {
                            "sql": "insert into names (rowid, name) values (:rowid, :name)",
                            "on_success_message_sql": "select this is bad SQL",
                            "write": True,
                        },
                        "delete_name": {
                            "sql": "delete from names where rowid = :rowid",
                            "write": True,
                            "on_success_message": "Name deleted",
                            "allow": {"id": "root"},
                        },
                        "update_name": {
                            "sql": "update names set name = :name where rowid = :rowid",
                            "params": ["rowid", "name", "extra"],
                            "write": True,
                        },
                    }
                }
            }
        },
    ) as client:
        yield client


@pytest.fixture
def stored_write_immutable_client():
    with make_app_client(
        is_immutable=True,
        config={
            "databases": {
                "fixtures": {
                    "queries": {
                        "add": {
                            "sql": "insert into sortable (text) values (:text)",
                            "write": True,
                        },
                    }
                }
            }
        },
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_stored_query_with_named_parameter(ds_client):
    response = await ds_client.get(
        "/fixtures/neighborhood_search.json?text=town&_shape=arrays"
    )
    assert response.json()["rows"] == [
        ["Corktown", "Detroit", "MI"],
        ["Downtown", "Los Angeles", "CA"],
        ["Downtown", "Detroit", "MI"],
        ["Greektown", "Detroit", "MI"],
        ["Koreatown", "Los Angeles", "CA"],
        ["Mexicantown", "Detroit", "MI"],
    ]


def test_insert(stored_write_client):
    response = stored_write_client.post(
        "/data/add_name",
        {"name": "Hello"},
        csrftoken_from=True,
        cookies={"foo": "bar"},
    )
    messages = stored_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )
    assert messages == [["Query executed, 1 row affected", 1]]
    assert response.status == 302
    assert response.headers["Location"] == "/data/add_name?success"


def test_insert_blocked_cross_site(stored_write_client):
    # A cross-site POST (browser-originated) must be blocked
    response = stored_write_client.post(
        "/data/add_name",
        {"name": "Hello"},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert 403 == response.status


def test_insert_no_cookies_no_csrf(stored_write_client):
    response = stored_write_client.post("/data/add_name", {"name": "Hello"})
    assert 302 == response.status
    assert "/data/add_name?success" == response.headers["Location"]


def test_custom_success_message(stored_write_client):
    response = stored_write_client.post(
        "/data/delete_name",
        {"rowid": 1},
        cookies={"ds_actor": stored_write_client.actor_cookie({"id": "root"})},
        csrftoken_from=True,
    )
    assert 302 == response.status
    messages = stored_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )
    assert [["Name deleted", 1]] == messages


def test_insert_error(stored_write_client):
    stored_write_client.post("/data/add_name", {"name": "Hello"}, csrftoken_from=True)
    response = stored_write_client.post(
        "/data/add_name_specify_id",
        {"rowid": 1, "name": "Should fail"},
        csrftoken_from=True,
    )
    assert 302 == response.status
    assert "/data/add_name_specify_id?error" == response.headers["Location"]
    messages = stored_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )
    assert [["UNIQUE constraint failed: names.rowid", 3]] == messages
    # How about with a custom error message?
    update_query(stored_write_client, "add_name_specify_id", on_error_message="ERROR")
    response = stored_write_client.post(
        "/data/add_name_specify_id",
        {"rowid": 1, "name": "Should fail"},
        csrftoken_from=True,
    )
    assert [["ERROR", 3]] == stored_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )


def test_on_success_message_sql(stored_write_client):
    response = stored_write_client.post(
        "/data/add_name_specify_id",
        {"rowid": 5, "name": "Should be OK"},
        csrftoken_from=True,
    )
    assert response.status == 302
    assert response.headers["Location"] == "/data/add_name_specify_id"
    messages = stored_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )
    assert messages == [["Name added: Should be OK with rowid 5", 1]]


def test_error_in_on_success_message_sql(stored_write_client):
    response = stored_write_client.post(
        "/data/add_name_specify_id_with_error_in_on_success_message_sql",
        {"rowid": 1, "name": "Should fail"},
        csrftoken_from=True,
    )
    messages = stored_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )
    assert messages == [
        ["Error running on_success_message_sql: no such column: bad", 3]
    ]


def test_custom_params(stored_write_client):
    response = stored_write_client.get("/data/update_name?extra=foo")
    assert (
        '<input type="text" id="qp3" name="extra" value="foo" data-parameter-control data-parameter-name="extra">'
        in response.text
    )


def test_stored_query_pages_vary_by_credentials(stored_write_client):
    # Even without per-cookie CSRF tokens, anonymous pages must not be reused
    # for authenticated users whose permissions or navigation can differ.
    for path in ("/data", "/data/update_name"):
        response = stored_write_client.get(path)
        assert response.headers["vary"] == "Cookie, Authorization"


def test_json_post_body(stored_write_client):
    response = stored_write_client.post(
        "/data/add_name",
        body=json.dumps({"name": ["Hello", "there"]}),
    )
    assert 302 == response.status
    assert "/data/add_name?success" == response.headers["Location"]
    rows = stored_write_client.get("/data/names.json?_shape=array").json
    assert rows == [{"rowid": 1, "name": '["Hello", "there"]'}]


@pytest.mark.parametrize(
    "headers,body,querystring",
    (
        (None, "name=NameGoesHere", "?_json=1"),
        ({"Accept": "application/json"}, "name=NameGoesHere", None),
        (None, "name=NameGoesHere&_json=1", None),
        (None, '{"name": "NameGoesHere", "_json": 1}', None),
    ),
)
def test_json_response(stored_write_client, headers, body, querystring):
    response = stored_write_client.post(
        "/data/add_name" + (querystring or ""),
        body=body,
        headers=headers,
    )
    assert 200 == response.status
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.json == {
        "ok": True,
        "message": "Query executed, 1 row affected",
        "redirect": "/data/add_name?success",
    }
    rows = stored_write_client.get("/data/names.json?_shape=array").json
    assert rows == [{"rowid": 1, "name": "NameGoesHere"}]


def test_stored_query_permissions_on_database_page(stored_write_client):
    # Without auth shows the five public queries
    anon_response = stored_write_client.get("/data.json")
    query_names = {q["name"] for q in anon_response.json["queries"]}
    assert query_names == {
        "add_name_specify_id_with_error_in_on_success_message_sql",
        "update_name",
        "add_name_specify_id",
        "stored_read",
        "add_name",
    }
    assert anon_response.json["queries_more"] is False

    # With auth the database page preview shows the first five queries
    response = stored_write_client.get(
        "/data.json",
        cookies={"ds_actor": stored_write_client.actor_cookie({"id": "root"})},
    )
    assert response.status == 200
    query_names_and_private = sorted(
        [
            {"name": q["name"], "private": q["private"]}
            for q in response.json["queries"]
        ],
        key=lambda q: q["name"],
    )
    assert query_names_and_private == [
        {"name": "add_name", "private": False},
        {"name": "add_name_specify_id", "private": False},
        {
            "name": "add_name_specify_id_with_error_in_on_success_message_sql",
            "private": False,
        },
        {"name": "delete_name", "private": True},
        {"name": "stored_read", "private": False},
    ]
    assert response.json["queries_more"] is True

    # The full query list endpoint includes the remaining query
    response = stored_write_client.get(
        "/data/-/queries.json?_size=10",
        cookies={"ds_actor": stored_write_client.actor_cookie({"id": "root"})},
    )
    assert response.status == 200
    query_names_and_private = sorted(
        [
            {"name": q["name"], "private": q["private"]}
            for q in response.json["queries"]
        ],
        key=lambda q: q["name"],
    )
    assert query_names_and_private == [
        {"name": "add_name", "private": False},
        {"name": "add_name_specify_id", "private": False},
        {
            "name": "add_name_specify_id_with_error_in_on_success_message_sql",
            "private": False,
        },
        {"name": "delete_name", "private": True},
        {"name": "stored_read", "private": False},
        {"name": "update_name", "private": False},
    ]


def test_stored_query_permissions(stored_write_client):
    assert 403 == stored_write_client.get("/data/delete_name").status
    assert 200 == stored_write_client.get("/data/update_name").status
    cookies = {"ds_actor": stored_write_client.actor_cookie({"id": "root"})}
    assert 200 == stored_write_client.get("/data/delete_name", cookies=cookies).status
    assert 200 == stored_write_client.get("/data/update_name", cookies=cookies).status


@pytest.fixture(scope="session")
def magic_parameters_client():
    with make_app_client(
        extra_databases={"data.db": "create table logs (line text)"},
        config={
            "databases": {
                "data": {
                    "queries": {
                        "runme_post": {"sql": "", "write": True},
                        "runme_get": {"sql": ""},
                    }
                }
            }
        },
    ) as client:
        yield client


@pytest.mark.parametrize(
    "magic_parameter,expected_re",
    [
        ("_actor_id", "root"),
        ("_header_host", "localhost"),
        ("_header_not_a_thing", ""),
        ("_cookie_foo", "bar"),
        ("_now_epoch", r"^\d+$"),
        ("_now_date_utc", r"^\d{4}-\d{2}-\d{2}$"),
        ("_now_datetime_utc", r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"),
        ("_random_chars_1", r"^\w$"),
        ("_random_chars_10", r"^\w{10}$"),
    ],
)
def test_magic_parameters(magic_parameters_client, magic_parameter, expected_re):
    update_query(
        magic_parameters_client,
        "runme_post",
        sql=f"insert into logs (line) values (:{magic_parameter})",
    )
    update_query(
        magic_parameters_client,
        "runme_get",
        sql=f"select :{magic_parameter} as result",
    )
    cookies = {
        "ds_actor": magic_parameters_client.actor_cookie({"id": "root"}),
        "foo": "bar",
    }
    # Test the GET version
    get_response = magic_parameters_client.get(
        "/data/runme_get.json?_shape=array", cookies=cookies
    )
    get_actual = get_response.json[0]["result"]
    assert re.match(expected_re, str(get_actual))
    # Test the form
    form_response = magic_parameters_client.get("/data/runme_post")
    soup = Soup(form_response.body, "html.parser")
    # The magic parameter should not be represented as a form field
    assert None is soup.find("input", {"name": magic_parameter})
    # Submit the form to create a log line
    response = magic_parameters_client.post(
        "/data/runme_post?_json=1", {}, csrftoken_from=True, cookies=cookies
    )
    assert response.json == {
        "ok": True,
        "message": "Query executed, 1 row affected",
        "redirect": None,
    }
    post_actual = magic_parameters_client.get(
        "/data/logs.json?_sort_desc=rowid&_shape=array"
    ).json[0]["line"]
    assert re.match(expected_re, post_actual)


@pytest.mark.parametrize("use_csrf", [True, False])
@pytest.mark.parametrize("return_json", [True, False])
def test_magic_parameters_csrf_json(magic_parameters_client, use_csrf, return_json):
    update_query(
        magic_parameters_client,
        "runme_post",
        sql="insert into logs (line) values (:_header_host)",
    )
    qs = ""
    if return_json:
        qs = "?_json=1"
    response = magic_parameters_client.post(
        f"/data/runme_post{qs}",
        {},
        csrftoken_from=use_csrf or None,
    )
    if return_json:
        assert response.status == 200
        assert response.json["ok"], response.json
    else:
        assert response.status == 302
        messages = magic_parameters_client.ds.unsign(
            response.cookies["ds_messages"], "messages"
        )
        assert [["Query executed, 1 row affected", 1]] == messages
    post_actual = magic_parameters_client.get(
        "/data/logs.json?_sort_desc=rowid&_shape=array"
    ).json[0]["line"]
    assert post_actual == "localhost"


def test_magic_parameters_cannot_be_used_in_arbitrary_queries(magic_parameters_client):
    response = magic_parameters_client.get(
        "/data/-/query.json?sql=select+:_header_host&_shape=array"
    )
    assert 400 == response.status
    assert response.json["error"].startswith("You did not supply a value for binding")


def test_stored_write_custom_template(stored_write_client):
    response = stored_write_client.get("/data/update_name")
    assert response.status == 200
    assert "!!!CUSTOM_UPDATE_NAME_TEMPLATE!!!" in response.text
    assert (
        "<!-- Templates considered: *query-data-update_name.html, query-data.html, query.html -->"
        in response.text
    )
    # And test for link rel=alternate while we're here:
    assert (
        '<link rel="alternate" type="application/json+datasette" href="http://localhost/data/update_name.json">'
        in response.text
    )
    assert (
        response.headers["link"]
        == '<http://localhost/data/update_name.json>; rel="alternate"; type="application/json+datasette"'
    )


def test_stored_write_query_disabled_for_immutable_database(
    stored_write_immutable_client,
):
    response = stored_write_immutable_client.get("/fixtures/add")
    assert response.status == 200
    assert (
        "This query cannot be executed because the database is immutable."
        in response.text
    )
    assert '<input type="submit" value="Run SQL" disabled>' in response.text
    # Submitting form should get a forbidden error
    response = stored_write_immutable_client.post(
        "/fixtures/add",
        {"text": "text"},
        csrftoken_from=True,
    )
    assert response.status == 403
    assert "Database is immutable" in response.text


JSON_EVENT_SQL = (
    "insert into events (value, flag, extra, tags, meta) "
    "values (:value, :flag, :extra, :tags, :meta)"
)
JSON_EVENT_PARAMS = {
    "value": 5,
    "flag": True,
    "extra": None,
    "tags": ["a", "b"],
    "meta": {"k": 1},
}


async def _json_params_datasette():
    ds = Datasette(
        memory=True,
        config={
            "databases": {
                "data": {
                    "queries": {
                        "add_event": {"sql": JSON_EVENT_SQL, "write": True},
                        "add_event_declared": {
                            "sql": "insert into events (value) values (:value)",
                            "params": ["value", "note"],
                            "write": True,
                        },
                        "add_event_actor": {
                            "sql": "insert into events (value, extra) values (:value, :_actor_id)",
                            "write": True,
                        },
                    }
                }
            }
        },
    )
    ds.root_enabled = True
    db = ds.add_memory_database("stored_query_json_params", name="data")
    # Columns without a declared type store values exactly as they are bound
    await db.execute_write(
        "create table if not exists events (value, flag, extra, tags, meta)"
    )
    await db.execute_write("delete from events")
    await ds.invoke_startup()
    return ds, db


async def _post_json_params(ds, endpoint, params):
    if endpoint == "stored":
        return await ds.client.post(
            "/data/add_event",
            actor={"id": "root"},
            json=params,
            headers={"Accept": "application/json"},
        )
    return await ds.client.post(
        "/data/-/execute-write",
        actor={"id": "root"},
        json={"sql": JSON_EVENT_SQL, "params": params},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ("stored", "execute-write"))
async def test_json_parameters_keep_types(endpoint):
    # https://github.com/simonw/datasette/issues/2946
    ds, db = await _json_params_datasette()
    response = await _post_json_params(ds, endpoint, JSON_EVENT_PARAMS)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    rows = (
        await db.execute(
            "select value, typeof(value) as value_type, flag, typeof(flag) as flag_type, "
            "extra, typeof(extra) as extra_type, tags, meta from events"
        )
    ).dicts()
    assert rows == [
        {
            "value": 5,
            "value_type": "integer",
            "flag": 1,
            "flag_type": "integer",
            "extra": None,
            "extra_type": "null",
            "tags": '["a", "b"]',
            "meta": '{"k": 1}',
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ("stored", "execute-write"))
@pytest.mark.parametrize(
    "params,expected_error",
    (
        ({"value": 1}, "Missing parameters: flag, extra, tags, meta"),
        ({**JSON_EVENT_PARAMS, "bogus": 1}, "Unknown parameters: bogus"),
    ),
)
async def test_json_parameters_missing_or_unknown(endpoint, params, expected_error):
    ds, db = await _json_params_datasette()
    response = await _post_json_params(ds, endpoint, params)
    assert response.status_code == 400
    assert response.json()["errors"] == [expected_error]
    assert (await db.execute("select count(*) from events")).single_value() == 0


@pytest.mark.asyncio
async def test_stored_query_json_params_wrapper_hint():
    ds, db = await _json_params_datasette()
    response = await ds.client.post(
        "/data/add_event",
        actor={"id": "root"},
        json={"params": JSON_EVENT_PARAMS},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["errors"] == [
        (
            "Unknown parameters: params. Stored queries take parameters as "
            'top-level keys, not inside "params"'
        )
    ]
    assert (await db.execute("select count(*) from events")).single_value() == 0


@pytest.mark.asyncio
async def test_stored_query_json_declared_and_magic_parameters():
    ds, db = await _json_params_datasette()
    # Parameters declared in "params" are accepted even if the SQL does not use them
    declared = await ds.client.post(
        "/data/add_event_declared",
        actor={"id": "root"},
        json={"value": 1, "note": "ignored"},
        headers={"Accept": "application/json"},
    )
    assert declared.status_code == 200
    # Magic parameters are filled in by Datasette, not required from the client
    magic = await ds.client.post(
        "/data/add_event_actor",
        actor={"id": "root"},
        json={"value": 2},
        headers={"Accept": "application/json"},
    )
    assert magic.status_code == 200
    rows = (await db.execute("select value, extra from events order by value")).dicts()
    assert rows == [{"value": 1, "extra": None}, {"value": 2, "extra": "root"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,expected_error",
    (
        ("[1, 2]", "JSON must be a dictionary"),
        ("{bad json}", "Invalid JSON: "),
    ),
)
async def test_stored_query_invalid_json_body(body, expected_error):
    ds, db = await _json_params_datasette()
    response = await ds.client.post(
        "/data/add_event",
        actor={"id": "root"},
        content=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["errors"][0].startswith(expected_error)
    assert (await db.execute("select count(*) from events")).single_value() == 0
