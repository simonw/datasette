import pytest
from .fixtures import make_app_client


@pytest.fixture(scope="session")
def canned_write_client():
    for client in make_app_client(
        extra_databases={"data.db": "create table names (name text)"},
        metadata={
            "databases": {
                "data": {
                    "queries": {
                        "add_name": {
                            "sql": "insert into names (name) values (:name)",
                            "write": True,
                        },
                        "delete_name": {
                            "sql": "delete from names where rowid = :rowid",
                            "write": True,
                        },
                        "update_name": {
                            "sql": "update names set name = :name where rowid = :rowid",
                            "params": ["rowid", "name"],
                            "write": True,
                        },
                    }
                }
            }
        },
    ):
        yield client


def test_insert(canned_write_client):
    response = canned_write_client.post(
        "/data/add_name", {"name": "Hello"}, allow_redirects=False
    )
    assert 302 == response.status
    assert "/data/add_name" == response.headers["Location"]
    messages = canned_write_client.ds.unsign(
        response.cookies["ds_messages"], "messages"
    )
    assert [["Query executed, 1 row affected", 1]] == messages
