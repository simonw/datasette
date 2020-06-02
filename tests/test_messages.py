from .fixtures import app_client
import pytest


@pytest.mark.parametrize(
    "qs,expected",
    [
        ("add_msg=added-message", [["added-message", 1]]),
        ("add_msg=added-warning&type=WARNING", [["added-warning", 2]]),
        ("add_msg=added-error&type=ERROR", [["added-error", 3]]),
    ],
)
def test_add_message_sets_cookie(app_client, qs, expected):
    response = app_client.get("/fixtures.message?{}".format(qs))
    signed = response.cookies["ds_messages"]
    decoded = app_client.ds.unsign(signed, "messages")
    assert expected == decoded


def test_messages_are_displayed_and_cleared(app_client):
    # First set the message cookie
    set_msg_response = app_client.get("/fixtures.message?add_msg=xmessagex")
    # Now access a page that displays messages
    response = app_client.get("/", cookies=set_msg_response.cookies)
    # Messages should be in that HTML
    assert "xmessagex" in response.text
    # Cookie should have been set that clears messages
    assert "" == response.cookies["ds_messages"]
