import collections
from datasette.app import Datasette
from datasette.cli import cli
from datasette.default_permissions import restrictions_allow_action
from .fixtures import app_client, assert_permissions_checked, make_app_client
from click.testing import CliRunner
from bs4 import BeautifulSoup as Soup
import copy
import json
from pprint import pprint
import pytest_asyncio
import pytest
import re
import time
import urllib


@pytest.fixture(scope="module")
def padlock_client():
    with make_app_client(
        config={
            "databases": {
                "fixtures": {
                    "queries": {"two": {"sql": "select 1 + 1"}},
                }
            }
        }
    ) as client:
        yield client


@pytest_asyncio.fixture
async def perms_ds():
    ds = Datasette()
    await ds.invoke_startup()
    one = ds.add_memory_database("perms_ds_one")
    two = ds.add_memory_database("perms_ds_two")
    await one.execute_write("create table if not exists t1 (id integer primary key)")
    await one.execute_write("insert or ignore into t1 (id) values (1)")
    await one.execute_write("create view if not exists v1 as select * from t1")
    await one.execute_write("create table if not exists t2 (id integer primary key)")
    await two.execute_write("create table if not exists t1 (id integer primary key)")
    # Trigger catalog refresh so allowed_resources() can be called
    await ds.client.get("/")
    return ds


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
        "/-/api",
        "/fixtures/compound_three_primary_keys",
        "/fixtures/compound_three_primary_keys/a,a,a",
        pytest.param(
            "/fixtures/two",
            marks=pytest.mark.xfail(
                reason="view-query not yet migrated to new permission system"
            ),
        ),  # Query
    ),
)
def test_view_padlock(allow, expected_anon, expected_auth, path, padlock_client):
    padlock_client.ds.config["allow"] = allow
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
    del padlock_client.ds.config["allow"]


@pytest.mark.parametrize(
    "allow,expected_anon,expected_auth",
    [
        (None, 200, 200),
        ({}, 403, 403),
        ({"id": "root"}, 403, 200),
    ],
)
@pytest.mark.parametrize("use_metadata", (True, False))
def test_view_database(allow, expected_anon, expected_auth, use_metadata):
    key = "metadata" if use_metadata else "config"
    kwargs = {key: {"databases": {"fixtures": {"allow": allow}}}}
    with make_app_client(**kwargs) as client:
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
        config={"databases": {"fixtures": {"allow": {"id": "root"}}}},
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
        config={
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
@pytest.mark.parametrize("use_metadata", (True, False))
def test_view_table(allow, expected_anon, expected_auth, use_metadata):
    key = "metadata" if use_metadata else "config"
    kwargs = {
        key: {
            "databases": {
                "fixtures": {
                    "tables": {"compound_three_primary_keys": {"allow": allow}}
                }
            }
        }
    }
    with make_app_client(**kwargs) as client:
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
        config={
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
        config={
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
    "config",
    [
        {"allow_sql": {"id": "root"}},
        {"databases": {"fixtures": {"allow_sql": {"id": "root"}}}},
    ],
)
def test_execute_sql(config):
    schema_re = re.compile("const schema = ({.*?});", re.DOTALL)
    with make_app_client(config=config) as client:
        form_fragment = '<form class="sql core" action="/fixtures/-/query"'

        # Anonymous users - should not display the form:
        anon_html = client.get("/fixtures").text
        assert form_fragment not in anon_html
        # And const schema should be an empty object:
        assert "const schema = {};" in anon_html
        # This should 403:
        assert client.get("/fixtures/-/query?sql=select+1").status == 403
        # ?_where= not allowed on tables:
        assert client.get("/fixtures/facet_cities?_where=id=3").status == 403

        # But for logged in user all of these should work:
        cookies = {"ds_actor": client.actor_cookie({"id": "root"})}
        response_text = client.get("/fixtures", cookies=cookies).text
        # Extract the schema= portion of the JavaScript
        schema_json = schema_re.search(response_text).group(1)
        schema = json.loads(schema_json)
        assert set(schema["attraction_characteristic"]) == {"name", "pk"}
        assert schema["paginated_view"] == []
        assert form_fragment in response_text
        query_response = client.get("/fixtures/-/query?sql=select+1", cookies=cookies)
        assert query_response.status == 200
        schema2 = json.loads(schema_re.search(query_response.text).group(1))
        assert set(schema2["attraction_characteristic"]) == {"name", "pk"}
        assert (
            client.get("/fixtures/facet_cities?_where=id=3", cookies=cookies).status
            == 200
        )


def test_query_list_respects_view_query():
    with make_app_client(
        config={
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
            "/fixtures/-/query?sql=select+1",
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
        pytest.param(
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
    # Needs file-backed app_client for /fixtures.db
    app_client.ds._permission_checks.clear()
    response = app_client.get(path)
    assert response.status_code in (200, 403)
    assert_permissions_checked(app_client.ds, permissions)


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_", ("all", "exclude-yours", "only-yours"))
async def test_permissions_debug(ds_client, filter_):
    ds_client.ds._permission_checks.clear()
    assert (await ds_client.get("/-/permissions")).status_code == 403
    # With the cookie it should work (need to set root_enabled for root user)
    ds_client.ds.root_enabled = True
    cookie = ds_client.actor_cookie({"id": "root"})
    response = await ds_client.get(
        f"/-/permissions?filter={filter_}", cookies={"ds_actor": cookie}
    )
    assert response.status_code == 200
    # Should have a select box listing permissions
    for fragment in (
        '<select name="permission" id="permission">',
        '<option value="view-instance">view-instance</option>',
        '<option value="insert-row">insert-row</option>',
    ):
        assert fragment in response.text
    # Should show one failure and one success
    soup = Soup(response.text, "html.parser")
    table = soup.find("table", {"id": "permission-checks-table"})
    rows = table.find("tbody").find_all("tr")
    checks = []
    for row in rows:
        cells = row.find_all("td")
        result_cell = cells[5]
        if result_cell.select_one(".check-result-true"):
            result = True
        elif result_cell.select_one(".check-result-false"):
            result = False
        else:
            result = None
        actor_code = cells[4].find("code")
        actor = json.loads(actor_code.text) if actor_code else None
        checks.append(
            {
                "action": cells[1].text.strip(),
                "result": result,
                "actor": actor,
            }
        )
    expected_checks = [
        {
            "action": "permissions-debug",
            "result": True,
            "actor": {"id": "root"},
        },
        {
            "action": "view-instance",
            "result": True,
            "actor": {"id": "root"},
        },
        {"action": "debug-menu", "result": False, "actor": None},
        {
            "action": "view-instance",
            "result": True,
            "actor": None,
        },
        {
            "action": "permissions-debug",
            "result": False,
            "actor": None,
        },
        {
            "action": "view-instance",
            "result": True,
            "actor": None,
        },
    ]
    if filter_ == "only-yours":
        expected_checks = [
            check for check in expected_checks if check["actor"] is not None
        ]
    elif filter_ == "exclude-yours":
        expected_checks = [check for check in expected_checks if check["actor"] is None]
    assert checks == expected_checks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,allow,expected_fragment",
    [
        ('{"id":"root"}', "{}", "Result: deny"),
        ('{"id":"root"}', '{"id": "*"}', "Result: allow"),
        ('{"', '{"id": "*"}', "Actor JSON error"),
        ('{"id":"root"}', '"*"}', "Allow JSON error"),
    ],
)
async def test_allow_debug(ds_client, actor, allow, expected_fragment):
    response = await ds_client.get(
        "/-/allow-debug?" + urllib.parse.urlencode({"actor": actor, "allow": allow})
    )
    assert response.status_code == 200
    assert expected_fragment in response.text


@pytest.mark.parametrize(
    "allow,expected",
    [
        ({"id": "root"}, 403),
        ({"id": "root", "unauthenticated": True}, 200),
    ],
)
def test_allow_unauthenticated(allow, expected):
    with make_app_client(config={"allow": allow}) as client:
        assert expected == client.get("/").status


@pytest.fixture(scope="session")
def view_instance_client():
    with make_app_client(config={"allow": {}}) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/fixtures",
        "/fixtures/facetable",
        "/-/versions",
        "/-/plugins",
        "/-/settings",
        "/-/threads",
        "/-/databases",
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
    previous_config = cascade_app_client.ds.config
    updated_config = copy.deepcopy(previous_config)
    actor = {"id": "test"}
    if "download" in permissions:
        actor["can_download"] = 1
    try:
        # Set up the different allow blocks
        updated_config["allow"] = allow if "instance" in permissions else deny
        # Note: download permission also needs database access (via plugin granting both)
        # so we don't set a deny rule when download is in permissions
        updated_config["databases"]["fixtures"]["allow"] = (
            allow if ("database" in permissions or "download" in permissions) else deny
        )
        updated_config["databases"]["fixtures"]["tables"]["binary_data"] = {
            "allow": (allow if "table" in permissions else deny)
        }
        updated_config["databases"]["fixtures"]["queries"]["magic_parameters"][
            "allow"
        ] = (allow if "query" in permissions else deny)
        cascade_app_client.ds.config = updated_config
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
        cascade_app_client.ds.config = previous_config


def test_padlocks_on_database_page(cascade_app_client):
    config = {
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
    previous_config = cascade_app_client.ds.config
    try:
        cascade_app_client.ds.config = config
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
        cascade_app_client.ds.config = previous_config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,permission,resource_1,resource_2,expected_result",
    (
        # Without restrictions the defaults apply
        ({"id": "t"}, "view-instance", None, None, True),
        ({"id": "t"}, "view-database", "one", None, True),
        ({"id": "t"}, "view-table", "one", "t1", True),
        # If there is an _r block, everything gets denied unless explicitly allowed
        ({"id": "t", "_r": {}}, "view-instance", None, None, False),
        ({"id": "t", "_r": {}}, "view-database", "one", None, False),
        ({"id": "t", "_r": {}}, "view-table", "one", "t1", False),
        # Explicit allowing works at the "a" for all level:
        ({"id": "t", "_r": {"a": ["vi"]}}, "view-instance", None, None, True),
        ({"id": "t", "_r": {"a": ["vd"]}}, "view-database", "one", None, True),
        ({"id": "t", "_r": {"a": ["vt"]}}, "view-table", "one", "t1", True),
        # But not if it's the wrong permission
        ({"id": "t", "_r": {"a": ["vi"]}}, "view-database", "one", None, False),
        ({"id": "t", "_r": {"a": ["vd"]}}, "view-table", "one", "t1", False),
        # Works at the "d" for database level:
        ({"id": "t", "_r": {"d": {"one": ["vd"]}}}, "view-database", "one", None, True),
        (
            # view-database-download requires view-database too (also_requires)
            {"id": "t", "_r": {"d": {"one": ["vdd", "vd"]}}},
            "view-database-download",
            "one",
            None,
            True,
        ),
        (
            # execute-sql requires view-database too (also_requires)
            {"id": "t", "_r": {"d": {"one": ["es", "vd"]}}},
            "execute-sql",
            "one",
            None,
            True,
        ),
        # Works at the "r" for table level:
        (
            {"id": "t", "_r": {"r": {"one": {"t1": ["vt"]}}}},
            "view-table",
            "one",
            "t1",
            True,
        ),
        (
            {"id": "t", "_r": {"r": {"one": {"t1": ["vt"]}}}},
            "view-table",
            "one",
            "t2",
            False,
        ),
        # non-abbreviations should work too
        (
            {"id": "t", "_r": {"a": ["view-instance"]}},
            "view-instance",
            None,
            None,
            True,
        ),
        (
            {"id": "t", "_r": {"d": {"one": ["view-database"]}}},
            "view-database",
            "one",
            None,
            True,
        ),
        (
            {"id": "t", "_r": {"r": {"one": {"t1": ["view-table"]}}}},
            "view-table",
            "one",
            "t1",
            True,
        ),
        # view-database does NOT grant view-instance (no upward cascading)
        ({"id": "t", "_r": {"a": ["vd"]}}, "view-instance", None, None, False),
    ),
)
async def test_actor_restricted_permissions(
    perms_ds, actor, permission, resource_1, resource_2, expected_result
):
    perms_ds.pdb = True
    perms_ds.root_enabled = True  # Allow root actor to access /-/permissions
    cookies = {"ds_actor": perms_ds.sign({"a": {"id": "root"}}, "actor")}
    csrftoken = (await perms_ds.client.get("/-/permissions", cookies=cookies)).cookies[
        "ds_csrftoken"
    ]
    cookies["ds_csrftoken"] = csrftoken
    response = await perms_ds.client.post(
        "/-/permissions",
        data={
            "actor": json.dumps(actor),
            "permission": permission,
            "resource_1": resource_1,
            "resource_2": resource_2,
            "csrftoken": csrftoken,
        },
        cookies=cookies,
    )
    # Response mirrors /-/check JSON structure
    if resource_1 is None:
        expected_path = "/"
    elif resource_2 is None:
        expected_path = f"/{resource_1}"
    else:
        expected_path = f"/{resource_1}/{resource_2}"

    expected_resource = {
        "parent": resource_1,
        "child": resource_2,
        "path": expected_path,
    }
    expected = {
        "action": permission,
        "allowed": expected_result,
        "resource": expected_resource,
    }
    if actor.get("id"):
        expected["actor_id"] = actor["id"]
    assert response.json() == expected


PermConfigTestCase = collections.namedtuple(
    "PermConfigTestCase",
    "config,actor,action,resource,expected_result",
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config,actor,action,resource,expected_result",
    (
        # Simple view-instance default=True example
        PermConfigTestCase(
            config={},
            actor=None,
            action="view-instance",
            resource=None,
            expected_result=True,
        ),
        # debug-menu on root
        PermConfigTestCase(
            config={"permissions": {"debug-menu": {"id": "user"}}},
            actor={"id": "user"},
            action="debug-menu",
            resource=None,
            expected_result=True,
        ),
        # debug-menu on root, wrong actor
        PermConfigTestCase(
            config={"permissions": {"debug-menu": {"id": "user"}}},
            actor={"id": "user2"},
            action="debug-menu",
            resource=None,
            expected_result=False,
        ),
        # create-table on root
        PermConfigTestCase(
            config={"permissions": {"create-table": {"id": "user"}}},
            actor={"id": "user"},
            action="create-table",
            resource=None,
            expected_result=True,
        ),
        # create-table on database - no resource specified
        PermConfigTestCase(
            config={
                "databases": {
                    "perms_ds_one": {"permissions": {"create-table": {"id": "user"}}}
                }
            },
            actor={"id": "user"},
            action="create-table",
            resource=None,
            expected_result=False,
        ),
        # create-table on database
        PermConfigTestCase(
            config={
                "databases": {
                    "perms_ds_one": {"permissions": {"create-table": {"id": "user"}}}
                }
            },
            actor={"id": "user"},
            action="create-table",
            resource="perms_ds_one",
            expected_result=True,
        ),
        # insert-row on root, wrong actor
        PermConfigTestCase(
            config={"permissions": {"insert-row": {"id": "user"}}},
            actor={"id": "user2"},
            action="insert-row",
            resource=("perms_ds_one", "t1"),
            expected_result=False,
        ),
        # insert-row on root, right actor
        PermConfigTestCase(
            config={"permissions": {"insert-row": {"id": "user"}}},
            actor={"id": "user"},
            action="insert-row",
            resource=("perms_ds_one", "t1"),
            expected_result=True,
        ),
        # insert-row on database
        PermConfigTestCase(
            config={
                "databases": {
                    "perms_ds_one": {"permissions": {"insert-row": {"id": "user"}}}
                }
            },
            actor={"id": "user"},
            action="insert-row",
            resource="perms_ds_one",
            expected_result=True,
        ),
        # insert-row on table, wrong table
        PermConfigTestCase(
            config={
                "databases": {
                    "perms_ds_one": {
                        "tables": {
                            "t1": {"permissions": {"insert-row": {"id": "user"}}}
                        }
                    }
                }
            },
            actor={"id": "user"},
            action="insert-row",
            resource=("perms_ds_one", "t2"),
            expected_result=False,
        ),
        # insert-row on table, right table
        PermConfigTestCase(
            config={
                "databases": {
                    "perms_ds_one": {
                        "tables": {
                            "t1": {"permissions": {"insert-row": {"id": "user"}}}
                        }
                    }
                }
            },
            actor={"id": "user"},
            action="insert-row",
            resource=("perms_ds_one", "t1"),
            expected_result=True,
        ),
        # view-query on canned query, wrong actor
        PermConfigTestCase(
            config={
                "databases": {
                    "perms_ds_one": {
                        "queries": {
                            "q1": {
                                "sql": "select 1 + 1",
                                "permissions": {"view-query": {"id": "user"}},
                            }
                        }
                    }
                }
            },
            actor={"id": "user2"},
            action="view-query",
            resource=("perms_ds_one", "q1"),
            expected_result=False,
        ),
        # view-query on canned query, right actor
        PermConfigTestCase(
            config={
                "databases": {
                    "perms_ds_one": {
                        "queries": {
                            "q1": {
                                "sql": "select 1 + 1",
                                "permissions": {"view-query": {"id": "user"}},
                            }
                        }
                    }
                }
            },
            actor={"id": "user"},
            action="view-query",
            resource=("perms_ds_one", "q1"),
            expected_result=True,
        ),
    ),
)
async def test_permissions_in_config(
    perms_ds, config, actor, action, resource, expected_result
):
    previous_config = perms_ds.config
    updated_config = copy.deepcopy(previous_config)
    updated_config.update(config)
    perms_ds.config = updated_config
    try:
        # Convert old-style resource to Resource object
        from datasette.resources import DatabaseResource, TableResource

        resource_obj = None
        if resource:
            if isinstance(resource, str):
                resource_obj = DatabaseResource(database=resource)
            elif isinstance(resource, tuple) and len(resource) == 2:
                resource_obj = TableResource(database=resource[0], table=resource[1])

        result = await perms_ds.allowed(
            action=action, resource=resource_obj, actor=actor
        )
        if result != expected_result:
            pprint(perms_ds._permission_checks)
            assert result == expected_result
    finally:
        perms_ds.config = previous_config


@pytest.mark.asyncio
async def test_actor_endpoint_allows_any_token():
    ds = Datasette()
    token = ds.sign(
        {
            "a": "root",
            "token": "dstok",
            "t": int(time.time()),
            "_r": {"a": ["debug-menu"]},
        },
        namespace="token",
    )
    response = await ds.client.get(
        "/-/actor.json", headers={"Authorization": f"Bearer dstok_{token}"}
    )
    assert response.status_code == 200
    assert response.json()["actor"] == {
        "id": "root",
        "token": "dstok",
        "_r": {"a": ["debug-menu"]},
    }


@pytest.mark.serial
@pytest.mark.parametrize(
    "options,expected",
    (
        ([], {"id": "root", "token": "dstok"}),
        (
            ["--all", "debug-menu"],
            {"_r": {"a": ["dm"]}, "id": "root", "token": "dstok"},
        ),
        (
            ["-a", "debug-menu", "--all", "create-table"],
            {"_r": {"a": ["dm", "ct"]}, "id": "root", "token": "dstok"},
        ),
        (
            ["-r", "db1", "t1", "insert-row"],
            {"_r": {"r": {"db1": {"t1": ["ir"]}}}, "id": "root", "token": "dstok"},
        ),
        (
            ["-d", "db1", "create-table"],
            {"_r": {"d": {"db1": ["ct"]}}, "id": "root", "token": "dstok"},
        ),
        # And one with all of them multiple times using all the names
        (
            [
                "-a",
                "debug-menu",
                "--all",
                "create-table",
                "-r",
                "db1",
                "t1",
                "insert-row",
                "--resource",
                "db1",
                "t2",
                "update-row",
                "-d",
                "db1",
                "create-table",
                "--database",
                "db2",
                "drop-table",
            ],
            {
                "_r": {
                    "a": ["dm", "ct"],
                    "d": {"db1": ["ct"], "db2": ["dt"]},
                    "r": {"db1": {"t1": ["ir"], "t2": ["ur"]}},
                },
                "id": "root",
                "token": "dstok",
            },
        ),
    ),
)
def test_cli_create_token(options, expected):
    runner = CliRunner()
    result1 = runner.invoke(
        cli,
        [
            "create-token",
            "--secret",
            "sekrit",
            "root",
        ]
        + options,
    )
    token = result1.output.strip()
    result2 = runner.invoke(
        cli,
        [
            "serve",
            "--secret",
            "sekrit",
            "--get",
            "/-/actor.json",
            "--token",
            token,
        ],
    )
    assert 0 == result2.exit_code, result2.output
    assert json.loads(result2.output) == {"actor": expected}


_visible_tables_re = re.compile(r">\/((\w+)\/(\w+))\.json<\/a> - Get rows for")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "is_logged_in,config,expected_visible_tables",
    (
        # Unprotected instance logged out user sees everything:
        (
            False,
            None,
            ["perms_ds_one/t1", "perms_ds_one/t2", "perms_ds_two/t1"],
        ),
        # Fully protected instance logged out user sees nothing
        (False, {"allow": {"id": "user"}}, None),
        # User with visibility of just perms_ds_one sees both tables there
        (
            True,
            {
                "databases": {
                    "perms_ds_one": {"allow": {"id": "user"}},
                    "perms_ds_two": {"allow": False},
                }
            },
            ["perms_ds_one/t1", "perms_ds_one/t2"],
        ),
        # User with visibility of only table perms_ds_one/t1 sees just that one
        (
            True,
            {
                "databases": {
                    "perms_ds_one": {
                        "allow": {"id": "user"},
                        "tables": {"t2": {"allow": False}},
                    },
                    "perms_ds_two": {"allow": False},
                }
            },
            ["perms_ds_one/t1"],
        ),
    ),
)
async def test_api_explorer_visibility(
    perms_ds, is_logged_in, config, expected_visible_tables
):
    try:
        prev_config = perms_ds.config
        perms_ds.config = config or {}
        cookies = {}
        if is_logged_in:
            cookies = {"ds_actor": perms_ds.client.actor_cookie({"id": "user"})}
        response = await perms_ds.client.get("/-/api", cookies=cookies)
        if expected_visible_tables:
            assert response.status_code == 200
            # Search HTML for stuff matching:
            # '>/perms_ds_one/t2.json</a> - Get rows for'
            visible_tables = [
                match[0] for match in _visible_tables_re.findall(response.text)
            ]
            assert visible_tables == expected_visible_tables
        else:
            assert response.status_code == 403
    finally:
        perms_ds.config = prev_config


@pytest.mark.asyncio
async def test_view_table_token_cannot_gain_access_without_base_permission(perms_ds):
    # Only allow a different actor to view this table
    previous_config = perms_ds.config
    perms_ds.config = {
        "databases": {
            "perms_ds_two": {
                # Only someone-else can see anything in this database
                "allow": {"id": "someone-else"},
            }
        }
    }
    try:
        actor = {
            "id": "restricted-token",
            "token": "dstok",
            # Restricted token claims access to perms_ds_two/t1 only
            "_r": {"r": {"perms_ds_two": {"t1": ["vt"]}}},
        }
        cookies = {"ds_actor": perms_ds.client.actor_cookie(actor)}
        response = await perms_ds.client.get("/perms_ds_two/t1.json", cookies=cookies)
        assert response.status_code == 403
    finally:
        perms_ds.config = previous_config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "restrictions,verb,path,body,expected_status",
    (
        # No restrictions
        (None, "get", "/.json", None, 200),
        (None, "get", "/perms_ds_one.json", None, 200),
        (None, "get", "/perms_ds_one/t1.json", None, 200),
        (None, "get", "/perms_ds_one/t1/1.json", None, 200),
        (None, "get", "/perms_ds_one/v1.json", None, 200),
        # Restricted to just view-instance
        ({"a": ["vi"]}, "get", "/.json", None, 200),
        ({"a": ["vi"]}, "get", "/perms_ds_one.json", None, 403),
        ({"a": ["vi"]}, "get", "/perms_ds_one/t1.json", None, 403),
        ({"a": ["vi"]}, "get", "/perms_ds_one/t1/1.json", None, 403),
        ({"a": ["vi"]}, "get", "/perms_ds_one/v1.json", None, 403),
        # Restricted to just view-database
        (
            {"a": ["vd"]},
            "get",
            "/.json",
            None,
            403,
        ),  # Cannot see instance (no upward cascading)
        ({"a": ["vd"]}, "get", "/perms_ds_one.json", None, 200),
        ({"a": ["vd"]}, "get", "/perms_ds_one/t1.json", None, 403),
        ({"a": ["vd"]}, "get", "/perms_ds_one/t1/1.json", None, 403),
        ({"a": ["vd"]}, "get", "/perms_ds_one/v1.json", None, 403),
        # Restricted to just view-table for specific database
        (
            {"d": {"perms_ds_one": ["vt"]}},
            "get",
            "/.json",
            None,
            403,
        ),  # Cannot see instance (no upward cascading)
        (
            {"d": {"perms_ds_one": ["vt"]}},
            "get",
            "/perms_ds_one.json",
            None,
            403,
        ),  # Cannot see database page (no upward cascading)
        (
            {"d": {"perms_ds_one": ["vt"]}},
            "get",
            "/perms_ds_two.json",
            None,
            403,
        ),  # But not this one
        (
            # Can see the table
            {"d": {"perms_ds_one": ["vt"]}},
            "get",
            "/perms_ds_one/t1.json",
            None,
            200,
        ),
        (
            # And the view
            {"d": {"perms_ds_one": ["vt"]}},
            "get",
            "/perms_ds_one/v1.json",
            None,
            200,
        ),
        # view-table access to a specific table
        (
            {"r": {"perms_ds_one": {"t1": ["vt"]}}},
            "get",
            "/.json",
            None,
            403,
        ),  # Cannot see instance (no upward cascading)
        (
            {"r": {"perms_ds_one": {"t1": ["vt"]}}},
            "get",
            "/perms_ds_one.json",
            None,
            403,
        ),  # Cannot see database page (no upward cascading)
        (
            {"r": {"perms_ds_one": {"t1": ["vt"]}}},
            "get",
            "/perms_ds_one/t1.json",
            None,
            200,
        ),
        # But cannot see the other table
        (
            {"r": {"perms_ds_one": {"t1": ["vt"]}}},
            "get",
            "/perms_ds_one/t2.json",
            None,
            403,
        ),
        # Or the view
        (
            {"r": {"perms_ds_one": {"t1": ["vt"]}}},
            "get",
            "/perms_ds_one/v1.json",
            None,
            403,
        ),
    ),
)
async def test_actor_restrictions(
    perms_ds, restrictions, verb, path, body, expected_status
):
    actor = {"id": "user"}
    if restrictions:
        actor["_r"] = restrictions
    method = getattr(perms_ds.client, verb)
    kwargs = {"cookies": {"ds_actor": perms_ds.client.actor_cookie(actor)}}
    if body:
        kwargs["json"] = body
    perms_ds._permission_checks.clear()
    response = await method(path, **kwargs)
    assert response.status_code == expected_status, json.dumps(
        {
            "verb": verb,
            "path": path,
            "body": body,
            "restrictions": restrictions,
            "expected_status": expected_status,
            "response_status": response.status_code,
            "checks": [
                {
                    "action": check.action,
                    "parent": check.parent,
                    "child": check.child,
                    "result": check.result,
                }
                for check in perms_ds._permission_checks
            ],
        },
        indent=2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "restrictions,action,resource,expected",
    (
        # Exact match: view-instance restriction allows view-instance action
        ({"a": ["view-instance"]}, "view-instance", None, True),
        # No implication: view-table does NOT imply view-instance
        ({"a": ["view-table"]}, "view-instance", None, False),
        ({"a": ["view-database"]}, "view-instance", None, False),
        # update-row does not imply view-instance
        ({"a": ["update-row"]}, "view-instance", None, False),
        # view-table on a resource does NOT imply view-instance
        ({"r": {"db1": {"t1": ["view-table"]}}}, "view-instance", None, False),
        # execute-sql on a database does NOT imply view-instance or view-database
        ({"d": {"db1": ["es"]}}, "view-instance", None, False),
        ({"d": {"db1": ["es"]}}, "view-database", "db1", False),
        ({"d": {"db1": ["es"]}}, "view-database", "db2", False),
        # But execute-sql abbreviation DOES allow execute-sql action on that database
        ({"d": {"db1": ["es"]}}, "execute-sql", "db1", True),
        # update-row on a resource does not imply view-instance
        ({"r": {"db1": {"t1": ["update-row"]}}}, "view-instance", None, False),
        # view-database on a database does NOT imply view-instance
        ({"d": {"db1": ["view-database"]}}, "view-instance", None, False),
        # But it DOES allow view-database on that specific database
        ({"d": {"db1": ["view-database"]}}, "view-database", "db1", True),
        # Having view-table on "a" allows access to any specific table
        ({"a": ["view-table"]}, "view-table", ("dbname", "tablename"), True),
        # Having view-table on a database allows access to tables in that database
        (
            {"d": {"dbname": ["view-table"]}},
            "view-table",
            ("dbname", "tablename"),
            True,
        ),
        # But not if it's allowed on a different database
        (
            {"d": {"dbname": ["view-table"]}},
            "view-table",
            ("dbname2", "tablename"),
            False,
        ),
    ),
)
async def test_restrictions_allow_action(restrictions, action, resource, expected):
    ds = Datasette()
    await ds.invoke_startup()
    actual = restrictions_allow_action(ds, restrictions, action, resource)
    assert actual == expected


@pytest.mark.asyncio
async def test_actor_restrictions_filters_allowed_resources(perms_ds):
    """Test that allowed_resources() respects actor restrictions - issue #2534"""

    # Actor restricted to just perms_ds_one/t1
    actor = {"id": "user", "_r": {"r": {"perms_ds_one": {"t1": ["vt"]}}}}

    # Should only return t1
    page = await perms_ds.allowed_resources("view-table", actor)
    assert len(page.resources) == 1
    assert page.resources[0].parent == "perms_ds_one"
    assert page.resources[0].child == "t1"

    # Database listing should be empty (no view-database permission)
    db_page = await perms_ds.allowed_resources("view-database", actor)
    assert len(db_page.resources) == 0


@pytest.mark.asyncio
async def test_actor_restrictions_do_not_expand_allowed_resources(perms_ds):
    """Restrictions cannot grant access not already allowed to the actor."""

    previous_config = perms_ds.config
    perms_ds.config = {
        "databases": {
            "perms_ds_one": {
                "allow": {"id": "someone-else"},
            }
        }
    }
    try:
        actor = {"id": "user", "_r": {"r": {"perms_ds_one": {"t1": ["vt"]}}}}

        # Base actor is not allowed to see t1, so restrictions should not change that
        page = await perms_ds.allowed_resources("view-table", actor)
        assert len(page.resources) == 0

        # And explicit permission checks should still deny
        response = await perms_ds.client.get(
            "/perms_ds_one/t1.json",
            cookies={"ds_actor": perms_ds.client.actor_cookie(actor)},
        )
        assert response.status_code == 403
    finally:
        perms_ds.config = previous_config


@pytest.mark.asyncio
async def test_actor_restrictions_database_level(perms_ds):
    """Test database-level restrictions allow all tables in database - issue #2534"""

    actor = {"id": "user", "_r": {"d": {"perms_ds_one": ["vt"]}}}

    page = await perms_ds.allowed_resources("view-table", actor, parent="perms_ds_one")

    # Should return all tables in perms_ds_one
    table_names = {r.child for r in page.resources}
    assert "t1" in table_names
    assert "t2" in table_names
    assert "v1" in table_names  # views too


@pytest.mark.asyncio
async def test_actor_restrictions_global_level(perms_ds):
    """Test global-level restrictions allow all resources - issue #2534"""

    actor = {"id": "user", "_r": {"a": ["vt"]}}

    page = await perms_ds.allowed_resources("view-table", actor)

    # Should return all tables in all databases
    assert len(page.resources) > 0
    dbs = {r.parent for r in page.resources}
    assert "perms_ds_one" in dbs
    assert "perms_ds_two" in dbs


@pytest.mark.asyncio
async def test_restrictions_gate_before_config(perms_ds):
    """Test that restrictions act as gating filter before config permissions - issue #2534"""
    from datasette.resources import TableResource

    # Actor restricted to just t1 (not t2)
    actor = {"id": "user", "_r": {"r": {"perms_ds_one": {"t1": ["vt"]}}}}

    # Config doesn't matter - restrictions gate what's checked
    # t2 is not in restriction allowlist, so should be DENIED
    result = await perms_ds.allowed(
        action="view-table",
        resource=TableResource("perms_ds_one", "t2"),
        actor=actor,
    )
    assert result is False

    # t1 is in restrictions AND passes normal permission check - should be ALLOWED
    result = await perms_ds.allowed(
        action="view-table",
        resource=TableResource("perms_ds_one", "t1"),
        actor=actor,
    )
    assert result is True


@pytest.mark.asyncio
async def test_actor_restrictions_json_endpoints_show_filtered_listings(perms_ds):
    """Test that /.json and /db.json show correct filtered listings - issue #2534"""

    actor = {"id": "user", "_r": {"r": {"perms_ds_one": {"t1": ["vt"]}}}}
    cookies = {"ds_actor": perms_ds.client.actor_cookie(actor)}

    # /.json should be 403 (no view-instance permission)
    response = await perms_ds.client.get("/.json", cookies=cookies)
    assert response.status_code == 403

    # /perms_ds_one.json should be 403 (no view-database permission)
    response = await perms_ds.client.get("/perms_ds_one.json", cookies=cookies)
    assert response.status_code == 403

    # /perms_ds_one/t1.json should be 200
    response = await perms_ds.client.get("/perms_ds_one/t1.json", cookies=cookies)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_actor_restrictions_view_instance_only(perms_ds):
    """Test actor restricted to view-instance only - issue #2534"""

    actor = {"id": "user", "_r": {"a": ["vi"]}}
    cookies = {"ds_actor": perms_ds.client.actor_cookie(actor)}

    # /.json should be 200 (has view-instance permission)
    response = await perms_ds.client.get("/.json", cookies=cookies)
    assert response.status_code == 200

    # But no databases should be visible (no view-database permission)
    data = response.json()
    # The instance is visible but databases list should be empty or minimal
    # Actually, let's check via allowed_resources
    page = await perms_ds.allowed_resources("view-database", actor)
    assert len(page.resources) == 0


@pytest.mark.asyncio
async def test_actor_restrictions_empty_allowlist(perms_ds):
    """Test actor with empty restrictions allowlist denies everything - issue #2534"""

    actor = {"id": "user", "_r": {}}

    # No actions in allowlist, so everything should be denied
    page1 = await perms_ds.allowed_resources("view-table", actor)
    assert len(page1.resources) == 0

    page2 = await perms_ds.allowed_resources("view-database", actor)
    assert len(page2.resources) == 0

    result = await perms_ds.allowed(action="view-instance", actor=actor)
    assert result is False


@pytest.mark.asyncio
async def test_actor_restrictions_cannot_be_overridden_by_config():
    """Test that config permissions cannot override actor restrictions - issue #2534"""
    from datasette.app import Datasette
    from datasette.resources import TableResource

    # Create datasette with config that allows user to access both t1 AND t2
    config = {
        "databases": {
            "test_db": {
                "tables": {
                    "t1": {"allow": {"id": "user"}},
                    "t2": {"allow": {"id": "user"}},
                }
            }
        }
    }

    ds = Datasette(config=config)
    await ds.invoke_startup()
    db = ds.add_memory_database("test_db")
    await db.execute_write("create table t1 (id integer primary key)")
    await db.execute_write("create table t2 (id integer primary key)")

    # Actor restricted to ONLY t1 (not t2)
    # Even though config allows t2, restrictions should deny it
    actor = {"id": "user", "_r": {"r": {"test_db": {"t1": ["vt"]}}}}

    # t1 should be allowed (in restrictions AND config allows)
    result = await ds.allowed(
        action="view-table", resource=TableResource("test_db", "t1"), actor=actor
    )
    assert result is True, "t1 should be allowed - in restriction allowlist"

    # t2 should be DENIED (not in restrictions, even though config allows)
    result = await ds.allowed(
        action="view-table", resource=TableResource("test_db", "t2"), actor=actor
    )
    assert (
        result is False
    ), "t2 should be denied - NOT in restriction allowlist, config cannot override"


@pytest.mark.asyncio
async def test_actor_restrictions_with_database_level_config(perms_ds):
    """Test database-level restrictions with table-level config - issue #2534"""
    from datasette.resources import TableResource

    # Config allows specific tables only
    perms_ds._config = {
        "databases": {
            "perms_ds_one": {
                "tables": {
                    "t1": {"allow": {"id": "user"}},
                    "t2": {"allow": {"id": "user"}},
                }
            }
        }
    }

    # Actor has database-level restriction (all tables in perms_ds_one)
    # Should only access tables that pass BOTH restrictions AND config
    actor = {"id": "user", "_r": {"d": {"perms_ds_one": ["vt"]}}}

    # t1 - in restrictions (all tables) AND config allows
    result = await perms_ds.allowed(
        action="view-table", resource=TableResource("perms_ds_one", "t1"), actor=actor
    )
    assert result is True

    # t2 - in restrictions (all tables) AND config allows
    result = await perms_ds.allowed(
        action="view-table", resource=TableResource("perms_ds_one", "t2"), actor=actor
    )
    assert result is True

    # v1 (view) - in restrictions (all tables) AND config doesn't mention it
    # Since actor has database-level restriction allowing all tables, v1 is allowed
    # Config is additive, not restrictive - it doesn't create implicit denies
    result = await perms_ds.allowed(
        action="view-table", resource=TableResource("perms_ds_one", "v1"), actor=actor
    )
    assert result is True, "v1 should be allowed - actor has db-level restriction"

    # Clean up
    perms_ds._config = None


@pytest.mark.asyncio
async def test_actor_restrictions_parent_deny_blocks_config_child_allow(perms_ds):
    """
    Test that table-level restrictions add parent-level deny to block
    other tables in the same database, even if config allows them
    """
    from datasette.resources import TableResource

    # Config allows both t1 and t2
    perms_ds._config = {
        "databases": {
            "perms_ds_one": {
                "tables": {
                    "t1": {"allow": {"id": "user"}},
                    "t2": {"allow": {"id": "user"}},
                }
            }
        }
    }

    # Restriction allows ONLY t1 in perms_ds_one
    # This should add:
    # - parent-level DENY for perms_ds_one (to block other tables)
    # - child-level ALLOW for t1
    actor = {"id": "user", "_r": {"r": {"perms_ds_one": {"t1": ["vt"]}}}}

    # t1 should work (child-level allow beats parent-level deny)
    result = await perms_ds.allowed(
        action="view-table", resource=TableResource("perms_ds_one", "t1"), actor=actor
    )
    assert result is True

    # t2 should be DENIED by parent-level deny from restrictions
    # even though config has child-level allow
    # Because restrictions should run first
    result = await perms_ds.allowed(
        action="view-table", resource=TableResource("perms_ds_one", "t2"), actor=actor
    )
    assert (
        result is False
    ), "t2 should be denied - restriction parent deny should beat config child allow"

    # Clean up
    perms_ds._config = None


@pytest.mark.asyncio
async def test_permission_check_view_requires_debug_permission():
    """Test that /-/check requires permissions-debug permission"""
    # Anonymous user should be denied
    ds = Datasette()
    response = await ds.client.get("/-/check.json?action=view-instance")
    assert response.status_code == 403
    assert "permissions-debug" in response.text

    # User without permissions-debug should be denied
    response = await ds.client.get(
        "/-/check.json?action=view-instance",
        cookies={"ds_actor": ds.sign({"id": "user"}, "actor")},
    )
    assert response.status_code == 403

    # Root user should have access (root has all permissions)
    ds_with_root = Datasette()
    ds_with_root.root_enabled = True
    root_token = ds_with_root.create_token("root")
    response = await ds_with_root.client.get(
        "/-/check.json?action=view-instance",
        headers={"Authorization": f"Bearer {root_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "view-instance"
    assert data["allowed"] is True
