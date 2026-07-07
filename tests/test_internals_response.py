from datasette.utils.asgi import Response
import json
import pytest


def test_response_html():
    response = Response.html("Hello from HTML")
    assert 200 == response.status
    assert "Hello from HTML" == response.body
    assert "text/html; charset=utf-8" == response.content_type


def test_response_text():
    response = Response.text("Hello from text")
    assert 200 == response.status
    assert "Hello from text" == response.body
    assert "text/plain; charset=utf-8" == response.content_type


def test_response_json():
    response = Response.json({"this_is": "json"})
    assert 200 == response.status
    assert '{"this_is": "json"}' == response.body
    assert "application/json; charset=utf-8" == response.content_type


def test_response_redirect():
    response = Response.redirect("/foo")
    assert 302 == response.status
    assert "/foo" == response.headers["Location"]


@pytest.mark.asyncio
async def test_response_set_cookie():
    events = []

    async def send(event):
        events.append(event)

    response = Response.redirect("/foo")
    response.set_cookie("foo", "bar", max_age=10, httponly=True)
    await response.asgi_send(send)

    assert [
        {
            "type": "http.response.start",
            "status": 302,
            "headers": [
                [b"Location", b"/foo"],
                [b"content-type", b"text/plain"],
                [b"set-cookie", b"foo=bar; HttpOnly; Max-Age=10; Path=/; SameSite=lax"],
            ],
        },
        {"type": "http.response.body", "body": b""},
    ] == events


def test_response_error_single_message():
    response = Response.error("Method not allowed", 405)
    assert response.status == 405
    assert response.content_type == "application/json; charset=utf-8"
    assert json.loads(response.body) == {
        "ok": False,
        "error": "Method not allowed",
        "errors": ["Method not allowed"],
        "status": 405,
    }


def test_response_error_message_list_and_default_status():
    response = Response.error(["First problem", "Second problem"])
    assert response.status == 400
    assert json.loads(response.body) == {
        "ok": False,
        "error": "First problem; Second problem",
        "errors": ["First problem", "Second problem"],
        "status": 400,
    }
