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


def test_logout(app_client):
    response = app_client.get(
        "/-/logout", cookies={"ds_actor": app_client.actor_cookie({"id": "test"})}
    )
    assert 200 == response.status
    assert "<p>You are logged in as <strong>test</strong></p>" in response.text
    # Actors without an id get full serialization
    response2 = app_client.get(
        "/-/logout", cookies={"ds_actor": app_client.actor_cookie({"name2": "bob"})}
    )
    assert 200 == response2.status
    assert (
        "<p>You are logged in as <strong>{&#39;name2&#39;: &#39;bob&#39;}</strong></p>"
        in response2.text
    )
    # If logged out you get a redirect to /
    response3 = app_client.get("/-/logout", allow_redirects=False)
    assert 302 == response3.status
    # A POST to that page should log the user out
    response4 = app_client.post(
        "/-/logout",
        csrftoken_from=True,
        cookies={"ds_actor": app_client.actor_cookie({"id": "test"})},
        allow_redirects=False,
    )
    assert "" == response4.cookies["ds_actor"]
    # Should also have set a message
    messages = app_client.ds.unsign(response4.cookies["ds_messages"], "messages")
    assert [["You are now logged out", 2]] == messages


@pytest.mark.parametrize("path", ["/", "/fixtures", "/fixtures/facetable"])
def test_logout_button_in_navigation(app_client, path):
    response = app_client.get(
        path, cookies={"ds_actor": app_client.actor_cookie({"id": "test"})}
    )
    anon_response = app_client.get(path)
    for fragment in (
        "<strong>test</strong> &middot;",
        '<form action="/-/logout" method="post">',
    ):
        assert fragment in response.text
        assert fragment not in anon_response.text


@pytest.mark.parametrize("path", ["/", "/fixtures", "/fixtures/facetable"])
def test_no_logout_button_in_navigation_if_no_ds_actor_cookie(app_client, path):
    response = app_client.get(path + "?_bot=1")
    assert "<strong>bot</strong>" in response.text
    assert "<strong>bot</strong> &middot;" not in response.text
    assert '<form action="/-/logout" method="post">' not in response.text
