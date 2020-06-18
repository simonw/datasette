import pytest
from .fixtures import make_app_client


@pytest.fixture
def canned_write_client():
    with make_app_client(
        extra_databases={"data.db": "create table names (name text)"},
        metadata={
            "databases": {
                "data": {
                    "queries": {
                        "add_name": {
                            "sql": "insert into names (name) values (:name)",
                            "write": True,
                            "on_success_redirect": "/data/add_name?success",
                        },
                        "add_name_specify_id": {
                            "sql": "insert into names (rowid, name) values (:rowid, :name)",
                            "write": True,
                            "on_error_redirect": "/data/add_name_specify_id?error",
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


def test_insert(canned_write_client):
    response = canned_write_client.post(
        "/data/add_name", {"name": "Hello"}, allow_redirects=False, csrftoken_from=True,
    )
    assert 302 == response.status
    assert "/data/add_name?success" == response.headers["Location"]
    messages = canned_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )
    assert [["Query executed, 1 row affected", 1]] == messages


def test_custom_success_message(canned_write_client):
    response = canned_write_client.post(
        "/data/delete_name",
        {"rowid": 1},
        cookies={"ds_actor": canned_write_client.actor_cookie({"id": "root"})},
        allow_redirects=False,
        csrftoken_from=True,
    )
    assert 302 == response.status
    messages = canned_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )
    assert [["Name deleted", 1]] == messages


def test_insert_error(canned_write_client):
    canned_write_client.post("/data/add_name", {"name": "Hello"}, csrftoken_from=True)
    response = canned_write_client.post(
        "/data/add_name_specify_id",
        {"rowid": 1, "name": "Should fail"},
        allow_redirects=False,
        csrftoken_from=True,
    )
    assert 302 == response.status
    assert "/data/add_name_specify_id?error" == response.headers["Location"]
    messages = canned_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )
    assert [["UNIQUE constraint failed: names.rowid", 3]] == messages
    # How about with a custom error message?
    canned_write_client.ds._metadata["databases"]["data"]["queries"][
        "add_name_specify_id"
    ]["on_error_message"] = "ERROR"
    response = canned_write_client.post(
        "/data/add_name_specify_id",
        {"rowid": 1, "name": "Should fail"},
        allow_redirects=False,
        csrftoken_from=True,
    )
    assert [["ERROR", 3]] == canned_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )


def test_custom_params(canned_write_client):
    response = canned_write_client.get("/data/update_name?extra=foo")
    assert '<input type="text" id="qp3" name="extra" value="foo">' in response.text


def test_vary_header(canned_write_client):
    # These forms embed a csrftoken so they should be served with Vary: Cookie
    assert "vary" not in canned_write_client.get("/data").headers
    assert "Cookie" == canned_write_client.get("/data/update_name").headers["vary"]


def test_canned_query_permissions_on_database_page(canned_write_client):
    # Without auth only shows three queries
    query_names = {
        q["name"] for q in canned_write_client.get("/data.json").json["queries"]
    }
    assert {
        "add_name",
        "add_name_specify_id",
        "update_name",
        "from_async_hook",
        "from_hook",
    } == query_names

    # With auth shows four
    response = canned_write_client.get(
        "/data.json",
        cookies={"ds_actor": canned_write_client.actor_cookie({"id": "root"})},
    )
    assert 200 == response.status
    assert [
        {"name": "add_name", "private": False},
        {"name": "add_name_specify_id", "private": False},
        {"name": "delete_name", "private": True},
        {"name": "from_async_hook", "private": False},
        {"name": "from_hook", "private": False},
        {"name": "update_name", "private": False},
    ] == sorted(
        [
            {"name": q["name"], "private": q["private"]}
            for q in response.json["queries"]
        ],
        key=lambda q: q["name"],
    )


def test_canned_query_permissions(canned_write_client):
    assert 403 == canned_write_client.get("/data/delete_name").status
    assert 200 == canned_write_client.get("/data/update_name").status
    cookies = {"ds_actor": canned_write_client.actor_cookie({"id": "root"})}
    assert 200 == canned_write_client.get("/data/delete_name", cookies=cookies).status
    assert 200 == canned_write_client.get("/data/update_name", cookies=cookies).status
