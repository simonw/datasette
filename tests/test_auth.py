from .fixtures import app_client


def test_auth_token(app_client):
    "The /-/auth-token endpoint sets the correct cookie"
    assert app_client.ds._root_token is not None
    path = "/-/auth-token?token={}".format(app_client.ds._root_token)
    response = app_client.get(path, allow_redirects=False,)
    assert 302 == response.status
    assert "/" == response.headers["Location"]
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.endswith("; Path=/")
    assert set_cookie.startswith("ds_actor=")
    cookie_value = set_cookie.split("ds_actor=")[1].split("; Path=/")[0]
    assert {"id": "root"} == app_client.ds.unsign(cookie_value, "actor")
    # Check that a second with same token fails
    assert app_client.ds._root_token is None
    assert 403 == app_client.get(path, allow_redirects=False,).status


def test_actor_cookie(app_client):
    "A valid actor cookie sets request.scope['actor']"
    cookie = app_client.ds.sign({"id": "test"}, "actor")
    response = app_client.get("/", cookies={"ds_actor": cookie})
    assert {"id": "test"} == app_client.ds._last_request.scope["actor"]
