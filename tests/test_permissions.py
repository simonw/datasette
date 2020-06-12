from .fixtures import app_client, assert_permissions_checked, make_app_client
from bs4 import BeautifulSoup as Soup
import pytest


@pytest.mark.parametrize(
    "allow,expected_anon,expected_auth",
    [(None, 200, 200), ({}, 403, 403), ({"id": "root"}, 403, 200),],
)
def test_view_instance(allow, expected_anon, expected_auth):
    with make_app_client(metadata={"allow": allow}) as client:
        for path in (
            "/",
            "/fixtures",
            "/fixtures/compound_three_primary_keys",
            "/fixtures/compound_three_primary_keys/a,a,a",
        ):
            anon_response = client.get(path)
            assert expected_anon == anon_response.status
            if allow and path == "/" and anon_response.status == 200:
                # Should be no padlock
                assert "<h1>Datasette 🔒</h1>" not in anon_response.text
            auth_response = client.get(
                path, cookies={"ds_actor": client.actor_cookie({"id": "root"})},
            )
            assert expected_auth == auth_response.status
            # Check for the padlock
            if allow and path == "/" and expected_anon == 403 and expected_auth == 200:
                assert "<h1>Datasette 🔒</h1>" in auth_response.text


@pytest.mark.parametrize(
    "allow,expected_anon,expected_auth",
    [(None, 200, 200), ({}, 403, 403), ({"id": "root"}, 403, 200),],
)
def test_view_database(allow, expected_anon, expected_auth):
    with make_app_client(
        metadata={"databases": {"fixtures": {"allow": allow}}}
    ) as client:
        for path in (
            "/fixtures",
            "/fixtures/compound_three_primary_keys",
            "/fixtures/compound_three_primary_keys/a,a,a",
        ):
            anon_response = client.get(path)
            assert expected_anon == anon_response.status
            if allow and path == "/fixtures" and anon_response.status == 200:
                # Should be no padlock
                assert ">fixtures 🔒</h1>" not in anon_response.text
            auth_response = client.get(
                path, cookies={"ds_actor": client.actor_cookie({"id": "root"})},
            )
            assert expected_auth == auth_response.status
            if (
                allow
                and path == "/fixtures"
                and expected_anon == 403
                and expected_auth == 200
            ):
                assert ">fixtures 🔒</h1>" in auth_response.text


def test_database_list_respects_view_database():
    with make_app_client(
        metadata={"databases": {"fixtures": {"allow": {"id": "root"}}}},
        extra_databases={"data.db": "create table names (name text)"},
    ) as client:
        anon_response = client.get("/")
        assert '<a href="/data">data</a></h2>' in anon_response.text
        assert '<a href="/fixtures">fixtures</a>' not in anon_response.text
        auth_response = client.get(
            "/", cookies={"ds_actor": client.actor_cookie({"id": "root"})},
        )
        assert '<a href="/data">data</a></h2>' in auth_response.text
        assert '<a href="/fixtures">fixtures</a> 🔒</h2>' in auth_response.text


def test_database_list_respects_view_table():
    with make_app_client(
        metadata={
            "databases": {
                "data": {
                    "tables": {
                        "names": {"allow": {"id": "root"}},
                        "v": {"allow": {"id": "root"}},
                    }
                }
            }
        },
        extra_databases={
            "data.db": "create table names (name text); create view v as select * from names"
        },
    ) as client:
        html_fragments = [
            ">names</a> 🔒",
            ">v</a> 🔒",
        ]
        anon_response_text = client.get("/").text
        assert "0 rows in 0 tables" in anon_response_text
        for html_fragment in html_fragments:
            assert html_fragment not in anon_response_text
        auth_response_text = client.get(
            "/", cookies={"ds_actor": client.actor_cookie({"id": "root"})},
        ).text
        for html_fragment in html_fragments:
            assert html_fragment in auth_response_text


@pytest.mark.parametrize(
    "allow,expected_anon,expected_auth",
    [(None, 200, 200), ({}, 403, 403), ({"id": "root"}, 403, 200),],
)
def test_view_table(allow, expected_anon, expected_auth):
    with make_app_client(
        metadata={
            "databases": {
                "fixtures": {
                    "tables": {"compound_three_primary_keys": {"allow": allow}}
                }
            }
        }
    ) as client:
        anon_response = client.get("/fixtures/compound_three_primary_keys")
        assert expected_anon == anon_response.status
        if allow and anon_response.status == 200:
            # Should be no padlock
            assert ">compound_three_primary_keys 🔒</h1>" not in anon_response.text
        auth_response = client.get(
            "/fixtures/compound_three_primary_keys",
            cookies={"ds_actor": client.actor_cookie({"id": "root"})},
        )
        assert expected_auth == auth_response.status
        if allow and expected_anon == 403 and expected_auth == 200:
            assert ">compound_three_primary_keys 🔒</h1>" in auth_response.text


def test_table_list_respects_view_table():
    with make_app_client(
        metadata={
            "databases": {
                "fixtures": {
                    "tables": {
                        "compound_three_primary_keys": {"allow": {"id": "root"}},
                        # And a SQL view too:
                        "paginated_view": {"allow": {"id": "root"}},
                    }
                }
            }
        }
    ) as client:
        html_fragments = [
            ">compound_three_primary_keys</a> 🔒",
            ">paginated_view</a> 🔒",
        ]
        anon_response = client.get("/fixtures")
        for html_fragment in html_fragments:
            assert html_fragment not in anon_response.text
        auth_response = client.get(
            "/fixtures", cookies={"ds_actor": client.actor_cookie({"id": "root"})}
        )
        for html_fragment in html_fragments:
            assert html_fragment in auth_response.text


@pytest.mark.parametrize(
    "allow,expected_anon,expected_auth",
    [(None, 200, 200), ({}, 403, 403), ({"id": "root"}, 403, 200),],
)
def test_view_query(allow, expected_anon, expected_auth):
    with make_app_client(
        metadata={
            "databases": {
                "fixtures": {"queries": {"q": {"sql": "select 1 + 1", "allow": allow}}}
            }
        }
    ) as client:
        anon_response = client.get("/fixtures/q")
        assert expected_anon == anon_response.status
        if allow and anon_response.status == 200:
            # Should be no padlock
            assert ">fixtures 🔒</h1>" not in anon_response.text
        auth_response = client.get(
            "/fixtures/q", cookies={"ds_actor": client.actor_cookie({"id": "root"})}
        )
        assert expected_auth == auth_response.status
        if allow and expected_anon == 403 and expected_auth == 200:
            assert ">fixtures 🔒</h1>" in auth_response.text


@pytest.mark.parametrize(
    "metadata",
    [
        {"allow_sql": {"id": "root"}},
        {"databases": {"fixtures": {"allow_sql": {"id": "root"}}}},
    ],
)
def test_execute_sql(metadata):
    with make_app_client(metadata=metadata) as client:
        form_fragment = '<form class="sql" action="/fixtures"'

        # Anonymous users - should not display the form:
        assert form_fragment not in client.get("/fixtures").text
        # This should 403:
        assert 403 == client.get("/fixtures?sql=select+1").status
        # ?_where= not allowed on tables:
        assert 403 == client.get("/fixtures/facet_cities?_where=id=3").status

        # But for logged in user all of these should work:
        cookies = {"ds_actor": client.actor_cookie({"id": "root"})}
        response_text = client.get("/fixtures", cookies=cookies).text
        assert form_fragment in response_text
        assert 200 == client.get("/fixtures?sql=select+1", cookies=cookies).status
        assert (
            200
            == client.get("/fixtures/facet_cities?_where=id=3", cookies=cookies).status
        )


def test_query_list_respects_view_query():
    with make_app_client(
        metadata={
            "databases": {
                "fixtures": {
                    "queries": {"q": {"sql": "select 1 + 1", "allow": {"id": "root"}}}
                }
            }
        }
    ) as client:
        html_fragment = '<li><a href="/fixtures/q" title="select 1 + 1">q</a> 🔒</li>'
        anon_response = client.get("/fixtures")
        assert html_fragment not in anon_response.text
        assert '"/fixtures/q"' not in anon_response.text
        auth_response = client.get(
            "/fixtures", cookies={"ds_actor": client.actor_cookie({"id": "root"})}
        )
        assert html_fragment in auth_response.text


@pytest.mark.parametrize(
    "path,permissions",
    [
        ("/", ["view-instance"]),
        ("/fixtures", ["view-instance", ("view-database", "fixtures")]),
        (
            "/fixtures/facetable/1",
            ["view-instance", ("view-table", ("fixtures", "facetable"))],
        ),
        (
            "/fixtures/simple_primary_key",
            [
                "view-instance",
                ("view-database", "fixtures"),
                ("view-table", ("fixtures", "simple_primary_key")),
            ],
        ),
        (
            "/fixtures?sql=select+1",
            [
                "view-instance",
                ("view-database", "fixtures"),
                ("execute-sql", "fixtures"),
            ],
        ),
        (
            "/fixtures.db",
            [
                "view-instance",
                ("view-database", "fixtures"),
                ("view-database-download", "fixtures"),
            ],
        ),
        (
            "/fixtures/neighborhood_search",
            [
                "view-instance",
                ("view-database", "fixtures"),
                ("view-query", ("fixtures", "neighborhood_search")),
            ],
        ),
    ],
)
def test_permissions_checked(app_client, path, permissions):
    app_client.ds._permission_checks.clear()
    response = app_client.get(path)
    assert response.status in (200, 403)
    assert_permissions_checked(app_client.ds, permissions)


def test_permissions_debug(app_client):
    app_client.ds._permission_checks.clear()
    assert 403 == app_client.get("/-/permissions").status
    # With the cookie it should work
    cookie = app_client.actor_cookie({"id": "root"})
    response = app_client.get("/-/permissions", cookies={"ds_actor": cookie})
    # Should show one failure and one success
    soup = Soup(response.body, "html.parser")
    check_divs = soup.findAll("div", {"class": "check"})
    checks = [
        {
            "action": div.select_one(".check-action").text,
            "result": bool(div.select(".check-result-true")),
            "used_default": bool(div.select(".check-used-default")),
        }
        for div in check_divs
    ]
    assert [
        {"action": "permissions-debug", "result": True, "used_default": False},
        {"action": "view-instance", "result": True, "used_default": True},
        {"action": "permissions-debug", "result": False, "used_default": True},
        {"action": "view-instance", "result": True, "used_default": True},
    ] == checks


@pytest.mark.parametrize(
    "allow,expected",
    [({"id": "root"}, 403), ({"id": "root", "unauthenticated": True}, 200),],
)
def test_allow_unauthenticated(allow, expected):
    with make_app_client(metadata={"allow": allow}) as client:
        assert expected == client.get("/").status


@pytest.fixture(scope="session")
def view_instance_client():
    with make_app_client(metadata={"allow": {}}) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/fixtures",
        "/fixtures/facetable",
        "/-/metadata",
        "/-/versions",
        "/-/plugins",
        "/-/config",
        "/-/threads",
        "/-/databases",
        "/-/actor",
        "/-/permissions",
        "/-/messages",
        "/-/patterns",
    ],
)
def test_view_instance(path, view_instance_client):
    assert 403 == view_instance_client.get(path).status
    if path not in ("/-/permissions", "/-/messages", "/-/patterns"):
        assert 403 == view_instance_client.get(path + ".json").status
