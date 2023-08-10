from .utils import cookie_was_deleted
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qs,expected",
    [
        ("add_msg=added-message", [["added-message", 1]]),
        ("add_msg=added-warning&type=WARNING", [["added-warning", 2]]),
        ("add_msg=added-error&type=ERROR", [["added-error", 3]]),
    ],
)
async def test_add_message_sets_cookie(ds_client, qs, expected):
    response = await ds_client.get(f"/fixtures.message?sql=select+1&{qs}")
    signed = response.cookies["ds_messages"]
    decoded = ds_client.ds.unsign(signed, "messages")
    assert expected == decoded


@pytest.mark.asyncio
async def test_messages_are_displayed_and_cleared(ds_client):
    # First set the message cookie
    set_msg_response = await ds_client.get(
        "/fixtures.message?sql=select+1&add_msg=xmessagex"
    )
    # Now access a page that displays messages
    response = await ds_client.get("/", cookies=set_msg_response.cookies)
    # Messages should be in that HTML
    assert "xmessagex" in response.text
    # Cookie should have been set that clears messages
    assert cookie_was_deleted(response, "ds_messages")
