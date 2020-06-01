from .fixtures import app_client
from bs4 import BeautifulSoup as Soup


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


def test_permissions_debug(app_client):
    assert 403 == app_client.get("/-/permissions").status
    # With the cookie it should work
    cookie = app_client.ds.sign({"id": "root"}, "actor")
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
        {"action": "permissions-debug", "result": False, "used_default": True},
    ] == checks
