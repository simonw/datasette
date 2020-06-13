from .fixtures import app_client
import baseconv
import pytest
import time


def test_auth_token(app_client):
    "The /-/auth-token endpoint sets the correct cookie"
    assert app_client.ds._root_token is not None
    path = "/-/auth-token?token={}".format(app_client.ds._root_token)
    response = app_client.get(path, allow_redirects=False,)
    assert 302 == response.status
    assert "/" == response.headers["Location"]
    assert {"a": {"id": "root"}} == app_client.ds.unsign(
        response.cookies["ds_actor"], "actor"
    )
    # Check that a second with same token fails
    assert app_client.ds._root_token is None
    assert 403 == app_client.get(path, allow_redirects=False,).status


def test_actor_cookie(app_client):
    "A valid actor cookie sets request.scope['actor']"
    cookie = app_client.actor_cookie({"id": "test"})
    response = app_client.get("/", cookies={"ds_actor": cookie})
    assert {"id": "test"} == app_client.ds._last_request.scope["actor"]


def test_actor_cookie_invalid(app_client):
    cookie = app_client.actor_cookie({"id": "test"})
    # Break the signature
    response = app_client.get("/", cookies={"ds_actor": cookie[:-1] + "."})
    assert None == app_client.ds._last_request.scope["actor"]
    # Break the cookie format
    cookie = app_client.ds.sign({"b": {"id": "test"}}, "actor")
    response = app_client.get("/", cookies={"ds_actor": cookie})
    assert None == app_client.ds._last_request.scope["actor"]


@pytest.mark.parametrize(
    "offset,expected", [((24 * 60 * 60), {"id": "test"}), (-(24 * 60 * 60), None),]
)
def test_actor_cookie_that_expires(app_client, offset, expected):
    expires_at = int(time.time()) + offset
    cookie = app_client.ds.sign(
        {"a": {"id": "test"}, "e": baseconv.base62.encode(expires_at)}, "actor"
    )
    response = app_client.get("/", cookies={"ds_actor": cookie})
    assert expected == app_client.ds._last_request.scope["actor"]
