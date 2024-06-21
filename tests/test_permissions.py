from .fixtures import app_client, assert_permissions_checked, make_app_client
from bs4 import BeautifulSoup as Soup
import copy
import pytest
import urllib


@pytest.fixture(scope="module")
def padlock_client():
    with make_app_client(
        metadata={
            "databases": {
                "fixtures": {
                    "queries": {"two": {"sql": "select 1 + 1"}},
                }
            }
        }
    ) as client:
        yield client


@pytest.mark.parametrize(
    "allow,expected_anon,expected_auth",
    [
        (None, 200, 200),
        ({}, 403, 403),
        ({"id": "root"}, 403, 200),
    ],
)
@pytest.mark.parametrize(
    "path",
    (
        "/",
        "/fixtures",
        "/fixtures/compound_three_primary_keys",
        "/fixtures/compound_three_primary_keys/a,a,a",
        "/fixtures/two",  # Query
    ),
)
def test_view_padlock(allow, expected_anon, expected_auth, path, padlock_client):
    padlock_client.ds._metadata_local["allow"] = allow
    fragment = "🔒</h1>"
    anon_response = padlock_client.get(path)
    assert expected_anon == anon_response.status
    if allow and anon_response.status == 200:
        # Should be no padlock
        assert fragment not in anon_response.text
    auth_response = padlock_client.get(
        path,
        cookies={"ds_actor": padlock_client.actor_cookie({"id": "root"})},
    )
    assert expected_auth == auth_response.status
    # Check for the padlock
    if allow and expected_anon == 403 and expected_auth == 200:
        assert fragment in auth_response.text
    del padlock_client.ds._metadata_local["allow"]


@pytest.mark.parametrize(
    "allow,expected_anon,expected_auth",
    [
        (None, 200, 200),
        ({}, 403, 403),
        ({"id": "root"}, 403, 200),
    ],
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
            assert expected_anon == anon_response.status, path
            if allow and path == "/fixtures" and anon_response.status == 200:
                # Should be no padlock
                assert ">fixtures 🔒</h1>" not in anon_response.text
            auth_response = client.get(
                path,
                cookies={"ds_actor": client.actor_cookie({"id": "root"})},
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
            "/",
            cookies={"ds_actor": client.actor_cookie({"id": "root"})},
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
            "/",
            cookies={"ds_actor": client.actor_cookie({"id": "root"})},
        ).text
        for html_fragment in html_fragments:
            assert html_fragment in auth_response_text


@pytest.mark.parametrize(
    "allow,expected_anon,expected_auth",
    [
        (None, 200, 200),
        ({}, 403, 403),
        ({"id": "root"}, 403, 200),
    ],
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
    [
        (None, 200, 200),
        ({}, 403, 403),
        ({"id": "root"}, 403, 200),
    ],
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
            assert "🔒</h1>" not in anon_response.text
        auth_response = client.get(
            "/fixtures/q", cookies={"ds_actor": client.actor_cookie({"id": "root"})}
        )
        assert expected_auth == auth_response.status
        if allow and expected_anon == 403 and expected_auth == 200:
            assert ">fixtures: q 🔒</h1>" in auth_response.text


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
    assert app_client.get("/-/permissions").status == 403
    # With the cookie it should work
    cookie = app_client.actor_cookie({"id": "root"})
    response = app_client.get("/-/permissions", cookies={"ds_actor": cookie})
    assert response.status == 200
    # Should show one failure and one success
    soup = Soup(response.body, "html.parser")
    check_divs = soup.findAll("div", {"class": "check"})
    checks = [
        {
            "action": div.select_one(".check-action").text,
            # True = green tick, False = red cross, None = gray None
            "result": (
                None
                if div.select(".check-result-no-opinion")
                else bool(div.select(".check-result-true"))
            ),
            "used_default": bool(div.select(".check-used-default")),
        }
        for div in check_divs
    ]
    assert checks == [
        {"action": "permissions-debug", "result": True, "used_default": False},
        {"action": "view-instance", "result": None, "used_default": True},
        {"action": "debug-menu", "result": False, "used_default": True},
        {"action": "view-instance", "result": True, "used_default": True},
        {"action": "permissions-debug", "result": False, "used_default": True},
        {"action": "view-instance", "result": None, "used_default": True},
    ]


@pytest.mark.parametrize(
    "actor,allow,expected_fragment",
    [
        ('{"id":"root"}', "{}", "Result: deny"),
        ('{"id":"root"}', '{"id": "*"}', "Result: allow"),
        ('{"', '{"id": "*"}', "Actor JSON error"),
        ('{"id":"root"}', '"*"}', "Allow JSON error"),
    ],
)
def test_allow_debug(app_client, actor, allow, expected_fragment):
    response = app_client.get(
        "/-/allow-debug?" + urllib.parse.urlencode({"actor": actor, "allow": allow})
    )
    assert 200 == response.status
    assert expected_fragment in response.text


@pytest.mark.parametrize(
    "allow,expected",
    [
        ({"id": "root"}, 403),
        ({"id": "root", "unauthenticated": True}, 200),
    ],
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
        "/-/settings",
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


@pytest.fixture(scope="session")
def cascade_app_client():
    with make_app_client(is_immutable=True) as client:
        yield client


@pytest.mark.parametrize(
    "path,permissions,expected_status",
    [
        ("/", [], 403),
        ("/", ["instance"], 200),
        # Can view table even if not allowed database or instance
        ("/fixtures/binary_data", [], 403),
        ("/fixtures/binary_data", ["database"], 403),
        ("/fixtures/binary_data", ["instance"], 403),
        ("/fixtures/binary_data", ["table"], 200),
        ("/fixtures/binary_data", ["table", "database"], 200),
        ("/fixtures/binary_data", ["table", "database", "instance"], 200),
        # ... same for row
        ("/fixtures/binary_data/1", [], 403),
        ("/fixtures/binary_data/1", ["database"], 403),
        ("/fixtures/binary_data/1", ["instance"], 403),
        ("/fixtures/binary_data/1", ["table"], 200),
        ("/fixtures/binary_data/1", ["table", "database"], 200),
        ("/fixtures/binary_data/1", ["table", "database", "instance"], 200),
        # Can view query even if not allowed database or instance
        ("/fixtures/magic_parameters", [], 403),
        ("/fixtures/magic_parameters", ["database"], 403),
        ("/fixtures/magic_parameters", ["instance"], 403),
        ("/fixtures/magic_parameters", ["query"], 200),
        ("/fixtures/magic_parameters", ["query", "database"], 200),
        ("/fixtures/magic_parameters", ["query", "database", "instance"], 200),
        # Can view database even if not allowed instance
        ("/fixtures", [], 403),
        ("/fixtures", ["instance"], 403),
        ("/fixtures", ["database"], 200),
        # Downloading the fixtures.db file
        ("/fixtures.db", [], 403),
        ("/fixtures.db", ["instance"], 403),
        ("/fixtures.db", ["database"], 200),
        ("/fixtures.db", ["download"], 200),
    ],
)
def test_permissions_cascade(cascade_app_client, path, permissions, expected_status):
    """Test that e.g. having view-table but NOT view-database lets you view table page, etc"""
    allow = {"id": "*"}
    deny = {}
    previous_metadata = cascade_app_client.ds.metadata()
    updated_metadata = copy.deepcopy(previous_metadata)
    actor = {"id": "test"}
    if "download" in permissions:
        actor["can_download"] = 1
    try:
        # Set up the different allow blocks
        updated_metadata["allow"] = allow if "instance" in permissions else deny
        updated_metadata["databases"]["fixtures"]["allow"] = (
            allow if "database" in permissions else deny
        )
        updated_metadata["databases"]["fixtures"]["tables"]["binary_data"] = {
            "allow": (allow if "table" in permissions else deny)
        }
        updated_metadata["databases"]["fixtures"]["queries"]["magic_parameters"][
            "allow"
        ] = (allow if "query" in permissions else deny)
        cascade_app_client.ds._metadata_local = updated_metadata
        response = cascade_app_client.get(
            path,
            cookies={"ds_actor": cascade_app_client.actor_cookie(actor)},
        )
        assert (
            response.status == expected_status
        ), "path: {}, permissions: {}, expected_status: {}, status: {}".format(
            path, permissions, expected_status, response.status
        )
    finally:
        cascade_app_client.ds._metadata_local = previous_metadata


def test_padlocks_on_database_page(cascade_app_client):
    metadata = {
        "databases": {
            "fixtures": {
                "allow": {"id": "test"},
                "tables": {
                    "123_starts_with_digits": {"allow": True},
                    "simple_view": {"allow": True},
                },
                "queries": {"query_two": {"allow": True, "sql": "select 2"}},
            }
        }
    }
    previous_metadata = cascade_app_client.ds._metadata_local
    try:
        cascade_app_client.ds._metadata_local = metadata
        response = cascade_app_client.get(
            "/fixtures",
            cookies={"ds_actor": cascade_app_client.actor_cookie({"id": "test"})},
        )
        # Tables
        assert ">123_starts_with_digits</a></h3>" in response.text
        assert ">Table With Space In Name</a> 🔒</h3>" in response.text
        # Queries
        assert ">from_async_hook</a> 🔒</li>" in response.text
        assert ">query_two</a></li>" in response.text
        # Views
        assert ">paginated_view</a> 🔒</li>" in response.text
        assert ">simple_view</a></li>" in response.text
    finally:
        cascade_app_client.ds._metadata_local = previous_metadata
