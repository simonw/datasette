from bs4 import BeautifulSoup as Soup
from .fixtures import app_client
from .utils import cookie_was_deleted, last_event
from click.testing import CliRunner
from datasette.utils import baseconv
from datasette.cli import cli
import pytest
import time


@pytest.mark.asyncio
async def test_auth_token(ds_client):
    """The /-/auth-token endpoint sets the correct cookie"""
    assert ds_client.ds._root_token is not None
    path = f"/-/auth-token?token={ds_client.ds._root_token}"
    response = await ds_client.get(path)
    assert response.status_code == 302
    assert "/" == response.headers["Location"]
    assert {"a": {"id": "root"}} == ds_client.ds.unsign(
        response.cookies["ds_actor"], "actor"
    )
    # Should have recorded a login event
    event = last_event(ds_client.ds)
    assert event.name == "login"
    assert event.actor == {"id": "root"}
    # Check that a second with same token fails
    assert ds_client.ds._root_token is None
    assert (await ds_client.get(path)).status_code == 403


@pytest.mark.asyncio
async def test_actor_cookie(ds_client):
    """A valid actor cookie sets request.scope['actor']"""
    cookie = ds_client.actor_cookie({"id": "test"})
    await ds_client.get("/", cookies={"ds_actor": cookie})
    assert ds_client.ds._last_request.scope["actor"] == {"id": "test"}


@pytest.mark.asyncio
async def test_actor_cookie_invalid(ds_client):
    cookie = ds_client.actor_cookie({"id": "test"})
    # Break the signature
    await ds_client.get("/", cookies={"ds_actor": cookie[:-1] + "."})
    assert ds_client.ds._last_request.scope["actor"] is None
    # Break the cookie format
    cookie = ds_client.ds.sign({"b": {"id": "test"}}, "actor")
    await ds_client.get("/", cookies={"ds_actor": cookie})
    assert ds_client.ds._last_request.scope["actor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset,expected",
    [
        ((24 * 60 * 60), {"id": "test"}),
        (-(24 * 60 * 60), None),
    ],
)
async def test_actor_cookie_that_expires(ds_client, offset, expected):
    expires_at = int(time.time()) + offset
    cookie = ds_client.ds.sign(
        {"a": {"id": "test"}, "e": baseconv.base62.encode(expires_at)}, "actor"
    )
    await ds_client.get("/", cookies={"ds_actor": cookie})
    assert ds_client.ds._last_request.scope["actor"] == expected


def test_logout(app_client):
    # Keeping app_client for the moment because of csrftoken_from
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
    response3 = app_client.get("/-/logout")
    assert 302 == response3.status
    # A POST to that page should log the user out
    response4 = app_client.post(
        "/-/logout",
        csrftoken_from=True,
        cookies={"ds_actor": app_client.actor_cookie({"id": "test"})},
    )
    # Should have recorded a logout event
    event = last_event(app_client.ds)
    assert event.name == "logout"
    assert event.actor == {"id": "test"}
    # The ds_actor cookie should have been unset
    assert cookie_was_deleted(response4, "ds_actor")
    # Should also have set a message
    messages = app_client.ds.unsign(response4.cookies["ds_messages"], "messages")
    assert [["You are now logged out", 2]] == messages


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/fixtures", "/fixtures/facetable"])
async def test_logout_button_in_navigation(ds_client, path):
    response = await ds_client.get(
        path, cookies={"ds_actor": ds_client.actor_cookie({"id": "test"})}
    )
    anon_response = await ds_client.get(path)
    for fragment in (
        "<strong>test</strong>",
        '<form class="nav-menu-logout" action="/-/logout" method="post">',
    ):
        assert fragment in response.text
        assert fragment not in anon_response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/fixtures", "/fixtures/facetable"])
async def test_no_logout_button_in_navigation_if_no_ds_actor_cookie(ds_client, path):
    response = await ds_client.get(path + "?_bot=1")
    assert "<strong>bot</strong>" in response.text
    assert (
        '<form class="nav-menu-logout" action="/-/logout" method="post">'
        not in response.text
    )


@pytest.mark.parametrize(
    "post_data,errors,expected_duration,expected_r",
    (
        ({"expire_type": ""}, [], None, None),
        ({"expire_type": "x"}, ["Invalid expire duration"], None, None),
        ({"expire_type": "minutes"}, ["Invalid expire duration"], None, None),
        (
            {"expire_type": "minutes", "expire_duration": "x"},
            ["Invalid expire duration"],
            None,
            None,
        ),
        (
            {"expire_type": "minutes", "expire_duration": "-1"},
            ["Invalid expire duration"],
            None,
            None,
        ),
        (
            {"expire_type": "minutes", "expire_duration": "0"},
            ["Invalid expire duration"],
            None,
            None,
        ),
        ({"expire_type": "minutes", "expire_duration": "10"}, [], 600, None),
        ({"expire_type": "hours", "expire_duration": "10"}, [], 10 * 60 * 60, None),
        ({"expire_type": "days", "expire_duration": "3"}, [], 60 * 60 * 24 * 3, None),
        # Token restrictions
        ({"all:view-instance": "on"}, [], None, {"a": ["vi"]}),
        ({"database:fixtures:view-query": "on"}, [], None, {"d": {"fixtures": ["vq"]}}),
        (
            {"resource:fixtures:facetable:insert-row": "on"},
            [],
            None,
            {"r": {"fixtures": {"facetable": ["ir"]}}},
        ),
    ),
)
def test_auth_create_token(
    app_client, post_data, errors, expected_duration, expected_r
):
    assert app_client.get("/-/create-token").status == 403
    ds_actor = app_client.actor_cookie({"id": "test"})
    response = app_client.get("/-/create-token", cookies={"ds_actor": ds_actor})
    assert response.status == 200
    assert ">Create an API token<" in response.text
    # Confirm some aspects of expected set of checkboxes
    soup = Soup(response.text, "html.parser")
    checkbox_names = {el["name"] for el in soup.select('input[type="checkbox"]')}
    assert checkbox_names.issuperset(
        {
            "all:view-instance",
            "all:view-query",
            "database:fixtures:drop-table",
            "resource:fixtures:foreign_key_references:insert-row",
        }
    )
    # Now try actually creating one
    response2 = app_client.post(
        "/-/create-token",
        post_data,
        csrftoken_from=True,
        cookies={"ds_actor": ds_actor},
    )
    assert response2.status == 200
    if errors:
        for error in errors:
            assert '<p class="message-error">{}</p>'.format(error) in response2.text
    else:
        # Check create-token event
        event = last_event(app_client.ds)
        assert event.name == "create-token"
        assert event.expires_after == expected_duration
        assert isinstance(event.restrict_all, list)
        assert isinstance(event.restrict_database, dict)
        assert isinstance(event.restrict_resource, dict)
        # Extract token from page
        token = response2.text.split('value="dstok_')[1].split('"')[0]
        details = app_client.ds.unsign(token, "token")
        if expected_r:
            r = details.pop("_r")
            assert r == expected_r
        assert details.keys() == {"a", "t", "d"} or details.keys() == {"a", "t"}
        assert details["a"] == "test"
        if expected_duration is None:
            assert "d" not in details
        else:
            assert details["d"] == expected_duration
        # And test that token
        response3 = app_client.get(
            "/-/actor.json",
            headers={"Authorization": "Bearer {}".format("dstok_{}".format(token))},
        )
        assert response3.status == 200
        assert response3.json["actor"]["id"] == "test"


@pytest.mark.asyncio
async def test_auth_create_token_not_allowed_for_tokens(ds_client):
    ds_tok = ds_client.ds.sign({"a": "test", "token": "dstok"}, "token")
    response = await ds_client.get(
        "/-/create-token",
        headers={"Authorization": "Bearer dstok_{}".format(ds_tok)},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_auth_create_token_not_allowed_if_allow_signed_tokens_off(ds_client):
    ds_client.ds._settings["allow_signed_tokens"] = False
    try:
        ds_actor = ds_client.actor_cookie({"id": "test"})
        response = await ds_client.get(
            "/-/create-token", cookies={"ds_actor": ds_actor}
        )
        assert response.status_code == 403
    finally:
        ds_client.ds._settings["allow_signed_tokens"] = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,should_work",
    (
        ("allow_signed_tokens_off", False),
        ("no_token", False),
        ("no_timestamp", False),
        ("invalid_token", False),
        ("expired_token", False),
        ("valid_unlimited_token", True),
        ("valid_expiring_token", True),
    ),
)
async def test_auth_with_dstok_token(ds_client, scenario, should_work):
    token = None
    _time = int(time.time())
    if scenario in ("valid_unlimited_token", "allow_signed_tokens_off"):
        token = ds_client.ds.sign({"a": "test", "t": _time}, "token")
    elif scenario == "valid_expiring_token":
        token = ds_client.ds.sign({"a": "test", "t": _time - 50, "d": 1000}, "token")
    elif scenario == "expired_token":
        token = ds_client.ds.sign({"a": "test", "t": _time - 2000, "d": 1000}, "token")
    elif scenario == "no_timestamp":
        token = ds_client.ds.sign({"a": "test"}, "token")
    elif scenario == "invalid_token":
        token = "invalid"
    if token:
        token = "dstok_{}".format(token)
    if scenario == "allow_signed_tokens_off":
        ds_client.ds._settings["allow_signed_tokens"] = False
    headers = {}
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    response = await ds_client.get("/-/actor.json", headers=headers)
    try:
        if should_work:
            data = response.json()
            assert data.keys() == {"actor"}
            actor = data["actor"]
            expected_keys = {"id", "token"}
            if scenario != "valid_unlimited_token":
                expected_keys.add("token_expires")
            assert actor.keys() == expected_keys
            assert actor["id"] == "test"
            assert actor["token"] == "dstok"
            if scenario != "valid_unlimited_token":
                assert isinstance(actor["token_expires"], int)
        else:
            assert response.json() == {"actor": None}
    finally:
        ds_client.ds._settings["allow_signed_tokens"] = True


@pytest.mark.parametrize("expires", (None, 1000, -1000))
def test_cli_create_token(app_client, expires):
    secret = app_client.ds._secret
    runner = CliRunner(mix_stderr=False)
    args = ["create-token", "--secret", secret, "test"]
    if expires:
        args += ["--expires-after", str(expires)]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0
    token = result.output.strip()
    assert token.startswith("dstok_")
    details = app_client.ds.unsign(token[len("dstok_") :], "token")
    expected_keys = {"a", "t"}
    if expires:
        expected_keys.add("d")
    assert details.keys() == expected_keys
    assert details["a"] == "test"
    response = app_client.get(
        "/-/actor.json", headers={"Authorization": "Bearer {}".format(token)}
    )
    if expires is None or expires > 0:
        expected_actor = {
            "id": "test",
            "token": "dstok",
        }
        if expires and expires > 0:
            expected_actor["token_expires"] = details["t"] + expires
        assert response.json == {"actor": expected_actor}
    else:
        expected_actor = None
    assert response.json == {"actor": expected_actor}
