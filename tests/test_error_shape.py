"""
Tests for the canonical JSON error shape.

Every JSON error response from Datasette should use one shape:

    {
        "ok": false,
        "error": "<all messages joined with '; '>",
        "errors": ["<message>", ...],
        "status": <int matching the HTTP status code>
    }

Additional context keys (for example "rows" and "truncated" on SQL errors)
are permitted, but "ok", "error", "errors" and "status" must always be
present and the legacy "title" key must not be.

https://github.com/simonw/datasette/issues - 1.0 API consistency
"""

import pytest
import time
from datasette.app import Datasette
from datasette.utils import sqlite3


def assert_canonical_error(response, expected_status):
    assert response.status_code == expected_status
    data = response.json()
    assert data["ok"] is False
    assert isinstance(data["error"], str)
    assert data["error"]
    assert isinstance(data["errors"], list)
    assert data["errors"]
    assert all(isinstance(message, str) for message in data["errors"])
    assert data["error"] == "; ".join(data["errors"])
    assert data["status"] == expected_status
    assert "title" not in data
    return data


@pytest.fixture
def ds_error_shape(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    conn = sqlite3.connect(db_path)
    conn.execute("vacuum")
    conn.execute("create table docs (id integer primary key, title text)")
    conn.close()
    ds = Datasette([db_path])
    ds.root_enabled = True
    yield ds
    ds.close()


# Shape 1: the exception handler (handle_exception.py)


@pytest.mark.asyncio
async def test_not_found_error_shape(ds_client):
    response = await ds_client.get("/fixtures/no_such_table.json")
    assert_canonical_error(response, 404)


@pytest.mark.asyncio
async def test_datasette_error_with_title_omits_title_key(ds_client):
    # DatasetteError(title="Invalid SQL") previously leaked a "title" key
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=update+facetable+set+state+=+1"
    )
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["Statement must be a SELECT"]


# Shape 2: the _error() helper (views/base.py) - write API and friends


@pytest.mark.asyncio
async def test_write_api_validation_error_shape(ds_error_shape):
    token = "dstok_{}".format(
        ds_error_shape.sign(
            {"a": "root", "token": "dstok", "t": 0},
            namespace="token",
        )
    )
    response = await ds_error_shape.client.post(
        "/data/docs/-/insert",
        json={"rows": [{"nope": 1}, {"also_nope": 2}]},
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": "application/json",
        },
    )
    data = assert_canonical_error(response, 400)
    # Multiple messages: errors keeps them all, error joins them
    assert len(data["errors"]) == 2
    assert data["errors"][0].startswith("Row 0")
    assert data["errors"][1].startswith("Row 1")


@pytest.mark.asyncio
async def test_write_api_permission_denied_shape(ds_error_shape):
    response = await ds_error_shape.client.post(
        "/data/docs/-/insert",
        json={"rows": [{"title": "hello"}]},
        headers={"Content-Type": "application/json"},
    )
    assert_canonical_error(response, 403)


# Shape 3: the JSON renderer (renderer.py)


@pytest.mark.asyncio
async def test_sql_error_shape_keeps_context_keys(ds_client):
    response = await ds_client.get(
        "/fixtures/-/query.json?sql=select+*+from+no_such_table"
    )
    data = assert_canonical_error(response, 400)
    # Renderer errors keep their context keys
    assert data["rows"] == []
    assert "truncated" in data


@pytest.mark.asyncio
async def test_invalid_shape_error_shape(ds_client):
    response = await ds_client.get("/fixtures/-/query.json?sql=select+1&_shape=bananas")
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["Invalid _shape: bananas"]


@pytest.mark.asyncio
async def test_shape_object_on_query_is_a_400_error(ds_client):
    # Previously returned HTTP 200 with an ok: false body
    response = await ds_client.get("/fixtures/-/query.json?sql=select+1&_shape=object")
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["_shape=object is only available on tables"]


# Shape 4: bare {"error": ...} from the permission debug endpoints


@pytest.mark.asyncio
async def test_allowed_missing_action_error_shape(ds_client):
    response = await ds_client.get("/-/allowed.json")
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["action parameter is required"]


@pytest.mark.asyncio
async def test_allowed_unknown_action_error_shape(ds_client):
    response = await ds_client.get("/-/allowed.json?action=no_such_action")
    assert_canonical_error(response, 404)


@pytest.mark.asyncio
async def test_check_unknown_action_error_shape(ds_error_shape):
    response = await ds_error_shape.client.get(
        "/-/check.json?action=no_such_action",
        actor={"id": "root"},
    )
    assert_canonical_error(response, 404)


@pytest.mark.asyncio
async def test_rules_missing_action_error_shape(ds_error_shape):
    response = await ds_error_shape.client.get(
        "/-/rules.json",
        actor={"id": "root"},
    )
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["action parameter is required"]


# Other stragglers


@pytest.mark.asyncio
async def test_method_not_allowed_error_shape(ds_client):
    response = await ds_client.post("/fixtures.json")
    assert_canonical_error(response, 405)


@pytest.mark.asyncio
async def test_schema_unknown_database_error_shape(ds_client):
    response = await ds_client.get("/no_such_db/-/schema.json")
    assert_canonical_error(response, 404)


# Forbidden responses (the default forbidden() hook)


@pytest.fixture
def ds_forbidden(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    conn = sqlite3.connect(db_path)
    conn.execute("vacuum")
    conn.execute("create table docs (id integer primary key, title text)")
    conn.close()
    ds = Datasette(
        [db_path],
        config={"databases": {"data": {"tables": {"docs": {"allow": {"id": "root"}}}}}},
    )
    ds.root_enabled = True
    yield ds
    ds.close()


@pytest.mark.asyncio
async def test_forbidden_json_path_returns_canonical_json(ds_forbidden):
    response = await ds_forbidden.client.get("/data/docs.json")
    data = assert_canonical_error(response, 403)
    assert "permission" in data["error"].lower()


@pytest.mark.asyncio
async def test_forbidden_accept_json_returns_canonical_json(ds_forbidden):
    response = await ds_forbidden.client.get(
        "/data/docs", headers={"Accept": "application/json"}
    )
    assert_canonical_error(response, 403)


@pytest.mark.asyncio
async def test_forbidden_html_path_still_returns_html(ds_forbidden):
    response = await ds_forbidden.client.get("/data/docs")
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_forbidden_json_path_allowed_actor_still_works(ds_forbidden):
    response = await ds_forbidden.client.get("/data/docs.json", actor={"id": "root"})
    assert response.status_code == 200
    assert response.json()["ok"] is True


# Write canned queries: SQL failures must not return HTTP 200


@pytest.fixture
def ds_write_query(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    conn = sqlite3.connect(db_path)
    conn.execute("vacuum")
    conn.execute("create table docs (id integer primary key, title text)")
    conn.close()
    ds = Datasette(
        [db_path],
        config={
            "databases": {
                "data": {
                    "queries": {
                        "add_doc": {
                            "sql": (
                                "insert into docs (id, title)" " values (:id, :title)"
                            ),
                            "write": True,
                        },
                        "add_doc_custom_error": {
                            "sql": (
                                "insert into docs (id, title)" " values (:id, :title)"
                            ),
                            "write": True,
                            "on_error_message": "Custom error message",
                            "on_error_redirect": "/data",
                        },
                    }
                }
            }
        },
    )
    yield ds
    ds.close()


@pytest.mark.asyncio
async def test_write_query_success_returns_200(ds_write_query):
    response = await ds_write_query.client.post(
        "/data/add_doc",
        json={"id": 1, "title": "One"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["message"] == "Query executed, 1 row affected"
    assert data["redirect"] is None


@pytest.mark.asyncio
async def test_write_query_sql_failure_returns_400(ds_write_query):
    for _ in range(2):
        response = await ds_write_query.client.post(
            "/data/add_doc",
            json={"id": 1, "title": "One"},
            headers={"Accept": "application/json"},
        )
    data = assert_canonical_error(response, 400)
    assert "UNIQUE constraint failed" in data["error"]
    # The redirect context key from the canned query flow is preserved
    assert data["redirect"] is None


@pytest.mark.asyncio
async def test_write_query_failure_uses_on_error_message_and_redirect(
    ds_write_query,
):
    for _ in range(2):
        response = await ds_write_query.client.post(
            "/data/add_doc_custom_error",
            json={"id": 1, "title": "One"},
            headers={"Accept": "application/json"},
        )
    data = assert_canonical_error(response, 400)
    assert data["error"] == "Custom error message"
    assert data["redirect"] == "/data"


@pytest.mark.asyncio
async def test_write_query_forbidden_is_canonical_403(ds_write_query):
    # An untrusted write query run by an actor without execute-write-sql
    # raises Forbidden, handled by the forbidden() hook
    await ds_write_query.invoke_startup()
    await ds_write_query.add_query(
        "data",
        name="untrusted_add",
        sql="insert into docs (id, title) values (:id, :title)",
        is_write=True,
        is_trusted=False,
        source="user",
        owner_id="someone",
    )
    response = await ds_write_query.client.post(
        "/data/untrusted_add",
        json={"id": 5, "title": "Five"},
        headers={"Accept": "application/json"},
        actor={"id": "someone"},
    )
    assert_canonical_error(response, 403)


@pytest.mark.asyncio
async def test_write_query_rejected_operation_is_canonical_403(ds_write_query):
    # A rejected operation (VACUUM) raises QueryWriteRejected, handled by
    # the dedicated branch in QueryView.post - root has execute-write-sql
    ds_write_query.root_enabled = True
    await ds_write_query.invoke_startup()
    await ds_write_query.add_query(
        "data",
        name="vacuum_it",
        sql="vacuum",
        is_write=True,
        is_trusted=False,
        source="user",
        owner_id="root",
    )
    response = await ds_write_query.client.post(
        "/data/vacuum_it",
        json={},
        headers={"Accept": "application/json"},
        actor={"id": "root"},
    )
    data = assert_canonical_error(response, 403)
    assert data["redirect"] is None


# Row delete write failures must be 400, matching row update


@pytest.mark.asyncio
async def test_row_delete_write_failure_is_400(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    conn = sqlite3.connect(db_path)
    conn.execute("vacuum")
    conn.execute("create table docs (id integer primary key, title text)")
    conn.execute("insert into docs (id, title) values (1, 'One')")
    conn.execute(
        "create trigger no_delete before delete on docs "
        "begin select raise(abort, 'deletes are blocked'); end"
    )
    conn.commit()
    conn.close()
    ds = Datasette([db_path])
    ds.root_enabled = True
    try:
        response = await ds.client.post(
            "/data/docs/1/-/delete",
            json={},
            headers={"Content-Type": "application/json"},
            actor={"id": "root"},
        )
        data = assert_canonical_error(response, 400)
        assert "deletes are blocked" in data["error"]
    finally:
        ds.close()


# Invalid bearer tokens must produce 401, not silent anonymous access


@pytest.mark.asyncio
async def test_expired_token_returns_401(ds_error_shape):
    token = "dstok_{}".format(
        ds_error_shape.sign(
            {"a": "root", "t": int(time.time()) - 2000, "d": 1000},
            namespace="token",
        )
    )
    response = await ds_error_shape.client.get(
        "/-/actor.json", headers={"Authorization": "Bearer {}".format(token)}
    )
    data = assert_canonical_error(response, 401)
    assert "expired" in data["error"].lower()
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.asyncio
async def test_bad_signature_token_returns_401(ds_error_shape):
    response = await ds_error_shape.client.get(
        "/-/actor.json", headers={"Authorization": "Bearer dstok_garbage"}
    )
    data = assert_canonical_error(response, 401)
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.asyncio
async def test_unrecognized_token_prefix_stays_anonymous(ds_error_shape):
    # No registered handler claims this token - it might belong to a
    # plugin's actor_from_request hook, so it must not hard-fail
    response = await ds_error_shape.client.get(
        "/-/actor.json", headers={"Authorization": "Bearer sometoken_abc"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "actor": None}


@pytest.mark.asyncio
async def test_valid_token_still_authenticates(ds_error_shape):
    token = "dstok_{}".format(
        ds_error_shape.sign(
            {"a": "root", "t": int(time.time())},
            namespace="token",
        )
    )
    response = await ds_error_shape.client.get(
        "/-/actor.json", headers={"Authorization": "Bearer {}".format(token)}
    )
    assert response.status_code == 200
    assert response.json()["actor"]["id"] == "root"


@pytest.mark.asyncio
async def test_bad_token_beats_valid_cookie(ds_error_shape):
    # A malformed Authorization header is a hard error even if a valid
    # ds_actor cookie is also present
    response = await ds_error_shape.client.get(
        "/-/actor.json",
        headers={"Authorization": "Bearer dstok_garbage"},
        cookies={"ds_actor": ds_error_shape.client.actor_cookie({"id": "root"})},
    )
    assert_canonical_error(response, 401)


@pytest.mark.asyncio
async def test_token_when_signed_tokens_disabled_returns_401(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    conn = sqlite3.connect(db_path)
    conn.execute("vacuum")
    conn.close()
    ds = Datasette([db_path], settings={"allow_signed_tokens": False})
    try:
        token = "dstok_{}".format(
            ds.sign({"a": "root", "t": int(time.time())}, namespace="token")
        )
        response = await ds.client.get(
            "/-/actor.json", headers={"Authorization": "Bearer {}".format(token)}
        )
        data = assert_canonical_error(response, 401)
        assert "not enabled" in data["error"]
    finally:
        ds.close()


# GET /db/-/query without SQL: 400 for data formats, HTML editor stays 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/fixtures/-/query.json",
        "/fixtures/-/query.json?sql=",
    ),
)
async def test_query_json_without_sql_is_400(ds_client, path):
    response = await ds_client.get(path)
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["?sql= is required"]


@pytest.mark.asyncio
async def test_query_html_without_sql_is_still_the_editor(ds_client):
    response = await ds_client.get("/fixtures/-/query")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


# Write API return:true responses use "rows" consistently


@pytest.mark.asyncio
async def test_row_update_return_uses_rows_list(ds_error_shape):
    await ds_error_shape.client.post(
        "/data/docs/-/insert",
        json={"row": {"id": 1, "title": "One"}},
        headers={"Content-Type": "application/json"},
        actor={"id": "root"},
    )
    response = await ds_error_shape.client.post(
        "/data/docs/1/-/update",
        json={"update": {"title": "Updated"}, "return": True},
        headers={"Content-Type": "application/json"},
        actor={"id": "root"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "row" not in data
    assert data["rows"] == [{"id": 1, "title": "Updated"}]


# Schema endpoints: no existence oracle, no 500 on unknown database


@pytest.mark.asyncio
async def test_schema_endpoints_no_existence_oracle(tmp_path_factory):
    db_directory = tmp_path_factory.mktemp("dbs")
    db_path = str(db_directory / "data.db")
    conn = sqlite3.connect(db_path)
    conn.execute("vacuum")
    conn.execute("create table docs (id integer primary key)")
    conn.close()
    ds = Datasette([db_path], default_deny=True)
    ds.root_enabled = True
    try:
        # An actor without view-database cannot distinguish an existing
        # database from a missing one
        denied_existing = await ds.client.get("/data/-/schema.json")
        denied_missing = await ds.client.get("/nope/-/schema.json")
        assert denied_existing.status_code == denied_missing.status_code == 403

        # An authorized actor sees the real thing
        root_existing = await ds.client.get("/data/-/schema.json", actor={"id": "root"})
        assert root_existing.status_code == 200
        root_missing = await ds.client.get("/nope/-/schema.json", actor={"id": "root"})
        assert root_missing.status_code == 404
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_table_schema_unknown_database_is_404_not_500(ds_client):
    response = await ds_client.get("/no_such_db/some_table/-/schema.json")
    assert_canonical_error(response, 404)


# Unknown _extra names are a 400, not silently ignored


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    (
        "/fixtures/facetable.json?_extra=nope",
        "/fixtures/facetable.json?_extra=count,nope",
        "/fixtures/simple_primary_key/1.json?_extra=nope",
        "/fixtures/-/query.json?sql=select+1&_extra=nope",
    ),
)
async def test_unknown_extra_is_400(ds_client, path):
    response = await ds_client.get(path)
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["Unknown _extra: nope"]


@pytest.mark.asyncio
async def test_html_only_extra_via_json_is_400(ds_client):
    # display_rows exists for the HTML view but is not part of the JSON API
    response = await ds_client.get("/fixtures/facetable.json?_extra=display_rows")
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["Unknown _extra: display_rows"]


@pytest.mark.asyncio
async def test_unknown_extra_ignored_on_html_pages(ds_client):
    response = await ds_client.get("/fixtures/facetable?_extra=nope")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


# /-/threads exposes runtime internals and requires permissions-debug


@pytest.mark.asyncio
async def test_threads_requires_permissions_debug(ds_error_shape):
    denied = await ds_error_shape.client.get("/-/threads.json")
    assert_canonical_error(denied, 403)
    allowed = await ds_error_shape.client.get("/-/threads.json", actor={"id": "root"})
    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True


# _size is the one page-size parameter, with uniform validation


@pytest.mark.asyncio
async def test_query_list_size_supports_max_keyword(ds_client):
    response = await ds_client.get("/fixtures/-/queries.json?_size=max")
    assert response.status_code == 200
    # ds_client runs with max_returned_rows=100
    assert response.json()["limit"] == 100


@pytest.mark.asyncio
async def test_query_list_size_rejects_out_of_range(ds_client):
    response = await ds_client.get("/fixtures/-/queries.json?_size=5000")
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["_size must be <= 100"]


@pytest.mark.asyncio
async def test_query_list_size_rejects_non_integer(ds_client):
    response = await ds_client.get("/fixtures/-/queries.json?_size=bananas")
    data = assert_canonical_error(response, 400)
    assert data["errors"] == ["_size must be a positive integer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ("allowed", "rules"))
async def test_debug_endpoints_use_size_and_page_parameters(ds_error_shape, endpoint):
    base = "/-/{}.json?action=view-instance".format(endpoint)
    ok = await ds_error_shape.client.get(
        base + "&_size=1&_page=1", actor={"id": "root"}
    )
    assert ok.status_code == 200
    assert ok.json()["page_size"] == 1

    max_size = await ds_error_shape.client.get(
        base + "&_size=max", actor={"id": "root"}
    )
    assert max_size.status_code == 200
    assert max_size.json()["page_size"] == 200

    too_big = await ds_error_shape.client.get(base + "&_size=500", actor={"id": "root"})
    data = assert_canonical_error(too_big, 400)
    assert data["errors"] == ["_size must be <= 200"]

    bad_page = await ds_error_shape.client.get(base + "&_page=0", actor={"id": "root"})
    data = assert_canonical_error(bad_page, 400)
    assert data["errors"] == ["_page must be a positive integer"]
