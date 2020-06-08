from .fixtures import make_app_client
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
            auth_response = client.get(
                path, cookies={"ds_actor": client.ds.sign({"id": "root"}, "actor")},
            )
            assert expected_auth == auth_response.status


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
            auth_response = client.get(
                path, cookies={"ds_actor": client.ds.sign({"id": "root"}, "actor")},
            )
            assert expected_auth == auth_response.status


def test_database_list_respects_view_database():
    with make_app_client(
        metadata={"databases": {"fixtures": {"allow": {"id": "root"}}}},
        extra_databases={"data.db": "create table names (name text)"},
    ) as client:
        anon_response = client.get("/")
        assert '<a href="/data">data</a></h2>' in anon_response.text
        assert '<a href="/fixtures">fixtures</a>' not in anon_response.text
        auth_response = client.get(
            "/", cookies={"ds_actor": client.ds.sign({"id": "root"}, "actor")},
        )
        assert '<a href="/data">data</a></h2>' in auth_response.text
        assert '<a href="/fixtures">fixtures</a> 🔒</h2>' in auth_response.text


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
        auth_response = client.get(
            "/fixtures/compound_three_primary_keys",
            cookies={"ds_actor": client.ds.sign({"id": "root"}, "actor")},
        )
        assert expected_auth == auth_response.status


def test_table_list_respects_view_table():
    with make_app_client(
        metadata={
            "databases": {
                "fixtures": {
                    "tables": {"compound_three_primary_keys": {"allow": {"id": "root"}}}
                }
            }
        }
    ) as client:
        html_fragment = '<a href="/fixtures/compound_three_primary_keys">compound_three_primary_keys</a> 🔒'
        anon_response = client.get("/fixtures")
        assert html_fragment not in anon_response.text
        assert '"/fixtures/compound_three_primary_keys"' not in anon_response.text
        auth_response = client.get(
            "/fixtures", cookies={"ds_actor": client.ds.sign({"id": "root"}, "actor")}
        )
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
        auth_response = client.get(
            "/fixtures/q", cookies={"ds_actor": client.ds.sign({"id": "root"}, "actor")}
        )
        assert expected_auth == auth_response.status


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
            "/fixtures", cookies={"ds_actor": client.ds.sign({"id": "root"}, "actor")}
        )
        assert html_fragment in auth_response.text
